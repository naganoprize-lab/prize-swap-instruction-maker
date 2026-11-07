"""Period-separated performance summaries with disclosed averaging denominators."""

from collections import defaultdict
from .analysis_settings import machine_type
from .floor_status import effective_stat, ratio


def mean(values):
    values = [v for v in values if v is not None]
    return sum(values) / len(values) if values else None


def period_key(machine):
    a = machine.analysis or {}
    start, end = a.get("period_start"), a.get("period_end")
    if start and end and a.get("period_source") in ("excel", "manual"):
        return (start, end, "confirmed")
    return (None, None, a.get("fingerprint") or a.get("loaded_at") or "legacy")


def totals(machines, settings=None, type_filter="", method_filter=""):
    groups = defaultdict(list)
    for m in machines:
        kind = machine_type(m, settings)
        method = m.operation_method.strip() or "未設定"
        if type_filter and kind != type_filter:
            continue
        if method_filter and method != method_filter:
            continue
        groups[(period_key(m), method)].append(m)
    output = []
    for (period, method), group in sorted(groups.items(), key=lambda item: str(item[0])):

        def values(key):
            return [effective_stat(m, key) for m in group]

        def total(key):
            vals = [v for v in values(key) if v is not None]
            return sum(vals) if vals else None

        def weighted(key):
            pairs = [(effective_stat(m, key), effective_stat(m, "sales")) for m in group]
            pairs = [(v, s) for v, s in pairs if v is not None and s is not None and s > 0]
            denominator = sum(s for _, s in pairs)
            return (
                sum(v * s for v, s in pairs) / denominator if denominator else None,
                len(pairs),
                denominator,
            )

        po, count, po_denominator = weighted("po_rate")
        cost_pairs = [(effective_stat(m, "cost"), effective_stat(m, "sales")) for m in group]
        cost_pairs = [(c, s) for c, s in cost_pairs if c is not None and s is not None and s > 0]
        cost_denominator = sum(s for _, s in cost_pairs)
        if cost_denominator:
            cost = sum(c for c, _ in cost_pairs) / cost_denominator
            cost_mode = f"合計原価÷合計売上（{len(cost_pairs)}台・売上{cost_denominator:,.0f}円分）"
        else:
            cost, cost_count, cost_denominator = weighted("cost_rate")
            cost_mode = (
                f"売上加重平均（{cost_count}台・売上{cost_denominator:,.0f}円分）"
                if cost is not None
                else "単純平均（売上分母なし）"
            )
            if cost is None:
                cost = mean(values("cost_rate"))
        po_mode = (
            f"売上加重平均（{count}台・売上{po_denominator:,.0f}円分）"
            if po is not None
            else "単純平均（売上分母なし）"
        )
        if po is None:
            po = mean(values("po_rate"))
        products = sorted(set(m.product for m in group if m.product))
        output.append(
            dict(
                method=method,
                uids=[m.uid for m in group],
                count=len(group),
                average_sales=mean(values("average")),
                sales=total("sales"),
                po_rate=po,
                po_arithmetic=mean(values("po_rate")),
                po_mode=po_mode,
                cost_rate=cost,
                cost_arithmetic=mean(values("cost_rate")),
                cost_mode=cost_mode,
                ratio=mean([ratio(m) for m in group]),
                gross_profit=total("gross_profit"),
                period=period,
                products=products,
                types=sorted(set(machine_type(m, settings) for m in group)),
                coverage={
                    k: sum(v is not None for v in values(k))
                    for k in ("sales", "average", "po_rate", "cost_rate", "gross_profit")
                },
            )
        )
    return output
