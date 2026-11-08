"""Font-independent numbered circles, also for booth numbers above twenty."""

from PySide6.QtCore import Qt, QRectF
from PySide6.QtGui import QColor, QPen, QFontMetricsF
from .fonts import print_font


def draw_badge(painter, rect, number, fill="white", ink="#111827"):
    painter.save()
    painter.setRenderHint(painter.RenderHint.Antialiasing)
    painter.setPen(QPen(QColor(ink), max(0.6, rect.width() * 0.035)))
    painter.setBrush(QColor(fill))
    painter.drawEllipse(rect)
    size = rect.height() * 0.57
    font = print_font(size)
    while (
        size > 3
        and QFontMetricsF(font, painter.device()).horizontalAdvance(str(number))
        > rect.width() * 0.83
    ):
        size -= 0.3
        font = print_font(size)
    painter.setFont(font)
    painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, str(number))
    painter.restore()
