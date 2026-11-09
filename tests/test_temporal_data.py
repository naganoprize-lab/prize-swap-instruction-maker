from copy import deepcopy
from dataclasses import asdict
from datetime import date
from prize_swap.daily_reference import ReferenceLibrary
from prize_swap.temporal_data import build_frame
from prize_swap.floor_data import Machine
from prize_swap.floor_status import effective_stat, ratio
from prize_swap.analysis_settings import validate_settings, sales_boundaries
from prize_swap.sales_evaluation import evaluation
from test_daily_reference import workbook


def frame(tmp_path, values=(100, 300), plays=True):
    for i, v in enumerate(values):
        workbook(tmp_path / f"file{i}.xlsx", day=f"2026/11/{6+i:02}", sales=v)
    lib = ReferenceLibrary(tmp_path / "cache.sqlite3")
    lib.scan(tmp_path)
    docs, c = lib.selection("2026-11-06", "2026-11-07", tmp_path)
    if plays:
        for d in docs:
            d["snapshot"]["records"]["20111右"]["po_inputs"] = dict(
                actual_plays=10 if d["metadata"]["start"].endswith("06") else 30,
                acquired_count=1,
                inserted_amount=d["snapshot"]["records"]["20111右"]["values"]["sales"],
            )
    master = Machine(
        source_key="R_66",
        product="現在の商品",
        x=750,
        y=450,
        width=200,
        height=150,
        ratio=999,
        sales=999999,
    )
    return master, docs, c


def test_period_sum_ratios_denominators_and_physical_master_untouched(tmp_path):
    master, docs, c = frame(tmp_path)
    before = asdict(master)
    f = build_frame([master], docs, "2026-11-06", "2026-11-07", c)
    m = f.machines[0]
    assert effective_stat(m, "sales") == 400
    assert effective_stat(m, "cost_rate") == 0.5  # Raw official aligned cost sums.
    assert effective_stat(m, "po_rate") == 2 / 40
    assert ratio(m) == 100
    assert m.temporal_summary["sales_days"] == 2 and not m.temporal_summary["partial"]
    assert (m.uid, m.x, m.y, m.width, m.height) == (master.uid, 750, 450, 200, 150)
    assert asdict(master) == before and f.snapshot["records"]["20111右"]["values"]["sales"] == 400


def test_no_plays_does_not_average_po_and_missing_calendar_days_are_not_zero(tmp_path):
    master, docs, c = frame(tmp_path, plays=False)
    c.update(expected=7, missing=5, partial=True)
    f = build_frame([master], docs, "2026-11-06", "2026-11-12", c)
    assert (
        effective_stat(f.machines[0], "sales") == 400
        and effective_stat(f.machines[0], "po_rate") is None
    )
    assert f.coverage["missing"] == 5
    empty = build_frame(
        [master],
        [],
        "2026-11-08",
        "2026-11-08",
        dict(expected=1, acquired=0, missing=1, partial=True, warnings=[], dates=[]),
    )
    assert effective_stat(empty.machines[0], "sales") is None and ratio(empty.machines[0]) is None
    assert empty.machines[0].product == ""


def test_day_uses_layout_product_management_mapping_and_explicit_day_edits(tmp_path):
    master, docs, c = frame(tmp_path)
    day = docs[0]
    before = deepcopy(day)
    edits = {
        day["id"]: {
            master.uid: dict(
                product="確認した景品 100", sales=0, _updated_at="2026-11-09T00:00:00+00:00"
            )
        }
    }
    f = build_frame(
        [master],
        [day],
        "2026-11-06",
        "2026-11-06",
        dict(expected=1, acquired=1, missing=0, partial=False, warnings=[], dates=["2026-11-06"]),
        edits,
    )
    m = f.machines[0]
    assert m.product == "確認した景品 100" and effective_stat(m, "sales") == 0
    assert m.resolved_number == "20111右" and not m.temporal_summary["read_only"]
    assert day == before and master.product == "現在の商品"


def test_partial_machine_coverage_is_not_ranked_against_whole_period(tmp_path):
    master, docs, c = frame(tmp_path)
    docs[0]["snapshot"]["records"].clear()
    docs[0]["layouts"][0]["sales"] = None
    f = build_frame([master], docs, "2026-11-06", "2026-11-07", c)
    m = f.machines[0]
    s = validate_settings()
    b = sales_boundaries(f.machines, s, f.snapshot)
    assert effective_stat(m, "sales") == 300
    assert not m.temporal_summary["sales_comparable"] and evaluation(m, s, b)["index"] is None


def test_cost_confirmation_does_not_leak_from_current_master_into_other_day(tmp_path):
    from prize_swap.temporal_data import day_projection
    from prize_swap.daily_reference import read_document
    from test_daily_reference import workbook
    from prize_swap.costing import cost_profile

    master = Machine(
        source_key="R_66",
        product="景品 100",
        prize_cost=900,
        prize_cost_product="景品 100",
        manual_updates={"prize_cost": "2026-11-09T10:00:00+09:00"},
    )
    doc = read_document(workbook(tmp_path / "one.xlsx"))
    projection, _ = day_projection([master], doc)
    assert projection[0].prize_cost is None and cost_profile(projection[0])["effective"] == 100


def test_history_method_totals_use_play_denominators_and_layout_has_confirmed_period(tmp_path):
    from prize_swap.method_analysis import totals, period_key
    from prize_swap.analytics_data import machine_record

    master, docs, coverage = frame(tmp_path)
    matched = build_frame([master], docs, "2026-11-06", "2026-11-07", coverage)
    result = totals(matched.machines, validate_settings())[0]
    assert result["po_rate"] == 2 / 40 and "期間プレイ回数" in result["po_mode"]
    assert result["period"] == ("2026-11-06", "2026-11-07", "confirmed")
    # A known file day also belongs to an unmatched layout value, without inventing a match.
    docs[0]["snapshot"]["records"] = {}
    c = dict(expected=1, acquired=1, missing=0, partial=False, warnings=[], dates=["2026-11-06"])
    single = build_frame([master], [docs[0]], "2026-11-06", "2026-11-06", c).machines[0]
    record = machine_record(single, date(2026, 11, 6), validate_settings())
    assert single.analysis is None and record["period"][:2] == ("2026-11-06", "2026-11-06")
    assert record["metric_periods"]["sales"][:2] == ("2026-11-06", "2026-11-06")
