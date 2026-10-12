#!/usr/bin/env python3
"""Stable shell-alias entry point, independent of the current directory."""
from pathlib import Path
import os
import subprocess
import sys


def _run_in_environment(command):
    # Both console processes receive Ctrl+C. The app handles it by finishing
    # in-flight writes; subprocess.call would kill it when our wait is interrupted.
    with subprocess.Popen(command) as child:
        while True:
            try:
                return child.wait()
            except KeyboardInterrupt:
                continue


def main():
    root = Path(__file__).resolve().parents[1]
    interpreter = root / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if interpreter.is_file() and Path(sys.prefix).resolve() != (root / ".venv").resolve():
        # Preserve the console and exit code for CLI calls (including Git Bash).
        # The Windows GUI launcher uses pythonw independently.
        return _run_in_environment([str(interpreter), "-I", str(Path(__file__).resolve()), *sys.argv[1:]])
    sys.path.insert(0, str(root / "app" / "src"))
    try:
        from dewarp.cli import main as run
    except ModuleNotFoundError as exc:
        print(f"Missing dependency: {exc.name}. Run scripts/Setup.ps1 or double-click Dewarp.cmd.", file=sys.stderr)
        return 1
    return run()


if __name__ == "__main__":
    raise SystemExit(main())
