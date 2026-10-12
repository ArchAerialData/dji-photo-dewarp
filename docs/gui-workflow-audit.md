# GUI workflow audit — 2026-10-10

Historical single-screen audit. The [separate-screen redesign](gui-screen-redesign.md)
records the current workflow and updated regression evidence.

This records the earlier processing/accessibility overhaul. The later package
move and system-theme verification are recorded in the [appearance and packaging
audit](packaging-overhaul-audit.md); current source paths are under `app/src/dewarp`.

The following findings were reproduced through event-driven or real synthetic
batch tests and resolved in [the GUI](../app/src/dewarp/gui/window.py). The original fixture
photos were not used by these checks.

| Finding | Resolution | Regression evidence |
| --- | --- | --- |
| Removing one row discarded the retained folders' result counts and output links. | Retained results and output paths are reindexed; removing the current row clears its old live details. | `test_removing_one_folder_preserves_other_results_and_output_links` |
| An unexpected batch-worker error left never-started folders marked Queued after the batch stopped. | The active folder becomes Failed; untouched folders become Not processed. Controls become usable again. | `test_worker_error_marks_never_started_folders_not_processed` |
| A folder containing only subfolders finished with stale Preparing/Scanning status and an unhelpful 0% summary. | Empty results explain that only direct JPEGs are processed and identify the need to add photo-containing subfolders. Live status resets after completion. | `test_parent_without_direct_photos_finishes_with_useful_guidance` |
| Completed batches with failed/skipped photos or metadata warnings announced only Finished. | The visible, compact, and accessible status say Finished with issues. Detailed result counts remain separate. | `test_completion_issues_are_in_visible_and_accessible_summary` |
| Start's description said queued folders although its established behavior processes all listed folders. | The accessible description and keyboard help explicitly explain all-folder reruns and separate output folders. Processing semantics stay consistent. | Source inspection and existing real-batch test |
| Per-folder elapsed time was not available in the GUI. | Engine `elapsed_seconds` values appear in Activity and the copyable Ctrl+I status snapshot. | Result-preservation test verifies the timing field |
| Finished with issues / Cancelled plus large counters clipped footer status text at 320×240 and 200% text. | Terminal footer text contains status and percentage; full counts remain in Live progress and Ctrl+I. Start/Cancel were fully visible before and after. | All 32 terminal-state geometry cases pass; [measurements](gui-terminal-state-audit.json), [bounded-run evidence](gui-terminal-state-audit-run.json) |

After the terminal-footer fix, all **15 GUI regression tests passed in 2.927
seconds** (3.612 seconds including interpreter startup) under a **120-second
hard process-tree timeout** and a **60-second no-output deadline**.
[Test output and limits](gui-workflow-tests.json) are saved alongside the
[full log](gui-workflow-tests.log). They also cover real synthetic batch outputs,
weighted progress, drag/drop, keyboard activation and shortcuts, 200% text,
fixed footer visibility, focus scrolling, native Qt accessibility interfaces,
system-font changes, cancellation, and the close-confirmation race.

Use `python tools/run_checks.py` for future synthetic regression checks. It
enforces a 120-second process-tree deadline and a 60-second no-output deadline.
It never processes the reusable photo fixture. Use
`python tools/run_gui_audits.py` for the separate native Windows accessibility
and geometry matrix; each audit has a 240-second wall deadline and a 180-second
no-output deadline. The additional terminal-state probe has a 30-second wall
deadline and a 25-second no-output deadline. Audits run serially to avoid competing window focus.

The native measurements and their limits are documented in the
[layout audit](gui-layout-audit.md) and
[accessibility investigation](a11y-investigation.md). Spoken screen-reader
behavior, actual Windows contrast themes, and physical transitions between
monitors configured at different DPI still require manual acceptance.
