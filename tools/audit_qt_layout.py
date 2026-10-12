"""Exercise the native Qt layout across DPI, text size, window size, and state.

Run: python tools/audit_qt_layout.py --output docs/gui-screen-layout.json
Each DPI uses a fresh native Windows Qt process with QT_SCALE_FACTOR normalized
against the host's measured native DPR; no monitor settings or photos are changed.
Windows are mapped with zero opacity, so
the native layout/style runs without obscuring the user's work. Geometry audits
cannot certify Narrator behavior or real monitor-to-monitor transitions.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]


def run_child(quick=False, screenshots=None):
    sys.path.insert(0, str(ROOT / "app" / "src"))
    import tempfile
    from PySide6 import __version__ as qt_binding_version
    from PySide6.QtCore import QPoint, QRect, Qt
    from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QLabel,
        QLineEdit, QPushButton, QSpinBox, QStyle, QStyleOptionButton, QWidget)
    from dewarp.gui.window import DewarpApp
    from dewarp.gui.settings import ProcessingSettings, SettingsDialog, SettingsStore

    app = QApplication([])
    temporary = tempfile.TemporaryDirectory(prefix="dewarp-layout-settings-")
    window = DewarpApp(settings_store=SettingsStore(Path(temporary.name) / "settings.ini"))
    window._timer.stop()
    window.setWindowOpacity(0.0)
    window.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
    window.show()

    def pump():
        for _ in range(5):
            app.processEvents()

    pump()
    available = window.screen().availableGeometry()
    initial = window.frameGeometry()
    startup = {"frame": [initial.x(), initial.y(), initial.width(), initial.height()],
               "available_workarea": [available.x(), available.y(), available.width(), available.height()],
               "frame_fits_workarea": available.contains(initial)}

    def containment(widget):
        problems = []
        if not widget.isVisible():
            problems.append("not visible")
        ancestor = widget.parentWidget()
        while ancestor is not None:
            bounds = QRect(widget.mapTo(ancestor, QPoint()), widget.size())
            if not ancestor.rect().contains(bounds):
                problems.append(type(ancestor).__name__ + ":" + ",".join(
                    side for side, clipped in (("left", bounds.left() < 0), ("top", bounds.top() < 0),
                    ("right", bounds.right() >= ancestor.width()), ("bottom", bounds.bottom() >= ancestor.height())) if clipped))
            if ancestor.isWindow():
                break  # A modal window need not fit inside its transient parent.
            ancestor = ancestor.parentWidget()
        return {"visible": not problems, "clipped_by": problems}

    def button_text_fits(button):
        option = QStyleOptionButton()
        option.initFrom(button)
        option.text = button.text()
        content = button.style().subElementRect(QStyle.SubElement.SE_PushButtonContents, option, button)
        metrics = button.fontMetrics()
        label = button.text().replace("&&", "\0").replace("&", "").replace("\0", "&")
        needed = [metrics.horizontalAdvance(label), metrics.height()]
        return {"fits": needed[0] <= content.width() and needed[1] <= content.height(),
                "content_size": [content.width(), content.height()], "text_size": needed}

    def reveal(scroll, widget):
        if scroll.widget().isAncestorOf(widget):
            for _ in range(2):
                scroll.ensureWidgetVisible(widget, 8, 8)
                pump()

    def measure(owner, scroll, critical_names, table, state, scale, size):
        scroll.verticalScrollBar().setValue(0)
        scroll.horizontalScrollBar().setValue(0)
        pump()
        critical = {name: {**containment(getattr(owner, name)), **button_text_fits(getattr(owner, name))}
                    for name in critical_names}
        hidden, unnamed, labels = [], [], []
        for widget in owner.findChildren(QWidget):
            if not isinstance(widget, (QPushButton, QCheckBox, QComboBox, QSpinBox, QLineEdit)):
                continue
            if isinstance(widget.parentWidget(), (QSpinBox, QComboBox)) or not widget.isVisibleTo(owner):
                continue
            reveal(scroll, widget)
            result = containment(widget)
            if isinstance(widget, QPushButton):
                result.update(button_text_fits(widget))
            name = widget.accessibleName() or (widget.text() if hasattr(widget, "text") else widget.objectName())
            if not result["visible"] or not result.get("fits", True):
                hidden.append({"type": type(widget).__name__, "text": name[:100], **result})
            if not name:
                unnamed.append(type(widget).__name__)
        for label in owner.findChildren(QLabel):
            if not label.isVisibleTo(owner) or not label.text():
                continue
            reveal(scroll, label)
            content = label.contentsRect()
            flags = Qt.TextFlag.TextWordWrap if label.wordWrap() else Qt.TextFlag.TextSingleLine
            text = label.text()
            if label.buddy() is not None:
                text = text.replace("&&", "\0").replace("&", "").replace("\0", "&")
            needed = label.fontMetrics().boundingRect(QRect(0, 0, content.width(), 100000), flags, text)
            horizontal = any("left" in side or "right" in side for side in containment(label)["clipped_by"])
            if needed.width() > content.width() or needed.height() > content.height() or horizontal:
                labels.append({"text": label.text()[:100], "content_size": [content.width(), content.height()],
                               "needed_size": [needed.width(), needed.height()], "horizontal_clipping": horizontal})
        scroll.verticalScrollBar().setValue(scroll.verticalScrollBar().maximum())
        pump()
        critical_bottom = {name: containment(getattr(owner, name)) for name in critical_names}
        hint = owner.minimumSizeHint()
        normal = state != "settings" and scale == 100 and owner.width() >= 1100 and owner.height() >= 760
        cancel, action = (getattr(owner, name) for name in critical_names)
        cancel_pos, action_pos = cancel.mapTo(owner, QPoint()), action.mapTo(owner, QPoint())
        same_row = max(cancel_pos.y(), action_pos.y()) < min(cancel_pos.y() + cancel.height(), action_pos.y() + action.height())
        footer_order_correct = (cancel_pos.x() < action_pos.x() if same_row
                                else cancel_pos.y() < action_pos.y())
        row_height = table.rowHeight(0) if table is not None and table.rowCount() else None
        font_height = table.fontMetrics().height() if table is not None else None
        case = {"qt_scale_factor": os.environ.get("QT_SCALE_FACTOR", "1"),
                "target_device_pixel_ratio": float(os.environ.get("DEWARP_AUDIT_TARGET_DPR", "1")),
                "device_pixel_ratio": owner.devicePixelRatioF(), "text_scale_percent": scale,
                "minimum_size_hint": [hint.width(), hint.height()],
                "minimum_hint_fits_workarea": hint.width() <= available.width() and hint.height() <= available.height(),
                "requested_logical_client": list(size), "actual_logical_client": [owner.width(), owner.height()],
                "state": state, "critical_top": critical, "critical_bottom": critical_bottom,
                "footer_order_correct": footer_order_correct,
                "unreachable_actions": hidden, "actions_without_explicit_accessible_names": unnamed,
                "label_issues": labels, "queue_row_height": row_height, "queue_font_height": font_height,
                "queue_text_fits": row_height is None or row_height >= font_height + 4,
                "horizontal_scroll_maximum": scroll.horizontalScrollBar().maximum(),
                "vertical_scroll_maximum": scroll.verticalScrollBar().maximum(),
                "normal_size_requires_no_outer_scroll": normal,
                "normal_size_has_no_outer_scroll": not normal or (scroll.verticalScrollBar().maximum() == 0 and scroll.horizontalScrollBar().maximum() == 0)}
        if state == "setup":
            case["page_contract_satisfied"] = window.pages.currentIndex() == 0 and not window.back_button.isVisible()
        elif state in ("running", "completed"):
            case["page_contract_satisfied"] = (window.pages.currentIndex() == 1 and not window.start_button.isVisible()
                and window.back_button.isEnabled() == (state == "completed")
                and window.cancel_button.isEnabled() == (state == "running"))
        else:
            case["page_contract_satisfied"] = owner.isModal()
        if state == "running":
            case["fixture_state_consistent"] = (
                window._batch_completed == 37 and window._batch_total == 100
                and window._statuses == {0: "Processing", 1: "Queued"}
                and window._folder_stats[0].get("completed") == 37
                and window._folder_stats[1].get("completed") == 0
                and window.current_bar.value() == 74 and window.batch_bar.value() == 37
                and "37/100" in window.footer_status.text())
            case["progress_snapshot"] = {"batch_completed": window._batch_completed,
                "batch_total": window._batch_total, "statuses": dict(window._statuses),
                "current_percent": window.current_bar.value(), "batch_percent": window.batch_bar.value(),
                "batch_text": window.batch_text.text(), "footer_text": window.footer_status.text()}
        elif state == "completed":
            case["fixture_state_consistent"] = (window._batch_completed == 100 and window._batch_total == 100
                and window._statuses == {0: "Completed", 1: "Completed"} and window.batch_bar.value() == 100)
        else:
            case["fixture_state_consistent"] = True
        capture = {
            ("1", 100, (1100, 820), "setup"): "normal-1100x820.png",
            ("1", 100, (1100, 820), "running"): "normal-running-progress-1100x820.png",
            ("1", 100, (1100, 820), "completed"): "normal-completed-1100x820.png",
            ("1", 200, (480, 360), "running"): "small-480x360-text-200.png",
            ("2", 150, (640, 480), "running"): "dpi-200-small-640x480-text-150.png",
            ("1", 100, (640, 480), "settings"): "settings-640x480.png",
        }.get((os.environ.get("DEWARP_AUDIT_TARGET_DPR", "1"), scale, size, state))
        if screenshots is not None and capture:
            scroll.verticalScrollBar().setValue(0)
            scroll.horizontalScrollBar().setValue(0)
            pump()
            screenshots.mkdir(parents=True, exist_ok=True)
            target = screenshots / capture
            if not owner.grab().save(str(target)):
                raise RuntimeError(f"Could not save app-only capture: {target}")
            case["app_render_capture"] = str(target)
        return case

    # Harmless existing directories seed rows only; no photo processing occurs.
    window.add_folders([ROOT, ROOT / "tools"])

    def reset_fixture():
        # Each measurement must be independent of earlier completed batches,
        # including visible text, reports, queue rows, and the activity history.
        window._set_running(False)
        window._cancel.clear()
        window._batch_completed = window._batch_total = 0
        window._batch_summary_received = False
        window._current_index = None
        window._folder_stats.clear()
        window.outputs.clear()
        window._statuses = {index: "Queued" for index in range(len(window.folders))}
        window.log.clear()
        window.current_text.setText("No folder processing")
        window.current_file_text.clear()
        window._set_current_counts({})
        window._set_progress(window.current_bar, 0, 0)
        window._update_batch_progress()
        window.time_label.setText("Elapsed: 0:00 · Rate: — · ETA: —")
        window.footer_status.setText("Ready")
        for index in range(len(window.folders)):
            window._update_row(index)
        window._update_overview()

    declared_minimum = [window.minimumWidth(), window.minimumHeight()]
    window.setMinimumSize(0, 0)
    sizes = ((320, 240), (640, 480), (1100, 820)) if quick else (
        (320, 240), (480, 360), (640, 480), (800, 600), (1024, 768), (1100, 820))
    scales = (100, 200) if quick else (100, 125, 150, 200)
    cases = []
    for scale in scales:
        window._set_text_scale(scale)
        pump()
        for size in sizes:
            window.resize(*size)
            for state in ("setup", "running", "completed"):
                reset_fixture()
                if state != "setup":
                    window.pages.setCurrentWidget(window.progress_scroll)
                window._set_running(state != "setup")
                if state == "setup":
                    window._show_setup()
                else:
                    window._handle_event({"event": "batch_start", "folders_total": 2, "total": 100, "workers": 2})
                    for index, folder in enumerate(window.folders):
                        window._handle_event({"event": "folder_queued", "index": index, "folder": str(folder),
                            "status": "Queued", "total": 50, "completed": 0, "applied": 0,
                            "output": "", "already": 0, "skipped": 0, "failed": 0})
                    window._handle_event({"event": "folder_start", "index": 0, "folder": str(ROOT), "total": 50,
                        "completed": 0, "output": str(Path(temporary.name) / "unused-audit-output")})
                    window._handle_event({"event": "photo_done", "index": 0, "name": "DJI_20261010153000_1000000_A.JPG",
                        "status": "applied", "total": 50, "completed": 37, "applied": 37, "remaining": 13,
                        "batch_total": 100, "batch_completed": 37, "already": 0, "skipped": 0, "failed": 0,
                        "warnings": []})
                    window._update_timing(12)
                    if state == "completed":
                        for index in range(2):
                            if index == 1:
                                window._handle_event({"event": "folder_start", "index": 1, "folder": str(window.folders[1]),
                                    "total": 50, "completed": 0, "output": str(Path(temporary.name) / "unused-audit-output-2")})
                                window._handle_event({"event": "photo_done", "index": 1, "name": "DJI_20261010154000_1000050_A.JPG",
                                    "status": "applied", "total": 50, "completed": 50, "applied": 50, "remaining": 0,
                                    "batch_total": 100, "batch_completed": 100, "warnings": []})
                            window._handle_event({"event": "folder_done", "index": index,
                                                  "status": "Completed", "total": 50, "completed": 50, "applied": 50})
                        window._handle_event({"event": "batch_done", "total": 100, "completed": 100,
                                              "applied": 100, "elapsed": 32, "cancelled": False, "warnings": 0})
                        window._set_running(False)
                pump()
                scroll = window.setup_scroll if state == "setup" else window.progress_scroll
                buttons = ("cancel_button", "start_button") if state == "setup" else ("cancel_button", "back_button")
                table = window.folder_table if state == "setup" else window.progress_table
                cases.append(measure(window, scroll, buttons, table, state, scale, size))
            dialog = SettingsDialog(ProcessingSettings(), window)
            dialog.setWindowOpacity(0.0)
            dialog.setMinimumSize(0, 0)
            dialog.resize(*size)
            dialog.show()
            pump()
            cases.append(measure(dialog, dialog.scroll_area, ("cancel_button", "save_button"), None, "settings", scale, size))
            dialog.reject()
            dialog.deleteLater()
            pump()
    window._set_running(False)
    window.close()
    pump()
    result = {"environment": {"qt_binding": qt_binding_version,
        "platform_plugin": app.platformName(), "style": app.style().objectName(),
        "screen_dpr": app.primaryScreen().devicePixelRatio(), "logical_dpi": app.primaryScreen().logicalDotsPerInch(),
        "declared_minimum": declared_minimum, "startup_geometry": startup}, "cases": cases}
    temporary.cleanup()
    print(json.dumps(result))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--child", action="store_true")
    parser.add_argument("--native-probe", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--quick", action="store_true")
    parser.add_argument("--screenshots", type=Path, help="Optional folder for app-only setup/progress/settings captures")
    args = parser.parse_args()
    if args.native_probe:
        from PySide6.QtWidgets import QApplication
        app = QApplication([])
        print(json.dumps({"native_dpr": app.primaryScreen().devicePixelRatio()}))
        return 0
    if args.child:
        run_child(args.quick, args.screenshots)
        return 0
    if args.output is None:
        parser.error("--output is required")
    cases = []
    environments = []
    probe_env = os.environ.copy()
    probe_env.pop("QT_SCALE_FACTOR", None)
    probe_env.pop("QT_SCREEN_SCALE_FACTORS", None)
    native_probe = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--native-probe"],
        env=probe_env, capture_output=True, text=True, encoding="utf-8", timeout=30, check=True)
    native_dpr = json.loads(native_probe.stdout)["native_dpr"]
    targets = (1, 2, 3) if args.quick else (1, 1.25, 1.5, 2, 2.5, 3)
    for target in targets:
        env = os.environ.copy()
        env.pop("QT_SCREEN_SCALE_FACTORS", None)
        factor = target / native_dpr
        env["QT_SCALE_FACTOR"] = f"{factor:.8g}"
        env["DEWARP_AUDIT_TARGET_DPR"] = str(target)
        command = [sys.executable, str(Path(__file__).resolve()), "--child"]
        if args.quick:
            command.append("--quick")
        if args.screenshots is not None:
            command.extend(("--screenshots", str(args.screenshots.resolve())))
        result = subprocess.run(command, env=env, capture_output=True, text=True,
                                encoding="utf-8", timeout=180, check=False)
        if result.returncode:
            raise RuntimeError(f"Qt scale {factor} audit failed:\n{result.stderr}\n{result.stdout[:1000]}")
        data = json.loads(result.stdout)
        environments.append(data["environment"])
        cases.extend(data["cases"])
    summary = {"cases": len(cases),
        "cases_with_critical_visibility_failures": sum(any(not v["visible"] for position in
            ("critical_top", "critical_bottom") for v in case[position].values()) for case in cases),
        "cases_with_clipped_critical_button_text": sum(any(not v["fits"] for v in case["critical_top"].values()) for case in cases),
        "cases_with_unreachable_actions": sum(bool(case["unreachable_actions"]) for case in cases),
        "cases_with_label_issues": sum(bool(case["label_issues"]) for case in cases),
        "cases_with_short_queue_rows": sum(not case["queue_text_fits"] for case in cases),
        "cases_with_page_contract_failures": sum(not case["page_contract_satisfied"] for case in cases),
        "cases_with_incorrect_footer_order": sum(not case["footer_order_correct"] for case in cases),
        "cases_with_inconsistent_fixture": sum(not case["fixture_state_consistent"] for case in cases),
        "normal_size_cases_with_outer_scroll": sum(not case["normal_size_has_no_outer_scroll"] for case in cases),
        "processes_with_startup_frame_outside_workarea": sum(not environment["startup_geometry"]["frame_fits_workarea"] for environment in environments),
        "cases_with_minimum_hint_exceeding_workarea": sum(not case["minimum_hint_fits_workarea"] for case in cases),
        "cases_with_client_size_changed_by_window_manager": sum(case["requested_logical_client"] != case["actual_logical_client"] for case in cases)}
    payload = {"method": "Independent native Qt processes for QT_SCALE_FACTOR; transparent mapped windows; all-ancestor widget clipping, scrolling reachability, style-aware primary button text rectangles, label font metrics, queue row metrics.",
        "limitations": ["Synthetic Qt scaling does not verify physical monitor transitions.",
            "Window minimum overridden to test constrained available space.",
            "No Narrator or Windows high-contrast certification from geometry alone."],
        "source_sha256": hashlib.sha256((ROOT / "app/src/dewarp/gui/window.py").read_bytes()).hexdigest(),
        "settings_source_sha256": hashlib.sha256((ROOT / "app/src/dewarp/gui/settings.py").read_bytes()).hexdigest(),
        "scrolling_source_sha256": hashlib.sha256((ROOT / "app/src/dewarp/gui/scrolling.py").read_bytes()).hexdigest(),
        "theme_source_sha256": hashlib.sha256((ROOT / "app/src/dewarp/gui/theme.py").read_bytes()).hexdigest(),
        "native_device_pixel_ratio": native_dpr,
        "python": platform.python_version(), "platform": platform.platform(),
        "environments": environments, "summary": summary, "cases": cases}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2))
    return int(any(summary[key] for key in ("cases_with_critical_visibility_failures",
        "cases_with_clipped_critical_button_text", "cases_with_unreachable_actions", "cases_with_short_queue_rows",
        "cases_with_label_issues", "cases_with_page_contract_failures", "cases_with_incorrect_footer_order", "cases_with_inconsistent_fixture", "normal_size_cases_with_outer_scroll", "processes_with_startup_frame_outside_workarea",
        "cases_with_minimum_hint_exceeding_workarea")))


if __name__ == "__main__":
    raise SystemExit(main())
