from datetime import date, datetime, timezone, timedelta
from pathlib import Path
import pytest
from openpyxl import Workbook
from openpyxl.utils.datetime import to_excel
from prize_swap.reference_dates import business_date, date_metadata
from prize_swap.daily_reference import ReferenceLibrary, read_document
from prize_swap.sales_analysis import HEADERS


def workbook(path, day="2026/11/06", end=None, sales=100, layout=True):
    b = Workbook()
    s = b.active
    s.title = "景品運営分析表"
    for c, label in HEADERS.items():
        s.cell(1, c + 1, label)
    s.append(
        [
            None,
            "20111-02",
            "UFO9",
            None,
            "売上登録用",
            sales,
            1,
            sales,
            sales * 0.5,
            0.5,
            0.1,
            1,
            sales,
            None,
            100,
            sales * 0.5,
        ]
    )
    s["B5"] = "期間(自)"
    s["C5"] = day
    s["B6"] = "期間(至)"
    s["C6"] = end if end is not None else day
    if layout:
        l = b.create_sheet("投入中レイアウト")
        l["C9"] = "R_66"
        l["D9"] = "景品 100"
        l["C10"] = "売上/ｱﾍﾞﾚｰｼﾞ"
        l["D10"] = sales
        l["E10"] = sales
        l["C11"] = "PO率/ｱﾍﾞ比"
        l["D11"] = 0.1
        l["E11"] = 1.0
        l["E11"].number_format = "0.0%"
        l["C12"] = "設置日/設定2"
        l["D12"] = datetime(2026, 11, 1)
        r = b.create_sheet("管理番号参考値")
        r.append(["R_66", "20111右"])
    b.save(path)
    b.close()
    return path


@pytest.mark.parametrize(
    "value",
    [
        "2026/11/06",
        "2026/11/6",
        "2026-11-06",
        "2026.11.06",
        "2026年11月6日",
        "20261106",
        "11/06/2026",
        "2026/11/06 00:00",
        "2026-11-06 00:00:00",
        date(2026, 11, 6),
        datetime(2026, 11, 6),
        20261106,
    ],
)
def test_business_date_formats(value):
    assert business_date(value) == "2026-11-06"


def test_serial_local_timezone_invalid_and_reversed_internal_period(tmp_path):
    assert business_date(to_excel(datetime(2026, 11, 6)), serial=True) == "2026-11-06"
    assert (
        business_date(datetime(2026, 11, 6, 0, 10, tzinfo=timezone(timedelta(hours=9))))
        == "2026-11-06"
    )
    assert business_date("2026/02/30") is None
    p = workbook(tmp_path / "売上参照値_20261107.xlsx", datetime(2026, 11, 7), "2026/11/06")
    d = read_document(p)
    assert d["metadata"]["data_type"] == "unknown" and not d["metadata"]["start"]
    assert "開始日が終了日" in " ".join(d["warnings"])


def test_cache_unchanged_files_and_failure_retains_last_good_source(tmp_path):
    folder = tmp_path / "refs"
    folder.mkdir()
    p = workbook(folder / "売上参照値_20261106.xlsx")
    (folder / "~$ignore.xlsx").write_bytes(b"not excel")
    lib = ReferenceLibrary(tmp_path / "cache.sqlite3")
    assert lib.scan(folder)["parsed"] == 1
    assert lib.scan(folder)["cached"] == 1
    before = p.read_bytes()
    doc = lib.documents(folder)[0]
    p.write_bytes(b"broken")
    assert lib.scan(folder)["failed"] == 1
    restored = lib.documents(folder)[0]
    assert restored["id"] == doc["id"] and restored["read_error"]
    p.write_bytes(before)
    assert lib.scan(folder)["cached"] == 1 and not lib.documents(folder)[0]["read_error"]
    assert ReferenceLibrary(lib.path).documents(folder)[0]["id"] == doc["id"]


def test_dates_conflicts_content_clones_and_explicit_confirmation(tmp_path):
    a = workbook(tmp_path / "売上参照値_20261106.xlsx", sales=100)
    b = workbook(tmp_path / "売上参照値_20261107.xlsx", day="2026/11/7", sales=100)
    lib = ReferenceLibrary(tmp_path / "cache.sqlite3")
    lib.scan(tmp_path)
    docs = lib.documents(tmp_path)
    assert all(d["duplicate_content"] for d in docs)
    usable, warnings = lib.usable(tmp_path)
    assert len(usable) == 1 and warnings
    second = next(d for d in docs if d["name"] == b.name)
    lib.confirm(second["id"], "2026-11-07", "2026-11-07", "daily", independent=True)
    selected, coverage = lib.selection("2026-11-06", "2026-11-08", tmp_path)
    assert (
        len(selected) == 2
        and coverage["acquired"] == 2
        and coverage["missing"] == 1
        and coverage["partial"]
    )
    workbook(tmp_path / "alternate.xlsx", sales=999)
    lib.scan(tmp_path)
    usable, _ = lib.usable(tmp_path)
    assert not any(d["metadata"]["start"] == "2026-11-06" for d in usable)
    first = next(d for d in lib.documents(tmp_path) if d["name"] == a.name)
    lib.confirm(first["id"], "2026-11-06", "2026-11-06", "daily", True)
    assert len(lib.selection("2026-11-06", "2026-11-07", tmp_path)[0]) == 2


def test_cumulative_period_not_added_to_overlapping_daily_or_prorated(tmp_path):
    workbook(tmp_path / "one.xlsx", sales=100)
    workbook(tmp_path / "period.xlsx", day="2026/11/06", end="2026/11/08", sales=900)
    lib = ReferenceLibrary(tmp_path / "cache.sqlite3")
    lib.scan(tmp_path)
    docs, coverage = lib.selection("2026-11-06", "2026-11-08", tmp_path)
    assert (
        len(docs) == 1
        and coverage["acquired"] == 1
        and "重なる累計" in " ".join(coverage["warnings"])
    )
    docs, coverage = lib.selection("2026-11-07", "2026-11-08", tmp_path)
    assert not docs and "一部は利用しません" in " ".join(coverage["warnings"])


def test_filename_does_not_override_internal_date_and_sources_are_unchanged(tmp_path):
    p = workbook(tmp_path / "売上参照値_20261107.xlsx", day="2026/11/06")
    before = p.read_bytes()
    d = read_document(p)
    assert d["metadata"]["start"] == "2026-11-06" and d["metadata"]["source"] == "excel"
    assert "ファイル名とExcel" in " ".join(d["warnings"]) and p.read_bytes() == before
    assert d["layouts"][0]["number"] == "20111右"


def test_daily_folder_reuses_header_profiles_for_renamed_reordered_sheets(tmp_path):
    b = Workbook()
    s = b.active
    s.title = "毎日の実績"
    s.append(["商品名", "売上金額", "機械番号", "P/O率", "景品原価率", "払出数"])
    s.append(["景品 270", "6,000円", "20111-02", "20%", "49.5%", 11])
    s.append(["対象日", "2026/11/06"])
    l = b.create_sheet("配置マスター")
    l.append(["管理ナンバー", "機械番号", "商品名", "X座標", "Y座標"])
    l.append(["R_66", "20111右", "景品 270", 500, 300])
    p = tmp_path / "別名.xlsx"
    b.save(p)
    b.close()
    doc = read_document(p)
    assert doc["snapshot"]["records"]["20111右"]["values"]["sales"] == 6000
    assert doc["layouts"][0]["x"] == 500 and doc["metadata"]["start"] == "2026-11-06"
