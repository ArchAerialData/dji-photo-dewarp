#!/usr/bin/env python3
"""Stable GUI/CLI entry point used by the existing dewarp shell alias."""
from __future__ import annotations

import argparse
from pathlib import Path
import sys
import threading

from dewarp.core.engine import (
    BatchOptions, already_dewarped, build_K_dist, canonical, exiftool_path,
    parse_dewarp_numbers, process_folder, run_batch, save_with_exif,
    undistort, xmp_extract_values,
)


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if not argv:
        try:
            from dewarp.gui.window import run_gui
        except ModuleNotFoundError as exc:
            if (exc.name or "").split(".")[0] != "PySide6":
                raise
            print("The GUI requires PySide6. Run scripts/Setup.ps1 or double-click Dewarp.cmd.", file=sys.stderr)
            return 1
        run_gui()
        return 0
    parser = argparse.ArgumentParser(description="Dewarp DJI JPEGs with per-photo calibration and preserved EXIF.")
    parser.add_argument("folders", nargs="+", type=Path, help="One or more folders containing JPEGs (non-recursive)")
    parser.add_argument("--output", "--output-root", dest="output_root", type=Path,
                        help="Output parent; default: inside each input folder. Existing outputs are never overwritten.")
    parser.add_argument("--workers", type=int, choices=range(1, 5), default=2,
                        help="Concurrent photos, 1-4 (default: 2; use 1 for less RAM)")
    parser.add_argument("--copy-xmp", action="store_true", help="Preserve full XMP using ExifTool (GUI default: on)")
    parser.add_argument("--require-dewarp", action="store_true",
                        help="Compatibility option: missing/invalid calibration is always skipped")
    parser.add_argument("--alpha", type=float, choices=[0.0, 1.0], default=0.0,
                        help="0: crop edges; 1: wider view, then valid-region crop (legacy behavior)")
    parser.add_argument("--report", action="store_true", help="Also print folder summaries; JSON reports are always saved")
    args = parser.parse_args(argv)
    options = BatchOptions(output_root=args.output_root, workers=args.workers,
                           copy_xmp=args.copy_xmp, alpha=args.alpha,
                           require_dewarp=args.require_dewarp)
    cancelled = threading.Event()

    def on_event(event):
        kind = event["event"]
        if kind == "folder_start":
            print(f"\nProcessing: {event['folder']}\nOutput: {event['output']}", flush=True)
        elif kind == "photo_done":
            print(f"[{event['status'].upper():7}] {event['name']} - {event['message']} "
                  f"({event['batch_completed']}/{event['batch_total']})", flush=True)
            for warning in event["warnings"]:
                print(f"  Warning: {warning}", flush=True)
        elif kind == "folder_done" and (args.report or event.get("error")):
            print(f"{event['folder']}: {event['status']}; {event['applied']} dewarped, "
                  f"{event['already']} already corrected, {event['skipped']} skipped, {event['failed']} failed; "
                  f"{event['elapsed_seconds']:.1f}s.", flush=True)
            if event.get("error"):
                print(event["error"], file=sys.stderr)
        elif kind == "log":
            print(event["message"], flush=True)
        elif kind == "batch_done":
            print(f"\n{'Cancelled' if event['cancelled'] else 'Finished'}: {event['completed']}/{event['total']} photos; "
                  f"{event['applied']} dewarped, {event['already']} already corrected, "
                  f"{event['skipped']} skipped, {event['failed']} failed; {event['elapsed']:.1f}s.", flush=True)

    # Ctrl+C requests the same graceful cancellation as the GUI.
    import signal
    previous = signal.getsignal(signal.SIGINT)

    def cancel_batch(signum, frame):
        if not cancelled.is_set():
            print("\nCancelling after in-flight photos finish...", flush=True)
            cancelled.set()

    signal.signal(signal.SIGINT, cancel_batch)
    try:
        results = run_batch(args.folders, options, on_event, cancelled)
        if cancelled.is_set():
            return 130
        return int(any(r["failed"] or r["status"] == "Failed" for r in results))
    except (ValueError, OSError, RuntimeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    finally:
        signal.signal(signal.SIGINT, previous)


if __name__ == "__main__":
    raise SystemExit(main())
