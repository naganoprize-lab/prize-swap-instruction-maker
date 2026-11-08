"""Independent booth plans and actual per-machine operating methods."""

from collections import Counter
from dataclasses import dataclass
import re

from .core import AppError, machine_numbers, split_values

UNKNOWN_SUFFIX = "（台数未設定）"
MAX_COUNT = 2_147_483_647


def validate_counts(value):
    if not isinstance(value, dict):
        raise AppError("予定ブース数は方法名と整数の組で保存してください。")
    result = {}
    for name, count in value.items():
        if not isinstance(name, str) or not name.strip():
            raise AppError("運営方法名は必須です。")
        if count is not None and (type(count) is not int or not 0 <= count <= MAX_COUNT):
            raise AppError("予定ブース数は0以上の整数で指定してください。")
        if name.strip() in result:
            raise AppError("同じ運営方法名が重複しています。")
        result[name.strip()] = count
    return result


def legacy_names(value):
    if isinstance(value, str):
        return split_values(value)
    if not isinstance(value, list) or any(not isinstance(x, str) for x in value):
        raise AppError("運営方法の形式が不正です。")
    return list(dict.fromkeys(name.strip() for name in value if name.strip()))


def registered_machines(product):
    text = product.machines if isinstance(product.machines, str) else "\n".join(product.machines)
    return machine_numbers(text) or list(getattr(product, "management_numbers", []))


def validate_assignments(value):
    if not isinstance(value, dict):
        raise AppError("マシンごとの運営方法が不正です。")
    result = {}
    for number, method in value.items():
        if not isinstance(number, str) or machine_numbers(number) != [number]:
            raise AppError("運営方法に関連付けたマシン番号が不正です。")
        if method is not None and not isinstance(method, str):
            raise AppError("マシンの運営方法名が不正です。")
        result[number] = method.strip() or None if isinstance(method, str) else None
    return result


def active_assignments(product):
    # A registered number alone is not an assignment. Empty/whitespace-only
    # legacy values remain unassigned; duplicate numbers are canonicalized.
    result = {}
    for number in registered_machines(product):
        method = product.machine_methods.get(number)
        result[number] = (method.strip() or None) if isinstance(method, str) else None
    return result


def sync_machine_methods(product):
    active = set(registered_machines(product))
    for number in list(product.machine_methods):
        if number not in active:
            product.retired_machine_methods[number] = product.machine_methods.pop(number)
    for number in registered_machines(product):
        if number not in product.machine_methods:
            product.machine_methods[number] = product.retired_machine_methods.pop(number, None)


def plans(product):
    result = dict.fromkeys(legacy_names(product.methods), None)
    result.update(validate_counts(product.planned_booths))
    return result


def allocations(product):
    counts = Counter(method for method in active_assignments(product).values() if method)
    result = {name: counts.pop(name, 0) for name, count in plans(product).items() if count != 0}
    result.update(counts)
    return result


def parse_allocations(value, current=None):
    if isinstance(value, dict):
        return validate_counts(value)
    if isinstance(value, (list, tuple)):
        try:
            if any(not isinstance(pair, (list, tuple)) or len(pair) != 2 for pair in value):
                raise ValueError()
            mapping = dict(value)
            if len(mapping) != len(value):
                raise ValueError()
            return validate_counts(mapping)
        except (TypeError, ValueError) as exc:
            raise AppError("予定ブース数の組が不正です。") from exc
    result = {}
    for token in split_values(str(value)):
        if token.endswith(UNKNOWN_SUFFIX):
            name, count = token[: -len(UNKNOWN_SUFFIX)], None
        elif "×" in token:
            name, number = token.rsplit("×", 1)
            if not re.fullmatch(r"\s*[0-9]+\s*", number):
                raise AppError("予定ブース数は0以上の整数で指定してください。")
            count = int(number)
        else:
            name, count = token, (current or {}).get(token)
        result[name.strip()] = count
    return validate_counts(result)


def set_allocations(product, value):
    if isinstance(value, dict) and "plans" in value and "assignments" in value:
        requested_plans = parse_allocations(value["plans"])
        try:
            requested_assignments = validate_assignments(dict(value["assignments"]))
        except (ValueError, TypeError) as exc:
            raise AppError("マシン別の運営方法が不正です。") from exc
        if set(requested_assignments) - set(registered_machines(product)):
            raise AppError("登録されていないマシンの割当です。番号を再確認してください。")
    else:
        requested_plans = parse_allocations(value, plans(product))
        requested_assignments = dict(product.machine_methods)
    product.planned_booths = requested_plans
    product.methods = [name for name, count in requested_plans.items() if count != 0]
    product.machine_methods = requested_assignments
    sync_machine_methods(product)


def editor_state(product):
    # Lists preserve insertion order when traversing Qt's QVariant/proxy model.
    return dict(
        plans=list(plans(product).items()),
        assignments=list(active_assignments(product).items()),
    )


def format_counts(counts, separator="、"):
    return separator.join(
        name + (UNKNOWN_SUFFIX if count is None else f"×{count}")
        for name, count in counts.items()
        if count != 0
    )


def format_allocations(product, separator="、"):
    actual = allocations(product)
    if not any(active_assignments(product).values()):
        return separator.join(
            name + (UNKNOWN_SUFFIX if count is None else "×0")
            for name, count in plans(product).items()
            if count != 0
        )
    return separator.join(f"{name}×{count}" for name, count in actual.items())


def format_cell_status(product):
    status = allocation_status(product)
    pending = (
        (f"{status.unassigned}＋未設定" if status.unassigned else "未設定")
        if status.unknown
        else str(status.unassigned)
    )
    text = f"割当{status.assigned}／未割当{pending}"
    if status.excess:
        text += f"／超過{status.excess}"
    methods = format_allocations(product)
    # Keep the counts visible even when the list of method names is elided.
    return f"{text}　{methods}" if methods else text


def format_plans(product, separator="\n"):
    values = plans(product)
    for name in allocations(product):
        values.setdefault(name, None)
    return separator.join(
        f"{name} {count}ブース" if count is not None else f"{name}：予定未設定"
        for name, count in values.items()
        if count != 0 or allocations(product).get(name)
    )


@dataclass(frozen=True)
class BoothTotal:
    method: str
    planned: int
    assigned: int
    unassigned: int
    excess: int
    unknown: int = 0


def total_booths(rows):
    rows = list(rows)
    return BoothTotal(
        "合計",
        *(
            sum(getattr(row, field) for row in rows)
            for field in ("planned", "assigned", "unassigned", "excess", "unknown")
        ),
    )


def booth_value(row, field):
    value = getattr(row, field)
    if row.unknown and field != "assigned":
        return f"{value}＋未設定" if value else "未設定"
    return str(value)


def booth_report(product, compact=False):
    """Measured print text and semantic colour spans, shared by all printers.

    Names and plans are printed once per product. Compact metric labels are
    expanded by the page legend, so the report also works in monochrome.
    """
    rows = booth_totals(product)
    if not rows:
        return "", []
    parts, spans, offset = [], [], 0

    def append(text, field=None):
        nonlocal offset
        if field:
            spans.append((offset, len(text.encode("utf-16-le")) // 2, field))
        parts.append(text)
        offset += len(text.encode("utf-16-le")) // 2

    for row in [*rows, total_booths(rows)]:
        if parts:
            append("\n")
        if compact:
            plan = (
                f"{row.planned}+?"
                if row.unknown and row.planned
                else "?" if row.unknown else str(row.planned)
            )
            append(f"{row.method} {plan} ")
        else:
            plan = (
                (f"{row.planned}ブース＋予定未設定" if row.planned else "予定未設定")
                if row.unknown
                else f"{row.planned}ブース"
            )
            append(f"{row.method} {plan}\n")
        for i, (field, label) in enumerate(
            (("assigned", "済"), ("unassigned", "未"), ("excess", "超"))
        ):
            if i:
                append(" ")
            count = str(getattr(row, field))
            if row.unknown and field != "assigned":
                count = f"{count}+?" if getattr(row, field) else "?"
            append(label + count, field)
    return "".join(parts), spans


def booth_totals(product):
    expected = plans(product)
    actual = allocations(product)
    result = []
    for name in dict.fromkeys([*expected, *actual]):
        planned = expected.get(name)
        assigned = actual.get(name, 0)
        if planned == 0 and not assigned:
            continue
        result.append(
            BoothTotal(
                name,
                planned or 0,
                assigned,
                max(0, planned - assigned) if planned is not None else 0,
                max(0, assigned - planned) if planned is not None else 0,
                int(planned is None),
            )
        )
    return result


def aggregate_booths(products):
    result = {}
    for product in products:
        if not product.included:
            continue
        for row in booth_totals(product):
            values = result.setdefault(row.method, [0] * 5)
            for i, value in enumerate(
                (row.planned, row.assigned, row.unassigned, row.excess, row.unknown)
            ):
                values[i] += value
    return [BoothTotal(name, *values) for name, values in result.items()]


@dataclass(frozen=True)
class AllocationStatus:
    registered: int
    assigned: int
    planned: int
    unknown: int
    unassigned: int
    excess: int
    unmapped: int

    @property
    def message(self):
        pending = f"{self.unassigned}＋未確定" if self.unknown else str(self.unassigned)
        excess = f"{self.excess}＋未確定" if self.unknown else str(self.excess)
        text = f"予定{self.planned}ブース / 登録{self.registered}台 / 方法割当{self.assigned}台 / 未割当{pending} / 超過{excess}"
        if self.unknown:
            text += f" / 予定未設定{self.unknown}方法"
        if self.unmapped:
            text += f" / マシンの方法未設定{self.unmapped}台"
        if not self.registered:
            text += " / マシン番号未設定"
        return ("警告：" if self.excess or self.planned > self.registered else "") + text


def status_for_counts(counts, registered):
    # Used by the plan controls before assigning individual machines.
    values = validate_counts(counts)
    planned = sum(c for c in values.values() if c is not None)
    return AllocationStatus(
        registered,
        0,
        planned,
        sum(c is None for c in values.values()),
        planned,
        0,
        registered,
    )


def allocation_status(product):
    rows = booth_totals(product)
    methods = active_assignments(product)
    return AllocationStatus(
        len(methods),
        sum(bool(m) for m in methods.values()),
        sum(r.planned for r in rows),
        sum(r.unknown for r in rows),
        sum(r.unassigned for r in rows),
        sum(r.excess for r in rows),
        sum(not m for m in methods.values()),
    )
