"""One non-destructive cost choice and recalculation service for every screen."""

import math, re, unicodedata


def positive(value):
    return type(value) in (int, float) and math.isfinite(value) and value > 0


def trailing_candidate(name):
    text = unicodedata.normalize("NFKC", str(name or "")).strip()
    match = re.search(r"(?:^|\s)(\d{1,7}(?:,\d{3})*)(?:\s*円)?$", text)
    if not match:
        return None, False
    value = float(match[1].replace(",", ""))
    if not positive(value):
        return None, False
    prefix = text[: match.start()].rstrip()
    ambiguous = (
        bool(
            re.search(
                r"(?:vol\.?|ver\.?|no\.?|型番|サイズ|容量|数量|モデル|第|全)\s*$", prefix, re.I
            )
        )
        or value < 10
        and len(match[1]) < 3
        and not text.endswith("円")
    )
    return value, not ambiguous


def cost_profile(machine):
    from .floor_status import raw_stat

    a = machine.analysis or {}
    official = (a.get("po_inputs") or {}).get("prize_cost")
    if official is None:
        official = raw_stat(machine, "unit_price")
    if not positive(official):
        official = None
    candidate, safe = trailing_candidate(machine.product)
    manual = (
        machine.prize_cost
        if positive(machine.prize_cost) and machine.prize_cost_product == machine.product
        else None
    )
    warnings = []
    context = a.get("product_context")
    changed = context is not None and (
        context.get("product") != machine.product
        or context.get("installed_on") != machine.installed_on
    )
    if changed:
        official = None
        warnings.append(
            "商品・設置日が実績読込時から変わっています。旧商品の実績で再計算しません。"
        )
    if manual is not None:
        effective, source, confidence = manual, "手動確認済み", "confirmed"
    elif candidate is not None and safe:
        effective, source, confidence = candidate, "商品名末尾", "estimated"
    elif official is not None:
        effective, source, confidence = official, "正式単価", "confirmed"
    else:
        effective, source, confidence = None, "原価不明", "unavailable"
    if candidate is not None and not safe:
        warnings.append(
            "末尾の数字は型番・サイズ・数量の可能性があります。原価を確認してください。"
        )
    if source == "商品名末尾":
        warnings.append(
            "商品名末尾の原価候補を使用しています。型番・数量ではないことを確認してください。"
        )
    if (
        official is not None
        and candidate is not None
        and not math.isclose(official, candidate, abs_tol=0.01)
    ):
        warnings.append(
            f"末尾原価候補{candidate:,.0f}円と正式単価{official:,.0f}円が相違します。使用原価：{source}。"
        )
    sales = raw_stat(machine, "sales")
    count = (a.get("po_inputs") or {}).get("acquired_count")
    if count is None:
        count = raw_stat(machine, "dispensed")
    reason = ""
    if not positive(sales):
        reason = "売上金額が0または不明"
    elif (
        count is None
        or type(count) not in (int, float)
        or not math.isfinite(count)
        or count < 0
        or count != int(count)
    ):
        reason = "獲得個数が不明・不正"
    elif effective is None:
        reason = "使用原価が不明"
    elif changed:
        reason = "商品・設置日と実績期間の不一致"
    elif a and (
        not context
        or a.get("period_source") not in ("excel", "manual")
        or not a.get("period_start")
        or not a.get("period_end")
    ):
        reason = "実績期間・商品の対応が未確認"
    elif a and machine.installed_on and a["period_start"] < machine.installed_on:
        reason = "集計期間に設置前の商品が含まれる可能性"
    elif a.get("quality_issues"):
        reason = "実績の重大な異常を確認してください"
    total = None if reason else effective * count
    calculated = None if total is None else total / sales
    overridden = source in ("手動確認済み", "商品名末尾")
    return dict(
        effective=effective,
        source=source,
        confidence=confidence,
        candidate=candidate,
        candidate_safe=safe,
        official=official,
        confirmed=manual,
        changed=changed,
        overridden=overridden,
        excel_cost_rate=raw_stat(machine, "cost_rate"),
        excel_cost=raw_stat(machine, "cost"),
        calculated_cost_rate=calculated,
        calculated_cost=total,
        count=count,
        sales=sales,
        reason=reason,
        warnings=warnings,
    )
