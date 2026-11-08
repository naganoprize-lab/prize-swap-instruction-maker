"""Explicit candidate-to-instruction drafts; current and replacement products stay separate."""

from copy import deepcopy
from datetime import datetime, timezone
from dataclasses import asdict
import math
from uuid import uuid4
from PySide6.QtWidgets import QMessageBox
from .core import Product, AppError
from .machine_matching import machine_key, normalize_number, normalize_reference

FIELDS = {
    "assignment_id",
    "machine_key",
    "machine_number",
    "management_number",
    "machine_display_number",
    "current_product_name",
    "replacement_product_name",
    "operating_method",
    "average_ratio",
    "sales_amount",
    "installed_days",
    "source_candidate_status",
    "source_candidate_reason",
    "created_at",
    "confirmed_replacement_date",
}


def validate_assignment(value):
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) != FIELDS:
        raise AppError("候補からの引継ぎ情報が不正です。")
    for key, item in value.items():
        if key in ("average_ratio", "sales_amount", "installed_days"):
            if item is not None and (type(item) not in (int, float) or not math.isfinite(item)):
                raise AppError("候補の実績値が不正です。")
        elif key == "confirmed_replacement_date":
            if item is not None:
                from .core import parse_date

                if parse_date(item) is None:
                    raise AppError("確定した入替日が不正です。")
        elif not isinstance(item, str):
            raise AppError("候補の引継ぎ情報は文字列で保存してください。")
    if not value["machine_key"] or not value["assignment_id"]:
        raise AppError("候補の内部IDが必要です。")
    return deepcopy(value)


def display_number(machine):
    return machine_key(machine) or normalize_reference(machine.source_key) or ""


def registered_ids(floor):
    """Share the map's safe UID / number / reference / management reconciliation."""
    from .instructions import booths

    products = [p for p in floor.products() if p.day == floor.selected_day()]
    return {
        b.machine.uid
        for b in booths(products, floor.machines, floor.number_references, all_entries=True)
        if b.machine is not None
    }


def transfer(floor, machines, parent=None, category="選択候補", source_method=None):
    byid = {m.uid: m for m in machines}
    machines = list(byid.values())
    already = registered_ids(floor)
    fresh = [m for m in machines if m.uid not in already]
    result = dict(
        target=len(machines),
        added=0,
        skipped=len(machines) - len(fresh),
        missing_number=sum(not machine_key(m) for m in fresh),
        errors=0,
        cancelled=False,
    )
    if not machines:
        return result
    text = f'対象：{category}\n追加対象：{len(machines)}台\n追加済み：{result["skipped"]}台\n新規追加：{len(fresh)}台\n\n商品名・数量・入替後の運営方法は空欄で登録します。出力チェックはONにし、空欄のメモとして印刷できます。追加しますか？'
    if (
        QMessageBox.question(parent or floor, "入替指示書へ追加の確認", text)
        != QMessageBox.StandardButton.Yes
    ):
        result["cancelled"] = True
        return result
    records = {r["uid"]: r for r in floor.analytics.records()}
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    products = floor.products()
    added = []
    for m in fresh:
        r = records[m.uid]
        assessment = r["assessment_detail"]
        number = display_number(m)
        assignment = dict(
            assignment_id=uuid4().hex,
            machine_key=m.uid,
            machine_number=machine_key(m) or "",
            management_number=m.source_key or "",
            machine_display_number=number,
            current_product_name=m.product,
            replacement_product_name="",
            operating_method=m.operation_method,
            average_ratio=r["ratio"],
            sales_amount=r["sales"],
            installed_days=r["days"],
            source_candidate_status=assessment["status"],
            source_candidate_reason=" / ".join(assessment["reasons"]),
            created_at=stamp,
            confirmed_replacement_date=None,
        )
        validate_assignment(assignment)
        added.append(
            Product(
                floor.selected_day(),
                "",
                machines=[number] if number else [],
                included=True,
                source_type="candidate",
                source_method=source_method or ("一括：" + category),
                order=max([p.order for p in products] + [0]) + len(added) + 1,
                candidate_assignment=assignment,
            )
        )
    if added:
        from PySide6.QtGui import QUndoCommand

        host = parent.window() if parent else floor.window()
        snapshot = None
        if hasattr(host, "instruction_history"):
            from .instruction_editor import all_state

            snapshot = all_state(host)

        window = parent.window() if parent else floor.window()

        # Standalone floor widgets may be used by importers/tests; use the mutable shared list.
        class TransferCommand(QUndoCommand):
            def __init__(self):
                super().__init__("入替候補を指示書へ追加")

            def refresh(self):
                floor.product_edited.emit()
                floor.sync()
                floor.schedule_save()
                if hasattr(window, "refresh"):
                    window.refresh()

            def redo(self):
                for p in added:
                    if not any(existing.entry_id == p.entry_id for existing in floor.products()):
                        floor.products().append(p)
                self.refresh()

            def undo(self):
                for p in added:
                    for i, existing in enumerate(floor.products()):
                        if existing.entry_id == p.entry_id:
                            floor.products().pop(i)
                            break
                self.refresh()

        floor.history.push(TransferCommand())
        if snapshot is not None:
            from .instruction_editor import InstructionCommand, all_state

            host.instruction_history.push(
                InstructionCommand(host, snapshot, all_state(host), "入替候補から指示へ追加", True)
            )
            host.mark_dirty()
        result["added"] = len(added)
    return result
