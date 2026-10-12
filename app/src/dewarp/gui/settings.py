"""Validated per-user processing defaults and their native settings dialog."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QEvent, QSettings, Qt, QTimer
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDialog,
                              QGridLayout, QLabel, QScrollArea, QSizePolicy,
                              QSpinBox, QVBoxLayout, QWidget)

from .buttons import ActionButton
from .scrolling import ContentScrollArea


@dataclass(frozen=True)
class ProcessingSettings:
    copy_xmp: bool = True
    alpha: float = 0.0
    workers: int = 2

    def __post_init__(self):
        if type(self.copy_xmp) is not bool:
            raise ValueError("Preserve XMP must be on or off.")
        if isinstance(self.alpha, bool) or self.alpha not in (0.0, 1.0):
            raise ValueError("Framing must be Crop edges or Wider view.")
        if type(self.workers) is not int or not 1 <= self.workers <= 4:
            raise ValueError("Parallel photos must be between 1 and 4.")


class SettingsStore:
    """Explicit identity; tests can inject an INI path without touching the user."""

    def __init__(self, path: str | Path | None = None):
        self._settings = (QSettings(str(path), QSettings.Format.IniFormat) if path is not None
                          else QSettings("DewarpM3EJPGs", "DJI Photo Dewarp"))
        self._settings.setFallbacksEnabled(False)
        self.last_load_warning = ""

    def load(self) -> ProcessingSettings:
        self._settings.sync()
        invalid = []
        values = {}
        for key, default, allowed in (("copy_xmp", True, ("true", "false")),
                                      ("alpha", 0.0, ("0", "0.0", "1", "1.0")),
                                      ("workers", 2, ("1", "2", "3", "4"))):
            value = self._settings.value(f"processing/{key}", default)
            normalized = str(value).strip().lower()
            if normalized not in allowed:
                invalid.append(key)
                values[key] = default
            elif key == "copy_xmp":
                values[key] = normalized == "true"
            elif key == "alpha":
                values[key] = float(normalized)
            else:
                values[key] = int(normalized)
        self.last_load_warning = ("Invalid saved settings were reset to defaults: " + ", ".join(invalid)
                                  if invalid else "")
        if self._settings.status() != QSettings.Status.NoError:
            self.last_load_warning = "Saved settings could not be read completely; available values or defaults are in use."
        return ProcessingSettings(**values)

    def save(self, settings: ProcessingSettings) -> None:
        # Validate even if a caller supplied a lookalike rather than this dataclass.
        validated = ProcessingSettings(settings.copy_xmp, settings.alpha, settings.workers)
        for key, value in vars(validated).items():
            self._settings.setValue(f"processing/{key}", value)
        self._settings.sync()
        if self._settings.status() != QSettings.Status.NoError:
            raise OSError("Could not save processing settings. Check access to your user settings location.")


def _name(widget, name, description=""):
    widget.setAccessibleName(name)
    widget.setAccessibleDescription(description)
    widget.setObjectName(name.lower().replace(" ", "_"))
    return widget


class SettingsDialog(QDialog):
    """Edit a detached value. Only the caller persists an accepted result."""

    def __init__(self, settings: ProcessingSettings, parent=None):
        super().__init__(parent)
        # Dialogs are top-level windows, which normally stop Qt font inheritance.
        # Include them in the application's text zoom and system-font updates.
        self.setAttribute(Qt.WidgetAttribute.WA_WindowPropagation, True)
        self._initial = settings
        self.setWindowTitle("Processing settings")
        self.setAccessibleName("Processing settings")
        self.setMinimumSize(320, 240)
        self.setSizeGripEnabled(True)
        self.setWindowModality(Qt.WindowModality.ApplicationModal)
        layout = QVBoxLayout(self)
        self.scroll_area = _name(ContentScrollArea(), "Processing defaults")
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QScrollArea.Shape.NoFrame)
        body = QWidget()
        content = QVBoxLayout(body)
        content.setContentsMargins(4, 4, 4, 4)
        self.scroll_area.setWidget(body)
        layout.addWidget(self.scroll_area, 1)

        def label(text):
            item = QLabel(text)
            item.setWordWrap(True)
            item.setMinimumWidth(0)
            item.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            content.addWidget(item)
            return item

        label("Defaults apply to future runs and are saved for your Windows user.")
        self.copy_xmp = _name(QCheckBox("Preserve &XMP"), "Preserve XMP",
                              "Keep full XMP and DJI MakerNotes metadata. Requires ExifTool. Enabled by default.")
        self.copy_xmp.setChecked(settings.copy_xmp)
        content.addWidget(self.copy_xmp)
        label("Keep full XMP and DJI MakerNotes metadata (requires ExifTool).")
        framing_label = label("&Framing")
        self.alpha_combo = _name(QComboBox(), "Framing", "Crop edges is the default. Wider view keeps more field of view; both crop to a valid region.")
        self.alpha_combo.addItem("Crop edges", 0.0)
        self.alpha_combo.addItem("Wider view", 1.0)
        self.alpha_combo.setCurrentIndex(self.alpha_combo.findData(settings.alpha))
        self.alpha_combo.setMinimumWidth(0)
        framing_label.setBuddy(self.alpha_combo)
        content.addWidget(self.alpha_combo)
        label("Crop edges is the default. Wider view keeps more field of view; both use a valid-region crop.")
        workers_label = label("&Parallel photos")
        self.workers_spin = _name(QSpinBox(), "Parallel photos", "One to four simultaneous photos. Two is the default; more uses more memory.")
        self.workers_spin.setRange(1, 4)
        self.workers_spin.setValue(settings.workers)
        workers_label.setBuddy(self.workers_spin)
        content.addWidget(self.workers_spin)
        label("Two parallel photos balances speed and memory. Higher values use more memory.")
        self.defaults_button = _name(ActionButton("Restore defaults"), "Restore default settings",
                                     "Restore recommended values in this dialog. Save settings to apply them.")
        self.defaults_button.clicked.connect(self._restore_defaults)
        content.addWidget(self.defaults_button, 0, Qt.AlignmentFlag.AlignLeft)
        content.addStretch(1)

        self._footer = QWidget()
        self._footer.installEventFilter(self)
        self._footer_layout = QGridLayout(self._footer)
        self._footer_layout.setContentsMargins(0, 0, 0, 0)
        self.cancel_button = _name(ActionButton("Cancel"), "Cancel settings")
        self.save_button = _name(ActionButton("Save settings", role="positive"), "Save settings")
        self.cancel_button.clicked.connect(self.reject)
        self.save_button.clicked.connect(self.accept)
        for button in (self.cancel_button, self.save_button, self.defaults_button):
            button.setAutoDefault(False)
            button.setDefault(False)
        self._footer_stacked = None
        layout.addWidget(self._footer)
        for signal in (self.copy_xmp.toggled, self.alpha_combo.currentIndexChanged, self.workers_spin.valueChanged):
            signal.connect(self._update_actions)
        self._update_actions()
        self._reflow_footer()
        available = self.screen().availableGeometry()
        self.resize(min(540, available.width()), min(510, max(240, available.height() - 60)))
        controls = (self.copy_xmp, self.alpha_combo, self.workers_spin,
                    self.defaults_button, self.cancel_button, self.save_button)
        for first, second in zip(controls, controls[1:]):
            self.setTabOrder(first, second)
        QApplication.instance().focusChanged.connect(self._reveal_focus)
        QTimer.singleShot(0, self.copy_xmp.setFocus)

    def settings_value(self) -> ProcessingSettings:
        self.workers_spin.interpretText()
        return ProcessingSettings(self.copy_xmp.isChecked(), self.alpha_combo.currentData(), self.workers_spin.value())

    def _update_actions(self, *_args):
        value = self.settings_value()
        self.save_button.setEnabled(value != self._initial)
        self.defaults_button.setEnabled(value != ProcessingSettings())

    def _restore_defaults(self):
        defaults = ProcessingSettings()
        self.copy_xmp.setChecked(defaults.copy_xmp)
        self.alpha_combo.setCurrentIndex(self.alpha_combo.findData(defaults.alpha))
        self.workers_spin.setValue(defaults.workers)

    def _reveal_focus(self, _old, new):
        if new is not None and self.scroll_area.widget().isAncestorOf(new):
            self.scroll_area.ensureWidgetVisible(new, 12, 12)

    def _reflow_footer(self):
        stacked = self._footer.width() < (self.cancel_button.sizeHint().width() + self.save_button.sizeHint().width() + 16)
        if stacked == self._footer_stacked:
            return
        self._footer_stacked = stacked
        for button in (self.cancel_button, self.save_button):
            self._footer_layout.removeWidget(button)
        self._footer_layout.addWidget(self.cancel_button, 0, 0, 1, 2 if stacked else 1)
        self._footer_layout.addWidget(self.save_button, 1 if stacked else 0, 0 if stacked else 1, 1, 2 if stacked else 1)
        for column in (0, 1):
            self._footer_layout.setColumnStretch(column, 1)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "_footer"):
            self._reflow_footer()

    def eventFilter(self, watched, event):
        if getattr(self, "_footer", None) is watched and event.type() == QEvent.Type.Resize:
            self._reflow_footer()
        return super().eventFilter(watched, event)
