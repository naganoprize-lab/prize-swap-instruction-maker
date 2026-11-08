"""One current-report sales reference population, independent of visible booths.

Unmatched Excel records contribute to the reference only: this module never matches
or assigns records. Layout values with unknown periods are labelled reference-only.
"""

import math
from bisect import bisect_left, bisect_right
from .analysis_bands import SALES_LABELS, SALES_COLORS, UNKNOWN
from .floor_status import effective_stat, numeric


def cohort(machine):
    a = machine.analysis or {}
    if a:
        return (
            a.get("period_start"),
            a.get("period_end"),
            (
                "confirmed"
                if a.get("period_start")
                and a.get("period_end")
                and a.get("period_source") in ("excel", "manual")
                else a.get("fingerprint") or a.get("file_name")
            ),
        )
    return (None, None, "layout")


def valid(value):
    value = numeric(value)
    return value if value is not None and value >= 0 else None


class NeutralBounds(list):
    pass


class RankedBounds(list):
    def __init__(self, values, settings):
        self.values = tuple(sorted(v for v in (valid(v) for v in values) if v is not None))
        self.percentiles = tuple(settings.get("sales_percentiles", [0.2, 0.4, 0.6, 0.8]))
        self.colors = tuple(settings.get("sales_colors", SALES_COLORS))
        # List-compatible amount cutoffs remain available to legacy consumers.
        super().__init__(_cutoffs(self.values, self.percentiles))


def _cutoffs(values, ps):
    if not values:
        return []
    result = []
    for p in ps:
        index = (len(values) - 1) * p
        lo = int(index)
        hi = min(len(values) - 1, lo + 1)
        result.append(values[lo] + (values[hi] - values[lo]) * (index - lo))
    return result


def quartiles(values, percentiles=None):
    values = sorted(v for v in (valid(v) for v in values) if v is not None)
    if not values:
        return None
    if len(values) < 2 or values[0] == values[-1]:
        return NeutralBounds([values[0]] * 4)
    return RankedBounds(values, {"sales_percentiles": percentiles or [0.2, 0.4, 0.6, 0.8]})


class SalesBounds(list):
    def __init__(self, groups, settings=None, snapshot=None):
        self.groups = groups
        self.settings = settings or {}
        self.snapshot = snapshot
        primary = next(iter(groups.values()), None)
        super().__init__(primary or [])

    @property
    def primary(self):
        return next(iter(self.groups.values()), None)


def boundaries(machines, settings, snapshot=None):
    groups = {}
    # Only the active snapshot is a current-report population. History is excluded.
    if snapshot and snapshot.get("records"):
        values = [r.get("values", {}).get("sales") for r in snapshot["records"].values()]
        groups[(snapshot.get("period_start"), snapshot.get("period_end"), "excel")] = RankedBounds(
            values, settings
        )
        return SalesBounds(groups, settings, snapshot)
    for m in machines:
        value = valid(effective_stat(m, "sales"))
        if value is not None:
            groups.setdefault(cohort(m), []).append(value)
    return SalesBounds({k: RankedBounds(v, settings) for k, v in groups.items()}, settings)


def value_evaluation(value, bounds, mode="quantile", settings=None):
    s = settings or (bounds.settings if isinstance(bounds, SalesBounds) else {})
    colors = s.get("sales_colors", getattr(bounds, "colors", SALES_COLORS))
    if isinstance(bounds, SalesBounds):
        bounds = bounds.primary
    value = valid(value)
    out = dict(value=value, rank=None, count=0, top_percent=None, index=None, provisional=False)
    if value is None:
        return out | dict(color=UNKNOWN, label="不明：売上データなし", basis="データなし")
    if mode == "fixed":
        i = min(4, bisect_right(s.get("sales_fixed_breaks", bounds or []), value))
        return out | dict(color=colors[i], label=SALES_LABELS[i], index=i, basis="固定金額")
    values = getattr(bounds, "values", ())
    if values:
        n = len(values)
        greater = n - bisect_right(values, value)
        out.update(rank=greater + 1, count=n)
        if n > 1 and values[0] != values[-1]:
            top = min(100.0, greater / (n - 1) * 100)
            # Competition ranks keep tied amounts together at every boundary.
            i = 4 - min(
                4, bisect_left(getattr(bounds, "percentiles", [0.2, 0.4, 0.6, 0.8]), top / 100)
            )
            return out | dict(
                color=colors[i],
                label=SALES_LABELS[i],
                index=i,
                top_percent=top,
                basis=f"{greater+1}位 / 全{n}台・上位{top:.2f}%",
            )
        return out | dict(
            color=colors[2],
            label="同額：同額のため相対比較不可",
            index=2,
            basis=f"全{n}台・同額のため相対比較不可",
        )
    if not bounds:
        return out | dict(
            color=colors[2], label="中程度：比較対象不足", index=2, basis="比較対象不足"
        )
    if isinstance(bounds, NeutralBounds) or len(set(bounds)) == 1:
        return out | dict(
            color=colors[2], label="同額：同額のため相対比較不可", index=2, basis="相対比較不可"
        )
    i = min(4, bisect_right(bounds, value))
    return out | dict(color=colors[i], label=SALES_LABELS[i], index=i, basis="金額境界")


def evaluation(machine, settings, bounds):
    selected = bounds
    provisional = False
    if isinstance(bounds, SalesBounds):
        if bounds.snapshot:
            current = (bounds.snapshot.get("period_start"), bounds.snapshot.get("period_end"))
            period = cohort(machine)[:2]
            if all(period) and all(current) and period != current:
                return dict(
                    value=valid(effective_stat(machine, "sales")),
                    color=UNKNOWN,
                    label="対象期間外",
                    basis="異なる集計期間のため比較しません",
                    rank=None,
                    count=0,
                    top_percent=None,
                    index=None,
                    provisional=True,
                )
            provisional = not all(period) or not machine.analysis
            selected = bounds.primary
        else:
            selected = bounds.groups.get(cohort(machine))
    result = value_evaluation(
        effective_stat(machine, "sales"), selected, settings["sales_mode"], settings
    )
    result["provisional"] = provisional
    if provisional:
        result["basis"] += "（レイアウト由来・期間未確認の参考比較）"
    return result


def classify(value, bounds, mode="quantile"):
    r = value_evaluation(value, bounds, mode)
    return r["color"], r["label"]


def machine_sales_status(machine, settings, bounds):
    r = evaluation(machine, settings, bounds)
    return r["color"], r["label"] + " / " + r["basis"]


def record_evaluation(record, settings, bounds=None):
    # Records built by the common service already carry the exact floor result.
    if record.get("sales_evaluation") is not None:
        return record["sales_evaluation"]
    if isinstance(bounds, SalesBounds):
        bounds = bounds.groups.get(record.get("sales_cohort"), bounds.primary)
    return value_evaluation(record.get("sales"), bounds, settings["sales_mode"], settings)


def record_bounds(records, settings):
    groups = {}
    for r in records:
        if valid(r.get("sales")) is not None:
            groups.setdefault(r.get("sales_cohort"), []).append(r["sales"])
    return SalesBounds({k: RankedBounds(v, settings) for k, v in groups.items()}, settings)


def boundary_legend(bounds, mode="quantile"):
    if isinstance(bounds, SalesBounds):
        if not bounds.snapshot and len(bounds.groups) > 1:
            return "異なる期間は別比較：" + " / ".join(
                f'{k[0] or "レイアウト・期間不明"}：{boundary_legend(v, mode)}'
                for k, v in bounds.groups.items()
            )
        s = bounds.settings
        if mode == "fixed":
            return "固定金額：" + " / ".join(f"{v:,.0f}円" for v in s["sales_fixed_breaks"])
        bounds = bounds.primary
    if not bounds:
        return "有効な売上金額なし"
    values = getattr(bounds, "values", ())
    if isinstance(bounds, NeutralBounds) or values and (len(values) < 2 or values[0] == values[-1]):
        return f"同額のため相対比較不可：{bounds[0]:,.0f}円（中程度の単一色）"
    if values:
        ps = [0, *[p * 100 for p in bounds.percentiles], 100]
        spans = []
        for i in range(5):
            amounts = [v for v in set(values) if value_evaluation(v, bounds)["index"] == 4 - i]
            actual = f"{min(amounts):,.0f}〜{max(amounts):,.0f}円" if amounts else "該当なし"
            spans.append(f"上位{ps[i]:g}〜{ps[i+1]:g}%：{actual}")
        return (
            f"売上金額の相対的な高さ・全{len(values)}台（利益・良否の評価ではありません）／"
            + "／".join(spans)
            + "／同額は同色・期間不明のレイアウト値は参考比較"
        )
    return "売上金額の相対的な高さ／" + " / ".join(f"{v:,.0f}円" for v in bounds)
