"""Run synthetic regression checks with a 120-second hard process-tree deadline."""
import argparse
import json
from pathlib import Path
import sys

from validation_support import run_bounded

ROOT = Path(__file__).resolve().parents[1]

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "docs" / "validation-tests.json")
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    result = run_bounded([sys.executable, "-u", "-m", "unittest", "discover", "-s", "tests", "-v"],
                         cwd=ROOT, timeout=120, stall_timeout=60,
                         log_path=args.output.with_suffix(".log"))
    args.output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))
    raise SystemExit(1 if result["timeout"] or result["exit_code"] else 0)
