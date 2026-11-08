from datetime import date
from dataclasses import asdict
from hashlib import sha256
from pathlib import Path
import pytest
from openpyxl import Workbook
from prize_swap.excel_profiles import detect_workbook, make_profile, validate_profile
from prize_swap.excel_staging import prepare_import
from prize_swap.floor import FloorWidget
from prize_swap.floor_data import Machine


def make_file(tmp_path, title, rows, name="input.xlsx"):
    wb = Workbook()
    ws = wb.active
    ws.title = title
    for row in rows:
        ws.append(row)
    p = tmp_path / name
    wb.save(p)
    return p


def profile(path, kind):
    return next(
        c["profile"]
        for c in detect_workbook(path)["candidates"]
        if c["profile"]["file_type"] == kind
    )


def test_changed_sheet_order_alias_headers_money_percent_and_ancillary_rows(tmp_path):
    path = make_file(
        tmp_path,
        "今月の実績",
        [
            ["対象期間：2026/11/06 ～ 2026/11/06"],
            ["商品名", "売上", "機械番号", "PO率", "景品原価率", "払出数", "景品種別"],
            ["参考商品", "¥1,000", " ２００８８-０１ ", 0.05, 0.6, 2, "表示禁止種別"],
            ["検索条件", "条件説明", "検索日時", None, None, None, "非表示"],
        ],
    )
    before = path.read_bytes()
    p = profile(path, "sales")
    assert p["header_row"] == 2
    prepared = prepare_import(path, p)
    assert prepared.count == 1 and not prepared.errors
    snap = prepared.data
    r = snap["records"]["20088左"]
    assert (
        r["values"]["sales"] == 1000
        and r["values"]["po_rate"] == 0.05
        and r["values"]["cost_rate"] == 0.6
    )
    assert (
        r["row"] == 3
        and snap["period_start"] == "2026-11-06"
        and snap["source"] == str(path.resolve())
    )
    assert snap["fingerprint"] == sha256(before).hexdigest() and "表示禁止種別" not in str(
        asdict(prepared)
    )
    assert "景品種別" not in str(prepared.unknown_columns) and path.read_bytes() == before


def test_arrival_date_serial_changed_order_and_saved_manual_mapping(tmp_path):
    path = make_file(
        tmp_path,
        "入荷リスト",
        [
            ["メーカー", "入荷予定日", "商品名", "ケース数", "入数"],
            ["製造元", date(2026, 11, 8), "景品A", 2, 40],
        ],
    )
    result = prepare_import(path, profile(path, "arrivals"))
    assert (
        result.count == 1 and result.data[0].day == date(2026, 11, 8) and result.data[0].cases == 2
    )
    custom = make_file(
        tmp_path, "名前変更", [["独自ID", "金額データ"], ["20088-02", 2000]], "custom.xlsm"
    )
    assert not detect_workbook(custom)["candidates"]
    p = make_profile("sales", "名前変更", 1, ["独自ID", "金額データ"], {"number": 0, "sales": 1})
    recognized = detect_workbook(custom, [p])
    assert recognized["candidates"][0]["confidence"] == "保存済みの列対応"
    assert (
        prepare_import(custom, recognized["candidates"][0]["profile"]).data["records"]["20088右"][
            "values"
        ]["sales"]
        == 2000
    )


def test_reference_profile_and_explicit_management_to_sales_number(tmp_path):
    path = make_file(
        tmp_path, "対応リスト", [["機械番号", "管理ナンバー"], ["20111-02", "Ｌ＿６６"]]
    )
    result = prepare_import(path, profile(path, "references"))
    assert result.count == 1 and result.data[0]["source_key"] == "L_66"
    sales = make_file(tmp_path, "売上", [["管理番号", "売上金額"], ["L_66", 0]], "sales.xlsx")
    f = FloorWidget(lambda: [], lambda: date(2026, 11, 8), tmp_path / "floor.json")
    try:
        f.machines = [Machine(source_key="L_66", number="20111右")]
        f.number_references = result.data
        parsed = prepare_import(sales, profile(sales, "sales"), f)
        assert parsed.data["records"]["20111右"]["values"]["sales"] == 0
        assert parsed.data["records"]["20111右"]["source_management_number"] == "L_66"
    finally:
        f.timer.stop()
        f.day_timer.stop()
        f.close()


def test_table_layout_coordinates_and_invalid_data_never_guess_positions(tmp_path):
    path = make_file(
        tmp_path,
        "座標一覧",
        [
            ["商品名", "Y座標", "マシン番号", "X座標", "設置日"],
            ["景品A", 50, "20088-01", 100, date(2026, 11, 1)],
        ],
    )
    result = prepare_import(path, profile(path, "layout"))
    m = result.data[0][0]
    assert (m.number, m.x, m.y, m.installed_on) == ("20088左", 100, 50, "2026-11-01")
    bad = make_file(
        tmp_path,
        "座標一覧",
        [["商品名", "Y座標", "マシン番号", "X座標"], ["景品", 50, "20088-01", "不正"]],
        "bad.xlsx",
    )
    with pytest.raises(Exception, match="座標"):
        prepare_import(bad, profile(bad, "layout"))


def test_reference_ambiguity_and_missing_required_columns_are_not_auto_applied(tmp_path):
    p = make_file(
        tmp_path,
        "管理番号対応",
        [["管理番号", "機械番号"], ["L_66", "20111左"], ["L_66", "20111右"]],
    )
    prepared = prepare_import(p, profile(p, "references"))
    assert prepared.errors
    with pytest.raises(Exception, match="必須列"):
        validate_profile(make_profile("sales", "x", 1, ["商品"], {"product": 0}))
