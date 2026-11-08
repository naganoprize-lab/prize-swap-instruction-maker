"""Non-destructive, booth-level checks shared by home and every instruction output."""

from dataclasses import dataclass, field
from uuid import uuid5, NAMESPACE_URL
from collections import defaultdict
from .core import quantity, AppError
from .instructions import booths
from .methods import allocation_status, active_assignments
from .machine_matching import normalize_number, normalize_reference, machine_key, reference_index


@dataclass
class ValidationIssue:
    issue_id: str
    severity: str
    issue_type: str
    message: str
    affected_entry_ids: list = field(default_factory=list)
    affected_assignment_ids: list = field(default_factory=list)
    machine_key: str = ""
    suggested_action: str = ""
    can_override: bool = True


def validate_instructions(products, machines=(), references=(), day=None):
    products = [p for p in products if day is None or p.day == day]
    active = [p for p in products if p.included]
    issues = []

    def add(
        level, kind, message, entries=(), assignments=(), key="", action="内容を確認してください。"
    ):
        entries = list(dict.fromkeys(entries))
        assignments = list(dict.fromkeys(assignments))
        identity = "|".join([kind, *entries, *assignments, key])
        issues.append(
            ValidationIssue(
                uuid5(NAMESPACE_URL, identity).hex,
                level,
                kind,
                message,
                entries,
                assignments,
                key,
                action,
                level != "error",
            )
        )

    for p in products:
        if type(p.included) is not bool:
            add(
                "error",
                "invalid_output",
                "出力チェックの値が不正です。",
                [p.entry_id],
                action="出力対象を選び直してください。",
            )
    if products and not active:
        add(
            "warning",
            "no_output",
            "現在の日付に出力対象の指示がありません。",
            [p.entry_id for p in products],
            action="出力チェックをONにしてください。",
        )
    refs = booths(active, machines, references)
    byentry = defaultdict(list)
    mapping, problems = reference_index(list(references))
    duplicates = defaultdict(list)
    for b in refs:
        byentry[b.product.entry_id].append(b)
        token_number = normalize_number(b.token, allow_bare=True) or normalize_number(
            b.link["machine_number"], allow_bare=True
        )
        management = normalize_reference(b.token) or normalize_reference(
            b.link["management_number"]
        )
        key = b.machine.uid if b.machine else token_number or management or ""
        if key:
            duplicates[key].append(b)
        if management in problems:
            add(
                "error",
                "ambiguous_reference",
                f"{management} の管理ナンバー参考表に矛盾があります。",
                [b.product.entry_id],
                [b.key],
                management,
                "参考表の重複・左右・対応を修正してください。",
            )
        elif b.status == "照合要確認":
            add(
                "error",
                "ambiguous_machine",
                f"{b.token or management} が複数マシンに一致します。",
                [b.product.entry_id],
                [b.key],
                key,
                "入替予定地図の位置未特定一覧から対象を選んでください。",
            )
        if b.machine:
            actual = machine_key(b.machine) or normalize_number(b.machine.number, allow_bare=True)
            if (
                token_number
                and actual
                and token_number != actual
                or management in mapping
                and actual
                and mapping[management] != actual
            ):
                add(
                    "error",
                    "reference_mismatch",
                    "機械番号・管理ナンバー・内部IDの対応が一致していません。",
                    [b.product.entry_id],
                    [b.key],
                    key,
                    "マシン番号と地図の対応先を確認・修正してください。",
                )
        else:
            if management and management not in problems:
                add(
                    "warning",
                    "management_unmatched",
                    f"管理ナンバー {management} の位置を照合できません。",
                    [b.product.entry_id],
                    [b.key],
                    management,
                    "参考表または入替予定地図の一覧で照合してください。",
                )
            add(
                "warning",
                "position_unknown",
                f'{b.token or "マシン番号未入力"}：地図上の位置が未特定です。',
                [b.product.entry_id],
                [b.key],
                key,
                "地図の一覧から実在マシンを選択できます。空欄メモとして印刷を続けることもできます。",
            )
    for key, bs in duplicates.items():
        if len(bs) < 2:
            continue
        names = {b.product.name.strip() for b in bs if b.product.name.strip()}
        methods = {
            active_assignments(b.product).get(b.token)
            for b in bs
            if active_assignments(b.product).get(b.token)
        }
        error = len(names) > 1 or len(methods) > 1
        add(
            "error" if error else "warning",
            "contradictory_instruction" if error else "duplicate_machine",
            f"同じマシンに{len(bs)}件の"
            + (
                "矛盾する入替指示があります。"
                if error
                else "指示があります。意図的か確認してください。"
            ),
            [b.product.entry_id for b in bs],
            [b.key for b in bs],
            key,
            "対象行の商品名・運営方法・重複を確認してください。",
        )
    for p in active:
        entry = [p.entry_id]
        assignments = [b.key for b in byentry[p.entry_id]]
        for key, title in [("units", "入数"), ("cases", "CS数")]:
            try:
                quantity(getattr(p, key))
            except AppError:
                add(
                    "error",
                    "invalid_" + key,
                    f"{title}は0以上の数値か空欄で入力してください。",
                    entry,
                    assignments,
                    action=f"{title}を修正してください。",
                )
        missing = [
            title
            for key, title in [("units", "入数"), ("cases", "CS数")]
            if getattr(p, key) in ("", None)
        ]
        if missing:
            add(
                "warning",
                "quantity_pending",
                "・".join(missing) + "が未設定です。",
                entry,
                assignments,
                action="未定のまま印刷する場合は内容を確認して続行してください。",
            )
        status = allocation_status(p)
        if status.unassigned:
            add(
                "warning",
                "unassigned_booths",
                f"予定ブースの未割当が{status.unassigned}ブースあります。",
                entry,
                assignments,
                action="予定数とマシン別運営方法を確認してください。未割当でもメモとして印刷できます。",
            )
        if status.excess:
            add(
                "warning",
                "excess_booths",
                f"予定数に対して割当が{status.excess}ブース超過しています。",
                entry,
                assignments,
                action="予定数または割当を確認してください。",
            )
        if not p.name:
            add(
                "info",
                "product_pending",
                "商品名未設定。印刷では空欄にします。",
                entry,
                assignments,
            )
        if status.unmapped or not p.methods:
            add(
                "info",
                "method_pending",
                "運営方法未設定。印刷では空欄にします。",
                entry,
                assignments,
            )
        if p.source_type == "manual":
            add(
                "info",
                "manual_product",
                "手動追加した指示です。入荷予定表との紐付けは不要です。",
                entry,
                assignments,
            )
    return issues
