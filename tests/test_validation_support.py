"""Synthetic timeout/cleanup safety tests; child programs have their own failsafes."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from validation_support import cleanup_scratch, create_scratch, run_bounded


class ValidationSupportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="dewarp-validation-support-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def run_child(self, source, *, timeout=3, stall_timeout=0.5):
        return run_bounded([sys.executable, "-u", "-c", source], cwd=self.root,
                           timeout=timeout, stall_timeout=stall_timeout)

    def test_normal_child_output_and_exit_are_preserved(self):
        result = self.run_child("print('one'); print('two'); raise SystemExit(7)")
        self.assertEqual(result["exit_code"], 7)
        self.assertIsNone(result["timeout"])
        self.assertEqual(result["tail"], ["one", "two"])

    def test_silent_child_hits_stall_deadline(self):
        result = self.run_child("import time; time.sleep(5)")
        self.assertEqual(result["timeout"], "no-output timeout")
        self.assertLess(result["elapsed_seconds"], 3)
        self.assertNotEqual(result["exit_code"], 0)

    def test_continuous_output_does_not_extend_wall_deadline(self):
        result = self.run_child("import time\nfor i in range(100):\n print(i); time.sleep(.05)",
                                timeout=0.8, stall_timeout=0.5)
        self.assertEqual(result["timeout"], "wall timeout")
        self.assertLess(result["elapsed_seconds"], 3)
        self.assertGreater(len(result["tail"]), 1)

    @unittest.skipUnless(os.name == "nt", "Native Windows Job Object regression")
    def test_exited_parent_cannot_orphan_a_descendant_holding_stdout(self):
        import ctypes as c
        from ctypes import wintypes as w
        descendant = "import time; time.sleep(5)"  # Failsafe even if the runner breaks.
        source = (
            "import subprocess,sys,pathlib\n"
            f"p=subprocess.Popen([sys.executable,'-c',{descendant!r}],stdout=sys.stdout,stderr=sys.stderr,creationflags=subprocess.CREATE_NO_WINDOW)\n"
            "pathlib.Path('descendant.pid').write_text(str(p.pid))\n"
            "print('parent exited',flush=True)\n"
        )
        start = time.monotonic()
        result = self.run_child(source, timeout=3, stall_timeout=0.7)
        self.assertLess(time.monotonic() - start, 3)
        self.assertEqual(result["timeout"], "no-output timeout")
        self.assertEqual(result["exit_code"], 0)  # Parent already exited before the timeout.
        pid = int((self.root / "descendant.pid").read_text())
        kernel = c.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
        kernel.OpenProcess.restype = w.HANDLE
        kernel.WaitForSingleObject.argtypes = [w.HANDLE, w.DWORD]
        kernel.WaitForSingleObject.restype = w.DWORD
        kernel.CloseHandle.argtypes = [w.HANDLE]
        handle = kernel.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE
        if handle:
            try:
                self.assertEqual(kernel.WaitForSingleObject(handle, 1000), 0,
                                 "Owned descendant survived the job timeout")
            finally:
                kernel.CloseHandle(handle)

    def test_invalid_timeout_is_rejected_before_process_creation(self):
        for wall, stall in ((0, 1), (1, 0), (-1, 1), (float("inf"), 1), (1, float("nan"))):
            with self.subTest(wall=wall, stall=stall), self.assertRaises(ValueError):
                self.run_child("raise RuntimeError('must not run')", timeout=wall, stall_timeout=stall)

    def test_owned_scratch_cleanup_preserves_parent_and_sibling(self):
        scratch, token = create_scratch(self.root)
        sibling = self.root / "original.jpg"
        sibling.write_bytes(b"original")
        (scratch / "result.jpg").write_bytes(b"output")
        cleanup_scratch(scratch, self.root, token)
        self.assertFalse(scratch.exists())
        self.assertEqual(sibling.read_bytes(), b"original")

    def test_scratch_refuses_parent_sibling_and_bad_marker(self):
        scratch, token = create_scratch(self.root)
        sibling = self.root / "sibling"
        sibling.mkdir()
        for target in (self.root, sibling):
            with self.subTest(target=target), self.assertRaises(ValueError):
                cleanup_scratch(target, self.root, token)
            self.assertTrue(target.is_dir())
        (scratch / ".validation-owner.json").write_text(json.dumps({"token": "different"}), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "marker"):
            cleanup_scratch(scratch, self.root, token)
        self.assertTrue(scratch.is_dir())


if __name__ == "__main__":
    unittest.main()
