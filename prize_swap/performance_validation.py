"""Explicit report metadata and explainable, non-destructive reconciliation."""

from datetime import datetime
import re
import unicodedata
from .core import parse_date, AppError


def canonical(value):
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", str(value or ""))).casefold()


def ratio_value(value):
    from .floor_status import numeric

    number = numeric(value)
    if (
        number is not None
        and isinstance(value, str)
        and "%" in unicodedata.normalize("NFKC", value)
    ):
        number /= 100
    return number


def issue(
    number,
    kind,
    message,
    severity="警告",
    row=0,
    remedy="元Excelとマシン番号・対象期間を確認してください。",
):
    return dict(
        number=number, kind=kind, message=message, severity=severity, row=row, remedy=remedy
    )


def metadata(rows):
    result = dict(period_start=None, period_end=None, period_source="unknown", extracted_at=None)
    ranges = []
    for row in rows:
        values = [str(c.value).strip() for c in row if c.value is not None]
        text = " ".join(values)
        period_match = re.search(
            r"(?:対象期間|集計期間)\s*[:：]?\s*(\d{4}[/-]\d{1,2}[/-]\d{1,2})\s*[~～〜]\s*(\d{4}[/-]\d{1,2}[/-]\d{1,2})",
            text,
        )
        if period_match:
            start, end = (parse_date(v) for v in period_match.groups())
            if start and end and start <= end:
                ranges.append((start.isoformat(), end.isoformat()))
        for label, key in (
            ("期間(自)", "period_start"),
            ("期間(至)", "period_end"),
            ("開始日", "period_start"),
            ("終了日", "period_end"),
        ):
            if canonical(label) in canonical(text):
                dates = re.findall(r"\d{4}[/年-]\d{1,2}[/月-]\d{1,2}日?", text)
                if dates:
                    day = parse_date(
                        dates[-1].replace("年", "/").replace("月", "/").replace("日", "")
                    )
                    if day:
                        result[key] = day.isoformat()
        match = re.search(
            r"(?:検索日時|抽出日時)\s*[:：]?\s*(\d{4}/\d{1,2}/\d{1,2})\s+(\d{1,2}:\d{2}:\d{2})",
            text,
        )
        if match:
            result["extracted_at"] = (
                datetime.strptime(" ".join(match.groups()), "%Y/%m/%d %H:%M:%S").isoformat()
                + "+09:00"
            )
    if ranges:
        unique = set(ranges)
        if len(unique) == 1 and all(
            result[k] is None or result[k] == ranges[0][i]
            for i, k in enumerate(("period_start", "period_end"))
        ):
            result["period_start"], result["period_end"] = ranges[0]
        else:
            result["period_start"] = result["period_end"] = None
    if result["period_start"] and result["period_end"]:
        if result["period_start"] <= result["period_end"]:
            result["period_source"] = "excel"
        else:
            result["period_start"] = result["period_end"] = None
    return result


def period_text(snapshot):
    start, end = snapshot.get("period_start"), snapshot.get("period_end")
    if not start or not end:
        return "対象期間不明"
    return f"集計期間：{start.replace('-', '/')} ～ {end.replace('-', '/')}" + (
        "（手動設定）" if snapshot.get("period_source") == "manual" else ""
    )


def validate_tolerance(settings=None):
    import math

    result = dict(absolute=2.0, relative=0.02) | (settings or {})
    if (
        set(result) != {"absolute", "relative"}
        or any(
            type(v) not in (int, float) or not math.isfinite(v) or v < 0 for v in result.values()
        )
        or result["relative"] > 1
    ):
        raise AppError("照合許容差は絶対額0以上・相対差0〜100%で指定してください。")
    return result


def record_issues(number, values, row, tolerance):
    issues = []
    for key, value in values.items():
        from .sales_analysis import METRICS

        if value is None:
            issues.append(
                issue(number, "missing", f"{METRICS[key]}が欠損しています。", "警告", row)
            )
        elif key != "gross_profit" and value < 0:
            issues.append(issue(number, "negative", f"{METRICS[key]}が負数です。", "エラー", row))
        elif key in ("po_rate", "cost_rate") and not 0 <= value <= 1:
            issues.append(
                issue(
                    number,
                    "range",
                    f"{METRICS[key]}が0〜100%の範囲外です（単位を自動補正しません）。",
                    "エラー",
                    row,
                )
            )
    sales, average, days = (values[k] for k in ("sales", "average", "operating_days"))
    checks = []
    if sales is not None and average is not None and days is not None:
        checks.append((sales, average * days, "売上金額と平均売上×稼働日数"))
        if days == 0 and sales != 0:
            issues.append(
                issue(
                    number,
                    "zero_division",
                    "稼働日数0で売上があり、日平均を検証できません。",
                    "エラー",
                    row,
                )
            )
    profit, cost = values["gross_profit"], values["cost"]
    if sales is not None and profit is not None and cost is not None:
        checks.append((sales, profit + cost, "売上金額と粗利益＋原価"))
    for a, b, label in checks:
        delta = abs(a - b)
        allowed = max(tolerance["absolute"], max(abs(a), abs(b)) * tolerance["relative"])
        if delta > allowed:
            issues.append(
                issue(
                    number,
                    "consistency",
                    f"{label}が不一致（差{delta:,.2f}円・許容{allowed:,.2f}円）。",
                    "エラー",
                    row,
                    "集計仕様を確認し、必要に応じ照合許容差を設定してください。",
                )
            )
    return issues


def reconcile(machines, snapshot, previous=None):
    dynamic = {
        "unmatched",
        "absent",
        "machine_name",
        "product_name",
        "matching",
        "layout_reference_missing",
    }
    issues = [i for i in snapshot.get("issues", []) if i["kind"] not in dynamic]
    records = snapshot["records"]
    from .machine_matching import resolve_snapshot

    matches = resolve_snapshot(machines, snapshot)
    known = {r.number: m for m in machines if (r := matches[m.uid]).number}
    for m in machines:
        r = matches[m.uid]
        if r.method == "review":
            issues.append(
                issue(
                    m.source_key or m.number,
                    "matching",
                    r.message,
                    "警告",
                    remedy="管理ナンバー照合画面で対応を確認・修正してください。",
                )
            )
        elif not r.number:
            issues.append(
                issue(
                    m.source_key or m.number or m.uid,
                    "layout_reference_missing",
                    "レイアウト側の管理番号に対応する機器番号が未登録です。",
                    remedy="管理番号参考表で対応を確認してください。番号や左右は推測しません。",
                )
            )
    for number, record in records.items():
        m = known.get(number)
        if m is None:
            issues.append(
                issue(
                    number, "unmatched", "レイアウトに機器番号が存在しません。", row=record["row"]
                )
            )
            continue
        name = record.get("machine_name", "")
        if name and m.kind not in ("", "クレーン") and canonical(name) != canonical(m.kind):
            issues.append(
                issue(number, "machine_name", f"機器名相違：レイアウト {m.kind} / 実績 {name}")
            )
        product = record["reference_name"]
        if product and product != m.product:
            issues.append(
                issue(
                    number,
                    "product_name",
                    f"商品名相違（参考）：{product}。既存商品名を維持します。",
                    "情報",
                )
            )
    if snapshot.get("file_name"):
        for number in sorted(set(known) - set(records)):
            issues.append(
                issue(
                    number,
                    "absent",
                    "最新Excelに有効実績がありません。レイアウト由来の有効値のみ使用します。",
                )
            )
    if previous and previous.get("file_name"):
        if snapshot.get("fingerprint") == previous.get("fingerprint"):
            issues.append(
                issue("", "reimport", "同一ファイルを再読込しました。履歴の二重登録は行いません。")
            )
        old, new = previous.get("extracted_at"), snapshot.get("extracted_at")
        if old and new and new < old:
            issues.append(issue("", "old_data", "前回より古い抽出日時のデータです。"))
        if (previous.get("period_start"), previous.get("period_end")) != (
            snapshot.get("period_start"),
            snapshot.get("period_end"),
        ):
            issues.append(
                issue("", "period", "前回と集計期間が異なります。単純加算・無条件比較しません。")
            )
        for number in set(records) & set(previous["records"]):
            if records[number]["values"] != previous["records"][number]["values"]:
                issues.append(issue(number, "changed", "前回実績と値が異なります。", "情報"))
    unique = {}
    for item in issues:
        unique[(item["number"], item["kind"], item["message"])] = item
    return list(unique.values())
