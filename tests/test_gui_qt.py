"""Qt GUI integration and keyboard/accessibility regression checks.

Run with the standard unittest suite; synthetic photos only. DPI matrices and
native Windows UI Automation probes are recorded separately in docs/.
"""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app" / "src"))
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from PIL import Image
from PySide6 import QtCore, QtGui, QtTest, QtWidgets

from dewarp.gui import window as dewarp_gui
from dewarp.gui.settings import ProcessingSettings, SettingsStore


XMP = b'''<x:xmpmeta xmlns:x="adobe:ns:meta/">
<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
<rdf:Description rdf:about="" xmlns:drone-dji="http://www.dji.com/drone-dji/1.0/"
drone-dji:DewarpFlag="0"
drone-dji:DewarpData="2026-01-01;200,200,0,0,-0.1,0.01,0,0,0"/>
</rdf:RDF></x:xmpmeta>'''


class GuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        cls.qt.setQuitOnLastWindowClosed(False)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        self.settings_store = SettingsStore(path=self.directory / "preferences.ini")
        self.settings_store.save(ProcessingSettings(copy_xmp=False))
        self.window = dewarp_gui.DewarpApp(settings_store=self.settings_store)
        self.window.show()
        self.window.activateWindow()
        self.qt.processEvents()

    def tearDown(self):
        window = self.window
        if window._worker is not None and window._worker.is_alive():
            window._cancel.set()
            window._worker.join(timeout=10)
        window._running = False
        for timer in window.findChildren(QtCore.QTimer):
            timer.stop()
        window.close()
        window.deleteLater()
        self.qt.processEvents()
        QtCore.QCoreApplication.sendPostedEvents(None, QtCore.QEvent.Type.DeferredDelete)
        self.temp.cleanup()

    def pump_until(self, predicate, timeout=10):
        deadline = time.monotonic() + timeout
        while not predicate():
            if time.monotonic() >= deadline:
                self.fail("Timed out while waiting for GUI worker")
            self.qt.processEvents()
            time.sleep(0.01)
        self.qt.processEvents()

    def assert_availability(self, *, start, remove, clear, reset, opened, cancel, browse):
        window = self.window
        expected = ((window.start_button, start), (window.start_action, start),
                    (window.remove_button, remove), (window.clear_button, clear),
                    (window.output_reset_button, reset), (window.open_button, opened),
                    (window.open_action, opened), (window.cancel_button, cancel),
                    (window.cancel_action, cancel), (window.add_button, browse),
                    (window.add_action, browse), (window.output_browse_button, browse),
                    (window.output_action, browse))
        for control, enabled in expected:
            with self.subTest(control=control.objectName() or control.text()):
                self.assertEqual(control.isEnabled(), enabled)

    def assert_folder_tables_match(self):
        window = self.window
        self.assertEqual(window.folder_table.rowCount(), window.progress_table.rowCount())
        self.assertEqual(window.folder_table.columnCount(), window.progress_table.columnCount())
        for row in range(window.folder_table.rowCount()):
            for column in range(window.folder_table.columnCount()):
                self.assertEqual(window.folder_table.item(row, column).text(),
                                 window.progress_table.item(row, column).text())

    def select_setup_row(self, row):
        # Data/reindex tests choose an exact row, independently of physical key
        # modifiers and QTableView.selectRow's visible-header geometry.
        table = self.window.folder_table
        table.selectionModel().setCurrentIndex(table.model().index(row, 0),
            QtCore.QItemSelectionModel.SelectionFlag.ClearAndSelect |
            QtCore.QItemSelectionModel.SelectionFlag.Rows)
        self.assertEqual(self.window._selected_rows(), [row])

    def test_availability_tracks_queue_selection_and_output_location(self):
        window = self.window
        self.assert_availability(start=False, remove=False, clear=False, reset=False,
                                 opened=False, cancel=False, browse=True)
        other = self.directory / "Second folder"
        other.mkdir()
        window.add_folders([self.directory, other])
        self.assert_availability(start=True, remove=True, clear=True, reset=False,
                                 opened=False, cancel=False, browse=True)
        window.folder_table.clearSelection()
        self.assert_availability(start=True, remove=False, clear=True, reset=False,
                                 opened=False, cancel=False, browse=True)
        window.output_edit.setText("   ")
        self.assertFalse(window.output_reset_button.isEnabled())
        window.output_edit.setText(str(self.directory / "Custom output"))
        self.assertTrue(window.output_reset_button.isEnabled())
        output = self.directory / "Existing output"
        output.mkdir()
        window.outputs[0] = output
        self.select_setup_row(0)
        self.assert_availability(start=True, remove=True, clear=True, reset=True,
                                 opened=True, cancel=False, browse=True)
        output.rmdir()
        window._update_selection()
        self.assertFalse(window.open_button.isEnabled())
        self.assertFalse(window.open_action.isEnabled())
        window._remove_selected()
        self.assertEqual(window.folders, [other])
        self.assert_availability(start=True, remove=True, clear=True, reset=True,
                                 opened=False, cancel=False, browse=True)
        window._clear_queue()
        self.assert_availability(start=False, remove=False, clear=False, reset=True,
                                 opened=False, cancel=False, browse=True)
        window.output_reset_button.click()
        self.assertEqual(window.output_edit.text(), "")
        self.assert_availability(start=False, remove=False, clear=False, reset=False,
                                 opened=False, cancel=False, browse=True)

    def test_availability_tracks_running_cancel_failure_and_completion(self):
        window = self.window
        window.add_folders([self.directory])
        window.output_edit.setText(str(self.directory / "Destination"))
        output = self.directory / "Available output"
        output.mkdir()
        for outcome in ("cancelled", "failed", "completed"):
            with self.subTest(outcome=outcome):
                release = threading.Event()

                def worker(folders, options, on_event, cancel_event):
                    on_event(dict(event="folder_start", index=0, folder=str(folders[0]),
                                  output=str(output), total=1))
                    if not release.wait(timeout=5):
                        raise RuntimeError("Availability test did not release worker")
                    if outcome == "failed":
                        raise OSError("Synthetic worker failure")
                    cancelled = cancel_event.is_set()
                    completed = 0 if cancelled else 1
                    on_event(dict(event="folder_done", index=0, folder=str(folders[0]),
                                  output=str(output), total=1, completed=completed,
                                  applied=completed, status="Cancelled" if cancelled else "Completed"))
                    on_event(dict(event="batch_done", cancelled=cancelled, total=1,
                                  completed=completed, applied=completed, elapsed=0.1))
                    return []

                with patch.object(dewarp_gui, "run_batch", side_effect=worker):
                    try:
                        window._start_batch()
                        self.pump_until(lambda: 0 in window.outputs)
                        self.assertIs(window.pages.currentWidget(), window.progress_scroll)
                        self.assertFalse(window.back_button.isEnabled())
                        window.back_button.click()
                        self.assertIs(window.pages.currentWidget(), window.progress_scroll)
                        self.assert_availability(start=False, remove=False, clear=False, reset=False,
                                                 opened=True, cancel=True, browse=False)
                        window._remove_selected()
                        window._clear_queue()
                        window.add_folders([output])
                        window.output_reset_button.click()
                        self.assertEqual(window.folders, [self.directory])
                        self.assertEqual(window.output_edit.text(), str(self.directory / "Destination"))
                        if outcome == "cancelled":
                            window._cancel_batch()
                            window._set_running(True)  # A refresh must not re-enable cancellation.
                            self.assert_availability(start=False, remove=False, clear=False, reset=False,
                                                     opened=True, cancel=False, browse=False)
                    finally:
                        release.set()
                        self.pump_until(lambda: not window._running)
                self.assertTrue(window.back_button.isEnabled())
                self.assertIs(window.pages.currentWidget(), window.progress_scroll)
                self.assertFalse(window.start_button.isEnabled())
                self.assert_folder_tables_match()
                window.back_button.click()
                self.assertIs(window.pages.currentWidget(), window.setup_scroll)
                self.assert_availability(start=True, remove=True, clear=True, reset=True,
                                         opened=True, cancel=False, browse=True)

    def test_synthetic_batch_destinations_progress_empty_folder_and_rerun(self):
        window = self.window
        folders = []
        for group in ("First", "Second"):
            folder = self.directory / group / "Flight"
            folder.mkdir(parents=True)
            folders.append(folder)
            for index in range(2):
                Image.new("RGB", (320, 240), (100 + index, 150, 50)).save(
                    folder / f"DJI_{index}.JPG", xmp=XMP)
        empty = self.directory / "Empty"
        empty.mkdir()
        folders.append(empty)
        window.add_folders(folders + folders[:1])
        self.assertEqual(len(window.folders), 3)
        window.output_edit.setText(str(self.directory / "Results"))
        sources_before = {path: path.read_bytes() for folder in folders for path in folder.glob("*.JPG")}
        main_thread = threading.get_ident()
        original = window._handle_event
        handler_threads = []

        def handle(event):
            handler_threads.append(threading.get_ident())
            original(event)

        with patch.object(window, "_handle_event", side_effect=handle):
            window._start_batch()
            self.assertTrue(window._running)
            self.assertIs(window.pages.currentWidget(), window.progress_scroll)
            self.assertFalse(window.add_button.isEnabled())
            self.pump_until(lambda: not window._running)

        self.assertTrue(handler_threads)
        self.assertEqual(set(handler_threads), {main_thread})
        self.assertEqual(window._batch_completed, 4)
        self.assertEqual(window.batch_bar.value(), 100)
        self.assertEqual(window._statuses, {0: "Completed", 1: "Completed", 2: "Empty"})
        self.assertEqual(window.outputs[0].name, "Flight - Dewarped Photos")
        self.assertEqual(window.outputs[1].name, "Flight - Dewarped Photos (2)")
        self.assertEqual(len(list(window.outputs[0].glob("*.JPG"))), 2)
        self.assertTrue((window.outputs[0] / "processing-report.json").is_file())
        self.assert_folder_tables_match()

        self.assertTrue(window.back_button.isEnabled())
        self.assertFalse(window.start_button.isEnabled())
        first_outputs = dict(window.outputs)
        window.back_button.click()
        self.assertIs(window.pages.currentWidget(), window.setup_scroll)
        self.assertEqual(window.folders, folders)
        self.assertEqual(window.outputs, first_outputs)
        self.assertTrue(window.add_button.isEnabled())
        self.assertTrue(window.settings_action.isEnabled())
        window.start_button.click()
        self.assertIs(window.pages.currentWidget(), window.progress_scroll)
        self.pump_until(lambda: not window._running)
        self.assertEqual(window._batch_completed, 4)
        self.assertEqual(window.outputs[0].name, "Flight - Dewarped Photos (3)")
        self.assertEqual(window.outputs[1].name, "Flight - Dewarped Photos (4)")
        self.assertTrue(all(path.is_dir() for path in first_outputs.values()))
        self.assertEqual({path: path.read_bytes() for path in sources_before}, sources_before)
        self.assert_folder_tables_match()

    def test_synthetic_single_folder_uses_progress_page_and_returns_to_setup(self):
        window = self.window
        folder = self.directory / "One flight"
        folder.mkdir()
        photo = folder / "DJI_0.JPG"
        Image.new("RGB", (320, 240), (32, 144, 210)).save(photo, xmp=XMP)
        original = photo.read_bytes()
        window.add_folders([folder])
        window.start_button.click()
        self.assertIs(window.pages.currentWidget(), window.progress_scroll)
        self.pump_until(lambda: not window._running)
        self.assertEqual(window._statuses, {0: "Completed"})
        self.assertEqual(window._batch_completed, 1)
        self.assertEqual(window.outputs[0].parent, folder)
        self.assertTrue((window.outputs[0] / photo.name).is_file())
        self.assertEqual(photo.read_bytes(), original)
        self.assert_folder_tables_match()
        window.back_button.click()
        self.assertIs(window.pages.currentWidget(), window.setup_scroll)
        self.assertEqual(window.folders, [folder])
        self.assertTrue(window.start_button.isEnabled())

    def test_current_and_batch_progress_are_photo_weighted(self):
        window = self.window
        window.add_folders([self.directory])
        window._handle_event(dict(event="batch_start", total=10, folders_total=1, workers=2))
        window._handle_event(dict(event="folder_start", index=0, folder=str(self.directory), output="", total=2))
        window._handle_event(dict(event="photo_done", index=0, name="a.jpg", status="applied",
                                  completed=1, remaining=1, total=2, applied=1, already=0,
                                  skipped=0, failed=0, batch_completed=1, batch_total=10))
        self.assertEqual(window.current_bar.value(), 50)
        self.assertEqual(window.batch_bar.value(), 10)
        self.assertIn("Dewarped: 1", window.current_counts.text())
        self.assertIn("Remaining: 1", window.current_counts.text())

    def test_saved_preferences_are_loaded_and_forwarded_to_the_worker(self):
        preferences = ProcessingSettings(copy_xmp=True, alpha=1.0, workers=3)
        self.settings_store.save(preferences)
        self.window.close()
        self.window.deleteLater()
        QtCore.QCoreApplication.sendPostedEvents(None, QtCore.QEvent.Type.DeferredDelete)
        self.window = dewarp_gui.DewarpApp(settings_store=SettingsStore(path=self.directory / "preferences.ini"))
        window = self.window
        window.show()
        self.qt.processEvents()
        self.assertEqual(window.processing_settings, preferences)
        window.add_folders([self.directory])
        output = self.directory / "Custom outputs"
        window.output_edit.setText(str(output))
        received = []
        release = threading.Event()

        def worker(folders, options, on_event, cancel_event):
            received.append((folders, options))
            if not release.wait(timeout=5):
                raise RuntimeError("Settings workflow did not release worker")
            on_event(dict(event="batch_done", cancelled=False, total=0, completed=0,
                          applied=0, already=0, skipped=0, failed=0, elapsed=0.1))
            return []

        with patch.object(dewarp_gui, "exiftool_path", return_value="synthetic-exiftool"), \
             patch.object(dewarp_gui, "run_batch", side_effect=worker):
            try:
                window.start_button.click()
                self.pump_until(lambda: bool(received))
                self.assertFalse(window.settings_action.isEnabled())
                with patch.object(dewarp_gui, "SettingsDialog") as settings_dialog:
                    window._show_settings()
                    settings_dialog.assert_not_called()
                folders, options = received[0]
                self.assertEqual(folders, [self.directory])
                self.assertEqual(options.output_root, output)
                self.assertTrue(options.copy_xmp)
                self.assertEqual(options.alpha, 1.0)
                self.assertEqual(options.workers, 3)
                self.assertTrue(options.require_dewarp)
            finally:
                release.set()
                self.pump_until(lambda: not window._running)
        window.back_button.click()
        self.assertIs(window.pages.currentWidget(), window.setup_scroll)
        self.assertEqual(window.processing_settings, preferences)
        self.assertEqual(SettingsStore(path=self.directory / "preferences.ini").load(), preferences)

    def test_file_settings_accept_updates_current_and_saved_options(self):
        window = self.window
        updated = ProcessingSettings(copy_xmp=True, alpha=1.0, workers=4)
        with patch.object(dewarp_gui, "SettingsDialog") as dialog_type:
            dialog = dialog_type.return_value
            dialog.exec.return_value = QtWidgets.QDialog.DialogCode.Accepted
            dialog.settings_value.return_value = updated
            window.settings_action.trigger()
        dialog_type.assert_called_once()
        self.assertEqual(window.processing_settings, updated)
        self.assertEqual(SettingsStore(path=self.directory / "preferences.ini").load(), updated)

    def test_file_settings_cancel_preserves_current_and_saved_options(self):
        window = self.window
        initial = window.processing_settings
        with patch.object(dewarp_gui, "SettingsDialog") as dialog_type, \
             patch.object(self.settings_store, "save") as save:
            dialog = dialog_type.return_value
            dialog.exec.return_value = QtWidgets.QDialog.DialogCode.Rejected
            dialog.settings_value.return_value = ProcessingSettings(copy_xmp=True, alpha=1.0, workers=4)
            window.settings_action.trigger()
        save.assert_not_called()
        self.assertEqual(window.processing_settings, initial)
        self.assertEqual(SettingsStore(path=self.directory / "preferences.ini").load(), initial)

    def test_file_settings_save_failure_keeps_previous_run_options(self):
        window = self.window
        initial = window.processing_settings
        with patch.object(dewarp_gui, "SettingsDialog") as dialog_type, \
             patch.object(self.settings_store, "save", side_effect=OSError("Synthetic settings write denied")), \
             patch.object(QtWidgets.QMessageBox, "warning") as warning:
            dialog = dialog_type.return_value
            dialog.exec.return_value = QtWidgets.QDialog.DialogCode.Accepted
            dialog.settings_value.return_value = ProcessingSettings(copy_xmp=True, alpha=1.0, workers=4)
            window.settings_action.trigger()
        warning.assert_called_once()
        self.assertEqual(window.processing_settings, initial)
        self.assertEqual(SettingsStore(path=self.directory / "preferences.ini").load(), initial)

    def test_repeated_settings_dialogs_release_their_widgets_after_cancel(self):
        window = self.window
        for opening in range(3):
            with self.subTest(opening=opening):
                self.assertEqual(window.findChildren(dewarp_gui.SettingsDialog), [])
                dialogs_seen = []

                def reject_settings():
                    dialogs = window.findChildren(dewarp_gui.SettingsDialog)
                    dialogs_seen.extend(dialogs)
                    for dialog in dialogs:
                        dialog.reject()

                QtCore.QTimer.singleShot(0, reject_settings)
                window.settings_action.trigger()
                self.assertEqual(len(dialogs_seen), 1)
                self.qt.processEvents()
                QtCore.QCoreApplication.sendPostedEvents(None, QtCore.QEvent.Type.DeferredDelete)
                self.assertEqual(window.findChildren(dewarp_gui.SettingsDialog), [])

    def test_invalid_and_duplicate_folders_report_details_and_allow_recovery(self):
        window = self.window
        unavailable = self.directory / "Missing folder"
        window.add_folders([unavailable])
        self.assertEqual(window.folders, [])
        self.assertFalse(window.start_button.isEnabled())
        self.assertIn("1 unavailable", window.footer_status.text())
        self.assertIn(str(unavailable), window._status_contents())
        window.add_folders([self.directory])
        self.assertEqual(window.folders, [self.directory])
        self.assertTrue(window.start_button.isEnabled())
        self.assertIn("1 added", window.footer_status.text())
        window.add_folders([self.directory])
        self.assertEqual(window.folders, [self.directory])
        self.assertIn("1 already queued", window.footer_status.text())
        self.assertIn("Already queued:", window._status_contents())
        self.assertIs(window.pages.currentWidget(), window.setup_scroll)
        self.assert_folder_tables_match()

    def test_invalid_destination_stays_on_setup_without_starting_a_worker(self):
        window = self.window
        window.add_folders([self.directory])
        destination = self.directory / "existing-file.txt"
        destination.write_text("keep this original file")
        window.output_edit.setText(str(destination))
        with patch.object(QtWidgets.QMessageBox, "warning") as warning, \
             patch.object(dewarp_gui, "run_batch") as worker:
            window.start_button.click()
            self.qt.processEvents()
        warning.assert_called_once()
        worker.assert_not_called()
        self.assertFalse(window._running)
        self.assertIs(window.pages.currentWidget(), window.setup_scroll)
        self.assertEqual(destination.read_text(), "keep this original file")

    def test_multiple_dropped_paths_preserve_spaces_braces_and_remove_selection(self):
        window = self.window
        folders = [self.directory / "Mission {Alpha}", self.directory / "Mission with spaces"]
        for folder in folders:
            folder.mkdir()
        mime = QtCore.QMimeData()
        mime.setUrls([QtCore.QUrl.fromLocalFile(str(folder)) for folder in folders])
        event = QtGui.QDropEvent(QtCore.QPointF(10, 10), QtCore.Qt.DropAction.CopyAction,
                                mime, QtCore.Qt.MouseButton.LeftButton, QtCore.Qt.KeyboardModifier.NoModifier)
        window.dropEvent(event)
        self.assertTrue(event.isAccepted())
        window.add_folders(folders)
        self.assertEqual(window.folders, folders)
        self.assertEqual(window.folder_table.rowCount(), 2)
        self.select_setup_row(1)
        window._remove_selected()
        self.assertEqual(window.folders, folders[:1])

    def test_queue_keyboard_selection_and_delete_remove_the_selected_folder(self):
        second = self.directory / "Second folder"
        second.mkdir()
        self.window.add_folders([self.directory, second])
        self.select_setup_row(0)
        table = self.window.folder_table
        table.setFocus()
        self.qt.processEvents()
        QtTest.QTest.keyClick(table, QtCore.Qt.Key.Key_Down)
        self.assertEqual(self.window._selected_rows(), [1])
        self.assertTrue(self.window.remove_button.isEnabled())
        QtTest.QTest.keyClick(table, QtCore.Qt.Key.Key_Delete)
        self.assertEqual(self.window.folders, [self.directory])
        self.assert_folder_tables_match()

    def test_cancel_and_close_wait_for_worker_exit(self):
        window = self.window
        release = threading.Event()

        def worker(folders, options, on_event, cancel_event):
            on_event(dict(event="batch_start", total=2, folders_total=1, workers=1))
            on_event(dict(event="folder_start", index=0, folder=str(folders[0]), output="", total=2))
            release.wait(timeout=5)
            if not cancel_event.is_set():
                raise AssertionError("Expected cancellation before worker release")
            on_event(dict(event="folder_done", index=0, folder=str(folders[0]), output="",
                          total=2, completed=1, applied=1, already=0, skipped=0, failed=0, status="Cancelled"))
            on_event(dict(event="batch_done", cancelled=True, total=2, completed=1, applied=1,
                          already=0, skipped=0, failed=0, elapsed=0.25, warnings=1))
            return []

        window.add_folders([self.directory])
        with patch.object(dewarp_gui, "run_batch", side_effect=worker), \
             patch.object(QtWidgets.QMessageBox, "question", return_value=QtWidgets.QMessageBox.StandardButton.Yes):
            window._start_batch()
            window.close()
            self.assertTrue(window._cancel.is_set())
            self.assertTrue(window._close_when_finished)
            self.assertTrue(window._worker.is_alive())
            self.assertTrue(window.isVisible())
            release.set()
            self.pump_until(lambda: not window.isVisible())
            self.assertFalse(window._running)
            self.assertEqual(window._statuses[0], "Cancelled")
            self.assertIn("Cancelled", window.batch_text.text())

    def test_primary_controls_expose_accessible_names_and_focus(self):
        window = self.window
        for widget in (window.start_button, window.cancel_button, window.add_button,
                       window.remove_button, window.clear_button, window.open_button,
                       window.folder_table, window.progress_table, window.output_edit,
                       window.back_button, window.current_bar,
                       window.batch_bar, window.log):
            with self.subTest(widget=widget.objectName() or type(widget).__name__):
                self.assertTrue(widget.accessibleName())
                interface = QtGui.QAccessible.queryAccessibleInterface(widget)
                self.assertIsNotNone(interface)
                self.assertTrue(interface.isValid())
                self.assertTrue(interface.text(QtGui.QAccessible.Text.Name))
        self.assertEqual(window.start_button.focusPolicy(), QtCore.Qt.FocusPolicy.StrongFocus)
        self.assertTrue(window.log.isReadOnly())

    def test_footer_stays_visible_and_focus_scrolls_content(self):
        window = self.window
        window._set_text_scale(200)
        window.resize(480, 360)
        self.qt.processEvents()
        for page in (window.setup_scroll, window.progress_scroll):
            window.pages.setCurrentWidget(page)
            for position in (0, page.verticalScrollBar().maximum()):
                page.verticalScrollBar().setValue(position)
                self.qt.processEvents()
                controls = ((window.start_button, window.cancel_button) if page is window.setup_scroll
                            else (window.back_button, window.cancel_button))
                for button in controls:
                    rect = QtCore.QRect(button.mapTo(window, QtCore.QPoint()), button.size())
                    self.assertTrue(window.rect().contains(rect), f"{button.text()} clipped: {rect}")
                    self.assertGreaterEqual(button.height(), button.fontMetrics().height())
        window.pages.setCurrentWidget(window.setup_scroll)
        window.setup_scroll.verticalScrollBar().setValue(0)
        window.activateWindow()
        self.qt.processEvents()
        window.output_edit.setFocus()
        self.qt.processEvents()
        self.qt.processEvents()
        viewport = window.setup_scroll.viewport()
        point = window.output_edit.mapTo(viewport, QtCore.QPoint())
        self.assertGreaterEqual(point.y(), 0)
        self.assertLess(point.y(), viewport.height())
        self.assertEqual(window.styleSheet(), "")

    def test_enter_and_space_activate_focused_buttons(self):
        window = self.window
        for key in (QtCore.Qt.Key.Key_Return, QtCore.Qt.Key.Key_Space):
            with self.subTest(key=key):
                spy = QtTest.QSignalSpy(window.add_button.clicked)
                with patch.object(QtWidgets.QFileDialog, "getExistingDirectory", return_value=""):
                    window.add_button.setFocus()
                    QtTest.QTest.keyClick(window.add_button, key)
                    self.qt.processEvents()
                self.assertEqual(spy.count(), 1)

    def test_minimum_window_reveals_complete_styled_fields(self):
        """Cursor visibility alone can leave a Windows-style frame clipped."""
        window = self.window
        window._set_text_scale(100)
        window.resize(320, 240)
        window.footer_status.setText("9,999,999/10,000,000 photos - 100%")
        for _ in range(3):
            self.qt.processEvents()
        for page, fields in ((window.setup_scroll, (window.output_edit,)),
                             (window.progress_scroll, (window.current_text, window.current_file_text))):
            window.pages.setCurrentWidget(page)
            self.qt.processEvents()
            viewport = page.viewport()
            for field in fields:
                with self.subTest(field=field.accessibleName()):
                    page.verticalScrollBar().setValue(0)
                    page.ensureWidgetVisible(field, 8, 8)
                    self.qt.processEvents()
                    self.qt.processEvents()
                    bounds = QtCore.QRect(field.mapTo(viewport, QtCore.QPoint()), field.size())
                    self.assertTrue(viewport.rect().contains(bounds),
                                    f"Full styled control {bounds} is outside {viewport.rect()}")

    def test_enter_in_output_path_does_not_start_processing(self):
        window = self.window
        window.output_edit.setText(str(self.directory))
        window.output_edit.setFocus()
        with patch.object(QtWidgets.QMessageBox, "information") as information:
            QtTest.QTest.keyClick(window.output_edit, QtCore.Qt.Key.Key_Return)
            self.qt.processEvents()
            information.assert_not_called()
        self.assertFalse(window._running)

    def test_close_accepts_when_work_finishes_during_confirmation(self):
        window = self.window
        window._set_running(True)

        def finish_while_question_is_open(*args, **kwargs):
            window._set_running(False)
            return QtWidgets.QMessageBox.StandardButton.Yes

        with patch.object(QtWidgets.QMessageBox, "question", side_effect=finish_while_question_is_open):
            window.close()
        self.assertFalse(window.isVisible())

    def test_start_and_cancel_keyboard_shortcuts(self):
        window = self.window
        started = threading.Event()

        def worker(folders, options, on_event, cancel_event):
            started.set()
            cancel_event.wait(timeout=5)
            on_event(dict(event="batch_done", cancelled=cancel_event.is_set(), total=0,
                          completed=0, applied=0, already=0, skipped=0, failed=0, elapsed=0.1))
            return []

        window.add_folders([self.directory])
        window.activateWindow()
        window.start_button.setFocus()
        self.qt.processEvents()
        with patch.object(dewarp_gui, "run_batch", side_effect=worker):
            QtTest.QTest.keyClick(window, QtCore.Qt.Key.Key_Return, QtCore.Qt.KeyboardModifier.ControlModifier)
            self.pump_until(started.is_set)
            self.assertTrue(window._running)
            self.assertTrue(window.cancel_button.hasFocus())
            QtTest.QTest.keyClick(window, QtCore.Qt.Key.Key_Escape)
            self.assertTrue(window._cancel.is_set())
            self.pump_until(lambda: not window._running)
        self.assertTrue(window.back_button.hasFocus())

    def test_system_font_changes_preserve_app_text_scale(self):
        original = QtGui.QFont(self.qt.font())
        try:
            window = self.window
            window.add_folders([self.directory])
            window._set_text_scale(150)
            updated = QtGui.QFont(original)
            updated.setPointSizeF(original.pointSizeF() + 2)
            self.qt.setFont(updated)
            self.qt.processEvents()
            self.assertAlmostEqual(window.font().pointSizeF(), updated.pointSizeF() * 1.5)
            for table in (window.folder_table, window.progress_table):
                self.assertGreaterEqual(table.rowHeight(0), window.fontMetrics().height())
        finally:
            self.qt.setFont(original)
            self.qt.processEvents()

    def test_removing_one_folder_preserves_other_results_and_output_links(self):
        window = self.window
        folders = [self.directory / name for name in ("First", "Second", "Third")]
        for folder in folders:
            folder.mkdir()
        window.add_folders(folders)
        output = self.directory / "Third - Dewarped Photos"
        output.mkdir()
        window._handle_event(dict(event="folder_start", index=2, folder=str(folders[2]),
                                  output=str(output), total=5))
        window._handle_event(dict(event="folder_done", index=2, output=str(output),
                                  status="Completed", total=5, completed=5, applied=5,
                                  elapsed_seconds=1.25))
        self.select_setup_row(1)
        window._remove_selected()
        self.assertEqual(window.folders, [folders[0], folders[2]])
        self.assertEqual(window.outputs, {1: output})
        self.assertEqual(window._statuses, {0: "Queued", 1: "Completed"})
        self.assertEqual(window._folder_stats[1]["completed"], 5)
        self.assertEqual(window._folder_stats[1]["index"], 1)
        self.assertEqual(window._current_index, 1)
        self.assertIn("Folder 2 of 2", window.current_text.text())
        self.assertEqual(window.folder_table.item(1, 4).toolTip(), str(output))
        self.assert_folder_tables_match()
        self.assertTrue(window.open_button.isEnabled())
        self.assertIn("Elapsed: 1.25 seconds", window._status_contents())
        window._remove_selected()
        self.assertEqual(window.folders, folders[:1])
        self.assertIsNone(window._current_index)
        self.assertEqual(window.current_text.text(), "No folder processing")

    def test_worker_error_marks_never_started_folders_not_processed(self):
        window = self.window
        folders = [self.directory / name for name in ("First", "Second")]
        for folder in folders:
            folder.mkdir()
        window.add_folders(folders)

        def worker(folders, options, on_event, cancel_event):
            on_event(dict(event="folder_start", index=0, folder=str(folders[0]), output="", total=2))
            raise OSError("Synthetic fixture failure")

        with patch.object(dewarp_gui, "run_batch", side_effect=worker):
            window._start_batch()
            self.pump_until(lambda: not window._running)
        self.assertEqual(window._statuses, {0: "Failed", 1: "Not processed"})
        self.assertIn("0 queued", window.overview_text.text())
        self.assertIn("1 not processed", window.overview_text.text())
        self.assertIn("Synthetic fixture failure", window.log.toPlainText())
        self.assertIn("Batch stopped with an error", window.current_file_text.text())
        self.assertTrue(window.back_button.isEnabled())
        window.back_button.click()
        self.assertTrue(window.start_button.isEnabled())

    def test_parent_without_direct_photos_finishes_with_useful_guidance(self):
        window = self.window
        child = self.directory / "Photos"
        child.mkdir()
        Image.new("RGB", (320, 240)).save(child / "DJI_0.JPG", xmp=XMP)
        window.add_folders([self.directory])
        window._start_batch()
        self.pump_until(lambda: not window._running)
        self.assertEqual(window._statuses, {0: "Empty"})
        self.assertFalse(window.outputs)
        self.assertEqual(window.current_text.text(), "No folder processing")
        self.assertNotIn("Scanning", window.current_file_text.text())
        self.assertIn("No JPEG photos found directly", window.batch_text.text())
        self.assertIn("add the photo-containing subfolders", window.log.toPlainText())

    def test_completion_issues_are_in_visible_and_accessible_summary(self):
        window = self.window
        window.add_folders([self.directory])
        window._handle_event(dict(event="folder_done", index=0, status="Completed with issues",
                                  total=2, completed=2, applied=1, skipped=1))
        window._handle_event(dict(event="batch_done", cancelled=False, total=2, completed=2,
                                  applied=1, skipped=1, failed=0, elapsed=0.5))
        self.assertIn("Finished with issues", window.batch_text.text())
        self.assertIn("Finished with issues", window.footer_status.text())
        self.assertIn("Finished with issues", window._status_contents())


if __name__ == "__main__":
    unittest.main()
