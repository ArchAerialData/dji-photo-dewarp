# DJI photo dewarping

Double-click **Dewarp.cmd** or run **`dewarp`** from Git Bash on this machine.
The launcher installs dependencies into an isolated project environment on first
use. See the [setup guide](../.github/README.md) for other computers.

## Batch workflow

1. Click **Add folder…** for each input, or drag several folders into the queue.
   Duplicate selections are ignored. JPG/JPEG photos are found recursively in
   every subfolder, regardless of extension capitalization. Previous dewarp
   outputs, a nested output location, and directory links/junctions are excluded.
2. Choose an **Output location**, or leave it blank to save inside each input
   folder. With a chosen output location, input folder names and each photo’s
   relative subfolder path are preserved: `Raw/Abu CS/2D/Flight/photo.JPG`
   becomes `Output/Abu CS/2D/Flight/photo.JPG` when `Abu CS` is queued. If you
   queue `Raw` itself, the output also includes that `Raw` level. Leaving the
   output location blank uses `Input name - Dewarped Photos` inside each input.
   Subfolders containing processed photos are created as needed; non-JPEG files
   and empty directories are not copied.
   Existing names and duplicate input names receive `(2)`, `(3)`, etc. Source
   photos and previous output folders are never overwritten by a batch run.
3. Optionally click **Detect Photos** (or **Ctrl+D**) to fill the **Photos**
   column for every queued folder and show the total before processing. Detection
   runs in the background and stays on setup; **Cancel detection** or **Escape**
   stops it. Scan errors appear in the row tooltip and **Ctrl+I** details.
   Detection counts JPG/JPEG files without decoding or checking calibration,
   creating output folders, or changing photos. Changing the output location
   clears these counts. Click Detect Photos again to refresh after files change.
4. Click **Start batch** at the bottom right. It scans again for current files. The app switches to its dedicated
   processing screen. Folders run in queue order, with two photos at a time by
   default. The progress table shows queued, processing, completed, empty,
   failed and cancelled folders. Full paths are available in table tooltips and
   the copyable **View → Batch status** snapshot (**Ctrl+I**).
5. Watch current-folder counts, overall progress, folder status and activity on
   the processing screen. **Processed** includes
   dewarped photos, already-corrected photos, skipped photos and failures.
   The separate counters distinguish each outcome; 100% means all photos were
   attempted, not that every photo succeeded. Overall progress is weighted by
   photo count rather than giving small and large folders equal weight.
   **Duration**, between **Photos** and **Output folder**, shows each folder's
   elapsed processing time after it finishes, as `m:ss` (or `h:mm:ss`). Hover
   for seconds with two decimal places. A dash means no final timing is available.
6. Use **Open output** to view a selected folder's result. Each processed folder
   includes `processing-report.json` with options, totals, warnings and each
   photo's outcome, identified by its relative path. A `.dewarp-output` marker
   prevents later scans from reprocessing outputs, including interrupted runs.
   Folders without JPEGs anywhere below them do not create output directories.
7. When processing has finished or cancellation has completed, click
   **Back to setup** at the bottom right to prepare another run. The folder
   queue and output location are retained. Remove unwanted folders or use
   **Clear queue** to begin with a fresh selection. Start processes every listed
   folder again, including completed rows, creating separate output folders.

**Cancel batch**, at the bottom left, finishes the photos already running, saves their results and
report, then leaves the rest unprocessed. Closing an active window offers the
same safe cancellation and waits for workers to exit. Starting again creates
a new output folder; it does not resume the earlier partial folder.

## Saved processing settings

Open **File → Settings** (**Ctrl+,**) before starting a run. **Preserve XMP**
and **Crop edges** are enabled by default; **Parallel photos** defaults to **2**.
Click **Save settings** to persist changes for your Windows user. **Cancel**
discards dialog edits. **Restore defaults** fills in the recommended values;
you still choose Save to apply them. Invalid saved values fall back to defaults.

Settings are unavailable while a batch is running. They apply to future GUI
runs, including after closing and reopening the application; CLI options remain
independent. The main setup screen contains one output parent selection rather
than duplicate selected-folder path fields. Output selection applies to the
current queue and is retained when returning from results to setup.

## Image and metadata behavior

- Uses each photo's DJI `DewarpData`; missing or invalid calibration is skipped.
  No guessed generic camera profile is applied. An invalid JPEG fails on its
  own without stopping the other photos.
- JPEG settings remain **quality 95, chroma subsampling 0 (4:4:4)**. This is a
  JPEG re-encode, as in the original script, rather than a lossless format.
- Preserves EXIF (including GPS and capture time) and ICC color profiles.
  Calibration is applied before EXIF rotation/mirroring. Output orientation and
  dimensions are updated, and stale embedded EXIF thumbnails are removed.
- The original EXIF block is retained through JPEG encoding, then ExifTool
  updates it so vendor maker-note references remain valid. Photos containing
  MakerNotes require ExifTool even when **Preserve XMP metadata** is off; if it
  is missing, that photo fails clearly without publishing a damaged output.
  Simple EXIF without MakerNotes supports the no-ExifTool fallback.
- **Preserve XMP metadata** is enabled in the GUI and requires ExifTool on PATH.
  It retains full XMP, then updates the dewarp flag, orientation and image
  dimensions. Metadata-write failures fail that photo instead of publishing an
  incomplete output. With the option off, full source XMP is omitted, while a
  minimal DJI dewarp flag is still written to prevent applying correction twice.
- Photos already marked dewarped are normalized/re-encoded with preserved EXIF
  and optional XMP, matching the original script's general behavior.
- Both framing choices retain the original crop behavior: alpha 0 crops edges;
  alpha 1 uses a wider camera matrix and then crops to its valid region. It is
  not an uncropped full-sensor export.

## Performance and memory

The engine keeps ExifTool running for the batch, reuses correction maps only
when the complete calibration and image size match exactly, and processes a
bounded number of photos concurrently. The map cache is limited to 256 MiB;
in-flight photo buffers use additional memory. For less RAM, select **1** parallel
photo in **File → Settings**. **2** is the default; **3–4** may help on faster storage/CPUs but can also
be slower. The GUI reports elapsed time, processing rate and an estimated ETA.

See [performance measurements](performance.md) for measured results and their limits.
Synthetic validation does not establish performance on every camera, disk or
folder. [Sampled real-photo timings and findings](fixture-overhaul-audit.md)
are recorded; full-flight throughput has not been measured.

## Setup and command line

Use Windows 10/11 x64 with Python 3.10–3.13. `Dewarp.cmd` runs
`scripts/Setup.ps1` when needed to install the tested dependencies and ExifTool.
Explicit setup and diagnostics are documented in the [setup guide](../.github/README.md).
The GUI uses PySide6 / Qt 6.10+ for DPI scaling, native accessibility and system
appearance. Dependency and GUI logs are retained under `.local/logs`.

Existing single-folder CLI commands still work. Multiple folders and an output
parent are now supported:

```powershell
.\Dewarp.cmd "D:\Photos\Gorgon LGL" "D:\Photos\Flight B" --output "E:\Dewarped" --copy-xmp --report --workers 2
```

CLI full-XMP copying remains opt-in with `--copy-xmp`, as before.
`--output-root` is an alias for `--output`. `--require-dewarp` remains accepted
for compatibility; missing calibration is always skipped. Ctrl+C cancels safely.
CLI exit status is 0 for a completed run, 1 for file/folder failures, or 130 for
cancellation. Skips are counted and reported but do not set a failure exit code.

## Maintenance and verification

- `app/src/dewarp/cli.py`: GUI/CLI argument handling.
- `app/src/dewarp/core/engine.py`: calibration, metadata and batch execution.
- `app/src/dewarp/gui/`: Qt window, progress and Windows appearance.
- `scripts/`: setup and launcher support; `tests/` and `tools/`: validation.
- `archive/`: historical scripts, unused by current launchers.

```powershell
.\.venv\Scripts\python.exe tools/run_checks.py
```

The implementation follows OpenCV's documented relationship between
[undistort and remapping](https://docs.opencv.org/4.x/d9/d0c/group__calib3d.html)
and ExifTool's documented
[stay-open command protocol](https://exiftool.org/exiftool_pod.html#stay_open-FLAG).
Pillow added JPEG `xmp=` saving in
[version 11.0](https://pillow.readthedocs.io/en/stable/releasenotes/11.0.0.html),
which is enforced so older versions cannot silently omit the output marker.

Checks use explicit process-tree deadlines. The synthetic suite stops after
120 seconds overall or 60 seconds without output. Run native accessibility and
scaling checks separately with `python tools/run_gui_audits.py`.

The reusable real-photo fixture is **opt-in**: `python tools/test_real_fixture.py`
tests twelve evenly spaced copies per photo folder in CLI/GUI single and batch
workflows, saves timings and verification evidence, removes its scratch outputs,
and compares SHA-256/size/modification-time manifests for all originals.
`--full` explicitly selects every photo and should only be used when relevant
to a processing or workflow change. Cosmetic GUI and documentation changes do
not need real-photo runs. See [working rules](development/AGENTS.md) for timeout and cleanup rules.

## Scaling and accessibility

Setup and processing have separate screens. Cancel stays on the left of the
fixed footer; Start or Back to setup stays on the right. The controls stack at
very narrow widths. Settings has its own fixed Cancel/Save footer. Normal-sized
windows fit each screen without outer scrolling. On small windows or with
enlarged text, content scrolls; keyboard focus brings controls into view. The
folder tables and activity log have their own scrollbars. Full paths can be
read and copied from **Ctrl+I** without making the main window wider.

The GUI follows the current Windows user's light/dark mode, including the native
title bar and dialogs. It uses system fonts and palettes and lets Qt follow
Windows contrast themes. Theme changes apply while the app stays open; no
Windows settings are changed by the app. See the [theme audit](system-theme-audit.md)
for direct verification and remaining manual checks. Text can also be enlarged
from the **View** menu without changing Windows settings. Table rows resize with
the font. Statuses use words and counts rather than depending on color.
**F1** displays keyboard help, and **Ctrl+I** opens a readable status summary.
Use **Ctrl+O** to add a folder, **Ctrl+Enter** to start, and **Escape** to cancel an
active batch. Tab/Shift+Tab navigate controls; Enter or Space activate a focused
button. Pressing Enter while editing an output path does not start processing.

Browsing buttons are blue and **Start batch** is green when available. Disabled
actions are grey: Start needs a queued folder, Remove needs selected rows, and
Clear needs a nonempty queue. Processing disables editing and Start; Cancel
becomes unavailable after cancellation is requested. Windows high contrast
uses native colors instead of these accents. See the [button audit](button-action-audit.md).

See [the accessibility investigation](a11y-investigation.md) and the
recorded [setup/progress redesign audit](gui-screen-redesign.md) for current
verification and limits. The earlier [layout audit](gui-layout-audit.md)
records the preceding single-screen interface.
Qt documents its automatic Windows per-monitor support in
[High DPI](https://doc.qt.io/qt-6.10/highdpi.html) and its platform accessibility
interfaces in [Accessibility for QWidget applications](https://doc.qt.io/qt-6.10/accessible-qwidget.html).
