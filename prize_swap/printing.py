"""Point-based shared layout and QPainter rendering for printer, preview and PDF."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from tempfile import NamedTemporaryFile

from PySide6.QtCore import QMarginsF, QPointF, QRectF, Qt
from PySide6.QtGui import (
    QColor,
    QFont,
    QImage,
    QPageLayout,
    QPageSize,
    QPainter,
    QPen,
    QTextLayout,
    QTextOption,
)
from PySide6.QtPrintSupport import QPrinter

from .core import AppError
from .fonts import print_font, validate_print_fonts
from .instruction_fields import LABELS, product_values
from .instruction_layout import GroupPair, ProductGroup, pack_products

PADDING = 4.0
GAP = 8.0
TITLE_HEIGHT = 32.0
FOOTER_HEIGHT = 20.0
WEIGHTS = (0.13, 0.12, 0.24, 0.285, 0.055, 0.065, 0.105)


def configured_printer(pdf_path=None, orientation=QPageLayout.Orientation.Landscape):
    validate_print_fonts()
    printer = QPrinter(QPrinter.PrinterMode.HighResolution)
    printer.setResolution(300)
    printer.setPageLayout(
        QPageLayout(
            QPageSize(QPageSize.PageSizeId.A4),
            orientation,
            QMarginsF(6, 6, 6, 6),
            QPageLayout.Unit.Millimeter,
        )
    )
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
class PrintPlan:
    pages: list[list[GroupPair]]
    width: float
    height: float
    widths: list[float]
    headers: list[TextBlock]
    header_height: float
    font: QFont
    # Retain the metrics device for the lifetime of QTextLayouts.
    metrics_device: QImage
    warnings: list | None = None

    @property
    def body_height(self):
        return self.height - TITLE_HEIGHT - self.header_height - FOOTER_HEIGHT


def text_block(text, width, font, device, alignment=Qt.AlignmentFlag.AlignLeft):
    # QTextLayout doesn't treat a raw newline as a paragraph break; U+2028 does.
    text = str(text).replace("\r\n", "\n").replace("\r", "\n")
    layout = QTextLayout(text.replace("\n", "\u2028"), font, device)
    option = QTextOption()
    option.setWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
    option.setAlignment(alignment)
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
    headers = [
        text_block(
            "運営方法・予定ブース数" if i == 1 else ("" if i == 2 else label),
            (widths[1] + widths[2] if i == 1 else w) - 2 * PADDING,
            font,
            device,
        )
        for i, (label, w) in enumerate(zip(LABELS, widths))
    ]
    header_height = max(b.height for b in headers) + 2 * PADDING
    plan = PrintPlan([], page_width, page_height, widths, headers, header_height, font, device)
    if plan.body_height < 26 or min(widths) <= 2 * PADDING:
        raise AppError("印刷可能領域が小さすぎます。A4横向き・余白6mmを選択してください。")
    name_font = print_font(14)
    name_font.setWeight(QFont.Weight.Bold)

    def measure(product, ids, continuation):
        from .methods import active_assignments

        values = product_values(product)
        assigned = active_assignments(product)
        machine_cells = [
            [
                text_block(number or "未割当", widths[0] - 2 * PADDING, font, device),
                text_block(assigned.get(number) or "未設定", widths[1] - 2 * PADDING, font, device),
            ]
            for number in ids
        ]
        merged = [
            text_block(
                value,
                widths[c] - 2 * PADDING,
                name_font if c == 3 else font,
                device,
                Qt.AlignmentFlag.AlignHCenter if c >= 3 else Qt.AlignmentFlag.AlignLeft,
            )
            for c, value in enumerate(values)
            if c >= 2
        ]
        heights = [max(24, max(b.height for b in cells) + 2 * PADDING) for cells in machine_cells]
        needed = max(b.height for b in merged) + 2 * PADDING
        extra = max(0, needed - sum(heights)) / len(heights)
        heights = [h + extra for h in heights]
        combined = [
            text_block(
                "\n".join(b[c].text for b in machine_cells), widths[c] - 2 * PADDING, font, device
            )
            for c in (0, 1)
        ]
        return ProductGroup(
            product, list(ids), heights, combined + merged, machine_cells, continuation
        )

    plan.pages, plan.warnings = pack_products(products, rows, layout, plan.body_height, measure)
    return plan


def draw_page(painter, plan, page_index, selected_day, printed_day=None):
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(QColor("#111111"))
    painter.setFont(print_font(15))
    painter.drawText(
        QRectF(0, 0, plan.width, TITLE_HEIGHT - 5),
        Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
        f"入れ替え指示書　{selected_day:%Y年%m月%d日}"
        + (
            "　（継続商品あり）"
            if any(g and g.continuation for r in plan.pages[page_index] for g in r.groups)
            else ""
        ),
    )
    pen = QPen(QColor("#000000"))
    pen.setWidthF(1.0)
    painter.setPen(pen)
    painter.setFont(plan.font)
    for side in range(2):
        x = side * ((plan.width + GAP) / 2)
        for col, (w, block) in enumerate(zip(plan.widths, plan.headers)):
            if col != 2:
                width = plan.widths[1] + plan.widths[2] if col == 1 else w
                rect = QRectF(x, TITLE_HEIGHT, width, plan.header_height)
                painter.fillRect(rect, QColor("#E8F0EB"))
                painter.drawRect(rect)
                block.draw(painter, QPointF(x + PADDING, TITLE_HEIGHT + PADDING))
            x += w
    y = TITLE_HEIGHT + plan.header_height
    for row in plan.pages[page_index]:
        for side, group in enumerate(row.groups):
            x0 = side * ((plan.width + GAP) / 2)
            fill = QColor("#FCE4EC" if group and group.product.priority else "white")
            painter.fillRect(QRectF(x0, y, sum(plan.widths), row.height), fill)
            # Individual machine / method rows stop at the merged-field border.
            yy = y
            for i, height in enumerate(row.heights):
                x = x0
                for c in (0, 1):
                    painter.drawRect(QRectF(x, yy, plan.widths[c], height))
                    if group and i < len(group.machine_cells):
                        b = group.machine_cells[i][c]
                        b.draw(painter, QPointF(x + PADDING, yy + (height - b.height) / 2))
                    x += plan.widths[c]
                yy += height
            x = x0 + sum(plan.widths[:2])
            for c in range(2, 7):
                painter.drawRect(QRectF(x, y, plan.widths[c], row.height))
                if group:
                    b = group.cells[c]
                    b.draw(painter, QPointF(x + PADDING, y + (row.height - b.height) / 2))
                x += plan.widths[c]
        y += row.height
    painter.setFont(print_font(10, False))
    printed_day = printed_day or datetime.now(timezone(timedelta(hours=9))).date()
    painter.drawText(
        QRectF(0, plan.height - FOOTER_HEIGHT + 4, plan.width, 16),
        Qt.AlignmentFlag.AlignLeft,
        f"印刷日：{printed_day:%Y/%m/%d}",
    )
    painter.drawText(
        QRectF(0, plan.height - FOOTER_HEIGHT + 4, plan.width, 16),
        Qt.AlignmentFlag.AlignRight,
        f"{page_index + 1} / {len(plan.pages)}",
    )
    painter.restore()


def render_document(printer, products, selected_day, rows=20, layout="column"):
    page = printer.pageRect(QPrinter.Unit.Point)
    plan = plan_pages(products, page.width(), page.height(), rows, layout)
    render_pages(
        printer,
        len(plan.pages),
        lambda painter, index: draw_page(painter, plan, index, selected_day),
    )
    return plan


def render_pages(printer, page_count, draw):
    """Shared device/DPI transform, ranges, copy count and page order."""
    page = printer.pageRect(QPrinter.Unit.Point)
    first = max(1, printer.fromPage())
    last = min(page_count, printer.toPage() or page_count)
    indexes = list(range(first - 1, last))
    if not indexes:
        raise AppError("選択した印刷ページがありません。")
    if printer.pageOrder() == QPrinter.PageOrder.LastPageFirst:
        indexes.reverse()
    # Native drivers handle their own copies when supported. Otherwise render
    # the requested copies, respecting collated / uncollated order.
    copies = 1 if printer.supportsMultipleCopies() else max(1, printer.copyCount())
    indexes = (
        indexes * copies if printer.collateCopies() else [i for i in indexes for _ in range(copies)]
    )
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
            draw(painter, index)
            painter.restore()
    finally:
        painter.end()


def save_pdf(path, products, selected_day, rows=20, layout="column", source=None):
    return save_rendered_pdf(
        path,
        lambda temp: render_document(
            configured_printer(temp), products, selected_day, rows, layout
        ),
        [source] if source else [],
    )


def save_rendered_pdf(path, render, sources=()):
    """Atomic PDF replacement shared by instructions and booth labels."""
    path = Path(path)
    if any(source and path.resolve() == Path(source).resolve() for source in sources):
        raise AppError("入力Excelには上書きできません。")
    temp = None
    try:
        with NamedTemporaryFile(dir=path.parent, suffix=".pdf", delete=False) as handle:
            temp = Path(handle.name)
        plan = render(temp)
        if temp.stat().st_size == 0:
            raise AppError("PDFを生成できませんでした。")
        temp.replace(path)
        return plan
    finally:
        if temp and temp.exists():
            temp.unlink()
