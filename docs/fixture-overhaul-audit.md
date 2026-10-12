# Fixture and workflow audit — 2026-10-10

This records the earlier processing/accessibility overhaul. The later package
move and system-theme verification are recorded in the [appearance and packaging
audit](packaging-overhaul-audit.md); current source paths are under `app/src/dewarp`.

The sampled real-photo checks passed for **CLI single folder, CLI batch, GUI
single folder, and GUI batch** after the fixes below. All generated test photos
and output folders were removed. All **867 original files** retained identical
paths, sizes, modification times, and SHA-256 hashes, including the flight-data
sidecars. [Final machine-readable result](benchmarks/real-fixture-20261010T205832Z/result.json),
[before manifest](benchmarks/real-fixture-20261010T205832Z/sources-before.json),
[after manifest](benchmarks/real-fixture-20261010T205832Z/sources-after.json).
The [final cleanup check](fixture-cleanup-verification.json) also compares the
very first manifest with the final manifest across all investigation runs and
confirms that the fixture parent contains only `Original Input JPGs`.

## Findings and resolutions

| Finding | Applied resolution | Evidence |
| --- | --- | --- |
| EXIF rebuilding copied the MakerNote directory but lost its external vendor data, introducing bad offsets and other structural warnings. | Preserve the complete original EXIF block during encoding and let ExifTool update orientation/dimensions and remove stale thumbnails. All extracted vendor fields remain intact. | [Investigation](makernote-investigation.json), [three-folder fix verification](makernote-fix-verification.json), real-fixture output checks |
| The optional no-ExifTool path could also silently damage MakerNotes. | Fail explicitly before publishing such a photo; simple EXIF without MakerNotes still works through Pillow's aligned serializer. | `test_makernotes_require_exiftool_before_any_output_is_published`, `test_simple_exif_fallback_preserves_metadata_without_new_structure_warnings` |
| Minimal XMP lacked its packet wrapper; output EXIF added inappropriate TIFF dimension tags. | Wrap the marker packet and use JPEG/ExifIFD dimensions. | No additional output-validation warnings in the real checks |
| Malformed calibration strings and focal lengths that became zero in float32 passed validation. | Require exactly nine valid numeric coefficients and positive representable focal lengths. | Parser regression tests in [test_dewarp.py](../tests/test_dewarp.py) |
| Per-folder processing time was absent. | Include `elapsed_seconds` in results, reports, CLI summaries, GUI Activity and Ctrl+I. | Preserved reports and timing table below |
| Removing one queue row lost retained folders' results/output links. | Preserve and reindex retained state. | [GUI workflow audit](gui-workflow-audit.md) |
| Aborted batches left untouched rows misleadingly Queued. | Distinguish Failed from Not processed and restore usable controls. | GUI workflow regression tests |
| Empty parent folders left stale preparation text and no useful guidance. | Explain that only direct JPEGs are processed and identify the need to add photo-containing subfolders. | GUI workflow regression tests |
| Completion text hid failures, skips, or warnings. | Show and announce Finished with issues; keep detailed counts and logs. | Synthetic tests plus real GUI warning-state checks |
| Long terminal summaries clipped at 320×240 with 200% text. | Keep the footer to final status and percentage; full counts remain in Live progress and Ctrl+I. Start/Cancel stay visible. | [32 terminal-layout cases](gui-terminal-state-audit.json) |
| Start's description incorrectly implied only queued rows would run. | Explicitly describe processing every listed folder, with new outputs for reruns. | GUI descriptions and keyboard help |
| Killing a test's original process could leave descendants running after that process exited. | Own a Windows Job Object, attach the suspended child before it runs, enforce wall/stall deadlines, and terminate only that owned tree. | Native orphan/timeout regression tests in [test_validation_support.py](../tests/test_validation_support.py) |
| Real-photo tests lacked persistent safety, timing, and cleanup rules. | Add an opt-in sampled runner, original manifests, metadata/pixel checks, marked scratch ownership, cleanup verification, and repository instructions. | [Runner](../tools/test_real_fixture.py), [working rules](development/AGENTS.md) |
| The first warning-normalization test mishandled output names containing ` - `. | Split diagnostics at the absolute path prefix, retaining the full diagnostic text and severity. | Harness regression test; successful final run |

The initial strict fixture check and a later harness-path check stopped early.
Both retained timing/report evidence, removed their scratch outputs, and verified
all originals unchanged. Their records remain available for investigation:
[initial metadata finding](benchmarks/real-fixture-20261010T204901Z/result.json),
[diagnostic-path correction](benchmarks/real-fixture-20261010T205735Z/result.json).
They are not successful benchmark baselines.

## Real-photo baseline

The original tree contains 365 Conan CS JPEGs, 241 Gorgon LGL JPEGs, and 246
Neptune LGL JPEGs: **852 JPEGs**, plus 15 sidecars, totaling 4,862,154,278 bytes.
The successful run selected twelve evenly spaced photos per folder, including
the first and last. The four scenarios generated and checked **96 outputs from
36 distinct 5280×3956 source photos**. No full 852-photo processing run was needed.

Settings: two workers, alpha 0, full XMP, JPEG quality 95 / 4:4:4. Runtime:
Python 3.13.3, OpenCV 4.12.0, Pillow 12.3.0, Qt/PySide6 6.10.2, ExifTool 13.30.
Inputs and outputs were isolated copies on K:. Code hashes, selected filenames,
runtime versions, and complete per-folder reports are recorded with the run.
The final footer-text shortening followed this run and only changes presentation;
it was checked with GUI tests/audits instead of reprocessing the real photos.

| Scenario | Photos | Conan CS | Gorgon LGL | Neptune LGL | Total wall time |
| --- | ---: | ---: | ---: | ---: | ---: |
| CLI single | 12 | 2.601 s | — | — | 3.192 s |
| CLI batch | 36 | 2.606 s | 2.294 s | 2.347 s | 7.900 s |
| GUI single | 12 | 2.630 s | — | — | 3.696 s |
| GUI batch | 36 | 2.617 s | 2.259 s | 2.396 s | 8.362 s |

Folder times cover output reservation and photo processing, excluding report
serialization. Total wall time includes process/tool/GUI startup and shutdown;
staging, output verification, hashing, and cleanup are excluded. These are one
run per configuration, in the displayed order, with filesystem caching and
normal system activity uncontrolled. They are reproducible comparison baselines,
not estimates of full-flight throughput or proof that one interface is faster.

All 96 outputs decoded, retained GPS/capture dates/ICC/calibration, and had
correct orientation/dimensions/dewarp flags. All 14 extracted DJI MakerNote
fields matched each source, including binary values checked through ExifTool's
base64 JSON representation. No new metadata-validation warnings were introduced.
Eight reference comparisons had exactly equal decoded pixels to a direct
`cv2.undistort` + matching JPEG encode. That reference shares calibration and
orientation helpers, so it tests transform/encoding parity, not the correctness
of the camera's supplied calibration.

The sources already have nonstandard metadata warnings. The final outputs retain
three of those source warnings (two aperture field types and the location of
UniqueCameraModel); source MakerNote warnings can also be emitted while updating
metadata. These remain visible in reports and the GUI. Tests compare each output
against its own source diagnostics rather than suppressing all warnings.

## Timeouts, repeatability, and accessibility

- `python tools/run_checks.py`: 120-second wall / 60-second no-output limit.
  All **48 regression tests passed** in 11.780 seconds. A focused six-test harness
  recheck passed after the diagnostic-path correction; all fifteen GUI tests
  passed again in 2.927 seconds after the footer presentation fix.
  [Test evidence](validation-tests.json), [final GUI checks](gui-workflow-audit.md).
- `python tools/run_gui_audits.py`: each audit has 240-second wall / 180-second
  no-output limits. All **288 layout cases and 42 native Windows accessibility
  checks passed**. [Recorded audit](gui-audit-rerun.json).
  The wrapper also passed **32 terminal-state layout cases** with a 30-second
  wall / 25-second no-output limit. All final GUI evidence matches the final
  GUI source hash; these presentation checks did not reprocess photos.
- `python tools/test_real_fixture.py`: opt-in twelve-photo-per-folder sample;
  600-second wall / 60-second no-progress limit per processing scenario.
  Hashing, characterization, staging, verification, and cleanup each have
  180-second wall / 60-second no-progress limits for sampled runs. Larger
  staging/characterization/verification phases use 600 seconds with `--full`.
  No final-run phase timed out.

Use real photos only when a processing or workflow change warrants them. Do not
run the full fixture for cosmetic GUI/documentation changes. Keep timing and
verification evidence, remove generated images, and verify originals before
closing the task. Native process-tree isolation follows Microsoft's
[Job Object model](https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects).

The existing `dewarp` alias was invoked successfully with `--help` under a
15-second deadline; the stable launcher remains unchanged in location. GUI
automation confirms layout, native accessibility interfaces, keyboard workflows,
and progress/status behavior. Actual screen-reader speech, Windows contrast
themes, and physical transitions between monitors with different configured DPI
remain unverified; see the [accessibility investigation](a11y-investigation.md).
