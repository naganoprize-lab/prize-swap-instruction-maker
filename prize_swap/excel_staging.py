"""Adapt explicit column profiles to the existing parsers, without writing input files."""

from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
import re
from openpyxl import load_workbook, Workbook
from .core import AppError, Product, read_products, machine_numbers, parse_date
from .excel_profiles import validate_profile, canonical, IGNORED
from .machine_matching import (
    normalize_number,
    normalize_reference,
    read_reference_rows,
    reference_index,
    validate_references,
)
from .floor_status import numeric, percent
from .performance_validation import ratio_value


@dataclass
class PreparedImport:
    profile: dict
    source: str
    data: object = None
    count: int = 0
    warnings: list = field(default_factory=list)
    errors: list = field(default_factory=list)
    unknown_columns: list = field(default_factory=list)
    samples: list = field(default_factory=list)


def reference_numbers(floor):
    mapping, problems = reference_index(floor.number_references)
    claims = {}
    from .machine_matching import machine_key

    for m in floor.machines:
        key = normalize_reference(m.source_key)
        number = machine_key(m)
        if key and number:
            claims.setdefault(key, set()).add(number)
    for key, values in claims.items():
        if (
            len(values) == 1
            and key not in problems
            and (key not in mapping or mapping[key] in values)
        ):
            mapping[key] = next(iter(values))
        else:
            mapping.pop(key, None)
    return mapping


def prepare_import(path, profile, floor=None, tolerance=None):
    p = validate_profile(profile)
    path = Path(path)
    result = PreparedImport(p, str(path.resolve()))
    wb = load_workbook(path, data_only=True, read_only=False)
    try:
        if p["sheet_name"] not in wb.sheetnames:
            raise AppError("対象シートがありません。")
        sheet = wb[p["sheet_name"]]
        m = p["column_mapping"]
        header = p["header_row"]
        raw_headers = [c.value for c in sheet[header]]
        if any(canonical(raw_headers[i]) in IGNORED for i in m.values() if i < len(raw_headers)):
            raise AppError("対象外の列は選択できません。")
        result.unknown_columns = (
            [
                f"{i+1}列：{v}"
                for i, v in enumerate(raw_headers)
                if v not in (None, "") and i not in m.values() and canonical(v) not in IGNORED
            ]
            if p["layout_mode"] != "cells"
            else []
        )
        if sheet.max_row > 10001 and p["layout_mode"] != "cells":
            raise AppError("表データは10000行以下で指定してください。")

        def value(row, key):
            index = m.get(key)
            return row[index].value if index is not None and index < len(row) else None

        if p["file_type"] == "sales":
            from .sales_analysis import load_analysis, HEADERS, validate_snapshot

            normalized = Workbook()
            normalized.epoch = wb.epoch
            out = normalized.active
            out.title = "景品運営分析表"
            fields = {
                1: "number",
                2: "machine_name",
                4: "product",
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
                16: "prize_cost",
                17: "play_amount",
                18: "play_count",
                19: "actual_plays",
                20: "acquired_count",
                21: "inserted_amount",
            }
            titles = dict(HEADERS) | {
                16: "景品原価",
                17: "プレイ料金",
                18: "料金回数",
                19: "プレイ回数",
                20: "景品獲得数",
                21: "投入金額",
            }
            out.append([titles.get(i, "") for i in range(22)])
            references = reference_numbers(floor) if floor is not None else {}
            ancillary = []
            source_ids = {}
            unmatched = []
            missing_sales = []
            ignored_count = 0
            for row in sheet.iter_rows(min_row=header + 1):
                raw = value(row, "number")
                manager = normalize_reference(
                    value(row, "management_number")
                ) or normalize_reference(raw)
                number = normalize_number(raw)
                if number and manager in references and references[manager] != number:
                    raise AppError(f"{row[0].row}行：機械番号と管理ナンバーの対応が一致しません。")
                if not number and manager in references:
                    raw = references[manager]
                    number = normalize_number(raw)
                sale = value(row, "sales")
                source_number = number
                if number and sale in ("", None):
                    missing_sales.append(
                        dict(
                            row=row[0].row,
                            number=number,
                            message="売上金額が欠損しているため実績行から除外しました。",
                            kind="numeric",
                        )
                    )
                    raw = None
                    number = None
                if not number:
                    if any(c.value is not None for c in row):
                        ignored_count += 1
                    identifier = manager or value(row, "number")
                    if (
                        identifier not in ("", None)
                        and numeric(sale) is not None
                        and not re.search("検索|条件|日時|合計|集計", str(identifier))
                    ):
                        unmatched.append(dict(number=str(identifier), row=row[0].row))
                values = [None] * 22
                for col, key in fields.items():
                    v = raw if key == "number" else value(row, key)
                    if (
                        key in ("po_rate", "cost_rate")
                        and p["rate_units"].get(key) == "percent"
                        and v not in ("", None)
                        and "%" not in str(v)
                        and "%" not in row[m[key]].number_format
                    ):
                        amount = numeric(v)
                        v = amount / 100 if amount is not None else v
                    values[col] = v
                out.append(values)
                for col, key in fields.items():
                    if key in m:
                        out.cell(out.max_row, col + 1).number_format = row[m[key]].number_format
                if number:
                    source_ids.setdefault(
                        number,
                        dict(
                            source_number=value(row, "number"),
                            source_management_number=value(row, "management_number") or manager,
                            reference_installed_on=(
                                parse_date(value(row, "installed_on"), wb.epoch).isoformat()
                                if parse_date(value(row, "installed_on"), wb.epoch)
                                else None
                            ),
                            imported_ratio=(
                                percent(value(row, "ratio"), row[m["ratio"]].number_format)
                                if "ratio" in m
                                else None
                            ),
                        ),
                    )
                else:
                    ancillary.append(
                        [
                            c.value
                            for c in row
                            if canonical(c.value) not in IGNORED
                            and c.column - 1
                            not in {i for i, v in enumerate(raw_headers) if canonical(v) in IGNORED}
                        ]
                    )
            if header > 1:
                for row in sheet.iter_rows(max_row=header - 1):
                    ancillary.append([c.value for c in row if canonical(c.value) not in IGNORED])
            for row in ancillary:
                out.append([" ".join(str(v) for v in row if v is not None)])
            with TemporaryDirectory(prefix="prize-excel-stage-") as directory:
                temp = Path(directory) / "normalized.xlsx"
                normalized.save(temp)
                try:
                    snapshot = load_analysis(temp, tolerance)
                except AppError:
                    if not unmatched and not missing_sales:
                        raise
                    from .sales_analysis import empty_snapshot
                    from .performance_validation import metadata
                    from .workflow_state import now

                    snapshot = empty_snapshot()
                    snapshot.update(metadata(list(out.iter_rows())[1:]))
                    snapshot["loaded_at"] = now()
            snapshot.update(
                source=str(path.resolve()),
                file_name=path.name,
                fingerprint=sha256(path.read_bytes()).hexdigest(),
            )
            stamp = re.search(r"(\d{12,14})(?=\D|$)", path.stem)
            if stamp:
                try:
                    snapshot["filename_timestamp"] = datetime.strptime(
                        stamp[1], "%Y%m%d%H%M%S" if len(stamp[1]) == 14 else "%Y%m%d%H%M"
                    ).isoformat()
                except ValueError:
                    pass
            for number, record in snapshot["records"].items():
                record["row"] += header - 1
                record.update(source_ids.get(number, {}))
            for error in snapshot["errors"]:
                error["row"] += header - 1
            for issue in snapshot["issues"]:
                if issue.get("row"):
                    issue["row"] += header - 1
            snapshot["ignored_rows"] = ignored_count
            snapshot["errors"].extend(missing_sales)
            snapshot["unmatched_rows"] = list(
                {
                    (r["number"], r["row"]): r for r in snapshot["unmatched_rows"] + unmatched
                }.values()
            )
            snapshot = validate_snapshot(snapshot)
            result.data = snapshot
            result.count = len(snapshot["records"])
            result.warnings = (
                [i["message"] for i in snapshot["issues"]]
                + [f"{r['row']}行：未照合 {r['number']}" for r in unmatched]
                + [r["message"] for r in missing_sales]
            )
            result.samples = [
                f"{number} ／ 売上 {record['values']['sales'] if record['values']['sales'] is not None else '—'} ／ PO率 {record['values']['po_rate'] if record['values']['po_rate'] is not None else '—'}（内部比率）"
                for number, record in list(snapshot["records"].items())[:3]
            ]
        elif p["file_type"] == "arrivals":
            normalized = Workbook()
            normalized.epoch = wb.epoch
            out = normalized.active
            out.title = "縦"
            out.append(["稼働予定日", "品名", "入数", "CS数", "メーカー"])
            source_rows = {}
            for row in sheet.iter_rows(min_row=header + 1):
                out.append(
                    [value(row, key) for key in ("day", "product", "units", "cases", "maker")]
                )
                source_rows[out.max_row] = row
                for col, key in enumerate(("day", "product", "units", "cases", "maker"), 1):
                    if key in m:
                        out.cell(out.max_row, col).number_format = row[m[key]].number_format
            with TemporaryDirectory(prefix="prize-excel-stage-") as directory:
                temp = Path(directory) / "normalized.xlsx"
                normalized.save(temp)
                products, warnings = read_products(temp)
            for product in products:
                row = source_rows[product.source_row]
                raw = value(row, "number") or value(row, "management_number")
                if raw not in ("", None):
                    product.machines = machine_numbers(str(raw))
                product.source_row += header - 1
            result.data = products
            result.count = len(products)
            result.warnings = warnings
            result.samples = [f"{p.day} ／ {p.name}" for p in products[:3]]
        elif p["file_type"] == "references":
            normalized = Workbook()
            out = normalized.active
            out.title = "管理番号参考値"
            out.append(["管理番号", "機械番号"])
            for row in sheet.iter_rows(min_row=header + 1):
                a, b = value(row, "management_number"), value(row, "number")
                if a in ("", None) and b in ("", None):
                    continue
                out.append([a, b])
            refs = [
                dict(
                    source_key=normalize_reference(r["source_key"]) or r["source_key"],
                    number=normalize_number(r["number"]) or r["number"],
                )
                for r in read_reference_rows(normalized)
            ]
            mapping, problems = reference_index(refs)
            if problems:
                result.errors.extend(f"{k}：{v}" for k, v in problems.items())
            if any(
                not normalize_reference(r["source_key"]) or not normalize_number(r["number"])
                for r in refs
            ):
                result.errors.append("管理ナンバーまたは機械番号の形式が不正です。")
            result.data = refs
            result.count = len(mapping)
            result.samples = [f"{k} → {v}" for k, v in list(mapping.items())[:3]]
        elif p["file_type"] == "layout":
            from .floor_data import import_layout, Machine, validate_machines
            from dataclasses import asdict

            if p["layout_mode"] == "cells":
                if sheet.title != "投入中レイアウト":
                    if "投入中レイアウト" in wb.sheetnames:
                        wb["投入中レイアウト"].title = "旧レイアウト_未使用"
                    sheet.title = "投入中レイアウト"
                with TemporaryDirectory(prefix="prize-layout-stage-") as directory:
                    temp = Path(directory) / "layout.xlsx"
                    wb.save(temp)
                    machines, warnings, refs = import_layout(temp, with_references=True)
            else:
                machines = []
                warnings = []
                refs = read_reference_rows(wb)
                for row in sheet.iter_rows(min_row=header + 1):
                    if all(c.value is None for c in row):
                        continue
                    x, y = numeric(value(row, "x")), numeric(value(row, "y"))
                    if x is None or y is None:
                        raise AppError(f"{row[0].row}行：座標が不正です。配置を推測しません。")
                    raw = value(row, "number")
                    number = (
                        normalize_number(raw, allow_bare=True)
                        if isinstance(raw, str)
                        else normalize_number(str(raw)) if raw is not None else ""
                    )
                    manager = (
                        normalize_reference(value(row, "management_number"))
                        or normalize_reference(raw)
                        or ""
                    )
                    if raw not in ("", None) and not number and not manager:
                        raise AppError(f"{row[0].row}行：機械番号が不正です。")
                    mobj = Machine(
                        number=number or "",
                        source_key=manager,
                        product=str(value(row, "product") or ""),
                        kind=str(value(row, "machine_name") or "クレーン"),
                        x=x,
                        y=y,
                        width=(
                            numeric(value(row, "width")) if value(row, "width") is not None else 180
                        ),
                        height=(
                            numeric(value(row, "height"))
                            if value(row, "height") is not None
                            else 120
                        ),
                        installed_on=parse_date(value(row, "installed_on"), wb.epoch),
                        operation_method=str(value(row, "operation_method") or ""),
                    )
                    if value(row, "installed_on") not in ("", None) and mobj.installed_on is None:
                        raise AppError(f"{row[0].row}行：設置日の形式が不正です。")
                    for key in ("sales", "average", "ratio", "po_rate", "cost_rate"):
                        rawvalue = value(row, key)
                        if rawvalue not in ("", None):
                            parsed = (
                                ratio_value(rawvalue)
                                if key in ("po_rate", "cost_rate")
                                else (
                                    percent(rawvalue, row[m[key]].number_format)
                                    if key == "ratio"
                                    else numeric(rawvalue)
                                )
                            )
                            if parsed is None:
                                raise AppError(f"{row[0].row}行：{key}が数値ではありません。")
                            setattr(mobj, key, parsed)
                    machines.append(mobj)
                validate_machines([asdict(m) for m in machines])
            result.data = (machines, warnings, refs)
            result.count = len(machines)
            result.warnings = warnings
            result.samples = [
                f'{m.number or m.source_key or "番号未設定"} ／ ({m.x:g}, {m.y:g})'
                for m in machines[:3]
            ]
        if not result.count:
            result.errors.append("有効なデータ行がありません。既存データは変更しません。")
        return result
    finally:
        wb.close()
