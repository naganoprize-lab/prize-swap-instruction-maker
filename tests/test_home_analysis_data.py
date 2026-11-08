from datetime import date
from dataclasses import asdict
import pytest
from prize_swap.analysis_settings import validate_settings, sales_boundaries
from prize_swap.sales_evaluation import classify, NeutralBounds
from prize_swap.analysis_bands import SALES_COLORS, UNKNOWN, cost_band, age_band
from prize_swap.floor_data import Machine, validate_machines
from prize_swap.po_evaluation import cost_candidate, evaluate_po
from prize_swap.analytics_data import machine_record
from prize_swap.radar import evaluate, score_record

DAY = date(2026, 11, 8)


def test_quintile_edges_zero_unknown_same_value_same_band():
    s = validate_settings()
    ms = [Machine(sales=i, average=9999) for i in range(11)]
    b = sales_boundaries(ms, s)
    assert list(b) == [2, 4, 6, 8]
    assert [classify(i, b)[0] for i in [0, 2, 4, 6, 8]] == list(SALES_COLORS)
    assert classify(None, b)[0] == UNKNOWN
    assert classify(0, b)[0] == SALES_COLORS[0]
    assert classify(8, b) == classify(8.0, b)
    same = sales_boundaries([Machine(sales=0)] * 8, s)
    assert "相対比較不可" in classify(0, same.groups[(None, None, "layout")])[1]


@pytest.mark.parametrize(
    "text,value",
    [
        ("堅あげ 270", 270),
        ("堅あげ 020", 20),
        ("堅あげ 050", 50),
        ("景品 1,000", 1000),
        ("Vol.2 景品 1000", 1000),
        ("Vol.2", None),
        ("景品 -20", None),
        ("景品 0", None),
    ],
)
def test_cost_suffix_is_candidate_not_automatic_fact(text, value):
    assert cost_candidate(text) == value
    m = Machine(product=text)
    g = evaluate_po(m, validate_settings())
    assert g["target"] == (pytest.approx(60 / value) if value is not None else None)
    if value is not None:
        assert g["cost_confidence"] == "estimated"


def test_po_pt_source_confidence_cost_mismatch_and_raw_definition():
    s = validate_settings()
    m = Machine(product="堅あげ 020", unit_price=270, po_rate=0.2)
    g = evaluate_po(m, s)
    assert g["target"] == 3 and g["deviation_points"] == pytest.approx(-280)
    assert (
        g["cost_confidence"] == "estimated"
        and g["provisional"]
        and any("相違" in w for w in g["warnings"])
    )
    m.analysis = {"values": {"po_rate": 0.2, "unit_price": 270}, "fingerprint": "file1"}
    g = evaluate_po(m, s)
    assert g["actual"] is None and g["raw_actual"] == 0.2 and g["score"] is None
    m.analysis["po_definition"] = "other"
    assert evaluate_po(m, s)["actual"] is None
    m.po_definition = "prize_per_play"
    m.po_definition_fingerprint = "file1"
    assert evaluate_po(m, s)["actual"] == 0.2
    assert validate_machines(
        [asdict(Machine(prize_cost=20, prize_cost_product="X", prize_cost_origin="product_suffix"))]
    )


def test_real_plays_and_amount_override_provisional_fee():
    m = Machine(
        analysis={
            "values": {"unit_price": 270, "po_rate": 0.8},
            "po_inputs": {"actual_plays": 100, "acquired_count": 20, "inserted_amount": 10000},
        }
    )
    g = evaluate_po(m, validate_settings())
    assert (
        g["actual"] == 0.2
        and g["raw_actual"] == 0.8
        and g["play_price"] == 100
        and not g["provisional"]
    )
    assert g["deviation_points"] == pytest.approx(-2.222222)
    m.analysis["po_inputs"]["actual_plays"] = 0
    g = evaluate_po(m, validate_settings())
    assert g["actual"] is None and g["target"] is None


def test_more_than_100_target_unrated_not_clamped():
    g = evaluate_po(Machine(unit_price=20, po_rate=0.2), validate_settings())
    assert g["target"] == 3 and g["score"] is None and any("100%" in w for w in g["warnings"])


def test_all_equal_and_single_sales_are_unrated_and_real_labels_remain():
    s = validate_settings()
    ms = [Machine(sales=100, ratio=120) for _ in range(5)]
    rs = [score_record(m, DAY, s) for m in ms]
    assert evaluate(ms[0], DAY, s, rs)[1]["score"] is None
    assert evaluate(ms[0], DAY, s, rs[:1])[1]["score"] is None
    assert evaluate(ms[0], DAY, s, rs)[1]["actual"] == "100円"


def test_bands_align_age10_and_cost80():
    s = validate_settings()
    assert age_band(9)[0] != "#C5A5ED" and age_band(10)[0] == "#C5A5ED"
    assert age_band(-1)[1] == "投入予定" and age_band(None)[1] == "不明"
    assert cost_band(0.7001, s)[1] == "目標より高い" and cost_band(0.8001, s)[1] == "高すぎる"


def test_policy_settings_and_old_custom_fixed_thresholds():
    s = validate_settings(
        {"sales_mode": "fixed", "sales_low": 100, "sales_middle": 300, "sales_high": 600}
    )
    assert s["sales_fixed_breaks"] == [100, 300, 450, 600]
    assert validate_settings(s) == s
    for bad in [[0.2, 0.2, 0.6, 0.8], [0.2, 0.4, 0.6, 1]]:
        with pytest.raises(Exception):
            validate_settings({"sales_percentiles": bad})


def test_preview_sales_normalization_always_stays_within_internal_bounds():
    from prize_swap.radar import sales_score

    s = validate_settings()
    peers = [score_record(Machine(sales=v), DAY, s) for v in [0, 100, 200]]
    draft = peers[0] | {"sales": 10000}
    for method in ("percentile", "linear_range"):
        assert sales_score(draft, peers, s | {"radar_sales_method": method})[0] == 100
