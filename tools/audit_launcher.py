"""Bound actual root CMD -> Pythonw GUI startup, native UIA/theme, and clean close."""
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.validation_support import cleanup_scratch, create_scratch, run_bounded


def main():
    parent = ROOT / ".work"
    parent.mkdir(exist_ok=True)
    scratch, token = create_scratch(parent)
    output = ROOT / "docs/benchmarks/launcher-gui-audit.json"
    powershell = Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    result = None
    try:
        result = run_bounded([powershell, "-NoLogo", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                              ROOT / "tools/audit_launcher.ps1", "-OutputFile", output],
                             cwd=scratch, timeout=60, stall_timeout=35,
                             log_path=ROOT / "docs/benchmarks/launcher-gui-audit.log")
        print(json.dumps(result, indent=2))
        return 1 if result["timeout"] else result["exit_code"]
    finally:
        cleanup_scratch(scratch, parent, token)
        summary = {"result": result, "scratch_removed": not scratch.exists(), "photos_accessed": False}
        (ROOT / "docs/benchmarks/launcher-gui-run.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
