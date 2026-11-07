from copy import deepcopy
from dataclasses import asdict
from datetime import date, timedelta
import hashlib
import json

import pytest
from openpyxl import Workbook, load_workbook
from PySide6.QtCore import QDate
from PySide6.QtWidgets import QApplication, QDialog, QTableWidget, QTabWidget

from prize_swap.core import AppError, Product
from prize_swap.floor import FloorWidget
from prize_swap.floor_data import Machine, save_layout
from prize_swap.floor_details import MachineDetailDialog
from prize_swap.floor_print import FloorPrintOptions, floor_printer
from prize_swap.floor_status import effective_stat, ratio, days_text, filter_status
from prize_swap.sales_analysis import load_analysis, normalize_number, import_summary
from prize_swap.pdf_validation import require_searchable_pdf, normalized_pdf_text

DAY = date(2026, 11, 7)
HEADERS = [
    "管理番号",
    "機器番号",
    "機器",
    "商品コード",
    "商品名",
    "売上金額",
    "稼働日数",
    "平均売上",
    "粗利益",
    "原価率",
    "P/O率",
    "払出数",
    "個当たり",
    "景品種別",
    "景品単価",
    "原価",
]


def report_file(path, rows):
    w = Workbook()
    s = w.active
    s.title = "景品運営分析表"
    s.append(HEADERS)
    for row in rows:
        s.append(row)
    for row in s.iter_rows(min_row=2):
        row[9].number_format = "0.00%"
        row[10].number_format = "0.00%"
    w.save(path)
    w.close()
    return path


def data_row(number="20088-01", sales=30000, average=10000, days=3):
    return [
        None,
        number,
        "機器",
        "99999",
        "売上登録用",
        sales,
        days,
        average,
        20000,
        0.3333,
        0.08,
        20,
        1500,
        "DO_NOT_IMPORT_CATEGORY",
        500,
        10000,
    ]


@pytest.fixture
def floor(tmp_path):
    products = [
        Product(
            DAY,
            "入替予定商品",
            machines=["20088左"],
            planned_booths={"リング": 2},
            machine_methods={"20088左": "リング"},
        )
    ]
    w = FloorWidget(lambda: products, lambda: DAY, tmp_path / "auto.json")
    w.machines = [
        Machine(
            number="20088左",
            product="既存の商品",
            maker="既存メーカー",
            sales=7500,
            average=2500,
            ratio=50,
            po_rate=.20,
            x=60,
            y=80,
            width=260,
            height=180,
            installed_on="2026-10-28",
            operation_method="橋渡し",
        )
    ]
    w.reference_date.setDate(QDate(DAY))
    w.rebuild()
    yield w
    w.timer.stop()
    w.day_timer.stop()
    w.close()


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("20088-01", "20088左"),
        ("20088-02", "20088右"),
        (" 20102-02 ", "20102右"),
        ("20088-03", None),
        ("検索条件", None),
        (2008801, None),
        ("20088-01 extra", None),
    ],
)
def test_machine_number_normalization(raw, expected):
    assert normalize_number(raw) == expected


def test_read_valid_rows_rates_and_ignore_metadata_and_category(tmp_path):
    bad = data_row("20090-01")
    bad[7] = "不正な平均売上"
    path = report_file(
        tmp_path / "景品運営分析表_日本語.xlsx",
        [
            data_row(),
            data_row("20088-02"),
            [None, "検索日時: 2026/11/07", None, None, None, 9999],
            bad,
        ],
    )
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    snapshot = load_analysis(path)
    assert set(snapshot["records"]) == {"20088左", "20088右"}
    values = snapshot["records"]["20088左"]["values"]
    assert values["sales"] == 30000 and values["average"] == 10000 and values["operating_days"] == 3
    assert values["po_rate"] == .08 and values["cost_rate"] == pytest.approx(.3333)
    assert (
        values["gross_profit"] == 20000 and values["dispensed"] == 20 and values["per_item"] == 1500
    )
    assert values["unit_price"] == 500 and values["cost"] == 10000
    assert len(snapshot["errors"]) == 1 and snapshot["ignored_rows"] == 1
    assert "DO_NOT_IMPORT_CATEGORY" not in json.dumps(snapshot, ensure_ascii=False)
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before


def test_latest_values_protected_fields_reference_and_calendar_age(floor, tmp_path):
    path = report_file(tmp_path / "分析.xlsx", [data_row(), data_row("29999-02")])
    before = asdict(floor.machines[0])
    products = deepcopy(floor.products())
    summary = floor.import_analysis_path(path)
    m = floor.machines[0]
    assert summary["read"] == 2 and summary["matched"] == 1 and summary["unmatched"] == ["29999右"]
    for field in (
        "number",
        "product",
        "maker",
        "x",
        "y",
        "width",
        "height",
        "installed_on",
        "operation_method",
        "units",
        "cases",
        "limit",
    ):
        assert getattr(m, field) == before[field]
    assert floor.products() == products
    assert (
        effective_stat(m, "sales") == 30000
        and effective_stat(m, "average") == 10000
        and effective_stat(m, "po_rate") == .08
    )
    assert m.sales == 7500 and m.average == 2500  # Layer 2 remains available as a fallback.
    assert (
        m.reference_average == 5000 and ratio(m) == 200
    )  # H/reference, not F/H or G/calendar days.
    assert effective_stat(m, "operating_days") == 3 and days_text(m, DAY) == "10日"
    assert filter_status(m, "age", DAY)[0] == "#C5A5ED"
    detail = MachineDetailDialog(floor, m)
    assert detail.values["sales"].text() == "30,000" and detail.values["average"].text() == "10,000"
    assert (
        detail.values["po_rate"].text() == "8.00%" and detail.values["operating_days"].text() == "3"
    )
    assert detail.values["days"].text() == "10日"
    assert detail.name.toPlainText() == "既存の商品"
    detail.close()
    floor.import_analysis_path(path)
    assert (
        len(floor.analysis_state["records"]) == 2
        and effective_stat(floor.machines[0], "sales") == 30000
    )
    path2 = report_file(tmp_path / "新しい分析.xlsx", [data_row(average=15000, sales=45000)])
    floor.import_analysis_path(path2)
    assert ratio(floor.machines[0]) == 300 and "新しい分析.xlsx" in floor.analysis_note.text()
    assert len(floor.analysis_state["records"]) == 1


def test_optional_fallback_zero_unknown_reference_and_new_snapshot(floor, tmp_path):
    row = data_row(average=0, sales=0)
    row[10] = None
    floor.import_analysis_path(report_file(tmp_path / "zero.xlsx", [row]))
    m = floor.machines[0]
    assert effective_stat(m, "average") == 0 and effective_stat(m, "sales") == 0 and ratio(m) == 0
    assert effective_stat(m, "po_rate") is None  # Latest missing data stays unknown.
    floor.import_analysis_path(report_file(tmp_path / "別マシン.xlsx", [data_row("29999-01")]))
    assert effective_stat(m, "sales") == 7500 and ratio(m) == 50  # Unmatched: use identified layout values.
    floor.add_machine(Machine(number="29999左", product="追加商品"))
    fresh = floor.machines[-1]
    assert effective_stat(fresh, "sales") == 30000 and fresh.product == "追加商品"
    assert ratio(fresh) is None  # A baseline is not guessed from the imported average.


def test_duplicates_are_never_added_and_conflicts_are_excluded(tmp_path):
    snapshot = load_analysis(report_file(tmp_path / "same.xlsx", [data_row(), data_row()]))
    assert len(snapshot["records"]) == 1 and snapshot["duplicate_rows"] == 1
    snapshot = load_analysis(
        report_file(tmp_path / "conflict.xlsx", [data_row(), data_row(average=5000)])
    )
    assert not snapshot["records"] and len(snapshot["errors"]) == 1


def test_json_roundtrip_and_undo_analysis_import(floor, tmp_path):
    floor.import_analysis_path(
        report_file(tmp_path / "analysis.xlsx", [data_row(), data_row("29999-02")])
    )
    state = floor.state()
    path = tmp_path / "一時保存.json"
    save_layout(path, state)
    restored = FloorWidget(lambda: [], lambda: DAY, path)
    try:
        assert restored.state() == state
        assert (
            effective_stat(restored.machines[0], "sales") == 30000
            and ratio(restored.machines[0]) == 200
        )
        assert restored.analysis_state["records"]["29999右"]["reference_name"] == "売上登録用"
    finally:
        restored.timer.stop()
        restored.day_timer.stop()
        restored.close()
    floor.history.undo()
    assert effective_stat(floor.machines[0], "sales") == 7500 and ratio(floor.machines[0]) == 50
    floor.history.redo()
    assert effective_stat(floor.machines[0], "sales") == 30000 and ratio(floor.machines[0]) == 200


@pytest.mark.parametrize("header", ["別の項目", None])
def test_invalid_header_preserves_current_import_and_result_dialog(
    floor, tmp_path, monkeypatch, header
):
    path = report_file(tmp_path / "analysis.xlsx", [data_row("29999-02")])
    floor.import_analysis_path(path)
    previous = floor.state()
    w = load_workbook(path)
    w.active["H1"] = header
    w.save(path)
    w.close()
    with pytest.raises(AppError, match="ヘッダー"):
        floor.import_analysis_path(path)
    assert floor.state() == previous
    observed = []

    def observe(dialog):
        tabs = dialog.findChild(QTabWidget)
        observed.append([(tabs.widget(i).columnCount(), tabs.widget(i).rowCount()) for i in range(tabs.count())])
        return QDialog.DialogCode.Rejected

    monkeypatch.setattr(QDialog, "exec", observe)
    floor.show_analysis_report()
    assert observed[0][:2] == [(5, 1), (3, 0)]
    assert observed[0][2][0] == 5


def test_pdf_uses_recalculated_ratio_and_separate_age_filter(floor, tmp_path):
    floor.import_analysis_path(report_file(tmp_path / "analysis.xlsx", [data_row(days=1)]))
    floor.set_print_options(FloorPrintOptions(color_filter="ratio"))
    p = tmp_path / "ratio.pdf"
    floor.paint_floor(floor_printer(floor.print_options, p))
    content = normalized_pdf_text("".join(v["text"] for v in require_searchable_pdf(p)["pages"]))
    assert "200.0%" in content and "既存の商品" in content and "売上登録用" not in content
    floor.set_print_options(FloorPrintOptions(color_filter="age"))
    p = tmp_path / "age.pdf"
    plan = floor.paint_floor(floor_printer(floor.print_options, p))
    assert plan.pages[0].cards[0].background == "#C5A5ED"
    content = normalized_pdf_text("".join(v["text"] for v in require_searchable_pdf(p)["pages"]))
    assert "設置日数10日" in content and "設置日数1日" not in content


def test_shared_reference_only_when_consistent():
    from prize_swap.sales_analysis import ensure_references

    group = [
        Machine(average=2000, ratio=100),
        Machine(average=4000, ratio=200),
        Machine(average=0, ratio=0),
    ]
    ensure_references(group)
    assert [m.reference_average for m in group] == [2000, 2000, 2000]
    group = [
        Machine(average=2000, ratio=100),
        Machine(average=4000, ratio=400),
        Machine(average=0, ratio=0),
    ]
    ensure_references(group)
    assert group[2].reference_average is None


def test_direct_print_with_analysis_matches_pdf(floor, tmp_path, monkeypatch):
    import prize_swap.floor as module
    from PySide6.QtWidgets import QMessageBox

    floor.import_analysis_path(report_file(tmp_path / "analysis.xlsx", [data_row()]))
    first = tmp_path / "PDF.pdf"
    floor.paint_floor(floor_printer(floor.print_options, first))
    second = tmp_path / "直接.pdf"

    class Accepted:
        def __init__(self, *args):
            pass

        def setMinMax(self, *args):
            pass

        def setOption(self, *args):
            pass

        def exec(self):
            return QDialog.DialogCode.Accepted

    monkeypatch.setattr(module, "QPrintDialog", Accepted)
    monkeypatch.setattr(module, "floor_printer", lambda options: floor_printer(options, second))
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Yes)
    errors = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: errors.append(args))
    floor.print_floor()
    assert not errors

    def text(path):
        return normalized_pdf_text(
            "".join(p["text"] for p in require_searchable_pdf(path)["pages"])
        )

    assert (
        text(first) == text(second)
        and "200.0%" in text(second)
        and "売上登録用" not in text(second)
    )


def test_import_during_fullscreen_restores_visible_result_note(floor, tmp_path):
    floor.show()
    QApplication.processEvents()
    floor.toggle_fullscreen()
    QApplication.processEvents()
    floor.import_analysis_path(report_file(tmp_path / "full-analysis.xlsx", [data_row()]))
    assert floor.analysis_note.isHidden()
    floor._fullscreen_host.close()
    QApplication.processEvents()
    assert floor.analysis_note.isVisible() and "full-analysis.xlsx" in floor.analysis_note.text()
