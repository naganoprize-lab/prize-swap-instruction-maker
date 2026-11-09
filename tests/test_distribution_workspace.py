from datetime import date
from copy import deepcopy
from dataclasses import asdict
import pytest
from PySide6.QtCore import QDate, Qt
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox, QInputDialog
from PySide6.QtTest import QTest
from prize_swap.ui import Window
from prize_swap.floor_data import Machine, validate_state
from prize_swap.distributions import distribution
from prize_swap.analysis_settings import validate_settings
from prize_swap.analytics_data import machine_record
from prize_swap.candidate_page import candidate_key

DAY = date(2026, 11, 8)


@pytest.fixture
def window(tmp_path):
    w = Window(layout_storage=tmp_path / "auto.json")
    f = w.floor
    f.follow_today = False
    f.reference_date.setDate(QDate(2026, 11, 8))
    f.machines = [
        Machine(
            number=f"{21000+i}右",
            source_key=f"L_{i}",
            kind="UFO9" if i % 2 else "UFO10",
            product=f"長い商品名 テスト {i} 270",
            sales=i * 100,
            average=None,
            ratio=[40, 70, 120, None][i % 4],
            po_rate=0.2,
            unit_price=270,
            cost_rate=0.6,
            operation_method="リング" if i % 2 else "橋渡し",
            installed_on="2026-10-01",
            x=i % 20 * 240,
            y=i // 20 * 170,
        )
        for i in range(300)
    ]
    f.rebuild()
    w.show()
    QApplication.processEvents()
    yield w
    w.dirty = False
    f.timer.stop()
    f.day_timer.stop()
    w.analysis_page.timer.stop()
    w.candidate_page.timer.stop()
    w.close()


def test_distribution_counts_ids_and_explicit_unknowns():
    s = validate_settings()
    ms = [
        Machine(
            sales=v,
            average=9000,
            ratio=v,
            po_rate=0.2,
            unit_price=270,
            cost_rate=0.6,
            installed_on="2026-11-01",
        )
        for v in [0, 100, 200, 300, 400, None]
    ]
    records = [machine_record(m, DAY, s) for m in ms]
    for axis in (
        "ratio_bin",
        "sales_bin",
        "po_bin",
        "cost_bin",
        "days_bin",
        "machine_type",
        "method",
    ):
        d = distribution(records, axis, s)
        assert sum(r["value"] for r in d["rows"]) == 6
        assert sorted(uid for r in d["rows"] for uid in r["uids"]) == sorted(m.uid for m in ms)
    d = distribution(records, "sales_bin", s)
    assert {r["label"]: r["value"] for r in d["rows"]} == {
        "特に高売上": 1,
        "高売上": 1,
        "中程度": 1,
        "やや低売上": 1,
        "低売上": 1,
        "不明": 1,
    }
    assert d["unknown"] == 1


def test_default_tiles_responsive_and_no_average_ui(window):
    w = window
    p = w.analysis_page
    w.navigation.buttons["analysis"].click()
    p.refresh()
    QTest.qWait(30)
    assert w.tabs.currentWidget() is p and p.workspace_tabs.currentWidget() is p.distributions
    assert all(p.metric_choice.itemData(i) != "average" for i in range(p.metric_choice.count()))
    assert p.distributions.cards["sales_bin"][2].rows
    # A managed child is otherwise resized back by the main window layout.
    from viewport_helpers import distribution_viewport

    with distribution_viewport(p):
        p.distributions.resize(1600, 700)
        QApplication.processEvents()
        assert p.distributions.columns == 3
        p.distributions.resize(1000, 700)
        QApplication.processEvents()
        assert p.distributions.columns == 2
        p.distributions.resize(700, 700)
        QApplication.processEvents()
        assert p.distributions.columns == 1
    p.distributions.open_detail("sales_bin")
    assert p.workspace_tabs.currentWidget() is p.detail_page and p.axis.currentData() == "sales_bin"


def test_tile_click_keeps_all_floor_nodes_search_zoom_and_geometry(window):
    w = window
    f = w.floor
    p = w.analysis_page
    f.set_zoom(150)
    f.floor_search.setText("テスト 29")
    before = [asdict(m) for m in f.machines]
    nodes = {k: id(n) for k, n in f.nodes.items()}
    w.navigation.buttons["analysis"].click()
    p.refresh()
    QTest.qWait(30)
    c = p.distributions.cards["ratio_bin"][2]
    c.grab()
    rect, row = c.hits[0]
    QTest.mouseClick(c, Qt.MouseButton.LeftButton, pos=rect.center().toPoint())
    assert w.tabs.currentWidget() is f and f.analysis_highlight_uids == set(row["uids"])
    assert (
        len(f.visible_nodes()) == 300
        and [asdict(m) for m in f.machines] == before
        and f._zoom == 150
        and f.floor_search.text() == "テスト 29"
    )
    assert {k: id(n) for k, n in f.nodes.items()} == nodes


def test_candidates_embedded_colors_filters_and_persistent_exclusion(window, monkeypatch):
    w = window
    p = w.candidate_page
    f = w.floor
    monkeypatch.setattr(
        QDialog, "exec", lambda *_: pytest.fail("candidate navigation must remain embedded")
    )
    f.show_candidates()
    QApplication.processEvents()
    assert w.tabs.currentWidget() is p and p.table.rowCount() == 300
    assert p.cards["要確認"].value.text() == "75" and p.cards["好調"].value.text() == "75"
    assert all(
        p.table.horizontalHeaderItem(i).text() != "平均売上" for i in range(p.table.columnCount())
    )
    check = p.table.item(0, 0)
    check.setCheckState(
        Qt.CheckState.Checked
    )  # Explicit checkbox operation; row focus no longer selects machines.
    m = p.selected_machines()[0]
    assert p.set_exclusions("現場で継続稼働を確認")
    state = validate_state(f.state())
    assert state["candidate_ui"]["exclusions"][candidate_key(m)]["reason"] == "現場で継続稼働を確認"
    assert p.table.rowCount() == 299
    f.restore(state)
    p.refresh()
    assert p.table.rowCount() == 299
    p.show_excluded.setChecked(True)
    p.refresh()
    assert p.table.rowCount() == 300
    p.search.setText(m.source_key)
    p.refresh()
    assert p.table.rowCount() >= 1
    p.search.clear()
    p.kinds.setCurrentIndex(p.kinds.findData("UFO9"))
    p.changed()
    p.refresh()
    assert p.table.rowCount() == 150


def test_candidate_registration_only_after_explicit_product_confirmation(window, monkeypatch):
    from prize_swap.core import Product
    from datetime import datetime

    w = window
    f = w.floor
    p = w.candidate_page
    product = Product(DAY, "新景品", 10, 1)
    monkeypatch.setattr(f, "products", lambda: [product])
    monkeypatch.setattr(f, "selected_day", lambda: DAY)
    p.refresh()
    check = p.table.item(0, 0)
    check.setCheckState(
        Qt.CheckState.Checked
    )  # Explicit checkbox operation; row focus no longer selects machines.
    selected = p.selected_machines()[0]
    assert not product.machines
    monkeypatch.setattr(QInputDialog, "getItem", lambda *a, **k: ("1: 新景品", True))
    monkeypatch.setattr(QMessageBox, "question", lambda *a: QMessageBox.StandardButton.No)
    p.register()
    assert not product.machines
    monkeypatch.setattr(QMessageBox, "question", lambda *a: QMessageBox.StandardButton.Yes)
    p.register()
    assert product.machines == [selected.number]
    assert p.cards["入替予定登録済み"].value.text() == "1"


def test_sales_bucket_uses_all_population_even_with_focus(window):
    p = window.analysis_page
    f = window.floor
    p.focus_machine(f.machines[0].uid)
    p.axis.setCurrentIndex(p.axis.findData("sales_bin"))
    p.metric_choice.setCurrentIndex(p.metric_choice.findData("number"))
    p.operation.setCurrentIndex(p.operation.findData("count"))
    p.refresh()
    assert len(p.results) == 1 and p.results[0]["label"].startswith("低売上")
    p.distributions.form.setCurrentIndex(p.distributions.form.findData("donut"))
    p.distributions.method_button.setChecked(True)
    saved = validate_state(f.state())
    assert (
        saved["analytics_ui"]["overview_chart"] == "donut"
        and saved["analytics_ui"]["overview_methods"]
    )


def test_valid_recent_install_is_observation_not_missing_data(window):
    from prize_swap.sales_analysis import METRICS
    from prize_swap.replacement import assess

    f = window.floor
    m = f.machines[0]
    m.installed_on = DAY.isoformat()
    m.analysis = dict(
        values={key: None for key in METRICS},
        file_name="synthetic.xlsx",
        loaded_at="2026-11-08T12:00:00+09:00",
        period_source="excel",
        period_start="2026-11-08",
        period_end="2026-11-08",
        fingerprint="recent",
    )
    a = assess(m, DAY)
    assert a["status"] == "判定保留" and a["trend"] is None
    assert any("未評価" in r for r in a["reasons"]) and not any(
        "履歴不足" in r for r in a["reasons"]
    )
    p = window.candidate_page
    p.search.setText(m.source_key)
    p.changed()
    p.refresh()
    row = next(
        i
        for i in range(p.table.rowCount())
        if p.table.item(i, 0).data(Qt.ItemDataRole.UserRole) == [m.uid]
    )
    assert p.table.item(row, 10).text() == "経過観察"
