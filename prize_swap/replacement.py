"""Explainable assessments. Insufficient history is never inferred from one report."""

from datetime import datetime, timezone
from .analysis_settings import validate_settings
from .machine_matching import machine_key
from .floor_status import ratio, days, effective_stat
from .floor_dashboard import freshness


def episode(machine):
    return dict(product=machine.product, installed_on=machine.installed_on)


def comparable_trend(machine, history, metric="average"):
    overlay = machine.analysis or {}
    current = overlay.get("values", {}).get(metric)
    start, end = overlay.get("period_start"), overlay.get("period_end")
    context = overlay.get("product_context")
    if (
        current is None
        or not start
        or not end
        or not context
        or not context.get("product")
        or not context.get("installed_on")
        or overlay.get("period_source") not in ("excel", "manual")
        or context != episode(machine)
        or metric == "sales"
        and start < machine.installed_on
    ):
        return None, []
    from datetime import date

    span = (date.fromisoformat(end) - date.fromisoformat(start)).days
    candidates = []
    for entry in history:
        if entry.get("deleted"):
            continue
        snapshot = entry.get("snapshot", entry)
        if snapshot.get("period_source") not in ("excel", "manual"):
            continue
        prior = snapshot.get("records", {}).get(machine_key(machine))
        stop = snapshot.get("period_end")
        begin = snapshot.get("period_start")
        if not prior or not begin or not stop or stop >= start:
            continue
        if prior.get("product_context") != context:
            continue
        if (
            metric == "sales"
            and (date.fromisoformat(stop) - date.fromisoformat(begin)).days != span
        ):
            continue
        if metric == "sales" and (
            prior.get("operation_context") != overlay.get("operation_context")
            or overlay.get("operation_context") != machine.operation_method
            or not machine.operation_method
        ):
            continue
        value = prior["values"].get(metric)
        if value is None or value <= 0:
            continue
        candidates.append((stop, begin, value, snapshot.get("fingerprint", "")))
    if not candidates:
        return None, []
    latest = max(candidates, key=lambda item: (item[0], item[1]))
    # If the nearest comparison period itself has competing/overlapping reports,
    # do not choose an arbitrary one as a trend baseline.
    overlaps = [c for c in candidates if c[1] <= latest[0] and c[0] >= latest[1]]
    if len({c[3] for c in overlaps}) > 1:
        return None, []
    return current / latest[2], [latest[3], overlay.get("fingerprint", "")]


def assess(machine, reference, settings=None, history=(), sales_bounds=None):
    s = validate_settings({k: v for k, v in (settings or {}).items() if not k.startswith("_")})
    sales_bounds = (
        sales_bounds if sales_bounds is not None else (settings or {}).get("_sales_bounds")
    )
    value = ratio(machine)
    age = days(machine, reference)
    status, priority = "判定保留", 3
    reasons = []
    overlay = machine.analysis or {}
    context = overlay.get("product_context")
    changed = context is not None and context != episode(machine)
    if machine.match_method == "review":
        reasons.append("機械番号の照合要確認のため判定保留")
    elif overlay.get("quality_issues"):
        reasons.append("実績の重大な異常を確認するまで判定保留")
    elif overlay and overlay.get("period_source") not in ("excel", "manual"):
        reasons.append("対象期間が未確定のため判定保留（期間の確認・手動設定が必要）")
    elif changed:
        reasons.append("商品または設置日が実績読込時から変わったため判定保留")
    elif value is None or age is not None and age < 0:
        reasons.append("アベ比・設置日などの有効データが不足しています。")
    elif age is None and value < s["consider_ratio"]:
        if effective_stat(machine, "sales") is not None:
            status, priority = "暫定判定", 1
            reasons.append(
                ("要確認候補" if value < s["poor_ratio"] else "入替検討候補")
                + "（日数未確認）：設置日不明のため日数条件は未成立。"
            )
        else:
            reasons.append("判定材料不足：設置日・売上が不明です。")
    elif value < s["poor_ratio"] and age >= s["poor_days"]:
        status, priority = "要確認", 0
        reasons.append(f"投入{age}日・アベ比{value:.1f}%のため要確認")
    elif s["poor_ratio"] <= value < s["consider_ratio"] and age >= s["consider_days"]:
        status, priority = "入替検討", 1
        reasons.append(f"投入{age}日・アベ比{value:.1f}%のため入替検討")
    elif value >= s["consider_ratio"]:
        status, priority = "好調", 2
        reasons.append(f"アベ比{value:.1f}%で好調")
    else:
        reasons.append("投入日数が判定条件に達していません。")
    if not machine.analysis:
        reasons.append("実績データの取得元：レイアウト由来")
    po, cost = effective_stat(machine, "po_rate"), effective_stat(machine, "cost_rate")
    from .po_evaluation import evaluate_po

    guidance = evaluate_po(machine, s)
    po = guidance["actual"]
    if po is not None and guidance["label"] in ("適正より低い", "適正より高い"):
        reasons.append(f"PO率{po:.2%}：理論目安から{guidance['label']}（原価・料金条件を確認）")
    elif po is not None and guidance["target"] is None:
        reasons.append("PO率の理論目安：原価・料金条件不足で未評価")
    if cost is not None and (cost < s["cost_near"] or cost > s["cost_high"]):
        reasons.append(
            f'原価率{cost:.1%}：目標{s["cost_target"]:.0%}との差を確認（入替確定条件ではありません）'
        )
    if age is not None and age >= s["long_days"]:
        reasons.append(f"長期投入：{age}日")
    stamp = freshness(overlay, s["stale_days"])
    if "古い" in stamp or "不明" in stamp or "未来" in stamp:
        reasons.append(stamp)
    trend, sources = comparable_trend(machine, history, "sales")
    if trend is None:
        reasons.append(
            "売上推移：未評価（商品・設置日・運営方法が一致する同日数の非重複期間が必要）"
        )
    elif trend < s["decline_ratio"]:
        reasons.append("同日数・非重複期間の売上金額が前回より低下しています")
    from .sales_evaluation import evaluation, boundaries

    sales = evaluation(
        machine, s, sales_bounds if sales_bounds is not None else boundaries([machine], s)
    )
    provisional = status == "暫定判定" or sales["provisional"]
    display_status = reasons[0].split("：")[0] if status == "暫定判定" else status
    if status in ("要確認", "入替検討") and sales["index"] in (3, 4):
        display_status += "・高売上"
        reasons.append(
            "売上は全台比較で高い一方、アベ比は基準未満です。基準アベレージ・運営条件を確認し、入替を自動確定しません。"
        )
    if status == "好調" and age is None:
        reasons.append("日数未確認：好調判定はアベ比によります。")
    reasons.append(
        "売上 "
        + (f'{sales["value"]:,.0f}円' if sales["value"] is not None else "不明")
        + " / "
        + sales["basis"]
    )
    reasons.append(
        "投入日数："
        + (f"{age}日" if age is not None else "不明（日数条件を満たしたとは扱いません）")
    )
    conditions = f'要確認：アベ比{s["poor_ratio"]:g}%未満・{s["poor_days"]}日以上／入替検討：{s["poor_ratio"]:g}〜{s["consider_ratio"]:g}%未満・{s["consider_days"]}日以上'
    reasons.append(
        ("暫定・要追加確認" if provisional else "設定ルールによる参考判定") + " / " + conditions
    )
    color = {
        "要確認": "#F4A6A6",
        "入替検討": "#F6BE78",
        "暫定判定": "#FFE16A",
        "好調": "#94D8A0",
        "判定保留": "#AEB7C2",
    }[status]
    return dict(
        display_status=display_status,
        provisional=provisional,
        sales_evaluation=sales,
        conditions=conditions,
        missing=[
            k
            for k, v in {"設置日": age, "アベ比": value, "売上": sales["value"]}.items()
            if v is None
        ],
        color=color,
        machine_id=machine_key(machine),
        uid=machine.uid,
        status=status,
        priority=priority,
        reasons=reasons,
        trend=trend,
        evaluated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        source_record_ids=sources or [overlay.get("fingerprint", "")],
    )
