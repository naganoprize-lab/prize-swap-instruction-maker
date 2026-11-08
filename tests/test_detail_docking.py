from dataclasses import asdict
from copy import deepcopy
import pytest
from PySide6.QtCore import Qt, QDate
from PySide6.QtWidgets import QApplication, QMessageBox, QMainWindow
from PySide6.QtTest import QTest
from prize_swap.ui import Window
from prize_swap.floor_data import Machine, validate_state
from prize_swap.floor_details import MachineDetailPanel
from prize_swap.theme import manager


@pytest.fixture
def window(tmp_path):
    w = Window(layout_storage=tmp_path / "floor.json")
    w.floor.machines = [
        Machine(
            number=f"{20000+i}右",
            source_key=f"R_{i}",
            product=f"景品{i} 270",
            sales=6000,
            dispensed=11,
            cost_rate=0.4,
            po_rate=0.2,
            x=i * 250,
        )
        for i in range(3)
    ]
    w.floor.rebuild()
    w.navigation.activate("floor")
    w.show()
    QApplication.processEvents()
    yield w
    w.dirty = False
    w.instruction_save_timer.stop()
    w.floor.timer.stop()
    w.floor.day_timer.stop()
    if w.floor.detail_controller.panel:
        w.floor.detail_controller.panel.load_machine(w.floor.machines[0])
    w.close()


def test_right_half_left_float_redock_and_same_panel_keep_geometry_selection(window):
    w = window
    f = w.floor
    c = f.detail_controller
    before = [asdict(m) for m in f.machines]
    f.selection_state.set({m.uid for m in f.machines})
    f.open_details(f.machines[0].uid)
    QApplication.processEvents()
    panel = c.panel
    assert (
        isinstance(c.host, QMainWindow)
        and isinstance(panel, MachineDetailPanel)
        and not panel.isWindow()
    )
    assert c.host.dockWidgetArea(c.dock) == Qt.DockWidgetArea.RightDockWidgetArea
    assert 0.35 < c.dock.width() / c.host.width() < 0.65
    c.set_position("left")
    QApplication.processEvents()
    assert c.host.dockWidgetArea(c.dock) == Qt.DockWidgetArea.LeftDockWidgetArea
    c.host.resizeDocks([c.dock], [int(c.host.width() * 0.6)], Qt.Orientation.Horizontal)
    QApplication.processEvents()
    c.capture()
    assert f.detail_settings["ratio"] > 0.5
    c.set_position("floating")
    QApplication.processEvents()
    assert c.dock.isFloating() and c.dock.isWindow()
    c.set_position("right")
    QApplication.processEvents()
    assert not c.dock.isFloating()
    assert (
        f.open_details(f.machines[1].uid)
        and c.panel is panel
        and panel.name.toPlainText() == f.machines[1].product
    )
    assert (
        f.selection_state.selected_machine_keys == {m.uid for m in f.machines}
        and [asdict(m) for m in f.machines] == before
    )
    manager().set_mode("dark", persist=False)
    assert not c.dock.grab().isNull()
    manager().set_mode("light", persist=False)


def test_switch_protects_unsaved_edits_cancel_discard_and_save_with_undo(window, monkeypatch):
    w = window
    f = w.floor
    c = f.detail_controller
    first, second = f.machines[:2]
    f.open_details(first.uid)
    p = c.panel
    p.name.setPlainText("編集途中 300")
    monkeypatch.setattr(QMessageBox, "question", lambda *_: QMessageBox.StandardButton.Cancel)
    assert not f.open_details(second.uid) and p.original.uid == first.uid
    monkeypatch.setattr(QMessageBox, "question", lambda *_: QMessageBox.StandardButton.Discard)
    assert f.open_details(second.uid) and f.machines[0].product == first.product
    p.name.setPlainText("保存する景品 300")
    p.confirmed_cost.setValue(300)
    monkeypatch.setattr(QMessageBox, "question", lambda *_: QMessageBox.StandardButton.Save)
    assert f.open_details(first.uid)
    assert f.machines[1].product == "保存する景品 300" and f.machines[1].prize_cost == 300
    f.history.undo()
    assert f.machines[1].product == second.product
    f.history.redo()
    assert f.machines[1].product == "保存する景品 300"


def test_dock_preferences_roundtrip_and_restore_window_onscreen(window, tmp_path):
    w = window
    f = w.floor
    c = f.detail_controller
    f.open_details(f.machines[0].uid)
    QApplication.processEvents()
    c.set_position("floating")
    QApplication.processEvents()
    c.capture()
    state = f.state()
    state["detail_dock"]["geometry"] = [-50000, -50000, 650, 700]
    assert validate_state(state)
    f.restore(state)
    QApplication.processEvents()
    assert c.dock.isFloating() and not c.dock.isHidden()
    assert c.dock.screen().availableGeometry().intersects(c.dock.frameGeometry())
    saved = f.state()
    other = Window(layout_storage=tmp_path / "second.json")
    try:
        other.floor.restore(saved)
        other.navigation.activate("floor")
        other.show()
        QApplication.processEvents()
        oc = other.floor.detail_controller
        assert oc.panel.original.uid == c.panel.original.uid and oc.dock.isFloating()
        assert not oc.dock.isHidden()
    finally:
        other.dirty = False
        other.instruction_save_timer.stop()
        other.floor.timer.stop()
        other.floor.day_timer.stop()
        other.close()


def test_opening_existing_unassigned_instruction_does_not_create_dirty_edits(window):
    from prize_swap.core import Product

    w = window
    m = w.floor.machines[0]
    w.products = [Product(w.selected_day(), "入替予定", machines=[m.number])]
    w.refresh()
    w.floor.open_details(m.uid)
    p = w.floor.detail_controller.panel
    assert p.products and not p.is_dirty()
    assert w.floor.detail_controller.dock.close()


def test_target_changes_refresh_existing_panel_without_discarding_cost_draft(window):
    w = window
    f = w.floor
    c = f.detail_controller
    f.open_details(f.machines[0].uid)
    p = c.panel
    p.confirmed_cost.setValue(300)
    before = p.radar.axes[4]["score"]
    f.analysis_settings["cost_target"] = 0.5
    f.changed.emit()
    assert c.panel is p and p.confirmed_cost.value() == 300 and p.is_dirty()
    assert p.radar.axes[4]["score"] != before
    assert "50.0%" in p.po_summary.text()
    p.load_machine(f.machines[0])


def test_short_window_keeps_detail_save_controls_inside_the_dock(window):
    from PySide6.QtWidgets import QDialogButtonBox

    w = window
    w.resize(1000, 650)
    w.floor.open_details(w.floor.machines[0].uid)
    QTest.qWait(80)
    c = w.floor.detail_controller
    p = c.panel
    buttons = p.findChild(QDialogButtonBox)
    point = buttons.mapTo(c.host, buttons.rect().bottomRight())
    assert c.host.rect().contains(point)
    assert c.dock.height() <= c.host.height()


def test_fullscreen_dock_and_save_controls_fit_small_200_percent_monitor(tmp_path):
    import json, os, subprocess, sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    (tmp_path / "screen.json").write_text(
        json.dumps(
            {"screens": [{"name": "DPI200", "width": 1280, "height": 720, "logicalDpi": 96}]}
        )
    )
    output = tmp_path / "native-small"
    result = subprocess.run(
        [sys.executable, str(root / "main.py"), "--floor-visual-test", str(output)],
        cwd=tmp_path,
        env=os.environ
        | {"QT_QPA_PLATFORM": "offscreen:configfile=screen.json", "QT_SCALE_FACTOR": "2"},
        capture_output=True,
        text=True,
        timeout=60,
    )
    report = json.loads((output / "report.json").read_text(encoding="utf-8"))
    assert result.returncode == 0 and report["ok"], report.get("error", result.stderr)
    assert report["detail_geometry"]["controls_fit"] and report["detail_geometry"]["on_screen"]
    assert report["details_opened"] == 1
