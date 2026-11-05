from __future__ import annotations

from copy import copy
import math
import re
from pathlib import Path
from tempfile import NamedTemporaryFile
import unicodedata

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.worksheet.page import PageMargins
from openpyxl.worksheet.pagebreak import Break
from openpyxl.utils import get_column_letter

from .core import AppError, ordered
from .instruction_fields import LABELS, product_values
from .instruction_layout import ProductGroup, pack_products
from .methods import active_assignments

PINK = "FFFCE4EC"


def positions(products, rows=20, layout="column"):
    def measure(p, ids, continuation):
        return ProductGroup(p, ids, [1] * len(ids), continuation=continuation)

    pages, _ = pack_products(products, rows, layout, float("inf"), measure)
    for page_no, page in enumerate(pages):
        offset = 0
        for pair in page:
            for side, g in enumerate(pair.groups):
                if g:
                    yield page_no, offset, side, g.product
            offset += len(pair.heights)


def safe_value(cell, value):
    cell.value = value
    if isinstance(value, str):
        cell.data_type = "s"


def text_height(value, width, size=10):
    if value is None or value == "":
        return 0
    capacity = max(1, (width * 7 - 10) / (size * 96 / 72 * 0.6))
    lines = 0
    for paragraph in str(value).split("\n"):
        paragraph_lines, used = 1, 0
        for token in re.findall(r"\S+\s*", paragraph):
            units = sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in token)
            if used and used + units > capacity:
                paragraph_lines += 1
                used = 0
            overflow = max(0, math.ceil(units / capacity) - 1)
            paragraph_lines += overflow
            used += units - overflow * capacity
        lines += paragraph_lines
    return lines * max(12, size * 1.5) + 8


def export_excel(path, products, selected_day, source=None, rows=20, layout="column"):
    path = Path(path)
    if path.suffix.lower() != ".xlsx":
        raise AppError("出力先は.xlsx形式を指定してください。")
    if source and path.resolve() == Path(source).resolve():
        raise AppError("元のExcelには上書きできません。")
    wb = Workbook()
    ws = wb.active
    ws.title = "入れ替え指示書"
    margin = 6 / 25.4
    ws.page_margins = PageMargins(
        left=margin, right=margin, top=margin, bottom=margin, header=0, footer=0
    )
    template = None
    original = None
    thin = Side(style="thin", color="000000")
    for c, width in enumerate([15, 14, 20, 36, 7, 7, 17] * 2, 1):
        ws.column_dimensions[get_column_letter(c)].width = width
    try:
        if source and Path(source).exists():
            template = load_workbook(source, data_only=False)
            if "入れ替え指示書" in template.sheetnames:
                original = template["入れ替え指示書"]
                ws.page_margins = copy(original.page_margins)
                ws.print_options = copy(original.print_options)
                # Old C/J product-name widths map to the new D/K name fields.
                for start in (0, 7):
                    for target, reference in (
                        (1, 1),
                        (2, 2),
                        (4, 3),
                        (5, 5),
                        (6, 6),
                        (7, 7),
                    ):
                        key = get_column_letter(start + reference)
                        if key in original.column_dimensions:
                            ws.column_dimensions[get_column_letter(start + target)].width = (
                                original.column_dimensions[key].width
                            )
        printable_h = 595.28 - 72 * (ws.page_margins.top + ws.page_margins.bottom)
        printable_w = 841.89 - 72 * (ws.page_margins.left + ws.page_margins.right)
        width_points = sum(
            (ws.column_dimensions[get_column_letter(c)].width * 7 + 5) * 0.75 for c in range(1, 15)
        )
        scale = min(1, printable_w / width_points) * 0.98
        # Compensate font sizes for Excel's width scale, keeping printed type
        # readable (10pt body / 14pt names) instead of shrinking it to 5-7pt.
        header_height = max(
            26 / scale,
            max(
                text_height(
                    LABELS[(c - 1) % 7],
                    ws.column_dimensions[get_column_letter(c)].width,
                    10 / scale,
                )
                for c in range(1, 15)
            ),
        )
        title_height = 28 / scale
        body_height = printable_h / scale - title_height - header_height
        widths = [
            min(
                ws.column_dimensions[get_column_letter(c)].width,
                ws.column_dimensions[get_column_letter(c + 7)].width,
            )
            for c in range(1, 8)
        ]
        indexes = {id(p): i for i, p in enumerate(ordered(products))}

        def measure(p, ids, continuation):
            values = product_values(p)
            assigned = active_assignments(p)
            baseline = (
                (original.row_dimensions[3 + indexes[id(p)] % rows].height if original else None)
                or 26
            ) / scale
            heights = [
                max(
                    baseline,
                    text_height(n or "未割当", widths[0], 10 / scale),
                    text_height(assigned.get(n) or "未設定", widths[1], 10 / scale),
                )
                for n in ids
            ]
            needed = max(
                text_height(values[c], widths[c], (14 if c == 3 else 10) / scale)
                for c in range(2, 7)
            )
            extra = max(0, needed - sum(heights)) / len(heights)
            return ProductGroup(
                p, list(ids), [h + extra for h in heights], continuation=continuation
            )

        pages, warnings = pack_products(products, rows, layout, body_height, measure)
        cursor = 1
        for page_no, page in enumerate(pages):
            if page_no:
                ws.row_breaks.append(Break(id=cursor - 1))
            ws.merge_cells(start_row=cursor, start_column=1, end_row=cursor, end_column=14)
            ws.cell(
                cursor,
                1,
                f"入れ替え指示書　{selected_day:%Y年%m月%d日}　{page_no+1}/{len(pages)}"
                + (
                    " （継続商品あり）"
                    if any(g and g.continuation for pair in page for g in pair.groups)
                    else ""
                ),
            )
            ws.cell(cursor, 1).font = Font(name="Yu Gothic", size=16 / scale, bold=True)
            ws.row_dimensions[cursor].height = title_height
            for c in range(1, 15):
                cell = ws.cell(cursor + 1, c, LABELS[(c - 1) % 7])
                cell.font = Font(name="Yu Gothic", size=10 / scale, bold=True)
                cell.fill = PatternFill("solid", fgColor="FFE2E8F0")
                cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
                cell.border = Border(left=thin, right=thin, top=thin, bottom=thin)
            for base in (0, 7):
                ws.merge_cells(
                    start_row=cursor + 1,
                    end_row=cursor + 1,
                    start_column=base + 2,
                    end_column=base + 3,
                )
                ws.cell(cursor + 1, base + 2, "運営方法・予定ブース数")
            ws.row_dimensions[cursor + 1].height = header_height
            row = cursor + 2
            reference_rows = [3, 3]
            for pair in page:
                # Excel caps a physical row at 409pt. A tall logical machine
                # row can span several physical rows without splitting text
                # or duplicating the machine, method, or product fields.
                starts, repeats, expanded = [], [], []
                for height in pair.heights:
                    starts.append(len(expanded))
                    count = max(1, math.ceil(height / 409))
                    repeats.append(count)
                    expanded.extend([height / count] * count)
                span = len(expanded)
                for offset, height in enumerate(expanded):
                    ws.row_dimensions[row + offset].height = height
                for side, group in enumerate(pair.groups):
                    base = side * 7
                    for offset in range(span):
                        for c in range(1, 8):
                            cell = ws.cell(row + offset, base + c)
                            reference = c if c < 3 or c > 4 else 3
                            if original:
                                ref = original.cell(reference_rows[side], base + reference)
                                for attr in (
                                    "font",
                                    "fill",
                                    "border",
                                    "protection",
                                    "number_format",
                                ):
                                    setattr(cell, attr, copy(getattr(ref, attr)))
                            else:
                                cell.font = Font(name="Yu Gothic", size=10)
                            copied_font = copy(cell.font)
                            copied_font.sz = 10 / scale
                            copied_font.bold = True
                            copied_font.color = "000000"
                            cell.font = copied_font
                            if original:
                                source_border = original.cell(
                                    reference_rows[side], base + reference
                                ).border
                                sides = {
                                    edge: Side(
                                        style=getattr(getattr(source_border, edge), "style", None)
                                        or "thin",
                                        color="000000",
                                    )
                                    for edge in ("left", "right", "top", "bottom")
                                }
                                cell.border = Border(**sides)
                            else:
                                cell.border = Border(left=thin, right=thin, top=thin, bottom=thin)
                            if group and group.product.priority:
                                cell.fill = PatternFill("solid", fgColor=PINK)
                            cell.alignment = Alignment(
                                horizontal="center" if c >= 4 else "left",
                                vertical="center",
                                wrap_text=True,
                            )
                    if group:
                        assigned = active_assignments(group.product)
                        values = product_values(group.product)
                        for offset, number in enumerate(group.machines):
                            machine_row = row + starts[offset]
                            safe_value(ws.cell(machine_row, base + 1), number or "未割当")
                            safe_value(
                                ws.cell(machine_row, base + 2),
                                assigned.get(number) or "未設定",
                            )
                        for c in range(3, 8):
                            safe_value(ws.cell(row, base + c), values[c - 1])
                            if c == 4:
                                ws.cell(row, base + c).font = Font(
                                    name="Yu Gothic", size=14 / scale, bold=True
                                )
                            if span > 1:
                                ws.merge_cells(
                                    start_row=row,
                                    end_row=row + span - 1,
                                    start_column=base + c,
                                    end_column=base + c,
                                )
                    for offset, count in enumerate(repeats):
                        if count > 1:
                            for c in (1, 2):
                                ws.merge_cells(
                                    start_row=row + starts[offset],
                                    end_row=row + starts[offset] + count - 1,
                                    start_column=base + c,
                                    end_column=base + c,
                                )
                    reference_rows[side] += 1
                row += span
            cursor = row
        ws.page_setup.orientation = "landscape"
        ws.page_setup.paperSize = ws.PAPERSIZE_A4
        ws.page_setup.scale = max(10, int(scale * 100))
        ws.sheet_properties.pageSetUpPr.fitToPage = False
        ws.sheet_properties.pageSetUpPr.autoPageBreaks = False
        ws.print_area = f"A1:N{cursor-1}"
        ws.sheet_view.showGridLines = False
        temp = None
        try:
            with NamedTemporaryFile(dir=path.parent, suffix=".xlsx", delete=False) as handle:
                temp = Path(handle.name)
            wb.save(temp)
            temp.replace(path)
        finally:
            if temp and temp.exists():
                temp.unlink()
        return len(pages)
    finally:
        wb.close()
        if template:
            template.close()
