"""Shared, period-aware analysis views over the existing resolved machine data.

No matching, layout edits, inferred missing values or financial-total radar score here.
"""

from collections import defaultdict, OrderedDict
from copy import deepcopy
import math
import statistics
import unicodedata
from .core import AppError
from .floor_status import effective_stat, ratio, days, money
from .machine_matching import (
    machine_key,
    display_identity,
    metric_info,
    MATCH_LABELS,
    machine_updated_at,
)
from .analysis_settings import machine_type
from .method_analysis import period_key
from .replacement import assess, episode

NUMERIC = {
    "ratio": ("アベ比", "%"),
    "sales": ("売上金額", "円"),
    "average": ("平均売上", "円"),
    "operating_days": ("稼働日数", "日"),
    "days": ("投入日数", "日"),
    "cost_rate": ("原価率", "比率"),
    "po_rate": ("PO率", "比率"),
    "gross_profit": ("粗利益", "円"),
    "dispensed": ("払出数", "個"),
    "per_item": ("個当たり", "円"),
    "unit_price": ("景品単価", "円"),
    "cost": ("原価", "円"),
}
CATEGORIES = {
    "machine_type": "機種",
    "method": "運営方法",
    "product": "商品名",
    "number": "マシン番号",
    "source_key": "管理ナンバー",
    "planned": "投入予定",
    "assessment": "入替候補判定",
    "match_method": "データ照合状態",
    "period_text": "集計期間",
}
FIELDS = {k: v[0] for k, v in NUMERIC.items()} | CATEGORIES
DISPLAY_FIELDS = {k: v for k, v in FIELDS.items() if k != "average"}
DISPLAY_NUMERIC = {k: v for k, v in NUMERIC.items() if k != "average"}
AXES = {
    "machine_type": "機種別",
    "method": "運営方法別",
    "product": "商品別",
    "number": "マシン別",
    "ratio_bin": "アベ比区分別",
    "po_bin": "PO率区分別",
    "cost_bin": "原価率区分別",
    "days_bin": "投入日数区分別",
    "sales_bin": "売上区分別",
    "assessment": "入替候補別",
    "period_text": "期間別",
}
AGGREGATIONS = {
    "sum": "合計",
    "mean": "単純平均",
    "median": "中央値",
    "min": "最小",
    "max": "最大",
    "count": "件数",
    "share": "件数割合",
    "weighted": "売上加重平均",
    "recalculate": "合計原価÷合計売上",
}
RATIOS = {"ratio", "po_rate", "cost_rate"}


def normalized(value):
    return "".join(unicodedata.normalize("NFKC", str(value)).split()).casefold()


def formatted(key, value):
    if value is None:
        return "—"
    if key in ("cost_rate", "po_rate"):
        return f"{value:.2%}"
    if key == "ratio":
        return f"{value:,.1f}%"
    if key == "days" and value < 0:
        return "投入予定"
    if key == "planned":
        return "投入予定" if value else "予定なし"
    return money(value) if key in NUMERIC else str(value)


def period_label(key):
    return f"{key[0]} ～ {key[1]}" if key[0] and key[1] else "対象期間不明"


def machine_record(machine, reference, settings, assignments=None, history=None):
    """The same effective values/provenance used by floor cards and machine details."""
    m = machine
    record = {k: effective_stat(m, k) for k in NUMERIC if k not in ("ratio", "days")}
    record.update(
        uid=m.uid,
        ratio=ratio(m),
        days=days(m, reference),
        number=machine_key(m) or display_identity(m),
        source_key=m.source_key,
        product=m.product,
        machine_type=machine_type(m, settings),
        method=m.operation_method.strip() or "未設定",
        installed_on=m.installed_on,
        planned=bool((assignments or {}).get(machine_key(m))),
        assessment=assess(m, reference, settings, history or [])["status"],
        match_method=MATCH_LABELS[m.match_method],
        period=period_key(m),
        loaded_at=(m.analysis or {}).get("loaded_at") or m.layout_loaded_at,
        extracted_at=(m.analysis or {}).get("extracted_at"),
        source_file=(m.analysis or {}).get("file_name") or "売り場レイアウト",
        updated_at=machine_updated_at(m),
        sources={k: metric_info(m, k) for k in NUMERIC if k != "days"},
    )
    record["sources"]["days"] = dict(
        value=record["days"],
        source=("手動入力の設置日" if "installed_on" in m.manual_updates else "設置日")
        + "と基準日から算出",
        updated_at=m.manual_updates.get("installed_on", ""),
    )
    record["period_text"] = period_label(record["period"])
    record["metric_periods"] = {
        key: (
            record["period"]
            if record["sources"][key]["source"].startswith("景品運営分析表")
            else (None, None, "layout")
        )
        for key in NUMERIC
    }
    from .po_evaluation import evaluate_po
    from .sales_evaluation import cohort

    record["po_guidance"] = evaluate_po(m, settings)
    record["sales_cohort"] = cohort(m)
    return record


def filter_records(records, conditions=(), search="", uids=None):
    needle = normalized(search)
    output = []
    for r in records:
        if uids is not None and r["uid"] not in uids:
            continue
        if needle and not any(needle in normalized(r.get(k, "")) for k in CATEGORIES):
            continue
        matches = True
        for c in conditions:
            key, op, value = c["field"], c["op"], c.get("value")
            current = r.get(key)
            if op == "missing":
                ok = current is None or current == ""
            elif op == "contains":
                ok = current is not None and normalized(value) in normalized(current)
            elif op == "eq":
                ok = current is not None and (
                    current == value if key in NUMERIC else normalized(current) == normalized(value)
                )
            elif current is None:
                ok = False
            elif op == "ge":
                ok = current >= value
            elif op == "le":
                ok = current <= value
            elif op == "range":
                ok = value <= current <= c["upper"]
            else:
                raise AppError("分析条件の比較方法が不正です。")
            if not ok:
                matches = False
                break
        if matches:
            output.append(r)
    return output


def bucket(r, axis, settings):
    if axis in ("machine_type", "method", "product", "assessment", "period_text"):
        return str(r.get(axis) or "不明")
    if axis == "number":
        return f'{r["number"]} ({r["source_key"] or "管理番号なし"})'
    key = {
        "ratio_bin": "ratio",
        "po_bin": "po_rate",
        "cost_bin": "cost_rate",
        "days_bin": "days",
        "sales_bin": "sales",
    }[axis]
    value = r.get(key)
    if value is None:
        return "計算不可" if axis == "po_bin" else "不明"
    if axis == "ratio_bin":
        return (
            "50%未満"
            if value < 50
            else "50〜100%未満" if value < 100 else "100〜200%未満" if value < 200 else "200%以上"
        )
    if axis == "po_bin":
        return r.get("po_guidance", {}).get("label", "計算不可")
    if axis == "cost_bin":
        return (
            "低すぎる・要確認"
            if value < settings["cost_low"]
            else (
                "低め"
                if value < settings["cost_near"]
                else "目標付近" if value <= settings["cost_high"] else "高い"
            )
        )
    if axis == "days_bin":
        return (
            "投入予定"
            if value < 0
            else (
                "0〜6日"
                if value < 7
                else (
                    "7〜13日"
                    if value < 14
                    else "14〜20日" if value < 21 else "21〜27日" if value < 28 else "28日以上"
                )
            )
        )
    from .sales_evaluation import classify

    bounds = settings.get("_sales_record_bounds", {}).get(r.get("sales_cohort"))
    return classify(value, bounds, settings["sales_mode"])[1]


def aggregate(records, axis, metric, operation, settings, stack=None, population=None):
    if axis not in AXES or metric not in FIELDS or operation not in AGGREGATIONS:
        raise AppError("集計条件が不正です。")
    if operation == "sum" and (metric in RATIOS or metric not in NUMERIC):
        raise AppError("比率・カテゴリ値は合計できません。")
    if metric not in NUMERIC and operation not in ("count", "share"):
        raise AppError("文字項目は件数または件数割合で集計してください。")
    if operation == "recalculate" and metric != "cost_rate":
        raise AppError("再計算は原価率に対応します。")
    from .sales_evaluation import quartiles

    sales_groups = defaultdict(list)
    for r in (records if population is None else population):
        if r.get("sales") is not None:
            sales_groups[r.get("sales_cohort")].append(r["sales"])
    settings = settings | {
        "_sales_record_bounds": {
            k: (
                quartiles(v)
                if settings["sales_mode"] != "fixed"
                else [settings[t] for t in ("sales_low", "sales_middle", "sales_high")]
            )
            for k, v in sales_groups.items()
        }
    }
    groups = defaultdict(list)
    denominators = defaultdict(int)
    for r in records:
        key = (
            r.get("metric_periods", {}).get(metric, r["period"])
            if operation not in ("count", "share")
            else r["period"]
        )
        denominators[key] += 1
        groups[
            (key, bucket(r, axis, settings), bucket(r, stack, settings) if stack else "")
        ].append(r)
    output = []
    for (period, label, series), group in groups.items():
        values = [r[metric] for r in group if r.get(metric) is not None]
        value, denominator = None, None
        mode = AGGREGATIONS[operation]
        if operation == "count":
            value = len(group)
        elif operation == "share":
            denominator = denominators[period]
            value = len(group) / denominator if denominator else None
        elif operation in ("weighted", "recalculate"):
            key = "cost" if operation == "recalculate" else metric
            pairs = [
                (r.get(key), r.get("sales"))
                for r in group
                if r.get("metric_periods", {}).get(key, period) == period
                and r.get("metric_periods", {}).get("sales", period) == period
            ]
            pairs = [(v, s) for v, s in pairs if v is not None and s is not None and s > 0]
            denominator = sum(s for _, s in pairs)
            if denominator:
                value = (
                    sum(v for v, _ in pairs)
                    if operation == "recalculate"
                    else sum(v * s for v, s in pairs)
                ) / denominator
            mode += f"（有効{len(pairs)}台、売上分母{money(denominator)}円）"
        elif values:
            value = {
                "sum": sum,
                "mean": statistics.mean,
                "median": statistics.median,
                "min": min,
                "max": max,
            }[operation](values)
        output.append(
            dict(
                label=label,
                series=series,
                period=period,
                period_text=period_label(period),
                value=value,
                count=len(group),
                valid_count=len(values),
                denominator=denominator,
                mode=mode,
                uids=[r["uid"] for r in group],
                products=sorted({r["product"] for r in group}),
            )
        )
    return output


class AnalyticsService:
    """A bounded cache independent of theme, physical coordinates and zoom."""

    def __init__(self, owner):
        self.owner = owner
        self.signature = None
        self._records = []
        self.cache = OrderedDict()
        self.builds = 0

    def records(self):
        o = self.owner
        signature = (
            o.reference_day(),
            repr(o.analysis_settings),
            tuple(
                (
                    m.uid,
                    m.number,
                    m.resolved_number,
                    m.source_key,
                    m.product,
                    m.kind,
                    m.operation_method,
                    m.installed_on,
                    m.ratio,
                    m.ratio_from_excel,
                    m.layout_loaded_at,
                    repr(m.manual_updates),
                    m.prize_cost,
                    m.prize_cost_product,
                    repr(m.play_pricing),
                    m.average,
                    m.sales,
                    m.reference_average,
                    m.po_rate,
                    m.cost_rate,
                    m.operating_days,
                    m.gross_profit,
                    m.dispensed,
                    m.per_item,
                    m.unit_price,
                    m.cost,
                    m.match_method,
                    repr(m.analysis),
                )
                for m in o.machines
            ),
            tuple(
                (key, tuple(p.name for p in products)) for key, products in o.assignments().items()
            ),
            tuple(
                (
                    e["id"],
                    e.get("deleted"),
                    e["snapshot"].get("period_start"),
                    e["snapshot"].get("period_end"),
                )
                for e in o.performance_history
            ),
        )
        if signature != self.signature:
            self._records = [
                machine_record(
                    m,
                    o.reference_day(),
                    o.analysis_settings,
                    o.assignments(),
                    o.performance_history,
                )
                for m in o.machines
            ]
            self.signature = signature
            self.cache.clear()
            self.builds += 1
        return self._records

    def query(
        self,
        conditions=(),
        search="",
        uids=None,
        axis="method",
        metric="sales",
        operation="mean",
        stack=None,
    ):
        records = self.records()
        key = repr(
            (
                conditions,
                search,
                sorted(uids) if uids is not None else None,
                axis,
                metric,
                operation,
                stack,
            )
        )
        if key not in self.cache:
            selected = filter_records(records, conditions, search, uids)
            result = (
                selected,
                aggregate(
                    selected,
                    axis,
                    metric,
                    operation,
                    self.owner.analysis_settings,
                    stack,
                    population=records,
                ),
            )
            self.cache[key] = result
            if len(self.cache) > 32:
                self.cache.popitem(last=False)
        self.cache.move_to_end(key)
        return self.cache[key]


def history_series(owner, records, metric="average"):
    """Only confirmed, disjoint periods and the same known product/method episode."""
    selected = {r["uid"] for r in records}
    machines = [m for m in owner.machines if m.uid in selected]
    entries = [e["snapshot"] for e in owner.performance_history if not e.get("deleted")]
    current = owner.analysis_state
    if current.get("records") and not any(
        e.get("fingerprint") == current.get("fingerprint") for e in entries
    ):
        entries.append(current)
    points, omitted = [], 0
    seen = set()
    for m in machines:
        eligible = []
        for s in entries:
            start, end = s.get("period_start"), s.get("period_end")
            r = s["records"].get(machine_key(m))
            if not r or s.get("period_source") not in ("excel", "manual") or not start or not end:
                continue
            if (
                r.get("product_context") != episode(m)
                or not m.product
                or not m.installed_on
                or start < m.installed_on
                or r.get("operation_context") != m.operation_method
                or not m.operation_method
            ):
                omitted += 1
                continue
            if any(
                i["number"] == machine_key(m) and i["severity"] == "エラー"
                for i in s.get("issues", [])
            ):
                continue
            value = r["values"].get(metric)
            if value is not None:
                eligible.append((start, end, value, s))
        accepted = []
        for start, end, value, s in sorted(
            eligible, key=lambda x: (x[0], x[1], x[3].get("loaded_at", "")), reverse=True
        ):
            if any(not (end < a or start > b) for a, b in accepted):
                omitted += 1
                continue
            accepted.append((start, end))
            key = (m.uid, start, end)
            if key not in seen:
                seen.add(key)
                points.append(
                    dict(
                        label=f"{start} ～ {end}",
                        series=display_identity(m),
                        period=(start, end, "confirmed"),
                        value=value,
                        uids=[m.uid],
                        source=s.get("file_name"),
                        source_file=s.get("file_name"),
                        source_kind=(
                            "景品運営分析表"
                            if s.get("fingerprint") == current.get("fingerprint")
                            else "前回データ（履歴）"
                        ),
                        period_text=f"{start} ～ {end}",
                        loaded_at=s.get("loaded_at"),
                        extracted_at=s.get("extracted_at"),
                        count=1,
                    )
                )
    counts = defaultdict(int)
    for p in points:
        counts[p["series"]] += 1
    return (
        sorted([p for p in points if counts[p["series"]] >= 2], key=lambda p: p["period"]),
        omitted,
    )


def validate_ui_state(value=None):
    defaults = dict(
        version=1,
        chart="horizontal",
        axis="method",
        metric="sales",
        operation="mean",
        stack="machine_type",
        x_metric="ratio",
        y_metric="po_rate",
        conditions=[],
        search="",
        legend=True,
        descending=True,
        sort="descending",
        focus_uids=None,
        detail_size=[1120, 800],
        overview_chart="horizontal",
        overview_methods=False,
        suspended_average_conditions=[],
    )
    if value is None:
        return defaults
    if not isinstance(value, dict) or set(value) - set(defaults):
        raise AppError("分析画面の保存形式が不正です。")
    s = defaults | deepcopy(value)
    if "sort" not in value:
        s["sort"] = "descending" if s["descending"] else "ascending"
    if (
        s["overview_chart"] not in ("horizontal", "pie", "donut")
        or type(s["overview_methods"]) is not bool
    ):
        raise AppError("分布表示設定が不正です。")
    if (
        s["version"] != 1
        or s["axis"] not in AXES
        or s["stack"] not in AXES
        or s["metric"] not in FIELDS
        or s["operation"] not in AGGREGATIONS
        or s["x_metric"] not in NUMERIC
        or s["y_metric"] not in NUMERIC
    ):
        raise AppError("分析画面の選択項目が不正です。")
    if (
        s["chart"]
        not in (
            "horizontal",
            "vertical",
            "pie",
            "donut",
            "line",
            "area",
            "stacked",
            "scatter",
            "histogram",
            "radar",
            "cross",
        )
        or any(type(s[k]) is not bool for k in ("legend", "descending"))
        or not isinstance(s["search"], str)
        or len(s["search"]) > 1000
    ):
        raise AppError("グラフの表示設定が不正です。")
    if (
        s["sort"] not in ("descending", "ascending", "label")
        or s["focus_uids"] is not None
        and (
            not isinstance(s["focus_uids"], list)
            or len(s["focus_uids"]) > 10000
            or any(not isinstance(v, str) or len(v) > 200 for v in s["focus_uids"])
        )
    ):
        raise AppError("分析の並び順・対象マシン設定が不正です。")
    if (
        not isinstance(s["detail_size"], list)
        or len(s["detail_size"]) != 2
        or any(type(v) is not int or not 300 <= v <= 8000 for v in s["detail_size"])
    ):
        raise AppError("詳細画面サイズが不正です。")
    if not isinstance(s["suspended_average_conditions"], list) or len(s["suspended_average_conditions"]) > 50:
        raise AppError("休止中の分析条件が不正です。")
    if not isinstance(s["conditions"], list) or len(s["conditions"]) > 50:
        raise AppError("絞り込み条件は50件までです。")
    for c in s["conditions"] + s["suspended_average_conditions"]:
        if (
            not isinstance(c, dict)
            or c.get("field") not in FIELDS
            or c.get("op") not in ("contains", "eq", "ge", "le", "range", "missing")
        ):
            raise AppError("絞り込み条件が不正です。")
        op, key = c["op"], c["field"]
        if op in ("ge", "le", "range") or op == "eq" and key in NUMERIC:
            vals = [c.get("value"), c.get("upper")] if op == "range" else [c.get("value")]
            if (
                key not in NUMERIC
                or any(type(v) not in (int, float) or not math.isfinite(v) for v in vals)
                or op == "range"
                and vals[0] > vals[1]
            ):
                raise AppError("数値条件の範囲が不正です。")
        elif op != "missing" and (
            not isinstance(c.get("value"), (str, int, float, bool)) or len(str(c["value"])) > 1000
        ):
            raise AppError("検索条件の値が不正です。")
    s["suspended_average_conditions"] += [c for c in s["conditions"] if c["field"] == "average"]
    s["conditions"] = [c for c in s["conditions"] if c["field"] != "average"]
    for key in ("metric", "x_metric", "y_metric"):
        if s[key] == "average":
            s[key] = "sales"
    return s
