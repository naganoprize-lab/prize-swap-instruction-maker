"""Viewport-independent, vector floor printing with card-safe pagination."""

from dataclasses import dataclass, field, replace
from datetime import date
import math

from PySide6.QtCore import QMarginsF, QPointF, QRectF, QSizeF, Qt
from PySide6.QtGui import QColor, QPageLayout, QPageSize, QPen
from PySide6.QtPrintSupport import QPrinter, QPrinterInfo

from .core import AppError
from .machine_matching import machine_key, display_identity
from .floor_render import CardRenderer, FIELDS, SUMMARY_FIELDS, draw_card
from .floor_presentation import ordered_machines
from .floor_status import ratio_text, days_text, metric_text
from .analysis_settings import validate_settings, sales_boundaries
from .floor_status import (
    machine_status,
    overlapping,
    filter_status,
    filter_legend,
    FILTER_FIELDS,
    COLOR_FILTERS,
)
from .printing import configured_printer, render_pages, save_rendered_pdf, text_block
from .fonts import print_font

PAPERS = {"A4": (210, 297), "A3": (297, 420), "A2": (420, 594), "A1": (594, 841), "A0": (841, 1189)}


@dataclass
class FloorPrintOptions:
    paper: str = "A4"
    orientation: str = "landscape"
    width_mm: float = 210
    height_mm: float = 297
    mode: str = "fit"
    scale_percent: float = 100
    font_size: str = "standard"
    card_size: str = "auto"
    card_width: float = 200
    card_height: float = 150
    fields: list = field(default_factory=lambda: list(SUMMARY_FIELDS))
    card_style: str = "summary"
    color_filter: str = "ratio"
    sort: str = "number"
    descending: bool = False
    visible_only: bool = False
    colour: bool = True
    show_methods: bool = False
    selection_only: bool = False
    boundaries: bool = True
    analysis_settings: dict = field(default_factory=dict)

    def validate(self):
        validate_settings(self.analysis_settings)
        if (
            self.paper not in (*PAPERS, "custom")
            or self.orientation not in ("portrait", "landscape")
            or self.mode not in ("fit", "tile", "selection", "actual", "list")
        ):
            raise AppError("売り場印刷の用紙・方向・印刷方式が不正です。")
        if self.color_filter not in COLOR_FILTERS:
            raise AppError("印刷の色フィルターが不正です。")
        if self.card_style not in ("summary", "detailed") or self.sort not in (
            "number",
            "ratio",
            "days",
            "product",
        ):
            raise AppError("印刷の表示形式・並べ替えが不正です。")
        if self.font_size not in ("standard", "large", "xlarge") or self.card_size not in (
            "auto",
            "standard",
            "enlarge",
        ):
            raise AppError("印刷文字・カードサイズの設定が不正です。")
        for name, low, high in (
            ("width_mm", 50, 5000),
            ("height_mm", 50, 5000),
            ("scale_percent", 10, 400),
            ("card_width", 80, 2000),
            ("card_height", 60, 2000),
        ):
            value = getattr(self, name)
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or not low <= value <= high
            ):
                raise AppError(f"印刷設定 {name} は{low}〜{high}で指定してください。")
        if self.width_mm > self.height_mm:
            raise AppError("用紙の短辺は長辺以下で指定してください。")
        if (
            not isinstance(self.fields, list)
            or not self.fields
            or len(set(self.fields)) != len(self.fields)
            or any(f not in FIELDS for f in self.fields)
        ):
            raise AppError("印刷する項目を1つ以上選択してください。")
        if any(
            type(getattr(self, n)) is not bool
            for n in (
                "colour",
                "show_methods",
                "selection_only",
                "boundaries",
                "visible_only",
                "descending",
            )
        ):
            raise AppError("印刷の表示設定が不正です。")
        return self

    @classmethod
    def restore(cls, value):
        try:
            return cls(**({} if value is None else value)).validate()
        except (TypeError, ValueError) as exc:
            raise AppError(f"売り場印刷設定を読み込めません：{exc}") from exc

    def paper_size(self):
        if self.paper == "custom":
            return QPageSize(
                QSizeF(self.width_mm, self.height_mm),
                QPageSize.Unit.Millimeter,
                "ユーザー指定",
                QPageSize.SizeMatchPolicy.ExactMatch,
            )
        return QPageSize(getattr(QPageSize.PageSizeId, self.paper))

    def printable_points(self):
        w, h = PAPERS.get(self.paper, (self.width_mm, self.height_mm))
        if self.orientation == "landscape":
            w, h = h, w
        return (w - 12) * 72 / 25.4, (h - 12) * 72 / 25.4


def floor_printer(options, pdf_path=None, preview=False):
    options.validate()
    orientation = (
        QPageLayout.Orientation.Landscape
        if options.orientation == "landscape"
        else QPageLayout.Orientation.Portrait
    )
    printer = configured_printer(pdf_path, orientation)
    if preview:
        printer.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
        printer.setOutputFileName("")
    layout = QPageLayout(
        options.paper_size(), orientation, QMarginsF(6, 6, 6, 6), QPageLayout.Unit.Millimeter
    )
    if (
        not printer.setPageLayout(layout)
        and printer.outputFormat() == QPrinter.OutputFormat.PdfFormat
    ):
        raise AppError(
            "このプリンターでは指定した用紙・余白を設定できません。用紙を変更してください。"
        )
    printer.setColorMode(
        QPrinter.ColorMode.Color if options.colour else QPrinter.ColorMode.GrayScale
    )
    printer.setDocName("売り場レイアウト")
    return printer


def paper_supported(printer, options):
    if printer.outputFormat() == QPrinter.OutputFormat.PdfFormat:
        return True
    pages = QPrinterInfo(printer).supportedPageSizes()
    if not pages:
        return True  # Driver capabilities unavailable; the dialog remains authoritative.
    requested = options.paper_size().size(QPageSize.Unit.Millimeter)
    return any(
        abs(p.size(QPageSize.Unit.Millimeter).width() - requested.width()) < 1
        and abs(p.size(QPageSize.Unit.Millimeter).height() - requested.height()) < 1
        for p in pages
    )


@dataclass
class PrintCard:
    machine: object
    rect: QRectF
    text: object
    background: str
    priority: bool


@dataclass
class FloorPage:
    origin: QPointF
    cards: list
    row: int = 0
    column: int = 0
    table_rows: list | None = None


@dataclass
class FloorPlan:
    pages: list
    scale: float
    body: QRectF
    width: float
    height: float
    options: FloorPrintOptions
    reference: date
    warnings: list
    headers: list
    footers: list
    bounds: QRectF

    @property
    def frames(self):
        return [
            QRectF(
                p.origin, QSizeF(self.body.width() / self.scale, self.body.height() / self.scale)
            )
            for p in self.pages
        ]


def plan_floor(
    machines,
    assignments,
    options,
    reference,
    renderer=None,
    selected=(),
    page_size=None,
    loaded_at="",
    population=None,
    snapshot=None,
):
    options.validate()
    metrics_settings = validate_settings(options.analysis_settings)
    metrics_settings["_sales_bounds"] = sales_boundaries(
        list(machines) if population is None else population, metrics_settings, snapshot
    )
    renderer = renderer or CardRenderer()
    chosen = (
        [m for m in machines if m.uid in selected]
        if options.selection_only or options.mode == "selection"
        else list(machines)
    )
    if not chosen:
        raise AppError("印刷対象のマシンがありません。選択範囲印刷ではマシンを選択してください。")
    width, height = page_size or options.printable_points()
    heading = f"売り場レイアウト　基準日 {reference:%Y/%m/%d}"
    note = "売上・アベ比は読込時の値です。基準日は投入日数だけに適用。" + (
        f" 読込：{loaded_at}" if loaded_at else ""
    )
    headers = [
        text_block(heading, width, print_font(14), renderer.device),
        text_block(note, width, print_font(9, False), renderer.device),
    ]
    header_height = sum(b.height for b in headers) + 8
    footers = [
        text_block(
            filter_legend(
                options.color_filter, options.analysis_settings, metrics_settings["_sales_bounds"]
            ),
            width,
            print_font(9),
            renderer.device,
        )
    ]
    footer_height = footers[0].height + 20
    body = QRectF(0, header_height, width, height - header_height - footer_height)
    if body.width() < 40 or body.height() < 40:
        raise AppError("用紙の印刷可能領域が小さすぎます。大きい用紙を選んでください。")
    if options.mode == "list":
        return plan_table(
            chosen,
            assignments,
            options,
            reference,
            renderer,
            body,
            width,
            height,
            headers,
            footers,
            metrics_settings,
        )
    cards = []
    font = {"standard": 14, "large": 18, "xlarge": 22}[options.font_size]
    factor = 1.4 if options.card_size == "enlarge" else 1
    for m in chosen:
        plans = assignments.get(machine_key(m), [])
        priority = any(p.priority for p in plans)
        colour, status = filter_status(
            m,
            options.color_filter,
            reference,
            bool(plans),
            priority,
            metrics_settings,
        )
        methods = (
            "／".join(
                dict.fromkeys(p.machine_methods.get(machine_key(m)) or "未設定" for p in plans)
            )
            if options.show_methods
            else ""
        )
        w, h = options.card_width * factor, options.card_height * factor
        layout = renderer.plan(
            m,
            w,
            h,
            font,
            (
                FILTER_FIELDS[options.color_filter]
                if options.card_style == "summary"
                else options.fields
            ),
            reference,
            status,
            methods,
            grow=options.card_size == "auto",
            summary=options.card_style == "summary",
            settings=options.analysis_settings,
        )
        if options.card_size == "auto":
            h = max(h, layout.height)
        cards.append(PrintCard(m, QRectF(m.x, m.y, w, h), layout, colour, priority))
    bounds = QRectF(cards[0].rect)
    for c in cards[1:]:
        bounds = bounds.united(c.rect)
    bounds = bounds.adjusted(-4, -4, 4, 4)
    warnings = []
    # Print rectangles are separate from screen rectangles and never modify them.
    printable = [replace(c.machine, width=c.rect.width(), height=c.rect.height()) for c in cards]
    collisions = overlapping(printable)
    if collisions:
        warnings.append(
            f"印刷カードが{len(collisions)}台で重なります。カードサイズ・項目を調整するか、配置を手動で広げてください。"
        )
    if options.mode in ("fit", "selection"):
        scale = min(body.width() / bounds.width(), body.height() / bounds.height())
        pages = [FloorPage(bounds.topLeft(), cards)]
    else:
        scale = 0.75 * options.scale_percent / 100
        pw, ph = body.width() / scale, body.height() / scale
        mw, mh = max(c.rect.width() for c in cards), max(c.rect.height() for c in cards)
        if mw + 8 > pw or mh + 8 > ph:
            raise AppError(
                "1枚のカードが用紙に収まりません。用紙を大きくするか、倍率・カードサイズ・印刷項目を減らしてください。"
            )
        # Overlapping page regions ensure that a complete card belongs to one
        # page. Each card is assigned once, so no name or number is cut in half.
        sx, sy = pw - mw - 8, ph - mh - 8
        groups = {}
        for c in cards:
            column = max(0, int((c.rect.left() - bounds.left()) / sx))
            row = max(0, int((c.rect.top() - bounds.top()) / sy))
            groups.setdefault((row, column), []).append(c)
        if len(groups) > 500:
            raise AppError("500ページを超えます。用紙を大きくするか倍率を下げてください。")
        pages = [
            FloorPage(QPointF(bounds.left() + col * sx, bounds.top() + row * sy), items, row, col)
            for (row, col), items in sorted(groups.items())
        ]
    minimum = min(c.text.minimum_font * c.text.scale * scale for c in cards)
    if minimum < 8:
        warnings.append(
            f"最小印刷文字は約{minimum:.1f}ptです。A3・複数ページ分割・印刷項目の削減を推奨します。"
        )
    return FloorPlan(
        pages, scale, body, width, height, options, reference, warnings, headers, footers, bounds
    )


def plan_table(
    machines,
    assignments,
    options,
    reference,
    renderer,
    body,
    width,
    height,
    headers,
    footers,
    metrics_settings,
):
    font_size = {"standard": 11, "large": 13, "xlarge": 15}[options.font_size]
    fields = FILTER_FIELDS[options.color_filter]
    widths = [width * 0.22, width * 0.24, width * 0.54]
    labels = [
        {
            "number": "マシン番号 / 状態",
            "ratio": "アベ比",
            "days": "設置日数",
            "product": "商品名",
            "po_rate": "PO率",
            "cost_rate": "原価率",
            "sales_metric": "平均売上 / 総売上",
            "machine_type": "機種",
            "assessment": "入替候補",
        }[key]
        for key in fields
    ]
    table_headers = [
        text_block(label, w - 12, print_font(font_size), renderer.device)
        for label, w in zip(labels, widths)
    ]
    heading = max(b.height for b in table_headers) + 12
    rows = []
    for m in ordered_machines(machines, options.sort, options.descending, reference):
        plans = assignments.get(machine_key(m), [])
        colour, status = filter_status(
            m,
            options.color_filter,
            reference,
            bool(plans),
            any(p.priority for p in plans),
            metrics_settings,
        )
        number = (
            display_identity(m)
            + (" / 投入予定" if plans else "")
            + (" / 優先" if any(p.priority for p in plans) else "")
        )
        data = {
            "number": number,
            "ratio": ratio_text(m),
            "days": days_text(m, reference),
            "product": m.product or "商品未設定",
        }
        data.update(
            {
                k: metric_text(m, k, reference, options.analysis_settings)
                for k in ("po_rate", "cost_rate", "sales_metric", "machine_type", "assessment")
                if k in fields
            }
        )
        values = [data[key] for key in fields]
        blocks = [
            text_block(
                value,
                w - 12,
                print_font(font_size + 2 if fields[i] in ("ratio", "days") else font_size),
                renderer.device,
            )
            for i, (value, w) in enumerate(zip(values, widths))
        ]
        h = max(b.height for b in blocks) + 12
        if h + heading > body.height():
            raise AppError(
                "商品名が一覧表の1ページに収まりません。A3・小さい印刷文字・商品名の修正を選んでください。"
            )
        rows.append((m.uid, blocks, h, colour))
    pages = []
    current = []
    used = heading
    for row in rows:
        if current and used + row[2] > body.height():
            pages.append(FloorPage(QPointF(), [], table_rows=current))
            current = []
            used = heading
        current.append(row)
        used += row[2]
    if current:
        pages.append(FloorPage(QPointF(), [], table_rows=current))
    plan = FloorPlan(
        pages,
        1,
        body,
        width,
        height,
        options,
        reference,
        [],
        headers,
        footers,
        QRectF(0, 0, width, body.height()),
    )
    plan.table_widths = widths
    plan.table_headers = table_headers
    plan.table_heading = heading
    return plan


def draw_table(painter, plan, page):
    painter.save()
    painter.setPen(QPen(QColor("#24392F"), 0.8))
    y = plan.body.top()
    rows = [(None, plan.table_headers, plan.table_heading, "#EAF0EC"), *page.table_rows]
    for _, blocks, height, background in rows:
        x = 0
        for block, width in zip(blocks, plan.table_widths):
            rect = QRectF(x, y, width, height)
            painter.fillRect(rect, QColor(background))
            painter.drawRect(rect)
            block.draw(painter, QPointF(x + 6, y + (height - block.height) / 2))
            x += width
        y += height
    painter.restore()


def draw_floor_page(painter, plan, index):
    painter.save()
    painter.fillRect(QRectF(0, 0, plan.width, plan.height), QColor("white"))
    painter.setPen(QColor("black"))
    y = 0
    for block in plan.headers:
        block.draw(painter, QPointF(0, y))
        y += block.height + 2
    painter.setPen(QPen(QColor("#333333"), 0.6))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawRect(plan.body)
    page = plan.pages[index]
    if page.table_rows is not None:
        draw_table(painter, plan, page)
    painter.save()
    painter.translate(plan.body.topLeft())
    painter.scale(plan.scale, plan.scale)
    for c in page.cards:
        rect = QRectF(c.rect)
        rect.translate(-page.origin.x(), -page.origin.y())
        draw_card(painter, rect, c.text, c.background, c.priority)
    painter.restore()
    y = plan.body.bottom() + 4
    for block in plan.footers:
        block.draw(painter, QPointF(0, y))
        y += block.height
    painter.setFont(print_font(9, False))
    painter.setPen(QColor("black"))
    footer = (
        f"ページ {index+1}/{len(plan.pages)}　マシン一覧表"
        if page.table_rows is not None
        else f"ページ {index+1}/{len(plan.pages)}　範囲：行{page.row+1}・列{page.column+1}　{plan.scale/.75*100:.1f}%（画面100%基準）"
    )
    painter.drawText(
        QRectF(0, plan.height - 15, plan.width, 15), Qt.AlignmentFlag.AlignLeft, footer
    )
    painter.restore()


def render_floor(
    printer,
    machines,
    assignments,
    options,
    reference,
    renderer=None,
    selected=(),
    loaded_at="",
    population=None,
    snapshot=None,
):
    area = printer.pageRect(QPrinter.Unit.Point)
    plan = plan_floor(
        machines,
        assignments,
        options,
        reference,
        renderer,
        selected,
        (area.width(), area.height()),
        loaded_at,
        population,
        snapshot,
    )
    render_pages(
        printer, len(plan.pages), lambda painter, index: draw_floor_page(painter, plan, index)
    )
    return plan


def save_floor_pdf(
    path,
    machines,
    assignments,
    options,
    reference,
    renderer=None,
    selected=(),
    loaded_at="",
    sources=(),
    population=None,
    snapshot=None,
):
    return save_rendered_pdf(
        path,
        lambda temporary: render_floor(
            floor_printer(options, temporary),
            machines,
            assignments,
            options,
            reference,
            renderer,
            selected,
            loaded_at,
            population,
        ),
        sources,
    )
