"""Floor-plan persistence and read-only import of cell-based Excel layouts."""
from dataclasses import dataclass, field
from pathlib import Path
import math
import re
from uuid import uuid4

from openpyxl import load_workbook
from .core import AppError, atomic_json, machine_numbers


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


def validate_machines(records):
    if not isinstance(records, list) or len(records) > 5000:
        raise AppError("マシン配置データが不正です（最大5000枠）。")
    result, numbers, ids = [], set(), set()
    try:
        for record in records:
            m = Machine(**record)
            if any(not isinstance(getattr(m, key), str) for key in ("number", "kind", "product", "source_key", "uid")):
                raise ValueError("文字列項目が不正")
            if not m.uid or m.uid in ids:
                raise ValueError("マシンIDが重複")
            ids.add(m.uid)
            if m.number:
                if machine_numbers(m.number) != [m.number] or m.number in numbers:
                    raise ValueError("マシン番号が不正または重複")
                numbers.add(m.number)
            for key in ("x", "y", "width", "height"):
                value = getattr(m, key)
                if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value) or not 0 <= value <= 100000:
                    raise ValueError("位置・サイズが不正")
            if m.width < 80 or m.height < 60:
                raise ValueError("サイズが小さすぎます")
            result.append(m)
    except (TypeError, ValueError) as exc:
        raise AppError(f"マシン配置を読み込めません：{exc}") from exc
    return result


def validate_state(state):
    if not isinstance(state, dict) or state.get("version") != 1 or not isinstance(state.get("source", ""), str):
        raise AppError("配置JSONの形式が不正です。")
    validate_machines(state.get("machines"))
    validate_machines(state.get("original", []))
    if type(state.get("initialized", False)) is not bool:
        raise AppError("配置の初期化情報が不正です。")
    if type(state.get("show_methods",False)) is not bool:
        raise AppError("運営方法表示設定が不正です。")
    if type(state.get("snap", True)) is not bool:
        raise AppError("スナップ設定が不正です。")
    return state


def import_layout(path):
    wb = load_workbook(path, data_only=True, read_only=False)
    try:
        if "投入中レイアウト" not in wb.sheetnames:
            raise AppError("「投入中レイアウト」シートがありません。")
        ws = wb["投入中レイアウト"]
        mapping, warnings = {}, []
        if "管理番号参考値" in wb.sheetnames:
            for key, number in wb["管理番号参考値"].iter_rows(min_col=1, max_col=2, values_only=True):
                if key is None or number is None:
                    continue
                try:
                    numbers = machine_numbers(str(number).strip())
                    if len(numbers) != 1:
                        raise AppError("番号が複数")
                    key = str(key).strip()
                    if key in mapping and mapping[key] != numbers[0]:
                        warnings.append(f"参照ID {key} の対応が競合しています。")
                        mapping[key] = ""
                    else:
                        mapping[key] = numbers[0]
                except AppError:
                    warnings.append(f"参照ID {key} の管理番号が不正です。")
        cells = [c for row in ws for c in row if isinstance(c.value, str) and re.fullmatch(r"[LR]_\d+", c.value.strip())]
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
            ys.append(ys[-1] + (0 if dim.hidden else (dim.height or ws.sheet_format.defaultRowHeight or 15) * 4 / 3))
        origin_x, origin_y = min(xs[c.column - 1] for c in cells), min(ys[c.row - 1] for c in cells)
        machines, seen = [], set()
        for cell in cells:
            key = cell.value.strip()
            number = mapping.get(key, "")
            if number in seen:
                warnings.append(f"{cell.coordinate}：管理番号 {number} が重複するため未設定にしました。")
                number = ""
            if number:
                seen.add(number)
            product = ws.cell(cell.row, cell.column + 1).value
            machines.append(Machine(number=number, product=str(product) if product is not None else "", source_key=key,
                x=xs[cell.column - 1] - origin_x, y=ys[cell.row - 1] - origin_y,
                width=max(80, xs[cell.column + 2] - xs[cell.column - 1] - 8),
                height=max(60, ys[cell.row + 3] - ys[cell.row - 1] - 8)))
        unknown = sum(not m.number for m in machines)
        if unknown:
            warnings.append(f"{unknown}枠は実マシン番号の対応がないため未設定です。参照IDから実番号を推測していません。")
        return machines, warnings
    finally:
        wb.close()


def save_layout(path, state):
    validate_state(state)
    if state.get("source") and Path(path).resolve() == Path(state["source"]).resolve():
        raise AppError("元のExcelには上書きできません。")
    if Path(path).suffix.lower() != ".json":
        raise AppError("配置は.json形式で保存してください。")
    atomic_json(Path(path), state)
