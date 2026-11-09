"""Real Qt integration: history is a reversible view, not a replacement master."""

from copy import deepcopy
from dataclasses import asdict
import time
import pytest
from PySide6.QtWidgets import QApplication, QDialogButtonBox
from PySide6.QtTest import QTest
from prize_swap.ui import Window
from prize_swap.floor_data import Machine, validate_state
from prize_swap.floor_status import effective_stat
from prize_swap.temporal_controller import period_range
from test_daily_reference import workbook


def wait_until(predicate, seconds=15):
    stop = time.monotonic() + seconds
    while not predicate() and time.monotonic() < stop:
        QApplication.processEvents()
        QTest.qWait(10)
    assert predicate()


@pytest.fixture
def historical_window(tmp_path):
    folder = tmp_path / "refs"
    folder.mkdir()
    workbook(folder / "one.xlsx", sales=100)
    workbook(folder / "two.xlsm", day="2026/11/07", sales=300)
    w = Window(layout_storage=tmp_path / "floor.json")
    f = w.floor
    f.machines = [
        Machine(source_key="R_66", product="現行商品 270", sales=999, x=345, y=234),
        Machine(source_key="L_100", product="未照合の現行商品", x=700, y=234),
    ]
    f.initialized = True
    f.rebuild()
    w.navigation.activate("floor")
    w.show()
    QApplication.processEvents()
    f.temporal.set_folder(folder)
    wait_until(lambda: not f.temporal._busy and not f.temporal.aggregate_busy)
    yield w
    f.temporal.to_live(save=False)
    w.dirty = False
    w.instruction_save_timer.stop()
    f.timer.stop()
    w.close()
    wait_until(lambda: not f.temporal._jobs)


def test_async_import_period_state_master_camera_selection_and_same_nodes(historical_window):
    w = historical_window
    f = w.floor
    c = f.temporal
    assert c.active and c.frame.start == "2026-11-07"
    assert len(c.docs) == 2 and "snapshot" not in c.docs[0]
    original = [asdict(m) for m in c.physical_machines]
    ids = {m.uid for m in f.machines}
    f.selection_state.set(ids)
    nodes = dict(f.nodes)
    f.view.scale(1.2, 1.2)
    transform = f.view.transform()
    assert c.apply("custom", "2026-11-06", "2026-11-08")
    assert effective_stat(f.machines[0], "sales") == 400
    assert effective_stat(f.machines[1], "sales") is None
    assert c.frame.coverage["missing"] == 1
    assert [asdict(m) for m in c.physical_machines] == original
    assert f.state()["machines"] == original and validate_state(f.state())
    assert f.selection_state.selected_machine_keys == ids and f.view.transform() == transform
    assert all(f.nodes[k] is v for k, v in nodes.items())
    assert all(
        p.start.date().toString("yyyy-MM-dd") == "2026-11-06" for p in w.period_controls.values()
    )
    assert c.to_live() and f.machines[0].product == "現行商品 270" and f.machines[0].sales == 999


def test_day_edit_undo_redo_saved_separately_and_period_readonly(historical_window):
    f = historical_window.floor
    c = f.temporal
    c.apply("day", "2026-11-06")
    f.open_details(f.machines[0].uid)
    p = f.detail_controller.panel
    p.name.setPlainText("確認した過去商品 200")
    p.confirmed_cost.setValue(200)
    f.detail_controller.save()
    assert f.machines[0].product == "確認した過去商品 200"
    assert c.physical_machines[0].product == "現行商品 270"
    f.history.undo()
    assert f.machines[0].product == "景品 100"
    f.history.redo()
    assert f.machines[0].product == "確認した過去商品 200"
    c.apply("day", "2026-11-07")
    assert f.machines[0].product == "景品 100"
    c.apply("custom", "2026-11-06", "2026-11-07")
    assert (
        not p.findChild(QDialogButtonBox).button(QDialogButtonBox.StandardButton.Save).isEnabled()
    )
    assert not p.name.isEnabled()
    c.to_live()
    assert p.name.isEnabled()


def test_playback_missing_monthly_speed_loop_and_pause(historical_window):
    w = historical_window
    c = w.floor.temporal
    assert c.prepare_playback("2026-11-06", "2026-11-08") == 2
    wait_until(lambda: not c.aggregate_busy)
    c.settings["skip_missing"] = False
    assert c.prepare_playback("2026-11-06", "2026-11-08") == 3
    c.seek(2)
    wait_until(lambda: not c.aggregate_busy)
    assert c.frame.start == "2026-11-08" and effective_stat(c.frame.machines[0], "sales") is None
    for speed in (0.5, 1.0, 2.0, 4.0):
        c.settings["speed"] = speed
        c.play()
        assert c.play_timer.interval() == round(1000 / speed)
        c.pause()
    c.settings["loop"] = True
    c.next_frame()
    wait_until(lambda: not c.aggregate_busy)
    assert c.frame_index == 0
    c.settings["play_mode"] = "monthly"
    assert c.prepare_playback("2026-11-06", "2026-12-08") == 2
    wait_until(lambda: not c.aggregate_busy)
    assert c.frame.start == "2026-11-01" and c.frame.end == "2026-11-30"
    c.play()
    w.floor.open_details(w.floor.machines[0].uid)
    assert not c.playing


def test_invalid_folder_and_cached_missing_folder_start_safely(tmp_path):
    from prize_swap.core import AppError

    w = Window(layout_storage=tmp_path / "floor.json")
    try:
        with pytest.raises(AppError):
            w.floor.temporal.set_folder(tmp_path / "missing")
        state = w.floor.state()
        state["temporal_settings"].update(
            folder=str(tmp_path / "missing"), mode="day", start="2026-11-06", end="2026-11-06"
        )
        w.floor.restore(state)
        QApplication.processEvents()
        assert "見つかりません" in w.daily_settings.status.text()
    finally:
        w.dirty = False
        w.close()


def test_japanese_calendar_period_presets():
    from datetime import date

    today = date(2026, 11, 9)
    assert period_range("7days", "2026-11-09", "", today) == ("2026-11-03", "2026-11-09")
    assert period_range("month", "", "", today) == ("2026-11-01", "2026-11-30")
    assert period_range("last_month", "", "", today) == ("2026-10-01", "2026-10-31")
