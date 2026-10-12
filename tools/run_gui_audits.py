"""Run native GUI audits serially with explicit process-tree timeouts.

No photos are processed and no Windows display settings are changed.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys

from validation_support import run_bounded

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "docs" / "gui-screen-audits.json")
    args = parser.parse_args()
    if os.name != "nt":
        parser.error("The native UI Automation audit requires Windows PowerShell.")
    args.output = args.output.resolve()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    commands = [
        ("setup", ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "tools/audit_gui_accessibility.ps1", "-Python", sys.executable,
                  "-OutputFile", "docs/gui-screen-accessibility-setup.json"]),
        ("running", ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "tools/audit_gui_accessibility.ps1", "-Python", sys.executable,
                     "-RunningState", "-OutputFile", "docs/gui-screen-accessibility-running.json"]),
        ("completed", ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "tools/audit_gui_accessibility.ps1", "-Python", sys.executable,
                       "-State", "completed", "-OutputFile", "docs/gui-screen-accessibility-completed.json"]),
        ("settings", ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "tools/audit_gui_accessibility.ps1", "-Python", sys.executable,
                      "-State", "settings", "-OutputFile", "docs/gui-screen-accessibility-settings.json"]),
        ("layout", [sys.executable, "tools/audit_qt_layout.py", "--output", "docs/gui-screen-layout.json",
                    "--screenshots", "docs/gui-screen-images"]),
        ("terminal-states", [sys.executable, "tools/audit_qt_terminal_states.py"]),
    ]
    report = {
        "captured_utc": datetime.now(timezone.utc).isoformat(),
        "source_sha256": hashlib.sha256((ROOT / "app/src/dewarp/gui/window.py").read_bytes()).hexdigest(),
        "settings_source_sha256": hashlib.sha256((ROOT / "app/src/dewarp/gui/settings.py").read_bytes()).hexdigest(),
        "scrolling_source_sha256": hashlib.sha256((ROOT / "app/src/dewarp/gui/scrolling.py").read_bytes()).hexdigest(),
        "theme_source_sha256": hashlib.sha256((ROOT / "app/src/dewarp/gui/theme.py").read_bytes()).hexdigest(),
        "results": [],
    }
    for name, command in commands:
        timeout, stall_timeout = (30, 25) if name == "terminal-states" else (240, 180)
        result = run_bounded(command, cwd=ROOT, timeout=timeout, stall_timeout=stall_timeout,
                             log_path=args.output.with_name(f"{args.output.stem}-{name}.log"))
        report["results"].append(result)
        # Keep partial evidence if a later audit reaches its deadline.
        args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"audit": name, **result}), flush=True)
        if result["timeout"] or result["exit_code"]:
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
