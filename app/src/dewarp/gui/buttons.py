"""Semantic button accents that leave system contrast themes in control."""
from PySide6.QtCore import Qt
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import QApplication, QPushButton


ACCENTS = {
    "browse": ("#2563eb", "#1d4ed8", "#1e40af"),
    "positive": ("#15803d", "#166534", "#14532d"),
}


class ActionButton(QPushButton):
    """Keep standard button behavior while distinguishing available actions."""

    def __init__(self, text, *, role=None):
        super().__init__(text)
        if role is not None and role not in ACCENTS:
            raise ValueError(f"Unknown button role: {role}")
        self.action_role = role
        if role:
            self._hints = QApplication.instance().styleHints()
            self._accessibility = self._hints.accessibility()
            self._hints.colorSchemeChanged.connect(self._refresh_style)
            self._accessibility.contrastPreferenceChanged.connect(self._refresh_style)
            self._refresh_style()

    def _refresh_style(self, *_args):
        if self._accessibility.contrastPreference() == Qt.ContrastPreference.HighContrast:
            # User-selected Windows contrast colors take priority over role cues.
            self.setStyleSheet("")
            return
        dark = self._hints.colorScheme() == Qt.ColorScheme.Dark
        if self._hints.colorScheme() == Qt.ColorScheme.Unknown:
            dark = QApplication.palette().color(QPalette.ColorRole.Window).lightness() < 128
        normal, hover, pressed = ACCENTS[self.action_role]
        border = ("#93b9ff" if self.action_role == "browse" else "#86d7a0") if dark else pressed
        disabled_bg, disabled_text, disabled_border = (
            ("#333333", "#a0a0a0", "#505050") if dark else ("#e5e5e5", "#767676", "#b5b5b5")
        )
        # Scope styles to this button. No font or fixed size: native DPI, text
        # enlargement and the responsive footer still determine its geometry.
        self.setStyleSheet(f"""
            QPushButton {{
                background-color: {normal}; color: #ffffff;
                border: 2px solid {border}; border-radius: 4px;
                padding: 3px 8px;
            }}
            QPushButton:hover:enabled {{ background-color: {hover}; }}
            QPushButton:pressed:enabled {{ background-color: {pressed}; }}
            QPushButton:focus:enabled {{ border: 2px dashed #ffffff; }}
            QPushButton:disabled {{
                background-color: {disabled_bg}; color: {disabled_text};
                border: 2px solid {disabled_border};
            }}
        """)

    def keyPressEvent(self, event):
        # Enter activates only the focused button, never an implicit default.
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and self.hasFocus():
            if not event.isAutoRepeat():
                self.click()
            event.accept()
        else:
            super().keyPressEvent(event)
