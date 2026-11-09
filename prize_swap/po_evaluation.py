"""Explainable theoretical payout-per-play guidance; ratios are fractions, never percent points."""

import math
import re
import unicodedata
from .core import AppError
from .floor_status import effective_stat

PO_DEFINITIONS = {
    "unknown": "定義未確認",
    "prize_per_play": "景品獲得数÷プレイ回数",
    "other": "その他の比率",
}


def raw_po(machine):
    value = (
        (machine.analysis or {}).get("values", {}).get("po_rate")
        if machine.analysis
        else machine.po_rate
    )
    return value if type(value) in (float, int) and math.isfinite(value) and value >= 0 else None


def po_definition(machine):
    source = machine.analysis or {}
    override = getattr(machine, "po_definition", "prize_per_play")
    if source:
        if getattr(machine, "po_definition_fingerprint", "") == source.get(
            "fingerprint"
        ) and getattr(machine, "po_definition_fingerprint", ""):
            return override
        return source.get("po_definition", "unknown")
    return override


def validate_definition(value):
    if value not in PO_DEFINITIONS:
        raise AppError("PO率の定義が不正です。")
    return value


def positive(value):
    return type(value) in (float, int) and math.isfinite(value) and value > 0


def cost_candidate(name):
    text = unicodedata.normalize("NFKC", str(name or "")).strip()
    match = re.search(r"(?:^|\s)(\d{1,7}(?:,\d{3})*)(?:\s*円)?$", text)
    if not match:
        return None
    value = float(match[1].replace(",", ""))
    return value if positive(value) else None


def validate_pricing(value):
    if value is None:
        return None
    if (
        not isinstance(value, dict)
        or set(value) != {"amount", "plays", "confirmed"}
        or not positive(value["amount"])
        or type(value["plays"]) is not int
        or not 1 <= value["plays"] <= 10000
        or type(value["confirmed"]) is not bool
    ):
        raise AppError("料金条件は正の金額・回数と確認状態で指定してください。")
    return value


def validate_inputs(value):
    if value is None:
        return
    if not isinstance(value, dict) or set(value) - {
        "prize_cost",
        "pricing",
        "pricing_unavailable",
        "actual_plays",
        "acquired_count",
        "inserted_amount",
    }:
        raise AppError("正式な景品原価・料金情報が不正です。")
    if value.get("prize_cost") is not None and (not positive(value["prize_cost"])):
        raise AppError("正式な景品原価は正の金額で指定してください。")
    if "pricing_unavailable" in value and type(value["pricing_unavailable"]) is not bool:
        raise AppError("正式料金の確認状態が不正です。")
    for key in ("actual_plays", "acquired_count", "inserted_amount"):
        v = value.get(key)
        if v is not None and (
            type(v) not in (int, float)
            or not math.isfinite(v)
            or v < 0
            or key != "inserted_amount"
            and v != int(v)
        ):
            raise AppError("実測の投入金額・プレイ回数・景品獲得数が不正です。")
    validate_pricing(value.get("pricing"))


def evaluate_po(machine, settings=None):
    from .analysis_settings import validate_settings

    settings = settings or validate_settings()
    official = (machine.analysis or {}).get("po_inputs") or {}
    from .costing import cost_profile

    profile = cost_profile(machine)
    unit = profile["effective"]
    source = profile["source"]
    candidate = profile["candidate"]
    warning = list(profile["warnings"])
    changed = profile["changed"]
    pricing = official.get("pricing")
    price_source = "Excel正式料金"
    if pricing is None:
        pricing = getattr(machine, "play_pricing", None)
        price_source = "マシン設定料金"
    if pricing is None:
        pricing = dict(amount=settings["default_play_price"], plays=1, confirmed=False)
        price_source = "アプリ標準料金（仮設定）"
    valid_pricing = not official.get("pricing_unavailable", False)
    if not valid_pricing:
        price_source = "正式料金条件が不明（要確認）"
    try:
        validate_pricing(pricing)
    except AppError:
        valid_pricing = False
    play_price = pricing["amount"] / pricing["plays"] if valid_pricing else None
    real_plays, inserted = official.get("actual_plays"), official.get("inserted_amount")
    if real_plays is not None and inserted is not None:
        play_price = inserted / real_plays if real_plays > 0 else None
        valid_pricing = play_price is not None
        price_source = "実測投入金額÷実測プレイ回数"
        pricing = dict(amount=inserted, plays=real_plays, confirmed=True)
    provisional = valid_pricing and not pricing["confirmed"]
    if provisional:
        warning.append("料金は仮設定です。理論上の目安であり確定値ではありません。")
    if not valid_pricing:
        warning.append("料金条件が不明・不正のため計算不可。")
    target = settings.get("cost_target")
    expected = (
        play_price * target / unit
        if positive(unit) and positive(play_price) and positive(target) and target <= 1
        else None
    )
    raw_actual = raw_po(machine)
    basis = po_definition(machine)
    acquired = official.get("acquired_count")
    if acquired is not None and real_plays is not None:
        actual = acquired / real_plays if real_plays > 0 else None
        actual_source = "実測景品獲得数÷実測プレイ回数"
        basis = "prize_per_play"
        if (
            actual is not None
            and raw_actual is not None
            and not math.isclose(actual, raw_actual, abs_tol=0.00005)
        ):
            warning.append(
                "Excel P/O率と実測獲得数÷プレイ回数が一致しません。別項目として原値を保持します。"
            )
    else:
        actual = raw_actual if basis == "prize_per_play" else None
        actual_source = "確認済みのExcel P/O率" if machine.analysis else "レイアウトのPO率"
    if basis != "prize_per_play":
        warning.append(
            "Excel P/O率の定義は未確認または異なります。原値を残し、景品獲得数÷プレイ回数の評価には使用しません。"
        )
    score = None
    label, color = "計算不可", "#B8BEC6"
    if expected is not None:
        if expected > 1:
            warning.append(
                "理論PO率が100%を超えています。料金・原価・運営条件を確認してください。丸めていません。"
            )
        elif actual is not None and not changed:
            deviation = abs(actual - expected) / expected
            if settings["po_score_method"] == "tolerance_linear":
                t = settings["po_tolerance"]
                score = (
                    100.0
                    if deviation <= t
                    else max(0.0, 100 * (1 - (deviation - t) / max(1e-9, 1 - t)))
                )
            else:
                score = max(0.0, min(100.0, 100 * (1 - deviation)))
            from .analysis_bands import po_band

            color, label = po_band((actual - expected) / expected, settings)
    temporal = getattr(machine, "temporal_summary", {})
    if temporal.get("context_changed"):
        score, label, color = None, "期間内の運営条件変更・未評価", "#B8BEC6"
        warning.append("期間中の商品・設置日・運営方法が異なるため、単一原価の理論PO率による適合度は評価しません。")
    if expected is None:
        warning.append("原価・料金・目標原価率の有効値が不足しています。")
    if actual is None:
        warning.append("実際のPO率はデータなし。")
    return dict(
        actual=actual,
        target=expected,
        raw_actual=raw_actual,
        definition=basis,
        actual_source=actual_source,
        deviation_points=(
            (actual - expected) * 100 if actual is not None and expected is not None else None
        ),
        target_cost_ratio=target,
        cost_confidence=profile["confidence"],
        cost_details=profile,
        confidence=(
            "confirmed"
            if unit is not None
            and profile["confidence"] == "confirmed"
            and valid_pricing
            and not provisional
            else (
                "estimated"
                if unit is not None and play_price is not None
                else "candidate" if candidate is not None else "unavailable"
            )
        ),
        score=score,
        label=label,
        color=color,
        prize_cost=unit,
        cost_source=source,
        candidate=candidate,
        play_price=play_price,
        pricing=pricing,
        price_source=price_source,
        provisional=bool(provisional),
        warnings=warning,
        explanation="PO率＝景品獲得数÷プレイ回数。ExcelのP/O原値は別に保持します。適正PO率は景品原価・料金・目標原価率から求める目安です。複数獲得・サービスクレジットなど実際の条件を確認してください。",
    )
