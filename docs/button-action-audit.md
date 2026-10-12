# Button colors and availability

This records the first button-color revision. The later
[separate-screen redesign](gui-screen-redesign.md) adds Settings and Back to
setup, and contains the current workflow and layout verification.

Browsing actions (**Add folder**, **Browse**, **Open output**) use blue accents.
**Start batch** uses green. These buttons become neutral grey when disabled;
other unavailable controls retain their native disabled appearance. Hover,
pressed and keyboard-focus states remain distinct. Colors supplement the text
labels and native enabled/disabled accessibility state.

Start is available only with a nonempty queue while idle. Remove needs selected
rows; Clear needs a nonempty queue; Use input folders needs a custom output path;
Open output needs an existing output directory. Editing actions are disabled
during processing. Cancel is enabled only while a batch is running and has not
already received a cancellation request. Menu commands use the same predicates.

Styles apply only to role-colored buttons. The system theme still owns
the window, dialogs, fonts and native title bar. Qt's live contrast-preference
signal removes button accent styles for Windows high contrast, restoring native
colors. No Windows setting is changed. Font size and control dimensions remain
responsive; no fixed font or fixed button size is introduced.

## Verification

- **25 GUI/theme/button tests passed**, including queue changes, selection,
  synthetic batch completion, cancellation, worker errors, keyboard operation,
  actual rendered blue/green/grey pixels in both light and dark mode, and a
  simulated contrast-preference fallback. [Results](button-regressions.json)
- **288 DPI/layout cases, 42 native accessibility checks, and 32 terminal-state
  cases passed**. No clipped critical controls or unreachable actions.
  [Audit run](gui-audit-buttons.json)
- All six enabled normal/hover/pressed background colors have at least **4.5:1**
  contrast against their white labels, computed without rounding thresholds.
  Disabled buttons cannot emit a click through normal activation.
  [Button tests](../tests/test_buttons.py)
- Visually inspected the [ready state](gui-audit-images/normal-1100x820.png)
  and [running state](gui-audit-images/normal-running-progress-1100x820.png).
  The fixed footer remains available at small sizes and enlarged text.

Tests are bounded: 60 seconds wall / 30 seconds without output for the focused
regressions; 240/180 seconds for each large native audit, 30/25 for terminal
states. No real-photo fixture was read or processed. The processing engine and
launcher/alias are unchanged. Actual OS contrast-theme visuals and spoken
screen-reader behavior still require manual verification; mocking the Qt
preference only verifies that application styling gets out of the way.

Implementation follows Qt's [scoped button styles and interaction states](https://doc.qt.io/qt-6.10/stylesheet-examples.html#customizing-a-qpushbutton-using-the-box-model).
The text-color target follows the [W3C contrast calculation and 4.5:1 criterion](https://www.w3.org/WAI/WCAG22/Understanding/contrast-minimum.html),
used here as a desktop readability benchmark, not a claim of complete WCAG conformance.
