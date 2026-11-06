"""Independent plans, machine assignments, and real merged-cell output."""

from datetime import date
import json

import pytest
from openpyxl import load_workbook
from PySide6.QtCore import QDate, QPoint, Qt
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtPrintSupport import QPrintDialog
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox

from prize_swap.core import AppError, Product, load_session, save_session
from prize_swap.exporter import export_excel
from prize_swap.floor_data import Machine
from prize_swap.labels_data import LabelEntry
from prize_swap.labels_printing import save_labels_pdf
from prize_swap.methods import (
    active_assignments,
    aggregate_booths,
    allocation_status,
    booth_totals,
    editor_state,
    format_allocations,
    format_plans,
    plans,
    set_allocations,
    sync_machine_methods,
)
from prize_swap.pdf_validation import normalized_pdf_text, require_searchable_pdf
from prize_swap.printing import (
    PADDING,
    TITLE_HEIGHT,
    configured_printer,
    draw_page,
    plan_pages,
    render_document,
    save_pdf,
)
from prize_swap.table import MethodsCombo, ProductModel
from prize_swap.ui import Window

DAY = date(2026, 11, 5)


def product():
    return Product(
        DAY,
        "鬼滅の刃 フィギュア",
        40,
        1,
        machines=["20098右", "20083左", "20103右"],
        planned_booths={"橋渡し": 2, "リング": 1},
        machine_methods={"20098右": "橋渡し", "20083左": "リング", "20103右": "橋渡し"},
    )


def compact(value):
    return normalized_pdf_text(value)


def test_planned_three_with_one_machine_and_method_summary():
    p = Product(
        DAY,
        "クロミ ブランケット",
        machines=["20085右"],
        planned_booths={"リング": 3},
        machine_methods={"20085右": "リング"},
    )
    assert format_plans(p) == "リング 3ブース"
    assert format_allocations(p) == "リング×1"
    assert booth_totals(p)[0].unassigned == 2
    assert allocation_status(p).registered == 1
    p.machines.append("20086左")
    sync_machine_methods(p)
    assert p.planned_booths == {"リング": 3}
    assert p.machine_methods["20086左"] is None
    assert LabelEntry(p).copies == 2
    assert format_plans(p) == "リング 3ブース"


def test_machine_assignments_and_separate_per_product_and_global_totals():
    p = product()
    assert format_allocations(p) == "橋渡し×2、リング×1"
    assert format_plans(p) == "橋渡し 2ブース\nリング 1ブース"
    assert allocation_status(p).unassigned == allocation_status(p).excess == 0
    p.planned_booths = {"橋渡し": 3, "リング": 0}
    q = Product(
        DAY,
        "商品B",
        machines=["30001左", "30002右"],
        planned_booths={"橋渡し": 1},
        machine_methods={"30001左": "橋渡し", "30002右": "橋渡し"},
    )
    excluded = Product(DAY, "除外", planned_booths={"橋渡し": 99}, included=False)
    totals = {r.method: r for r in aggregate_booths([p, q, excluded])}
    assert (totals["橋渡し"].planned, totals["橋渡し"].assigned) == (4, 4)
    # A shortage in one product must not cancel another product's excess.
    assert totals["橋渡し"].unassigned == totals["橋渡し"].excess == 1
    assert totals["リング"].excess == 1
    assert p.planned_booths["リング"] == 0


def test_machine_edit_archives_removed_assignments_and_restores_exact_ids():
    p = product()
    model = ProductModel()
    model.replace([p])
    assert model.setData(model.index(0, 6), "20098右、20083左;20999右")
    assert p.machine_methods == {
        "20098右": "橋渡し",
        "20083左": "リング",
        "20999右": None,
    }
    assert p.retired_machine_methods == {"20103右": "橋渡し"}
    assert p.planned_booths == {"橋渡し": 2, "リング": 1}
    assert allocation_status(p).unmapped == 1
    assert model.setData(model.index(0, 6), "20098右，20083左\n20103右")
    assert active_assignments(p) == product().machine_methods
    assert p.retired_machine_methods == {"20999右": None}


def test_session_roundtrip_preserves_independent_plans_assignments_and_print_options(
    tmp_path,
):
    p = product()
    p.planned_booths["リング"] = 3
    path = tmp_path / "ブース予定.json"
    save_session(path, [p], DAY, "", layout="row", rows_per_page=12)
    payload = json.loads(path.read_text())
    assert payload["products"][0]["assigned_machine_count"] == 3
    # A stale cache must not overwrite the real IDs, plans or assignments.
    payload["products"][0]["assigned_machine_count"] = 999
    path.write_text(json.dumps(payload), encoding="utf-8")
    products, day, metadata = load_session(path)
    assert products == [p] and day == DAY
    assert products[0].assigned_machine_count == 3
    assert metadata["layout"] == "row" and metadata["rows_per_page"] == 12


@pytest.mark.parametrize("legacy", [["橋渡し", "リング"], "橋渡し、リング"])
def test_legacy_names_keep_unknown_plan_and_no_inferred_assignment(tmp_path, legacy):
    path = tmp_path / "旧データ.json"
    path.write_text(
        json.dumps(
            dict(
                version=1,
                selected_day=DAY.isoformat(),
                products=[
                    dict(
                        day=DAY.isoformat(),
                        name="旧商品",
                        methods=legacy,
                        machines=["20085右", "20098左"],
                    )
                ],
            )
        ),
        encoding="utf-8",
    )
    products, _, _ = load_session(path)
    p = products[0]
    assert plans(p) == {"橋渡し": None, "リング": None}
    assert active_assignments(p) == {"20085右": None, "20098左": None}
    assert "予定未設定" in format_plans(p)
    assert allocation_status(p).unknown == 2


@pytest.mark.parametrize("invalid", [-1, 1.5, True, "3"])
def test_invalid_plan_is_rejected_without_changing_assignments(invalid):
    p = product()
    original = editor_state(p)
    with pytest.raises(AppError):
        set_allocations(p, {"リング": invalid})
    assert editor_state(p) == original


@pytest.fixture
def window(tmp_path):
    w = Window(layout_storage=tmp_path / "floor-auto.json")
    w.products = [product()]
    w.day.setDate(QDate(2026, 11, 5))
    w.refresh()
    w.show()
    QApplication.processEvents()
    yield w
    w.dirty = False
    w.close()
    QApplication.processEvents()


def test_real_inline_popup_keyboard_counts_and_machine_assignment(window):
    index = window.proxy.index(0, 7)
    window.table.scrollTo(index)
    window.table.setCurrentIndex(index)
    QApplication.processEvents()
    editor = next(e for e in window.table.viewport().findChildren(MethodsCombo) if e.isVisible())
    assert not editor.isWindow()
    editor.showPopup()
    QApplication.processEvents()
    spin = editor.rows["リング"][1]
    spin.setFocus()
    QTest.keyClick(spin, Qt.Key.Key_Up)
    QApplication.processEvents()
    p = window.products[0]
    assert p.planned_booths["リング"] == 2
    assert p.machine_methods["20083左"] == "リング"
    editor.tabs.setCurrentIndex(1)
    combo = editor.assignment_editors["20083左"]
    combo.setFocus()
    QTest.keyClick(combo, Qt.Key.Key_Home)
    QTest.keyClick(combo, Qt.Key.Key_Down)  # 未設定 -> 橋渡し
    QApplication.processEvents()
    assert p.machine_methods["20083左"] == "橋渡し"
    assert p.planned_booths["リング"] == 2
    assert "橋渡し×3" in window.model.data(window.model.index(0, 7))
    editor.hidePopup()
    window.summary_toggle.setChecked(True)
    QApplication.processEvents()
    assert window.summary_table.rowCount() == 2
    assert window.table.height() > window.height() * 0.3


def test_legacy_popup_focus_does_not_turn_unknown_into_zero(qt_app):
    editor = MethodsCombo()
    p = Product(DAY, "旧商品", methods=["橋渡し"])
    editor.set_allocations(editor_state(p), [])
    editor.show()
    editor.showPopup()
    spin = editor.rows["橋渡し"][1]
    spin.setFocus()
    QTest.keyClick(spin, Qt.Key.Key_Tab)
    QApplication.processEvents()
    assert editor.counts()["橋渡し"] is None
    editor.hidePopup()
    editor.close()


@pytest.mark.parametrize("layout", ["column", "row"])
def test_excel_real_vertical_merges_individual_methods_and_plans_once(tmp_path, layout):
    p = product()
    p.planned_booths["リング"] = 3
    p.priority = True
    path = tmp_path / "結合指示書.xlsx"
    export_excel(path, [p], DAY, layout=layout)
    wb = load_workbook(path)
    ws = wb.active
    assert [ws.cell(r, 1).value for r in (3, 4, 5)] == p.machines
    assert [ws.cell(r, 2).value for r in (3, 4, 5)] == ["橋渡し", "リング", "橋渡し"]
    assert ws["C3"].value == "橋渡し 2ブース\nリング 3ブース"
    assert ws["D3"].value == "【優先入替】\n" + p.name
    for col in "CDEFG":
        assert f"{col}3:{col}5" in ws.merged_cells
        assert ws[f"{col}4"].value is None
    assert not any(str(r).startswith("A3:") or str(r).startswith("B3:") for r in ws.merged_cells)
    assert ws["D3"].font.bold and ws["D3"].alignment.horizontal == "center"
    assert ws["C3"].fill.fgColor.rgb.endswith("FCE4EC")
    assert ws.page_setup.orientation == "landscape" and int(ws.page_setup.paperSize) == int(
        ws.PAPERSIZE_A4
    )
    wb.close()


def test_excel_long_name_fits_without_exceeding_physical_row_limit(tmp_path):
    p = Product(
        DAY,
        "日本語の商品名" * 12,
        machines=["20085右"],
        planned_booths={"リング": 3},
        machine_methods={"20085右": "リング"},
    )
    path = tmp_path / "長い商品名.xlsx"
    pages = export_excel(path, [p], DAY)
    wb = load_workbook(path)
    ws = wb.active
    assert pages == 1
    assert ws["D3"].value == p.name
    assert ws["C3"].value == "リング 3ブース"
    assert any(r.min_col == 4 and r.min_row == 3 and r.max_row > 3 for r in ws.merged_cells)
    assert all((r.height or 0) <= 409 for r in ws.row_dimensions.values())
    assert sum(cell.value == "20085右" for row in ws for cell in row) == 1
    wb.close()


def test_pdf_plans_are_once_per_product_and_merged_geometry_is_visible(tmp_path):
    p = product()
    p.planned_booths["リング"] = 3
    path = tmp_path / "予定ブース.pdf"
    plan = save_pdf(path, [p], DAY)
    text = compact(require_searchable_pdf(path)["pages"][0]["text"])
    assert text.count(compact(p.name)) == 1
    assert text.count("橋渡し2ブース") == text.count("リング3ブース") == 1
    for number in p.machines:
        assert text.count(number) == 1
    row = plan.pages[0][0]
    group = row.groups[0]
    assert group.machines == p.machines
    assert len(group.machine_cells) == 3
    assert group.cells[2].text == "橋渡し 2ブース\nリング 3ブース"
    # Actual painting: the machine-row separator stops before merged plan/name.
    image = QImage(round(plan.width), round(plan.height), QImage.Format.Format_ARGB32)
    image.fill(QColor("white"))
    painter = QPainter(image)
    draw_page(painter, plan, 0, DAY)
    painter.end()
    y = round(TITLE_HEIGHT + plan.header_height + row.heights[0])
    assert image.pixelColor(3, y).lightness() < 150
    plan_left = sum(plan.widths[:2])
    assert image.pixelColor(round(plan_left + 2), y).lightness() > 200
    # Every measured text line fits its respective merged or individual cell.
    for block in group.cells[2:]:
        assert block.height + 2 * PADDING <= row.height + 0.01
        for i in range(block.layout.lineCount()):
            line = block.layout.lineAt(i)
            assert line.naturalTextWidth() <= block.width + 0.01


@pytest.mark.parametrize("layout", ["column", "row"])
def test_many_products_stay_atomic_without_loss_and_oversized_groups_split(tmp_path, layout):
    products = []
    for i in range(24):
        ids = [f"{30000+i*10+j}右" for j in range(1 + i % 5)]
        products.append(
            Product(
                DAY,
                f"[BLOCK{i:02}] " + "日本語の商品名" * 3,
                order=i,
                machines=ids,
                planned_booths={"橋渡し": len(ids) + 1},
                machine_methods=dict.fromkeys(ids, "橋渡し"),
            )
        )
    path = tmp_path / "複数ページ.pdf"
    plan = save_pdf(path, products, DAY, rows=8, layout=layout)
    assert len(plan.pages) > 2 and not plan.warnings
    groups = [g for page in plan.pages for pair in page for g in pair.groups if g]
    assert len(groups) == len(products)
    assert sorted(id(g.product) for g in groups) == sorted(id(p) for p in products)
    for g in groups:
        assert g.machines == g.product.machines and not g.continuation
    text = compact("".join(p["text"] for p in require_searchable_pdf(path)["pages"]))
    for i, p in enumerate(products):
        assert text.count(f"[BLOCK{i:02}]") == 1
        for number in p.machines:
            assert text.count(number) == 1
    huge = Product(
        DAY,
        "継続する商品",
        machines=[f"{50000+i}左" for i in range(65)],
        planned_booths={"リング": 65},
    )
    split = plan_pages([huge], 808, 561, layout=layout)
    fragments = [g for page in split.pages for pair in page for g in pair.groups if g]
    assert split.warnings and len(fragments) > 1
    assert [n for g in fragments for n in g.machines] == huge.machines
    assert not fragments[0].continuation and all(g.continuation for g in fragments[1:])


def test_labels_warn_on_plan_difference_but_print_only_names(window, tmp_path, monkeypatch):
    p = window.products[0]
    p.planned_booths["リング"] = 3
    window.labels.sync()
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: warnings.append(args[2]))
    window.labels.prepare()
    assert any("差分" in text or "未割当" in text for text in warnings)
    assert window.labels.model.entries[0].copies == 3
    path = tmp_path / "ラベル.pdf"
    save_labels_pdf(path, window.labels.model.entries)
    text = compact("".join(p["text"] for p in require_searchable_pdf(path)["pages"]))
    assert text.count(compact(p.name)) == 3
    assert "リング" not in text and "ブース" not in text
    assert all(n not in text for n in p.machines)


def test_direct_print_dialog_uses_same_machine_and_plan_renderer(window, tmp_path, monkeypatch):
    path = tmp_path / "直接印刷経路.pdf"
    import prize_swap.ui as ui

    monkeypatch.setattr(ui, "configured_printer", lambda: configured_printer(path))
    monkeypatch.setattr(QPrintDialog, "exec", lambda self: QDialog.DialogCode.Accepted)
    window.print_direct()  # Dialog acceptance -> real common QPrinter/QPainter.
    text = compact(require_searchable_pdf(path)["pages"][0]["text"])
    assert "橋渡し2ブース" in text and "リング1ブース" in text
    assert text.count(compact(window.products[0].name)) == 1
    saved = tmp_path / "PDF保存経路.pdf"
    save_pdf(saved, window.products, DAY)
    saved_text = compact(require_searchable_pdf(saved)["pages"][0]["text"])
    assert saved_text == text
    # Compare actual page rendering as well as text; metadata timestamps differ.
    from PySide6.QtPdf import QPdfDocument
    from PySide6.QtCore import QSize
    images = []
    for filename in (saved, path):
        document = QPdfDocument()
        assert document.load(str(filename)) == QPdfDocument.Error.None_
        images.append(document.render(0, QSize(1684, 1190)))
        assert not images[-1].isNull()
        document.close()
    assert images[0] == images[1]


def test_floor_optional_method_display_and_state(window):
    window.floor.add_machine(Machine(number="20098右", x=0, y=0))
    assert not window.floor.show_methods.isChecked()
    window.floor.show_methods.setChecked(True)
    state = window.floor.state()
    assert state["show_methods"] is True
    assert any("橋渡し" in item.toolTip() for item in window.floor.scene.items())


def test_previous_count_mapping_migrates_without_guessing_machine_methods(tmp_path):
    path = tmp_path / "旧台数.json"
    path.write_text(
        json.dumps(
            dict(
                version=1,
                selected_day=DAY.isoformat(),
                products=[
                    dict(
                        day=DAY.isoformat(),
                        name="旧台数",
                        methods=["リング"],
                        method_counts={"リング": 3},
                        machines=["20085右"],
                    )
                ],
            )
        ),
        encoding="utf-8",
    )
    products, _, _ = load_session(path)
    assert products[0].planned_booths == {"リング": 3}
    assert products[0].assigned_machine_count == 1
    assert active_assignments(products[0]) == {"20085右": None}


@pytest.mark.parametrize("layout", ["column", "row"])
def test_excel_multipage_oversized_group_keeps_all_machine_rows(tmp_path, layout):
    p = Product(
        DAY, "継続商品", machines=[f"{50000+i}右" for i in range(65)], planned_booths={"リング": 70}
    )
    p.machine_methods = dict.fromkeys(p.machines, "リング")
    path = tmp_path / "継続商品.xlsx"
    pages = export_excel(path, [p], DAY, layout=layout)
    wb = load_workbook(path)
    ws = wb.active
    emitted = [
        ws.cell(r, c).value
        for r in range(3, ws.max_row + 1)
        for c in (1, 8)
        if ws.cell(r, c).value in p.machines
    ]
    assert pages > 1
    assert sorted(emitted) == sorted(p.machines)
    assert len(emitted) == len(set(emitted))
    assert len(ws.row_breaks.brk) == pages - 1
    assert any("継続商品あり" in str(cell.value) for row in ws for cell in row)
    for cell_range in ws.merged_cells:
        if cell_range.min_col in (3, 10) and cell_range.min_row > 2:
            value = ws.cell(cell_range.min_row, cell_range.min_col).value
            assert value == "リング 70ブース"
    wb.close()
