# Windows GUI accessibility investigation — 2026-10-10

This investigation distinguishes native accessibility metadata from actual screen-reader usability. A Windows UI Automation (UIA) inspection is direct evidence of the properties available to assistive tools; it is not a substitute for an end-to-end Narrator or NVDA session. Microsoft describes this distinction in the [UI Automation tree overview](https://learn.microsoft.com/en-us/dotnet/framework/ui-automation/ui-automation-tree-overview).

## Verified baseline

The installed runtime is Python 3.13.3 with Tk 8.6.15. The initial DewarpApp ran DPI-unaware; Tk reported a logical screen size of 2048 × 864 and 1.333 pixels per point. A separate, disposable Python process made system-DPI-aware before creating Tk reported 2560 × 1080, 120 DPI (125%), and 1.666 pixels per point. No display or accessibility settings were changed.

A separate visible instance of the original DewarpApp was inspected through `System.Windows.Automation.AutomationElement.FromHandle`. Its 45 UIA descendants were unnamed `ControlType.Pane`/`TkChild` elements without supported control patterns. Direct inspection of the native handles for **Add folder**, **Start batch**, and **Cancel batch** produced the same result. The actual Tk controls were mapped and visible. The API `tk accessible check_screenreader` failed with an unknown-subcommand error. [Captured baseline and Qt prototype](accessibility-baseline.json).

These observations confirm a toolkit-level accessibility gap in the original GUI. Tk 8.6's [documented commands](https://www.tcl-lang.org/man/tcl8.6/TkCmd/tk.htm) do not include the newer [Tk 9.1 accessibility API](https://www.tcl-lang.org/man/tcl9.1/TkCmd/accessible.html). Adding labels, keyboard shortcuts, or a scrollable layout to the old Tk application would not by itself expose its buttons and progress values to Windows UIA.

## Verified replacement capability

PySide6/Qt 6.10.2 was already installed. A disposable Qt Widgets prototype exposed:

| Widget | UIA role | Directly observed patterns |
| --- | --- | --- |
| Add folder / Start batch / Cancel batch | Named Button | Invoke |
| Current folder progress | Named ProgressBar | RangeValue |
| Folder queue | Named Table | Selection, Grid, Table |
| Folder names / statuses / counts | Named DataItem | GridItem, SelectionItem, TableItem |

The prototype automatically used the monitor's 1.25 device pixel ratio and a DPI-aware process. The final application was then audited separately, as described below. [Captured prototype](accessibility-baseline.json).

Qt 6.10's [high-DPI documentation](https://doc.qt.io/qt-6.10/highdpi.html) specifies automatic Windows Per-Monitor V2 support and device-independent layout. Its [accessibility guidance](https://doc.qt.io/qt-6.10/accessible.html) documents system font/palette defaults and keyboard support. Standard Qt Widgets already have [accessibility interfaces](https://doc.qt.io/qt-6.10/accessible-qwidget.html). This provides a maintainable basis for the required scaling and accessibility fixes without custom Win32 message hooks or experimental Tk upgrades.

## Final application: native Windows UIA verification

The actual replacement DewarpApp passed **21/21 checks in the idle state and 21/21 checks in a simulated running state**. The test did not start the photo-processing worker. It checked the native Windows provider, not just Qt's internal accessibility interfaces. Each capture records the source file's SHA-256, runtime version, and timestamp. [Idle evidence](accessibility-after.json), [running-state evidence](accessibility-running.json).

- Start, Cancel, Add, Remove, Clear, and Open output expose named Buttons with Invoke patterns. Their enabled states match application state.
- Output location, selected input/output paths, current-folder status, and latest-photo text expose named Edit controls with the complete string available through Value. Read-only text remains selectable and keyboard-focusable.
- XMP preservation exposes a CheckBox with Toggle; framing exposes a ComboBox with ExpandCollapse and its selected value; parallel-photo count exposes a Spinner with value and range 1–4. The activity log exposes an Edit control with Text and Value patterns.
- The queue exposes a named Table with Grid support, all five named column headers, and named cells.
- Current-folder and overall progress expose named ProgressBars with RangeValue; the fixture's 37 and 18 values are available with the expected 0–100 range.
- Non-default photo counts and footer status, plus queue overview, expose their actual current text through the native Text element name. This checks that generic accessibility labels do not hide live values.
- Windows directly reports Per-Monitor V2 awareness for the real application's window. The test monitor's effective device pixel ratio is 1.25.

The reusable audit launches a separate GUI instance, performs read-only UIA queries, and signals only that instance to exit. The child also has a 30-second safety timer. The bounded wrapper runs idle, running, and layout checks serially, each with a 240-second wall deadline and a 180-second no-output deadline. Its terminal-state geometry probe has a 30-second wall deadline and a 25-second no-output deadline:

```powershell
python tools/run_gui_audits.py
```

After the [workflow fixes](gui-workflow-audit.md), the native audits were repeated
against GUI SHA-256 `6ee432f8b3ba2bffac3328a8ca51071212b6f6b07ab04afd87ddf55a73908123`.
All 21 idle checks and 21 running checks passed again, in 1.601 and 1.611 seconds
respectively, without timeouts. The captured files above have been refreshed;
[bounded-run evidence](gui-audit-rerun.json) records their commands and limits.
The palette-inheritance capture remains the initial migration check and has not
been represented as a new real Windows contrast-theme test.

An additional runtime test changed only the disposable application's palette to black/white, verified palette-role inheritance in ten controls, restored the original palette, and verified there were no widget style sheets overriding colors. **This is a palette-inheritance test, not a real Windows contrast-theme visual test**: the native Windows Vista style can draw themed backgrounds independently of a synthetic palette. No Windows contrast settings were changed. [Palette evidence](accessibility-palette.json).

## Implementation and verification requirements

- Keep Start/Cancel in an unscrolled footer. Let the main content scroll, including horizontal access to wide table columns. Bring newly keyboard-focused controls into view.
- Size buttons and table rows from current fonts, and allow larger text without truncating labels. Preserve visible focus indicators. Add named inputs, label buddies, and keyboard accelerators.
- Use native palette roles, including text/background pairs and selection colors. Preserve written status labels so information does not depend on color. Microsoft advises against overriding users' [contrast preferences](https://learn.microsoft.com/en-us/windows/win32/winauto/accessibility-best-practices).
- Give both progress bars accessible names and numeric ranges. Announce important transitions sparingly; do not produce speech for every photo.
- Do not call a Win32 DPI-awareness setter after window creation or override Qt's default. Microsoft requires process awareness to be configured [before any HWND exists](https://learn.microsoft.com/en-us/windows/win32/hidpi/setting-the-default-dpi-awareness-for-a-process).
- Test effective scale factors, not just environment-variable values: `QT_SCALE_FACTOR` multiplies the native device pixel ratio. On this 125% monitor, factors 0.8, 1, 1.2, 1.6, and 2.4 produce effective 100%, 125%, 150%, 200%, and 300% scaling respectively. This is process-only simulation; real movement between monitors and live Windows setting changes remain separate tests.

## Manual acceptance still needed

Run a complete queue/options/start/progress/cancel workflow with Narrator or NVDA, including reading table headers/cells and modal errors. Test real Windows contrast themes and large system text, and move the window between different-DPI monitors while processing. Automated geometry and UIA checks cannot establish how a specific screen-reader version announces every interaction, nor prove all possible screen sizes or assistive-device configurations.
