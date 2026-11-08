"""Explainable theoretical payout-per-play guidance; ratios are fractions, never percent points."""

import math
import re
import unicodedata
from .core import AppError
from .floor_status import effective_stat


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
    if not isinstance(value, dict) or set(value) - {"prize_cost", "pricing", "pricing_unavailable"}:
        raise AppError("正式な景品原価・料金情報が不正です。")
    if value.get("prize_cost") is not None and (not positive(value["prize_cost"])):
        raise AppError("正式な景品原価は正の金額で指定してください。")
    if "pricing_unavailable" in value and type(value["pricing_unavailable"]) is not bool:
        raise AppError("正式料金の確認状態が不正です。")
    validate_pricing(value.get("pricing"))


def evaluate_po(machine, settings):
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
    if unit is None:
        manual = getattr(machine, "prize_cost", None)
        context = getattr(machine, "prize_cost_product", "")
        if manual is not None and context == machine.product:
            unit, source = manual, "ユーザー確認原価"
        elif candidate is not None:
            source = "商品名末尾の原価候補（未確認）"
            warning.append(
                f"原価候補 {candidate:,.0f}円は型番・数量の可能性があります。確認が必要です。"
            )
        else:
            source = "原価不明"
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
    actual = effective_stat(machine, "po_rate")
    score = None
    label, color = "計算不可", "#B8BEC6"
    if expected is not None:
        if expected > 1:
            warning.append(
                "理論PO率が100%を超えています。料金・原価・運営条件を確認してください。丸めていません。"
            )
        elif actual is not None and not changed:
            deviation = abs(actual - expected) / expected
            score = max(0.0, 100 * (1 - deviation))
            tolerance = settings["po_tolerance"]
            label, color = (
                ("適正範囲", "#94D8A0")
                if deviation <= tolerance + 1e-12
                else (("低い", "#8FC5F4") if actual < expected else ("高い", "#F4A6A6"))
            )
    if expected is None:
        warning.append("原価・料金・目標原価率の有効値が不足しています。")
    if actual is None:
        warning.append("実際のPO率はデータなし。")
    return dict(
        actual=actual,
        target=expected,
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
        explanation="PO率＝獲得回数÷プレイ回数。ExcelのP/O率は原値を保持します。払出数が個数か回数か・割引条件は元データだけでは確認できないため、理論値は目安です。",
    )
