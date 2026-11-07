from copy import deepcopy
from dataclasses import asdict
from datetime import date
import json
import pytest
from prize_swap.core import AppError, atomic_json
from prize_swap.floor_data import Machine, save_layout, validate_state
from prize_swap.floor import FloorWidget
from prize_swap.performance_history import archive, identity, set_period, validate_history
from prize_swap.sales_analysis import load_analysis
from prize_swap.replacement import comparable_trend
from test_sales_analysis import report_file, data_row

DAY = date(2026, 11, 7)


def test_import_history_idempotence_context_and_restore(tmp_path):
    w = FloorWidget(lambda: [], lambda: DAY, tmp_path / "auto.json")
    other = None
    try:
        w.machines = [
            Machine(
                number="20088左",
                product="元商品",
                installed_on="2026-10-01",
                average=100,
                ratio=100,
            )
        ]
        w.rebuild()
        p = report_file(tmp_path / "分析.xlsx", [data_row()])
        w.import_analysis_path(p)
        assert len(w.performance_history) == 1
        context = deepcopy(w.analysis_state["records"]["20088左"]["product_context"])
        w.machines[0].product = "変更後商品"
        w.import_analysis_path(p)
        assert len(w.performance_history) == 1
        assert w.analysis_state["records"]["20088左"]["product_context"] == context
        state = w.state()
        save_layout(tmp_path / "保存.json", state)
        other = FloorWidget(lambda: [], lambda: DAY, tmp_path / "保存.json")
        assert other.state() == state
        # Soft-deleted history survives save/restore, without switching the current report.
        other.performance_history[0]["deleted"] = True
        assert validate_history(other.performance_history)[0]["deleted"]
        assert other.analysis_state["file_name"] == "分析.xlsx"
    finally:
        for view in (w, other):
            if view:
                view.timer.stop()
                view.day_timer.stop()
                view.close()


def test_manual_period_marking_archive_dedup_and_deleted_restore(tmp_path):
    snapshot = load_analysis(report_file(tmp_path / "x.xlsx", [data_row()]))
    manual = set_period(snapshot, "2026-11-01", "2026-11-06")
    assert manual["period_source"] == "manual" and snapshot["period_source"] == "unknown"
    entries = archive([], manual)
    assert archive(entries, manual) == entries
    entries[0]["deleted"] = True
    assert validate_history(entries)[0]["deleted"]
    entries[0]["deleted"] = False
    assert not validate_history(entries)[0]["deleted"]
    with pytest.raises(AppError):
        set_period(snapshot, "2026-11-06", "2026-11-01")
    with pytest.raises(AppError):
        validate_history(entries + entries)


def test_previous_json_backed_up_before_replacement(tmp_path):
    p = tmp_path / "work.json"
    atomic_json(p, {"old": 1})
    atomic_json(p, {"new": 2})
    assert json.loads(p.read_text()) == {"new": 2}
    assert json.loads(p.with_name("work.json.bak").read_text()) == {"old": 1}
    with pytest.raises(ValueError):
        atomic_json(p, {"bad": float("nan")})
    assert json.loads(p.read_text()) == {"new": 2} and json.loads(
        p.with_name("work.json.bak").read_text()
    ) == {"old": 1}


def test_old_floor_state_migrates_rates_and_retains_layout(tmp_path):
    m = Machine(number="20088左", product="旧保存", po_rate=8, cost_rate=60, x=111, y=222)
    old = dict(version=1, machines=[asdict(m)], source="", initialized=True)
    new = validate_state(old)
    assert (
        new["version"] == 2
        and new["machines"][0]["po_rate"] == 0.08
        and new["machines"][0]["cost_rate"] == 0.6
    )
    assert old["machines"][0]["po_rate"] == 8 and new["machines"][0]["x"] == 111
    w = FloorWidget(lambda: [], lambda: DAY, tmp_path / "auto.json")
    try:
        w.restore(old)
        assert w.machines[0].po_rate == 0.08 and w.machines[0].product == "旧保存"
        assert w.analysis_settings["sales_benchmark"] is None and w.performance_history == []
    finally:
        w.timer.stop()
        w.day_timer.stop()
        w.close()


def test_analysis_dialogs_and_settings_are_reviewable(tmp_path, monkeypatch):
    from PySide6.QtWidgets import QDialog, QTabWidget, QTableWidget, QPushButton
    from prize_swap.analysis_views import show_candidates, show_method_analysis
    from prize_swap.analysis_settings_ui import edit_analysis_settings
    from prize_swap.history_ui import show_history, manual_period

    w = FloorWidget(lambda: [], lambda: DAY, tmp_path / "auto.json")
    seen = []
    try:
        w.machines = [Machine(number="20088左", product="現在商品", operation_method="フック")]
        w.rebuild()
        w.import_analysis_path(report_file(tmp_path / "x.xlsx", [data_row()]))

        def observe(dialog):
            seen.append(dialog.windowTitle())
            assert dialog.layout() is not None
            return QDialog.DialogCode.Rejected

        monkeypatch.setattr(QDialog, "exec", observe)
        for call in (
            show_candidates,
            show_method_analysis,
            show_history,
            manual_period,
            edit_analysis_settings,
        ):
            call(w)
        assert len(seen) == 5
    finally:
        w.timer.stop()
        w.day_timer.stop()
        w.close()


def test_history_limit_failure_is_atomic_including_type_palette(tmp_path, monkeypatch):
    import prize_swap.performance_history as history
    from openpyxl import load_workbook

    w = FloorWidget(lambda: [], lambda: DAY, tmp_path / "auto.json")
    try:
        w.machines = [Machine(number="20088左")]
        w.rebuild()
        current = report_file(tmp_path / "old.xlsx", [data_row()])
        w.import_analysis_path(current)
        new = report_file(tmp_path / "new.xlsx", [data_row()])
        book = load_workbook(new)
        book.active["C2"] = "新しい機種"
        book.save(new)
        book.close()
        before = w.state()
        original = history.archive

        def limited(entries, snapshot):
            if snapshot["file_name"] == "new.xlsx":
                raise AppError("履歴上限")
            return original(entries, snapshot)

        monkeypatch.setattr(history, "archive", limited)
        with pytest.raises(AppError, match="上限"):
            w.import_analysis_path(new)
        assert w.state() == before
    finally:
        w.timer.stop()
        w.day_timer.stop()
        w.close()
