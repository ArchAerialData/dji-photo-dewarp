"""Check terminal-state footer geometry without processing any photos.

Uses an invisible mapped native Qt window. Run in a bounded validation child.
"""
import hashlib
import json
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "app" / "src"))

from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtWidgets import QApplication, QStyle, QStyleOptionButton
from dewarp.gui.window import DewarpApp
from dewarp.gui.settings import SettingsStore


def main():
    app = QApplication([])
    temporary = tempfile.TemporaryDirectory(prefix="dewarp-terminal-settings-")
    window = DewarpApp(settings_store=SettingsStore(Path(temporary.name) / "settings.json"))
    window._timer.stop()
    window.setWindowOpacity(0.0)
    window.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
    window.show()
    window.add_folders([ROOT])

    def pump():
        for _ in range(5):
            app.processEvents()

    def contained(widget):
        parent = widget.parentWidget()
        while parent is not None:
            rect = QRect(widget.mapTo(parent, QPoint()), widget.size())
            if not parent.rect().contains(rect):
                return False
            parent = parent.parentWidget()
        return widget.isVisible()

    pump()
    cases = []

    def measure_footer(state, scale, size, scroll, page, controls):
        buttons = {}
        for button in controls:
            option = QStyleOptionButton()
            option.initFrom(button)
            option.text = button.text()
            content = button.style().subElementRect(QStyle.SubElement.SE_PushButtonContents, option, button)
            metrics = button.fontMetrics()
            text = button.text().replace("&&", "\0").replace("&", "").replace("\0", "&")
            buttons[button.text()] = {"contained": contained(button),
                "text_fits": metrics.horizontalAdvance(text) <= content.width() and metrics.height() <= content.height()}
        label = window.footer_status
        needed = label.fontMetrics().boundingRect(QRect(0, 0, label.contentsRect().width(), 100000),
                                                  Qt.TextFlag.TextWordWrap, label.text())
        contract = window.pages.currentIndex() == page and not window.cancel_button.isEnabled()
        contract = contract and (window.start_button.isEnabled() if page == 0 else window.back_button.isEnabled())
        return {"state": state, "text_scale": scale, "requested_size": size,
                "actual_size": [window.width(), window.height()], "scroll": scroll,
                "footer_text": label.text(), "footer_label_contained": contained(label),
                "page": page, "page_contract_satisfied": contract,
                "footer_label_text_fits": needed.width() <= label.contentsRect().width() and needed.height() <= label.contentsRect().height(),
                "buttons": buttons}

    for scale in (100, 200):
        window._set_text_scale(scale)
        for size in ((320, 240), (480, 360)):
            window.resize(*size)
            for state in ("completed", "issues", "error", "cancelled", "empty"):
                window._cancel.clear()
                window._statuses = {0: "Empty" if state == "empty" else "Completed"}
                window.pages.setCurrentWidget(window.progress_scroll)
                window._set_running(True)
                if state == "error":
                    window._handle_event({"event": "worker_error", "message": "Synthetic error"})
                else:
                    window._handle_event({"event": "batch_done", "total": 0 if state == "empty" else 10_000_000,
                        "completed": 0 if state == "empty" else 10_000_000, "elapsed": 1,
                        "cancelled": state == "cancelled", "warnings": 1 if state == "issues" else 0})
                window._set_running(False)
                pump()
                for scroll in (0, window.progress_scroll.verticalScrollBar().maximum()):
                    window.progress_scroll.verticalScrollBar().setValue(scroll)
                    pump()
                    cases.append(measure_footer(state, scale, size, scroll, 1, (window.cancel_button, window.back_button)))
                window.back_button.click()
                pump()
                cases[-1]["return_to_setup"] = (window.pages.currentIndex() == 0
                    and window.start_button.isVisible() and window.start_button.isEnabled())
            # Exercise actual add-folder feedback with one new, one duplicate,
            # and one unavailable folder. No strings are patched into the UI.
            window._clear_queue()
            window.log.clear()
            window.add_folders([ROOT])
            window.add_folders([ROOT / "tools", ROOT, Path(temporary.name) / "missing-input"])
            pump()
            mixed_event_verified = (len(window.folders) == 2 and "Already queued" in window.log.toPlainText()
                                    and "Not a folder" in window.log.toPlainText())
            for scroll in (0, window.setup_scroll.verticalScrollBar().maximum()):
                window.setup_scroll.verticalScrollBar().setValue(scroll)
                pump()
                case = measure_footer("setup-mixed-feedback", scale, size, scroll, 0,
                                      (window.cancel_button, window.start_button))
                case["mixed_add_events_verified"] = mixed_event_verified
                cases.append(case)
            window._clear_queue()
            window.add_folders([ROOT])
    window.close()
    pump()
    failures = [case for case in cases if not case["footer_label_contained"] or not case["footer_label_text_fits"]
        or not case["page_contract_satisfied"] or not case.get("mixed_add_events_verified", True)
        or not case.get("return_to_setup", True)
        or any(not result["contained"] or not result["text_fits"] for result in case["buttons"].values())]
    report = {"source_sha256": hashlib.sha256((ROOT / "app/src/dewarp/gui/window.py").read_bytes()).hexdigest(),
        "settings_source_sha256": hashlib.sha256((ROOT / "app/src/dewarp/gui/settings.py").read_bytes()).hexdigest(),
        "scrolling_source_sha256": hashlib.sha256((ROOT / "app/src/dewarp/gui/scrolling.py").read_bytes()).hexdigest(),
        "theme_source_sha256": hashlib.sha256((ROOT / "app/src/dewarp/gui/theme.py").read_bytes()).hexdigest(),
        "device_pixel_ratio": window.devicePixelRatioF(), "cases": cases, "failed_cases": len(failures)}
    (ROOT / "docs/gui-screen-terminal-states.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"cases": len(cases), "failed_cases": len(failures), "failures": failures}))
    temporary.cleanup()
    return int(bool(failures))


if __name__ == "__main__":
    raise SystemExit(main())
