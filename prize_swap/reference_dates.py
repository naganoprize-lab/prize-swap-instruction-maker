"""Business dates stay local calendar dates; file times are never sales dates."""

from datetime import date, datetime
import re, unicodedata
from openpyxl.utils.datetime import from_excel, WINDOWS_EPOCH


def text(value):
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", str(value or "")))


def business_date(value, epoch=WINDOWS_EPOCH, serial=False):
    if isinstance(value, datetime):
        return value.date().isoformat()  # Preserve the displayed local day, not its UTC date.
    if isinstance(value, date):
        return value.isoformat()
    if type(value) in (int, float):
        if 19000101 <= value <= 21991231 and value == int(value):
            value = str(int(value))
        elif serial and 1 <= value <= 110000:
            try:
                return from_excel(value, epoch).date().isoformat()
            except (ValueError, TypeError, OverflowError, AttributeError):
                return None
        else:
            return None
    s = text(value)
    patterns = [
        r"^(\d{4})[/.年-](\d{1,2})[/.月-](\d{1,2})(?:日)?(?:T|\s|\d{2}:|$)",
        r"^(\d{4})(\d{2})(\d{2})$",
    ]
    # Explicit time suffixes are allowed; calendar dates are not shifted by timezone conversion.
    m = re.match(r"^(\d{4})[/.年-](\d{1,2})[/.月-](\d{1,2})(?:日)?(?=$|T|\d{2}:)", s)
    if not m:
        m = re.fullmatch(r"(\d{4})(\d{2})(\d{2})", s)
    if m:
        parts = tuple(map(int, m.groups()))
    else:
        m = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{4})(?=$|T|\d{2}:)", s)
        if not m:
            return None
        month, day, year = map(int, m.groups())
        parts = (year, month, day)  # M/D/YYYY as specified.
    try:
        return date(*parts).isoformat()
    except ValueError:
        return None


def filename_date(path):
    from pathlib import Path

    matches = re.findall(r"(?<!\d)(20\d{6})(?!\d)", Path(path).stem)
    ds = {business_date(v) for v in matches}
    ds.discard(None)
    return next(iter(ds)) if len(ds) == 1 else None


def date_metadata(workbook, path):
    starts, ends, days, spans = [], [], [], []
    evidence = []
    seen_period = False
    for sheet in workbook.worksheets:
        # Date metadata may be below hundreds of data rows; examine only filled cells.
        for row in sheet.iter_rows():
            for cell in row:
                v = cell.value
                if not isinstance(v, str):
                    continue
                label = text(v)
                if any(
                    k in label
                    for k in ("検索日時", "抽出日時", "作成日時", "更新日時", "設置日", "投入日")
                ):
                    continue
                is_start = label in (
                    "期間(自)",
                    "期間（自）",
                    "開始日",
                    "集計開始日",
                    "対象開始日",
                    "売上開始日",
                )
                is_end = label in (
                    "期間(至)",
                    "期間（至）",
                    "終了日",
                    "集計終了日",
                    "対象終了日",
                    "売上終了日",
                )
                is_day = label in ("対象日", "売上対象日", "営業日", "実績日", "売上日", "対象日付")
                if is_start or is_end or is_day:
                    seen_period |= is_start or is_end
                    found = []
                    for dr, dc in [(0, 1), (0, 2), (1, 0)]:
                        x = sheet.cell(cell.row + dr, cell.column + dc)
                        d = business_date(x.value, workbook.epoch, serial=True)
                        if d:
                            found.append(d)
                            evidence.append(f"{sheet.title}!{x.coordinate}：{d}")
                        if d:
                            break
                    (starts if is_start else ends if is_end else days).extend(found)
                if any(k in label for k in ("集計期間", "対象期間", "売上期間")):
                    dates = re.findall(r"\d{4}[/.年-]\d{1,2}[/.月-]\d{1,2}日?", label)
                    parsed = [business_date(x) for x in dates]
                    if len(parsed) == 2 and all(parsed):
                        spans.append(tuple(parsed))
                        evidence.append(f"{sheet.title}!{cell.coordinate}：{v}")
                    elif label.endswith(("集計期間", "対象期間", "売上期間")):
                        adjacent = [
                            business_date(
                                sheet.cell(cell.row, cell.column + i).value, workbook.epoch, True
                            )
                            for i in (1, 2)
                        ]
                        if all(adjacent):
                            spans.append(tuple(adjacent))
    warnings = []
    file_day = filename_date(path)
    choices = set(spans)
    if starts or ends:
        if len(set(starts)) == 1 and len(set(ends)) == 1:
            choices.add((starts[0], ends[0]))
        else:
            warnings.append("Excel内部の開始日・終了日が不足または矛盾しています。")
    if days:
        if len(set(days)) == 1:
            choices.add((days[0], days[0]))
        else:
            warnings.append("Excel内部に複数の対象日があります。")
    source = "excel"
    start = end = None
    if len(choices) == 1:
        start, end = next(iter(choices))
    elif len(choices) > 1:
        warnings.append("Excel内部の対象日・期間が一致しません。")
    elif not seen_period and not warnings and file_day:
        start = end = file_day
        source = "filename"
        warnings.append("Excel内の対象日がなく、ファイル名の日付を使用しています。")
    if start and end and start > end:
        warnings.append("開始日が終了日より後です。日付を確認してください。")
        start = end = None
    if file_day and start and (start != file_day or end != file_day):
        warnings.append("ファイル名とExcel内部の日付・期間が一致しません。Excel内部を優先します。")
    if not start or not end:
        warnings.append("日付未確定：時系列・期間集計から除外しています。")
    return dict(
        start=start,
        end=end,
        source=source if start else "unknown",
        file_day=file_day,
        data_type="daily" if start and start == end else "period" if start else "unknown",
        warnings=warnings,
        evidence=evidence,
    )
