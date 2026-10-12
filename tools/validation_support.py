"""Bounded validation subprocesses and confined, explicitly owned scratch space."""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import queue
import shutil
import signal
import subprocess
import threading
import time
import uuid


class _WindowsJob:
    """Keep the owned child tree killable even after the original PID exits.

    The child is created suspended and assigned before it can spawn descendants.
    No breakaway flags are enabled. Closing the non-inherited handle terminates
    the whole job, including when the validation runner itself is terminated.
    https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects
    """

    def __init__(self):
        import ctypes as c
        from ctypes import wintypes as w

        self.c = c
        self.api = api = c.WinDLL("kernel32", use_last_error=True)

        class BasicLimits(c.Structure):
            _fields_ = [("PerProcessUserTimeLimit", c.c_longlong), ("PerJobUserTimeLimit", c.c_longlong),
                        ("LimitFlags", w.DWORD), ("MinimumWorkingSetSize", c.c_size_t),
                        ("MaximumWorkingSetSize", c.c_size_t), ("ActiveProcessLimit", w.DWORD),
                        ("Affinity", c.c_size_t), ("PriorityClass", w.DWORD), ("SchedulingClass", w.DWORD)]

        class ExtendedLimits(c.Structure):
            _fields_ = [("BasicLimitInformation", BasicLimits), ("IoInfo", c.c_ulonglong * 6),
                        ("ProcessMemoryLimit", c.c_size_t), ("JobMemoryLimit", c.c_size_t),
                        ("PeakProcessMemoryUsed", c.c_size_t), ("PeakJobMemoryUsed", c.c_size_t)]

        class ThreadEntry(c.Structure):
            _fields_ = [("dwSize", w.DWORD), ("cntUsage", w.DWORD), ("th32ThreadID", w.DWORD),
                        ("th32OwnerProcessID", w.DWORD), ("tpBasePri", w.LONG),
                        ("tpDeltaPri", w.LONG), ("dwFlags", w.DWORD)]

        self.ThreadEntry = ThreadEntry
        signatures = {
            "CreateJobObjectW": ([c.c_void_p, w.LPCWSTR], w.HANDLE),
            "SetInformationJobObject": ([w.HANDLE, c.c_int, c.c_void_p, w.DWORD], w.BOOL),
            "AssignProcessToJobObject": ([w.HANDLE, w.HANDLE], w.BOOL),
            "TerminateJobObject": ([w.HANDLE, w.UINT], w.BOOL),
            "OpenProcess": ([w.DWORD, w.BOOL, w.DWORD], w.HANDLE),
            "CreateToolhelp32Snapshot": ([w.DWORD, w.DWORD], w.HANDLE),
            "Thread32First": ([w.HANDLE, c.POINTER(ThreadEntry)], w.BOOL),
            "Thread32Next": ([w.HANDLE, c.POINTER(ThreadEntry)], w.BOOL),
            "OpenThread": ([w.DWORD, w.BOOL, w.DWORD], w.HANDLE),
            "ResumeThread": ([w.HANDLE], w.DWORD),
            "CloseHandle": ([w.HANDLE], w.BOOL),
        }
        for name, (arguments, result) in signatures.items():
            function = getattr(api, name)
            function.argtypes, function.restype = arguments, result
        self.handle = api.CreateJobObjectW(None, None)
        if not self.handle:
            raise c.WinError(c.get_last_error())
        limits = ExtendedLimits()
        limits.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not api.SetInformationJobObject(self.handle, 9, c.byref(limits), c.sizeof(limits)):
            error = c.WinError(c.get_last_error())
            self.close()
            raise error

    def attach_and_resume(self, child):
        c, api = self.c, self.api
        # PROCESS_SET_QUOTA | PROCESS_TERMINATE, required by AssignProcessToJobObject.
        process = api.OpenProcess(0x0100 | 0x0001, False, child.pid)
        if not process:
            raise c.WinError(c.get_last_error())
        try:
            if not api.AssignProcessToJobObject(self.handle, process):
                raise c.WinError(c.get_last_error())
        finally:
            api.CloseHandle(process)
        # Popen closes CreateProcess's primary thread handle. A suspended child
        # has not run user code; resume only threads belonging to that exact PID.
        snapshot = api.CreateToolhelp32Snapshot(0x00000004, 0)  # TH32CS_SNAPTHREAD
        if snapshot == c.c_void_p(-1).value:
            raise c.WinError(c.get_last_error())
        try:
            entry = self.ThreadEntry()
            entry.dwSize = c.sizeof(entry)
            found = api.Thread32First(snapshot, c.byref(entry))
            resumed = False
            while found:
                if entry.th32OwnerProcessID == child.pid:
                    thread = api.OpenThread(0x0002, False, entry.th32ThreadID)  # THREAD_SUSPEND_RESUME
                    if not thread:
                        raise c.WinError(c.get_last_error())
                    try:
                        if api.ResumeThread(thread) == 0xFFFFFFFF:
                            raise c.WinError(c.get_last_error())
                        resumed = True
                    finally:
                        api.CloseHandle(thread)
                found = api.Thread32Next(snapshot, c.byref(entry))
            if not resumed:
                raise RuntimeError("Could not find the owned suspended process's thread")
        finally:
            api.CloseHandle(snapshot)

    def close(self):
        if self.handle:
            if not self.api.CloseHandle(self.handle):
                raise self.c.WinError(self.c.get_last_error())
            self.handle = None

    def terminate(self):
        if self.handle:
            if not self.api.TerminateJobObject(self.handle, 124):
                raise self.c.WinError(self.c.get_last_error())
            self.close()


def run_bounded(command, *, cwd, timeout, stall_timeout, log_path=None):
    """Stop only this child process tree after its wall/no-output deadline.

    A line of child output resets the stall deadline, never the overall deadline.
    Progress-only callers should emit lines only after completed work. Logs stream
    to disk; the result retains at most the last 30 lines.
    """
    if any(not math.isfinite(limit) or limit <= 0 for limit in (timeout, stall_timeout)):
        raise ValueError("Timeouts must be finite and positive")
    kwargs = ({"creationflags": subprocess.CREATE_NO_WINDOW | 0x00000004}  # CREATE_SUSPENDED
              if os.name == "nt" else {"start_new_session": True})
    start = last_output = time.monotonic()
    log = Path(log_path).open("w", encoding="utf-8") if log_path else None
    child, job, reader = None, None, None
    lines = queue.Queue()

    def read_output():
        try:
            for line in child.stdout:
                lines.put(line)
        finally:
            lines.put(None)
            child.stdout.close()
    tail, reason, ended = [], None, False

    def terminate_tree():
        if job:
            job.terminate()  # Still valid when child exited but descendants hold its pipes.
        elif os.name != "nt":
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        if child.poll() is None:
            child.kill()
        child.wait(timeout=10)

    try:
        job = _WindowsJob() if os.name == "nt" else None
        child = subprocess.Popen([str(arg) for arg in command], cwd=cwd, stdin=subprocess.DEVNULL,
                                 stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                 encoding="utf-8", errors="replace", bufsize=1, **kwargs)
        if job:
            job.attach_and_resume(child)
        reader = threading.Thread(target=read_output, daemon=True)
        reader.start()
        while not ended or child.poll() is None:
            now = time.monotonic()
            if now - start >= timeout or now - last_output >= stall_timeout:
                reason = "wall timeout" if now - start >= timeout else "no-output timeout"
                terminate_tree()
                break
            try:
                line = lines.get(timeout=min(0.1, timeout - (now - start),
                                             stall_timeout - (now - last_output)))
            except queue.Empty:
                continue
            if line is None:
                ended = True
            else:
                last_output = time.monotonic()
                tail.append(line.rstrip())
                tail = tail[-30:]
                if log:
                    log.write(line)
                    log.flush()
        return {"command": [str(arg) for arg in command], "exit_code": child.poll(),
                "elapsed_seconds": time.monotonic() - start, "timeout": reason,
                "wall_limit_seconds": timeout, "stall_limit_seconds": stall_timeout,
                "tail": tail}
    finally:
        try:
            if child is not None:
                terminate_tree()
            elif job:
                job.close()
        finally:
            try:
                if reader:
                    reader.join(timeout=2)
                    # Closing a TextIOWrapper from here can block on the reader's
                    # lock if an unexpected external owner still holds its pipe.
                    if reader.is_alive():
                        raise RuntimeError("Child output pipe did not close after process-tree termination")
                elif child is not None:
                    child.stdout.close()
            finally:
                if log:
                    log.close()


def create_scratch(parent: Path):
    parent = parent.resolve(strict=True)
    token = uuid.uuid4().hex
    scratch = parent / (".dewarp-validation-" + token)
    scratch.mkdir()
    (scratch / ".validation-owner.json").write_text(json.dumps({"token": token}), encoding="utf-8")
    return scratch, token


def cleanup_scratch(scratch: Path, parent: Path, token: str):
    """Delete only our exact marked direct child; reject redirected paths."""
    parent = parent.resolve(strict=True)
    if scratch.is_symlink() or (hasattr(scratch, "is_junction") and scratch.is_junction()):
        raise ValueError("Refusing redirected scratch directory")
    target = scratch.resolve(strict=True)
    if target.parent != parent or target.name != ".dewarp-validation-" + token:
        raise ValueError("Scratch path is not the expected direct child")
    marker = target / ".validation-owner.json"
    if json.loads(marker.read_text(encoding="utf-8")) != {"token": token}:
        raise ValueError("Scratch ownership marker mismatch")
    for entry in target.rglob("*"):
        if entry.is_symlink() or (hasattr(entry, "is_junction") and entry.is_junction()):
            raise ValueError(f"Refusing redirected entry in scratch: {entry}")
        if not entry.resolve().is_relative_to(target):
            raise ValueError("Scratch entry escapes the verified target")
    for attempt in range(3):
        try:
            shutil.rmtree(target)
            return
        except OSError:
            if attempt == 2:
                raise
            time.sleep(0.2)
