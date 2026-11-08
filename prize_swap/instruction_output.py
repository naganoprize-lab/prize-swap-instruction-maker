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


def validate_job(value):
    keys = {"include_instruction", "include_map", "include_checklist"}
    if (
        not isinstance(value, dict)
        or set(value) != keys
        or any(type(v) is not bool for v in value.values())
        or not any(value.values())
    ):
        raise AppError("出力する書類を1種類以上選択してください。")
    return dict(value)


@dataclass
class OutputPlan:
    instruction: object
    mapping: object
    pages: list
    base_layout: object
    checklist: object = None

    @property
    def reason(self):
        return " / ".join(p.reason for p in (self.instruction, self.mapping, self.checklist) if p)

    def page_layout(self, index):
        kind, n = self.pages[index]
        if kind == "instructions":
            return self.base_layout
        if kind == "checklist":
            return self.checklist.layout
        return map_layout(self.mapping.paper, self.mapping.pages[n].orientation)

    def draw(self, painter, index, day):
        kind, n = self.pages[index]
        if kind == "instructions":
            draw_page(painter, self.instruction, n, day)
        elif kind == "map":
            draw_map_page(painter, self.mapping, n)
        else:
            from .work_checklist import draw_checklist_page

            draw_checklist_page(painter, self.checklist, n)


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
    job_config=None,
):
    s = validate_options(options)
    job = validate_job(job_config) if job_config is not None else None
    area = printer.pageRect(QPrinter.Unit.Point)
    p = (
        plan_pages(products, area.width(), area.height(), rows, layout, mode)
        if (job["include_instruction"] if job else s["mode"] != "map")
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
            all_entries=s["all_entries"] and s["mode"] == "map" and job is None,
        )
        if (job["include_map"] if job else s["mode"] != "instructions")
        else None
    )
    pages = [("instructions", i) for i in range(len(p.pages) if p else 0)] + [
        ("map", i) for i in range(len(mapping.pages) if mapping else 0)
    ]
    checklist = None
    if job and job["include_checklist"]:
        from .work_checklist import checklist_plan

        checklist = checklist_plan(products, machines, references, day)
        pages += [("checklist", i) for i in range(len(checklist.pages))]
    return OutputPlan(p, mapping, pages, QPageLayout(printer.pageLayout()), checklist)


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
    job_config=None,
):
    s = validate_options(options)
    if s["mode"] == "instructions" and job_config is None:
        return render_document(printer, products, day, rows, layout, mode)
    plan = output_plan(
        printer, products, machines, references, day, rows, layout, mode, s, job_config
    )
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
