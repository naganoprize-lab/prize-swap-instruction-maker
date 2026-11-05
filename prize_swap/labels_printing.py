"""Measured, vector-text booth labels using the existing printing lifecycle."""
from dataclasses import dataclass

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QImage, QPageLayout, QPen
from PySide6.QtPrintSupport import QPrinter

from .core import AppError
from .fonts import print_font
from .labels_data import LabelOptions, expanded_labels
from .printing import configured_printer, render_pages, save_rendered_pdf, text_block

PADDING = 8.0
MIN_AUTO_SIZE = 8
MAX_AUTO_SIZE = 256


@dataclass
class LabelCell:
    name: str
    rect: QRectF
    block: object
    font_size: int


@dataclass
class LabelPlan:
    pages: list
    width: float
    height: float
    options: LabelOptions
    warnings: list[str]
    device: QImage


def label_printer(path=None):
    printer = configured_printer(path, QPageLayout.Orientation.Portrait)
    printer.setDocName("入荷ブース用ラベル")
    return printer


def fitted_text(name, width, height, options, device):
    alignment = Qt.AlignmentFlag.AlignHCenter if options.alignment == "center" else Qt.AlignmentFlag.AlignLeft
    def measure(size):
        font = print_font(size)
        font.setWeight(QFont.Weight.Black if options.weight == "black" else QFont.Weight.Bold)
        metrics = QFontMetricsF(font, device)
        block = text_block(name, width, font, device, alignment)
        fits = metrics.height() <= height and block.height <= height
        for i in range(block.layout.lineCount()):
            line = block.layout.lineAt(i)
            fits &= line.naturalTextWidth() <= width + .001 and line.y() + line.height() <= height + .001
        return block, bool(fits)
    if options.mode == "manual":
        block, fits = measure(options.font_size)
        if not fits:
            raise AppError(f"「{name[:40]}」が手動{options.font_size}ptでは枠に収まりません。自動サイズにするか文字を小さくしてください。")
        return block, options.font_size
    low, high = MIN_AUTO_SIZE, MAX_AUTO_SIZE
    best = None
    while low <= high:
        size = (low + high) // 2
        block, fits = measure(size)
        if fits:
            best = block, size
            low = size + 1
        else:
            high = size - 1
    if best is None:
        raise AppError(f"「{name[:40]}」が長すぎて8ptでも枠に収まりません。商品名を調整するか1ページの枚数を減らしてください。省略して印刷することはありません。")
    return best


def plan_labels(entries, width, height, options=None):
    options = (options or LabelOptions()).validate()
    names = expanded_labels(entries)
    if not names:
        raise AppError("選択日に印刷対象のラベルがありません。")
    device = QImage(1, 1, QImage.Format.Format_ARGB32)
    device.setDotsPerMeterX(2835)
    device.setDotsPerMeterY(2835)
    row_height = (height - options.border) / options.per_page
    inner_width = width - options.border - PADDING * 2
    inner_height = row_height - PADDING * 2
    if min(inner_width, inner_height) <= 0:
        raise AppError("ラベルの印刷可能領域が小さすぎます。")
    pages, warnings, cache = [], [], {}
    for i, name in enumerate(names):
        if not isinstance(name, str) or not name.strip():
            raise AppError("ラベルの商品名は必須です。")
        if i % options.per_page == 0:
            pages.append([])
        if name not in cache:
            cache[name] = fitted_text(name, inner_width, inner_height, options, device)
            if len(name) > 300 or cache[name][1] < 12:
                warnings.append(f"「{name[:40]}」：長い商品名のため{cache[name][1]}ptで印刷します。")
        block, size = cache[name]
        rect = QRectF(options.border / 2, options.border / 2 + (i % options.per_page) * row_height, width - options.border, row_height)
        pages[-1].append(LabelCell(name, rect, block, size))
    return LabelPlan(pages, width, height, options, warnings, device)


def draw_labels_page(painter, plan, index):
    painter.save()
    painter.setPen(QPen(QColor("black"), plan.options.border))
    for cell in plan.pages[index]:
        painter.fillRect(cell.rect, QColor("white"))
        painter.drawRect(cell.rect)
        origin = QPointF(cell.rect.left() + PADDING, cell.rect.top() + (cell.rect.height() - cell.block.height) / 2)
        cell.block.draw(painter, origin)
    painter.restore()


def render_labels(printer, entries, options=None):
    area = printer.pageRect(QPrinter.Unit.Point)
    plan = plan_labels(entries, area.width(), area.height(), options)
    render_pages(printer, len(plan.pages), lambda painter, index: draw_labels_page(painter, plan, index))
    return plan


def save_labels_pdf(path, entries, options=None, sources=()):
    return save_rendered_pdf(path, lambda temp: render_labels(label_printer(temp), entries, options), sources)
