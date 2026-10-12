"""Opt-in real-photo workflow checks. Originals are hashed, never processed in place.

Default: twelve evenly spaced JPEGs from each fixture folder, four serial
scenarios (CLI/GUI, single/batch). --full is explicitly opt-in. No test discovery
imports or runs this script. Every slow phase runs in a bounded child process.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
import platform
import re
import shutil
import sys
import time
import traceback

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "app" / "src"))
from tools.validation_support import cleanup_scratch, create_scratch, run_bounded

DEFAULT_SOURCE = Path(r"K:\Temp-Dewarp-Test-Fixture\Original Input JPGs")
JPEG = {".jpg", ".jpeg"}


def write_json(path, value):
    Path(path).write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding="utf-8")


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def snapshot(source):
    files, directories = [], []
    for entry in sorted(source.rglob("*")):
        if entry.is_symlink() or (hasattr(entry, "is_junction") and entry.is_junction()):
            raise ValueError(f"Fixture must not contain redirected paths: {entry}")
        relative = entry.relative_to(source).as_posix()
        if entry.is_dir():
            directories.append(relative)
        elif entry.is_file():
            stat = entry.stat()
            files.append({"path": relative, "size": stat.st_size,
                          "mtime_ns": stat.st_mtime_ns, "sha256": sha256(entry)})
            if len(files) % 64 == 0:
                print(f"Hashed {len(files)} source files", flush=True)
    print(f"Hashed all {len(files)} source files", flush=True)
    return {"source": str(source.resolve()), "directories": directories, "files": files}


def choose_photos(manifest, limit):
    """Deterministic evenly spaced sample, including first and last photo."""
    groups = {}
    for record in manifest["files"]:
        path = Path(record["path"])
        if path.suffix.lower() in JPEG:
            groups.setdefault(path.parent.as_posix(), []).append(record)
    chosen = {}
    for folder, records in sorted(groups.items()):
        records.sort(key=lambda item: item["path"].casefold())
        if limit is None or len(records) <= limit:
            chosen[folder] = records
        elif limit == 1:
            chosen[folder] = [records[0]]
        else:
            chosen[folder] = [records[round(i * (len(records) - 1) / (limit - 1))]
                              for i in range(limit)]
    return chosen


def normalized_warning(message):
    """Compare diagnostic text, retaining severity/content but excluding paths."""
    message = str(message)
    if message.startswith("Warning: "):
        message = message[len("Warning: "):]
    # Split at the absolute path prefix, not at a later " - " in a folder name.
    return re.split(r" - (?=(?:[A-Za-z]:[\\/]|/|\\\\))", message, maxsplit=1)[0]


def metadata_diagnostics(session, source):
    # -a and family 4 preserve repeated Warning tags in JSON.
    text, stderr = session.execute(["-j", "-a", "-G4", "-validate", "-warning", "-error", str(source)])
    data = json.loads(text)[0]
    warnings = [str(value) for key, value in data.items() if key.split(":")[-1] == "Warning"]
    errors = [str(value) for key, value in data.items() if key.split(":")[-1] == "Error"]
    if errors:
        raise AssertionError(f"Metadata validation errors for {source}: {errors}")
    return {"validation": data, "warnings": warnings + [normalized_warning(w) for w in stderr]}


def maker_note_fingerprint(session, source):
    # -b makes non-printable vendor values base64 in JSON instead of lossy text.
    text, warnings = session.execute(["-j", "-n", "-u", "-b", "-G1", "-MakerNotes:all", str(source)])
    fields = {key: value for key, value in json.loads(text)[0].items()
              if key != "SourceFile" and key.split(":")[-1] not in ("Warning", "Error")}
    return {"extracted_fields": len(fields),
            "sha256": hashlib.sha256(json.dumps(fields, sort_keys=True).encode("utf-8")).hexdigest(),
            "warnings": [normalized_warning(warning) for warning in warnings]}


def characterize(config):
    """Record existing source diagnostics without changing source metadata."""
    import cv2
    import PIL
    import PySide6
    from PIL import Image
    from dewarp.core.engine import ExifToolSession, exiftool_path
    session = ExifToolSession(exiftool_path(), timeout=30)
    records = {}
    try:
        version, _ = session.execute(["-ver"])
        for selected in config["selected"].values():
            for record in selected:
                source = Path(config["source"]) / record["path"]
                diagnostics = metadata_diagnostics(session, source)
                maker_notes = maker_note_fingerprint(session, source)
                with Image.open(source) as photo:
                    dimensions = list(photo.size)
                records[record["path"]] = {"dimensions": dimensions, "maker_notes": maker_notes, **diagnostics}
                print(f"Characterized {record['path']}", flush=True)
    finally:
        session.close()
    return {"runtime": {"opencv": cv2.__version__, "pillow": PIL.__version__,
                        "pyside6": PySide6.__version__, "exiftool": version.strip(),
                        "logical_cpus": os.cpu_count()}, "files": records}


def stage(config):
    source, scratch = Path(config["source"]), Path(config["scratch"])
    for records in config["selected"].values():
        for record in records:
            relative = Path(record["path"])
            target = scratch / "inputs" / relative
            if not target.resolve().is_relative_to(scratch.resolve()):
                raise ValueError("Staged path escapes scratch")
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source / relative, target)
            if sha256(target) != record["sha256"]:
                raise AssertionError(f"Staged copy mismatch: {relative}")
            print(f"Staged {relative}", flush=True)


def gui_scenario(config, scenario):
    from PySide6 import QtCore, QtWidgets
    from dewarp.gui.settings import ProcessingSettings, SettingsStore
    from dewarp.gui.window import DewarpApp
    app = QtWidgets.QApplication([])
    app.setQuitOnLastWindowClosed(False)
    settings_store = SettingsStore(path=Path(config["scratch"]) / (scenario["name"] + "-preferences.ini"))
    settings_store.save(ProcessingSettings(copy_xmp=True, alpha=0.0, workers=2))
    window = DewarpApp(settings_store=settings_store)
    window.setWindowOpacity(0.0)
    window.show()
    folders = [Path(path) for path in scenario["folders"]]
    window.add_folders(folders)
    window.output_edit.setText(scenario["output"])
    captured, failure = [], []
    original = window._handle_event

    def handle(event):
        original(event)
        if event["event"] in ("folder_done", "batch_done", "worker_error"):
            captured.append(dict(event))
        if event["event"] in ("photo_done", "folder_done", "batch_done", "worker_error"):
            print(json.dumps({key: event[key] for key in ("event", "index", "completed", "total", "status")
                              if key in event}), flush=True)

    window._handle_event = handle
    # Native modal errors are failures in unattended tests, not invisible stalls.
    def modal_error(*args, **kwargs):
        failure.append(str(args[2] if len(args) > 2 else args))
        return QtWidgets.QMessageBox.StandardButton.Ok

    QtWidgets.QMessageBox.critical = modal_error
    QtWidgets.QMessageBox.warning = modal_error
    QtWidgets.QMessageBox.information = modal_error
    start = time.monotonic()
    window.start_button.click()
    if not window._running:
        raise AssertionError(f"GUI did not start: {failure}")
    if window.pages.currentWidget() is not window.progress_scroll or window.back_button.isEnabled():
        raise AssertionError("GUI did not enter the dedicated processing page with editing locked")
    while window._running or (window._worker and window._worker.is_alive()):
        app.processEvents()
        time.sleep(0.01)
    app.processEvents()
    result = {"elapsed_seconds": time.monotonic() - start, "events": captured,
              "statuses": window._statuses, "batch_total": window._batch_total,
              "batch_completed": window._batch_completed, "batch_bar": window.batch_bar.value(),
              "start_enabled": window.start_button.isEnabled(),
              "cancel_enabled": window.cancel_button.isEnabled(),
              "back_enabled": window.back_button.isEnabled(),
              "progress_page_visible": window.pages.currentWidget() is window.progress_scroll,
              "errors": failure}
    expected = sum(len(config["selected"][Path(folder).relative_to(Path(config["scratch"]) / "inputs").as_posix()])
                   for folder in folders)
    summaries = [event for event in captured if event["event"] == "batch_done"]
    worker_errors = [event for event in captured if event["event"] == "worker_error"]
    clean_summary = (len(summaries) == 1 and not summaries[0].get("cancelled")
                     and summaries[0].get("completed") == expected
                     and summaries[0].get("applied") == expected
                     and not any(summaries[0].get(key, 0) for key in
                                 ("failed", "skipped", "already", "folders_failed")))
    if (failure or result["batch_completed"] != expected or result["batch_total"] != expected
            or result["batch_bar"] != 100 or not result["back_enabled"] or result["cancel_enabled"]
            or not result["progress_page_visible"]
            or not clean_summary or worker_errors
            or any(value not in ("Completed", "Completed with issues") for value in window._statuses.values())):
        raise AssertionError(f"GUI completion mismatch: {result}")
    if summaries[0].get("warnings", 0) > summaries[0].get("suppressed_warnings", 0) and "Finished with issues" not in window.batch_text.text():
        raise AssertionError("GUI did not make metadata warnings visible in its final summary")
    for row in range(len(folders)):
        for column in range(window.folder_table.columnCount()):
            if window.folder_table.item(row, column).text() != window.progress_table.item(row, column + (column >= 4)).text():
                raise AssertionError("Setup and progress folder summaries disagree")
    window.back_button.click()
    app.processEvents()
    result["back_retains_queue"] = (window.pages.currentWidget() is window.setup_scroll
                                     and window.folders == folders and window.start_button.isEnabled())
    if not result["back_retains_queue"]:
        raise AssertionError("Back did not restore the populated setup page")
    window.close()
    app.processEvents()
    write_json(scenario["gui_result"], result)
    print("GUI workflow validated", flush=True)


def verify_scenario(config, scenario):
    from dewarp.core.engine import ExifToolSession, exiftool_path
    session = ExifToolSession(exiftool_path(), timeout=30)
    try:
        return _verify_scenario(config, scenario, session)
    finally:
        session.close()


def _verify_scenario(config, scenario, session):
    import cv2
    import numpy as np
    import piexif
    from PIL import Image
    from dewarp.core.engine import build_K_dist, normalize_orientation, xmp_extract_values
    reports = list(Path(scenario["output"]).glob("*/processing-report.json"))
    if len(reports) != len(scenario["folders"]):
        raise AssertionError("Folder report count mismatch")
    by_folder = {}
    for report_path in reports:
        report = json.loads(report_path.read_text(encoding="utf-8"))
        summary = report["summary"]
        by_folder[str(Path(summary["folder"]).resolve())] = (report_path, report)
    validations, timings, diagnostics = [], [], []
    reference_count = 0
    for folder_string in scenario["folders"]:
        folder = Path(folder_string)
        report_path, report = by_folder[str(folder.resolve())]
        summary = report["summary"]
        expected = {path.name for path in folder.iterdir() if path.suffix.lower() in JPEG}
        actual = {path.name for path in report_path.parent.iterdir() if path.suffix.lower() in JPEG}
        expected_status = "Completed with issues" if summary["warnings"] > summary.get("suppressed_warnings", 0) else "Completed"
        if expected != actual or summary["status"] != expected_status or summary["applied"] != len(expected):
            raise AssertionError(f"Output counts/status mismatch: {summary}")
        if summary["failed"] or summary["skipped"]:
            raise AssertionError(f"Unexpected processing issues: {summary}")
        if len(report["files"]) != len(expected) or any(r["status"] != "applied" for r in report["files"]):
            raise AssertionError("Per-photo report mismatch")
        if summary.get("elapsed_seconds", -1) <= 0:
            raise AssertionError("Missing positive per-folder processing timing")
        timings.append({"folder": folder.name, "photos": len(expected),
                        "elapsed_seconds": summary["elapsed_seconds"],
                        "photos_per_second": len(expected) / summary["elapsed_seconds"]})
        for index, name in enumerate(sorted(expected)):
            source, output = folder / name, report_path.parent / name
            relative = source.relative_to(Path(config["scratch"]) / "inputs").as_posix()
            baseline_warnings = set(config["source_characterization"]["files"][relative]["warnings"])
            photo_report = next(record for record in report["files"] if record["name"] == name)
            process_warnings = {normalized_warning(w) for w in photo_report["warnings"]}
            if process_warnings - baseline_warnings:
                raise AssertionError(f"New processing warnings for {relative}: {process_warnings - baseline_warnings}")
            output_diagnostics = metadata_diagnostics(session, output)
            unexpected = set(output_diagnostics["warnings"]) - baseline_warnings
            if unexpected:
                raise AssertionError(f"New output metadata warnings for {relative}: {unexpected}")
            before_notes = config["source_characterization"]["files"][relative]["maker_notes"]
            after_notes = maker_note_fingerprint(session, output)
            if (before_notes["sha256"] != after_notes["sha256"]
                    or before_notes["extracted_fields"] != after_notes["extracted_fields"]):
                raise AssertionError(f"Extracted MakerNote values changed: {relative}")
            if set(after_notes["warnings"]) - baseline_warnings:
                raise AssertionError(f"New MakerNote warnings: {relative}")
            diagnostics.append({"source": relative, "source_warnings": sorted(baseline_warnings),
                                "processing_warnings": sorted(process_warnings),
                                "output_warnings": output_diagnostics["warnings"],
                                "maker_notes_fields_preserved": after_notes["extracted_fields"]})
            with Image.open(source) as source_image, Image.open(output) as output_image:
                output_image.load()  # Decode every output, not just its header.
                source_xmp = xmp_extract_values(source_image.info.get("xmp"))
                output_xmp = xmp_extract_values(output_image.info.get("xmp"))
                if output_xmp.get("dewarp_flag") != "1" or any(source_xmp.get(key) != output_xmp.get(key)
                        for key in ("dewarp_data", "cocx", "cocy")):
                    raise AssertionError("DJI calibration/flag metadata mismatch")
                if source_image.info.get("icc_profile") != output_image.info.get("icc_profile"):
                    raise AssertionError("ICC profile mismatch")
                before, after = piexif.load(str(source)), piexif.load(str(output))
                if before["GPS"] != after["GPS"]:
                    raise AssertionError("GPS metadata mismatch")
                for tag in (piexif.ExifIFD.DateTimeOriginal, piexif.ExifIFD.DateTimeDigitized):
                    if before["Exif"].get(tag) != after["Exif"].get(tag):
                        raise AssertionError("Capture date metadata mismatch")
                width, height = output_image.size
                if (after["0th"].get(piexif.ImageIFD.Orientation) != 1
                        or after["Exif"].get(piexif.ExifIFD.PixelXDimension) != width
                        or after["Exif"].get(piexif.ExifIFD.PixelYDimension) != height):
                    raise AssertionError("Output EXIF orientation/dimensions mismatch")
                # Reference uses direct undistort rather than cached remapping.
                # Calibration interpretation/orientation helpers are shared, so
                # this proves transform/encoding parity, not camera calibration.
                if index == 0:
                    decoded = cv2.imdecode(np.frombuffer(source.read_bytes(), dtype=np.uint8),
                                           cv2.IMREAD_COLOR | cv2.IMREAD_IGNORE_ORIENTATION)
                    h, w = decoded.shape[:2]
                    matrix, dist = build_K_dist(w, h, source_xmp)
                    new_matrix, roi = cv2.getOptimalNewCameraMatrix(matrix, dist, (w, h), 0.0)
                    corrected = cv2.undistort(decoded, matrix, dist, None, new_matrix)
                    x, y, rw, rh = roi
                    if rw > 0 and rh > 0:
                        corrected = corrected[y:y + rh, x:x + rw]
                    corrected = normalize_orientation(corrected, before["0th"].get(piexif.ImageIFD.Orientation, 1))
                    encoded = BytesIO()
                    Image.fromarray(cv2.cvtColor(corrected, cv2.COLOR_BGR2RGB)).save(
                        encoded, "JPEG", quality=95, subsampling=0)
                    encoded.seek(0)
                    with Image.open(encoded) as reference:
                        if not np.array_equal(np.asarray(reference), np.asarray(output_image)):
                            raise AssertionError(f"Reference decoded pixels differ: {name}")
                    reference_count += 1
            print(f"Verified {folder.name}/{name}", flush=True)
        validations.append({"folder": folder.name, "verified_photos": len(expected)})
    return {"folder_timings": timings, "folder_time_sum_seconds": sum(r["elapsed_seconds"] for r in timings),
            "validated": validations, "pixel_exact_reference_comparisons": reference_count,
            "metadata_diagnostics": diagnostics}


def internal(action, config_path, output_path):
    config = json.loads(Path(config_path).read_text(encoding="utf-8"))
    if action == "snapshot":
        write_json(output_path, snapshot(Path(config["source"])))
    elif action == "stage":
        stage(config)
    elif action == "characterize":
        write_json(output_path, characterize(config))
    elif action == "gui":
        gui_scenario(config, config["active_scenario"])
    elif action == "verify":
        write_json(output_path, verify_scenario(config, config["active_scenario"]))
    elif action == "cleanup":
        cleanup_scratch(Path(config["scratch"]), Path(config["source"]).parent, config["scratch_token"])
        print("Owned scratch directory removed", flush=True)
    else:
        raise ValueError(action)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--sample-per-folder", type=int, default=12)
    parser.add_argument("--full", action="store_true", help="Explicitly process all photos; only for relevant changes")
    parser.add_argument("--report-dir", type=Path)
    parser.add_argument("--scenario-timeout", type=float, default=600)
    parser.add_argument("--stall-timeout", type=float, default=60)
    args = parser.parse_args()
    if args.sample_per_folder < 1 or args.scenario_timeout <= 0 or args.stall_timeout <= 0:
        parser.error("Sample count and timeouts must be positive")
    source = args.source.resolve(strict=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    evidence = (args.report_dir or ROOT / "docs" / "benchmarks" / ("real-fixture-" + stamp)).resolve()
    if evidence.is_relative_to(source.parent):
        parser.error("Evidence must be outside the fixture parent")
    evidence.mkdir(parents=True, exist_ok=False)
    result = {"started_utc": stamp, "source": str(source), "full_fixture": args.full,
              "sample_per_folder": None if args.full else args.sample_per_folder,
              "python": sys.version, "platform": platform.platform(),
              "source_hashes": {name: sha256(ROOT / name) for name in
                                ("app/src/dewarp/core/engine.py", "app/src/dewarp/gui/window.py",
                                 "app/src/dewarp/gui/settings.py", "app/src/dewarp/gui/theme.py",
                                 "app/src/dewarp/gui/scrolling.py", "app/src/dewarp/cli.py")},
              "settings": {"workers": 2, "alpha": 0, "copy_xmp": True,
                           "jpeg_quality": 95, "jpeg_subsampling": 0},
              "scenarios": [], "phases": [], "errors": [], "originals_unchanged": False,
              "scratch_removed": False}
    config = {"source": str(source)}
    config_path = evidence / "run-config.json"
    write_json(config_path, config)
    scratch = token = None
    before = None

    def phase(name, command, timeout=180, stall=60):
        print(f"Starting {name} (wall {timeout}s; no-progress {stall}s)", flush=True)
        outcome = run_bounded(command, cwd=ROOT, timeout=timeout, stall_timeout=stall,
                              log_path=evidence / (name + ".log"))
        result["phases"].append({"name": name, **outcome})
        write_json(evidence / "result.json", result)
        if outcome["exit_code"] or outcome["timeout"]:
            raise RuntimeError(f"{name} failed: exit={outcome['exit_code']}, timeout={outcome['timeout']}; {outcome['tail'][-3:]}")
        return outcome

    def worker(action, output, name=None, timeout=180):
        return phase(name or action, [sys.executable, "-u", __file__, "--internal", action,
                                     str(config_path), str(output)], timeout=timeout)

    try:
        worker("snapshot", evidence / "sources-before.json", "hash-before")
        before = json.loads((evidence / "sources-before.json").read_text(encoding="utf-8"))
        selected = choose_photos(before, None if args.full else args.sample_per_folder)
        if not selected:
            raise ValueError("No JPEGs in fixture")
        scratch, token = create_scratch(source.parent)
        result["scratch_path"] = str(scratch)
        result["source_files"] = len(before["files"])
        result["source_bytes"] = sum(item["size"] for item in before["files"])
        result["selected_photos"] = {folder: len(records) for folder, records in selected.items()}
        config.update(scratch=str(scratch), scratch_token=token, selected=selected)
        write_json(config_path, config)
        worker("characterize", evidence / "source-characterization.json", timeout=600 if args.full else 180)
        config["source_characterization"] = json.loads((evidence / "source-characterization.json").read_text(encoding="utf-8"))
        result["runtime"] = config["source_characterization"]["runtime"]
        write_json(config_path, config)
        worker("stage", evidence / "unused.json", timeout=600 if args.full else 180)
        folders = [str(scratch / "inputs" / folder) for folder in selected]
        for interface in ("cli", "gui"):
            for mode in ("single", "batch"):
                name = f"{interface}-{mode}"
                scenario = {"name": name, "folders": folders[:1] if mode == "single" else folders,
                            "output": str(scratch / "outputs" / name),
                            "gui_result": str(evidence / (name + "-gui.json"))}
                config["active_scenario"] = scenario
                write_json(config_path, config)
                if interface == "cli":
                    command = [sys.executable, "-u", ROOT / "scripts/dewarp.py", *scenario["folders"],
                               "--output", scenario["output"], "--copy-xmp", "--report", "--workers", "2"]
                else:
                    command = [sys.executable, "-u", __file__, "--internal", "gui", str(config_path), "unused"]
                execution = phase(name, command, timeout=args.scenario_timeout, stall=args.stall_timeout)
                verification_path = evidence / (name + "-verification.json")
                worker("verify", verification_path, name + "-verify", timeout=600 if args.full else 180)
                verification = json.loads(verification_path.read_text(encoding="utf-8"))
                result["scenarios"].append({"name": name, "process_wall_seconds": execution["elapsed_seconds"],
                                            **verification})
    except Exception:
        result["errors"].append(traceback.format_exc())
    finally:
        # Preserve reports as evidence; no photos are retained in the repository.
        if scratch:
            try:
                reports = []
                for path in (scratch / "outputs").glob("*/*/processing-report.json"):
                    reports.append({"scenario": path.parent.parent.name,
                                    "report": json.loads(path.read_text(encoding="utf-8"))})
                write_json(evidence / "processing-reports.json", reports)
            except Exception:
                result["errors"].append("Report preservation failed: " + traceback.format_exc())
            try:
                worker("cleanup", evidence / "unused.json", timeout=180)
                result["scratch_removed"] = not scratch.exists()
            except Exception:
                result["errors"].append("Cleanup failed: " + traceback.format_exc())
        if before:
            try:
                worker("snapshot", evidence / "sources-after.json", "hash-after")
                after = json.loads((evidence / "sources-after.json").read_text(encoding="utf-8"))
                result["originals_unchanged"] = before == after
                if before != after:
                    result["errors"].append("Source manifest changed (paths, sizes, mtime, or SHA-256)")
            except Exception:
                result["errors"].append("Post-run integrity check failed: " + traceback.format_exc())
        result["passed"] = not result["errors"] and result["originals_unchanged"] and result["scratch_removed"]
        write_json(evidence / "result.json", result)
    print(json.dumps({"passed": result["passed"], "evidence": str(evidence),
                      "originals_unchanged": result["originals_unchanged"],
                      "scratch_removed": result["scratch_removed"], "errors": result["errors"]}, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--internal":
        internal(*sys.argv[2:])
    else:
        raise SystemExit(main())
