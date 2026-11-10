"""Read-only explanations of both sides of machine reconciliation.

This projection never changes machine UIDs, references or sales records.
"""

from .machine_matching import resolve_snapshot, normalize_number, normalize_reference, clean

KIND_LABELS = {
    "matching": "レイアウト照合要確認",
    "unmatched": "正規化済み番号が未登録",
    "absent": "売上実績なし",
    "duplicate": "機器番号の重複",
    "number_format": "売上側の機器番号が不正",
    "numeric": "数値形式不正",
    "range": "数値の範囲外",
    "missing": "数値欠損",
    "negative": "負数",
    "consistency": "数値の不整合",
    "zero_division": "ゼロ除算",
    "machine_name": "機種名の相違",
    "product_name": "商品名の参考情報",
    "reimport": "同一ファイル再読込",
    "old_data": "過去データ",
    "period": "集計期間の変更",
    "changed": "実績値の変更",
    "po_input": "景品原価・料金条件の確認",
    "layout_reference_missing": "レイアウト側の管理番号が不明",
}


def problem_category(message):
    if "左右が不明" in message:
        return "機器番号が不正・左右区分が不明"
    if "同じ管理ナンバー" in message or "複数の管理ナンバー" in message:
        return "管理番号の対応が複数あり自動照合不可"
    if "複数のマシン枠" in message:
        return "同じマシン番号が重複"
    if "複数候補" in message:
        return "複数候補があり自動照合不可"
    if "異なります" in message:
        return "機械番号と参考表の対応不一致"
    return "レイアウト側の管理番号が不明"


def reconciliation_rows(machines, snapshot, references=()):
    matches = resolve_snapshot(machines, snapshot)
    targets = {}
    for m in machines:
        r = matches[m.uid]
        if r.matched:
            targets[r.number] = m.source_key or m.number or m.uid
    rows = []
    reference_values = {}
    for reference in references:
        reference_values.setdefault(normalize_reference(reference["source_key"]), set()).add(
            reference["number"]
        )
    for number, record in snapshot["records"].items():
        target = targets.get(number, "")
        rows.append(
            (
                record["raw_number"],
                number,
                target or "—",
                "照合済み" if target else "未照合",
                (
                    "機器番号で一致"
                    if target
                    else "正規化後のマシン番号に対応するレイアウト枠がありません。"
                ),
                (
                    "対応不要"
                    if target
                    else "管理番号参考表・レイアウトの登録番号を確認してください。"
                ),
            )
        )
    for m in machines:
        r = matches[m.uid]
        if r.matched:
            continue
        raw = (
            m.direct_number_raw
            or " / ".join(sorted(reference_values.get(normalize_reference(m.source_key), ())))
            or m.number
            or m.reference_number
        )
        rows.append(
            (
                raw or "—",
                r.number or normalize_number(raw) or "—",
                m.source_key or m.uid,
                "未照合",
                problem_category(r.message) + "：" + r.message,
                "照合画面で参考表・左右区分を確認してください。推測では割り当てません。",
            )
        )
    for row in snapshot.get("unmatched_rows", []):
        raw = row["number"]
        reason = "左右区分が不明" if clean(raw).isdigit() else "機器番号が不正"
        rows.append(
            (
                raw or "（空欄）",
                "—",
                "—",
                "読込不可",
                reason,
                f"Excel {row['row']}行の機器番号を確認してください。",
            )
        )
    return rows
