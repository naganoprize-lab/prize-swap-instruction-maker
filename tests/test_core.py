import hashlib
import json
from datetime import date, datetime
from zipfile import ZipFile, ZIP_DEFLATED

import pytest
from openpyxl import Workbook, load_workbook
from openpyxl.styles import PatternFill, Border, Side

from prize_swap.core import (
    AppError,
    HEADERS,
    Product,
    for_day,
    load_session,
    machine_numbers,
    parse_date,
    read_products,
    save_session,
)
from prize_swap.exporter import PINK, export_excel

DAY = date(2026, 11, 5)


@pytest.fixture
def source(tmp_path):
    path = tmp_path / "日本語 入荷.xlsx"
    wb = Workbook()
    ws = wb.active
    ws.title = "縦"
    ws.append(HEADERS)
    ws.append([datetime(2026, 11, 5, 13, 30), "景品A", 24, 2, "メーカーA"])
    ws.append(["2026/11/06", "景品B", 12, 1, "メーカーB"])
    ws.append([datetime(2026, 11, 5, 13, 30), "景品A", 24, 2, "メーカーA"])
    ws.append(["不正", "除外", 1, 1, ""])
    ws.append([DAY, "異常数量", -1, "文字列", ""])
    ws.append([None] * 5)
    ws.append([DAY, None, 1, 1, ""])
    template = wb.create_sheet("入れ替え指示書")
    template.column_dimensions["C"].width = 44
    template.row_dimensions[3].height = 55
    template["C3"].fill = PatternFill("solid", fgColor="FFFFFF00")
    template["C3"].border = Border(bottom=Side(style="double"))
    wb.save(path)
    wb.close()
    return path


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_xlsx_dates_quantities_duplicates(source):
    products, warnings = read_products(source)
    assert len(products) == 4
    selected = for_day(products, DAY)
    assert len(selected) == 3
    assert (selected[0].units, selected[0].cases, selected[0].maker) == (24, 2, "メーカーA")
    assert (selected[2].units, selected[2].cases) == ("", "")
    assert len(warnings) == 5
    assert for_day(products, date(2020, 1, 1)) == []


def test_macro_enabled_package(source, tmp_path):
    # Synthetic macro-enabled OOXML fixture; macro bytes are never executed.
    target = tmp_path / "マクロ付き.xlsm"
    with ZipFile(source) as src, ZipFile(target, "w", ZIP_DEFLATED) as dst:
        for name in src.namelist():
            content = src.read(name)
            if name == "[Content_Types].xml":
                content = content.replace(
                    b"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml",
                    b"application/vnd.ms-excel.sheet.macroEnabled.main+xml",
                )
            dst.writestr(name, content)
        dst.writestr("xl/vbaProject.bin", b"synthetic-vba-fixture")
    before = digest(target)
    products, _ = read_products(target)
    out = tmp_path / "マクロなし.xlsx"
    export_excel(out, for_day(products, DAY), DAY, target)
    assert digest(target) == before
    with ZipFile(out) as z:
        assert "xl/vbaProject.bin" not in z.namelist()
        assert b"macroEnabled" not in z.read("[Content_Types].xml")


@pytest.mark.parametrize(
    "raw",
    [datetime(2026, 11, 5, 23, 59), DAY, "2026/11/05", "2026年11月05日", "2026-11-05T12:34:00"],
)
def test_parse_date(raw):
    assert parse_date(raw) == DAY


@pytest.mark.parametrize("value", [None, "abc", "2026/99/99", True])
def test_invalid_date(value):
    assert parse_date(value) is None


def test_machines():
    assert machine_numbers("20085右,20098左\n20085右，123") == ["20085右", "20098左", "123"]
    with pytest.raises(AppError):
        machine_numbers("123中央")


def test_export_style_values_and_source_unchanged(source, tmp_path):
    before = digest(source)
    p = Product(
        DAY,
        "長い商品名",
        12,
        3,
        machines=["20085右", "20098左"],
        methods=["橋渡し", "自由入力"],
        priority=True,
        limit="トータル⑩",
    )
    normal = Product(DAY, "通常", order=1)
    out = tmp_path / "指示書 日本語.xlsx"
    assert export_excel(out, [p, normal], DAY, source) == 1
    wb = load_workbook(out)
    ws = wb.active
    assert ws["A3"].value == "① 20085右"
    assert ws["A4"].value == "② 20098左"
    assert ws["B3"].value is None
    assert ws["C3"].value == "橋渡し：予定未設定\n自由入力：予定未設定"
    assert ws["G3"].value == "トータル⑩"
    assert ws["E3"].value == 12 and ws["F3"].value == 3
    assert all(ws.cell(3, c).fill.fgColor.rgb == PINK for c in range(1, 8))
    assert ws["D5"].fill.fgColor.rgb == "00000000"
    assert ws["D3"].alignment.wrap_text
    assert ws.column_dimensions["D"].width == 44
    assert ws.row_dimensions[3].height >= 55
    assert ws["D3"].border.bottom.style == "double"
    assert ws.page_setup.orientation == "landscape"
    assert ws.page_setup.paperSize == int(ws.PAPERSIZE_A4)
    assert digest(source) == before
    wb.close()


@pytest.mark.parametrize(
    "layout, expected",
    [
        ("column", {"D3": "商品0", "D4": "商品1", "K3": "商品2", "K4": "商品3", "D7": "商品4"}),
        ("row", {"D3": "商品0", "K3": "商品1", "D4": "商品2", "K4": "商品3", "D7": "商品4"}),
    ],
)
def test_layout_and_pages(tmp_path, layout, expected):
    products = [Product(DAY, f"商品{i}", order=i) for i in range(5)]
    out = tmp_path / "out.xlsx"
    assert export_excel(out, products, DAY, rows=2, layout=layout) == 2
    wb = load_workbook(out)
    ws = wb.active
    for cell, value in expected.items():
        assert ws[cell].value == value
    assert [b.id for b in ws.row_breaks.brk] == [4]
    assert ws.print_area == "'入れ替え指示書'!$A$1:$N$7"
    wb.close()


def test_order_and_exclusion_and_text_injection(tmp_path):
    products = [
        Product(DAY, "最後", order=10),
        Product(DAY, "除外", included=False),
        Product(DAY, "=1+1", order=-1),
    ]
    out = tmp_path / "out.xlsx"
    export_excel(out, products, DAY)
    wb = load_workbook(out)
    assert wb.active["D3"].value == "=1+1"
    assert wb.active["D3"].data_type == "s"
    assert wb.active["D4"].value == "最後"
    assert wb.active["D5"].value is None
    wb.close()


def test_source_overwrite_rejected(source):
    before = digest(source)
    with pytest.raises(AppError):
        export_excel(source, [], DAY, source)
    with pytest.raises(AppError):
        save_session(source, [], DAY, source)
    assert digest(source) == before


def test_session_round_trip(tmp_path, source):
    products, _ = read_products(source)
    products[0].machines = ["20085右", "20098左"]
    products[0].methods = ["橋渡し", "独自"]
    products[0].priority = True
    products[0].limit = "各③"
    products[0].included = False
    products[0].order = 17
    products[0].name = "編集済み"
    path = tmp_path / "作業.json"
    save_session(path, products, DAY, source, "row", 12)
    restored, selected, metadata = load_session(path)
    assert restored == products
    assert selected == DAY
    assert metadata["layout"] == "row"
    assert metadata["rows_per_page"] == 12
    # Saving twice is safe and repeatable.
    save_session(path, restored, DAY, source, "row", 12)
    assert load_session(path)[0] == products


@pytest.mark.parametrize(
    "payload", ["{}", '{"version":99}', "[]", '{"version":1,"selected_day":"bad"}']
)
def test_invalid_session(tmp_path, payload):
    path = tmp_path / "invalid.json"
    path.write_text(payload)
    with pytest.raises(AppError):
        load_session(path)


def test_sheet_and_headers_validation(tmp_path):
    path = tmp_path / "bad.xlsx"
    wb = Workbook()
    wb.save(path)
    with pytest.raises(AppError, match="「縦」シート"):
        read_products(path)
    wb.active.title = "縦"
    wb.active.append(["wrong"])
    wb.save(path)
    with pytest.raises(AppError, match="見出し"):
        read_products(path)
    wb.close()
