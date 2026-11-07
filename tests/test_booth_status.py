"""Plans count only actual machine/method pairs; display and print share them."""

from datetime import date
import json
import sys

import pytest
from PySide6.QtCore import QDate, Qt
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtPdf import QPdfDocument
from PySide6.QtPrintSupport import QPrinter
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from prize_swap.booth_style import METRIC_COLORS
from prize_swap.core import METHODS, Product, load_session, save_session
from prize_swap.methods import (
    active_assignments,
    aggregate_booths,
    allocation_status,
    booth_report,
    booth_totals,
    booth_value,
    editor_state,
    total_booths,
)
from prize_swap.methods_editor import MethodsCombo
from prize_swap.pdf_validation import normalized_pdf_text, require_searchable_pdf
from prize_swap.printing import configured_printer, draw_page, plan_pages, render_document, save_pdf
from prize_swap.ui import Window

DAY = date(2026, 11, 5)
IDS = ["20098右", "20083左", "20103右", "20104左"]


@pytest.mark.parametrize("method", ["橋渡し", "リング", "フック"])
@pytest.mark.parametrize("assigned,pending,excess", [(0, 3, 0), (1, 2, 0), (3, 0, 0), (4, 0, 1)])
def test_three_planned_counts_only_unique_machines_with_methods(method, assigned, pending, excess):
    p = Product(
        DAY,
        "商品",
        machines=IDS + [IDS[0], "20999右"],
        planned_booths={method: 3},
        machine_methods={n: method for n in IDS[:assigned]},
    )
    p.machine_methods["20999右"] = "  "
    row = booth_totals(p)[0]
    assert (row.planned, row.assigned, row.unassigned, row.excess) == (3, assigned, pending, excess)
    assert allocation_status(p).assigned == assigned
    assert allocation_status(p).registered == 5
    assert p.planned_booths[method] == 3


def test_unassigned_methods_are_not_inferred_from_registered_numbers():
    p = Product(
        DAY,
        "商品",
        machines=IDS,
        planned_booths={"橋渡し": 2, "リング": 1, "フック": 3},
        machine_methods={IDS[0]: None, IDS[1]: "", IDS[2]: "\t"},
    )
    rows = booth_totals(p)
    assert [(r.method, r.assigned, r.unassigned) for r in rows] == [
        ("橋渡し", 0, 2),
        ("リング", 0, 1),
        ("フック", 0, 3),
    ]
    total = total_booths(rows)
    assert (total.planned, total.assigned, total.unassigned, total.excess) == (6, 0, 6, 0)
    assert not any(active_assignments(p).values())
    p.machine_methods[IDS[0]] = " フック "
    assert booth_totals(p)[2].unassigned == 2
    assert p.planned_booths["フック"] == 3


def test_unknown_plan_is_shown_as_unknown_instead_of_zero_difference():
    p = Product(
        DAY, "旧商品", methods=["その他"], machines=[IDS[0]], machine_methods={IDS[0]: "その他"}
    )
    row = booth_totals(p)[0]
    assert booth_value(row, "planned") == booth_value(row, "unassigned") == "未設定"
    assert booth_value(row, "assigned") == "1"
    assert "済1 未? 超?" in booth_report(p)[0]


def test_global_shortages_and_excess_remain_per_product():
    p = Product(
        DAY,
        "商品A",
        machines=IDS,
        planned_booths={"フック": 3},
        machine_methods=dict.fromkeys(IDS, "フック"),
    )
    q = Product(DAY, "商品B", machines=IDS, planned_booths={"フック": 3})
    excluded = Product(DAY, "除外", planned_booths={"フック": 99}, included=False)
    rows = aggregate_booths([p, q, excluded])
    assert (rows[0].planned, rows[0].assigned, rows[0].unassigned, rows[0].excess) == (6, 4, 3, 1)


@pytest.fixture
def window(tmp_path):
    w = Window(layout_storage=tmp_path / "floor.json")
    w.products = [
        Product(
            DAY,
            "ブース商品",
            machines=IDS[:2],
            planned_booths={"橋渡し": 2, "リング": 1, "フック": 3},
        )
    ]
    w.day.setDate(QDate(2026, 11, 5))
    w.refresh()
    w.show()
    QApplication.processEvents()
    yield w
    w.dirty = False
    w.close()
    QApplication.processEvents()


def open_editor(window):
    index = window.proxy.index(0, 7)
    window.table.scrollTo(index)
    window.table.setCurrentIndex(index)
    QApplication.processEvents()
    editor = next(e for e in window.table.viewport().findChildren(MethodsCombo) if e.isVisible())
    editor.showPopup()
    QApplication.processEvents()
    return editor


def test_inline_and_global_numeric_tables_update_immediately_and_use_metric_colours(window):
    assert METHODS == ["橋渡し", "リング", "フック"]
    editor = open_editor(window)
    assert list(editor.rows) == METHODS
    assert editor.metric_labels["橋渡し"]["unassigned"].text() == "2"
    assert editor.metric_labels["リング"]["unassigned"].text() == "1"
    assert editor.total_metrics["assigned"].text() == "0"
    assert editor.total_metrics["unassigned"].text() == "6"
    editor.tabs.setCurrentIndex(1)
    combo = editor.assignment_editors[IDS[0]]
    assert [combo.itemData(i) for i in range(combo.count())] == [None, *METHODS]
    combo.setCurrentIndex(combo.findData("フック"))
    QApplication.processEvents()
    assert editor.metric_labels["フック"]["assigned"].text() == "1"
    assert editor.metric_labels["フック"]["unassigned"].text() == "2"
    assert editor.total_metrics["unassigned"].text() == "5"
    assert window.products[0].machine_methods[IDS[0]] == "フック"
    assert window.model.data(window.model.index(0, 7)).startswith("割当1／未割当5")
    assert window.table.columnWidth(7) >= 220
    assert window.summary_table.item(3, 0).text() == "合計"
    assert window.summary_table.item(3, 2).text() == "1"
    assert window.summary_table.item(3, 3).text() == "5"
    for field, labels in (
        ("assigned", editor.total_metrics),
        ("unassigned", editor.total_metrics),
        ("excess", editor.total_metrics),
    ):
        from PySide6.QtGui import QPalette

        labels[field].ensurePolished()
        assert labels[field].property("metricRole") == field
        assert (
            labels[field].palette().color(QPalette.ColorRole.Window).name()
            == METRIC_COLORS[field][0].lower()
        )
    for r in range(window.summary_table.rowCount()):
        for c, field in ((2, "assigned"), (3, "unassigned"), (4, "excess")):
            item = window.summary_table.item(r, c)
            assert item.background().color().name() == METRIC_COLORS[field][0].lower()
            assert item.foreground().color().name() == METRIC_COLORS[field][1].lower()
    editor.hidePopup()


def test_custom_method_can_be_added_planned_and_assigned_in_same_popup(window):
    editor = open_editor(window)
    previous_metric = editor.metric_labels["橋渡し"]["assigned"]
    editor.build_rows()
    editor.update_status()
    assert not previous_metric.isVisible()  # Deferred old labels must not cover the table.
    editor.custom_name.setText("アーム輪")
    QTest.keyClick(editor.custom_name, Qt.Key.Key_Return)
    QApplication.processEvents()
    editor.rows["アーム輪"][1].setValue(3)
    editor.tabs.setCurrentIndex(1)
    combo = editor.assignment_editors[IDS[0]]
    assert "自由記載" in combo.itemText(combo.findData("アーム輪"))
    combo.setCurrentIndex(combo.findData("アーム輪"))
    QApplication.processEvents()
    p = window.products[0]
    assert p.planned_booths["アーム輪"] == 3
    assert p.machine_methods[IDS[0]] == "アーム輪"
    assert editor.metric_labels["アーム輪"]["unassigned"].text() == "2"
    editor.hidePopup()


def test_old_methods_are_retained_as_custom_with_plans_and_assignments(tmp_path):
    legacy = ["直置き", "吊り下げ", "たこ焼き", "その他"]
    record = dict(
        day=DAY.isoformat(),
        name="旧商品",
        units=40,
        cases=2,
        machines=IDS,
        methods=legacy,
        planned_booths=dict.fromkeys(legacy, 3),
        machine_methods=dict(zip(IDS, legacy)),
    )
    path = tmp_path / "旧作業.json"
    path.write_text(
        json.dumps(dict(version=1, selected_day=DAY.isoformat(), products=[record])),
        encoding="utf-8",
    )
    products, _, _ = load_session(path)
    p = products[0]
    assert p.methods == legacy and p.planned_booths == dict.fromkeys(legacy, 3)
    assert p.machine_methods == dict(zip(IDS, legacy))
    assert (p.name, p.units, p.cases) == ("旧商品", 40, 2)
    editor = MethodsCombo()
    editor.set_allocations(editor_state(p), p.machines)
    for name in legacy:
        assert "自由記載" in editor.rows[name][0].text()
        assert all(combo.findData(name) >= 0 for combo in editor.assignment_editors.values())
    assert all(r.unassigned == 2 for r in booth_totals(p))
    restored = tmp_path / "新作業.json"
    save_session(restored, products, DAY, "")
    assert load_session(restored)[0] == products
    editor.close()


def test_print_report_keeps_plans_name_once_and_colours_and_monochrome_text(tmp_path):
    p = Product(
        DAY,
        "ブース検証景品",
        40,
        1,
        machines=IDS[:2],
        planned_booths={"フック": 3},
        machine_methods=dict.fromkeys(IDS[:2], "フック"),
    )
    colour = tmp_path / "カラー.pdf"
    gray = tmp_path / "白黒.pdf"
    plan = save_pdf(colour, [p], DAY, mode="standard")
    text = normalized_pdf_text(require_searchable_pdf(colour)["pages"][0]["text"])
    assert text.count(p.name) == 1
    assert "フック3ブース済2未1超0合計3ブース済2未1超0" in text
    assert "割当済み" in text and "未割当" in text and "超過" in text
    assert p.planned_booths == {"フック": 3}
    image = QImage(round(plan.width), round(plan.height), QImage.Format.Format_ARGB32)
    image.fill(QColor("white"))
    painter = QPainter(image)
    draw_page(painter, plan, 0, DAY)
    painter.end()
    pixels = bytes(image.bits())
    for background, _ in METRIC_COLORS.values():
        assert pixels.count(QColor(background).rgba().to_bytes(4, sys.byteorder)) > 4
    printer = configured_printer(gray)
    printer.setColorMode(QPrinter.ColorMode.GrayScale)
    render_document(printer, [p], DAY, mode="standard")
    assert normalized_pdf_text(require_searchable_pdf(gray)["pages"][0]["text"]) == text
    doc = QPdfDocument()
    assert doc.load(str(colour)) == QPdfDocument.Error.None_
    assert doc.pagePointSize(0).width() > doc.pagePointSize(0).height()
    assert not doc.render(0, doc.pagePointSize(0).toSize()).isNull()
    doc.close()
