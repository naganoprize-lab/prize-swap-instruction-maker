"""Point-based shared layout and QPainter rendering for printer, preview and PDF."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from tempfile import NamedTemporaryFile

from PySide6.QtCore import QMarginsF, QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontDatabase, QImage, QPageLayout, QPageSize, QPainter, QPen, QTextLayout, QTextOption
from PySide6.QtPrintSupport import QPrinter

from .core import AppError, ordered
from .exporter import LABELS, product_values

PADDING = 4.0
GAP = 8.0
TITLE_HEIGHT = 32.0
FOOTER_HEIGHT = 20.0
WEIGHTS = (.14, .13, .37, .075, .065, .06, .16)


def print_font(size=11, bold=True):
    families = QFontDatabase.families()
    # Latin-first fallback avoids ambiguous digit glyphs in CJK PDF cmaps.
    family = next((f for f in ("Arial", "Liberation Sans", "DejaVu Sans", "Yu Gothic", "Noto Sans CJK JP", "Meiryo") if f in families), "sans-serif")
    font = QFont(family)
    font.setPixelSize(size)  # Logical drawing units are points, independent of printer DPI.
    font.setWeight(QFont.Weight.DemiBold if bold else QFont.Weight.Normal)
    return font


def configured_printer(pdf_path=None):
    printer = QPrinter(QPrinter.PrinterMode.HighResolution)
    printer.setResolution(300)
    printer.setPageLayout(QPageLayout(QPageSize(QPageSize.PageSizeId.A4), QPageLayout.Orientation.Landscape, QMarginsF(6, 6, 6, 6), QPageLayout.Unit.Millimeter))
    printer.setDocName("プライズ景品 入替指示書")
    if pdf_path is not None:
        printer.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
        printer.setOutputFileName(str(pdf_path))
    return printer


@dataclass
class TextBlock:
    text: str
    layout: QTextLayout
    width: float
    height: float

    def draw(self, painter, origin):
        self.layout.draw(painter, origin)


@dataclass
class PrintRow:
    left: object | None
    right: object | None
    height: float
    cells: list[list[TextBlock]]


@dataclass
class PrintPlan:
    pages: list[list[PrintRow]]
    width: float
    height: float
    widths: list[float]
    headers: list[TextBlock]
    header_height: float
    font: QFont
    # Retain the metrics device for the lifetime of QTextLayouts.
    metrics_device: QImage

    @property
    def body_height(self):
        return self.height - TITLE_HEIGHT - self.header_height - FOOTER_HEIGHT


def text_block(text, width, font, device):
    # QTextLayout doesn't treat a raw newline as a paragraph break; U+2028 does.
    text = str(text).replace("\r\n", "\n").replace("\r", "\n")
    layout = QTextLayout(text.replace("\n", "\u2028"), font, device)
    option = QTextOption()
    option.setWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
    layout.setTextOption(option)
    layout.beginLayout()
    height = 0.0
    while True:
        line = layout.createLine()
        if not line.isValid():
            break
        line.setLineWidth(max(1, width))
        line.setPosition(QPointF(0, height))
        height += line.height()
    layout.endLayout()
    return TextBlock(text, layout, width, height)


def plan_pages(products, page_width, page_height, rows=20, layout="column"):
    if not 1 <= rows <= 50 or layout not in ("column", "row"):
        raise AppError("ページ配置の設定が不正です。")
    font = print_font()
    device = QImage(1, 1, QImage.Format.Format_ARGB32)
    device.setDotsPerMeterX(2835)
    device.setDotsPerMeterY(2835)
    widths = [(page_width - GAP) / 2 * w for w in WEIGHTS]
    headers = [text_block(label, w - 2 * PADDING, font, device) for label, w in zip(LABELS, widths)]
    header_height = max(b.height for b in headers) + 2 * PADDING
    plan = PrintPlan([], page_width, page_height, widths, headers, header_height, font, device)
    if plan.body_height < 26 or min(widths) <= 2 * PADDING:
        raise AppError("印刷可能領域が小さすぎます。A4横向き・余白6mmを選択してください。")
    data = ordered(products)
    cached = {}
    for p in data:
        blocks = [text_block(value, w - 2 * PADDING, font, device) for value, w in zip(product_values(p), widths)]
        height = max(26, max(b.height for b in blocks) + 2 * PADDING)
        if height > plan.body_height:
            raise AppError(f"「{p.name[:30]}」の情報が1ページの高さを超えています。マシン番号・運営方法・商品名を短くしてください。文字を縮小・切断せずに印刷を中止しました。")
        cached[id(p)] = (blocks, height)

    def build(chunk, capacity):
        left = chunk[::2] if layout == "row" else chunk[:capacity]
        right = chunk[1::2] if layout == "row" else chunk[capacity:]
        count = len(left)
        result = []
        for i in range(count):
            a, b = left[i], right[i] if i < len(right) else None
            cells = [cached[id(a)][0], cached[id(b)][0] if b else []]
            height = max(cached[id(a)][1], cached[id(b)][1] if b else 0)
            result.append(PrintRow(a, b, height, cells))
        return result

    remaining = list(data)
    while remaining:
        # Find the largest contiguous prefix fitting the page. Reducing the
        # candidate can change left/right pairing, so recompute both heights.
        for capacity in range(rows, 0, -1):
            count = min(capacity * 2, len(remaining))
            page = build(remaining[:count], capacity)
            if sum(r.height for r in page) <= plan.body_height:
                break
        else:
            count = 0
        if not count:
            raise AppError("商品を印刷可能領域に配置できません。")
        plan.pages.append(page)
        remaining = remaining[count:]
    if not plan.pages:
        plan.pages = [[]]
    return plan


def draw_page(painter, plan, page_index, selected_day, printed_day=None):
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(QColor("#111111"))
    painter.setFont(print_font(15))
    painter.drawText(QRectF(0, 0, plan.width, TITLE_HEIGHT - 5), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, f"入れ替え指示書　{selected_day:%Y年%m月%d日}")
    pen = QPen(QColor("#000000"))
    pen.setWidthF(1.0)
    painter.setPen(pen)
    painter.setFont(plan.font)
    for side in range(2):
        x = side * ((plan.width + GAP) / 2)
        for w, block in zip(plan.widths, plan.headers):
            rect = QRectF(x, TITLE_HEIGHT, w, plan.header_height)
            painter.fillRect(rect, QColor("#E8F0EB"))
            painter.drawRect(rect)
            block.draw(painter, QPointF(x + PADDING, TITLE_HEIGHT + PADDING))
            x += w
    y = TITLE_HEIGHT + plan.header_height
    for row in plan.pages[page_index]:
        for side, product in enumerate((row.left, row.right)):
            x = side * ((plan.width + GAP) / 2)
            for col, w in enumerate(plan.widths):
                rect = QRectF(x, y, w, row.height)
                painter.fillRect(rect, QColor("#FCE4EC" if product and product.priority else "#FFFFFF"))
                painter.drawRect(rect)
                if product:
                    row.cells[side][col].draw(painter, QPointF(x + PADDING, y + PADDING))
                x += w
        y += row.height
    painter.setFont(print_font(10, False))
    printed_day = printed_day or datetime.now(timezone(timedelta(hours=9))).date()
    painter.drawText(QRectF(0, plan.height - FOOTER_HEIGHT + 4, plan.width, 16), Qt.AlignmentFlag.AlignLeft, f"印刷日：{printed_day:%Y/%m/%d}")
    painter.drawText(QRectF(0, plan.height - FOOTER_HEIGHT + 4, plan.width, 16), Qt.AlignmentFlag.AlignRight, f"{page_index + 1} / {len(plan.pages)}")
    painter.restore()


def render_document(printer, products, selected_day, rows=20, layout="column"):
    page = printer.pageRect(QPrinter.Unit.Point)
    plan = plan_pages(products, page.width(), page.height(), rows, layout)
    first = max(1, printer.fromPage())
    last = min(len(plan.pages), printer.toPage() or len(plan.pages))
    indexes = list(range(first - 1, last))
    if not indexes:
        raise AppError("選択した印刷ページがありません。")
    if printer.pageOrder() == QPrinter.PageOrder.LastPageFirst:
        indexes.reverse()
    # Native drivers handle their own copies when supported. Otherwise render
    # the requested copies, respecting collated / uncollated order.
    copies = 1 if printer.supportsMultipleCopies() else max(1, printer.copyCount())
    indexes = indexes * copies if printer.collateCopies() else [i for i in indexes for _ in range(copies)]
    painter = QPainter()
    if not painter.begin(printer):
        raise AppError("プリンターまたはPDFを開けません。接続・保存先を確認してください。")
    try:
        for n, index in enumerate(indexes):
            if n and not printer.newPage():
                raise AppError("改ページに失敗しました。プリンターの状態を確認してください。")
            painter.save()
            pixels = printer.pageRect(QPrinter.Unit.DevicePixel)
            # The printer's default origin is already at the printable margin.
            painter.scale(pixels.width() / page.width(), pixels.height() / page.height())
            draw_page(painter, plan, index, selected_day)
            painter.restore()
    finally:
        painter.end()
    return plan


def save_pdf(path, products, selected_day, rows=20, layout="column", source=None):
    path = Path(path)
    if source and path.resolve() == Path(source).resolve():
        raise AppError("入力Excelには上書きできません。")
    temp = None
    try:
        with NamedTemporaryFile(dir=path.parent, suffix=".pdf", delete=False) as handle:
            temp = Path(handle.name)
        plan = render_document(configured_printer(temp), products, selected_day, rows, layout)
        if temp.stat().st_size == 0:
            raise AppError("PDFを生成できませんでした。")
        temp.replace(path)
        return plan
    finally:
        if temp and temp.exists():
            temp.unlink()
