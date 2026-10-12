"""Accessible Qt batch GUI. Engine events cross threads through a plain queue.

Qt owns operating-system DPI scaling. App text scaling starts with the system
font, and scrolling content leaves the Start/Cancel footer available.
"""
from __future__ import annotations

import os
from pathlib import Path
import queue
import sys
import threading
import time

from PySide6.QtCore import QEvent, QPoint, QRect, QSize, Qt, QTimer, QUrl
from PySide6.QtGui import QAction, QActionGroup, QAccessible, QAccessibleAnnouncementEvent, QDesktopServices, QFont, QKeySequence
from PySide6.QtWidgets import (QApplication, QAbstractItemView, QDialog, QFileDialog, QGridLayout, QGroupBox, QHeaderView, QLabel, QLayout, QLineEdit, QMainWindow, QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QScrollArea, QSizePolicy, QStackedWidget, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from dewarp.core.engine import BatchOptions, exiftool_path, run_batch
from .theme import install_system_theme
from .buttons import ActionButton as _Button
from .settings import ProcessingSettings, SettingsDialog, SettingsStore
from .scrolling import ContentScrollArea as _ContentScrollArea


def _duration(seconds):
    hours, remainder = divmod(max(0, int(seconds)), 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes}:{seconds:02d}"


def _accessible(widget, name, description=""):
    widget.setAccessibleName(name)
    widget.setAccessibleDescription(description)
    widget.setObjectName(name.lower().replace(" ", "_"))
    return widget


class _StatusField(QLineEdit):
    """Selectable status text remains readable even for unbroken long names."""
    def __init__(self, text=""):
        super().__init__(text)
        self.setReadOnly(True)
        self.setMinimumWidth(0)

    def setText(self, text):
        super().setText(text)
        self.setCursorPosition(0)


class _CompactTable(QTableWidget):
    """Prefer the font-based minimum rows; expand when the page has room."""
    def sizeHint(self):
        hint = super().sizeHint()
        hint.setHeight(max(self.minimumHeight(), self.minimumSizeHint().height()))
        return hint


class _CompactLog(QPlainTextEdit):
    """Do not let the generic editor's tall size hint force outer scrolling."""
    def sizeHint(self):
        hint = super().sizeHint()
        hint.setHeight(max(self.minimumHeight(), self.minimumSizeHint().height()))
        return hint


class _FlowLayout(QLayout):
    """Wrap button rows using their current font and platform style hints."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self._items = []
        self.setContentsMargins(0, 0, 0, 0)
        self.setSpacing(8)

    def addItem(self, item):
        self._items.append(item)

    def count(self):
        return len(self._items)

    def itemAt(self, index):
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index):
        return self._items.pop(index) if 0 <= index < len(self._items) else None

    def expandingDirections(self):
        return Qt.Orientation(0)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self._arrange(QRect(0, 0, width, 0), True)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._arrange(rect, False)

    def minimumSize(self):
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        return size

    def sizeHint(self):
        return self.minimumSize()

    def _arrange(self, rect, measure):
        x, y, line_height = rect.x(), rect.y(), 0
        for item in self._items:
            size = item.sizeHint()
            if x + size.width() > rect.right() + 1 and line_height:
                x, y, line_height = rect.x(), y + line_height + self.spacing(), 0
            if not measure:
                item.setGeometry(QRect(x, y, min(size.width(), rect.width()), size.height()))
            x += size.width() + self.spacing()
            line_height = max(line_height, size.height())
        return y + line_height - rect.y()


class DewarpApp(QMainWindow):
    TEXT_SCALES = (100, 125, 150, 175, 200)

    def __init__(self, settings_store=None):
        install_system_theme(QApplication.instance())
        super().__init__()
        self.settings_store = settings_store if settings_store is not None else SettingsStore()
        self.processing_settings: ProcessingSettings = self.settings_store.load()
        self.folders = []
        self.outputs = {}
        self._events = queue.Queue()
        self._cancel = threading.Event()
        self._worker = None
        self._running = False
        self._close_when_finished = False
        self._started_at = 0.0
        self._batch_total = self._batch_completed = 0
        self._current_index = None
        self._folder_stats = {}
        self._statuses = {}
        self._batch_summary_received = False
        self._base_font = QFont(QApplication.font())
        self._text_scale = 100
        self._footer_stacked = None
        self.setWindowTitle("DJI Photo Dewarp — Batch Processor")
        self.setAccessibleName("DJI Photo Dewarp batch processor")
        self.setMinimumSize(320, 240)
        self.setAcceptDrops(True)
        self._build_ui()
        self._build_menus()
        self.pages.currentChanged.connect(self._page_changed)
        self._page_changed()
        self._connect_focus_order()
        self._apply_font_metrics()
        self._initial_screen = self.screen()
        self._startup_positioned = False
        available = self._initial_screen.availableGeometry()
        self.resize(min(1100, max(320, available.width() - 60)), min(820, max(240, available.height() - 60)))
        self.move(available.center() - self.rect().center())
        self._timer = QTimer(self)
        self._timer.setInterval(100)
        self._timer.timeout.connect(self._poll_events)
        self._timer.start()
        QApplication.instance().focusChanged.connect(self._ensure_focus_visible)
        QApplication.instance().fontChanged.connect(self._system_font_changed)
        if self.settings_store.last_load_warning:
            self._log(self.settings_store.last_load_warning)
        if not exiftool_path():
            self._log("ExifTool was not found on PATH. Run scripts/Setup.ps1, then restart. Full XMP and DJI MakerNotes require ExifTool.")

    def showEvent(self, event):
        super().showEvent(event)
        if not self._startup_positioned:
            self._startup_positioned = True
            # Native title-bar/frame sizes are reliable only after mapping.
            QTimer.singleShot(0, self._position_initial_frame)

    def _position_initial_frame(self):
        screens = QApplication.screens()
        screen = self._initial_screen if self._initial_screen in screens else self.screen()
        available = screen.availableGeometry()
        frame = self.frameGeometry()
        self.resize(min(self.width(), max(self.minimumWidth(), available.width() - frame.width() + self.width())), min(self.height(), max(self.minimumHeight(), available.height() - frame.height() + self.height())))
        frame = self.frameGeometry()
        target = available.center() - frame.center() + frame.topLeft()
        target.setX(max(available.left(), min(target.x(), available.right() - frame.width() + 1)))
        target.setY(max(available.top(), min(target.y(), available.bottom() - frame.height() + 1)))
        self.move(self.pos() + target - frame.topLeft())

    @staticmethod
    def _label(text="", description=""):
        label = QLabel(text)
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setWordWrap(True)
        label.setMinimumWidth(0)
        label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        # Native label names follow their text, so live counts remain accessible.
        label.setAccessibleDescription(description)
        return label

    def _button(self, text, name, callback, description="", *, role=None):
        button = _accessible(_Button(text, role=role), name, description)
        button.clicked.connect(callback)
        button.setAutoDefault(False)
        return button

    @staticmethod
    def _group(title, parent_layout, stretch=0):
        group = QGroupBox(title)
        group.setMinimumWidth(0)
        layout = QVBoxLayout(group)
        layout.setSpacing(6)
        parent_layout.addWidget(group, stretch)
        return layout

    @staticmethod
    def _flow(*widgets):
        container = QWidget()
        layout = _FlowLayout(container)
        for widget in widgets:
            layout.addWidget(widget)
        return container

    @staticmethod
    def _page(name, description):
        scroll = _accessible(_ContentScrollArea(), name, description)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setMinimumSize(0, 0)
        scroll.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        body = QWidget()
        body.setMinimumWidth(0)
        layout = QVBoxLayout(body)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)
        scroll.setWidget(body)
        return scroll, layout

    def _table(self, name, description):
        table = _accessible(_CompactTable(0, 5), name, description)
        table.setHorizontalHeaderLabels(["Input folder", "Status", "Processed", "Photos", "Output folder"])
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        table.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        table.setWordWrap(True)
        table.setMinimumWidth(0)
        table.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        table.horizontalHeader().setStretchLastSection(True)
        table.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        table.verticalHeader().setVisible(False)
        table.itemSelectionChanged.connect(self._update_selection)
        return table

    @property
    def scroll_area(self):
        """The active page's fallback scroll surface, also used by keyboard focus."""
        return self.progress_scroll if self.pages.currentIndex() == 1 else self.setup_scroll

    def _build_ui(self):
        central = QWidget()
        central_layout = QVBoxLayout(central)
        central_layout.setContentsMargins(0, 0, 0, 0)
        central_layout.setSpacing(0)
        self.setCentralWidget(central)
        self.pages = _accessible(QStackedWidget(), "Batch workflow", "Set up a batch, then review its progress and results.")
        self.pages.setMinimumSize(0, 0)
        central_layout.addWidget(self.pages, 1)
        self.setup_scroll, setup_layout = self._page("Batch setup", "Add input folders and choose where to save the output. Processing defaults are under File, Settings.")
        self.progress_scroll, progress_page_layout = self._page("Batch progress and results", "Live progress, folder results, and activity. Batch controls remain below this area.")
        self.pages.addWidget(self.setup_scroll)
        self.pages.addWidget(self.progress_scroll)

        setup_layout.addWidget(self._label("Dewarp DJI JPEG photos using their calibration metadata. Original files are preserved."))
        folder_layout = self._group("Folders and output", setup_layout, 1)
        self.add_button = self._button("Add folder…", "Add input folder", self._browse_folder, "Add another folder. Shortcut: Control+O.", role="browse")
        self.remove_button = self._button("Remove selected", "Remove selected folders", self._remove_selected, "Remove selected queue rows without deleting files.")
        self.clear_button = self._button("Clear queue", "Clear folder queue", self._clear_queue)
        folder_layout.addWidget(self._flow(self.add_button, self.remove_button, self.clear_button))
        self.setup_summary = self._label("No folders added", "Number of folders in the batch setup")
        folder_layout.addWidget(self.setup_summary)
        self.folder_table = self._table("Input folder queue", "Input folder, status, processed photos, total photos, and output folder. Arrow keys navigate; Shift or Control selects rows. Delete removes selected folders on the setup page. Full paths are available in cell tooltips and the Batch status dialog.")
        self.folder_table.installEventFilter(self)
        self.folder_table.viewport().setAcceptDrops(True)
        self.folder_table.viewport().installEventFilter(self)
        folder_layout.addWidget(self.folder_table, 1)
        folder_layout.addWidget(self._label("Add or drop multiple folders. Only JPEGs directly inside each folder are processed."))
        self.output_edit = _accessible(QLineEdit(), "Output parent folder", "Leave blank to save inside each input folder. Enter or browse to a common parent folder for all outputs.")
        self.output_edit.setMinimumWidth(0)
        self.output_edit.setPlaceholderText("Save inside each input folder")
        self.output_edit.textChanged.connect(self._update_selection)
        label = QLabel("&Output location")
        label.setBuddy(self.output_edit)
        folder_layout.addWidget(label)
        folder_layout.addWidget(self.output_edit)
        self.output_browse_button = self._button("Browse…", "Browse output location", self._browse_output, "Choose one parent folder for every batch output. Shortcut: Control+Shift+O.", role="browse")
        self.output_reset_button = self._button("Use input folders", "Use input folders for output", lambda: self.output_edit.clear())
        folder_layout.addWidget(self._flow(self.output_browse_button, self.output_reset_button))
        folder_layout.addWidget(self._label("Output names: “Input name - Dewarped Photos”. Existing names receive a number."))
        self._editable_widgets = [self.add_button, self.remove_button, self.clear_button, self.output_edit, self.output_browse_button, self.output_reset_button]

        progress_layout = self._group("Live progress", progress_page_layout)
        self.current_text = _accessible(_StatusField("No folder processing"), "Current folder status", "Read-only current folder and position in the batch. Home and End navigate long folder names; Control+A and Control+C copy the full status.")
        self.current_counts = self._label("", "Current folder photo counts")
        self.current_file_text = _accessible(_StatusField(), "Latest photo or folder status", "Read-only latest filename or folder status. Long filenames scroll horizontally. Home and End navigate; Control+A and Control+C copy.")
        self.current_file_text.setPlaceholderText("Latest processed photo")
        self.batch_text = self._label("Add one or more folders to get started.", "Batch completion summary")
        self.time_label = self._label("Elapsed: 0:00 · Rate: — · ETA: —", "Elapsed time, processing rate, and estimated remaining time")
        self.current_bar = _accessible(QProgressBar(), "Current folder completion", "Percentage processed in the current folder, including skipped or failed attempts. Counts describe the results.")
        self.batch_bar = _accessible(QProgressBar(), "Overall batch completion", "Photo-weighted completion across all input folders, including skipped or failed attempts.")
        for bar in (self.current_bar, self.batch_bar):
            bar.setRange(0, 100)
            bar.setValue(0)
            bar.setMinimumWidth(0)
            bar.setFormat("0.0%")
        for control in (self.current_text, self.current_bar, self.current_counts, self.current_file_text, self.batch_text, self.batch_bar, self.time_label):
            progress_layout.addWidget(control)
        self._set_current_counts({})

        results_layout = self._group("Folder results", progress_page_layout, 2)
        self.overview_text = self._label("0 folders queued", "Folder queue summary")
        results_layout.addWidget(self.overview_text)
        self.progress_table = self._table("Batch folder results", "Input folder, status, processed photos, total photos, and output folder. Full paths are available in cell tooltips and the Batch status dialog. Select a completed folder, then choose Open output.")
        self.progress_table.itemDoubleClicked.connect(lambda _item: self._open_output())
        results_layout.addWidget(self.progress_table, 1)
        self.open_button = self._button("Open output", "Open selected output folder", self._open_output, "Open the selected output in the file manager. Shortcut: Control+Shift+E.", role="browse")
        self.open_button.setEnabled(False)
        results_layout.addWidget(self._flow(self.open_button))
        log_layout = self._group("Activity", progress_page_layout, 1)
        self.log = _accessible(_CompactLog(), "Processing activity log", "Read-only messages, errors, and warnings. Control+A and Control+C copy the log. Per-folder reports retain complete results.")
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(1000)
        self.log.setMinimumWidth(0)
        log_layout.addWidget(self.log, 1)

        self._footer = QWidget()
        self._footer.installEventFilter(self)
        self._footer_layout = QGridLayout(self._footer)
        self._footer_layout.setContentsMargins(12, 8, 12, 8)
        self._footer_layout.setSpacing(6)
        self._footer_layout.setColumnStretch(1, 1)
        self.footer_status = self._label("Ready", "Compact processing status")
        self.start_button = self._button("Start batch", "Start batch", self._start_batch, "Process every listed folder, including completed rows; reruns create separate output folders. Shortcuts: Control+Return or Alt+S. Return or Space works when this button has focus.", role="positive")
        self.cancel_button = self._button("Cancel batch", "Cancel batch", self._cancel_batch, "Stop after active photos finish safely. Shortcut: Escape while a batch is active.")
        self.back_button = self._button("Back to setup", "Back to batch setup", self._show_setup, "Keep these results and return to setup. The folder queue is retained for a new batch.")
        self.back_button.setVisible(False)
        self.cancel_button.setEnabled(False)
        self._footer_layout.addWidget(self.footer_status, 0, 0, 1, 3)
        self._footer_layout.addWidget(self.cancel_button, 1, 0)
        self._footer_layout.addWidget(self.start_button, 1, 2)
        self._footer_layout.addWidget(self.back_button, 1, 2)
        central_layout.addWidget(self._footer)

    def _action(self, menu, text, callback, shortcuts=()):
        action = QAction(text, self)
        action.triggered.connect(callback)
        if shortcuts:
            action.setShortcuts([QKeySequence(key) for key in shortcuts])
        menu.addAction(action)
        return action

    def _build_menus(self):
        file_menu = self.menuBar().addMenu("&File")
        self.add_action = self._action(file_menu, "Add folder…", self._browse_folder, ("Ctrl+O",))
        self.output_action = self._action(file_menu, "Choose output location…", self._browse_output, ("Ctrl+Shift+O",))
        self.open_action = self._action(file_menu, "Open selected output", self._open_output, ("Ctrl+Shift+E",))
        self.open_action.setEnabled(False)
        self.settings_action = self._action(file_menu, "Settings…", self._show_settings, ("Ctrl+,",))
        file_menu.addSeparator()
        self.start_action = self._action(file_menu, "Start batch", self._start_batch, ("Ctrl+Return", "Alt+S"))
        self.cancel_action = self._action(file_menu, "Cancel batch", self._cancel_batch, ("Esc",))
        self.cancel_action.setEnabled(False)
        file_menu.addSeparator()
        self._action(file_menu, "Close", self.close, ("Alt+F4",))
        view_menu = self.menuBar().addMenu("&View")
        self._action(view_menu, "Batch status…", self._show_status, ("Ctrl+I",))
        text_menu = view_menu.addMenu("Text size")
        self._scale_group = QActionGroup(self)
        self._scale_group.setExclusive(True)
        self._scale_actions = {}
        for percent in self.TEXT_SCALES:
            action = QAction(f"{percent}%", self)
            action.setCheckable(True)
            action.setChecked(percent == 100)
            action.triggered.connect(lambda _checked=False, value=percent: self._set_text_scale(value))
            self._scale_group.addAction(action)
            text_menu.addAction(action)
            self._scale_actions[percent] = action
        self._action(view_menu, "Increase text size", lambda: self._step_text_scale(1), ("Ctrl++", "Ctrl+="))
        self._action(view_menu, "Decrease text size", lambda: self._step_text_scale(-1), ("Ctrl+-",))
        self._action(view_menu, "Reset text size", lambda: self._set_text_scale(100), ("Ctrl+0",))
        help_menu = self.menuBar().addMenu("&Help")
        self._action(help_menu, "Keyboard and processing help…", self._show_help, ("F1",))

    def _connect_focus_order(self):
        order = [self.add_button, self.remove_button, self.clear_button, self.folder_table,
                 self.output_edit, self.output_browse_button, self.output_reset_button,
                 self.current_text, self.current_file_text, self.progress_table,
                 self.open_button, self.log, self.cancel_button, self.start_button, self.back_button]
        for previous, following in zip(order, order[1:]):
            QWidget.setTabOrder(previous, following)

    def _ensure_focus_visible(self, _previous, current):
        if current is not None:
            for scroll in (self.setup_scroll, self.progress_scroll):
                if scroll.isVisible() and scroll.widget().isAncestorOf(current):
                    scroll.ensureWidgetVisible(current, 8, 8)
                    break

    def _page_changed(self, *_args):
        setup = self.pages.currentIndex() == 0
        self.start_button.setVisible(setup)
        self.back_button.setVisible(not setup)
        self._footer_stacked = None
        self._reflow_footer()
        self._update_availability()

    def _show_setup(self):
        if self._running:
            return
        self.pages.setCurrentWidget(self.setup_scroll)
        if not self._batch_summary_received and not self._batch_completed:
            self.footer_status.setText("Ready")
        (self.start_button if self.folders else self.add_button).setFocus()

    def _show_settings(self):
        if self._running:
            return
        dialog = SettingsDialog(self.processing_settings, self)
        try:
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return
            try:
                candidate = dialog.settings_value()
                self.settings_store.save(candidate)
            except (OSError, ValueError) as exc:
                QMessageBox.warning(self, "Could not save settings", str(exc))
                return
            self.processing_settings = candidate
            self._log("Processing settings saved; they will apply to the next batch.")
        finally:
            dialog.deleteLater()

    def _step_text_scale(self, step):
        index = self.TEXT_SCALES.index(self._text_scale)
        self._set_text_scale(self.TEXT_SCALES[max(0, min(len(self.TEXT_SCALES) - 1, index + step))])

    def _set_text_scale(self, percent):
        if percent not in self.TEXT_SCALES:
            raise ValueError("Text size must be 100, 125, 150, 175, or 200 percent")
        self._text_scale = percent
        font = QFont(self._base_font)
        if font.pointSizeF() > 0:
            font.setPointSizeF(self._base_font.pointSizeF() * percent / 100)
        else:
            font.setPixelSize(round(self._base_font.pixelSize() * percent / 100))
        self.setFont(font)
        self.menuBar().setFont(font)
        for menu in self.menuBar().findChildren(QWidget):
            menu.setFont(font)
        self._scale_actions[percent].setChecked(True)
        self._apply_font_metrics()
        QTimer.singleShot(0, lambda: self._ensure_focus_visible(None, QApplication.focusWidget()))

    def changeEvent(self, event):
        super().changeEvent(event)
        if not hasattr(self, "_scale_actions"):
            return
        if event.type() == QEvent.Type.ApplicationFontChange:
            self._base_font = QFont(QApplication.font())
            self._set_text_scale(self._text_scale)
        elif event.type() == QEvent.Type.FontChange:
            self._apply_font_metrics()

    def _system_font_changed(self, font):
        # Qt may omit ApplicationFontChange for a window with an explicit zoom
        # font. The application signal still reports the new system baseline.
        self._base_font = QFont(font)
        self._set_text_scale(self._text_scale)

    def _apply_font_metrics(self):
        line = self.fontMetrics().height()
        for table, rows in ((self.folder_table, 5), (self.progress_table, 3)):
            table.setMinimumHeight(line * rows + table.horizontalHeader().sizeHint().height() + 24)
            for column, characters in enumerate((22, 18, 10, 8, 24)):
                table.setColumnWidth(column, self.fontMetrics().horizontalAdvance("M") * characters + 20)
            table.resizeRowsToContents()
        self.log.setMinimumHeight(line * 3 + 16)
        self._reflow_footer()

    def _reflow_footer(self):
        right_button = self.start_button if self.pages.currentIndex() == 0 else self.back_button
        needed = right_button.sizeHint().width() + self.cancel_button.sizeHint().width() + 6
        stacked = self._footer.width() - 24 < needed
        if stacked == self._footer_stacked:
            return
        self._footer_stacked = stacked
        for button in (self.start_button, self.back_button, self.cancel_button):
            self._footer_layout.removeWidget(button)
        self._footer_layout.addWidget(self.cancel_button, 1, 0, 1, 3 if stacked else 1)
        for button in (self.start_button, self.back_button):
            self._footer_layout.addWidget(button, 2 if stacked else 1, 0 if stacked else 2, 1, 3 if stacked else 1)

    def eventFilter(self, watched, event):
        if hasattr(self, "_footer") and watched is self._footer and event.type() == QEvent.Type.Resize:
            self._reflow_footer()
        if watched in (self.folder_table, self.folder_table.viewport()):
            if event.type() == QEvent.Type.KeyPress and event.key() == Qt.Key.Key_Delete:
                self._remove_selected()
                return True
            if event.type() in (QEvent.Type.DragEnter, QEvent.Type.DragMove) and not self._running and self.pages.currentIndex() == 0 and event.mimeData().hasUrls():
                event.acceptProposedAction()
                return True
            if event.type() == QEvent.Type.Drop:
                self.dropEvent(event)
                return True
        return super().eventFilter(watched, event)

    def dragEnterEvent(self, event):
        if not self._running and self.pages.currentIndex() == 0 and any(url.isLocalFile() for url in event.mimeData().urls()):
            event.acceptProposedAction()

    def dropEvent(self, event):
        if not self._running and self.pages.currentIndex() == 0:
            self._drop_paths(url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile())
            event.acceptProposedAction()

    def _drop_paths(self, paths):
        self.add_folders(paths)

    def _browse_folder(self):
        if not self._running and self.pages.currentIndex() == 0:
            folder = QFileDialog.getExistingDirectory(self, "Add input folder")
            if folder:
                self.add_folders([folder])

    def _browse_output(self):
        if not self._running and self.pages.currentIndex() == 0:
            folder = QFileDialog.getExistingDirectory(self, "Choose parent folder for all outputs")
            if folder:
                self.output_edit.setText(folder)

    def add_folders(self, paths):
        if self._running or self.pages.currentIndex() != 0:
            return
        known = {os.path.normcase(str(folder)) for folder in self.folders}
        added = duplicates = invalid = 0
        for raw_path in paths:
            try:
                path = Path(raw_path).expanduser().resolve()
                if not path.is_dir():
                    self._log(f"Not a folder: {path}")
                    invalid += 1
                    continue
            except (OSError, ValueError) as exc:
                self._log(f"Could not add folder: {exc}")
                invalid += 1
                continue
            key = os.path.normcase(str(path))
            if key in known:
                self._log(f"Already queued: {path}")
                duplicates += 1
                continue
            known.add(key)
            self.folders.append(path)
            added += 1
            index = len(self.folders) - 1
            self._statuses[index] = "Queued"
            self.folder_table.insertRow(index)
            self.progress_table.insertRow(index)
            self._update_row(index)
        if self.folders and not self._selected_rows():
            self.folder_table.selectRow(0)
        self._update_overview()
        self._update_selection()
        feedback = [f"{added} added"] if added else []
        if duplicates:
            feedback.append(f"{duplicates} already queued")
        if invalid:
            feedback.append(f"{invalid} unavailable")
        if feedback:
            self.footer_status.setText(" · ".join(feedback))
            self.footer_status.setToolTip("Control+I opens full details, including the activity log.")

    def _selected_rows(self, table=None):
        if table is None:
            table = self.folder_table if self.pages.currentIndex() == 0 else self.progress_table
        return sorted({index.row() for index in table.selectionModel().selectedRows()})

    def _remove_selected(self):
        if self._running or self.pages.currentIndex() != 0:
            return
        selected = set(self._selected_rows())
        if selected:
            # Row indexes identify engine results. Reindex retained results
            # instead of clearing them when an unrelated queue row is removed.
            retained = [index for index in range(len(self.folders)) if index not in selected]
            new_index = {old: new for new, old in enumerate(retained)}
            self.folders = [self.folders[index] for index in retained]
            self.outputs = {new_index[index]: value for index, value in self.outputs.items() if index in new_index}
            self._statuses = {new_index[index]: value for index, value in self._statuses.items() if index in new_index}
            self._folder_stats = {new_index[index]: dict(value, index=new_index[index]) for index, value in self._folder_stats.items() if index in new_index}
            self._current_index = new_index.get(self._current_index)
            for index in sorted(selected, reverse=True):
                self.folder_table.removeRow(index)
                self.progress_table.removeRow(index)
            if not self.folders:
                self._clear_queue()
                return
            for index in range(len(self.folders)):
                self._update_row(index)
            if self._current_index is None:
                self.current_text.setText("No folder processing")
                self.current_file_text.clear()
                self._set_current_counts({})
                self._set_progress(self.current_bar, 0, 0)
            else:
                self.current_text.setText(f"Folder {self._current_index + 1} of {len(self.folders)} · {self.folders[self._current_index].name}")
            self.folder_table.selectRow(min(min(selected), len(self.folders) - 1))
            self._update_overview()
            self._update_selection()

    def _clear_queue(self):
        if self._running or self.pages.currentIndex() != 0:
            return
        self.folder_table.setRowCount(0)
        self.progress_table.setRowCount(0)
        self.folders.clear()
        self.outputs.clear()
        self._statuses.clear()
        self._folder_stats.clear()
        self._current_index = None
        self._batch_total = self._batch_completed = 0
        self._batch_summary_received = False
        self.current_text.setText("No folder processing")
        self.current_file_text.setText("")
        self._set_current_counts({})
        for bar in (self.current_bar, self.batch_bar):
            bar.setValue(0)
            bar.setFormat("0.0%")
        self.batch_text.setText("Add one or more folders to get started.")
        self.time_label.setText("Elapsed: 0:00 · Rate: — · ETA: —")
        self.footer_status.setText("Ready")
        self._update_overview()
        self._update_selection()

    def _update_selection(self, *_args):
        # Full paths stay attached to table cells and in the status snapshot.
        self.output_edit.setToolTip(self.output_edit.text() or "Save inside each input folder")
        self._update_availability()

    def _update_availability(self):
        """Derive visible buttons and menu actions from the same page/state."""
        idle = not self._running
        setup = self.pages.currentIndex() == 0
        can_edit = idle and setup
        selection = [index for index in self._selected_rows() if index < len(self.folders)]
        output = self.outputs.get(selection[0]) if selection else None
        try:
            can_open = bool(output and output.is_dir())
        except OSError:
            can_open = False
        can_start = can_edit and bool(self.folders)
        can_cancel = self._running and not self._cancel.is_set()
        for widget in self._editable_widgets:
            widget.setEnabled(can_edit)
        self.remove_button.setEnabled(can_edit and bool(selection))
        self.clear_button.setEnabled(can_edit and bool(self.folders))
        self.output_reset_button.setEnabled(can_edit and bool(self.output_edit.text().strip()))
        self.start_button.setEnabled(can_start)
        self.cancel_button.setEnabled(can_cancel)
        self.back_button.setEnabled(idle and not setup)
        self.open_button.setEnabled(can_open)
        for action, enabled in ((self.add_action, can_edit), (self.output_action, can_edit),
                                (self.start_action, can_start), (self.cancel_action, can_cancel),
                                (self.open_action, can_open), (self.settings_action, idle)):
            action.setEnabled(enabled)

    def _open_output(self):
        selection = self._selected_rows()
        output = self.outputs.get(selection[0]) if selection else None
        if output and output.is_dir() and not QDesktopServices.openUrl(QUrl.fromLocalFile(str(output))):
            QMessageBox.warning(self, "Cannot open output", f"The system could not open {output}.")

    def _set_running(self, running):
        self._running = running
        self._update_availability()

    def _start_batch(self):
        if self._running or self.pages.currentIndex() != 0:
            return
        if not self.folders:
            QMessageBox.information(self, "Add an input folder", "Add one or more folders containing DJI JPEG photos.")
            self.add_button.setFocus()
            return
        if self.processing_settings.copy_xmp and not exiftool_path():
            QMessageBox.warning(self, "ExifTool is required for full XMP", "ExifTool was not found on PATH. Run scripts/Setup.ps1 and restart this window. DJI photos with MakerNotes also require ExifTool when Preserve XMP is off.")
            self.add_button.setFocus()
            return
        try:
            output_text = self.output_edit.text().strip()
            output = Path(output_text).expanduser().resolve() if output_text else None
            if output and output.exists() and not output.is_dir():
                raise ValueError("The output location must be a folder.")
            options = BatchOptions(output_root=output, copy_xmp=self.processing_settings.copy_xmp, alpha=self.processing_settings.alpha, workers=self.processing_settings.workers, require_dewarp=True)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Check processing options", str(exc))
            return
        self.outputs.clear()
        self._folder_stats.clear()
        self._statuses = {index: "Queued" for index in range(len(self.folders))}
        self._batch_summary_received = False
        self._batch_total = self._batch_completed = 0
        self._current_index = None
        self._cancel.clear()
        self._started_at = time.monotonic()
        self.pages.setCurrentWidget(self.progress_scroll)
        self._set_running(True)
        for bar in (self.current_bar, self.batch_bar):
            bar.setValue(0)
            bar.setFormat("0.0%")
        self.current_text.setText("Preparing batch…")
        self.current_file_text.setText("Scanning input folders…")
        self._set_current_counts({})
        self.batch_text.setText("Preparing batch…")
        self.footer_status.setText("Preparing batch…")
        for index in range(len(self.folders)):
            self._update_row(index)
        self._update_overview()
        self._update_selection()
        if self.folders:
            self.progress_table.selectRow(0)
        self._log(f"Starting batch: {len(self.folders)} folder(s); requested parallel photos: {options.workers}.")
        self.cancel_button.setFocus()
        # Show live progress once at launch without disturbing later scrolling.
        self.scroll_area.ensureWidgetVisible(self.current_bar, 8, 8)
        self._worker = threading.Thread(target=self._run_worker, args=(list(self.folders), options), name="dewarp-batch", daemon=False)
        self._worker.start()

    def _run_worker(self, folders, options):
        try:
            run_batch(folders, options, on_event=self._events.put, cancel_event=self._cancel)
        except Exception as exc:
            self._events.put({"event": "worker_error", "message": f"{type(exc).__name__}: {exc}"})
        finally:
            self._events.put({"event": "worker_exit"})

    def _cancel_batch(self):
        if self._running and not self._cancel.is_set():
            self._cancel.set()
            self._update_availability()
            self.batch_text.setText("Cancelling… active photos will finish before stopping.")
            self.footer_status.setText("Cancelling…")
            self._log("Cancellation requested; waiting for active photos to finish.")
            self._announce("Cancellation requested. Active photos will finish safely.")

    def closeEvent(self, event):
        if self._running:
            if not self._close_when_finished and QMessageBox.question(self, "Stop processing and close?", "Cancel this batch and close after active photos finish? Completed output files will be kept.", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes:
                self._close_when_finished = True
                # A modal confirmation runs a nested event loop; the worker may
                # finish while the user is deciding. Do not wait for a second
                # worker-exit event that has already been handled.
                if not self._running:
                    self._timer.stop()
                    event.accept()
                    return
                self._cancel_batch()
            event.ignore()
        else:
            self._timer.stop()
            event.accept()

    def _poll_events(self):
        for _ in range(100):
            try:
                event = self._events.get_nowait()
            except queue.Empty:
                break
            self._handle_event(event)
        if self._running:
            self._update_timing()
        if self._running and self._worker is not None and not self._worker.is_alive() and self._events.empty():
            self._finish_worker()
            if self._close_when_finished:
                self.close()

    def _handle_event(self, event):
        kind = event.get("event")
        if kind == "batch_start":
            self._batch_total = event.get("total", 0)
            self._log(f"Batch contains {self._batch_total:,} photos in {event.get('folders_total', len(self.folders))} folder(s). Effective parallel photos: {event.get('workers', '—')}.")
            self._update_batch_progress()
        elif kind in ("folder_queued", "folder_start", "folder_done"):
            index = event["index"]
            if event.get("output"):
                self.outputs[index] = Path(event["output"])
            self._folder_stats[index] = dict(event)
            if kind == "folder_queued":
                status = "Queued"
            elif kind == "folder_start":
                status = "Processing"
                self._current_index = index
                self.current_text.setText(f"Folder {index + 1} of {len(self.folders)} · {self.folders[index].name}")
                self.current_file_text.setText("Reading calibration metadata…")
                self._set_current_counts(event)
                self._set_progress(self.current_bar, 0, event.get("total", 0))
                self.progress_table.scrollToItem(self.progress_table.item(index, 0))
                self._log(f"Processing {event.get('folder', self.folders[index])} → {event.get('output', '—')}")
                self._announce(self.current_text.text())
            else:
                status = event.get("status", "Completed")
                if index == self._current_index:
                    self._set_current_counts(event)
                    self._set_progress(self.current_bar, event.get("completed", 0), event.get("total", 0))
                    self.current_file_text.setText(status)
                elapsed = event.get("elapsed_seconds")
                timing = f" Elapsed: {elapsed:.2f} seconds." if elapsed is not None else ""
                detail = " No JPEGs directly inside this folder; add the photo-containing subfolders separately." if status == "Empty" else ""
                self._log(f"{self.folders[index].name}: {status}. Dewarped {event.get('applied', 0)}, already corrected {event.get('already', 0)}, skipped {event.get('skipped', 0)}, failed {event.get('failed', 0)}." + timing + detail + (f" {event['error']}" if event.get("error") else ""))
                self._announce(f"{self.folders[index].name}: {status}.")
            self._statuses[index] = status
            self._update_row(index)
            self._update_overview()
            self._update_selection()
        elif kind == "photo_done":
            index = event["index"]
            self._folder_stats[index] = dict(event)
            self._batch_total = event.get("batch_total", self._batch_total)
            self._batch_completed = event.get("batch_completed", self._batch_completed + 1)
            self._current_index = index
            self._set_progress(self.current_bar, event.get("completed", 0), event.get("total", 0))
            self._set_current_counts(event)
            self.current_file_text.setText(f"Latest: {event.get('name', '')}")
            self._update_row(index)
            self._update_batch_progress()
            if event.get("status") in ("failed", "skipped"):
                self._log(f"{event.get('status', '').capitalize()} {event.get('name', '')}: {event.get('message', '')}")
            for warning in event.get("warnings", []) or []:
                self._log(f"Warning · {event.get('name', '')}: {warning}")
        elif kind == "batch_done":
            self._batch_summary_received = True
            self._batch_total = event.get("total", self._batch_total)
            self._batch_completed = event.get("completed", self._batch_completed)
            cancelled = event.get("cancelled", False)
            for index, status in list(self._statuses.items()):
                if status in ("Queued", "Processing"):
                    self._statuses[index] = "Cancelled" if cancelled else "Failed"
                    self._update_row(index)
            issues = (any(status in ("Failed", "Completed with issues") for status in self._statuses.values())
                      or event.get("failed", 0) or event.get("skipped", 0) or event.get("warnings", 0))
            final = "Cancelled" if cancelled else "Finished with issues" if issues else "Finished"
            if self._current_index is None:
                self.current_text.setText("No folder processing")
                self.current_file_text.setText(final)
            self._update_overview()
            self._update_batch_progress(final=final)
            self._update_timing(event.get("elapsed"), finished=True)
            self._log(f"Batch {'cancelled' if cancelled else 'finished'}: {self._batch_completed:,}/{self._batch_total:,} photos processed; dewarped {event.get('applied', 0):,}, already corrected {event.get('already', 0):,}, skipped {event.get('skipped', 0):,}, failed {event.get('failed', 0):,}.")
            warnings = event.get("warnings", []) or []
            if isinstance(warnings, int):
                if warnings:
                    self._log(f"{warnings:,} metadata warning(s); see the per-folder processing-report.json files for details.")
            else:
                for warning in warnings:
                    self._log(f"Warning: {warning}")
            self._announce(self.batch_text.text())
        elif kind == "worker_error":
            self._log(f"Batch error: {event.get('message', 'Unknown error')}")
            self.batch_text.setText("Batch stopped with an error. See Activity for details.")
            self.footer_status.setText("Batch error")
            if self._current_index is None:
                self.current_text.setText("No folder processing")
            self.current_file_text.setText("Batch stopped with an error. See Activity for details.")
            for index, status in list(self._statuses.items()):
                if status in ("Queued", "Processing"):
                    self._statuses[index] = "Failed" if status == "Processing" else "Not processed"
                    self._update_row(index)
            self._update_overview()
            self._announce(f"Batch stopped: {event.get('message', 'Unknown error')}")
        elif kind == "log":
            self._log(str(event.get("message", "")))

    @staticmethod
    def _set_progress(bar, completed, total):
        percentage = 100 * completed / total if total else 0.0
        bar.setValue(round(percentage))
        bar.setFormat(f"{percentage:.1f}%")

    def _set_current_counts(self, event):
        remaining = event.get("remaining", max(0, event.get("total", 0) - event.get("completed", 0)))
        self.current_counts.setText(f"Dewarped: {event.get('applied', 0):,} · Already corrected: {event.get('already', 0):,} · Skipped: {event.get('skipped', 0):,} · Failed: {event.get('failed', 0):,} · Remaining: {remaining:,}")

    def _update_row(self, index):
        stats = self._folder_stats.get(index, {})
        output = self.outputs.get(index)
        values = (self.folders[index].name or str(self.folders[index]), self._statuses.get(index, "Queued"), stats.get("completed", 0), stats.get("total", "—"), output.name if output else "Assigned at start")
        for table in (self.folder_table, self.progress_table):
            for column, value in enumerate(values):
                item = table.item(index, column)
                if item is None:
                    item = QTableWidgetItem()
                    table.setItem(index, column, item)
                item.setText(str(value))
                full_text = str(self.folders[index]) if column == 0 else str(output) if column == 4 and output else str(value)
                item.setToolTip(full_text)
                item.setData(Qt.ItemDataRole.AccessibleDescriptionRole, full_text)
            table.resizeRowToContents(index)

    def _update_overview(self):
        statuses = list(self._statuses.values())
        parts = [f"{statuses.count('Completed')} completed", f"{statuses.count('Queued')} queued"]
        for text, count in (("processing", statuses.count("Processing")), ("with issues", sum(status in ("Failed", "Completed with issues") for status in statuses)), ("not processed", statuses.count("Not processed")), ("cancelled", statuses.count("Cancelled")), ("empty", statuses.count("Empty"))):
            if count:
                parts.append(f"{count} {text}")
        self.overview_text.setText(" · ".join(parts))
        self.setup_summary.setText(f"{len(self.folders):,} folder(s) in this batch" if self.folders else "No folders added")

    def _update_batch_progress(self, final=None):
        fraction = self._batch_completed / self._batch_total if self._batch_total else 0.0
        self._set_progress(self.batch_bar, self._batch_completed, self._batch_total)
        if final == "Finished" and not self._batch_total:
            self.batch_text.setText("Finished · No JPEG photos found directly inside the listed folders.")
            self.footer_status.setText("No JPEG photos")
        elif self._cancel.is_set() and not final:
            self.batch_text.setText(f"Cancelling… {self._batch_completed:,} / {self._batch_total:,} photos processed ({fraction:.1%})")
            self.footer_status.setText("Cancelling…")
        else:
            self.batch_text.setText(f"{final or 'Batch'} · {fraction:.1%} · {self._batch_completed:,} / {self._batch_total:,} photos processed")
            # Terminal status words must still fit the fixed footer at large
            # text sizes; full counts remain in Live progress and Ctrl+I.
            self.footer_status.setText(f"{final} · {fraction:.0%}" if final else
                                       f"{self._batch_completed:,}/{self._batch_total:,} photos · {fraction:.0%}")

    def _update_timing(self, elapsed=None, *, finished=False):
        if self._batch_summary_received and not finished:
            return
        elapsed = max(0.0, elapsed if elapsed is not None else time.monotonic() - self._started_at)
        rate = self._batch_completed / elapsed if elapsed > 0 else 0
        rate_text = f"{rate * 60:.1f} photos/min" if self._batch_completed else "—"
        remaining = max(0, self._batch_total - self._batch_completed)
        eta = _duration(remaining / rate) if self._batch_completed >= 3 and rate and remaining else "—"
        self.time_label.setText(f"Elapsed: {_duration(elapsed)} · Rate: {rate_text} · ETA: {'—' if finished or self._cancel.is_set() else eta}")

    def _finish_worker(self):
        self._set_running(False)
        self._worker = None
        if not self._batch_summary_received:
            self._update_timing(finished=True)
        self._update_selection()
        if not self._close_when_finished:
            self.back_button.setFocus()

    def _log(self, message):
        self.log.appendPlainText(f"{time.strftime('%H:%M:%S')}  {message}")

    def _announce(self, message):
        event = QAccessibleAnnouncementEvent(self, message)
        event.setPoliteness(QAccessible.AnnouncementPoliteness.Polite)
        QAccessible.updateAccessibility(event)

    def _status_contents(self):
        lines = [self.batch_text.text(), self.overview_text.text(), self.current_text.text(), self.current_counts.text(), self.current_file_text.text(), self.time_label.text(), "", "Folders:"]
        for index, folder in enumerate(self.folders):
            stats = self._folder_stats.get(index, {})
            lines.append(f"{folder}\n  {self._statuses.get(index, 'Queued')}: {stats.get('completed', 0)} / {stats.get('total', 'not scanned')} photos")
            if stats.get("elapsed_seconds") is not None:
                lines.append(f"  Elapsed: {stats['elapsed_seconds']:.2f} seconds")
            if index in self.outputs:
                lines.append(f"  Output: {self.outputs[index]}")
        if self.log.toPlainText():
            lines.extend(("", "Activity:", self.log.toPlainText()))
        return "\n".join(lines)

    def _text_dialog(self, title, text):
        dialog = QDialog(self)
        dialog.setAttribute(Qt.WidgetAttribute.WA_WindowPropagation, True)
        dialog.setWindowTitle(title)
        dialog.setAccessibleName(title)
        dialog.setMinimumSize(280, 180)
        layout = QVBoxLayout(dialog)
        contents = _accessible(QPlainTextEdit(), f"{title} details", "Read-only text. Arrows and Page Up or Page Down navigate; Control+A and Control+C copy everything.")
        contents.setPlainText(text)
        contents.setReadOnly(True)
        layout.addWidget(contents)
        close = self._button("Close", f"Close {title.lower()}", dialog.accept)
        layout.addWidget(close)
        available = self.screen().availableGeometry()
        dialog.resize(min(650, available.width() - 40), min(550, available.height() - 40))
        contents.setFocus()
        try:
            dialog.exec()
        finally:
            dialog.deleteLater()

    def _show_status(self):
        self._text_dialog("Batch status", self._status_contents())

    def _show_help(self):
        self._text_dialog("Keyboard and processing help", """Keyboard shortcuts
Control+O: add an input folder.
Control+Shift+O: choose the output parent folder.
Control+Comma: open processing settings.
Control+Return or Alt+S: start the batch.
Escape: cancel an active batch; active photos finish safely.
Control+Shift+E: open the selected output folder.
Control+I: read a complete batch-status snapshot.
F1: open this help.
Control+Plus / Control+Minus: increase / decrease text size.
Control+0: reset text size to the system-font baseline.
Alt+F / Alt+V / Alt+H: File / View / Help menus.
Tab and Shift+Tab: move between controls; content scrolls to focused controls.
Return or Space: activate a focused button. Enter in a text field does not start a batch.
Arrow keys: navigate table cells or option values. Shift or Control selects table rows. Delete removes selected rows on the setup page.
Full input and output paths appear in table tooltips and the Batch status dialog (Control+I). The current folder and latest filename fields scroll horizontally: Home and End navigate, Control+A selects, and Control+C copies.
The activity log and status dialog support keyboard reading and copying.

Processing
Only JPEGs directly inside each input folder are processed. Add multiple folders or drop them into the window. A parent folder containing only subfolders is empty for processing; add its photo-containing subfolders separately. Start batch opens the progress page and processes every listed folder, including completed rows. Results remain visible after finishing or cancelling. Choose Back to setup to change the queue or run again; reruns create separate output folders. Blank output location saves inside each input folder. Named output folders receive a numeric suffix when needed.
Progress counts completed attempts; dewarped, already-corrected, skipped, and failed counts remain separate. Overall progress is weighted by photo count.
File > Settings stores your processing defaults for future runs. Preserve XMP requires ExifTool. Crop edges is the default framing; Wider view keeps more field of view. Both use a valid-region crop.
Two parallel photos is the default; one to four are available. Higher counts require more memory. Cancellation preserves completed output and lets active photos finish. Closing during a batch offers safe cancellation.
Each processed output folder includes processing-report.json with full results.

Display and accessibility
The window follows the operating system font, palette, and per-monitor scaling. View > Text size offers 100–200% text scaling. Main content scrolls when needed. Cancel stays on the left of the footer; Start or Back to setup stays on the right. At narrow widths the footer buttons stack. Table columns and paths scroll horizontally. Status words convey outcomes without relying on color.""")


def run_gui():
    application = QApplication.instance() or QApplication(sys.argv)
    application.setApplicationName("DJI Photo Dewarp")
    application.setOrganizationName("Dewarp M3E JPGs")
    window = DewarpApp()
    window.show()
    application.exec()


if __name__ == "__main__":
    run_gui()
