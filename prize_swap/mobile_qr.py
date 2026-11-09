"""Separate QR-label renderer. Existing instruction print geometry is untouched."""

from pathlib import Path
import segno
from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QImage, QPageSize, QPageLayout
from PySide6.QtPrintSupport import QPrinter
from .core import AppError
from .mobile_package import valid_id
from .machine_matching import machine_key


def qr_uri(machine_id):
    if not valid_id(machine_id):
        raise AppError("マシンIDが不正です。")
    return "prizeswap://machine/v1/" + machine_id


def parse_qr(value):
    prefix = "prizeswap://machine/v1/"
    if (
        not isinstance(value, str)
        or not value.startswith(prefix)
        or not valid_id(value[len(prefix) :])
    ):
        raise AppError("対応していないQRコードです。")
    return value[len(prefix) :]


def draw_qr(painter, rect, machine_id):
    matrix = list(
        segno.make(qr_uri(machine_id), error="m", micro=False).matrix_iter(
            scale=1, border=4
        )
    )
    side = min(rect.width(), rect.height())
    unit = side / len(matrix)
    painter.fillRect(rect, QColor("white"))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor("black"))
    for y, row in enumerate(matrix):
        for x, value in enumerate(row):
            if value:
                painter.drawRect(
                    QRectF(rect.x() + x * unit, rect.y() + y * unit, unit, unit)
                )


def label(painter, rect, machine):
    side = min(rect.width() * 0.57, rect.height() - 8)
    draw_qr(painter, QRectF(rect.x() + 4, rect.y() + 4, side, side), machine.uid)
    painter.setPen(QColor("black"))
    f = painter.font()
    f.setPixelSize(max(9, int(rect.height() / 9)))
    painter.setFont(f)
    painter.drawText(
        QRectF(
            rect.x() + side + 8,
            rect.y() + 4,
            rect.width() - side - 12,
            rect.height() - 8,
        ),
        Qt.AlignmentFlag.AlignLeft
        | Qt.AlignmentFlag.AlignVCenter
        | Qt.TextFlag.TextWordWrap,
        (machine_key(machine) or machine.source_key or "番号未設定")
        + "\n"
        + machine.source_key
        + "\n"
        + machine.kind,
    )


def qr_png(path, machine, width_mm=65, height_mm=35):
    dpi = 300
    image = QImage(
        round(width_mm / 25.4 * dpi),
        round(height_mm / 25.4 * dpi),
        QImage.Format.Format_RGB32,
    )
    image.fill(QColor("white"))
    p = QPainter(image)
    try:
        label(p, QRectF(image.rect()), machine)
    finally:
        p.end()
    if not image.save(str(path), "PNG"):
        raise AppError("PNGを保存できません。")


def render_labels(printer, machines, width_mm=65, height_mm=35):
    if not machines:
        raise AppError("QRラベルの対象がありません。")
    printer.setPageSize(QPageSize(QPageSize.PageSizeId.A4))
    printer.setPageOrientation(QPageLayout.Orientation.Portrait)
    painter = QPainter()
    if not painter.begin(printer):
        raise AppError("QRラベルの描画を開始できません。")
    try:
        area = printer.pageLayout().paintRectPixels(printer.resolution())
        w = width_mm / 25.4 * printer.resolution()
        h = height_mm / 25.4 * printer.resolution()
        cols = int(area.width() / w)
        rows = int(area.height() / h)
        if cols < 1 or rows < 1:
            raise AppError("ラベルサイズがA4の印刷領域に収まりません。")
        for i, m in enumerate(machines):
            if i and i % (cols * rows) == 0:
                printer.newPage()
            n = i % (cols * rows)
            label(painter, QRectF(n % cols * w, n // cols * h, w, h), m)
    finally:
        painter.end()
