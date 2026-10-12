# Separate setup, settings and processing screens

This revision implements the requested screen changes. It supersedes the
single-page layout described in the earlier GUI audit reports.

| Finding / request | Change |
| --- | --- |
| Start and Cancel were in the wrong order. | Cancel is fixed on the left; Start is on the right. At very narrow widths the controls stack. |
| Processing options took space on every run. | File → Settings edits persistent per-user defaults. Preserve XMP and Crop edges default on; two parallel photos is the default. |
| Selected input/output fields duplicated the output browser. | Setup has one output parent selection inside the folder queue section. Full paths remain in table tooltips and the copyable Ctrl+I snapshot. |
| Setup, progress and console formed one long scrolling page. | Setup and processing have separate pages in the same window. Starting switches to progress, folder results and activity. |
| Another run needed an obvious return path. | Back to setup becomes available after the worker exits, including after cancellation or failure. The queue and output location are retained for editing and reruns. |
| Top-level Qt dialogs did not inherit application text enlargement. | The Settings dialog explicitly enables window font propagation. Its Cancel/Save footer remains outside the scrolling content. |
| Generic table/editor size hints still forced scrolling at a normal window size. | The table and activity log prefer font-based minimum heights and expand into available space. Their internal scrollbars remain available for larger queues/logs. |
| Scrolling a native Settings spin control into view could clip its frame. | Settings and main pages share a scroll surface that reveals the full styled control, including its focus outline. |
| Repeatedly opening a parented dialog retained its hidden widgets. | Settings, status and help dialogs explicitly release their widgets after closing. |
| Returning to setup disabled access to prior outputs. | File → Open selected output works for retained results on either page. |
| Error/empty-result footer wording clipped at 320×240 with 200% text. | The footer uses short status text; full explanations remain in progress and the Ctrl+I snapshot. |
| Windows 10 displayed empty squares instead of Settings arrows/checkmarks. | When Segoe Fluent Icons is absent, Qt uses the installed Segoe MDL2 Assets symbol font inside this process. The native style, text font, palette and title bars remain unchanged. |
| Invalid or unwritable saved preferences could silently misconfigure a run. | Saved values are validated individually; invalid values fall back to defaults. Save failures are reported instead of claiming success. |

## Implementation

- [Main window and state transitions](../app/src/dewarp/gui/window.py)
- [Settings storage and dialog](../app/src/dewarp/gui/settings.py)
- [Shared focus scrolling](../app/src/dewarp/gui/scrolling.py)
- [System appearance and symbol fallback](../app/src/dewarp/gui/theme.py)
- [User workflow](user-guide.md)
- [Synthetic workflow tests](../tests/test_gui_qt.py)
- [Settings persistence, cancellation and text propagation tests](../tests/test_settings.py)

Defaults are stored using Qt's per-user settings under organization
`DewarpM3EJPGs`, application `DJI Photo Dewarp` (the current user's registry on
Windows). Only Save persists dialog changes. Tests use isolated temporary INI
files; they do not overwrite the user's preferences. CLI defaults are unchanged.

The processing engine remains unchanged, with SHA-256
`ee374ea47d04add43668af21a2f7dca64c2eb8a2b73ca13461a649d407838082`.
The `dewarp` alias still points to the stable `scripts/dewarp.py` entry point.

The Windows 10 symbol fix is supported by [font/glyph measurements and Qt primary
sources](gui-screen-symbol-font.json). All 21 symbols needed by this app resolve;
Qt's additional `Dash12` symbol is absent from the older font and is only used
for tristate checkboxes, which this app does not expose. No fonts are installed
and no Windows preferences are changed.

## Verification

The [full synthetic suite](gui-screen-tests.json) passed **84 tests** in
20.923 seconds (21.666 seconds including startup), with 120-second wall and
60-second inactivity deadlines. It covers single-folder and multi-folder GUI
processing, unchanged synthetic originals, unique rerun outputs, cancellation,
errors, returning to setup, settings persistence across a fresh process, invalid
preferences, save failures, keyboard use, dialog cleanup, text zoom and native
light/dark title-bar attributes for the main window and Settings dialog, and
the native symbol-font fallback. Queue data/reindex tests use explicit model
selection rather than Qt's header/modifier-dependent `selectRow()` convenience
method. A separate keyboard test selects another row and deletes it through the
real GUI event path.

The [Git Bash alias check](gui-screen-alias.json) passed in a fresh login shell:
`dewarp --help` resolves through `scripts/dewarp.py`, with 30/20-second deadlines.

The [bounded native audits](gui-screen-audits.json) passed:

- **576 layout cases**: setup, running, completed and Settings; six device scales
  from 100–300%, four text sizes from 100–200%, and six requested window sizes
  from 320×240 through 1100×820. No clipped critical buttons/text, unreachable
  actions, incorrect button order or normal-size outer scrolling.
  [Measurements and source hashes](gui-screen-layout.json)
- **40 Windows UI Automation checks** across the four views, covering native
  names, control types, interaction patterns, enabled states and live values.
  [Setup](gui-screen-accessibility-setup.json),
  [running](gui-screen-accessibility-running.json),
  [completed](gui-screen-accessibility-completed.json),
  [Settings](gui-screen-accessibility-settings.json)
- **48 completion/error/cancellation/empty-result and mixed-folder-feedback
  cases**, including return to setup and footer visibility at 320×240 and
  480×360 with 100%/200% text. [Measurements](gui-screen-terminal-states.json)

Large audits have 240-second wall and 180-second inactivity deadlines;
terminal-state checks have 30/25-second deadlines. Windows adjusted requested
client sizes in 50 matrix cases; the audit uses and records the actual sizes.
Screenshots were visually inspected for [setup](gui-screen-images/normal-1100x820.png),
[processing](gui-screen-images/normal-running-progress-1100x820.png), and
[Settings](gui-screen-images/settings-640x480.png).

Geometry and native accessibility
API checks do not certify spoken screen-reader behavior, actual Windows contrast
theme sessions or physical transitions between monitors with different DPI.

The real-photo fixture was not used for this screen redesign. No processing
algorithm, JPEG encoder settings or metadata path changed. Existing performance
measurements remain the relevant baseline; this revision makes no new speed claim.
