"""Floor-plan persistence and read-only import of cell-based Excel layouts."""

from dataclasses import dataclass, field
from pathlib import Path
import math
import re
import unicodedata
from uuid import uuid4
from datetime import date, datetime

from openpyxl import load_workbook
from .core import AppError, atomic_json, machine_numbers, parse_date
from .floor_status import numeric, percent
from .performance_validation import ratio_value
from copy import deepcopy


@dataclass
class Machine:
    number: str = ""
    kind: str = "クレーン"
    product: str = ""
    x: float = 0
    y: float = 0
    width: float = 180
    height: float = 120
    source_key: str = ""
    uid: str = field(default_factory=lambda: uuid4().hex)
    sales: float | None = None
    average: float | None = None
    ratio: float | None = None
    ratio_from_excel: bool = False
    installed_on: str | None = None
    font_size: float = 12
    maker: str = ""
    po_rate: float | None = None
    units: int | float | str = ""
    cases: int | float | str = ""
    limit: str = ""
    operation_method: str = ""
    reference_average: float | None = None
    analysis: dict | None = None
    operating_days: float | None = None
    gross_profit: float | None = None
    cost_rate: float | None = None
    dispensed: float | None = None
    per_item: float | None = None
    unit_price: float | None = None
    cost: float | None = None
    reference_number: str = ""
    reference_warning: str = ""
    number_origin: str = "direct"
    manual_number: str | None = None
    resolved_number: str = ""
    match_method: str = "layout"
    match_message: str = ""
    layout_loaded_at: str = ""
    direct_number_raw: str = ""
    manual_updates: dict = field(default_factory=dict)
    prize_cost: float | None = None
    prize_cost_product: str = ""
    play_pricing: dict | None = None
    prize_cost_origin: str = "manual"
    po_definition: str = "prize_per_play"
    po_definition_fingerprint: str = ""

    def __post_init__(self):
        if isinstance(self.installed_on, (date, datetime)):
            self.installed_on = parse_date(self.installed_on).isoformat()


def validate_machines(records):
    if not isinstance(records, list) or len(records) > 5000:
        raise AppError("マシン配置データが不正です（最大5000枠）。")
    result, numbers, ids = [], set(), set()
    try:
        for record in records:
            m = Machine(**record)
            if any(
                not isinstance(getattr(m, key), str)
                for key in (
                    "number",
                    "kind",
                    "product",
                    "source_key",
                    "uid",
                    "maker",
                    "limit",
                    "operation_method",
                    "reference_number",
                    "reference_warning",
                    "number_origin",
                    "resolved_number",
                    "match_method",
                    "match_message",
                    "layout_loaded_at",
                    "direct_number_raw",
                )
            ):
                raise ValueError("文字列項目が不正")
            from .machine_matching import normalize_number, MATCH_LABELS
            from .machine_matching import MANUAL_FIELDS

            if (
                not isinstance(m.manual_updates, dict)
                or set(m.manual_updates) - set(MANUAL_FIELDS)
                or any(not isinstance(v, str) or len(v) > 100 for v in m.manual_updates.values())
            ):
                raise ValueError("手動入力の取得元情報が不正")

            if m.number_origin not in ("direct", "reference") or m.match_method not in MATCH_LABELS:
                raise ValueError("照合状態が不正")
            if m.manual_number is not None and (
                not isinstance(m.manual_number, str)
                or m.manual_number
                and normalize_number(m.manual_number) != m.manual_number
            ):
                raise ValueError("手動照合先が不正")
            if not m.uid or m.uid in ids:
                raise ValueError("マシンIDが重複")
            ids.add(m.uid)
            if m.number:
                if machine_numbers(m.number) != [m.number] or m.number in numbers:
                    raise ValueError("マシン番号が不正または重複")
                numbers.add(m.number)
            for key in ("x", "y", "width", "height"):
                value = getattr(m, key)
                if (
                    isinstance(value, bool)
                    or not isinstance(value, (float, int))
                    or not math.isfinite(value)
                    or not 0 <= value <= 100000
                ):
                    raise ValueError("位置・サイズが不正")
            if m.width < 80 or m.height < 60:
                raise ValueError("サイズが小さすぎます")
            for key in (
                "sales",
                "average",
                "ratio",
                "po_rate",
                "reference_average",
                "operating_days",
                "gross_profit",
                "cost_rate",
                "dispensed",
                "per_item",
                "unit_price",
                "cost",
            ):
                value = getattr(m, key)
                if value is not None and (
                    isinstance(value, bool)
                    or not isinstance(value, (int, float))
                    or not math.isfinite(value)
                ):
                    raise ValueError("稼働数値が不正")
            from .po_evaluation import validate_pricing, positive

            validate_pricing(m.play_pricing)
            from .po_evaluation import validate_definition

            validate_definition(m.po_definition)
            if m.prize_cost_origin not in ("manual", "product_suffix") or not isinstance(
                m.po_definition_fingerprint, str
            ):
                raise ValueError("原価・PO率の取得元が不正です")
            if m.prize_cost is not None and not positive(m.prize_cost):
                raise ValueError("確認原価は正の金額で指定してください")
            if not isinstance(m.prize_cost_product, str):
                raise ValueError("確認原価の商品情報が不正")
            from .sales_analysis import validate_overlay

            validate_overlay(m.analysis)
            if m.reference_average is not None and m.reference_average <= 0:
                raise ValueError("基準アベレージは正の値で指定してください")
            from .core import quantity

            m.units, m.cases = quantity(m.units), quantity(m.cases)
            if m.average is not None and m.average < 0:
                raise ValueError("アベレージは0以上で指定してください")
            if type(m.ratio_from_excel) is not bool:
                raise ValueError("アベ比の取得元が不正")
            if m.installed_on is not None:
                installed = parse_date(m.installed_on)
                if installed is None:
                    raise ValueError("設置日が不正")
                m.installed_on = installed.isoformat()
            if (
                isinstance(m.font_size, bool)
                or not isinstance(m.font_size, (int, float))
                or not math.isfinite(m.font_size)
                or not 6 <= m.font_size <= 72
            ):
                raise ValueError("文字サイズは6〜72で指定してください")
            result.append(m)
    except (TypeError, ValueError) as exc:
        raise AppError(f"マシン配置を読み込めません：{exc}") from exc
    return result


def validate_state(state):
    from .instruction_output import validate_options

    if isinstance(state, dict):
        validate_options(state.get("instruction_print_options"))
    if (
        not isinstance(state, dict)
        or state.get("version") not in (1, 2, 3)
        or not isinstance(state.get("source", ""), str)
    ):
        raise AppError("配置JSONの形式が不正です。")
    from .theme import MODES

    if state.get("theme_mode", "system") not in MODES:
        raise AppError("テーマ設定が不正です。")
    state = deepcopy(state)
    if state["version"] == 1:
        for record in [*state.get("machines", []), *state.get("original", [])]:
            for key in ("po_rate", "cost_rate"):
                if record.get(key) is not None:
                    record[key] /= 100
        state["version"] = 2
    from .machine_matching import validate_references

    validate_references(state.get("number_references", []))
    validate_machines(state.get("machines"))
    validate_machines(state.get("original", []))
    if type(state.get("initialized", False)) is not bool:
        raise AppError("配置の初期化情報が不正です。")
    if type(state.get("show_methods", False)) is not bool:
        raise AppError("運営方法表示設定が不正です。")
    if type(state.get("snap", True)) is not bool:
        raise AppError("スナップ設定が不正です。")
    if state.get("display_mode", "all") not in ("compact", "product", "analysis", "all"):
        raise AppError("表示モードが不正です。")
    if state.get("reference_date") is not None and parse_date(state["reference_date"]) is None:
        raise AppError("基準日が不正です。")
    if type(state.get("follow_today", True)) is not bool:
        raise AppError("基準日の追従設定が不正です。")
    zoom = state.get("zoom", 100)
    if zoom is not None and (
        isinstance(zoom, bool) or not isinstance(zoom, (int, float)) or not 50 <= zoom <= 300
    ):
        raise AppError("表示倍率は50〜300%で指定してください。")
    if not isinstance(state.get("loaded_at", ""), str):
        raise AppError("読込日時が不正です。")
    from .floor_print import FloorPrintOptions

    FloorPrintOptions.restore(state.get("print_options"))
    from .floor_presentation import validate_presentation

    validate_presentation(state.get("presentation", {}))
    center = state.get("view_center")
    if center is not None and (
        not isinstance(center, list)
        or len(center) != 2
        or any(
            isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v)
            for v in center
        )
    ):
        raise AppError("表示位置が不正です。")
    presets = state.get("card_presets", {})
    if not isinstance(presets, dict) or len(presets) > 50:
        raise AppError("カードプリセットが不正です。")
    for name, settings in presets.items():
        if (
            not isinstance(name, str)
            or not isinstance(settings, list)
            or len(settings) != 3
            or any(
                isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v)
                for v in settings
            )
            or not (
                80 <= settings[0] <= 2000 and 60 <= settings[1] <= 2000 and 6 <= settings[2] <= 72
            )
        ):
            raise AppError("カードプリセットのサイズが不正です。")
    from .performance_history import validate_history

    validate_history(state.get("performance_history"))
    from .analysis_settings import validate_settings

    validate_settings(state.get("analysis_settings"))
    from .analytics_data import validate_ui_state

    validate_ui_state(state.get("analytics_ui"))
    from .candidate_page import validate_candidate_state

    validate_candidate_state(state.get("candidate_ui"))
    from .sales_analysis import validate_snapshot

    validate_snapshot(state.get("sales_analysis"))
    return state


def block_metrics(ws, cell, epoch):
    result = {}
    for row in range(cell.row + 1, min(ws.max_row, cell.row + 4) + 1):
        raw = ws.cell(row, cell.column).value
        if not isinstance(raw, str):
            continue
        label = unicodedata.normalize("NFKC", raw).replace(" ", "").strip()
        first, second = ws.cell(row, cell.column + 1), ws.cell(row, cell.column + 2)
        if label in ("売上/アベレージ", "売上／アベレージ"):
            result.update(sales=numeric(first.value), average=numeric(second.value))
        elif label in ("PO率/アベ比", "PO率／アベ比"):
            result.update(
                po_rate=ratio_value(first.value),
                ratio=percent(second.value, second.number_format),
                ratio_from_excel=second.value is not None and str(second.value).strip() != "",
            )
        elif label in ("設置日/設定2", "設置日／設定2", "設置日", "投入日"):
            value = parse_date(first.value, epoch)
            result["installed_on"] = value.isoformat() if value else None
        elif label in ("メーカー", "運営方法", "入数", "CS数", "獲得個数制限", "PO率"):
            key = {
                "メーカー": "maker",
                "運営方法": "operation_method",
                "入数": "units",
                "CS数": "cases",
                "獲得個数制限": "limit",
                "PO率": "po_rate",
            }[label]
            if key == "po_rate":
                result[key] = ratio_value(first.value)
            elif key in ("units", "cases"):
                result[key] = numeric(first.value) if numeric(first.value) is not None else ""
            else:
                result[key] = str(first.value).strip() if first.value is not None else ""
        elif label in (
            "平均売上",
            "売上金額",
            "稼働日数",
            "粗利益",
            "原価率",
            "P/O率",
            "払出数",
            "個当たり",
            "景品単価",
            "原価",
        ):
            key = {
                "平均売上": "average",
                "売上金額": "sales",
                "稼働日数": "operating_days",
                "粗利益": "gross_profit",
                "原価率": "cost_rate",
                "P/O率": "po_rate",
                "払出数": "dispensed",
                "個当たり": "per_item",
                "景品単価": "unit_price",
                "原価": "cost",
            }[label]
            result[key] = (
                ratio_value(first.value)
                if key in ("po_rate", "cost_rate")
                else numeric(first.value)
            )
        elif label in ("売上", "アベレージ", "アベ比", "アベ比(%)"):
            key = {
                "売上": "sales",
                "アベレージ": "average",
                "アベ比": "ratio",
                "アベ比(%)": "ratio",
            }[label]
            result[key] = (
                percent(first.value, first.number_format)
                if key == "ratio"
                else numeric(first.value)
            )
            if key == "ratio":
                result["ratio_from_excel"] = (
                    first.value is not None and str(first.value).strip() != ""
                )
    if result.get("average") is not None and result["average"] < 0:
        result["average"] = None
    return result


def import_layout(path, with_references=False):
    wb = load_workbook(path, data_only=True, read_only=False)
    try:
        if "投入中レイアウト" not in wb.sheetnames:
            raise AppError("「投入中レイアウト」シートがありません。")
        ws = wb["投入中レイアウト"]
        from .machine_matching import (
            read_reference_rows,
            reference_index,
            normalize_reference,
            normalize_number,
        )

        references = read_reference_rows(wb)
        mapping, problems = reference_index(references)
        warnings = [f"{key}：{message}" for key, message in problems.items()]
        reference_cells = [c for row in ws for c in row if normalize_reference(c.value) is not None]

        def in_reference_card(cell):
            return any(
                c.row <= cell.row <= c.row + 4 and c.column <= cell.column <= c.column + 2
                for c in reference_cells
            )

        direct_cells = [
            c
            for row in ws
            for c in row
            if isinstance(c.value, str)
            and normalize_number(c.value) is not None
            and not in_reference_card(c)
        ]
        cells = sorted(reference_cells + direct_cells, key=lambda c: (c.row, c.column))
        if not cells:
            raise AppError("レイアウトに L_番号 / R_番号 のマシン枠が見つかりません。")
        if len(cells) > 5000:
            raise AppError("マシン枠が多すぎます（最大5000枠）。")
        widths = {}
        for dimension in ws.column_dimensions.values():
            for col in range(dimension.min or 1, (dimension.max or dimension.min or 1) + 1):
                widths[col] = 0 if dimension.hidden else ((dimension.width or 9) * 7 + 5)
        xs, ys = [0.0], [0.0]
        for col in range(1, ws.max_column + 4):
            xs.append(xs[-1] + widths.get(col, (ws.sheet_format.defaultColWidth or 9) * 7 + 5))
        for row in range(1, ws.max_row + 5):
            dim = ws.row_dimensions[row]
            ys.append(
                ys[-1]
                + (
                    0
                    if dim.hidden
                    else (dim.height or ws.sheet_format.defaultRowHeight or 15) * 4 / 3
                )
            )
        origin_x, origin_y = min(xs[c.column - 1] for c in cells), min(ys[c.row - 1] for c in cells)
        machines, seen = [], set()
        for cell in cells:
            key = normalize_reference(cell.value) or ""
            direct_raw = str(cell.value) if not key else ""
            if key:
                for r in range(cell.row + 1, min(ws.max_row, cell.row + 4) + 1):
                    label = unicodedata.normalize(
                        "NFKC", str(ws.cell(r, cell.column).value or "")
                    ).replace(" ", "")
                    if label in ("機械番号", "機器番号", "マシン番号"):
                        direct_raw = str(ws.cell(r, cell.column + 1).value or "")
                        break
            direct = normalize_number(direct_raw)
            number = (direct or "") if direct_raw else mapping.get(key, "")
            if number in seen:
                warnings.append(
                    f"{cell.coordinate}：管理番号 {number} が重複するため未設定にしました。"
                )
                number = ""
            if number:
                seen.add(number)
            product = ws.cell(cell.row, cell.column + 1).value
            machines.append(
                Machine(
                    number=number,
                    product=str(product) if product is not None else "",
                    source_key=key,
                    number_origin="direct" if direct_raw else "reference",
                    direct_number_raw=direct_raw,
                    reference_number=mapping.get(key, ""),
                    reference_warning=problems.get(key, ""),
                    layout_loaded_at=datetime.now().astimezone().isoformat(timespec="seconds"),
                    x=xs[cell.column - 1] - origin_x,
                    y=ys[cell.row - 1] - origin_y,
                    width=max(80, xs[cell.column + 2] - xs[cell.column - 1] - 8),
                    height=max(60, ys[cell.row + 3] - ys[cell.row - 1] - 8),
                    **block_metrics(ws, cell, wb.epoch),
                )
            )
        unknown = sum(not m.number for m in machines)
        if unknown:
            warnings.append(
                f"{unknown}枠は実マシン番号の対応がないため未設定です。参照IDから実番号を推測していません。"
            )
        from .sales_analysis import ensure_references

        ensure_references(machines)
        return (machines, warnings, references) if with_references else (machines, warnings)
    finally:
        wb.close()


def save_layout(path, state):
    state = validate_state(state)
    if state.get("source") and Path(path).resolve() == Path(state["source"]).resolve():
        raise AppError("元のExcelには上書きできません。")
    if Path(path).suffix.lower() != ".json":
        raise AppError("配置は.json形式で保存してください。")
    atomic_json(Path(path), state)
