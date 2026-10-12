"""Disposable target for audit_gui_accessibility.ps1; never starts processing."""
from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
from pathlib import Path
import sys
import tempfile

import PySide6
from PySide6.QtCore import QTimer, Qt, qVersion
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QLineEdit,
    QPlainTextEdit, QProgressBar, QPushButton, QSpinBox, QTableWidget, QWidget)

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app" / "src"))
from dewarp.gui import window as dewarp_gui
from dewarp.gui.settings import ProcessingSettings, SettingsDialog, SettingsStore


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("ready_file", type=Path)
    parser.add_argument("--running-state", action="store_true")
    parser.add_argument("--state", choices=("setup", "running", "completed", "settings"), default="setup")
    args = parser.parse_args()
    app = QApplication([])
    temporary = tempfile.TemporaryDirectory(prefix="dewarp-a11y-settings-")
    window = dewarp_gui.DewarpApp(settings_store=SettingsStore(Path(temporary.name) / "settings.json"))
    window._timer.stop()
    window.setWindowTitle("Dewarp accessibility audit (no photo processing)")
    window.add_folders([Path(__file__).resolve().parents[1]])
    window.current_bar.setValue(round(window.current_bar.maximum() * 0.37))
    window.batch_bar.setValue(round(window.batch_bar.maximum() * 0.18))
    # Non-default text detects generic accessible names that hide live status.
    window._set_current_counts({"applied": 37, "remaining": 63})
    window.current_file_text.setText("Latest: accessibility-audit-photo.jpg")
    window.footer_status.setText("Accessibility audit progress: 37%")
    state = "running" if args.running_state else args.state
    if state in ("running", "completed"):
        # Exercise enabled/disabled accessibility state without starting a worker.
        window.pages.setCurrentWidget(window.progress_scroll)
        window._set_running(True)
        if state == "completed":
            window._handle_event({"event": "folder_done", "index": 0, "status": "Completed",
                                  "total": 100, "completed": 100, "applied": 100})
            window._handle_event({"event": "batch_done", "total": 100, "completed": 100,
                                  "elapsed": 12, "cancelled": False, "warnings": 0})
            window._set_running(False)
    window.show()
    surface = window
    if state == "settings":
        surface = SettingsDialog(ProcessingSettings(), window)
        surface.show()
    app.processEvents()
    expected = []
    # Inspect only the visible page/dialog. Hidden stacked-page controls are
    # deliberately absent; validating them would mask page-navigation mistakes.
    required = {"setup": ("start_button", "cancel_button", "folder_table", "output_edit"),
                "running": ("cancel_button", "back_button", "progress_table", "current_bar", "batch_bar"),
                "completed": ("cancel_button", "back_button", "progress_table"),
                "settings": ("copy_xmp", "alpha_combo", "workers_spin", "save_button", "cancel_button")}[state]
    for name in required:
        assert getattr(surface, name).isVisibleTo(surface), f"Required {state} control is hidden: {name}"
    controls = ((QPushButton, "Button", "Invoke"), (QCheckBox, "CheckBox", "Toggle"),
                (QComboBox, "ComboBox", "ExpandCollapse"), (QSpinBox, "Spinner", "RangeValue"),
                (QLineEdit, "Edit", "Value"), (QPlainTextEdit, "Edit", "Text"),
                (QTableWidget, "Table", "Grid"), (QProgressBar, "ProgressBar", "RangeValue"))
    attributes = {id(value): name for name, value in vars(surface).items() if isinstance(value, QWidget)}
    for widget in surface.findChildren(QWidget):
        if not widget.isVisibleTo(surface) or isinstance(widget.parentWidget(), (QSpinBox, QComboBox)):
            continue
        match = next(((role, pattern) for kind, role, pattern in controls if isinstance(widget, kind)), None)
        if match is None:
            continue
        role, pattern = match
        attr = attributes.get(id(widget), widget.objectName() or type(widget).__name__)
        name = widget.accessibleName()
        if not name and hasattr(widget, "text"):
            name = widget.text().replace("&", "")
        item = {"attribute": attr, "name": name, "type": f"ControlType.{role}",
                "pattern": f"{pattern}PatternIdentifiers.Pattern", "enabled": widget.isEnabled(),
                # UIA exposes focus capability separately from enabled state.
                "keyboard_focusable": widget.focusPolicy() != Qt.FocusPolicy.NoFocus}
        if role == "Edit":
            item["value"] = widget.text() if hasattr(widget, "text") else widget.toPlainText()
        if role == "ComboBox":
            item["value"] = widget.currentText()
        if role in ("ProgressBar", "Spinner"):
            item["range"] = {"value": widget.value(), "minimum": widget.minimum(), "maximum": widget.maximum()}
        expected.append(item)
    for attr in ("current_counts", "footer_status", "overview_text"):
        widget = getattr(surface, attr, None)
        if widget is not None and widget.isVisibleTo(surface):
            expected.append({"attribute": attr, "name": widget.text(), "type": "ControlType.Text"})
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.GetWindowDpiAwarenessContext.argtypes = [ctypes.c_void_p]
    user32.GetWindowDpiAwarenessContext.restype = ctypes.c_void_p
    user32.GetAwarenessFromDpiAwarenessContext.argtypes = [ctypes.c_void_p]
    user32.GetAwarenessFromDpiAwarenessContext.restype = ctypes.c_int
    user32.AreDpiAwarenessContextsEqual.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    user32.AreDpiAwarenessContextsEqual.restype = ctypes.c_bool
    handle = int(surface.winId())
    dpi_context = user32.GetWindowDpiAwarenessContext(handle)
    data = {
        "hwnd": handle, "python": sys.version, "pyside6": PySide6.__version__, "qt": qVersion(),
        "source_sha256": hashlib.sha256(Path(dewarp_gui.__file__).read_bytes()).hexdigest(),
        "settings_source_sha256": hashlib.sha256(Path(dewarp_gui.__file__).with_name("settings.py").read_bytes()).hexdigest(),
        "scrolling_source_sha256": hashlib.sha256(Path(dewarp_gui.__file__).with_name("scrolling.py").read_bytes()).hexdigest(),
        "theme_source_sha256": hashlib.sha256(Path(dewarp_gui.__file__).with_name("theme.py").read_bytes()).hexdigest(),
        "device_pixel_ratio": window.devicePixelRatioF(),
        "dpi_awareness": user32.GetAwarenessFromDpiAwarenessContext(dpi_context),
        "per_monitor_v2": user32.AreDpiAwarenessContextsEqual(dpi_context, ctypes.c_void_p(-4)),
        "visible": surface.isVisible(), "running_state_simulated": state == "running", "page": state,
        "photo_processing_started": False, "expected_controls": expected,
    }
    # Publish atomically so the PowerShell client cannot read partial JSON.
    pending = args.ready_file.with_suffix(".pending")
    pending.write_text(json.dumps(data, indent=2), encoding="utf-8")
    pending.replace(args.ready_file)
    stop_file = args.ready_file.with_suffix(".stop")
    timer = QTimer()
    def finish() -> None:
        timer.stop()
        # The running-state fixture has no worker; clear it before closeEvent
        # so the application's normal safe-cancel confirmation is not opened.
        window._set_running(False)
        if surface is not window:
            surface.reject()
        window.close()
        app.quit()

    timer.timeout.connect(lambda: finish() if stop_file.exists() else None)
    timer.start(100)
    QTimer.singleShot(30000, finish)
    app.exec()
    temporary.cleanup()


if __name__ == "__main__":
    main()
