from datetime import date
from copy import deepcopy
import pytest
from prize_swap.floor import FloorWidget
from prize_swap.floor_data import Machine, validate_state
from prize_swap.analytics_data import (
    machine_record,
    filter_records,
    aggregate,
    validate_ui_state,
    history_series,
)
from prize_swap.analysis_settings import validate_settings
from prize_swap.floor_status import effective_stat, ratio
from prize_swap.core import AppError
from test_sales_analysis import report_file, data_row
from prize_swap.performance_history import set_period
from prize_swap.sales_analysis import load_analysis

DAY = date(2026, 11, 7)


def records():
    settings = validate_settings()
    machines = [
        Machine(
            number=f"{20000+i}右",
            product=f"商品{i}",
            operation_method="リング",
            sales=s,
            average=s / 2,
            cost_rate=c,
            po_rate=p,
            ratio=v,
            installed_on="2026-11-01",
        )
        for i, (s, c, p, v) in enumerate([(100, 0.6, 0.05, 120), (300, 0.8, 0.1, 85), (0, 0, 0, 0)])
    ]
    return [machine_record(m, DAY, settings) for m in machines]


def test_shared_values_missing_zero_and_compound_conditions():
    rs = records()
    assert rs[-1]["sales"] == 0 and rs[-1]["po_rate"] == 0 and rs[-1]["days"] == 6
    assert len(filter_records(rs, [dict(field="sales", op="eq", value=0.0)])) == 1
    rs.append(rs[0] | dict(uid="missing", sales=None, po_rate=None))
    assert (
        len(
            filter_records(
                rs,
                [
                    dict(field="sales", op="range", value=0, upper=150),
                    dict(field="po_rate", op="le", value=0.06),
                ],
            )
        )
        == 2
    )
    assert [r["uid"] for r in filter_records(rs, [dict(field="sales", op="missing")])] == [
        "missing"
    ]
    assert len(filter_records(rs, search="商 品２")) == 1


def test_aggregate_weighted_recalculated_median_and_period_separation():
    rs = records()
    s = validate_settings()
    assert aggregate(rs, "method", "sales", "sum", s)[0]["value"] == 400
    assert aggregate(rs, "method", "sales", "median", s)[0]["value"] == 100
    out = aggregate(rs, "method", "po_rate", "weighted", s)[0]
    assert (
        out["value"] == pytest.approx(0.0875)
        and out["denominator"] == 400
        and len(out["uids"]) == 3
    )
    rs[0]["cost"] = 60
    rs[1]["cost"] = 240
    assert aggregate(rs, "method", "cost_rate", "recalculate", s)[0]["value"] == 0.75
    for r in rs:
        r["period"] = ("2026-11-01", "2026-11-03", "confirmed")
    rs[1]["period"] = ("2026-11-04", "2026-11-06", "confirmed")
    for r in rs:
        r["metric_periods"] = {key: r["period"] for key in r["metric_periods"]}
    out = aggregate(rs, "method", "sales", "sum", s)
    assert len(out) == 2 and {r["value"] for r in out} == {100, 300}
    with pytest.raises(AppError):
        aggregate(rs, "method", "po_rate", "sum", s)
    with pytest.raises(AppError):
        aggregate(rs, "method", "product", "mean", s)


def test_layout_fallback_metric_does_not_claim_current_analysis_period(tmp_path):
    from prize_swap.sales_analysis import apply_snapshot

    m = Machine(number="20111右", product="現在商品", ratio=85)
    s = set_period(
        load_analysis(report_file(tmp_path / "source.xlsx", [data_row("20111-02")])),
        "2026-11-01",
        "2026-11-03",
    )
    apply_snapshot([m], s)
    r = machine_record(m, DAY, validate_settings())
    assert r["ratio"] == 85 and r["period"][0] == "2026-11-01"
    assert (
        r["metric_periods"]["ratio"][:2] == (None, None)
        and r["metric_periods"]["sales"][0] == "2026-11-01"
    )
    output = aggregate([r], "method", "ratio", "weighted", validate_settings())[0]
    assert output["value"] is None and output["period_text"] == "対象期間不明"


def test_cache_preserves_layout_and_invalidates_on_effective_data_change(tmp_path):
    w = FloorWidget(lambda: [], lambda: DAY, tmp_path / "auto.json")
    try:
        w.machines = [
            Machine(number="20111右", source_key="L_66", sales=100, ratio=85, x=125, y=342)
        ]
        w.rebuild()
        old = deepcopy(w.state()["machines"])
        result = w.analytics.records()
        assert result[0]["ratio"] == ratio(w.machines[0])
        assert result[0]["sales"] == effective_stat(w.machines[0], "sales")
        w.set_zoom(150)
        assert w.analytics.records() is result and w.analytics.builds == 1
        w.analytics.query()
        w.analytics.query()
        assert len(w.analytics.cache) == 1
        w.machines[0].sales = 200
        assert w.analytics.records()[0]["sales"] == 200 and w.analytics.builds == 2
        assert [(m.x, m.y, m.width, m.height) for m in w.machines] == [
            (m["x"], m["y"], m["width"], m["height"]) for m in old
        ]
        saved = w.state()
        saved["analytics_ui"]["detail_size"] = [1300, 850]
        w.restore(saved)
        assert w.analytics_ui["detail_size"] == [1300, 850]
        saved.pop("analytics_ui")
        w.restore(saved)
        assert w.analytics_ui == validate_ui_state()
    finally:
        w.timer.stop()
        w.day_timer.stop()
        w.close()


def test_history_requires_method_episode_disjoint_periods_and_two_points(tmp_path):
    w = FloorWidget(lambda: [], lambda: DAY, tmp_path / "auto.json")
    try:
        w.machines = [
            Machine(
                number="20111右",
                product="商品",
                operation_method="リング",
                installed_on="2026-10-01",
            )
        ]
        w.rebuild()
        for i, (a, b) in enumerate(
            [
                ("2026-10-01", "2026-10-03"),
                ("2026-10-04", "2026-10-06"),
                ("2026-10-05", "2026-10-07"),
            ]
        ):
            snap = set_period(
                load_analysis(report_file(tmp_path / f"r{i}.xlsx", [data_row("20111-02")])), a, b
            )
            r = snap["records"]["20111右"]
            r["product_context"] = dict(product="商品", installed_on="2026-10-01")
            r["operation_context"] = "リング"
            w.performance_history.append(dict(id=str(i), snapshot=snap, deleted=False))
        points, omitted = history_series(w, w.analytics.records())
        assert len(points) == 2 and omitted == 1
        w.machines[0].operation_method = "フック"
        assert history_series(w, w.analytics.records())[0] == []
        w.machines[0].operation_method = "リング"
        w.performance_history[0]["snapshot"]["records"]["20111右"].pop("operation_context")
        assert history_series(w, w.analytics.records())[0] == []
    finally:
        w.timer.stop()
        w.day_timer.stop()
        w.close()


@pytest.mark.parametrize(
    "bad",
    [
        {"metric": "景品種別"},
        {"detail_size": [1, 2]},
        {"conditions": [dict(field="sales", op="range", value=3, upper=2)]},
    ],
)
def test_invalid_saved_conditions_are_rejected(bad):
    with pytest.raises(AppError):
        validate_ui_state(bad)
