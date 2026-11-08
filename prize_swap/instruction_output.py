"""Compose the original instruction renderer and independent map pages."""

from dataclasses import dataclass
from PySide6.QtGui import QPageLayout
from PySide6.QtPrintSupport import QPrinter
from .printing import plan_pages, draw_page, render_pages, render_document
from .instruction_map import plan_map, map_layout, draw_map_page
from .core import AppError

DEFAULTS = dict(
    mode="instructions", paper="A4", orientation="auto", all_entries=False, context=False
)


def validate_options(value=None):
    if value is not None and (not isinstance(value, dict) or set(value) - set(DEFAULTS)):
        raise AppError("指示書・地図の印刷設定が不正です。")
    s = DEFAULTS | dict(value or {})
    if (
        s["mode"] not in ("instructions", "map", "both")
        or s["paper"] not in ("A4", "A3")
        or s["orientation"] not in ("auto", "portrait", "landscape")
        or any(type(s[k]) is not bool for k in ("all_entries", "context"))
    ):
        raise AppError("指示書・地図の印刷設定が不正です。")
    return s


@dataclass
class OutputPlan:
    instruction: object
    mapping: object
    pages: list
    base_layout: object

    @property
    def reason(self):
        return " / ".join(p.reason for p in (self.instruction, self.mapping) if p)

    def page_layout(self, index):
        kind, n = self.pages[index]
        return (
            self.base_layout
            if kind == "instructions"
            else map_layout(self.mapping.paper, self.mapping.pages[n].orientation)
        )

    def draw(self, painter, index, day):
        kind, n = self.pages[index]
        if kind == "instructions":
            draw_page(painter, self.instruction, n, day)
        else:
            draw_map_page(painter, self.mapping, n)


def output_plan(
    printer,
    products,
    machines,
    references,
    day,
    rows=20,
    layout="column",
    mode="auto",
    options=None,
):
    s = validate_options(options)
    area = printer.pageRect(QPrinter.Unit.Point)
    p = (
        plan_pages(products, area.width(), area.height(), rows, layout, mode)
        if s["mode"] != "map"
        else None
    )
    mapping = (
        plan_map(
            products,
            machines,
            references,
            day,
            s["paper"],
            s["orientation"],
            all_entries=s["all_entries"] and s["mode"] == "map",
        )
        if s["mode"] != "instructions"
        else None
    )
    pages = [("instructions", i) for i in range(len(p.pages) if p else 0)] + [
        ("map", i) for i in range(len(mapping.pages) if mapping else 0)
    ]
    return OutputPlan(p, mapping, pages, QPageLayout(printer.pageLayout()))


def render_output(
    printer,
    products,
    machines,
    references,
    day,
    rows=20,
    layout="column",
    mode="auto",
    options=None,
):
    s = validate_options(options)
    if s["mode"] == "instructions":
        return render_document(printer, products, day, rows, layout, mode)
    plan = output_plan(printer, products, machines, references, day, rows, layout, mode, s)
    if printer.outputFormat() != QPrinter.OutputFormat.PdfFormat:
        from PySide6.QtPrintSupport import QPrinterInfo

        supported = QPrinterInfo(printer).supportedPageSizes()
        if supported and any(
            all(size.id() != plan.page_layout(i).pageSize().id() for size in supported)
            for i in range(len(plan.pages))
        ):
            raise AppError(
                "このプリンターは指定した用紙サイズに対応していません。用紙を変更するか指示書と地図を分けて印刷してください。"
            )
    render_pages(
        printer,
        len(plan.pages),
        lambda painter, index: plan.draw(painter, index, day),
        plan.page_layout,
    )
    return plan
