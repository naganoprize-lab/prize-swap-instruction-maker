from copy import deepcopy
from dataclasses import asdict
from datetime import date
import pytest
from prize_swap.costing import cost_profile, trailing_candidate
from prize_swap.floor_data import Machine, validate_machines
from prize_swap.floor_status import effective_stat, raw_stat
from prize_swap.po_evaluation import evaluate_po
from prize_swap.analysis_settings import validate_settings
from prize_swap.radar import evaluate, score_record

DAY = date(2026, 11, 8)


def sample(**kw):
    return Machine(
        **(
            dict(
                product="堅あげ 270",
                sales=6000,
                dispensed=11,
                po_rate=0.2,
                cost_rate=0.9,
                unit_price=999,
                installed_on="2026-11-01",
            )
            | kw
        )
    )


@pytest.mark.parametrize(
    "name,cost",
    [
        ("堅あげ 270", 270),
        ("商品 020", 20),
        ("商品 080", 80),
        ("商品 1000", 1000),
        ("商品 1,000", 1000),
    ],
)
def test_trailing_cost_precedes_official_but_saved_manual_is_preserved(name, cost):
    m = sample(product=name)
    p = cost_profile(m)
    assert p["effective"] == cost and p["source"] == "商品名末尾" and p["official"] == 999
    m.prize_cost = 321
    m.prize_cost_product = name
    assert cost_profile(m)["effective"] == 321 and cost_profile(m)["source"] == "手動確認済み"
    m.product = "別商品"
    assert cost_profile(m)["effective"] == 999


@pytest.mark.parametrize(
    "name", ["型番 270", "景品 Vol. 2", "サイズ 080", "景品 -20", "景品 0", "景品 2"]
)
def test_model_size_and_invalid_suffix_require_confirmation(name):
    m = sample(product=name)
    assert cost_profile(m)["source"] == "正式単価"
    assert cost_profile(sample(product=name, unit_price=None))["effective"] is None


def test_cost_total_cost_rate_po_and_radar_use_the_same_unit_without_modifying_measurements():
    m = sample()
    raw = asdict(m)
    s = validate_settings()
    assert effective_stat(m, "cost_rate") == pytest.approx(0.495)
    assert effective_stat(m, "cost") == 2970 and raw_stat(m, "cost_rate") == 0.9
    assert evaluate_po(m, s)["target"] == pytest.approx(2 / 9)
    before = evaluate(m, DAY, s, [score_record(m, DAY, s)])
    m.prize_cost = 300
    m.prize_cost_product = m.product
    assert effective_stat(m, "cost_rate") == 0.55 and effective_stat(m, "cost") == 3300
    po = evaluate_po(m, s)
    assert po["actual"] == 0.2 and po["target"] == 0.2 and po["score"] == 100
    after = evaluate(m, DAY, s, [score_record(m, DAY, s)])
    assert after[3]["actual"] == "55.00%" and after[3]["score"] != before[3]["score"]
    assert after[4]["actual"] == "20.00%" and after[4]["score"] > before[4]["score"]
    assert (
        m.cost_rate == raw["cost_rate"]
        and m.po_rate == raw["po_rate"]
        and m.sales == raw["sales"]
        and m.dispensed == raw["dispensed"]
    )
    assert validate_machines([asdict(m)])[0].prize_cost == 300


@pytest.mark.parametrize(
    "kw",
    [{"sales": 0}, {"dispensed": None}, {"unit_price": None, "product": "商品"}, {"dispensed": -1}],
)
def test_missing_count_zero_sales_and_missing_cost_never_become_zero_percent(kw):
    p = cost_profile(sample(**kw))
    assert p["calculated_cost_rate"] is None and p["reason"]


def test_zero_acquired_is_valid_and_period_or_product_mismatch_is_not_recalculated():
    assert cost_profile(sample(dispensed=0))["calculated_cost_rate"] == 0
    m = sample()
    m.analysis = {
        "values": {
            "sales": 6000,
            "dispensed": 11,
            "cost_rate": 0.9,
            "unit_price": 999,
            "po_rate": 0.2,
        },
        "po_definition": "prize_per_play",
        "period_start": "2026-11-01",
        "period_end": "2026-11-06",
        "period_source": "excel",
        "product_context": {"product": m.product, "installed_on": m.installed_on},
    }
    original = deepcopy(m.analysis)
    assert cost_profile(m)["calculated_cost_rate"] == pytest.approx(0.495)
    m.analysis["period_start"] = "2026-10-01"
    assert cost_profile(m)["calculated_cost_rate"] is None
    m.analysis = original
    m.product = "別商品 300"
    assert cost_profile(m)["calculated_cost_rate"] is None
    assert m.analysis == original


def test_detail_cost_edit_updates_actual_values_and_preserves_excel_input(tmp_path):
    from prize_swap.ui import Window
    from prize_swap.floor_details import MachineDetailDialog

    w = Window(layout_storage=tmp_path / "layout.json")
    try:
        m = sample()
        w.floor.machines = [m]
        w.floor.rebuild()
        d = MachineDetailDialog(w.floor, m)
        assert d.confirmed_cost.value() == 270 and "取得元：商品名末尾" in d.cost_values.text()
        d.confirmed_cost.setValue(300)
        assert d.kpis["cost_rate"].value.text() == "55.00%"
        assert d.values["cost"].text() == "3,300円"
        assert "手動確認済み" in d.cost_values.text() and "90.00%" in d.cost_values.text()
        assert d.radar.axes[3]["actual"] == "55.00%" and d.radar.axes[4]["actual"] == "20.00%"
        assert d.updated.cost_rate == 0.9 and d.updated.po_rate == 0.2
        d.accept()
        assert d.updated.prize_cost == 300
    finally:
        w.dirty = False
        w.instruction_save_timer.stop()
        w.floor.timer.stop()
        w.floor.day_timer.stop()
        w.close()


def test_source_product_difference_and_multiple_prizes_block_single_cost_calculation():
    m = sample()
    m.analysis = {
        "values": {"sales": 6000, "dispensed": 11},
        "reference_product_name": "別の景品 500",
        "product_context": {"product": m.product, "installed_on": m.installed_on},
        "period_source": "excel",
        "period_start": "2026-11-01",
        "period_end": "2026-11-06",
    }
    assert cost_profile(m)["calculated_cost_rate"] is None
    m.analysis["reference_product_name"] = "売上登録用"
    assert cost_profile(m)["calculated_cost_rate"] == pytest.approx(0.495)
    m.analysis["multiple_products"] = True
    assert cost_profile(m)["calculated_cost_rate"] is None
