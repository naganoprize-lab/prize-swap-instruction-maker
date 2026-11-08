from copy import deepcopy
from datetime import date
import json
import pytest
from openpyxl import Workbook
from PySide6.QtCore import QDate
from prize_swap.ui import Window
from prize_swap.floor_data import Machine
from prize_swap.excel_import_ui import AutoImportDialog
from prize_swap.excel_profiles import detect_workbook, make_profile
from prize_swap.excel_staging import prepare_import
from prize_swap.floor_status import ratio
from prize_swap.workflow_state import validate_workflow


def workbook(tmp_path, rows, title="実績", name="data.xlsx"):
    wb = Workbook()
    ws = wb.active
    ws.title = title
    for row in rows:
        ws.append(row)
    path = tmp_path / name
    wb.save(path)
    return path


@pytest.fixture
def window(tmp_path):
    w = Window(layout_storage=tmp_path / "floor.json")
    w.day.setDate(QDate(2026, 11, 8))
    yield w
    w.dirty = False
    for timer in (w.instruction_save_timer, w.floor.timer, w.floor.day_timer, w.preflight.timer):
        timer.stop()
    w.close()


def detected(path, kind):
    return next(
        c["profile"]
        for c in detect_workbook(path)["candidates"]
        if c["profile"]["file_type"] == kind
    )


def test_preview_cancel_and_apply_keep_layout_product_selection_and_actual_source(window, tmp_path):
    w = window
    m = Machine(
        number="20088左",
        source_key="L_66",
        product="現行商品 270",
        x=129,
        y=54,
        width=173,
        height=98,
        installed_on="2026-11-01",
        operation_method="橋渡し",
    )
    w.floor.machines = [m]
    w.floor.rebuild()
    w.floor.selection_state.set({m.uid})
    path = workbook(
        tmp_path,
        [
            ["集計期間：2026/11/06 ～ 2026/11/06"],
            ["売上金額", "機械番号", "商品名", "PO率", "原価率", "アベ比"],
            ["6,000円", "20088-01", "売上登録用", 0.05, 0.6, "85%"],
        ],
    )
    raw = path.read_bytes()
    before = w.floor.state()
    d = AutoImportDialog(w, path, "sales")
    assert d.prepared and d.prepared.count == 1 and d.apply_button.isEnabled()
    assert "認識件数：1件" in d.report.toPlainText()
    d.reject()
    assert w.floor.state() == before
    w.apply_prepared_import(d.prepared)
    current = next(v for v in w.floor.machines if v.uid == m.uid)
    assert (
        current.product,
        current.x,
        current.y,
        current.width,
        current.height,
        current.operation_method,
    ) == (m.product, 129, 54, 173, 98, "橋渡し")
    assert w.floor.selection_state.selected_machine_keys == {m.uid}
    assert current.analysis["values"]["sales"] == 6000 and ratio(current) == 85
    assert current.analysis["file_name"] == path.name and path.read_bytes() == raw
    state = validate_workflow(json.loads(json.dumps(w.workflow.state())))
    assert len(state["import_profiles"]) == 1 and state["recent"]["excel"]["file"] == str(
        path.resolve()
    )
    d.deleteLater()


def test_manual_column_profile_is_saved_and_recognized_on_renamed_sheet(window, tmp_path):
    w = window
    path = workbook(tmp_path, [["独自識別", "独自金額", "使わない列"], ["20088-02", 0, "備考"]])
    d = AutoImportDialog(w, path, "sales")
    assert d.prepared is None
    d.columns["number"].setCurrentIndex(d.columns["number"].findData(0))
    d.columns["sales"].setCurrentIndex(d.columns["sales"].findData(1))
    d.preview()
    assert d.prepared.count == 1 and "使わない列" in d.report.toPlainText()
    w.apply_prepared_import(d.prepared)
    other = workbook(
        tmp_path,
        [["独自識別", "独自金額", "使わない列"], ["20088-02", 120]],
        title="変更後",
        name="next.xlsx",
    )
    result = detect_workbook(other, w.workflow.data["import_profiles"])
    assert result["candidates"][0]["confidence"] == "保存済みの列対応"
    assert result["candidates"][0]["profile"]["sheet_name"] == "変更後"
    d.deleteLater()


def test_first_layout_import_loads_coordinates_and_subsequent_preserve_keeps_geometry(
    window, tmp_path
):
    w = window
    path = workbook(tmp_path, [["機械番号", "X座標", "Y座標"], ["20088-01", 100, 200]])
    d = AutoImportDialog(w, path, "layout")
    assert not d.preserve.isChecked()
    w.apply_prepared_import(d.prepared)  # The default preserve request is safe for an empty layout.
    assert len(w.floor.machines) == 1 and w.floor.machines[0].x == 100
    uid = w.floor.machines[0].uid
    w.floor.machines[0].x = 333
    w.apply_prepared_import(d.prepared, preserve=True)
    assert w.floor.machines[0].uid == uid and w.floor.machines[0].x == 333
    d.deleteLater()


def test_failed_staging_or_invalid_profile_preserves_current_data_and_undo(window, tmp_path):
    w = window
    w.floor.machines = [Machine(number="20088左", product="元商品")]
    w.floor.rebuild()
    before = w.floor.state()
    workflow = w.workflow.state()
    stacks = (w.instruction_history.count(), w.floor.history.count())
    path = workbook(tmp_path, [["機械番号", "X座標", "Y座標"], ["20088左", "座標不正", 20]])
    with pytest.raises(Exception):
        prepare_import(path, detected(path, "layout"), w.floor)
    path = workbook(tmp_path, [["機械番号", "売上"], ["20088左", 100]], name="valid.xlsx")
    prepared = prepare_import(path, detected(path, "sales"), w.floor)
    prepared.profile["column_mapping"]["sales"] = 0
    with pytest.raises(Exception):
        w.apply_prepared_import(prepared)
    assert w.floor.state() == before and w.workflow.state() == workflow
    assert stacks == (w.instruction_history.count(), w.floor.history.count())


def test_explicit_rate_units_unknown_fields_and_missing_rows(tmp_path):
    path = workbook(
        tmp_path,
        [
            ["機械番号", "売上", "PO率", "原価率", "景品種別", "独自項目"],
            ["20088-01", 0, 5, 60, "絶対に非表示", "保留"],
            ["20088-02", None, 4, 50, "非表示", None],
            ["L_99", 10, None, None, None, None],
            ["検索条件", 100, None, None, None, None],
        ],
    )
    p = detected(path, "sales")
    p["rate_units"] = {"po_rate": "percent", "cost_rate": "percent"}
    r = prepare_import(path, p)
    assert r.count == 1 and r.data["records"]["20088左"]["values"]["po_rate"] == 0.05
    assert r.data["records"]["20088左"]["values"]["cost_rate"] == 0.6
    assert len(r.data["errors"]) >= 1 and any(
        i["number"] == "L_99" for i in r.data["unmatched_rows"]
    )
    assert not any(i["number"] == "検索条件" for i in r.data["unmatched_rows"])
    assert "絶対に非表示" not in str(r) and any("独自項目" in c for c in r.unknown_columns)
