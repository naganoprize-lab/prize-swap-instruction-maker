"""Read-only analysis import and numbered overlays, separate from floor/product data."""

from copy import deepcopy
from datetime import datetime, timezone, timedelta
import math
from pathlib import Path
import re
import unicodedata

from openpyxl import load_workbook
from openpyxl.utils import get_column_letter
from .core import AppError, machine_numbers
from .floor_status import numeric, percent, reference_average

METRICS = {
    "sales": "売上金額",
    "average": "平均売上",
    "operating_days": "稼働日数",
    "gross_profit": "粗利益",
    "cost_rate": "原価率",
    "po_rate": "P/O率",
    "dispensed": "払出数",
    "per_item": "個当たり",
    "unit_price": "景品単価",
    "cost": "原価",
}
COLUMNS = {
    5: "sales",
    6: "operating_days",
    7: "average",
    8: "gross_profit",
    9: "cost_rate",
    10: "po_rate",
    11: "dispensed",
    12: "per_item",
    14: "unit_price",
    15: "cost",
}
HEADERS = {1: "機器番号", 4: "商品名", **{col: METRICS[key] for col, key in COLUMNS.items()}}


def normalize_number(value):
    if not isinstance(value, str):
        return None
    match = re.fullmatch(r"([0-9]+)-(01|02)", value.strip())
    return match[1] + ("左" if match[2] == "01" else "右") if match else None


def empty_snapshot():
    return dict(
        version=1,
        records={},
        file_name="",
        source="",
        loaded_at="",
        errors=[],
        ignored_rows=0,
        duplicate_rows=0,
    )


def validate_values(values):
    if not isinstance(values, dict) or set(values) != set(METRICS):
        raise AppError("売上分析の項目が不正です。")
    for key, value in values.items():
        if value is None:
            if key in ("sales", "average", "operating_days"):
                raise AppError("売上・平均売上・稼働日数は必須です。")
            continue
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
        ):
            raise AppError(f"{METRICS[key]}が数値ではありません。")
        if key == "operating_days" and value < 0:
            raise AppError("稼働日数は0以上で指定してください。")
        if key == "dispensed" and (value < 0 or value != int(value)):
            raise AppError("払出数は0以上の整数で指定してください。")
    return values


def validate_snapshot(value):
    if value is None:
        return empty_snapshot()
    if not isinstance(value, dict) or value.get("version") != 1:
        raise AppError("売上分析の保存形式が不正です。")
    records = value.get("records")
    if not isinstance(records, dict) or len(records) > 10000:
        raise AppError("売上分析の件数が不正です。")
    for number, record in records.items():
        if (
            not isinstance(number, str)
            or machine_numbers(number) != [number]
            or not number.endswith(("左", "右"))
        ):
            raise AppError("売上分析のマシン番号が不正です。")
        if (
            not isinstance(record, dict)
            or normalize_number(record.get("raw_number")) != number
            or not isinstance(record.get("reference_name"), str)
            or type(record.get("row")) is not int
        ):
            raise AppError("売上分析の照合情報が不正です。")
        validate_values(record.get("values"))
    if any(not isinstance(value.get(k), str) for k in ("source", "file_name", "loaded_at")):
        raise AppError("売上分析の読込情報が不正です。")
    if not isinstance(value.get("errors"), list) or len(value["errors"]) > 10000:
        raise AppError("売上分析のエラー情報が不正です。")
    for error in value["errors"]:
        if (
            not isinstance(error, dict)
            or type(error.get("row")) is not int
            or not all(isinstance(error.get(k), str) for k in ("number", "message"))
        ):
            raise AppError("売上分析のエラー行が不正です。")
    for key in ("ignored_rows", "duplicate_rows"):
        if type(value.get(key)) is not int or value[key] < 0:
            raise AppError("売上分析の除外件数が不正です。")
    return value


def validate_overlay(value):
    if value is None:
        return
    if not isinstance(value, dict) or any(
        not isinstance(value.get(k), str) for k in ("file_name", "loaded_at")
    ):
        raise AppError("マシンの売上分析情報が不正です。")
    validate_values(value.get("values"))


def load_analysis(path):
    path = Path(path)
    try:
        workbook = load_workbook(path, data_only=True, read_only=True)
    except Exception as exc:
        raise AppError(f"売上分析Excelを開けません：{exc}") from exc
    try:
        if "景品運営分析表" not in workbook.sheetnames:
            raise AppError("「景品運営分析表」シートがありません。")
        sheet = workbook["景品運営分析表"]
        if sheet.max_row > 10001:
            raise AppError("売上分析は10000行以下で読み込んでください。")

        def canonical(v):
            return unicodedata.normalize("NFKC", str(v or "")).replace(" ", "").replace("/", "")

        header = next(sheet.iter_rows(min_row=1, max_row=1, max_col=16))
        for col, label in HEADERS.items():
            if canonical(header[col].value) != canonical(label):
                raise AppError(
                    f"{get_column_letter(col+1)}列のヘッダーは「{label}」にしてください。"
                )
        result = empty_snapshot()
        result.update(
            source=str(path.resolve()),
            file_name=path.name,
            loaded_at=datetime.now(timezone(timedelta(hours=9))).isoformat(timespec="seconds"),
        )
        conflicts = set()
        for row in sheet.iter_rows(min_row=2, max_col=16):
            number = normalize_number(row[1].value)
            if number is None:
                if any(c.value is not None for c in row):
                    result["ignored_rows"] += 1
                continue
            values = {
                key: (
                    percent(row[col].value, row[col].number_format)
                    if key in ("cost_rate", "po_rate")
                    else numeric(row[col].value)
                )
                for col, key in COLUMNS.items()
            }
            try:
                for col, key in COLUMNS.items():
                    if (
                        key not in ("po_rate", "cost_rate")
                        and isinstance(row[col].value, str)
                        and "%" in unicodedata.normalize("NFKC", row[col].value)
                    ):
                        raise AppError(f"{METRICS[key]}にパーセント文字列は使用できません。")
                    if row[col].value not in (None, "") and values[key] is None:
                        raise AppError(f"{METRICS[key]}を数値として読み込めません。")
                validate_values(values)
            except AppError as exc:
                result["errors"].append(
                    dict(row=row[1].row, number=number, message=str(exc), kind="numeric")
                )
                continue
            record = dict(
                raw_number=row[1].value.strip(),
                reference_name=str(row[4].value or ""),
                row=row[1].row,
                values=values,
            )
            if number in conflicts:
                result["duplicate_rows"] += 1
                continue
            previous = result["records"].get(number)
            if previous:
                result["duplicate_rows"] += 1
                if previous["values"] != values:
                    result["records"].pop(number)
                    conflicts.add(number)
                    result["errors"].append(
                        dict(
                            row=row[1].row,
                            number=number,
                            message="同じ機器番号に異なる実績があります。照合せず除外しました。",
                            kind="duplicate",
                        )
                    )
                continue
            result["records"][number] = record
        if not result["records"] and not result["errors"]:
            raise AppError(
                "正規の機器番号を持つ実績行がありません。検索条件等は実績として扱いません。"
            )
        return validate_snapshot(result)
    finally:
        workbook.close()


def ensure_references(machines):
    candidates = [reference_average(m) for m in machines]
    known = [
        value
        for machine, value in zip(machines, candidates)
        if value is not None
        and value > 0
        and (
            machine.reference_average is not None
            or (machine.ratio is not None and machine.ratio > 0)
        )
    ]
    common = (
        known[0]
        if len(known) >= 2 and all(math.isclose(v, known[0], rel_tol=1e-9) for v in known)
        else None
    )
    for machine, value in zip(machines, candidates):
        if machine.reference_average is None:
            machine.reference_average = value if value is not None else common


def apply_snapshot(machines, snapshot):
    records = snapshot["records"]
    if records:
        ensure_references(machines)
    for machine in machines:
        record = records.get(machine.number)
        machine.analysis = (
            (
                dict(
                    values=deepcopy(record["values"]),
                    file_name=snapshot["file_name"],
                    loaded_at=snapshot["loaded_at"],
                )
            )
            if record
            else None
        )


def import_summary(machines, snapshot):
    numbers = {m.number for m in machines if m.number}
    matched = set(snapshot["records"]) & numbers
    unmatched = sorted(set(snapshot["records"]) - numbers)
    return dict(
        read=len(snapshot["records"]),
        matched=len(matched),
        unmatched=unmatched,
        numeric_errors=sum(e.get("kind", "numeric") == "numeric" for e in snapshot["errors"]),
        read_errors=len(snapshot["errors"]),
        file_name=snapshot["file_name"],
        loaded_at=snapshot["loaded_at"],
        ignored=snapshot["ignored_rows"],
        duplicates=snapshot["duplicate_rows"],
    )
