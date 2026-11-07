from copy import deepcopy
from dataclasses import asdict
from datetime import date
import hashlib
import pytest
from openpyxl import load_workbook
from prize_swap.sales_analysis import load_analysis, apply_snapshot, validate_snapshot
from prize_swap.performance_validation import reconcile, period_text
from prize_swap.floor_data import Machine, validate_state
from prize_swap.floor_status import effective_stat, ratio
from test_sales_analysis import report_file, data_row


def test_metadata_reordered_headers_and_fraction_units(tmp_path):
    p = report_file(
        tmp_path / "分析_202611070619.xlsx",
        [
            data_row(),
            [None, "期間(自)", "2026/11/01"],
            [None, "期間(至)", "2026/11/06"],
            [None, "検索日時: 2026/11/07 18:19:35"],
        ],
    )
    w = load_workbook(p)
    s = w.active
    for row in s:
        a, b = row[1].value, row[7].value
        row[1].value, row[7].value = b, a
    w.save(p)
    w.close()
    before = hashlib.sha256(p.read_bytes()).hexdigest()
    v = load_analysis(p)
    r = v["records"]["20088左"]
    assert r["machine_name"] == "機器"
    assert r["values"]["po_rate"] == 0.08 and r["values"]["cost_rate"] == 0.3333
    assert v["period_start"] == "2026-11-01" and v["period_end"] == "2026-11-06"
    assert v["extracted_at"] == "2026-11-07T18:19:35+09:00"
    assert v["filename_timestamp"] == "2026-11-07T06:19:00"
    assert hashlib.sha256(p.read_bytes()).hexdigest() == before
    assert "2026/11/01" in period_text(v)


def test_unknown_period_no_inference_and_missing_no_silent_fallback(tmp_path):
    row = data_row()
    row[10] = None
    v = load_analysis(report_file(tmp_path / "analysis_202611070619.xlsx", [row]))
    assert v["period_start"] is None and v["extracted_at"] is None
    m = Machine(number="20088左", po_rate=0.2, average=2000, ratio=100)
    n = Machine(number="29999右", sales=200, average=100, ratio=200)
    apply_snapshot([m, n], v)
    assert effective_stat(m, "po_rate") is None
    assert effective_stat(n, "sales") is None and ratio(n) is None
    assert n.sales == 200


def test_issues_duplicates_invalid_id_range_consistency_and_reconciliation(tmp_path):
    row = data_row()
    row[10] = 1.5
    row[5] = 123
    bad = data_row("20088-03")
    v = load_analysis(report_file(tmp_path / "x.xlsx", [row, deepcopy(row), bad]))
    issues = reconcile([Machine(number="29999右")], v, v)
    assert {
        "duplicate",
        "range",
        "consistency",
        "number_format",
        "unmatched",
        "absent",
        "reimport",
    } <= {i["kind"] for i in issues}
    assert v["unmatched_rows"][0]["number"] == "20088-03"
    assert any(i["severity"] == "エラー" for i in issues)


def test_tolerance_and_legacy_snapshot_migration(tmp_path):
    row = data_row()
    row[5] = 30010
    p = report_file(tmp_path / "x.xlsx", [row])
    assert any(
        i["kind"] == "consistency" for i in load_analysis(p, dict(absolute=0, relative=0))["issues"]
    )
    assert not any(
        i["kind"] == "consistency"
        for i in load_analysis(p, dict(absolute=11, relative=0))["issues"]
    )
    v = load_analysis(p)
    old = deepcopy(v)
    old["version"] = 1
    old["records"]["20088左"]["values"]["po_rate"] = 8
    old["records"]["20088左"]["values"]["cost_rate"] = 33.33
    migrated = validate_snapshot(old)
    assert migrated["records"]["20088左"]["values"]["po_rate"] == 0.08
    assert old["records"]["20088左"]["values"]["po_rate"] == 8


@pytest.mark.parametrize(
    "key,value,color",
    [
        ("po_rate", 0, "#8FC5F4"),
        ("po_rate", 0.02999, "#8FC5F4"),
        ("po_rate", 0.03, "#94D8A0"),
        ("po_rate", 0.07, "#94D8A0"),
        ("po_rate", 0.07001, "#F4A6A6"),
        ("po_rate", None, "#B8BEC6"),
        ("cost_rate", 0.3999, "#8FC5F4"),
        ("cost_rate", 0.4, "#C5E2FA"),
        ("cost_rate", 0.4999, "#C5E2FA"),
        ("cost_rate", 0.5, "#94D8A0"),
        ("cost_rate", 0.7, "#94D8A0"),
        ("cost_rate", 0.7001, "#F4A6A6"),
        ("cost_rate", None, "#B8BEC6"),
    ],
)
def test_rate_filter_boundaries_and_markers(key, value, color):
    from prize_swap.floor_status import filter_status

    m = Machine(**{key: value})
    colour, label = filter_status(m, key, date(2026, 11, 7), True)
    assert colour == color and "投入予定" in label


def test_sales_benchmark_is_independent_and_no_implicit_reference():
    from prize_swap.floor_status import filter_status
    from prize_swap.analysis_settings import validate_settings

    m = Machine(average=500, ratio=200)
    assert filter_status(m, "sales_metric", date.today())[0] == "#B8BEC6"
    s = validate_settings({"sales_benchmark": 1000})
    for average, color in [
        (499, "#F4A6A6"),
        (500, "#F7D5A0"),
        (1000, "#94D8A0"),
        (2000, "#FFE16A"),
    ]:
        m.average = average
        assert filter_status(m, "sales_metric", date.today(), settings=s)[0] == color
        assert ratio(m) == 200


def test_search_all_nodes_geometry_projection_and_fullscreen(tmp_path):
    from PySide6.QtWidgets import QApplication
    from prize_swap.floor import FloorWidget

    w = FloorWidget(lambda: [], date.today, tmp_path / "auto.json")
    try:
        w.machines = [
            Machine(
                number=f"{20000+i}右",
                product=f"商品 {i}",
                kind="UFO９ third",
                operation_method="フック",
                x=i % 20 * 200,
                y=i // 20 * 160,
            )
            for i in range(300)
        ]
        w.rebuild()
        w.show()
        QApplication.processEvents()
        w.set_zoom(100)
        ids = {k: id(v) for k, v in w.nodes.items()}
        before = [asdict(m) for m in w.machines]
        rects = {k: (n.pos(), n.boundingRect()) for k, n in w.nodes.items()}
        transform = w.view.transform()
        for query, count in [
            ("２００００ 右", 1),
            ("商品 2", 111),
            ("ufo9third", 300),
            ("フック", 300),
            ("zzzz", 0),
            ("", 300),
        ]:
            w.floor_search.setText(query)
            assert len(w._match_uids) == count
            assert len(w.visible_nodes()) == 300 and {k: id(v) for k, v in w.nodes.items()} == ids
            assert all((n.pos(), n.boundingRect()) == rects[k] for k, n in w.nodes.items())
            assert w.view.transform() == transform and [asdict(m) for m in w.machines] == before
        w.toggle_fullscreen()
        QApplication.processEvents()
        w._fullscreen_host.search.setText("商品 299")
        QApplication.processEvents()
        assert len(w._match_uids) == 1 and len(w.visible_nodes()) == 300
        w.next_match()
        assert w.selected()[0].product == "商品 299"
        w._fullscreen_host.close()
        QApplication.processEvents()
        assert [asdict(m) for m in w.machines] == before
    finally:
        w.timer.stop()
        w.day_timer.stop()
        w.close()


def test_type_styles_aliases_and_settings_roundtrip(tmp_path):
    from prize_swap.analysis_settings import machine_type, type_style, validate_settings

    s = validate_settings(
        {
            "aliases": {"UFO9 THIRD": "UFO9 third"},
            "types": {"UFO9 third": {"color": "#AABBCC", "short": "UFO9"}},
        }
    )
    m = Machine(kind="UFO9 THIRD")
    assert (
        machine_type(m, s) == "UFO9 third" and type_style(machine_type(m, s), s)["short"] == "UFO9"
    )
    assert type_style("different") == type_style("different")
    assert validate_settings(s) == s


def test_dashboard_distribution_counts_unknown_denominator_and_highlight(tmp_path):
    from prize_swap.floor_dashboard import distribution
    from prize_swap.floor import FloorWidget
    from PySide6.QtWidgets import QApplication

    machines = [
        Machine(number=f"{20000+i}右", ratio=v, x=i * 200)
        for i, v in enumerate((49.9, 50, 100, 200, None))
    ]
    d = distribution(machines)
    assert sum(v["count"] for v in d.values()) == 5
    assert all(v["percentage"] == 20 for v in d.values())
    w = FloorWidget(lambda: [], date.today, tmp_path / "auto.json")
    try:
        w.machines = machines
        w.rebuild()
        before = [asdict(m) for m in machines]
        w.dashboard.buttons["不明"].click()
        assert w._highlight_uids == {machines[-1].uid} and len(w.visible_nodes()) == 5
        w.dashboard.toggle.click()
        assert not w.analysis_settings["dashboard_visible"]
        w.toggle_fullscreen()
        QApplication.processEvents()
        assert w.dashboard.isHidden()
        w._fullscreen_host.close()
        QApplication.processEvents()
        assert [asdict(m) for m in w.machines] == before
    finally:
        w.timer.stop()
        w.day_timer.stop()
        w.close()


@pytest.mark.parametrize("mode", ["po_rate", "cost_rate", "sales_metric", "machine_type"])
def test_new_filters_pdf_and_direct_print_share_layout(tmp_path, monkeypatch, mode):
    import prize_swap.floor as module
    from prize_swap.floor import FloorWidget
    from prize_swap.floor_print import FloorPrintOptions, floor_printer
    from prize_swap.pdf_validation import require_searchable_pdf, normalized_pdf_text
    from PySide6.QtWidgets import QDialog, QMessageBox
    from PySide6.QtPdf import QPdfDocument
    from PySide6.QtCore import QSize

    w = FloorWidget(lambda: [], date.today, tmp_path / "auto.json")
    try:
        w.machines = [
            Machine(
                number="20088左",
                product="日本語の商品全文",
                po_rate=0.05,
                cost_rate=0.6,
                average=1000,
                sales=3000,
                kind="UFO9 third",
            )
        ]
        w.rebuild()
        w.set_print_options(
            FloorPrintOptions(color_filter=mode, analysis_settings={"sales_benchmark": 1000})
        )
        a = tmp_path / "a.pdf"
        b = tmp_path / "b.pdf"
        w.paint_floor(floor_printer(w.print_options, a))

        class Accepted:
            def __init__(self, *a):
                pass

            def setMinMax(self, *a):
                pass

            def setOption(self, *a):
                pass

            def exec(self):
                return QDialog.DialogCode.Accepted

        monkeypatch.setattr(module, "QPrintDialog", Accepted)
        monkeypatch.setattr(module, "floor_printer", lambda options: floor_printer(options, b))
        monkeypatch.setattr(QMessageBox, "question", lambda *a: QMessageBox.StandardButton.Yes)
        errors = []
        monkeypatch.setattr(QMessageBox, "warning", lambda *a: errors.append(a))
        w.print_floor()
        assert not errors
        texts = []
        for p in (a, b):
            texts.append(
                normalized_pdf_text("".join(v["text"] for v in require_searchable_pdf(p)["pages"]))
            )
        assert texts[0] == texts[1] and texts[0].count("日本語の商品全文") == 1
        da = QPdfDocument()
        db = QPdfDocument()
        da.load(str(a))
        db.load(str(b))
        assert da.render(0, QSize(842, 595)) == db.render(0, QSize(842, 595))
        da.close()
        db.close()
    finally:
        w.timer.stop()
        w.day_timer.stop()
        w.close()


def test_quantile_print_subset_uses_full_floor_population(tmp_path):
    from prize_swap.floor import FloorWidget
    from prize_swap.floor_print import FloorPrintOptions, floor_printer

    w = FloorWidget(lambda: [], date.today, tmp_path / "auto.json")
    try:
        w.machines = [
            Machine(number=f"{20000+i}右", average=i * 100, product=f"商品{i}", x=i * 200)
            for i in range(8)
        ]
        w.analysis_settings["sales_mode"] = "quantile"
        w.rebuild()
        w.set_color_filter("sales_metric")
        w.floor_search.setText("20000右")
        expected = w.card_context(w.machines[0])[0]
        w.set_print_options(
            FloorPrintOptions(
                color_filter="sales_metric",
                visible_only=True,
                analysis_settings={"sales_mode": "quantile"},
            )
        )
        plan = w.paint_floor(floor_printer(w.print_options, tmp_path / "subset.pdf"))
        assert (
            len(plan.pages[0].cards) == 1
            and plan.pages[0].cards[0].background == expected == "#F4A6A6"
        )
    finally:
        w.timer.stop()
        w.day_timer.stop()
        w.close()
