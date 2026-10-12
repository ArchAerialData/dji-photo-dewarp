// Windows PowerShell 5.1 bootstrap: suspend BEFORE assigning the kill-on-close job.
// A child cannot spawn outside our job before assignment. All setup commands
// have wall and no-output deadlines and preserve output diagnostics.
// https://learn.microsoft.com/windows/win32/procthread/job-objects
using System;
using System.Collections.Concurrent;
using System.ComponentModel;
using System.Diagnostics;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;
using Microsoft.Win32.SafeHandles;

namespace Dewarp.Bootstrap {
    public sealed class CommandResult {
        public int ExitCode;
        public string Output;
        public string Timeout;
        public double ElapsedSeconds;
    }

    public static class ProcessRunner {
        [StructLayout(LayoutKind.Sequential)] struct BasicLimits {
            public long PerProcessUserTimeLimit, PerJobUserTimeLimit;
            public uint LimitFlags;
            public UIntPtr MinimumWorkingSetSize, MaximumWorkingSetSize;
            public uint ActiveProcessLimit;
            public UIntPtr Affinity;
            public uint PriorityClass, SchedulingClass;
        }
        [StructLayout(LayoutKind.Sequential)] struct IoCounters {
            public ulong ReadOperationCount, WriteOperationCount, OtherOperationCount;
            public ulong ReadTransferCount, WriteTransferCount, OtherTransferCount;
        }
        [StructLayout(LayoutKind.Sequential)] struct ExtendedLimits {
            public BasicLimits Basic;
            public IoCounters Io;
            public UIntPtr ProcessMemoryLimit, JobMemoryLimit, PeakProcessMemoryUsed, PeakJobMemoryUsed;
        }
        [StructLayout(LayoutKind.Sequential)] struct SecurityAttributes {
            public int Length;
            public IntPtr Descriptor;
            [MarshalAs(UnmanagedType.Bool)] public bool Inherit;
        }
        [StructLayout(LayoutKind.Sequential, CharSet=CharSet.Unicode)] struct StartupInfo {
            public int Size;
            public string Reserved, Desktop, Title;
            public uint X, Y, XSize, YSize, XChars, YChars, Fill, Flags;
            public ushort Show, ReservedSize;
            public IntPtr ReservedPtr, Input, Output, Error;
        }
        [StructLayout(LayoutKind.Sequential)] struct ProcessInformation {
            public IntPtr Process, Thread;
            public uint ProcessId, ThreadId;
        }
        [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
        static extern IntPtr CreateJobObject(IntPtr attributes, string name);
        [DllImport("kernel32.dll", SetLastError=true)]
        static extern bool SetInformationJobObject(IntPtr job, int type, ref ExtendedLimits limits, uint size);
        [DllImport("kernel32.dll", SetLastError=true)]
        static extern bool AssignProcessToJobObject(IntPtr job, IntPtr process);
        [DllImport("kernel32.dll", SetLastError=true)]
        static extern bool TerminateJobObject(IntPtr job, uint exitCode);
        [DllImport("kernel32.dll", SetLastError=true)]
        static extern bool CreatePipe(out IntPtr read, out IntPtr write, ref SecurityAttributes attributes, uint size);
        [DllImport("kernel32.dll", SetLastError=true)]
        static extern bool SetHandleInformation(IntPtr handle, uint mask, uint flags);
        [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
        static extern IntPtr CreateFile(string name, uint access, uint share, ref SecurityAttributes attributes,
            uint creation, uint flags, IntPtr template);
        [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
        static extern bool CreateProcess(string app, StringBuilder command, IntPtr processAttributes,
            IntPtr threadAttributes, bool inherit, uint flags, IntPtr environment, string directory,
            ref StartupInfo startup, out ProcessInformation information);
        [DllImport("kernel32.dll", SetLastError=true)] static extern uint ResumeThread(IntPtr thread);
        [DllImport("kernel32.dll", SetLastError=true)] static extern bool TerminateProcess(IntPtr process, uint code);
        [DllImport("kernel32.dll", SetLastError=true)] static extern bool GetExitCodeProcess(IntPtr process, out uint code);
        [DllImport("kernel32.dll")] static extern uint WaitForSingleObject(IntPtr handle, uint milliseconds);
        [DllImport("kernel32.dll")] static extern bool CloseHandle(IntPtr handle);

        public static CommandResult Run(string executable, string arguments, string cwd,
                int wallSeconds, int stallSeconds, string logPath, bool quiet) {
            if (wallSeconds < 1 || stallSeconds < 1) throw new ArgumentOutOfRangeException("timeout");
            var queue = new ConcurrentQueue<string>();
            var clock = Stopwatch.StartNew();
            var output = new StringBuilder();
            IntPtr job = IntPtr.Zero, pipeRead = IntPtr.Zero, pipeWrite = IntPtr.Zero, input = IntPtr.Zero;
            var info = new ProcessInformation();
            StreamWriter log = null;
            StreamReader reader = null;
            Thread readerThread = null;
            int readerClosed = 0;
            try {
                job = CreateJobObject(IntPtr.Zero, null);
                if (job == IntPtr.Zero) throw new Win32Exception();
                var limits = new ExtendedLimits();
                limits.Basic.LimitFlags = 0x2000;
                if (!SetInformationJobObject(job, 9, ref limits, (uint)Marshal.SizeOf(limits))) throw new Win32Exception();
                var security = new SecurityAttributes { Length = Marshal.SizeOf(typeof(SecurityAttributes)), Inherit = true };
                if (!CreatePipe(out pipeRead, out pipeWrite, ref security, 0)) throw new Win32Exception();
                if (!SetHandleInformation(pipeRead, 1, 0)) throw new Win32Exception();
                input = CreateFile("NUL", 0x80000000, 3, ref security, 3, 0, IntPtr.Zero);
                if (input == new IntPtr(-1)) { input = IntPtr.Zero; throw new Win32Exception(); }
                var startup = new StartupInfo { Size = Marshal.SizeOf(typeof(StartupInfo)), Flags = 0x100,
                    Input = input, Output = pipeWrite, Error = pipeWrite };
                var command = new StringBuilder("\"" + executable + "\" " + arguments);
                if (!CreateProcess(executable, command, IntPtr.Zero, IntPtr.Zero, true,
                        0x08000004, IntPtr.Zero, cwd, ref startup, out info)) throw new Win32Exception(); // NO_WINDOW | SUSPENDED
                if (!AssignProcessToJobObject(job, info.Process)) throw new Win32Exception();
                if (ResumeThread(info.Thread) == UInt32.MaxValue) throw new Win32Exception();
                CloseHandle(info.Thread); info.Thread = IntPtr.Zero;
                CloseHandle(pipeWrite); pipeWrite = IntPtr.Zero;
                CloseHandle(input); input = IntPtr.Zero;
                reader = new StreamReader(new FileStream(new SafeFileHandle(pipeRead, true), FileAccess.Read), Encoding.UTF8, true);
                pipeRead = IntPtr.Zero;
                readerThread = new Thread(() => {
                    try { string line; while ((line = reader.ReadLine()) != null) queue.Enqueue(line); }
                    catch (IOException e) { queue.Enqueue("Output read failed: " + e.Message); }
                    finally { Interlocked.Exchange(ref readerClosed, 1); }
                });
                readerThread.IsBackground = true;
                readerThread.Start();
                if (!String.IsNullOrEmpty(logPath)) log = new StreamWriter(logPath, true, new UTF8Encoding(false));
                double lastOutput = clock.Elapsed.TotalSeconds;
                string timeout = null;
                while (true) {
                    string line;
                    while (queue.TryDequeue(out line)) {
                        output.AppendLine(line);
                        if (!quiet) Console.WriteLine(line);
                        if (log != null) { log.WriteLine(line); log.Flush(); }
                        lastOutput = clock.Elapsed.TotalSeconds;
                    }
                    if (WaitForSingleObject(info.Process, 0) == 0 && Volatile.Read(ref readerClosed) == 1 && queue.IsEmpty) break;
                    if (clock.Elapsed.TotalSeconds >= wallSeconds) timeout = "wall timeout";
                    else if (clock.Elapsed.TotalSeconds - lastOutput >= stallSeconds) timeout = "no-output timeout";
                    if (timeout != null) {
                        if (!TerminateJobObject(job, 124)) throw new Win32Exception();
                        WaitForSingleObject(info.Process, 5000);
                        break;
                    }
                    Thread.Sleep(25);
                }
                uint code;
                if (!GetExitCodeProcess(info.Process, out code)) throw new Win32Exception();
                return new CommandResult { ExitCode = timeout == null ? unchecked((int)code) : 124,
                    Output = output.ToString(), Timeout = timeout, ElapsedSeconds = clock.Elapsed.TotalSeconds };
            } finally {
                if (job != IntPtr.Zero) CloseHandle(job);
                if (info.Process != IntPtr.Zero) {
                    if (WaitForSingleObject(info.Process, 0) != 0) { TerminateProcess(info.Process, 124); WaitForSingleObject(info.Process, 5000); }
                    CloseHandle(info.Process);
                }
                if (info.Thread != IntPtr.Zero) CloseHandle(info.Thread);
                if (pipeWrite != IntPtr.Zero) CloseHandle(pipeWrite);
                if (pipeRead != IntPtr.Zero) CloseHandle(pipeRead);
                if (input != IntPtr.Zero) CloseHandle(input);
                if (readerThread != null && !readerThread.Join(5000)) throw new IOException("Owned output pipe failed to close after process-tree termination.");
                if (reader != null) reader.Dispose();
                if (log != null) log.Dispose();
            }
        }
    }
}
