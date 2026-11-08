from __future__ import annotations

import json
import math
import re
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from pathlib import Path
from uuid import uuid4
from tempfile import NamedTemporaryFile

from openpyxl import load_workbook
from openpyxl.utils.datetime import from_excel

METHODS = ["橋渡し", "リング", "フック"]
LIMITS = ["制限なし"] + [f"{prefix}{n}" for prefix in ("各", "トータル") for n in "①②③④⑤⑥⑦⑧⑨⑩"]
HEADERS = ["稼働予定日", "品名", "入数", "CS数", "メーカー"]


class AppError(ValueError):
    pass


def split_values(value: str) -> list[str]:
    return list(dict.fromkeys(s.strip() for s in re.split(r"[,、，\r\n]+", value) if s.strip()))


def machine_numbers(value: str) -> list[str]:
    import unicodedata
    from .machine_matching import normalize_number, normalize_reference
    raw=split_values(unicodedata.normalize("NFKC",value).replace(";", "\n").replace("；", "\n"))
    result=list(dict.fromkeys(normalize_number(v,allow_bare=True) or normalize_reference(v) or v for v in raw))
    if any(not re.fullmatch(r"(?:[0-9]+[左右]?|[LR]_\d+)", s) for s in result):
        raise AppError("マシン番号は数字と左右、または管理ナンバーで入力してください（例：20085右、L_66）。")
    return result


def parse_date(value, epoch=None) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool) and epoch:
        try:
            converted = from_excel(value, epoch)
            return converted.date() if isinstance(converted, datetime) else None
        except (ValueError, OverflowError):
            return None
    if isinstance(value, str):
        for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y年%m月%d日", "%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S"):
            try:
                return datetime.strptime(value.strip(), fmt).date()
            except ValueError:
                pass
        try:
            return datetime.fromisoformat(value.strip()).date()
        except ValueError:
            pass
    return None


def quantity(value):
    if value is None or value == "":
        return ""
    if isinstance(value, bool):
        raise AppError("入数・CS数には0以上の数値を入力してください。")
    try:
        number = float(value)
    except (ValueError, TypeError):
        raise AppError("入数・CS数には0以上の数値を入力してください。") from None
    if not math.isfinite(number) or number < 0:
        raise AppError("入数・CS数には0以上の数値を入力してください。")
    return int(number) if number.is_integer() else number


@dataclass
class Product:
    day: date
    name: str
    units: int | float | str = ""
    cases: int | float | str = ""
    maker: str = ""
    machines: list[str] = field(default_factory=list)
    methods: list[str] = field(default_factory=list)
    priority: bool = False
    limit: str = "制限なし"
    included: bool = True
    order: int = 0
    source_row: int = 0
    planned_booths: dict[str, int | None] = field(default_factory=dict)
    machine_methods: dict[str, str | None] = field(default_factory=dict)
    retired_machine_methods: dict[str, str | None] = field(default_factory=dict)

    candidate_assignment: dict | None = None
    entry_id: str = field(default_factory=lambda: uuid4().hex)
    source_type: str = "auto"
    source_method: str = ""
    notes: str = ""
    management_numbers: list[str] = field(default_factory=list)
    manually_edited_fields: list[str] = field(default_factory=list)
    booth_assignments: list[dict] = field(default_factory=list)
    import_snapshot: dict = field(default_factory=dict)
    created_at: str = ""
    updated_at: str = ""

    @property
    def method_counts(self):
        return self.planned_booths

    @method_counts.setter
    def method_counts(self, value):
        self.planned_booths = value

    @property
    def assigned_machine_count(self):
        values = self.machines if isinstance(self.machines, str) else "\n".join(self.machines)
        return len(machine_numbers(values) or self.management_numbers)


def read_products(path: str | Path) -> tuple[list[Product], list[str]]:
    path = Path(path)
    if path.suffix.lower() not in (".xlsx", ".xlsm"):
        raise AppError("xlsx または xlsm ファイルを選択してください。")
    try:
        wb = load_workbook(path, data_only=True, read_only=True)
    except Exception as exc:
        raise AppError(f"Excelを開けません。ファイル形式・アクセス権を確認してください。\n{exc}") from exc
    try:
        if "縦" not in wb.sheetnames:
            raise AppError("「縦」シートがありません。")
        ws = wb["縦"]
        rows = ws.iter_rows(min_col=1, max_col=5, values_only=True)
        header = next(rows, ())
        if [str(v).strip() if v is not None else "" for v in header] != HEADERS:
            raise AppError("「縦」のA〜E列の見出しは「稼働予定日、品名、入数、CS数、メーカー」が必要です。")
        products, warnings, seen = [], [], set()
        for row_num, row in enumerate(rows, 2):
            if all(v is None for v in row):
                continue
            day = parse_date(row[0], wb.epoch)
            name = str(row[1]).strip() if row[1] is not None else ""
            if day is None or not name:
                warnings.append(f"{row_num}行：日付または品名が不正・空欄のため除外しました。")
                continue
            amounts = []
            for col, raw in zip(("入数", "CS数"), row[2:4]):
                try:
                    amounts.append(quantity(raw))
                except AppError:
                    amounts.append("")
                    warnings.append(f"{row_num}行：{col}が不正なため空欄にしました。")
            maker = str(row[4]).strip() if row[4] is not None else ""
            key = (day, name, *amounts, maker)
            if key in seen:
                warnings.append(f"{row_num}行：同一内容の重複商品を保持しました。必要に応じて出力対象を外してください。")
            seen.add(key)
            products.append(Product(day, name, *amounts, maker, order=len(products) + 1, source_row=row_num))
        return products, warnings
    finally:
        wb.close()


def for_day(products: list[Product], day: date) -> list[Product]:
    return [p for p in products if p.day == day]


def ordered(products: list[Product]) -> list[Product]:
    return sorted((p for p in products if p.included), key=lambda p: p.order)


def atomic_json(path: Path, payload: dict):
    temp = None
    try:
        with NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, suffix=".tmp", delete=False) as f:
            temp = Path(f.name)
            json.dump(payload, f, ensure_ascii=False, indent=2, allow_nan=False)
        backup_json(path)
        temp.replace(path)
    finally:
        if temp and temp.exists():
            temp.unlink()


def save_session(path, products, selected_day, source, layout="column", rows_per_page=20, floor_layout=None, label_state=None, print_mode="auto", workflow_state=None):
    if source and Path(path).resolve() == Path(source).resolve():
        raise AppError("入力Excelと同じ場所には保存できません。")
    from .instructions import prepare_entry
    for p in products: prepare_entry(p)
    records = [{**asdict(p), "day": p.day.isoformat(), "assigned_machine_count": p.assigned_machine_count} for p in products]
    data = dict(version=1, products=records, selected_day=selected_day.isoformat(), source=str(source or ""), layout=layout, rows_per_page=rows_per_page)
    if workflow_state is not None:
        from .workflow_state import validate_workflow
        data['workflow_state']=validate_workflow(workflow_state)
    data["print_mode"] = print_mode
    if floor_layout is not None:
        data["floor_layout"] = floor_layout
    if label_state is not None:
        data["label_printing"] = label_state
    atomic_json(Path(path), data)


def load_session(path):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if data["version"] != 1 or data.get("layout", "column") not in ("column", "row"):
            raise ValueError("未対応の形式")
        if data.get("print_mode","auto") not in ("auto","standard","compact"):
            raise ValueError("印刷モードが不正")
        from .workflow_state import validate_workflow
        validate_workflow(data.get('workflow_state'))
        selected = date.fromisoformat(data["selected_day"])
        page_rows = data.get("rows_per_page", 20)
        if type(page_rows) is not int or not 1 <= page_rows <= 50:
            raise ValueError("ページ行数が不正")
        products = []
        for record in data["products"]:
            record = dict(record)
            record["day"] = date.fromisoformat(record["day"])
            record.pop("assigned_machine_count", None)  # Always derive this from the IDs.
            if "method_counts" in record:
                record.setdefault("planned_booths", record.pop("method_counts"))
            p = Product(**record)
            from .candidate_transfer import validate_assignment
            p.candidate_assignment=validate_assignment(p.candidate_assignment)
            if not isinstance(p.name, str) or not isinstance(p.maker, str):
                raise ValueError("品名・メーカーが不正")
            p.units, p.cases = quantity(p.units), quantity(p.cases)
            if not isinstance(p.machines, list) or any(not isinstance(x, str) for x in p.machines):
                raise ValueError("マシン番号が不正")
            p.machines = machine_numbers("\n".join(p.machines))
            from .methods import legacy_names, validate_counts, validate_assignments, sync_machine_methods
            p.methods = legacy_names(p.methods)
            p.planned_booths = validate_counts(p.planned_booths)
            p.machine_methods = validate_assignments(p.machine_methods)
            p.retired_machine_methods = validate_assignments(p.retired_machine_methods)
            if p.machine_methods or p.retired_machine_methods:
                sync_machine_methods(p)
            if p.limit not in LIMITS or type(p.order) is not int or type(p.priority) is not bool or type(p.included) is not bool:
                raise ValueError("商品設定が不正")
            from .instructions import prepare_entry
            prepare_entry(p)
            products.append(p)
        if len({p.entry_id for p in products})!=len(products):
            raise ValueError("指示IDが重複しています")
        links=[link["assignment_id"] for p in products for link in p.booth_assignments]
        if len(set(links))!=len(links):raise ValueError("ブース割当IDが重複しています")
        return products, selected, data
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        raise AppError(f"一時保存ファイルの形式が不正です：{exc}") from exc


def backup_json(path):
    """Keep the previous user data before replacement; never touch input Excel."""
    from pathlib import Path
    import shutil
    path=Path(path)
    if path.exists():
        backup=path.with_name(path.name+".bak")
        temporary=backup.with_name(backup.name+".tmp")
        shutil.copyfile(path,temporary)
        temporary.replace(backup)
