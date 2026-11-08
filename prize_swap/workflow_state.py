"""Durable audit and real file activity, independent of session-only undo stacks."""

from copy import deepcopy
from dataclasses import asdict
from datetime import datetime, timezone, timedelta
from uuid import uuid4
from PySide6.QtCore import QObject, Signal, QTimer
from PySide6.QtGui import QUndoStack
from .core import AppError


def now():
    return datetime.now(timezone(timedelta(hours=9))).isoformat(timespec="seconds")


def validate_workflow(value=None):
    s = dict(
        version=1,
        audit_log=[],
        recent={},
        import_profiles=[],
        candidate_evaluations=[],
        print_job=dict(include_instruction=True, include_map=True, include_checklist=True),
    )
    if value is not None and (not isinstance(value, dict) or set(value) - set(s)):
        raise AppError("作業履歴の保存形式が不正です。")
    s.update(deepcopy(value or {}))
    if (
        s["version"] != 1
        or not isinstance(s["recent"], dict)
        or set(s["recent"]) - {"excel", "saved", "output"}
    ):
        raise AppError("最近の作業の形式が不正です。")
    required = {
        "operation_id",
        "timestamp",
        "operation_type",
        "target_type",
        "target_id",
        "before_value",
        "after_value",
        "description",
    }
    if (
        not isinstance(s["audit_log"], list)
        or len(s["audit_log"]) > 5000
        or any(
            not isinstance(r, dict)
            or set(r) != required
            or any(not isinstance(r[k], str) for k in required - {"before_value", "after_value"})
            for r in s["audit_log"]
        )
    ):
        raise AppError("監査履歴が不正です。")
    for key, r in s["recent"].items():
        if (
            not isinstance(r, dict)
            or set(r) != {"file", "timestamp", "kind"}
            or any(not isinstance(v, str) for v in r.values())
        ):
            raise AppError("最近のファイル情報が不正です。")
    for k in ("import_profiles", "candidate_evaluations"):
        if (
            not isinstance(s[k], list)
            or len(s[k]) > 10000
            or any(not isinstance(v, dict) for v in s[k])
        ):
            raise AppError("作業設定・判定履歴が不正です。")
    if (
        not isinstance(s["print_job"], dict)
        or set(s["print_job"]) != {"include_instruction", "include_map", "include_checklist"}
        or any(type(v) is not bool for v in s["print_job"].values())
    ):
        raise AppError("作業セット設定が不正です。")
    from .excel_profiles import validate_profile

    for profile in s["import_profiles"]:
        validate_profile(profile)
    return s


class AuditUndoStack(QUndoStack):
    audit_kind = ""

    def push(self, command):
        self.audit_kind = "edit"
        try:
            super().push(command)
        finally:
            self.audit_kind = ""

    def undo(self):
        self.audit_kind = "undo"
        try:
            super().undo()
        finally:
            self.audit_kind = ""

    def redo(self):
        self.audit_kind = "redo"
        try:
            super().redo()
        finally:
            self.audit_kind = ""


class WorkflowStore(QObject):
    changed = Signal()

    def __init__(self, owner):
        super().__init__(owner)
        self.owner = owner
        self.data = validate_workflow()
        self._watchers = []
        self.watch(
            owner.instruction_history,
            "instruction",
            lambda: {p.entry_id: asdict(p) | {"day": p.day.isoformat()} for p in owner.products},
        )
        self.watch(owner.floor.history, "floor", self.floor_snapshot)
        self._last_import = self.import_marker()
        owner.floor.changed.connect(self.detect_import)
        self._reason_build = -1
        self.reason_timer = QTimer(self)
        self.reason_timer.setSingleShot(True)
        self.reason_timer.setInterval(200)
        self.reason_timer.timeout.connect(self.capture_assessments)
        owner.floor.changed.connect(lambda: self.reason_timer.start())

    def capture_assessments(self, force=False):
        if getattr(self.owner, "_loading_instructions", False):
            return
        floor = self.owner.floor
        records = floor.analytics.records()
        if not force and self._reason_build == floor.analytics.builds:
            return
        self._reason_build = floor.analytics.builds
        from .candidate_evidence import evidence

        byuid = {m.uid: m for m in floor.machines}
        latest = {r["machine_key"]: r for r in self.data["candidate_evaluations"]}
        added = []
        for record in records:
            row = evidence(
                byuid[record["uid"]], record, floor.reference_day(), floor.analysis_settings
            )
            if latest.get(row["machine_key"], {}).get("signature") != row["signature"]:
                added.append(row)
        if added:
            self.data["candidate_evaluations"] = (self.data["candidate_evaluations"] + added)[
                -10000:
            ]
            self.changed.emit()

    def floor_snapshot(self):
        result = {}
        for m in self.owner.floor.machines:
            value = asdict(m)
            if value.get("analysis"):
                value["analysis"] = {
                    k: v
                    for k, v in value["analysis"].items()
                    if k
                    in (
                        "values",
                        "fingerprint",
                        "loaded_at",
                        "period_start",
                        "period_end",
                        "file_name",
                    )
                }
            result["machine:" + m.uid] = value
        result.update(
            {
                "instruction:" + p.entry_id: asdict(p) | {"day": p.day.isoformat()}
                for p in self.owner.products
            }
        )
        result["candidates"] = deepcopy(self.owner.floor.candidate_ui)
        return result

    def state(self):
        return deepcopy(self.data)

    def reset_watchers(self):
        for watcher in self._watchers:
            watcher["snapshot"] = deepcopy(watcher["get"]())
            watcher["index"] = watcher["stack"].index()
            watcher["count"] = watcher["stack"].count()
        self._last_import = self.import_marker()

    def restore(self, value=None):
        self.data = validate_workflow(value)
        for watcher in self._watchers:
            watcher["snapshot"] = deepcopy(watcher["get"]())
            watcher["index"] = watcher["stack"].index()
            watcher["count"] = watcher["stack"].count()
        self._last_import = self.import_marker()
        self._reason_build = -1
        self.changed.emit()

    def append(self, kind, description, target_type="", target_id="", before=None, after=None):
        self.data["audit_log"].append(
            dict(
                operation_id=uuid4().hex,
                timestamp=now(),
                operation_type=kind,
                target_type=target_type,
                target_id=target_id,
                before_value=deepcopy(before),
                after_value=deepcopy(after),
                description=description,
            )
        )
        self.data["audit_log"] = self.data["audit_log"][-5000:]
        self.changed.emit()

    def record_file(self, key, path, kind=""):
        item = dict(file=str(path), timestamp=now(), kind=kind)
        self.data["recent"][key] = item
        self.append(
            key,
            {"excel": "Excel読込", "saved": "作業を保存", "output": "帳票を出力"}[key],
            kind,
            str(path),
            None,
            item,
        )

    def import_marker(self):
        f = self.owner.floor
        a = f.analysis_state
        return (a.get("source", ""), a.get("loaded_at", ""), f.source, f.loaded_at)

    def detect_import(self, *_):
        marker = self.import_marker()
        if marker != self._last_import:
            old = self._last_import
            self._last_import = marker
            if getattr(self.owner, "_loading_instructions", False):
                return
            if marker[1] and marker[:2] != old[:2]:
                self.record_file(
                    "excel",
                    marker[0] or self.owner.floor.analysis_state.get("file_name", ""),
                    "売上分析",
                )
            elif marker[3] and marker[2:] != old[2:]:
                self.record_file("excel", marker[2], "売り場レイアウト")

    def watch(self, stack, target, get):
        watcher = dict(
            stack=stack, get=get, snapshot=deepcopy(get()), index=stack.index(), count=stack.count()
        )
        self._watchers.append(watcher)

        def changed(index):
            current = deepcopy(get())
            before = watcher["snapshot"]
            oldindex = watcher["index"]
            oldcount = watcher["count"]
            watcher.update(snapshot=current, index=index, count=stack.count())
            if (
                stack.count() == 0
                or current == before
                or getattr(self.owner, "_loading_instructions", False)
            ):
                return
            kind = stack.audit_kind or (
                "undo" if index < oldindex else "redo" if stack.count() == oldcount else "edit"
            )
            command = stack.command(index if index < oldindex else max(0, index - 1))
            label = command.text() if command else "編集"
            if target == "floor" and "候補を指示書へ追加" in label:
                return
            keys = {k for k in current.keys() | before.keys() if current.get(k) != before.get(k)}
            delta_before = {k: before.get(k) for k in keys}
            delta_after = {k: current.get(k) for k in keys}
            self.append(
                kind,
                ("取り消し：" if kind == "undo" else "やり直し：" if kind == "redo" else "")
                + label,
                target,
                ",".join(sorted(keys)),
                delta_before,
                delta_after,
            )

        stack.indexChanged.connect(changed)
