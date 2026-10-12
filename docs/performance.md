# Dewarp performance and image compatibility

Measured on this Windows computer on October 10, 2026 using Python 3.13,
OpenCV 4.12.0, Pillow 12.3.0, and ExifTool 13.30. These are local synthetic
benchmarks, not measurements of a real 1,000-photo drone flight.

The later [real-photo workflow baseline](fixture-overhaul-audit.md#real-photo-baseline)
records CLI and GUI single/batch timings on twelve sampled photos from each of
the user's three folders. It verifies the corrected metadata writer and retains
per-folder and total times. Those measurements are also samples, not full-flight
throughput estimates. The historical synthetic results below predate that EXIF
preservation fix and should not be treated as current end-to-end timings.

## End-to-end result

Eight deterministic 5280 × 3956 textured JPEGs, 131.7 MB combined input, were
processed with the same per-file DJI calibration, alpha 0, XMP copying enabled,
JPEG quality 95, and 4:4:4 chroma sampling. Timings include decoding, dewarping,
metadata reads/writes, JPEG encoding, and disk I/O. Fixture generation and output
verification are excluded.

| Pipeline | Elapsed | Compared with original |
| --- | ---: | ---: |
| Original script, serial | 8.231 seconds | 1.00× |
| Updated engine, 1 worker | 5.643 seconds | 1.46× faster |
| Updated engine, 2 workers | 3.283 seconds | 2.51× faster |

All 16 updated output images had exactly the same decoded pixel arrays and
5279 × 3955 dimensions as their corresponding original-script outputs. Output
file bytes differ because the new engine corrects and adds metadata. This proves
compatibility for these fixtures and settings, not every possible input file.
The synthetic frames all share one calibration; datasets whose calibration
changes every frame will reuse fewer cached maps. Storage speed, metadata size,
image content, other system activity, and available memory affect throughput.
Each configuration was timed once in original/one-worker/two-worker order, so
filesystem cache effects and run-to-run variation are not isolated.

The measured output also retained the fixture's DJI altitude and calibration
fields, set DewarpFlag to 1, and reported the corrected dimensions and orientation
consistently in EXIF and XMP. All eight files in each updated run completed without
warnings. Sources: [full result JSON](benchmarks/benchmark_results.json) and
[benchmark implementation](../tools/benchmark_dewarp.py).

## Why processing is faster

- The old script launched ExifTool separately to read metadata and again to copy
  XMP for every corrected photo. The new engine keeps one ExifTool process open
  for a batch and checks every command's result.
- A bounded cache reuses OpenCV maps only when image size, calibration values,
  and alpha match exactly. Bilinear interpolation and the original crop behavior
  are unchanged. Two M3E-size maps fit within the 256 MiB cache; image buffers
  and active workers require additional memory.
- Two image workers overlap decoding, encoding, file I/O, and correction. OpenCV
  uses a small thread team during the batch to avoid excessive thread creation.

In a separate correction-only experiment, cached remap took 0.0192 seconds
versus 0.1774 seconds for `cv2.undistort`, using 24 OpenCV threads and alpha 0.
The cache required 119.5 MiB per full-size calibration. With four OpenCV threads,
the corresponding cached remap took 0.0372 seconds. Pixel comparisons were exact
for alpha 0 and 1 and eight additional varied full-resolution calibrations.
Sources: [kernel measurements](benchmarks/remap_benchmark.json) and
[additional calibration comparisons](benchmarks/remap_variants.json).

A small-JPEG metadata-read experiment measured 0.158 seconds per separately
launched ExifTool process versus 0.00230 seconds for an already-running process.
This is only metadata-read overhead, not whole-photo throughput.
Source: [metadata measurements](benchmarks/metadata_review.json).

A separate 1,001-photo reliability run used small 160 × 100 synthetic JPEGs,
two workers, and full XMP copying through the installed ExifTool. It completed
all 1,001 photos with no failures, skips or warnings, monotonic progress counts,
and 1,001 per-photo report records. This tests queue/report behavior at scale;
its 31.53-second runtime is not a full-resolution throughput measurement.
Source: [batch scale check](benchmarks/batch_scale_smoke.json).

OpenCV documents that `undistort` combines map generation with bilinear remapping:
[OpenCV 4.12 undistort documentation](https://docs.opencv.org/4.12.0/d9/d0c/group__calib3d.html).
ExifTool's own guidance identifies repeated process startup as avoidable overhead:
[Common ExifTool mistakes](https://exiftool.org/mistakes.html).

## Reproduce

Run from the project directory:

```powershell
.\.venv\Scripts\python.exe tools/benchmark_dewarp.py --original PATH_TO_PRESERVED_ORIGINAL
```

The benchmark imports the preserved original `.work/dewarp_original.py` and the
current `app/src/dewarp/core/engine.py`. It needs ExifTool and Pillow 11.0 or newer. Use
`--original PATH` for a different preserved baseline or `--keep-images` to retain
the generated sources and outputs for inspection. The default report is
`.work/benchmark_results.json`. Only synthetic images in a newly created scratch
directory are processed; no existing photo folders are used. Cleanup retries
brief Windows file locks and reports any scratch directory it cannot remove.

JPEG XMP writing through Pillow's explicit `xmp=` argument was introduced in
Pillow 11.0: [Pillow release notes](https://pillow.readthedocs.io/en/stable/releasenotes/11.0.0.html).
The same JPEG quality and subsampling settings are retained; dewarping still
involves the existing JPEG re-encoding step. The performance changes do not
reduce resolution or lower the encoding quality settings.
