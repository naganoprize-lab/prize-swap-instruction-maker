"""Wrap compact toolbar controls while preserving their widgets and shortcuts."""

from PySide6.QtCore import Qt, QSize, QRect, QPoint
from PySide6.QtWidgets import QLayout


class ControlFlow(QLayout):
    def __init__(self, parent=None, spacing=4):
        super().__init__(parent)
        self.items = []
        self.setSpacing(spacing)

    def addItem(self, item):
        self.items.append(item)

    def count(self):
        return len(self.items)

    def itemAt(self, index):
        return self.items[index] if 0 <= index < len(self.items) else None

    def takeAt(self, index):
        return self.items.pop(index) if 0 <= index < len(self.items) else None

    def expandingDirections(self):
        return Qt.Orientation(0)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self.arrange(QRect(0, 0, width, 0), False)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        size = QSize()
        for item in self.items:
            if not item.isEmpty():
                size = size.expandedTo(item.minimumSize())
        left, top, right, bottom = self.getContentsMargins()
        return size + QSize(left + right, top + bottom)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self.arrange(rect, True)

    def arrange(self, rect, apply):
        left, top, right, bottom = self.getContentsMargins()
        area = rect.adjusted(left, top, -right, -bottom)
        x = area.x()
        y = area.y()
        height = 0
        for item in self.items:
            if item.isEmpty():
                continue
            hint = item.sizeHint()
            width = min(hint.width(), max(1, area.width()))
            if height and x + width > area.right() + 1:
                x = area.x()
                y += height + self.spacing()
                height = 0
            if apply:
                item.setGeometry(QRect(QPoint(x, y), QSize(width, hint.height())))
            x += width + self.spacing()
            height = max(height, hint.height())
        return y + height - rect.y() + bottom
