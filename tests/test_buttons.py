"""Rendered role colors, inactive controls and contrast-theme fallback."""
from pathlib import Path
import sys
from unittest.mock import Mock, patch
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app" / "src"))

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import QApplication
from dewarp.gui.buttons import ACCENTS, ActionButton
from dewarp.gui.theme import install_system_theme


def contrast(hex_color):
    channels = [int(hex_color[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    channels = [c / 12.92 if c <= .04045 else ((c + .055) / 1.055) ** 2.4 for c in channels]
    luminance = sum(c * w for c, w in zip(channels, (.2126, .7152, .0722)))
    return 1.05 / (luminance + .05)


class ButtonTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)
        install_system_theme(cls.app)

    def test_enabled_text_has_contrast_in_all_accent_interaction_states(self):
        for role, colors in ACCENTS.items():
            for color in colors:
                with self.subTest(role=role, color=color):
                    self.assertGreaterEqual(contrast(color), 4.5)

    def test_native_rendering_is_colored_when_enabled_and_grey_when_disabled(self):
        hints = self.app.styleHints()
        if hints.accessibility().contrastPreference() == Qt.ContrastPreference.HighContrast:
            self.skipTest("Respect active Windows contrast settings")
        buttons = [ActionButton("Action", role=role) for role in ACCENTS]
        try:
            for scheme in (Qt.ColorScheme.Light, Qt.ColorScheme.Dark):
                hints.setColorScheme(scheme)
                self.app.processEvents()
                for button in buttons:
                    button.resize(180, 42)
                    button.show()
                    button.setEnabled(True)
                    button.clearFocus()
                    self.app.processEvents()
                    image = button.grab().toImage()
                    enabled = image.pixelColor(image.width() // 2, int(7 * button.devicePixelRatioF()))
                    self.assertGreater(enabled.saturation(), 100)
                    if button.action_role == "browse":
                        self.assertGreater(enabled.blue(), enabled.red())
                    else:
                        self.assertGreater(enabled.green(), enabled.blue())
                    count = []
                    button.clicked.connect(lambda: count.append(True))
                    button.setEnabled(False)
                    self.app.processEvents()
                    disabled_image = button.grab().toImage()
                    disabled = disabled_image.pixelColor(disabled_image.width() // 2, int(7 * button.devicePixelRatioF()))
                    self.assertEqual(disabled.red(), disabled.green())
                    self.assertEqual(disabled.green(), disabled.blue())
                    button.click()
                    self.assertEqual(count, [])
                    self.assertNotEqual(enabled, disabled)
        finally:
            hints.unsetColorScheme()
            for button in buttons:
                button.close()
                button.deleteLater()
            self.app.processEvents()
            QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def test_contrast_preference_removes_and_restores_scoped_styles(self):
        button = ActionButton("Start", role="positive")
        try:
            preference = Mock()
            with patch.object(button, "_accessibility", preference):
                preference.contrastPreference.return_value = Qt.ContrastPreference.HighContrast
                button._refresh_style()
                self.assertEqual(button.styleSheet(), "")
                preference.contrastPreference.return_value = Qt.ContrastPreference.NoPreference
                button._refresh_style()
                self.assertIn("QPushButton:disabled", button.styleSheet())
            self.assertEqual(self.app.styleSheet(), "")
        finally:
            button.deleteLater()
            QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
