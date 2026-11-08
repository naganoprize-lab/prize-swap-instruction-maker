from datetime import date
from dataclasses import asdict
import pytest
from prize_swap.floor_data import Machine, validate_machines
from prize_swap.analysis_settings import validate_settings, sales_boundaries
from prize_swap.floor_status import filter_status, filter_legend
from prize_swap.po_evaluation import evaluate_po, cost_candidate
from prize_swap.radar import evaluate, score_record, RadarChart

DAY = date(2026, 11, 8)


def test_sales_total_quartiles_zero_ties_missing_and_periods():
    s = validate_settings()
    ms = [Machine(sales=v, average=999999) for v in [0, 100, 200, 300, 400, None]]
    b = sales_boundaries(ms, s)
    colors = [
        filter_status(m, "sales_metric", DAY, settings=s | {"_sales_bounds": b})[0] for m in ms
    ]
    assert colors == ["#F4A6A6", "#F8C88A", "#F3F4F6", "#94D8A0", "#FFE16A", "#AEB7C2"]
    assert "80円" in filter_legend("sales_metric", s, b)
    for values in [[0], [0, 1], [2, 2, 2, 2], [0, 0, 10, 10]]:
        group = [Machine(sales=v) for v in values]
        bounds = sales_boundaries(group, s)
        colors = [
            filter_status(m, "sales_metric", DAY, settings=s | {"_sales_bounds": bounds})[0]
            for m in group
        ]
        if len(set(values)) == 1:
            assert set(colors) == {"#F3F4F6"}
        assert len(set((v, c) for v, c in zip(values, colors))) == len(set(values))
    ms[0].analysis = {
        "values": {"sales": 10000},
        "fingerprint": "other",
        "period_start": "2025-01-01",
    }
    b = sales_boundaries(ms, s)
    assert (
        filter_status(ms[0], "sales_metric", DAY, settings=s | {"_sales_bounds": b})[0] == "#F3F4F6"
    )


@pytest.mark.parametrize(
    "price,play,target,expected",
    [(270, 100, 0.6, 2 / 9), (270, 200, 0.6, 4 / 9), (270, 100, 0.54, 0.2)],
)
def test_po_target_formula_and_score(price, play, target, expected):
    s = validate_settings({"cost_target": target})
    m = Machine(
        unit_price=price, play_pricing=dict(amount=play, plays=1, confirmed=True), po_rate=expected
    )
    r = evaluate_po(m, s)
    assert r["target"] == pytest.approx(expected) and r["score"] == pytest.approx(100)
    m.po_rate = expected * 1.5
    assert evaluate_po(m, s)["score"] == pytest.approx(50)
    assert evaluate(m, DAY, s)[-1]["score"] == pytest.approx(50)


def test_po_candidate_requires_confirmation_and_sources_never_use_total_cost():
    m = Machine(product="堅あげ 270", cost=99999)
    assert cost_candidate(m.product) == 270
    r = evaluate_po(m, validate_settings())
    assert r["target"] == pytest.approx(2 / 9) and r["cost_source"] == "商品名末尾"
    assert r["cost_details"]["confidence"] == "estimated" and r["prize_cost"] != m.cost
    m.prize_cost = 270
    m.prize_cost_product = m.product
    r = evaluate_po(m, validate_settings())
    assert r["target"] == pytest.approx(2 / 9) and r["provisional"]
    m.product = "新しい商品 999"
    assert evaluate_po(m, validate_settings())["target"] == pytest.approx(60 / 999)
    m.unit_price = 500
    assert evaluate_po(m, validate_settings())["prize_cost"] == 999
    m.product = "商品名に原価候補なし"
    assert evaluate_po(m, validate_settings())["prize_cost"] == 500
    assert validate_machines([asdict(m)])[0].play_pricing is None


@pytest.mark.parametrize("price,expected", [(0, None), (None, None), (10, 6)])
def test_po_invalid_cost_and_over_hundred_not_clamped(price, expected):
    r = evaluate_po(Machine(unit_price=price, po_rate=0.1), validate_settings())
    assert r["target"] == expected
    assert r["score"] is None
    assert r["label"] == "計算不可"


def test_sales_radar_all_types_totals_and_labels_no_scores():
    s = validate_settings()
    ms = [
        Machine(kind=str(i), sales=v, average=400 - v, ratio=100, unit_price=270, po_rate=0.1)
        for i, v in enumerate([0, 100, 200, 300, 400])
    ]
    peers = [score_record(m, DAY, s) for m in ms]
    assert [evaluate(m, DAY, s, peers)[1]["score"] for m in ms] == [0, 25, 50, 75, 100]
    axes = evaluate(ms[1], DAY, s, peers)
    assert axes[1]["actual"] == "100円"
    assert all(
        "スコア" not in RadarChart.tooltip(a) and "点" not in RadarChart.tooltip(a) for a in axes
    )


def test_assessment_accepts_computed_sales_context_without_mutating_saved_settings():
    s = validate_settings()
    m = Machine(sales=100, ratio=40, installed_on="2026-10-01")
    settings = s | {"_sales_bounds": sales_boundaries([m], s)}
    color, label = filter_status(m, "assessment", DAY, settings=settings)
    assert color == "#F4A6A6" and "要確認" in label
    assert "_sales_bounds" not in s


@pytest.mark.parametrize(
    "text,expected", [("17,900円", 17900), ("￥１７，９００", 17900), ("¥ 0", 0), ("不明", None)]
)
def test_sales_yen_and_fullwidth_numeric_reading(text, expected):
    from prize_swap.floor_status import numeric

    assert numeric(text) == expected


def test_excel_sales_import_does_not_require_average_value_and_uses_optional_po_columns(tmp_path):
    from test_sales_analysis import report_file, data_row
    from openpyxl import load_workbook
    from prize_swap.sales_analysis import load_analysis, apply_snapshot

    row = data_row(sales=17900, average=None, days=1)
    row[8] = 9100
    row[15] = 8800
    row[12] = 1790
    path = report_file(tmp_path / "one-day.xlsx", [row])
    wb = load_workbook(path)
    ws = wb.active
    for col, label, value in [(17, "景品原価", 270), (18, "プレイ料金", 200), (19, "料金回数", 2)]:
        ws.cell(1, col, label)
        ws.cell(2, col, value)
    wb.save(path)
    wb.close()
    snapshot = load_analysis(path)
    assert len(snapshot["records"]) == 1
    m = Machine(number="20088左")
    apply_snapshot([m], snapshot)
    from prize_swap.floor_status import effective_stat

    assert effective_stat(m, "sales") == 17900 and effective_stat(m, "average") is None
    guidance = evaluate_po(m, validate_settings())
    assert guidance["target"] == pytest.approx(2 / 9) and not guidance["provisional"]
    assert guidance["cost_source"] == "正式単価" and guidance["cost_details"]["official"] == 270


@pytest.mark.parametrize("v", [float("nan"), float("inf"), -1, True, "100"])
def test_invalid_confirmed_po_inputs_are_rejected_without_crash(v):
    from prize_swap.core import AppError

    m = Machine(prize_cost=v)
    with pytest.raises(AppError):
        validate_machines([asdict(m)])


def test_discount_pricing_cost_priority_and_source_context():
    m = Machine(
        product="景品 270",
        prize_cost=270,
        prize_cost_product="景品 270",
        play_pricing=dict(amount=500, plays=6, confirmed=True),
        po_rate=0.1,
    )
    g = evaluate_po(m, validate_settings())
    assert g["target"] == pytest.approx(500 / 6 * 0.6 / 270)
    m.unit_price = 1000
    assert "手動確認済み" in evaluate_po(m, validate_settings())["cost_source"]
    assert evaluate_po(m, validate_settings())["prize_cost"] == 270
    m.analysis = {
        "values": {"unit_price": 500, "po_rate": 0.1},
        "product_context": {"product": "旧商品", "installed_on": None},
    }
    assert evaluate_po(m, validate_settings())["score"] is None


def test_invalid_official_price_never_becomes_a_confirmed_single_play(tmp_path):
    from test_sales_analysis import report_file, data_row
    from openpyxl import load_workbook
    from prize_swap.sales_analysis import load_analysis, apply_snapshot

    path = report_file(tmp_path / "bad-price.xlsx", [data_row()])
    wb = load_workbook(path)
    ws = wb.active
    ws.cell(1, 17, "プレイ料金")
    ws.cell(1, 18, "料金回数")
    ws.cell(2, 17, 100)
    ws.cell(2, 18, "不明")
    wb.save(path)
    wb.close()
    s = load_analysis(path)
    m = Machine(number="20088左")
    apply_snapshot([m], s)
    g = evaluate_po(m, validate_settings())
    assert g["target"] is None and g["score"] is None and "要確認" in g["price_source"]
    assert s["issues"]


def test_legacy_average_choices_paused_without_losing_raw_values():
    from prize_swap.analytics_data import validate_ui_state

    condition = {"field": "average", "op": "ge", "value": 1000}
    s = validate_ui_state({"metric": "average", "conditions": [condition]})
    assert s["metric"] == "sales" and not s["conditions"]
    assert s["suspended_average_conditions"] == [condition]
    assert validate_ui_state(s) == s


def test_sales_small_cohort_unrated_and_all_equal_neutral():
    s = validate_settings()
    ms = [Machine(sales=v) for v in [0, 100]]
    peers = [score_record(m, DAY, s) for m in ms]
    assert evaluate(ms[0], DAY, s, peers)[1]["score"] == 0
    ms[1].sales = 0
    peers = [score_record(m, DAY, s) for m in ms]
    assert evaluate(ms[0], DAY, s, peers)[1]["score"] is None


@pytest.mark.parametrize("amount,count", [("100%", 1), (None, 3)])
def test_formal_fee_requires_money_units_and_complete_discount_conditions(tmp_path, amount, count):
    from test_sales_analysis import report_file, data_row
    from openpyxl import load_workbook
    from prize_swap.sales_analysis import load_analysis, apply_snapshot

    path = report_file(tmp_path / "fee.xlsx", [data_row()])
    wb = load_workbook(path)
    ws = wb.active
    ws.cell(1, 17, "プレイ料金")
    ws.cell(1, 18, "料金回数")
    ws.cell(2, 17, amount)
    ws.cell(2, 18, count)
    wb.save(path)
    wb.close()
    snapshot = load_analysis(path)
    m = Machine(number="20088左")
    apply_snapshot([m], snapshot)
    assert evaluate_po(m, validate_settings())["target"] is None
