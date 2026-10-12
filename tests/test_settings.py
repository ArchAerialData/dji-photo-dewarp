"""Settings survive reopening without changing the real user's defaults."""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app" / "src"))

from PySide6.QtCore import QEvent, QPoint, QRect, QSettings, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QWidget

from dewarp.gui.settings import ProcessingSettings, SettingsDialog, SettingsStore


class SettingsStoreTests(unittest.TestCase):
    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory()
        self.addCleanup(self.scratch.cleanup)
        self.path = Path(self.scratch.name) / "preferences.ini"

    def test_first_use_defaults_preserve_metadata_and_crop(self):
        self.assertEqual(SettingsStore(self.path).load(), ProcessingSettings(True, 0.0, 2))
        self.assertFalse(self.path.exists(), "Reading defaults must not write settings")

    def test_values_survive_a_fresh_process(self):
        SettingsStore(self.path).save(ProcessingSettings(False, 1.0, 3))
        code = ("import sys; from pathlib import Path; "
                "sys.path.insert(0, str(Path(sys.argv[1])/'app'/'src')); "
                "from dewarp.gui.settings import SettingsStore, ProcessingSettings; "
                "assert SettingsStore(sys.argv[2]).load() == ProcessingSettings(False, 1.0, 3)")
        subprocess.run([sys.executable, "-I", "-c", code, str(ROOT), str(self.path)],
                       check=True, capture_output=True, text=True, timeout=15)

    def test_invalid_saved_fields_fall_back_individually(self):
        raw = QSettings(str(self.path), QSettings.Format.IniFormat)
        raw.setValue("processing/copy_xmp", "maybe")
        raw.setValue("processing/alpha", "nan")
        raw.setValue("processing/workers", 4)
        raw.sync()
        store = SettingsStore(self.path)
        self.assertEqual(store.load(), ProcessingSettings(True, 0.0, 4))
        self.assertIn("copy_xmp", store.last_load_warning)
        self.assertIn("alpha", store.last_load_warning)

    def test_out_of_range_worker_counts_do_not_escape_validation(self):
        for workers in (0, 5, True, "2"):
            with self.subTest(workers=workers), self.assertRaises(ValueError):
                ProcessingSettings(workers=workers)

    def test_write_failure_is_reported(self):
        store = SettingsStore(self.path)
        with patch.object(store._settings, "status", return_value=QSettings.Status.AccessError):
            with self.assertRaisesRegex(OSError, "Could not save"):
                store.save(ProcessingSettings(workers=3))


class SettingsDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt = QApplication.instance() or QApplication([])
        cls.qt.setQuitOnLastWindowClosed(False)

    def dialog(self, settings=ProcessingSettings()):
        dialog = SettingsDialog(settings)
        self.addCleanup(self.dispose, dialog)
        dialog.show()
        self.qt.processEvents()
        return dialog

    def dispose(self, dialog):
        dialog.close()
        dialog.deleteLater()
        self.qt.processEvents()
        QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def test_cancel_discards_edits_and_does_not_modify_initial_value(self):
        initial = ProcessingSettings()
        dialog = self.dialog(initial)
        self.assertFalse(dialog.save_button.isEnabled())
        self.assertFalse(dialog.defaults_button.isEnabled())
        dialog.workers_spin.setValue(4)
        self.assertTrue(dialog.save_button.isEnabled())
        QTest.mouseClick(dialog.cancel_button, Qt.MouseButton.LeftButton)
        self.assertEqual(dialog.result(), QDialog.DialogCode.Rejected)
        self.assertEqual(initial, ProcessingSettings())

    def test_restore_requires_explicit_save_and_accept_returns_all_options(self):
        dialog = self.dialog(ProcessingSettings(False, 1.0, 4))
        dialog.defaults_button.click()
        self.assertTrue(dialog.isVisible())
        self.assertEqual(dialog.settings_value(), ProcessingSettings())
        self.assertTrue(dialog.save_button.isEnabled())
        dialog.save_button.click()
        self.assertEqual(dialog.result(), QDialog.DialogCode.Accepted)
        self.assertEqual(dialog.settings_value(), ProcessingSettings())

    def test_escape_cancels_without_implicit_default_accept(self):
        dialog = self.dialog()
        dialog.workers_spin.setValue(3)
        self.assertFalse(dialog.save_button.isDefault())
        QTest.keyClick(dialog, Qt.Key.Key_Escape)
        self.assertEqual(dialog.result(), QDialog.DialogCode.Rejected)

    def test_dialog_inherits_and_tracks_parent_text_zoom(self):
        parent = QWidget()
        self.addCleanup(self.dispose, parent)
        font = parent.font()
        font.setPointSizeF(18)
        parent.setFont(font)
        dialog = SettingsDialog(ProcessingSettings(), parent)
        self.addCleanup(self.dispose, dialog)
        self.assertEqual(dialog.copy_xmp.font().pointSizeF(), 18)
        font.setPointSizeF(20)
        parent.setFont(font)
        self.assertEqual(dialog.copy_xmp.font().pointSizeF(), 20)

    def test_small_window_focus_reveals_complete_native_spin_control(self):
        dialog = self.dialog()
        font = dialog.font()
        font.setPointSizeF(18)
        dialog.setFont(font)
        dialog.resize(320, 240)
        dialog.activateWindow()
        dialog.workers_spin.setFocus()
        for _ in range(5):
            self.qt.processEvents()
        dialog.scroll_area.ensureWidgetVisible(dialog.workers_spin, 8, 8)
        viewport = dialog.scroll_area.viewport()
        bounds = QRect(dialog.workers_spin.mapTo(viewport, QPoint()), dialog.workers_spin.size())
        self.assertTrue(viewport.rect().contains(bounds), (viewport.rect(), bounds))


if __name__ == "__main__":
    unittest.main()
