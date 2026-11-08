from datetime import date
from copy import deepcopy
import pytest
from prize_swap.floor_data import Machine
from prize_swap.analysis_settings import validate_settings, sales_boundaries
from prize_swap.sales_evaluation import evaluation, boundary_legend
from prize_swap.replacement import assess
from prize_swap.analytics_data import machine_record, aggregate
from prize_swap.distributions import distribution

DAY = date(2026, 11, 9)


def snapshot(values):
    return dict(
        period_start="2026-11-06",
        period_end="2026-11-06",
        period_source="excel",
        records={str(i): dict(values=dict(sales=v)) for i, v in enumerate(values)},
    )


def overlay(sales):
    return dict(
        values=dict(sales=sales),
        period_start="2026-11-06",
        period_end="2026-11-06",
        period_source="excel",
    )


def test_full_excel_population_uses_unmatched_for_reference_without_assigning_them():
    s = validate_settings()
    # Exactly 30 higher values, two ties; the remaining rows have lower sales.
    snap = snapshot([20000] * 30 + [16100] * 2 + [1000] * 272)
    m = Machine(number="20111右", source_key="R_66", analysis=overlay(16100))
    peers = [m, Machine(sales=999999)]
    before = deepcopy(peers)
    b = sales_boundaries(peers, s, snap)
    r = evaluation(m, s, b)
    assert (r["rank"], r["count"], r["color"]) == (31, 304, "#FFE16A")
    assert r["top_percent"] == pytest.approx(30 / 303 * 100)
    assert peers == before and len(snap["records"]) == 304
    assert evaluation(peers[1], s, b)["provisional"]
    m.analysis["period_start"] = "2026-11-05"
    assert evaluation(m, s, b)["label"] == "対象期間外"


def test_ties_zeros_missing_decorated_money_and_fixed_custom_colors():
    s = validate_settings()
    ms = [Machine(sales=v) for v in [0, 0, 100, 200, 200, None]]
    b = sales_boundaries(ms, s)
    rs = [evaluation(m, s, b) for m in ms]
    assert rs[0]["rank"] == rs[1]["rank"] and rs[3]["color"] == rs[4]["color"]
    assert rs[0]["color"] != rs[-1]["color"]
    assert (
        evaluation(
            Machine(sales="￥１６，１００円"), s, sales_boundaries([], s, snapshot([0, 16100]))
        )["color"]
        == "#FFE16A"
    )
    fixed = validate_settings(dict(sales_mode="fixed", sales_colors=["#123456"] * 5))
    assert evaluation(ms[0], fixed, sales_boundaries(ms, fixed))["color"] == "#123456"
    assert "全5台" in boundary_legend(b)


def test_all_views_and_selection_share_reference_and_provisional_days_are_not_bulk_ready():
    s = validate_settings()
    ms = [
        Machine(ratio=40, sales=10000, installed_on=None),
        Machine(ratio=70, sales=5000),
        Machine(ratio=110, sales=0),
    ]
    b = sales_boundaries(ms, s, snapshot([0, 1000, 5000, 10000, 20000]))
    records = [machine_record(m, DAY, s, sales_bounds=b) for m in ms]
    assert records[0]["assessment"] == "暫定判定"
    assert "日数未確認" in records[0]["assessment_detail"]["display_status"]
    assert records[2]["assessment"] == "好調"
    full = distribution(records, "sales_bin", s, records)
    chosen = distribution(records[:1], "sales_bin", s, records)
    label = records[0]["sales_evaluation"]["label"]
    assert chosen["rows"][0]["label"] == label
    assert chosen["rows"][0]["color"] == next(
        r["color"] for r in full["rows"] if r["label"] == label
    )
    assert (
        aggregate(records[:1], "sales_bin", "number", "count", s, population=records)[0]["label"]
        == label
    )
    ms[0].installed_on = "2026-10-01"
    result = assess(ms[0], DAY, s, sales_bounds=b)
    assert result["status"] == "要確認" and "高売上" in result["display_status"]
    assert result["color"] == "#F4A6A6" and "全5台" in " / ".join(result["reasons"])
    ms[0].match_method = "review"
    assert assess(ms[0], DAY, s, sales_bounds=b)["status"] == "判定保留"


def test_unknown_age_without_ratio_or_sales_is_not_zero_day_or_confirmed():
    s = validate_settings()
    for m in [
        Machine(ratio=40),
        Machine(sales=100),
        Machine(installed_on="2026-12-01", ratio=40, sales=100),
    ]:
        assert assess(m, DAY, s)["status"] == "判定保留"
