from datetime import date
import pytest
from PySide6.QtCore import Qt, QDate
from PySide6.QtWidgets import QApplication
from prize_swap.floor import FloorWidget
from prize_swap.floor_data import Machine
from prize_swap.floor_details import MachineDetailDialog
from prize_swap.theme import manager

DAY = date(2026, 11, 7)


@pytest.fixture
def owner(tmp_path):
    w = FloorWidget(lambda: [], lambda: DAY, tmp_path / "auto.json")
    w.follow_today = False
    w.reference_date.setDate(QDate(DAY))
    w.machines = [
        Machine(
            source_key="L_66",
            product="全文の長い日本語商品名" * 10,
            kind="UFO9",
            operation_method="橋渡し",
            sales=12000,
            average=6000,
            ratio=120,
            po_rate=0.05,
            cost_rate=0.6,
            installed_on="2026-10-31",
        )
    ]
    w.rebuild()
    yield w
    w.timer.stop()
    w.day_timer.stop()
    w.close()


def test_overview_preserves_all_legacy_fields_and_values(owner):
    d = MachineDetailDialog(owner, owner.machines[0])
    try:
        assert (
            d.identity_label.text() == "L_66" and d.name.toPlainText() == owner.machines[0].product
        )
        assert "管理 L_66" in d.header_meta.text() and "UFO9" in d.header_meta.text()
        assert d.kpis["sales"].value.text() == "12,000 円" and d.kpis["days"].value.text() == "7 日"
        assert (
            d.kpis["ratio"].value.text() == "120.0%" and d.kpis["po_rate"].value.text() == "5.00%"
        )
        assert {
            "number",
            "source_key",
            "resolved_number",
            "match_method",
            "provenance",
            "ratio_source",
            "updated_at",
            "sales",
            "average",
            "ratio",
            "po_rate",
            "installed_on",
            "reference",
            "days",
            "operating_days",
            "gross_profit",
            "cost_rate",
            "dispensed",
            "per_item",
            "unit_price",
            "cost",
        } <= d.values.keys()
        assert d.values["operating_days"].text() == "—" and d.values["days"].text() == "7日"
        assert d.kpis["ratio"].toolTip() == "レイアウト由来"
        assert d.maker and d.units and d.cases and d.limit and d.method and not d.name.isHidden()
    finally:
        d.close()


def test_responsive_splitter_size_restore_and_no_physical_layout_edit(owner):
    before = [(m.x, m.y, m.width, m.height) for m in owner.machines]
    d = MachineDetailDialog(owner, owner.machines[0])
    d.show()
    d.resize(1200, 820)
    QApplication.processEvents()
    assert d.splitter.orientation() == Qt.Orientation.Horizontal
    assert not d.kpis["ratio"].visibleRegion().isEmpty() and all(s > 0 for s in d.splitter.sizes())
    d.resize(800, 700)
    QApplication.processEvents()
    assert d.splitter.orientation() == Qt.Orientation.Vertical
    assert not d.kpis["ratio"].visibleRegion().isEmpty() and all(s > 0 for s in d.splitter.sizes())
    d.reject()
    assert owner.analytics_ui["detail_size"] == [800, 700]
    d2 = MachineDetailDialog(owner, owner.machines[0])
    assert d2.width() <= 800 and d2.height() <= 700
    d2.close()
    assert [(m.x, m.y, m.width, m.height) for m in owner.machines] == before


def test_overview_theme_refresh_and_analysis_navigation_signal(owner):
    theme = manager()
    old = theme.mode
    d = MachineDetailDialog(owner, owner.machines[0])
    seen = []
    owner.analytics_requested.connect(lambda uid, scope: seen.append((uid, scope)))
    try:
        d.show()
        theme.set_mode("dark", persist=False)
        assert d.kpis["ratio"].background == "#1F5237"
        d.analysis_scope.setCurrentIndex(1)
        d.analysis_button.click()
        QApplication.processEvents()
        assert seen == [(owner.machines[0].uid, "machine_type")]
    finally:
        d.close()
        theme.set_mode(old, persist=False)


def test_manual_metric_provenance_saved_but_new_analysis_still_wins(owner, monkeypatch):
    from prize_swap.machine_matching import metric_info
    from prize_swap.floor_data import validate_state

    m = owner.machines[0]

    def edit(draft, *_):
        draft.sales = 4321
        draft.ratio = 85
        return True

    monkeypatch.setattr(owner, "machine_dialog", edit)
    d = MachineDetailDialog(owner, m)
    d.edit_metrics()
    d.accept()
    assert (
        metric_info(d.updated, "sales")["source"] == "手動入力"
        and metric_info(d.updated, "ratio")["updated_at"]
    )
    saved = owner.state()
    saved["machines"][0]["manual_updates"] = d.updated.manual_updates
    assert validate_state(saved)["machines"][0]["manual_updates"]["sales"]
    d.updated.analysis = {"values": {"sales": 9000}, "loaded_at": "new"}
    assert (
        metric_info(d.updated, "sales")["value"] == 9000
        and metric_info(d.updated, "sales")["source"] == "景品運営分析表"
    )
