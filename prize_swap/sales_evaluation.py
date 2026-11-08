"""Current-file total-sales cohorts; no average-sales or invented benchmark."""

import math
from statistics import quantiles
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


def quartiles(values):
    values = sorted(v for v in values if type(v) in (int, float) and math.isfinite(v) and v >= 0)
    if not values:
        return None
    # Interpolated inclusive quartiles; tied values always receive the same label.
    if len(values) < 4 or values[0] == values[-1]:
        return NeutralBounds([values[0], values[len(values) // 2], values[-1]])
    return list(quantiles(values, n=4, method="inclusive"))


class SalesBounds(list):
    """List-compatible legacy boundary API, with separate per-source/period cohorts."""

    def __init__(self, groups):
        self.groups = groups
        primary = next(iter(groups.values()), None)
        super().__init__(primary or [])


def boundaries(machines, settings):
    if settings["sales_mode"] == "fixed":
        return [settings[k] for k in ("sales_low", "sales_middle", "sales_high")]
    groups = {}
    for m in machines:
        v = effective_stat(m, "sales")
        if type(v) in (int, float) and math.isfinite(v) and v >= 0:
            groups.setdefault(cohort(m), []).append(v)
    return SalesBounds({k: quartiles(v) for k, v in groups.items()})


def classify(value, bounds, mode="quantile"):
    if value is None or not math.isfinite(value) or value < 0:
        return "#B8BEC6", "不明：売上データなし"
    if not bounds or len(bounds) != 3:
        return "#FFFFFF", "標準：比較対象不足"
    low, middle, high = bounds
    if isinstance(bounds, NeutralBounds) and mode != "fixed":
        return "#FFFFFF", "標準：同額または比較対象不足"
    if mode != "fixed":
        if low == middle or middle == high:
            # All-equal/small/tied quartile edge: avoid creating artificial winners/losers.
            if low == high:
                return "#FFFFFF", "標準：同額または比較対象不足"
        return (
            ("#F4A6A6", "低売上：下位25%")
            if value < low
            else (
                ("#94D8A0", "高売上：上位25%")
                if value > high
                else ("#FFFFFF", "標準：中位50%（境界同額を含む）")
            )
        )
    return (
        ("#F4A6A6", "低売上")
        if value < low
        else (("#94D8A0", "高売上") if value >= high else ("#FFFFFF", "標準"))
    )


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
    low, middle, high = (f"{v:,.2f}".rstrip("0").rstrip(".") for v in bounds)
    if isinstance(bounds, NeutralBounds) or bounds[0] == bounds[2]:
        return f"同額・比較対象不足：{low}〜{high}円／標準（白）"
    return f'低売上 < {low}円（赤）／標準 {low}円以上〜{high}円{"未満" if mode == "fixed" else "以下"}（白）／高売上 {">=" if mode == "fixed" else ">"} {high}円（緑）／不明（灰）'
