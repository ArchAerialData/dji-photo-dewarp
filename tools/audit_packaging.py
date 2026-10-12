"""Bounded portable-install/launcher checks; never reads photo fixtures."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.validation_support import cleanup_scratch, create_scratch, run_bounded


def main():
    parent = ROOT / ".work"
    parent.mkdir(exist_ok=True)
    scratch, token = create_scratch(parent)
    evidence = {"checks": [], "scratch": str(scratch), "photos_accessed": False}
    python = ROOT / ".venv/Scripts/python.exe"
    powershell = Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    result_path = ROOT / "docs/benchmarks/portable-install-audit.json"

    def check(name, command, expected=0, *, timeout=60, stall=40, contains=None):
        result = run_bounded(command, cwd=scratch, timeout=timeout, stall_timeout=stall)
        result["name"] = name
        evidence["checks"].append(result)
        if result["timeout"] or result["exit_code"] != expected:
            raise AssertionError(result)
        if contains and contains not in "\n".join(result["tail"]):
            raise AssertionError(f"{name}: expected {contains!r}; {result}")
        print(f"PASS: {name} ({result['elapsed_seconds']:.2f}s)", flush=True)

    try:
        (scratch / "dewarp.py").write_text('raise RuntimeError("WRONG LOCAL SHADOW MODULE")\n')
        launch = [powershell, "-NoLogo", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", ROOT / "scripts/Launch.ps1"]
        check("PowerShell launcher from external shadowing cwd", [*launch, "--help"], contains="usage:")
        check("PowerShell CLI exit code propagation", [*launch, "--workers", "99"], expected=2, contains="invalid choice")
        check("Root CMD launcher from external cwd", [os.environ.get("ComSpec", "cmd.exe"), "/d", "/c", ROOT / "Dewarp.cmd", "--help"], contains="usage:")
        check("Stable Python shim from external cwd", [sys.executable, ROOT / "scripts/dewarp.py", "--help"], contains="usage:")
        check("Installed console entry point", [ROOT / ".venv/Scripts/dewarp.exe", "--help"], contains="usage:")
        copied_app = scratch / "copied app source"
        shutil.copytree(ROOT / "app", copied_app, ignore=shutil.ignore_patterns("__pycache__", "*.egg-info", "build"))
        wheel_dir = scratch / "wheels"
        check("Build wheel from clean copied source", [python, "-I", "-m", "pip", "wheel", "--no-deps", "--disable-pip-version-check", "--no-input", "--timeout", "30", "--retries", "1", "--wheel-dir", wheel_dir, copied_app], timeout=240, stall=120, contains="Successfully built")
        wheel = next(wheel_dir.glob("dji_photo_dewarp-*.whl"))
        with zipfile.ZipFile(wheel) as archive:
            evidence["wheel_members"] = archive.namelist()
        assert "dewarp/core/engine.py" in evidence["wheel_members"]
        assert "dewarp/gui/window.py" in evidence["wheel_members"]
        assert "dewarp/gui/theme.py" in evidence["wheel_members"]
        target = scratch / "installed copy"
        check("Install wheel into independent target", [python, "-I", "-m", "pip", "install", "--no-deps", "--disable-pip-version-check", "--no-input", "--target", target, wheel], timeout=120, stall=60, contains="Successfully installed")
        source = "import sys,pathlib,runpy; sys.path.insert(0,sys.argv.pop(1)); import dewarp; assert pathlib.Path(dewarp.__file__).resolve().is_relative_to(pathlib.Path(sys.path[0]).resolve()); sys.argv=['dewarp','--help']; runpy.run_module('dewarp',run_name='__main__')"
        check("Independent wheel executes its own source", [python, "-I", "-c", source, target], contains="usage:")
        versions = "import importlib.metadata as m,json; print(json.dumps({name:m.version(name) for name in ('numpy','opencv-python','Pillow','piexif','PySide6','PySide6_Addons','PySide6_Essentials','shiboken6')}))"
        check("Record isolated environment versions", [python, "-I", "-c", versions], contains='"numpy": "2.2.6"')
        evidence["passed"] = True
    except BaseException as exc:
        evidence["passed"] = False
        evidence["error"] = str(exc)
        raise
    finally:
        cleanup_scratch(scratch, parent, token)
        evidence["scratch_removed"] = not scratch.exists()
        result_path.write_text(json.dumps(evidence, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
