"""Screen-only low-cost cards. Financial evaluation and print drawing are shared."""

from PySide6.QtCore import Qt, QPointF
from PySide6.QtGui import QColor, QPen, QStaticText
from .machine_matching import display_identity
from .theme import analysis_color, contrast_text


def paint_light(node, painter, background, priority):
    rect = node.boundingRect()
    painter.save()
    painter.setRenderHints(painter.renderHints(), False)
    fill = analysis_color(background)
    painter.setBrush(QColor(fill))
    painter.setPen(QPen(QColor("#94A3B8"), 1))
    painter.drawRect(rect.adjusted(1, 1, -1, -1))
    painter.setClipRect(rect.adjusted(4, 4, -4, -4))
    font = painter.font()
    font.setPixelSize(max(10, min(22, round(rect.height() * 0.15))))
    font.setBold(True)
    painter.setFont(font)
    text = display_identity(node.machine) or "番号未設定"
    key = (text, font.toString())
    if getattr(node, "_light_text_key", None) != key:
        node._light_text_key = key
        node._light_text = QStaticText(text)
        node._light_text.prepare(font=font)
    painter.setPen(QColor(contrast_text(fill)))
    painter.drawStaticText(QPointF(7, 7), node._light_text)
    painter.setClipping(False)
    if priority:
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor("#D49A00"), 3))
        painter.drawRect(rect.adjusted(4, 4, -4, -4))
    if (
        getattr(node, "search_match", False)
        or node.machine.uid in (node.owner.analysis_highlight_uids or set())
        or node.machine.uid in node.owner._highlight_uids
    ):
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor("#00BCD4"), 4))
        painter.drawRect(rect.adjusted(2, 2, -2, -2))
    painter.restore()
