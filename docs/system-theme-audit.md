# System appearance and window chrome

The GUI follows the current user's application appearance. On Windows it uses
Qt's `windows11` style, with the built-in `Fusion` style as a fallback when that
plugin is unavailable. Other operating systems retain their native Qt style.
There is no separate saved light/dark preference, application-wide palette or
stylesheet override, custom title bar, or registry-writing code. A later
[button update](button-action-audit.md) adds scoped blue/green action accents;
those styles are removed for Windows high contrast. The original theme
measurements below predate that update; its focused theme regressions also pass.

The previous default `windowsvista` style was the cause of the light interface:
on this machine Qt reported `ColorScheme.Dark`, while that style returned a light
`#f0f0f0` window background. Qt documents that its legacy Vista style replaces
the system palette with the light palette. A theme-aware style lets Qt manage
the controls and native window frame together. See Qt's [Windows dark-mode
explanation](https://www.qt.io/blog/dark-mode-on-windows-11-with-qt-6.5).

## Verification

Run `python tools/audit_gui_theme.py` on Windows. It has a 30-second wall timeout
and a 15-second no-output timeout, and owns its subprocess tree. It reads no
photos and starts no processing. It checks the current user's application
preference, changes only this test process between light and dark, restores
system following, and verifies that the user's Windows preferences are unchanged.

For each appearance, it reads `DWMWA_USE_IMMERSIVE_DARK_MODE` from the real HWND
of the main window, a modal dialog, and a message box. It also records palette
roles, font stability, footer visibility, and accessible button names. Screenshots
capture the audit window's device context within its visible DWM frame. The
audit makes only its own window temporarily topmost to prevent occlusion. It
excludes invisible outer margins and the lower two-pixel shadow edge. This
captures the actual composed dark title bar, including the standard caption
buttons, without capturing unrelated desktop areas.

On the development Windows 10 build 19045 machine with Qt 6.10.2, all **62 API/palette
checks passed**, and all four dedicated theme tests passed. The recorded
system preference is dark; its window background is `#1e1e1e` and its native
dark-frame attribute is enabled. The light process override produces a
`#f3f3f3` window background and disables that attribute. No font changed.

Visual inspection verified dark client controls and a dark native frame with
the actual current-user setting. The per-process light override changes the
reported DWM attribute but leaves the captured frame following Windows' actual
dark setting on this machine. Therefore this is **not visual certification of
an actual Windows light-setting transition**. The light screenshot deliberately
records that limitation rather than altering the system settings to hide it.
`PrintWindow` is unsuitable for this check: it rendered legacy/light frame
artifacts even for the confirmed dark composed window.

- Machine-readable result: [system-theme-audit.json](system-theme-audit.json)
- Process bounds: [system-theme-audit-bounds.json](system-theme-audit-bounds.json)
- Output log: [system-theme-audit.log](system-theme-audit.log)
- Dark main window: [theme-images/dark-main.png](theme-images/dark-main.png)
- Light client override (actual Windows dark chrome): [theme-images/light-main.png](theme-images/light-main.png)
- Dark message box: [theme-images/dark-message.png](theme-images/dark-message.png)

`tests/test_theme.py` additionally covers idempotent installation, missing-style
fallback, retaining other platforms' styles, and repeated light/dark transitions
with native dialogs. The bounded synthetic runner includes these tests.

## Design and limits

Qt's [QStyleHints contract](https://doc.qt.io/qt-6.10/qstylehints.html) specifies
that the default scheme follows system changes, and updates the platform palette.
The application clears an explicit scheme only at initial setup; it leaves later
updates to Qt. It does not override fonts or native caption, border, accent, or
text colors. Qt's [6.10 contrast support](https://www.qt.io/blog/high-contrast-mode-in-qt-6.10)
includes both selected Windows styles. This preserves the platform's opportunity
to honor contrast settings instead of substituting a handcrafted dark palette.

The DWM check uses Microsoft's documented [window attribute
20](https://learn.microsoft.com/en-us/windows/win32/api/dwmapi/ne-dwmapi-dwmwindowattribute),
whose official support starts with Windows 11 build 22000. Qt owns the actual
native-frame updates; the application does not duplicate them through ctypes.
The local Windows 10 build also supports reading the attribute in this audit;
that observed compatibility does not extend Microsoft's documented guarantee.

The light/dark transition audit uses Qt's documented per-process override, not a
change to Windows Settings. An actual OS contrast-theme visual session, spoken
screen-reader session, native file-picker appearance, and another physical
computer remain separate manual checks. Automated palette and API checks do not
certify those experiences. No operating-system theme setting is changed here.
