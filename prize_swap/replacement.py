"""Explainable assessments. Insufficient history is never inferred from one report."""

from datetime import datetime, timezone
from .analysis_settings import validate_settings
from .machine_matching import machine_key
from .floor_status import ratio, days, effective_stat
from .floor_dashboard import freshness


def episode(machine):
    return dict(product=machine.product, installed_on=machine.installed_on)


def comparable_trend(machine, history):
    overlay = machine.analysis or {}
    current = overlay.get("values", {}).get("average")
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
    ):
        return None, []
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
        value = prior["values"].get("average")
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


def assess(machine, reference, settings=None, history=()):
    s = validate_settings(settings)
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
    elif value is None or age is None or age < 0:
        reasons.append("アベ比・設置日などの有効データが不足しています。")
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
    if guidance["label"] in ("低い", "高い"):
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
    trend, sources = None, []
    if trend is None:
        reasons.append("売上推移：未評価（平均売上による評価は一時停止中）")
    elif trend < s["decline_ratio"]:
        reasons.append("別期間の実績を検出（売上推移の評価は一時停止）")
    return dict(
        machine_id=machine_key(machine),
        uid=machine.uid,
        status=status,
        priority=priority,
        reasons=reasons,
        trend=trend,
        evaluated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        source_record_ids=sources or [overlay.get("fingerprint", "")],
    )
