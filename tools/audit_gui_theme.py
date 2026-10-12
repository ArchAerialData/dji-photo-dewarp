"""Bounded Windows theme audit. Overrides this process only; never changes Windows settings.

Captures only the audit application's HWND, including its native title bar.
No photo fixture is read and no processing is started.
"""
from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app" / "src"))


def _native_dark_frame(window):
    api = ctypes.WinDLL("dwmapi")
    api.DwmGetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
    api.DwmGetWindowAttribute.restype = ctypes.c_long
    value = wintypes.BOOL()
    result = api.DwmGetWindowAttribute(int(window.winId()), 20, ctypes.byref(value), ctypes.sizeof(value))
    return {"hresult": result, "dark": bool(value.value)}


def _user_preferences():
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as key:
        result = {}
        for name in ("AppsUseLightTheme", "SystemUsesLightTheme"):
            try:
                result[name] = winreg.QueryValueEx(key, name)[0]
            except FileNotFoundError:
                result[name] = None
        return result


def _capture_window(window):
    """Copy only this owned HWND's visible frame from its window DC.

    PrintWindow renders a legacy/light non-client frame on this runtime even
    when the composed native frame is dark. Use the owned, topmost window's DC,
    clipped to DWM's visible frame: GetWindowRect includes invisible margins
    which can contain pixels from other windows. Never use the desktop DC.
    """
    from PIL import Image
    c, w = ctypes, wintypes
    user, gdi = c.WinDLL("user32", use_last_error=True), c.WinDLL("gdi32", use_last_error=True)
    signatures = [
        (user, "GetWindowRect", [w.HWND, c.POINTER(w.RECT)], w.BOOL),
        (user, "GetWindowDC", [w.HWND], w.HDC),
        (user, "ReleaseDC", [w.HWND, w.HDC], c.c_int),
        (gdi, "CreateCompatibleDC", [w.HDC], w.HDC),
        (gdi, "CreateCompatibleBitmap", [w.HDC, c.c_int, c.c_int], w.HBITMAP),
        (gdi, "SelectObject", [w.HDC, w.HANDLE], w.HANDLE),
        (gdi, "DeleteObject", [w.HANDLE], w.BOOL),
        (gdi, "DeleteDC", [w.HDC], w.BOOL),
        (gdi, "BitBlt", [w.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, w.HDC, ctypes.c_int, ctypes.c_int, w.DWORD], w.BOOL),
        (gdi, "GetDIBits", [w.HDC, w.HBITMAP, w.UINT, w.UINT, c.c_void_p, c.c_void_p, w.UINT], c.c_int),
    ]
    for api, name, arguments, result in signatures:
        function = getattr(api, name)
        function.argtypes, function.restype = arguments, result

    class BitmapHeader(c.Structure):
        _fields_ = [("size", w.DWORD), ("width", w.LONG), ("height", w.LONG),
                    ("planes", w.WORD), ("bits", w.WORD), ("compression", w.DWORD),
                    ("image_size", w.DWORD), ("x_res", w.LONG), ("y_res", w.LONG),
                    ("colors_used", w.DWORD), ("important_colors", w.DWORD)]

    handle = int(window.winId())
    rect = w.RECT()
    if not user.GetWindowRect(handle, c.byref(rect)):
        raise c.WinError(c.get_last_error())
    dwm = c.WinDLL("dwmapi")
    dwm.DwmGetWindowAttribute.argtypes = [w.HWND, w.DWORD, c.c_void_p, w.DWORD]
    dwm.DwmGetWindowAttribute.restype = c.c_long
    visible = w.RECT()
    if dwm.DwmGetWindowAttribute(handle, 9, c.byref(visible), c.sizeof(visible)) != 0:
        raise OSError("Could not identify the owned window's visible bounds")
    x, y = visible.left - rect.left, visible.top - rect.top
    width, height = visible.right - visible.left, visible.bottom - visible.top - 2
    source = user.GetWindowDC(handle)
    target = gdi.CreateCompatibleDC(source)
    bitmap = gdi.CreateCompatibleBitmap(source, width, height)
    previous = gdi.SelectObject(target, bitmap)
    try:
        if not all((source, target, bitmap, previous)) or not gdi.BitBlt(target, 0, 0, width, height, source, x, y, 0x00CC0020):
            raise OSError("Could not capture the owned audit window")
        gdi.SelectObject(target, previous)
        previous = None
        header = BitmapHeader(c.sizeof(BitmapHeader), width, -height, 1, 32)
        pixels = c.create_string_buffer(width * height * 4)
        if gdi.GetDIBits(target, bitmap, 0, height, pixels, c.byref(header), 0) != height:
            raise c.WinError(c.get_last_error())
        return Image.frombytes("RGB", (width, height), pixels.raw, "raw", "BGRX")
    finally:
        if previous:
            gdi.SelectObject(target, previous)
        if bitmap:
            gdi.DeleteObject(bitmap)
        if target:
            gdi.DeleteDC(target)
        if source:
            user.ReleaseDC(handle, source)


def _worker(output):
    from PySide6.QtCore import Qt, qVersion
    from PySide6.QtGui import QPalette
    from PySide6.QtWidgets import QApplication, QDialog, QLabel, QMessageBox, QVBoxLayout
    from dewarp.gui.window import DewarpApp

    app = QApplication([])
    app.setQuitOnLastWindowClosed(False)
    window = DewarpApp()
    window.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
    window.resize(1100, 760)
    window.show()
    hints = app.styleHints()
    accessibility = hints.accessibility()
    original_font = app.font().toString()
    report = {
        "captured_utc": datetime.now(timezone.utc).isoformat(),
        "qt_version": qVersion(), "windows_version": str(sys.getwindowsversion()),
        "style": app.style().objectName(), "current_user_preferences_before": _user_preferences(),
        "contrast_preference": accessibility.contrastPreference().name,
        "source_sha256": {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
                          for path in (ROOT / "app/src/dewarp/gui/window.py", ROOT / "app/src/dewarp/gui/theme.py")},
        "checks": [], "states": [],
        "limitations": ["Light/dark transitions use Qt's per-process override, not a Windows Settings change.",
                        "Actual Windows contrast-theme appearance and screen-reader speech are not certified by this audit.",
                        "Screenshots use this application's window DC, clipped to its visible DWM frame; native file-picker dialogs are owned by Windows."],
    }
    screenshots = output.parent / "theme-images"
    screenshots.mkdir(exist_ok=True)

    def pump():
        # Process a bounded number of frame/paint cycles; no nested modal exec.
        deadline = time.monotonic() + 0.50
        while time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.01)

    def check(name, passed):
        report["checks"].append({"name": name, "passed": bool(passed)})

    def capture(label):
        window.activateWindow()
        window.raise_()
        pump()
        dark = hints.colorScheme() == Qt.ColorScheme.Dark
        high_contrast = accessibility.contrastPreference() == Qt.ContrastPreference.HighContrast
        current = {"label": label, "scheme": hints.colorScheme().name, "windows": []}
        image = _capture_window(window)
        name = screenshots / f"{label}-main.png"
        image.save(name)
        current["main_screenshot"] = str(name.relative_to(ROOT))
        dialog = QDialog(window)
        dialog.setWindowTitle("Dewarp theme audit — modal dialog")
        dialog.setWindowModality(Qt.WindowModality.ApplicationModal)
        QVBoxLayout(dialog).addWidget(QLabel("Dialog text follows the user's system appearance."))
        message = QMessageBox(QMessageBox.Icon.Information, "Dewarp theme audit", "System colors also apply to messages.", parent=window)
        try:
            dialog.show()
            message.show()
            pump()
            for name, item in (("main", window), ("modal", dialog), ("message", message)):
                palette = item.palette()
                frame = _native_dark_frame(item)
                values = {role.name: palette.color(role).name() for role in
                          (QPalette.ColorRole.Window, QPalette.ColorRole.WindowText, QPalette.ColorRole.Base,
                           QPalette.ColorRole.Text, QPalette.ColorRole.Button, QPalette.ColorRole.ButtonText,
                           QPalette.ColorRole.Highlight, QPalette.ColorRole.HighlightedText)}
                current["windows"].append({"name": name, "palette": values, "native_frame": frame})
                check(f"{label}.{name}.native_attribute_available", frame["hresult"] == 0)
                if not high_contrast:
                    check(f"{label}.{name}.native_frame_matches_scheme", frame["dark"] == dark)
                    check(f"{label}.{name}.palette_matches_scheme",
                          (palette.color(QPalette.ColorRole.Window).lightness() < palette.color(QPalette.ColorRole.WindowText).lightness()) == dark)
                check(f"{label}.{name}.no_stylesheet", item.styleSheet() == "")
            if label == "dark":
                _capture_window(message).save(screenshots / "dark-message.png")
            check(f"{label}.font_unchanged", app.font().toString() == original_font)
            check(f"{label}.footer_visible", window.start_button.isVisible() and window.cancel_button.isVisible())
            check(f"{label}.native_controls_accessible", bool(window.start_button.accessibleName()) and bool(window.cancel_button.accessibleName()))
            report["states"].append(current)
            print(json.dumps({"state": label, "scheme": current["scheme"], "checks": len(report["checks"])}), flush=True)
        finally:
            message.close()
            dialog.close()
            message.deleteLater()
            dialog.deleteLater()
            pump()

    try:
        capture("system")
        preference = report["current_user_preferences_before"]["AppsUseLightTheme"]
        if preference is not None and accessibility.contrastPreference() != Qt.ContrastPreference.HighContrast:
            check("system.follows_current_user_app_preference", (hints.colorScheme() == Qt.ColorScheme.Light) == bool(preference))
        if accessibility.contrastPreference() != Qt.ContrastPreference.HighContrast:
            hints.setColorScheme(Qt.ColorScheme.Light)
            capture("light")
            hints.setColorScheme(Qt.ColorScheme.Dark)
            capture("dark")
        hints.unsetColorScheme()
        capture("restored-system")
        report["current_user_preferences_after"] = _user_preferences()
        check("system.preferences_unchanged", report["current_user_preferences_before"] == report["current_user_preferences_after"])
        report["passed"] = all(item["passed"] for item in report["checks"])
        output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"passed": report["passed"], "checks": len(report["checks"])}), flush=True)
        return 0 if report["passed"] else 1
    finally:
        hints.unsetColorScheme()
        window.close()
        app.processEvents()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "docs/system-theme-audit.json")
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if sys.platform != "win32":
        parser.error("Native window chrome audit requires Windows.")
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    if args.worker:
        return _worker(output)
    from validation_support import run_bounded
    result = run_bounded([sys.executable, __file__, "--worker", "--output", output], cwd=ROOT,
                         timeout=30, stall_timeout=15, log_path=output.with_suffix(".log"))
    output.with_name(output.stem + "-bounds.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))
    return 1 if result["timeout"] or result["exit_code"] else 0


if __name__ == "__main__":
    raise SystemExit(main())

