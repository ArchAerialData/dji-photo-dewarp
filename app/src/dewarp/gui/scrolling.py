"""Keyboard scrolling that includes a native control's complete styled frame."""
from PySide6.QtCore import QPoint, QRect
from PySide6.QtWidgets import QScrollArea


class ContentScrollArea(QScrollArea):
    def ensureWidgetVisible(self, widget, xmargin=50, ymargin=50):
        super().ensureWidgetVisible(widget, xmargin, ymargin)
        if widget is None or self.widget() is None or not self.widget().isAncestorOf(widget):
            return
        # Native editors can add padding outside the cursor rectangle Qt reveals.
        # Keep that frame and its focus outline inside the viewport as well.
        bounds = QRect(widget.mapTo(self.viewport(), QPoint()), widget.size())
        viewport = self.viewport().rect()
        for bar, start, end, extent, limit, requested_margin in (
            (self.horizontalScrollBar(), bounds.left(), bounds.right(), bounds.width(), viewport.width(), xmargin),
            (self.verticalScrollBar(), bounds.top(), bounds.bottom(), bounds.height(), viewport.height(), ymargin),
        ):
            if extent > limit:
                continue  # Large tables/logs retain Qt's normal partial reveal.
            margin = min(max(0, requested_margin), (limit - extent) // 2)
            if start < margin:
                bar.setValue(bar.value() + start - margin)
            elif end >= limit - margin:
                bar.setValue(bar.value() + end - limit + margin + 1)
