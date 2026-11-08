"""Workbook-structure detection and explicit column profiles. Filenames never decide a type."""

from copy import deepcopy
from hashlib import sha256
from pathlib import Path
import re, unicodedata
from openpyxl import load_workbook
from .core import AppError
from .machine_matching import normalize_number, normalize_reference

TYPES = {
    "sales": "売上分析・景品運営分析表",
    "arrivals": "入荷予定表",
    "references": "管理ナンバー参考表",
    "layout": "売り場レイアウト",
}
FIELDS = {
    "number": "マシン番号",
    "management_number": "管理ナンバー",
    "product": "商品名",
    "machine_name": "機器・機種",
    "sales": "売上金額",
    "average": "平均売上（内部保持）",
    "operating_days": "稼働日数",
    "gross_profit": "粗利益",
    "cost_rate": "原価率",
    "po_rate": "P/O率",
    "dispensed": "払出数",
    "per_item": "個当たり",
    "unit_price": "景品単価",
    "cost": "原価総額",
    "prize_cost": "景品原価（単価）",
    "play_amount": "プレイ料金",
    "play_count": "料金回数",
    "actual_plays": "プレイ回数",
    "acquired_count": "景品獲得数",
    "inserted_amount": "投入金額",
    "day": "稼働予定日・入荷日",
    "installed_on": "設置日・投入日",
    "maker": "メーカー",
    "units": "入数",
    "cases": "CS数",
    "operation_method": "運営方法",
    "ratio": "アベ比",
    "x": "X座標",
    "y": "Y座標",
    "width": "カード幅",
    "height": "カード高さ",
}
TYPE_FIELDS = {
    "sales": list(FIELDS)[:20] + ["installed_on", "ratio"],
    "arrivals": ["day", "product", "units", "cases", "maker", "number", "management_number"],
    "references": ["management_number", "number"],
    "layout": [
        "number",
        "management_number",
        "product",
        "machine_name",
        "x",
        "y",
        "width",
        "height",
        "sales",
        "average",
        "ratio",
        "po_rate",
        "cost_rate",
        "installed_on",
        "maker",
        "units",
        "cases",
        "operation_method",
    ],
}
ALIASES = {
    "number": ["機器番号", "機械番号", "マシン番号", "machine_id", "machine number"],
    "management_number": ["管理番号", "管理ナンバー", "management_id"],
    "product": ["商品名", "品名", "景品名", "product_name"],
    "machine_name": ["機器", "機種", "機種名", "機器名", "machine_type"],
    "sales": ["売上金額", "売上", "売上高", "sales", "sales_amount"],
    "average": ["平均売上", "アベレージ", "日平均売上"],
    "operating_days": ["稼働日数"],
    "gross_profit": ["粗利益"],
    "cost_rate": ["原価率", "景品原価率", "cost_rate"],
    "po_rate": ["P/O率", "PO率", "po_rate"],
    "dispensed": ["払出数", "払い出し数"],
    "per_item": ["個当たり", "個あたり"],
    "unit_price": ["景品単価"],
    "cost": ["原価", "原価総額", "景品原価総額"],
    "prize_cost": ["景品原価", "原価単価"],
    "play_amount": ["プレイ料金", "1プレイ料金"],
    "play_count": ["料金回数"],
    "actual_plays": ["プレイ回数"],
    "acquired_count": ["景品獲得数", "獲得個数"],
    "inserted_amount": ["投入金額"],
    "day": ["稼働予定日", "入荷予定日", "入荷日", "登録日"],
    "installed_on": ["設置日", "投入日"],
    "maker": ["メーカー", "製造元"],
    "units": ["入数"],
    "cases": ["CS数", "ケース数"],
    "operation_method": ["運営方法"],
    "ratio": ["アベ比", "アベ比(%)"],
    "x": ["X", "X座標", "横座標"],
    "y": ["Y", "Y座標", "縦座標"],
    "width": ["幅", "カード幅"],
    "height": ["高さ", "カード高さ"],
}


def canonical(value):
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return re.sub(r"[\s/_（）()%％]", "", text)


IGNORED = {canonical("景品種別")}


def header_signature(headers):
    return sha256(
        "|".join("" if canonical(v) in IGNORED else canonical(v) for v in headers).encode()
    ).hexdigest()


def recognize_headers(headers):
    mapping = {}
    conflicts = []
    for key, names in ALIASES.items():
        found = [
            i for i, v in enumerate(headers) if canonical(v) in {canonical(name) for name in names}
        ]
        if len(found) == 1:
            mapping[key] = found[0]
        elif len(found) > 1:
            conflicts.append(key)
    return mapping, conflicts


def make_profile(kind, sheet, header_row, headers, mapping=None, layout_mode="table"):
    signature = header_signature(headers)
    return dict(
        profile_id=sha256((kind + "|" + signature).encode()).hexdigest()[:24],
        file_type=kind,
        sheet_name=sheet,
        header_row=header_row,
        column_mapping=dict(mapping or {}),
        layout_mode=layout_mode,
        rate_units={},
        template_signature=signature,
        last_used_at="",
    )


def validate_profile(value, required=True):
    keys = {
        "profile_id",
        "file_type",
        "sheet_name",
        "header_row",
        "column_mapping",
        "layout_mode",
        "rate_units",
        "template_signature",
        "last_used_at",
    }
    if not isinstance(value, dict) or set(value) != keys:
        raise AppError("Excelの列対応設定が不正です。")
    p = deepcopy(value)
    m = p["column_mapping"]
    if (
        p["file_type"] not in TYPES
        or p["layout_mode"] not in ("table", "cells")
        or type(p["header_row"]) is not int
        or not 1 <= p["header_row"] <= 1000
        or any(
            not isinstance(p[k], str)
            for k in ("profile_id", "sheet_name", "template_signature", "last_used_at")
        )
    ):
        raise AppError("Excelのシート・見出し設定が不正です。")
    if p["file_type"] != "layout" and p["layout_mode"] != "table":
        raise AppError("セル配置はレイアウト読込でのみ使用できます。")
    if (
        not isinstance(m, dict)
        or set(m) - set(FIELDS)
        or any(type(v) is not int or not 0 <= v <= 1023 for v in m.values())
        or len(set(m.values())) != len(m)
    ):
        raise AppError("同じ列を複数項目に指定せず、正しい列を選択してください。")
    if (
        not isinstance(p["rate_units"], dict)
        or set(p["rate_units"]) - {"po_rate", "cost_rate"}
        or any(v not in ("fraction", "percent") for v in p["rate_units"].values())
    ):
        raise AppError("率の単位設定が不正です。")
    if required:
        kind = p["file_type"]
        valid = {
            "sales": ("sales" in m and ("number" in m or "management_number" in m)),
            "arrivals": ("day" in m and "product" in m),
            "references": ("management_number" in m and "number" in m),
            "layout": (
                p["layout_mode"] == "cells"
                or "x" in m
                and "y" in m
                and ("number" in m or "management_number" in m)
            ),
        }[kind]
        if not valid:
            raise AppError("必須列がありません。ファイル種別と列対応を確認してください。")
    return p


def workbook_headers(path, sheet, row):
    wb = load_workbook(path, data_only=True, read_only=True)
    try:
        if sheet not in wb.sheetnames:
            raise AppError("対象シートがありません。")
        values = next(
            wb[sheet].iter_rows(
                min_row=row, max_row=row, max_col=min(1024, wb[sheet].max_column), values_only=True
            ),
            (),
        )
        return [None if canonical(v) in IGNORED else v for v in values]
    finally:
        wb.close()


def detect_workbook(path, saved_profiles=()):
    path = Path(path)
    if path.suffix.lower() not in (".xlsx", ".xlsm"):
        raise AppError("xlsx / xlsmを選択してください。マクロは実行しません。")
    try:
        wb = load_workbook(path, data_only=True, read_only=True)
    except Exception as exc:
        raise AppError("Excelを開けません：" + str(exc)) from exc
    result = []
    try:
        for sheet in wb:
            sample = list(
                sheet.iter_rows(
                    max_row=min(sheet.max_row, 50),
                    max_col=min(sheet.max_column, 80),
                    values_only=True,
                )
            )
            table_found = False
            for row, headers in enumerate(sample, 1):
                mapping, conflicts = recognize_headers(headers)
                signature = header_signature(headers)
                kinds = []
                if "sales" in mapping and ("number" in mapping or "management_number" in mapping):
                    kinds.append("sales")
                if "day" in mapping and "product" in mapping:
                    kinds.append("arrivals")
                if (
                    "management_number" in mapping
                    and "number" in mapping
                    and "sales" not in mapping
                ):
                    kinds.append("references")
                if (
                    "x" in mapping
                    and "y" in mapping
                    and ("number" in mapping or "management_number" in mapping)
                ):
                    kinds.append("layout")
                for old in saved_profiles:
                    if old.get("template_signature") == signature:
                        p = deepcopy(old)
                        p.update(sheet_name=sheet.title, header_row=row)
                        result.append(dict(profile=p, confidence="保存済みの列対応", conflicts=[]))
                        table_found = True
                for kind in kinds:
                    fields = TYPE_FIELDS[kind]
                    p = make_profile(
                        kind,
                        sheet.title,
                        row,
                        headers,
                        {k: v for k, v in mapping.items() if k in fields},
                    )
                    result.append(
                        dict(profile=p, confidence="見出しと列構成で認識", conflicts=conflicts)
                    )
                    table_found = True
            if not table_found:
                identifier_cells = [
                    (r, c)
                    for r, row in enumerate(sample)
                    for c, v in enumerate(row)
                    if normalize_reference(v) or normalize_number(v)
                ]
                identifiers = len(identifier_cells)
                if identifiers and (
                    "レイアウト" in sheet.title
                    or identifiers >= 2
                    and len({c for r, c in identifier_cells}) > 1
                ):
                    p = make_profile(
                        "layout", sheet.title, 1, sample[0] if sample else [], layout_mode="cells"
                    )
                    result.append(
                        dict(profile=p, confidence="セル配置の識別子で認識", conflicts=[])
                    )
        unique = {}
        for candidate in result:
            p = candidate["profile"]
            key = (p["file_type"], p["sheet_name"], p["header_row"], p["layout_mode"])
            if key not in unique or candidate["confidence"].startswith("保存"):
                unique[key] = candidate
        return dict(sheets=list(wb.sheetnames), candidates=list(unique.values()))
    finally:
        wb.close()
