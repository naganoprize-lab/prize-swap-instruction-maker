from __future__ import annotations

from copy import copy
from datetime import date
from pathlib import Path
from tempfile import NamedTemporaryFile

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.worksheet.pagebreak import Break
from openpyxl.utils import get_column_letter

from .core import AppError, ordered

LABELS = ["マシン番号", "運営方法", "商品名", "優先入替", "入数", "CS数", "獲得個数制限"]
PINK = "FFFCE4EC"


def positions(products, rows=20, layout="column"):
    if not 1 <= rows <= 50 or layout not in ("column", "row"):
        raise AppError("レイアウト設定が不正です。")
    for i, product in enumerate(ordered(products)):
        page, index = divmod(i, rows * 2)
        if layout == "column":
            side, row = divmod(index, rows)
        else:
            row, side = divmod(index, 2)
        yield page, row, side, product


def product_values(p):
    return ["\n".join(p.machines), "\n".join(p.methods), p.name, "優先" if p.priority else "", p.units, p.cases, p.limit]


def safe_value(cell, value):
    cell.value = value
    # User-entered names and methods must remain text, even when they start with '='.
    if isinstance(value, str):
        cell.data_type = "s"


def export_excel(path, products, selected_day: date, source=None, rows=20, layout="column"):
    path = Path(path)
    if path.suffix.lower() != ".xlsx":
        raise AppError("出力先は.xlsx形式を指定してください。")
    if source and path.resolve() == Path(source).resolve():
        raise AppError("元のExcelには上書きできません。別の保存先を選んでください。")
    placements = list(positions(products, rows, layout))
    pages = max((item[0] + 1 for item in placements), default=1)
    # A new workbook avoids copying VBA, source data and external links.
    wb = Workbook()
    ws = wb.active
    ws.title = "入れ替え指示書"
    thin = Side(style="thin", color="888888")
    for col, width in enumerate([15, 14, 36, 9, 7, 7, 17] * 2, 1):
        ws.column_dimensions[get_column_letter(col)].width = width
    template = None
    if source and Path(source).exists():
        try:
            template = load_workbook(source, data_only=False)
            if "入れ替え指示書" in template.sheetnames:
                original = template["入れ替え指示書"]
                for col in range(1, 15):
                    key = get_column_letter(col)
                    if key in original.column_dimensions:
                        ws.column_dimensions[key].width = original.column_dimensions[key].width
                ws.page_margins = copy(original.page_margins)
                ws.print_options = copy(original.print_options)
                ws.sheet_properties.pageSetUpPr = copy(original.sheet_properties.pageSetUpPr)
            else:
                original = None
        except Exception as exc:
            raise AppError(f"レイアウト参考Excelを開けません：{exc}") from exc
    else:
        original = None
    try:
        for page in range(pages):
            start = page * (rows + 2) + 1
            ws.merge_cells(start_row=start, start_column=1, end_row=start, end_column=14)
            ws.cell(start, 1, f"入れ替え指示書　{selected_day:%Y年%m月%d日}　{page + 1}/{pages}")
            ws.cell(start, 1).font = Font(name="Yu Gothic", size=16, bold=True)
            ws.row_dimensions[start].height = 28
            for col in range(1, 15):
                c = ws.cell(start + 1, col, LABELS[(col - 1) % 7])
                c.fill = PatternFill("solid", fgColor="FFE2E8F0")
                c.font = Font(name="Yu Gothic", bold=True, size=10)
                c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
                c.border = Border(left=thin, right=thin, top=thin, bottom=thin)
            ws.row_dimensions[start + 1].height = 26
            for offset in range(rows):
                target = start + 2 + offset
                reference = 3 + offset
                ws.row_dimensions[target].height = (original.row_dimensions[reference].height if original else None) or 42
                for col in range(1, 15):
                    c = ws.cell(target, col)
                    if original:
                        reference_cell = original.cell(reference, col)
                        for attribute in ("font", "fill", "border", "alignment", "protection", "number_format"):
                            setattr(c, attribute, copy(getattr(reference_cell, attribute)))
                    else:
                        c.font = Font(name="Yu Gothic", size=10)
                        c.border = Border(left=thin, right=thin, top=thin, bottom=thin)
                    c.alignment = Alignment(vertical="center", wrap_text=True)
            if page:
                ws.row_breaks.append(Break(id=start - 1))
        for page, row, side, p in placements:
            target = page * (rows + 2) + row + 3
            for offset, value in enumerate(product_values(p)):
                c = ws.cell(target, side * 7 + offset + 1)
                safe_value(c, value)
                if p.priority:
                    c.fill = PatternFill("solid", fgColor=PINK)
        ws.page_setup.orientation = "landscape"
        ws.page_setup.paperSize = ws.PAPERSIZE_A4
        # Fixed scale respects manual page breaks; fit-to-page mode can ignore them.
        printable_width = 841.89 - 72 * (ws.page_margins.left + ws.page_margins.right)
        printable_height = 595.28 - 72 * (ws.page_margins.top + ws.page_margins.bottom)
        width_points = sum((ws.column_dimensions[get_column_letter(c)].width * 7 + 5) * 0.75 for c in range(1, 15))
        height_points = max(sum(ws.row_dimensions[r].height or 15 for r in range(p * (rows + 2) + 1, (p + 1) * (rows + 2) + 1)) for p in range(pages))
        scale = int(min(1, printable_width / width_points, printable_height / height_points) * 98)
        if scale < 10:
            raise AppError("参考レイアウトが大きすぎます。1ブロックの行数を減らしてください。")
        ws.page_setup.scale = scale
        ws.sheet_properties.pageSetUpPr.fitToPage = False
        ws.sheet_properties.pageSetUpPr.autoPageBreaks = False
        ws.print_area = f"A1:N{pages * (rows + 2)}"
        ws.sheet_view.showGridLines = False
        temp = None
        try:
            with NamedTemporaryFile(dir=path.parent, suffix=".xlsx", delete=False) as f:
                temp = Path(f.name)
            wb.save(temp)
            temp.replace(path)
        finally:
            if temp and temp.exists():
                temp.unlink()
    finally:
        wb.close()
        if template:
            template.close()
    return pages
