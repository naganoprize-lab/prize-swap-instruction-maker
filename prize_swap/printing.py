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
    QFontMetricsF,
    QImage,
    QPageLayout,
    QPageSize,
    QPainter,
    QPen,
    QTextLayout,
    QTextOption,
    QTextCharFormat,
)
from PySide6.QtPrintSupport import QPrinter

from .core import AppError, ordered
from .fonts import print_font, validate_print_fonts
from .instruction_fields import LABELS, product_values
from .instruction_layout import GroupPair, ProductGroup
from .print_layout import pack_balanced
from .methods import booth_report, active_assignments, registered_machines
from .booth_style import METRIC_COLORS

PADDING = 4.0
GAP = 8.0
TITLE_HEIGHT = 32.0
FOOTER_HEIGHT = 36.0
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
        printer.setColorMode(QPrinter.ColorMode.Color)
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
    padding: float = PADDING
    title_height: float = TITLE_HEIGHT
    footer_height: float = FOOTER_HEIGHT
    name_size: int = 14
    body_size: int = 11
    mode: str = "auto"
    reason: str = ""
    compact: bool = False

    @property
    def body_height(self):
        return self.height - self.title_height - self.header_height - self.footer_height

    def columns(self, page_index):
        return [
            [pair.groups[side] for pair in self.pages[page_index] if pair.groups[side]]
            for side in (0, 1)
        ]


def text_block(text, width, font, device, alignment=Qt.AlignmentFlag.AlignLeft, formats=None):
    # QTextLayout doesn't treat a raw newline as a paragraph break; U+2028 does.
    text = str(text).replace("\r\n", "\n").replace("\r", "\n")
    layout = QTextLayout(text.replace("\n", "\u2028"), font, device)
    option = QTextOption()
    option.setWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
    option.setAlignment(alignment)
    layout.setTextOption(option)
    if formats:
        layout.setFormats(formats)
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


def _column_widths(products, width, font, device, padding, compact):
    metrics = QFontMetricsF(font, device)

    def fit(values, minimum, maximum):
        return min(
            maximum,
            max(
                minimum,
                max((metrics.horizontalAdvance(str(v)) for v in values), default=0) + 2 * padding,
            ),
        )

    machine = fit((n for p in products for n in (registered_machines(p) or ["未割当"])), 35, 62)
    method = fit((v for p in products for v in active_assignments(p).values() if v), 30, 48)
    units = fit((p.units for p in products), 16, 28)
    cases = fit((p.cases for p in products), 17, 28)
    limit = fit((p.limit for p in products), 28, 34)
    reports = [booth_report(p, compact)[0] for p in products]
    plans = fit((line for text in reports for line in text.splitlines()), 76, 105)
    name = width - machine - method - plans - units - cases - limit
    if name < width * 0.38:
        plans = max(60, plans - (width * 0.38 - name))
        name = width - machine - method - plans - units - cases - limit
    if name <= 2 * padding or min(machine, method, plans, units, cases, limit) <= 2 * padding:
        raise AppError("印刷可能領域が小さすぎます。A4横向き・余白6mmを選択してください。")
    return [machine, method, plans, name, units, cases, limit]


def _measured_plan(products, page_width, page_height, rows, layout, profile):
    body_size, name_size, padding, compact = profile
    font = print_font(body_size)
    device = QImage(1, 1, QImage.Format.Format_ARGB32)
    device.setDotsPerMeterX(2835)
    device.setDotsPerMeterY(2835)
    widths = _column_widths(products, (page_width - GAP) / 2, font, device, padding, compact)
    heading = "運営方法・予定数\n（ブース）" if compact else "運営方法・予定ブース数"
    headers = [
        text_block(
            heading if i == 1 else "" if i == 2 else label,
            (widths[1] + widths[2] if i == 1 else w) - 2 * padding,
            font,
            device,
        )
        for i, (label, w) in enumerate(zip(LABELS, widths))
    ]
    plan = PrintPlan(
        [],
        page_width,
        page_height,
        widths,
        headers,
        max(b.height for b in headers) + 2 * padding,
        font,
        device,
        padding=padding,
        title_height=22 if compact else TITLE_HEIGHT,
        footer_height=28 if compact else FOOTER_HEIGHT,
        name_size=name_size,
        body_size=body_size,
        compact=compact,
    )
    if plan.body_height < 26:
        raise AppError("印刷可能領域が小さすぎます。A4横向き・余白6mmを選択してください。")
    name_font = print_font(name_size)
    name_font.setWeight(QFont.Weight.Bold)

    def measure(product, ids, continuation):
        from .methods import active_assignments

        values = product_values(product)
        values[2], spans = booth_report(product, compact)
        formats = []
        for start, length, field in spans:
            span = QTextLayout.FormatRange()
            span.start, span.length = start, length
            span.format = QTextCharFormat()
            background, foreground = METRIC_COLORS[field]
            span.format.setBackground(QColor(background))
            span.format.setForeground(QColor(foreground))
            formats.append(span)
        assigned = active_assignments(product)
        machine_cells = [
            [
                text_block(number or "", widths[0] - 2 * padding, font, device),
                text_block(assigned.get(number) or "", widths[1] - 2 * padding, font, device),
            ]
            for number in ids
        ]
        merged = [
            text_block(
                value,
                widths[c] - 2 * padding,
                name_font if c == 3 else font,
                device,
                Qt.AlignmentFlag.AlignHCenter if c >= 3 else Qt.AlignmentFlag.AlignLeft,
                formats if c == 2 else None,
            )
            for c, value in enumerate(values)
            if c >= 2
        ]
        heights = [
            max(12 if compact else 24, max(b.height for b in cells) + 2 * padding)
            for cells in machine_cells
        ]
        needed = max(b.height for b in merged) + 2 * padding
        extra = max(0, needed - sum(heights)) / len(heights)
        heights = [h + extra for h in heights]
        combined = [
            text_block(
                "\n".join(b[c].text for b in machine_cells), widths[c] - 2 * padding, font, device
            )
            for c in (0, 1)
        ]
        return ProductGroup(
            product, list(ids), heights, combined + merged, machine_cells, continuation
        )

    plan.pages, plan.warnings = pack_balanced(products, rows, layout, plan.body_height, measure)
    return plan


def plan_pages(products, page_width, page_height, rows=20, layout="column", mode="auto"):
    if (
        not 1 <= rows <= 50
        or layout not in ("column", "row")
        or mode not in ("auto", "standard", "compact")
    ):
        raise AppError("ページ配置の設定が不正です。")
    products = ordered(products)
    from .instructions import number_lookup

    number_lookup(products)
    target = max(1, (len(products) + 19) // 20)
    profiles = {
        "standard": [(11, 14, 4, False)],
        "compact": [(9, 11, 1.5, True), (9, 10, 1.5, True)],
        "auto": [(10, 13, 2, True), (10, 12, 2, True), (9, 11, 1.5, True), (9, 10, 1.5, True)],
    }[mode]
    chosen = None
    for profile in profiles:
        try:
            candidate = _measured_plan(products, page_width, page_height, rows, layout, profile)
        except AppError:
            if profile == profiles[-1] and chosen is None:
                raise
            continue
        if chosen is None or len(candidate.pages) < len(chosen.pages):
            chosen = candidate
        if mode != "compact" and len(candidate.pages) <= target:
            chosen = candidate
            break
    chosen.mode = mode
    label = {"auto": "自動最適化", "standard": "標準", "compact": "コンパクト"}[mode]
    chosen.reason = f"{label}：{len(products)}商品・{len(chosen.pages)}ページ / 商品名{chosen.name_size}pt・本文{chosen.body_size}pt。左右の高さとページ間の密度を均等化しています。"
    if len(chosen.pages) > target:
        groups = [
            g for page in range(len(chosen.pages)) for col in chosen.columns(page) for g in col
        ]
        lines = max((g.cells[3].layout.lineCount() for g in groups), default=0)
        machines = sum(len(g.machines) for g in groups)
        chosen.reason += f" 目標{target}ページを超えています。商品名最大{lines}行・マシン行{machines}行と片側上限{rows}商品の条件で、文字や商品ブロックを欠落させず配置するため{len(chosen.pages)}ページが必要です（使用可能高{chosen.body_height*25.4/72:.0f}mm/列）。"
    if chosen.warnings:
        chosen.reason += " " + " / ".join(chosen.warnings)
    return chosen


def draw_page(painter, plan, page_index, selected_day, printed_day=None):
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(QColor("#111111"))
    painter.setFont(print_font(14 if plan.compact else 15))
    continued = any(g.continuation for col in plan.columns(page_index) for g in col)
    painter.drawText(
        QRectF(0, 0, plan.width, plan.title_height - 3),
        Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
        f"入れ替え指示書　{selected_day:%Y年%m月%d日}"
        + ("　（継続商品あり）" if continued else ""),
    )
    pen = QPen(QColor("#000000"))
    pen.setWidthF(1)
    painter.setPen(pen)
    padding = plan.padding
    for side in (0, 1):
        x = side * ((plan.width + GAP) / 2)
        for col, (width, block) in enumerate(zip(plan.widths, plan.headers)):
            if col != 2:
                cell_width = plan.widths[1] + plan.widths[2] if col == 1 else width
                rect = QRectF(x, plan.title_height, cell_width, plan.header_height)
                painter.fillRect(rect, QColor("#E8F0EB"))
                painter.drawRect(rect)
                block.draw(painter, QPointF(x + padding, plan.title_height + padding))
            x += width
    from .instructions import number_lookup
    from .instruction_badges import draw_badge

    numbers = number_lookup(
        [
            g.product
            for i in range(len(plan.pages))
            for col in plan.columns(i)
            for g in col
            if not g.continuation
        ]
    )
    for side, column in enumerate(plan.columns(page_index)):
        y = plan.title_height + plan.header_height
        x0 = side * ((plan.width + GAP) / 2)
        for group in column:
            painter.fillRect(
                QRectF(x0, y, sum(plan.widths), group.height),
                QColor("#FCE4EC" if group.product.priority else "white"),
            )
            yy = y
            for i, height in enumerate(group.heights):
                x = x0
                for c in (0, 1):
                    painter.drawRect(QRectF(x, yy, plan.widths[c], height))
                    block = group.machine_cells[i][c]
                    if c == 0:
                        number = numbers.get((group.product.entry_id, group.machines[i]))
                        if number:
                            diameter = 10 if plan.compact else 12
                            draw_badge(
                                painter,
                                QRectF(x + 1, yy + (height - diameter) / 2, diameter, diameter),
                                number,
                            )
                            available = plan.widths[c] - diameter - 3
                            size = plan.body_size
                            font = print_font(size)
                            while (
                                size > 6
                                and QFontMetricsF(font, plan.metrics_device).horizontalAdvance(
                                    block.text
                                )
                                > available
                            ):
                                size -= 0.25
                                font = print_font(size)
                            badge_text = text_block(
                                block.text, available, font, plan.metrics_device
                            )
                            badge_text.draw(
                                painter,
                                QPointF(x + diameter + 2, yy + (height - badge_text.height) / 2),
                            )
                        else:
                            block.draw(
                                painter, QPointF(x + padding, yy + (height - block.height) / 2)
                            )
                    else:
                        block.draw(painter, QPointF(x + padding, yy + (height - block.height) / 2))
                    x += plan.widths[c]
                yy += height
            x = x0 + sum(plan.widths[:2])
            for c in range(2, 7):
                painter.drawRect(QRectF(x, y, plan.widths[c], group.height))
                block = group.cells[c]
                block.draw(painter, QPointF(x + padding, y + (group.height - block.height) / 2))
                x += plan.widths[c]
            y += group.height
    painter.setFont(print_font(9 if plan.compact else 10))
    painter.drawText(
        QRectF(0, plan.height - plan.footer_height + 1, plan.width, 13 if plan.compact else 16),
        Qt.AlignmentFlag.AlignLeft,
        "方式横の数字＝予定ブース数　済＝割当済み　未＝未割当　超＝超過　?＝予定未設定",
    )
    painter.setFont(print_font(9 if plan.compact else 10, False))
    printed_day = printed_day or datetime.now(timezone(timedelta(hours=9))).date()
    painter.drawText(
        QRectF(
            0,
            plan.height - 13 if plan.compact else plan.height - 16,
            plan.width,
            13 if plan.compact else 16,
        ),
        Qt.AlignmentFlag.AlignLeft,
        f"印刷日：{printed_day:%Y/%m/%d}",
    )
    painter.drawText(
        QRectF(
            0,
            plan.height - 13 if plan.compact else plan.height - 16,
            plan.width,
            13 if plan.compact else 16,
        ),
        Qt.AlignmentFlag.AlignRight,
        f"{page_index+1} / {len(plan.pages)}",
    )
    painter.restore()


def render_document(printer, products, selected_day, rows=20, layout="column", mode="auto"):
    page = printer.pageRect(QPrinter.Unit.Point)
    plan = plan_pages(products, page.width(), page.height(), rows, layout, mode)
    render_pages(
        printer,
        len(plan.pages),
        lambda painter, index: draw_page(painter, plan, index, selected_day),
    )
    return plan


def render_pages(printer, page_count, draw, page_layout=None):
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
    original_layout = QPageLayout(printer.pageLayout())

    def set_layout(index):
        desired = page_layout(index)
        if printer.pageLayout() != desired and not printer.setPageLayout(desired):
            raise AppError(
                "このプリンターはページごとの用紙変更に対応していません。指示書と地図を分けて印刷してください。"
            )

    if page_layout:
        set_layout(indexes[0])
    painter = QPainter()
    if not painter.begin(printer):
        raise AppError("プリンターまたはPDFを開けません。接続・保存先を確認してください。")
    try:
        for n, index in enumerate(indexes):
            if n and page_layout:
                set_layout(index)
            if n and not printer.newPage():
                raise AppError("改ページに失敗しました。プリンターの状態を確認してください。")
            painter.save()
            page = printer.pageRect(QPrinter.Unit.Point)
            pixels = printer.pageRect(QPrinter.Unit.DevicePixel)
            # The printer's default origin is already at the printable margin.
            painter.scale(pixels.width() / page.width(), pixels.height() / page.height())
            draw(painter, index)
            painter.restore()
    finally:
        painter.end()
        if page_layout and printer.pageLayout() != original_layout and not printer.paintingActive():
            printer.setPageLayout(original_layout)


def save_pdf(path, products, selected_day, rows=20, layout="column", source=None, mode="auto"):
    return save_rendered_pdf(
        path,
        lambda temp: render_document(
            configured_printer(temp), products, selected_day, rows, layout, mode
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
