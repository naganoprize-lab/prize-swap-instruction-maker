"""Instruction metadata on the existing Product model; no second instruction store."""

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import uuid4
from .core import AppError, ordered, machine_numbers
from .machine_matching import machine_key, normalize_number, normalize_reference, reference_index

SOURCE_LABELS = {"auto": "自動作成", "candidate": "入替候補から追加", "manual": "手動追加"}
IMPORT_FIELDS = ("day", "name", "units", "cases", "maker")
EDITABLE_FIELDS = {
    "day",
    "name",
    "units",
    "cases",
    "maker",
    "machines",
    "management_numbers",
    "methods",
    "planned_booths",
    "machine_methods",
    "retired_machine_methods",
    "priority",
    "limit",
    "included",
    "order",
    "notes",
    "booth_assignments",
}


def stamp():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def prepare_entry(p):
    if not p.entry_id:
        p.entry_id = uuid4().hex
    if not p.created_at:
        p.created_at = stamp()
    if not p.updated_at:
        p.updated_at = p.created_at
    if p.candidate_assignment and p.source_type == "auto":
        p.source_type = "candidate"
    if p.source_type not in SOURCE_LABELS:
        raise AppError("指示の追加元が不正です。")
    for key in ("entry_id", "source_method", "notes", "created_at", "updated_at"):
        if not isinstance(getattr(p, key), str):
            raise AppError("指示の保存項目が不正です。")
    if not isinstance(p.manually_edited_fields, list) or any(
        k not in EDITABLE_FIELDS for k in p.manually_edited_fields
    ):
        raise AppError("手動編集項目が不正です。")
    if not isinstance(p.import_snapshot, dict):
        raise AppError("Excelの元データが不正です。")
    if not isinstance(p.management_numbers, list) or any(
        not normalize_reference(v) for v in p.management_numbers
    ):
        raise AppError("管理ナンバーが不正です。")
    if not isinstance(p.booth_assignments, list):
        raise AppError("ブース割当の形式が不正です。")
    required = {"assignment_id", "token", "machine_key", "machine_number", "management_number"}
    for link in p.booth_assignments:
        if (
            not isinstance(link, dict)
            or set(link) != required
            or any(not isinstance(v, str) for v in link.values())
            or not link["assignment_id"]
        ):
            raise AppError("ブース割当IDの形式が不正です。")
    sync_booths(p)
    return p


def mark_edited(p, fields):
    prepare_entry(p)
    p.manually_edited_fields = list(dict.fromkeys([*p.manually_edited_fields, *fields]))
    p.updated_at = stamp()
    if p.candidate_assignment:
        p.candidate_assignment["replacement_product_name"] = p.name or ""
    sync_booths(p)


def sync_booths(p):
    tokens = (
        machine_numbers("\n".join(p.machines))
        or [normalize_reference(v) for v in p.management_numbers]
        or [""]
    )
    old = {l["token"]: l for l in p.booth_assignments}
    used = set()
    links = []
    for index, token in enumerate(tokens):
        link = old.get(token)
        if link is None:
            # Keep a booth's ID through a direct single-row edit, but discard its old match.
            previous = p.booth_assignments[index] if index < len(p.booth_assignments) else None
            link = dict(
                assignment_id=(
                    previous["assignment_id"]
                    if previous and previous["token"] not in tokens
                    else uuid4().hex
                ),
                token=token,
                machine_key="",
                machine_number=normalize_number(token, allow_bare=True) or "",
                management_number=normalize_reference(token) or "",
            )
            a = p.candidate_assignment
            if a and not p.booth_assignments and index == 0:
                link.update(
                    assignment_id=a["assignment_id"],
                    machine_key=a["machine_key"],
                    machine_number=a["machine_number"],
                    management_number=a["management_number"],
                )
        if index < len(p.management_numbers):
            link["management_number"] = normalize_reference(p.management_numbers[index]) or ""
        if link["assignment_id"] in used:
            link["assignment_id"] = uuid4().hex
        used.add(link["assignment_id"])
        links.append(link)
    p.booth_assignments = links


@dataclass
class Booth:
    product: object
    link: dict
    number: int
    machine: object = None
    status: str = "位置未特定"
    choices: tuple = ()

    @property
    def key(self):
        return self.link["assignment_id"]

    @property
    def token(self):
        return self.link["token"]


def reconcile(link, machines, references=()):
    if link["machine_key"]:
        found = [m for m in machines if m.uid == link["machine_key"]]
        if len(found) == 1:
            return found[0], "内部IDで一致", ()
    token = link["token"]
    number = normalize_number(token, allow_bare=True) or link["machine_number"]
    if number:
        found = [
            m
            for m in machines
            if number in {machine_key(m), normalize_number(m.number, allow_bare=True)}
        ]
        if found:
            return (
                (found[0], "機械番号で一致", ())
                if len(found) == 1
                else (None, "照合要確認", tuple(found))
            )
    key = normalize_reference(token) or normalize_reference(link["management_number"])
    mapping, problems = reference_index(list(references))
    if key in problems:
        choices = [
            m
            for m in machines
            if normalize_reference(m.source_key) == key
            or machine_key(m)
            in {
                normalize_number(r["number"])
                for r in references
                if normalize_reference(r["source_key"]) == key
            }
        ]
        return None, "照合要確認", tuple(choices)
    if key and key in mapping:
        found = [
            m
            for m in machines
            if mapping[key] in {machine_key(m), normalize_number(m.number, allow_bare=True)}
        ]
        if found:
            return (
                (found[0], "参考表で一致", ())
                if len(found) == 1
                else (None, "照合要確認", tuple(found))
            )
    if key:
        found = [m for m in machines if normalize_reference(m.source_key) == key]
        if found:
            return (
                (found[0], "管理ナンバーで一致", ())
                if len(found) == 1
                else (None, "照合要確認", tuple(found))
            )
    return None, "位置未特定", ()


def booths(products, machines=(), references=(), all_entries=False):
    entries = sorted(products, key=lambda p: p.order) if all_entries else ordered(products)
    output = []
    # Number all displayed entries in registration order; omitted output rows leave gaps
    # in the optional all-entry map, never renumber existing printed booths.
    numbered = ordered(products)
    labels = {}
    for p in numbered:
        prepare_entry(p)
        for link in p.booth_assignments:
            labels[link["assignment_id"]] = len(labels) + 1
    next_number = len(labels)
    for p in entries:
        prepare_entry(p)
        for link in p.booth_assignments:
            if link["assignment_id"] not in labels:
                next_number += 1
                labels[link["assignment_id"]] = next_number
            m, status, choices = reconcile(link, machines, references)
            output.append(Booth(p, link, labels[link["assignment_id"]], m, status, choices))
    return output


def number_lookup(products):
    return {(b.product.entry_id, b.token): b.number for b in booths(products)}


def display_badge(number):
    return chr(0x245F + number) if 1 <= number <= 20 else f"({number})"


def output_entries(products, day=None):
    return ordered([p for p in products if day is None or p.day == day])


def import_values(p):
    return {
        k: (getattr(p, k).isoformat() if k == "day" else deepcopy(getattr(p, k)))
        for k in IMPORT_FIELDS
    }


def merge_import(existing, incoming, policy="manual", field_choices=None):
    """Stable row context first, then an unambiguous original identity; never drop manual rows."""
    if policy not in ("manual", "excel", "fields"):
        raise AppError("Excel更新方針が不正です。")
    result = list(existing)
    matched = set()
    conflicts = []
    for fresh in incoming:
        prepare_entry(fresh)
        values = import_values(fresh)
        pool = [p for p in existing if p.entry_id not in matched and p.source_type == "auto"]
        exact = [
            p
            for p in pool
            if p.day == fresh.day
            and p.source_row == fresh.source_row
            and (p.import_snapshot.get("name", p.name) == fresh.name)
        ]
        if not exact:
            exact = [
                p
                for p in pool
                if p.day == fresh.day
                and p.import_snapshot.get("name", p.name) == fresh.name
                and p.import_snapshot.get("maker", p.maker) == fresh.maker
            ]
        if not exact:
            candidates = [
                p
                for p in pool
                if p.day == fresh.day and p.source_row == fresh.source_row and p.source_row > 0
            ]
            if len(candidates) == 1 and not any(
                n.name == candidates[0].import_snapshot.get("name", candidates[0].name)
                for n in incoming
            ):
                exact = candidates
        if len(exact) != 1:
            fresh.import_snapshot = values
            result.append(fresh)
            if len(exact) > 1:
                conflicts.append((fresh.entry_id, "identity", "照合要確認"))
            continue
        old = exact[0]
        prepare_entry(old)
        matched.add(old.entry_id)
        for key in IMPORT_FIELDS:
            value = getattr(fresh, key)
            if getattr(old, key) != value and key in old.manually_edited_fields:
                conflicts.append((old.entry_id, key, deepcopy(value)))
                use_excel = (
                    policy == "excel"
                    or policy == "fields"
                    and (field_choices or {}).get((old.entry_id, key), False)
                )
                if not use_excel:
                    continue
                old.manually_edited_fields.remove(key)
            setattr(old, key, deepcopy(value))
        old.import_snapshot = values
        old.updated_at = stamp()
    return result, conflicts
