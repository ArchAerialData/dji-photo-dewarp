#!/usr/bin/env python3
"""Compare the preserved original pipeline with the batch engine on synthetic JPGs.

Run from any directory. Generated test images are isolated under .work and removed
afterward unless --keep-images is supplied. No user photo folders are read.
"""

from __future__ import annotations

import argparse
import contextlib
from datetime import datetime, timezone
import importlib.util
import io
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile
import time

import cv2
import numpy as np
from PIL import Image, __version__ as pillow_version
import piexif


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "app" / "src"))


def load_original(path: Path):
    spec = importlib.util.spec_from_file_location("dewarp_benchmark_original", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot import original script: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def create_fixtures(folder: Path, count: int, width: int, height: int) -> dict:
    """Deterministic textured images, not a substitute for a real flight benchmark."""
    folder.mkdir(parents=True)
    rng = np.random.default_rng(20261010)
    low_resolution = rng.integers(15, 235, (height // 16, width // 16, 3), dtype=np.uint8)
    texture = cv2.resize(low_resolution, (width, height), interpolation=cv2.INTER_LINEAR)
    noise = rng.integers(-8, 9, (height, width, 3), dtype=np.int16)
    texture = np.clip(texture.astype(np.int16) + noise, 0, 255).astype(np.uint8)
    del noise
    # Intrinsics preserve the same field of view if a smaller test size is used.
    fx = 3713.29 * width / 5280
    fy = 3713.29 * height / 3956
    calibration = f"{fx},{fy},0,0,-0.11257524,0.01487443,-0.00008572,0.00000010,-0.02706411"
    xmp = (
        '<x:xmpmeta xmlns:x="adobe:ns:meta/">'
        '<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
        '<rdf:Description rdf:about="" '
        'xmlns:drone-dji="http://www.dji.com/drone-dji/1.0/" '
        'drone-dji:DewarpFlag="0" '
        f'drone-dji:DewarpData="2026-10-10;{calibration}" '
        'drone-dji:AbsoluteAltitude="235.42" '
        'drone-dji:RelativeAltitude="75.00"/>'
        '</rdf:RDF></x:xmpmeta>'
    ).encode("utf-8")
    exif = piexif.dump({
        "0th": {piexif.ImageIFD.Orientation: 1, piexif.ImageIFD.Make: b"DJI"},
        "Exif": {piexif.ExifIFD.PixelXDimension: width, piexif.ExifIFD.PixelYDimension: height},
        "GPS": {}, "1st": {}, "Interop": {},
    })
    for index in range(count):
        pixels = np.roll(texture, index * 19, axis=1)
        Image.fromarray(pixels).save(
            folder / f"DJI_{index:04d}.JPG", "JPEG", quality=95,
            subsampling=0, exif=exif, xmp=xmp,
        )
    return {
        "count": count, "dimensions": [width, height], "calibration": calibration,
        "source_bytes": sum(path.stat().st_size for path in folder.glob("*.JPG")),
        "kind": "deterministic interpolated random texture with low-amplitude noise; synthetic, not real DJI photos",
    }


def run_new(folder: Path, output_root: Path, workers: int):
    # Kept in one adapter so the benchmark does not depend on GUI internals.
    from dewarp.core import engine as dewarp_engine

    options = dewarp_engine.BatchOptions(output_root=output_root, workers=workers, copy_xmp=True, alpha=0.0)
    return dewarp_engine.run_batch([folder], options=options)


def output_images(folder: Path) -> dict[str, Path]:
    return {path.name: path for path in folder.rglob("*") if path.is_file() and path.suffix.lower() in (".jpg", ".jpeg")}


def compare_outputs(reference: dict[str, Path], actual: dict[str, Path]) -> dict:
    if reference.keys() != actual.keys():
        raise AssertionError(f"Output file set mismatch: {set(reference) ^ set(actual)}")
    samples = []
    for name, baseline in sorted(reference.items()):
        old = cv2.imdecode(np.fromfile(baseline, np.uint8), cv2.IMREAD_COLOR)
        new = cv2.imdecode(np.fromfile(actual[name], np.uint8), cv2.IMREAD_COLOR)
        if old is None or new is None:
            raise AssertionError(f"Cannot decode an output: {name}")
        equal = np.array_equal(old, new)
        samples.append({
            "file": name, "old_shape": list(old.shape), "new_shape": list(new.shape),
            "pixels_equal": equal,
            "old_file_bytes": baseline.stat().st_size,
            "new_file_bytes": actual[name].stat().st_size,
        })
    return {"all_pixels_equal": all(row["pixels_equal"] for row in samples), "images": samples}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--original", type=Path, default=ROOT / ".work" / "dewarp_original.py")
    parser.add_argument("--count", type=int, default=8)
    parser.add_argument("--width", type=int, default=5280)
    parser.add_argument("--height", type=int, default=3956)
    parser.add_argument("--report", type=Path, default=ROOT / ".work" / "benchmark_results.json")
    parser.add_argument("--keep-images", action="store_true")
    args = parser.parse_args()
    if args.count < 1 or min(args.width, args.height) < 32:
        parser.error("Positive image count and dimensions >= 32 are required")
    if not args.original.is_file():
        parser.error(f"Preserved original script missing: {args.original}")
    exiftool = shutil.which("exiftool")
    if not exiftool:
        parser.error("ExifTool is required for a like-for-like --copy-xmp benchmark")
    original = load_original(args.original)
    work_root = ROOT / ".work"
    work_root.mkdir(exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="benchmark-", dir=work_root)).resolve()
    default_threads = cv2.getNumThreads()
    report = {
        "measured_at_utc": datetime.now(timezone.utc).isoformat(),
        "python": sys.version, "opencv": cv2.__version__, "pillow": pillow_version,
        "exiftool": subprocess.run([exiftool, "-ver"], capture_output=True, text=True, check=True).stdout.strip(),
        "platform": platform.platform(), "logical_cpus": os.cpu_count(),
        "initial_opencv_threads": default_threads,
        "original_script": str(args.original.resolve()),
        "options": {"copy_xmp": True, "alpha": 0.0, "jpeg_quality": 95, "jpeg_subsampling": "4:4:4"},
        "timing_scope": "per-folder pipeline including metadata, JPEG decoding/encoding and file I/O; fixture generation and comparison excluded",
        "runs": [],
    }
    try:
        folder = work / "Synthetic flight"
        print(f"Generating {args.count} synthetic {args.width}x{args.height} photos...", flush=True)
        report["fixtures"] = create_fixtures(folder, args.count, args.width, args.height)
        print("Timing original serial pipeline...", flush=True)
        start = time.perf_counter()
        with contextlib.redirect_stdout(io.StringIO()):
            original.process_folder(folder, True, False, True, 0.0)
        baseline_s = time.perf_counter() - start
        reference = output_images(folder / "Dewarped_Images_Photoshop")
        if len(reference) != args.count:
            raise AssertionError(f"Original produced {len(reference)}/{args.count} images")
        report["runs"].append({"name": "original_serial", "workers": 1, "elapsed_s": baseline_s, "photos_per_s": args.count / baseline_s})
        print(f"Original: {baseline_s:.3f}s", flush=True)
        for workers in (1, 2):
            print(f"Timing updated pipeline with {workers} worker(s)...", flush=True)
            destination = work / f"updated-{workers}"
            start = time.perf_counter()
            with contextlib.redirect_stdout(io.StringIO()):
                run_new(folder, destination, workers)
            elapsed = time.perf_counter() - start
            actual = output_images(destination)
            comparison = compare_outputs(reference, actual)
            report["runs"].append({
                "name": f"updated_{workers}_workers", "workers": workers,
                "elapsed_s": elapsed, "photos_per_s": args.count / elapsed,
                "speedup_vs_original": baseline_s / elapsed, "comparison": comparison,
            })
            print(f"Updated {workers}: {elapsed:.3f}s, {baseline_s / elapsed:.2f}x, equal pixels: {comparison['all_pixels_equal']}", flush=True)
        report["all_pixels_equal"] = all(row.get("comparison", {}).get("all_pixels_equal", True) for row in report["runs"])
        if args.keep_images:
            report["retained_fixture_directory"] = str(work)
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(f"Report: {args.report.resolve()}", flush=True)
        return 0 if report["all_pixels_equal"] else 1
    finally:
        cv2.setNumThreads(default_threads)
        if not args.keep_images:
            # Only the exact isolated directory created above can be removed.
            if work.parent != work_root.resolve() or not work.name.startswith("benchmark-"):
                raise RuntimeError(f"Refusing cleanup outside benchmark directory: {work}")
            # Windows indexers or antivirus may briefly hold newly written JPGs.
            # A cleanup failure must not turn a successful measured run into a
            # failed benchmark; leave its exact path available for later cleanup.
            for attempt in range(5):
                try:
                    shutil.rmtree(work)
                    break
                except PermissionError:
                    if attempt == 4:
                        print(f"Temporary files still in use; retained: {work}", file=sys.stderr)
                    else:
                        time.sleep(0.2 * (attempt + 1))


if __name__ == "__main__":
    raise SystemExit(main())
