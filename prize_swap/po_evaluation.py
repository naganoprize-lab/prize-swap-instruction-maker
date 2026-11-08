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
    unit = official.get("prize_cost")
    source = "景品運営分析表：景品原価"
    if unit is None:
        unit = effective_stat(machine, "unit_price")
        source = "景品運営分析表：景品単価" if machine.analysis else "レイアウト：景品単価"
    candidate = cost_candidate(machine.product)
    warning = []
    context = (machine.analysis or {}).get("product_context")
    changed = context is not None and (
        context.get("product") != machine.product
        or context.get("installed_on") != machine.installed_on
    )
    if changed:
        unit = None
        warning.append(
            "商品・設置日が実績読込時から変わっています。旧商品の原価やPO評価は使いません。"
        )
    if (
        unit is not None
        and candidate is not None
        and not math.isclose(unit, candidate, abs_tol=0.01)
    ):
        warning.append(
            f"正式単価{unit:,.2f}円と末尾原価候補{candidate:,.2f}円が相違します。正式な値を優先しています。"
        )
    if unit is None:
        manual = getattr(machine, "prize_cost", None)
        context = getattr(machine, "prize_cost_product", "")
        if manual is not None and context == machine.product:
            unit, source = manual, (
                "商品名末尾からユーザーが確認した原価"
                if getattr(machine, "prize_cost_origin", "manual") == "product_suffix"
                else "ユーザー確認原価"
            )
        elif candidate is not None:
            source = "商品名末尾の原価候補（未確認）"
            warning.append(
                f"原価候補 {candidate:,.0f}円は型番・数量の可能性があります。確認が必要です。"
            )
        else:
            source = "原価不明"
    manual = getattr(machine, "prize_cost", None)
    if positive(manual) and getattr(machine, "prize_cost_product", "") == machine.product:
        if unit is not None and not math.isclose(unit, manual, abs_tol=0.01):
            warning.append("手動確認原価とExcel景品単価が相違します。手動修正を優先しています。")
        unit = manual
        source = (
            "商品名末尾からユーザーが確認した原価"
            if machine.prize_cost_origin == "product_suffix"
            else "ユーザー確認原価（手動修正）"
        )
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
        cost_confidence=(
            "confirmed"
            if unit is not None
            else "candidate" if candidate is not None else "unavailable"
        ),
        confidence=(
            "confirmed"
            if unit is not None and valid_pricing and not provisional
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
