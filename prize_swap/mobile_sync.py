"""Content-aware document revisions and explicit three-way choices, independent of UI."""

from copy import deepcopy
from dataclasses import asdict
from datetime import date
from pathlib import Path
from uuid import uuid5, NAMESPACE_URL
import json
from .core import AppError, atomic_json
from .mobile_package import now, uid, validate_document, valid_id, encoded


def body_of(products, title, day):
    entries = []
    from .instructions import prepare_entry

    for p in products:
        prepare_entry(p)
        entries.append(asdict(p) | {"day": p.day.isoformat()})
    return dict(title=title, date=day, entries=entries, entry_extras={})


def revise(doc, body, device, deleted=False):
    if doc["body"] == body and doc["deleted"] == deleted:
        return deepcopy(doc)
    return dict(
        document_id=doc["document_id"],
        revision=doc["revision"] + 1,
        revision_id=uid(),
        ancestors=[*doc["ancestors"], doc["revision_id"]][-128:],
        updated_at=now(),
        updated_by=device,
        deleted=deleted,
        body=deepcopy(body),
    )


def new_document(body, device, document_id=None):
    return dict(
        document_id=document_id or uid(),
        revision=1,
        revision_id=uid(),
        ancestors=[],
        updated_at=now(),
        updated_by=device,
        deleted=False,
        body=deepcopy(body),
    )


def relation(local, incoming):
    if local is None:
        return "new"
    if local["revision_id"] == incoming["revision_id"]:
        if local["body"] != incoming["body"] or local["deleted"] != incoming["deleted"]:
            raise AppError("同じ改訂IDに異なる指示書が存在します。")
        return "same"
    if local["revision_id"] in incoming["ancestors"]:
        return "incoming"
    if incoming["revision_id"] in local["ancestors"]:
        return "local"
    return "conflict"


_MISSING = object()


def three_way(base, local, incoming, choices=None):
    """Returns an unresolved path list. Deletion vs editing is a real conflict."""
    conflicts = []
    choices = choices or {}

    def merge(b, l, r, path):
        if l == r:
            return deepcopy(l) if l is not _MISSING else _MISSING
        if l == b:
            return deepcopy(r) if r is not _MISSING else _MISSING
        if r == b:
            return deepcopy(l) if l is not _MISSING else _MISSING
        if isinstance(b, dict) and isinstance(l, dict) and isinstance(r, dict):
            out = {}
            for k in sorted(set(b) | set(l) | set(r)):
                v = merge(
                    b.get(k, _MISSING),
                    l.get(k, _MISSING),
                    r.get(k, _MISSING),
                    path + "/" + k,
                )
                if v is not _MISSING:
                    out[k] = v
            return out
        # Entries have identities, so edits to different rows are independent.
        if path.endswith("/entries") and all(isinstance(x, list) for x in (b, l, r)):
            maps = [{e["entry_id"]: e for e in xs} for xs in (b, l, r)]
            out = []
            for key in dict.fromkeys([e["entry_id"] for e in l + r + b]):
                v = merge(*(m.get(key, _MISSING) for m in maps), path + "/" + key)
                if v is not _MISSING:
                    out.append(v)
            return out
        if choices.get(path) == "local":
            return deepcopy(l) if l is not _MISSING else _MISSING
        if choices.get(path) == "incoming":
            return deepcopy(r) if r is not _MISSING else _MISSING
        conflicts.append(
            dict(
                path=path,
                local=None if l is _MISSING else l,
                incoming=None if r is _MISSING else r,
            )
        )
        return deepcopy(l) if l is not _MISSING else _MISSING

    return merge(base, local, incoming, ""), conflicts


def copy_document(doc, device):
    out = deepcopy(doc)
    out.update(
        document_id=uid(),
        revision_id=uid(),
        revision=1,
        ancestors=[],
        updated_at=now(),
        updated_by=device,
    )
    extras = {}
    for e in out["body"]["entries"]:
        old = e["entry_id"]
        e["entry_id"] = uid()
        if old in out["body"].get("entry_extras", {}):
            extras[e["entry_id"]] = out["body"]["entry_extras"][old]
        mapping = {}
        for a in e["booth_assignments"]:
            old_id = a["assignment_id"]
            a["assignment_id"] = uid()
            mapping[old_id] = a["assignment_id"]
        if e["candidate_assignment"]:
            candidate = e["candidate_assignment"]
            candidate["assignment_id"] = mapping.get(
                candidate.get("assignment_id"), uid()
            )
    out["body"]["entry_extras"] = extras
    return out


class SyncStore:
    def __init__(self, path):
        self.path = Path(path)
        self.state = (
            json.loads(self.path.read_text(encoding="utf-8"))
            if self.path.exists()
            else dict(
                version=1,
                dataset_id=uid(),
                device_id=uid(),
                documents={},
                history={},
                packages=[],
                transfers=[],
                adb_path="",
                selected_device="",
            )
        )
        if (
            not isinstance(self.state, dict)
            or type(self.state.get("version")) is not int
            or self.state.get("version") != 1
            or not all(valid_id(self.state.get(k)) for k in ("dataset_id", "device_id"))
        ):
            raise AppError("Android連携設定の形式が不正です。")
        for key, kind in [
            ("documents", dict),
            ("history", dict),
            ("packages", list),
            ("transfers", list),
            ("adb_path", str),
            ("selected_device", str),
        ]:
            if not isinstance(self.state.get(key), kind):
                raise AppError("Android連携設定の構造が不正です：" + key)
        if any(not isinstance(x, dict) for x in self.state["transfers"]):
            raise AppError("転送履歴の形式が不正です。")
        for key, doc in self.state["documents"].items():
            validate_document(doc)
            if key != doc["document_id"]:
                raise AppError("指示書IDが一致しません。")
        if any(not isinstance(x, dict) for x in self.state["history"].values()):
            raise AppError("改訂履歴の形式が不正です。")

    def save(self, value=None):
        target = deepcopy(value) if value is not None else self.state
        revisions = [
            (docid, rid, doc.get("updated_at", ""), len(encoded(doc)))
            for docid, history in target["history"].items()
            for rid, doc in history.items()
        ]
        revisions.sort(key=lambda r: r[2])
        size = sum(r[3] for r in revisions)
        while revisions and (len(revisions) > 512 or size > 32 * 1024 * 1024):
            docid, rid, _, length = revisions.pop(0)
            target["history"][docid].pop(rid, None)
            size -= length
        self.path.parent.mkdir(parents=True, exist_ok=True)
        atomic_json(self.path, target)
        self.state = target

    def remember(self, doc):
        validate_document(doc)
        self.state["documents"][doc["document_id"]] = deepcopy(doc)
        history = self.state["history"].setdefault(doc["document_id"], {})
        history[doc["revision_id"]] = deepcopy(doc)
        while len(history) > 128:
            history.pop(next(iter(history)))

    def observe(self, products):
        docs = self.state["documents"]
        owned = {
            e["entry_id"]: d["document_id"]
            for d in docs.values()
            for e in d["body"]["entries"]
        }
        groups = {key: [] for key in docs}
        for p in products:
            key = (
                owned.get(p.entry_id)
                or uuid5(
                    NAMESPACE_URL,
                    self.state["dataset_id"] + ":day:" + p.day.isoformat(),
                ).hex
            )
            groups.setdefault(key, []).append(p)
        for key, ps in groups.items():
            previous = docs.get(key)
            day = ps[0].day.isoformat() if ps else previous["body"]["date"]
            title = previous["body"]["title"] if previous else day + " 入替指示書"
            body = body_of(ps, title, day)
            if previous:
                body["entry_extras"] = deepcopy(
                    previous["body"].get("entry_extras", {})
                )
            doc = (
                revise(previous, body, self.state["device_id"], not ps)
                if previous
                else new_document(body, self.state["device_id"], key)
            )
            self.remember(doc)
        self.save()
        return deepcopy(list(docs.values()))

    def common_base(self, local, incoming):
        if local["revision_id"] in incoming["ancestors"]:
            return local
        history = self.state["history"].get(local["document_id"], {})
        for rev in reversed([*local["ancestors"], local["revision_id"]]):
            if rev in incoming["ancestors"] and rev in history:
                try:
                    return validate_document(history[rev])
                except (AppError, TypeError, KeyError):
                    return None
        return None

    def plan(self, incoming):
        result = []
        for doc in incoming:
            old = self.state["documents"].get(doc["document_id"])
            kind = relation(old, doc)
            base = self.common_base(old, doc) if old else None
            diff = []
            if kind == "conflict" and base:
                _, diff = three_way(base["body"], old["body"], doc["body"])
            result.append(
                dict(
                    document_id=doc["document_id"],
                    title=doc["body"]["title"],
                    relation=kind,
                    can_merge=base is not None,
                    conflicts=diff,
                )
            )
        return result

    def resolve(self, incoming, decisions):
        out = []
        for doc in incoming:
            old = self.state["documents"].get(doc["document_id"])
            kind = relation(old, doc)
            choice = decisions.get(doc["document_id"], {})
            action = choice.get(
                "action",
                (
                    "incoming"
                    if kind in ("new", "incoming")
                    else "local" if kind in ("same", "local") else ""
                ),
            )
            if not action:
                raise AppError("競合する指示書の扱いを選択してください。")
            if action == "local":
                continue
            if action == "copy":
                out.append(copy_document(doc, self.state["device_id"]))
                continue
            if action == "merge":
                base = self.common_base(old, doc) if old else None
                if base is None:
                    raise AppError("共通基点がないため差分統合できません。")
                if old["deleted"] != doc["deleted"]:
                    raise AppError(
                        "削除と編集が競合しています。保持・採用・別名保存を選択してください。"
                    )
                body, conflicts = three_way(
                    base["body"], old["body"], doc["body"], choice.get("fields")
                )
                if conflicts:
                    raise AppError("項目ごとの競合を解決してください。")
                merged = revise(
                    old,
                    body,
                    self.state["device_id"],
                    (
                        doc["deleted"]
                        if old["deleted"] == base["deleted"]
                        else old["deleted"]
                    ),
                )
                if merged["revision_id"] == old["revision_id"]:
                    merged.update(
                        revision_id=uid(),
                        revision=old["revision"] + 1,
                        updated_at=now(),
                        updated_by=self.state["device_id"],
                    )
                    merged["ancestors"] = [*old["ancestors"], old["revision_id"]][-128:]
                merged["ancestors"] = list(
                    dict.fromkeys(
                        [*merged["ancestors"], *doc["ancestors"], doc["revision_id"]]
                    )
                )[-128:]
                out.append(merged)
            elif action == "incoming":
                out.append(deepcopy(doc))
            else:
                raise AppError("競合処理の指定が不正です。")
        for doc in out:
            validate_document(doc)
        return out
