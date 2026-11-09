from datetime import date
from dataclasses import asdict
import pytest
from PySide6.QtWidgets import QApplication
from prize_swap.temporal_analysis import time_series
from prize_swap.daily_reference import ReferenceLibrary
from prize_swap.floor_data import Machine
from prize_swap.analysis_settings import validate_settings
from test_daily_reference import workbook
from test_temporal_ui import historical_window, wait_until


def test_series_missing_gaps_month_total_and_selection_keep_global_population(tmp_path):
    folder = tmp_path / "refs"
    folder.mkdir()
    workbook(folder / "a.xlsx", sales=100)
    workbook(folder / "b.xlsx", day="2026/11/08", sales=300)
    db = tmp_path / "daily.sqlite3"
    ReferenceLibrary(db).scan(folder)
    machines = [Machine(source_key="R_66"), Machine(source_key="L_90")]
    result = time_series(db, folder, machines, "2026-11-06", "2026-11-08", validate_settings())
    assert [r["sales"] for r in result["daily"]] == [100, None, 300]
    assert [r["coverage"]["acquired"] for r in result["daily"]] == [1, 0, 1]
    assert result["monthly"][0]["sales"] == 400 and result["monthly"][0]["coverage"]["missing"] == 1
    assert result["daily"][0]["valid_sales"] == 1 and result["daily"][0]["target"] == 2
    selected = time_series(
        db, folder, machines, "2026-11-06", "2026-11-08", validate_settings(), {machines[1].uid}
    )
    assert all(r["sales"] is None and r["target"] == 1 for r in selected["daily"])
    assert selected["daily"][0]["sales_bins"] == {"不明": 1}
    assert sum(result["daily"][0]["sales_bins"].values()) == 2
    assert result["monthly"][0]["po_rate"] is None  # no fabricated play denominator


def test_embedded_trend_tiles_update_scope_and_click_date(historical_window):
    w = historical_window
    c = w.floor.temporal
    c.apply("custom", "2026-11-06", "2026-11-08")
    w.navigation.activate("analysis")
    w.analysis_page.workspace_tabs.setCurrentWidget(w.temporal_analysis)
    panel = w.temporal_analysis
    panel.refresh()
    wait_until(lambda: panel.data is not None)
    assert len(panel.charts) == 7 and panel.data["monthly"][0]["sales"] == 400
    panel.resize(680, 600)
    QApplication.processEvents()
    assert all(not chart.grab().isNull() for chart in panel.charts)
    uid = w.floor.machines[0].uid
    w.floor.selection_state.set({uid})
    w.analysis_page.selection_scope.setCurrentIndex(1)
    panel.refresh()
    old = panel.data
    wait_until(lambda: panel.data is not old)
    assert panel.data["daily"][0]["target"] == 1
    row = panel.data["daily"][0]
    panel.navigate((row, [uid]))
    wait_until(lambda: not c.aggregate_busy)
    assert c.frame.start == "2026-11-06" and w.floor.analysis_highlight_uids == {uid}


def test_cache_v1_migrates_to_summary_without_losing_raw_payload(tmp_path):
    import sqlite3, json

    folder = tmp_path / "refs"
    folder.mkdir()
    workbook(folder / "a.xlsx")
    db = tmp_path / "history.sqlite3"
    lib = ReferenceLibrary(db)
    lib.scan(folder)
    raw = lib.documents(folder)[0]
    with sqlite3.connect(db) as con:
        con.execute("ALTER TABLE documents DROP COLUMN summary")
        con.execute("PRAGMA user_version=1")
    reopened = ReferenceLibrary(db)
    index = reopened.documents(folder, full=False)[0]
    assert index["record_count"] == 1 and index["layout_count"] == 1 and "snapshot" not in index
    assert reopened.documents(folder)[0]["snapshot"] == raw["snapshot"]
