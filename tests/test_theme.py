"""Theme behavior and Windows native title bars; no system settings are changed."""
import ctypes
from ctypes import wintypes
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app" / "src"))

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QFont, QPalette, QRawFont
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox

from dewarp.gui import theme
from dewarp.gui.window import DewarpApp
from dewarp.gui.settings import ProcessingSettings, SettingsDialog, SettingsStore


def _dark_frame(window):
    api = ctypes.WinDLL("dwmapi")
    api.DwmGetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
    api.DwmGetWindowAttribute.restype = ctypes.c_long
    value = wintypes.BOOL()
    result = api.DwmGetWindowAttribute(int(window.winId()), 20, ctypes.byref(value), ctypes.sizeof(value))
    if result != 0:
        raise OSError(f"DwmGetWindowAttribute failed: {result}")
    return bool(value.value)


class ThemeConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.icon_font = patch.object(theme, "_prepare_icon_font")
        self.icon_font.start()
        self.addCleanup(self.icon_font.stop)

    def application(self):
        app = Mock(spec=["setStyle", "styleHints"])
        return app

    def test_windows_style_falls_back_when_plugin_missing(self):
        app, fallback = self.application(), object()
        with patch.object(theme.sys, "platform", "win32"), patch.object(theme.QStyleFactory, "create", side_effect=[None, fallback]) as create:
            theme.install_system_theme(app)
        self.assertEqual([call.args[0] for call in create.call_args_list], ["windows11", "Fusion"])
        app.setStyle.assert_called_once_with(fallback)
        app.styleHints().unsetColorScheme.assert_called_once_with()

    def test_installer_is_idempotent(self):
        app = self.application()
        with patch.object(theme.sys, "platform", "win32"), patch.object(theme.QStyleFactory, "create") as create:
            theme.install_system_theme(app)
            theme.install_system_theme(app)
        create.assert_called_once_with("windows11")
        app.styleHints().unsetColorScheme.assert_called_once_with()

    def test_other_platform_keeps_native_style(self):
        app = self.application()
        with patch.object(theme.sys, "platform", "linux"), patch.object(theme.QStyleFactory, "create") as create:
            theme.install_system_theme(app)
        create.assert_not_called()
        app.setStyle.assert_not_called()
        app.styleHints().unsetColorScheme.assert_called_once_with()


class IconFontFallbackTests(unittest.TestCase):
    def test_missing_fluent_uses_existing_mdl2(self):
        with patch.object(theme.QFontDatabase, "families", return_value=["Segoe UI", "Segoe MDL2 Assets"]), patch.object(theme.QFont, "insertSubstitution") as substitute:
            theme._prepare_icon_font()
        substitute.assert_called_once_with("Segoe Fluent Icons", "Segoe MDL2 Assets")

    def test_installed_fluent_is_never_overridden(self):
        with patch.object(theme.QFontDatabase, "families", return_value=["Segoe Fluent Icons", "Segoe MDL2 Assets"]), patch.object(theme.QFont, "insertSubstitution") as substitute:
            theme._prepare_icon_font()
        substitute.assert_not_called()

    def test_absent_fallback_font_is_not_referenced(self):
        with patch.object(theme.QFontDatabase, "families", return_value=["Segoe UI"]), patch.object(theme.QFont, "insertSubstitution") as substitute:
            theme._prepare_icon_font()
        substitute.assert_not_called()


@unittest.skipUnless(sys.platform == "win32", "Windows native title-bar verification")
class NativeThemeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt = QApplication.instance() or QApplication([])
        cls.qt.setQuitOnLastWindowClosed(False)
        theme.install_system_theme(cls.qt)
        cls.hints = cls.qt.styleHints()
        cls.accessibility = cls.hints.accessibility()

    def test_native_style_symbols_resolve_to_real_glyphs(self):
        if self.qt.style().objectName().casefold() != "windows11":
            self.skipTest("The active style does not use Fluent font symbols")
        font = QFont("Segoe Fluent Icons")
        font.setStyleStrategy(QFont.StyleStrategy.NoFontMerging)
        raw = QRawFont.fromFont(font)
        # Qt6.10 style symbols for arrows, checkbox/menu checks, window actions,
        # help and clear. Dash12/E629 is for a tristate checkbox; this app uses
        # binary settings and does not require that Fluent-only symbol.
        symbols = (0xF78C, 0xE73E, 0xEDD9, 0xEDDA, 0xEDDB, 0xEDDC, 0xE70D,
                   0xE70E, 0xE971, 0xE972, 0xE973, 0xE974, 0xE96D, 0xE96E,
                   0xE921, 0xE922, 0xE923, 0xE8BB, 0xE712, 0xE897, 0xE894)
        for codepoint in symbols:
            with self.subTest(codepoint=hex(codepoint)):
                self.assertNotEqual(raw.glyphIndexesForString(chr(codepoint)), [0])

    def test_live_schemes_update_main_and_modal_frames_without_changing_font(self):
        if self.accessibility.contrastPreference() == Qt.ContrastPreference.HighContrast:
            self.skipTest("Keep the user's active contrast theme; standard light/dark assertions do not apply")
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        window = DewarpApp(settings_store=SettingsStore(Path(scratch.name) / "preferences.ini"))
        dialog = SettingsDialog(ProcessingSettings(), window)
        dialog.setWindowModality(Qt.WindowModality.ApplicationModal)
        message = QMessageBox(QMessageBox.Icon.Information, "Theme test", "Native theme test", parent=window)
        windows = (window, dialog, message)
        initial_font = self.qt.font().toString()
        window.show()
        dialog.show()
        message.show()
        try:
            for scheme, dark in ((Qt.ColorScheme.Light, False), (Qt.ColorScheme.Dark, True), (Qt.ColorScheme.Light, False)):
                with self.subTest(scheme=scheme.name):
                    self.hints.setColorScheme(scheme)
                    deadline = time.monotonic() + 3
                    while time.monotonic() < deadline:
                        self.qt.processEvents()
                        if all(_dark_frame(item) == dark for item in windows):
                            break
                        time.sleep(0.01)
                    for item in windows:
                        self.assertEqual(_dark_frame(item), dark)
                        palette = item.palette()
                        self.assertEqual(palette.color(QPalette.ColorRole.Window).lightness() < palette.color(QPalette.ColorRole.WindowText).lightness(), dark)
                        self.assertEqual(item.styleSheet(), "")
                    self.assertEqual(self.qt.font().toString(), initial_font)
                    self.assertTrue(window.start_button.isVisible())
                    self.assertTrue(window.cancel_button.isVisible())
        finally:
            self.hints.unsetColorScheme()
            for item in reversed(windows):
                item.close()
                item.deleteLater()
            self.qt.processEvents()
            QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
