"""Current-file total-sales cohorts; no average-sales or invented benchmark."""

import math
from .analysis_bands import SALES_LABELS, SALES_COLORS, UNKNOWN
from .floor_status import effective_stat


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


class NeutralBounds(list):
    pass


def quartiles(values, percentiles=None):
    # Kept as the legacy function entry; default now has four quintile boundaries.
    values = sorted(v for v in values if type(v) in (int, float) and math.isfinite(v) and v >= 0)
    if not values:
        return None
    ps = percentiles or [0.2, 0.4, 0.6, 0.8]
    if len(values) < 2 or values[0] == values[-1]:
        return NeutralBounds([values[0]] * 4)

    def percentile(p):
        index = (len(values) - 1) * p
        lo = int(index)
        hi = min(len(values) - 1, lo + 1)
        return values[lo] + (values[hi] - values[lo]) * (index - lo)

    return [percentile(p) for p in ps]


class SalesBounds(list):
    """List-compatible legacy boundary API, with separate per-source/period cohorts."""

    def __init__(self, groups):
        self.groups = groups
        primary = next(iter(groups.values()), None)
        super().__init__(primary or [])


def boundaries(machines, settings):
    if settings["sales_mode"] == "fixed":
        return list(settings["sales_fixed_breaks"])
    groups = {}
    for m in machines:
        v = effective_stat(m, "sales")
        if type(v) in (int, float) and math.isfinite(v) and v >= 0:
            groups.setdefault(cohort(m), []).append(v)
    return SalesBounds({k: quartiles(v, settings["sales_percentiles"]) for k, v in groups.items()})


def classify(value, bounds, mode="quantile"):
    if value is None or not math.isfinite(value) or value < 0:
        return UNKNOWN, "不明：売上データなし"
    if not bounds:
        return SALES_COLORS[2], "中程度：比較対象不足"
    if isinstance(bounds, NeutralBounds) or len(set(bounds)) == 1 and mode != "fixed":
        return SALES_COLORS[2], "同額：同額のため相対比較不可"
    # bisect_right assigns every identical amount to the same band, including tied cutoffs.
    from bisect import bisect_right

    i = min(4, bisect_right(bounds, value))
    return SALES_COLORS[i], SALES_LABELS[i]


def machine_sales_status(machine, settings, bounds):
    if isinstance(bounds, SalesBounds):
        bounds = bounds.groups.get(cohort(machine))
    return classify(effective_stat(machine, "sales"), bounds, settings["sales_mode"])


def boundary_legend(bounds, mode="quantile"):
    if isinstance(bounds, SalesBounds):
        if len(bounds.groups) > 1:
            return "異なる期間・レイアウト由来の値は別々に比較：" + " ／ ".join(
                f'{k[0] or "期間不明"}〜{k[1] or "不明"}：{boundary_legend(v, mode)}'
                for k, v in bounds.groups.items()
            )
        bounds = next(iter(bounds.groups.values()), None)
    if not bounds:
        return "有効な売上金額なし"
    if isinstance(bounds, NeutralBounds):
        return f"同額のため相対比較不可：{bounds[0]:,.2f}円（中程度の単一色）"
    amounts = [f"{v:,.2f}".rstrip("0").rstrip(".") for v in bounds]
    ranges = (
        [f"{amounts[0]}円未満"]
        + [f"{a}円以上〜{b}円未満" for a, b in zip(amounts, amounts[1:])]
        + [f"{amounts[-1]}円以上"]
    )
    return (
        "売上金額の相対的な高さ（利益・良否の評価ではありません）／"
        + "／".join(f"{label}：{span}" for label, span in zip(SALES_LABELS, ranges))
        + "／不明：データなし"
    )
