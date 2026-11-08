from copy import deepcopy
from datetime import date
import pytest
from PySide6.QtCore import QSize
from PySide6.QtWidgets import QApplication
from prize_swap.radar import evaluate, score_record, RadarChart, AGE_EXPLANATION
from prize_swap.floor_data import Machine
from prize_swap.analysis_settings import validate_settings
from prize_swap.sales_analysis import apply_snapshot, load_analysis
from prize_swap.performance_history import set_period
from test_sales_analysis import report_file, data_row

DAY = date(2026, 11, 7)


def axis(m, key, settings=None, peers=()):
    return next(a for a in evaluate(m, DAY, settings, peers) if a["key"] == key)


@pytest.mark.parametrize(
    "value,expected", [(0, 0), (50, 25), (100, 50), (150, 75), (200, 100), (350, 100), (None, None)]
)
def test_ratio_scores(value, expected):
    assert axis(Machine(ratio=value), "ratio")["score"] == expected


@pytest.mark.parametrize(
    "installed,expected",
    [
        ("2026-11-07", 100),
        ("2026-10-31", 80),
        ("2026-10-24", 60),
        ("2026-10-17", 40),
        ("2026-10-10", 20),
        ("2026-10-03", 0),
        ("2026-09-01", 0),
        ("2026-11-08", None),
        (None, None),
    ],
)
def test_age_is_freshness_not_financial_result(installed, expected):
    a = axis(Machine(installed_on=installed, ratio=250, operating_days=999), "days")
    assert a["score"] == expected and a["note"] == AGE_EXPLANATION


@pytest.mark.parametrize(
    "value,expected",
    [
        (0.2, 0),
        (0.3, 0),
        (0.4, 50),
        (0.5, 75),
        (0.6, 100),
        (0.7, 75),
        (0.8, 0),
        (0.9, 0),
        (0.4916, 72.9),
        (None, None),
    ],
)
def test_cost_fit_scores_not_lower_is_better(value, expected):
    a = axis(Machine(cost_rate=value), "cost_rate")
    assert a["score"] == pytest.approx(expected) if expected is not None else a["score"] is None
    if value is not None and value < 0.4:
        assert "低原価率" in a["warning"]


@pytest.mark.parametrize(
    "value,expected",
    [
        (0, 0),
        (0.02, 40),
        (0.03, 60),
        (0.04, 80),
        (0.05, 100),
        (0.06, 80),
        (0.07, 60),
        (0.08, 40),
        (0.1, 0),
        (None, None),
    ],
)
def test_po_fit_scores(value, expected):
    score = axis(Machine(po_rate=value, unit_price=1200), "po_rate")["score"]
    assert score == pytest.approx(expected) if expected is not None else score is None


def test_po_target_and_boundaries_configurable_without_changing_measurements():
    s = validate_settings()
    s["cost_target"] = 0.6
    m = Machine(po_rate=0.06, unit_price=1000)
    assert axis(m, "po_rate", s)["score"] == 100 and m.po_rate == 0.06
    m.play_pricing = dict(amount=200, plays=1, confirmed=True)
    assert axis(m, "po_rate", s)["score"] == pytest.approx(50)
    assert (
        validate_settings({"radar_min_peers": 7})["radar_min_peers"] == 7
    )  # legacy setting remains readable


def test_sales_uses_type_period_rank_and_excludes_insufficient_or_changed_context(tmp_path):
    s = validate_settings()
    ms = []
    for i in range(5):
        m = Machine(
            number=f"{20100+i}右", kind="UFO9", product="商品", installed_on="2026-10-01", ratio=100
        )
        row = data_row(f"{20100+i}-02", sales=(i + 1) * 3000, average=(i + 1) * 1000)
        row[8] = (i + 1) * 2000
        row[15] = (i + 1) * 1000
        row[12] = (i + 1) * 150
        snap = set_period(
            load_analysis(report_file(tmp_path / f"{i}.xlsx", [row])), "2026-11-01", "2026-11-03"
        )
        snap["records"][m.number]["product_context"] = dict(
            product="商品", installed_on="2026-10-01"
        )
        apply_snapshot([m], snap)
        ms.append(m)
    peers = [score_record(m, DAY, s) for m in ms]
    assert [axis(m, "sales", s, peers)["score"] for m in ms] == [0, 25, 50, 75, 100]
    assert axis(ms[0], "ratio", s)["score"] == 50  # distinct from sales percentile
    ms[0].product = "新商品"
    assert axis(ms[0], "sales", s, peers)["score"] is None
    ms[0].product = "商品"
    assert axis(ms[0], "sales", s, peers[:4])["score"] == 0
    peers[1]["sales_cohort"] = ("2026-11-04", "2026-11-06", "confirmed")
    assert axis(ms[0], "sales", s, peers)["score"] == 0


def test_partial_radar_renders_markers_without_closed_invented_polygon():
    axes = evaluate(Machine(ratio=85, po_rate=0, unit_price=1200, installed_on="2026-11-01"), DAY)
    chart = RadarChart()
    chart.resize(460, 380)
    chart.set_axes(axes)
    chart.show()
    QApplication.processEvents()
    image = chart.grab().toImage()
    assert not image.isNull()
    assert sum(a["score"] is not None for a in axes) == 3
    assert len(chart.hit_targets) == 10 and "未評価" in chart.accessibleDescription()
    assert chart.tooltip_at(chart.hit_targets[0][0].center())
    assert AGE_EXPLANATION in chart.accessibleDescription()
    assert (
        len({image.pixelColor(x, y).name() for x in range(0, 460, 5) for y in range(0, 380, 5)}) > 5
    )
    chart.close()
