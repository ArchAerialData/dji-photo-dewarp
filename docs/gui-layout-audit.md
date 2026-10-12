# GUI layout audit — 2026-10-10

Historical single-screen audit. See the [separate-screen redesign](gui-screen-redesign.md)
for the current setup, processing and Settings interface and its verification.

This records the earlier processing/accessibility overhaul. The later package
move and system-theme verification are recorded in the [appearance and packaging
audit](packaging-overhaul-audit.md); current source paths are under `app/src/dewarp`.

The updated GUI keeps **Start batch** and **Cancel batch** completely visible,
with their full button text, in all 288 measured cases. Other controls remain
reachable by scrolling. Long paths, the current folder, and the latest filename
use read-only text fields that support horizontal navigation and copying.

## Before the changes

[Baseline evidence](gui-audit-before.json) records 96 actual Tk layouts: six DPI
equivalents from 96 through 288, normal and 150% named-font sizes, four client
sizes from 640×480 through 1100×820, and idle/running states. The test included
long paths, filenames, and large counters. It overrode the window minimum to
model a constrained desktop; otherwise the old minimum prevented testing a
small available viewport.

- Primary actions were unreachable in 84 of 96 cases. At 640×480 and 96 DPI,
  Start batch extended below the client area. At 1100×820 and 192 DPI, it was
  also below the window.
- All 96 cases had labels that exceeded their allocated space.
- In 80 cases the fixed 28-pixel queue rows were shorter than their font plus
  basic padding. At 192 DPI, the font itself required 32 pixels.
- There were no Tk callback exceptions during those measurements.

The original Tk source is identified by SHA-256 in the baseline evidence. The
legacy probe supports `--source` so the baseline can be reproduced against a
saved original module instead of the replacement GUI.

## After the changes

[Final evidence](gui-audit-after.json) records 288 native Windows Qt layouts:

- Effective device scale: 100%, 125%, 150%, 200%, 250%, and 300%.
- In-app text size: 100%, 125%, 150%, and 200%.
- Requested logical clients: 320×240, 480×360, 640×480, 800×600, 1024×768,
  and 1100×820.
- Idle and running states, with long paths/filenames and large live counters.

Each scale runs in a fresh process. The probe measures native screen scaling
first and normalizes `QT_SCALE_FACTOR`, so the target percentages do not
accidentally multiply the host's existing 125% scale. It records actual device
pixel ratio and actual client dimensions for every case.

| Check | Failures |
| --- | ---: |
| Start/Cancel rectangle clipped by any ancestor, at either scroll extreme | 0 |
| Start/Cancel glyphs exceed the native button content rectangle | 0 |
| Other actions cannot be fully reached by scrolling | 0 |
| Labels exceed their available text rectangle | 0 |
| Queue rows too short for their font | 0 |
| Initial native window frame outside its monitor work area | 0 |
| Minimum layout hint exceeds the monitor work area | 0 |

Windows capped the client height at 718 logical pixels for 16 cases requesting
1024×768 or 1100×820 at 300% device scale. Those cases passed at the **actual**
recorded size, not the larger requested height. Deliberately oversized stress
windows are separate from the startup check, which verifies the entire native
frame fits the available monitor work area.

The audit exposed and verified fixes for unbroken long folder/filename labels
and automatic window placement across monitors. The final JSON includes the
SHA-256 of the GUI source that was measured.

An additional [terminal-state audit](gui-terminal-state-audit.json) covers
Finished with issues, Batch error, Cancelled, and empty-result footer text at
320×240 and 480×360, 100% and 200% text, and both scroll extremes. It found
clipped status text in four cases, although Start/Cancel stayed visible.
Keeping the terminal footer to status plus percentage resolved the issue;
all **32 terminal-state cases** now pass. Full counts remain in Live progress
and the Ctrl+I snapshot. This probe uses the native 125% monitor scale.

## Reproduction and visual inspection

```powershell
python tools/run_gui_audits.py
```

The bounded wrapper runs native accessibility and layout audits serially. Each
main audit has a 240-second wall deadline and a 180-second no-output deadline;
the terminal-state probe uses 30-second wall and 25-second no-output limits. A
timeout stops only its child process tree. It returns a nonzero exit code for
timeout, accessibility, geometry, text, row-height, startup
placement, or minimum-layout failures. Window-manager size caps are informational.
It does not process photos or change Windows display settings. Synthetic live
values are held stable; processing behavior is covered by the GUI integration
tests separately.

The app-only captures were inspected for the primary controls and scrolling:

- [Normal window, idle at the top](gui-audit-images/normal-1100x820.png)
- [Normal window, running progress](gui-audit-images/normal-running-progress-1100x820.png)
- [480×360 with 200% text](gui-audit-images/small-480x360-text-200.png)
- [640×480 at 200% device scale and 150% text](gui-audit-images/dpi-200-small-640x480-text-150.png)

These are Qt widget renders of synthetic test states, not desktop screenshots.

## Regression and workflow recheck

The initial migration check on 2026-10-10 passed all **27 tests** in 4.886 seconds.
After the subsequent [workflow fixes](gui-workflow-audit.md), all **15 GUI tests**
passed in 2.927 seconds under a 120-second hard process-tree timeout. The native
audits were repeated against the final GUI: all **288 layout cases**,
**42 accessibility checks**, and **32 terminal-state cases** passed.
The [bounded-run evidence](gui-audit-rerun.json) records 1.601 seconds for idle
accessibility, 1.611 seconds for running accessibility, 27.668 seconds for the
layout matrix, and 1.145 seconds for terminal states, with no timeouts.

The [GUI tests](../tests/test_gui_qt.py)
exercise real synthetic batch processing, weighted progress, dropped folders,
keyboard activation and shortcuts, focus scrolling, enlarged text, system-font
changes, cancellation, and completion while the close-confirmation dialog is
open. The [engine tests](../tests/test_dewarp.py) cover calibration, pixels,
metadata, output collisions, errors, cancellation, and CLI compatibility.

Python compilation and `git diff --check` also passed. During the initial check, invoking
`dewarp --help` through an interactive Git Bash shell succeeded and displayed
the current batch CLI. The existing alias still launches `dewarp.py`; no alias
change was needed. The native Windows accessibility audit separately passed
21 checks in each of its idle and running states.

## Limits

This verifies native layout under controlled device and text scaling. It does
not certify physical movement between monitors with different configured DPI,
Narrator speech, or every assistive technology. Accessibility API and palette
checks are documented separately in [the accessibility investigation](a11y-investigation.md).
