from dataclasses import asdict
from datetime import date
from copy import deepcopy
import pytest
from PySide6.QtCore import Qt, QDate
from PySide6.QtWidgets import QApplication, QLineEdit, QMessageBox
from PySide6.QtTest import QTest
from prize_swap.ui import Window
from prize_swap.core import Product, load_session, AppError
from prize_swap.floor_data import Machine
from prize_swap.candidate_page import register_selected_candidates

DAY = date(2026, 11, 8)


@pytest.fixture
def window(tmp_path):
    w = Window(layout_storage=tmp_path / "floor.json")
    w.day.setDate(QDate(2026, 11, 8))
    w.products = [Product(DAY, "元の商品", 40, 1)]
    w.refresh()
    w.workflow.reset_watchers()
    w.navigation.activate("instructions")
    w.show()
    QApplication.processEvents()
    yield w
    w.dirty = False
    w.instruction_save_timer.stop()
    w.floor.timer.stop()
    w.floor.day_timer.stop()
    w.preflight.timer.stop()
    w.close()


def test_audit_is_persisted_separately_from_live_undo_and_redo(window, tmp_path, monkeypatch):
    w = window
    w.model.setData(w.model.index(0, 3), "新しい商品")
    assert len(w.workflow.data["audit_log"]) == 1 and "元の商品" in str(
        w.workflow.data["audit_log"][0]["before_value"]
    )
    w.instruction_history.undo()
    w.instruction_history.redo()
    assert [r["operation_type"] for r in w.workflow.data["audit_log"]] == ["edit", "undo", "redo"]
    path = tmp_path / "saved.json"
    monkeypatch.setattr(w, "save_path", lambda *_: str(path))
    w.save_json()
    assert len(load_session(path)[2]["workflow_state"]["audit_log"]) == 3
    w.autosave_instructions()
    other = Window(layout_storage=tmp_path / "floor.json")
    try:
        assert other.workflow.data["audit_log"] == w.workflow.data["audit_log"]
        assert not other.instruction_history.canUndo()
        assert other.workflow.data["recent"]["saved"]["file"] == str(path)
    finally:
        other.dirty = False
        other.instruction_save_timer.stop()
        other.floor.timer.stop()
        other.floor.day_timer.stop()
        other.close()


def test_candidate_manual_register_exclude_restore_are_single_undo_operations(window):
    w = window
    f = w.floor
    f.machines = [Machine(number="20088左"), Machine(source_key="R_168")]
    f.rebuild()
    f.selection_state.set(m.uid for m in f.machines)
    assert register_selected_candidates(f) == 2
    f.history.undo()
    assert not f.candidate_ui["manual_registrations"]
    f.history.redo()
    assert len(f.candidate_ui["manual_registrations"]) == 2
    assert w.candidate_page.set_exclusions("手動確認")
    assert len(f.candidate_ui["exclusions"]) == 2
    f.history.undo()
    assert not f.candidate_ui["exclusions"]
    assert f.selection_state.selected_machine_keys == {m.uid for m in f.machines}
    assert any("候補" in r["description"] for r in w.workflow.data["audit_log"])


def test_atomic_tsv_paste_is_one_undo_and_invalid_values_do_not_partially_apply(window):
    w = window
    w.table.setCurrentIndex(w.proxy.index(0, 3))
    QApplication.clipboard().setText("商品A\t50\t2\n商品B\t60\t3")
    w.shortcuts.paste_instructions()
    assert [(p.name, p.units, p.cases) for p in w.products] == [("商品A", 50, 2), ("商品B", 60, 3)]
    assert w.instruction_history.count() == 1
    w.instruction_history.undo()
    assert len(w.products) == 1 and w.products[0].name == "元の商品"
    w.instruction_history.redo()
    assert len(w.products) == 2
    w.table.setCurrentIndex(w.proxy.index(0, 3))
    before = [asdict(p) for p in w.products]
    QApplication.clipboard().setText("壊さない\t-5")
    with pytest.raises(AppError):
        w.shortcuts.paste_instructions()
    assert [asdict(p) for p in w.products] == before


def test_table_shortcuts_and_text_editing_do_not_run_twice(window, monkeypatch, tmp_path):
    w = window
    w.model.setData(w.model.index(0, 3), "変更後")
    w.table.setCurrentIndex(w.proxy.index(0, 0))
    w.table.setFocus()
    QApplication.processEvents()
    QTest.keyClick(w.table, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
    assert w.products[0].name == "元の商品"
    QTest.keyClick(
        w.table,
        Qt.Key.Key_Z,
        Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier,
    )
    assert w.products[0].name == "変更後"
    QTest.keyClick(w.table, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
    assert len(w.selected_products()) == 1
    QTest.keyClick(w.table, Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier)
    assert "変更後" in QApplication.clipboard().text()
    w.search.setText("文字入力")
    w.search.setFocus()
    QTest.keyClick(w.search, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
    assert w.search.selectedText() == "文字入力" and w.products[0].included
    w.search.clear()
    w.table.setFocus()
    called = []
    monkeypatch.setattr(w, "save_json", lambda: called.append("save"))
    QTest.keyClick(w.table, Qt.Key.Key_S, Qt.KeyboardModifier.ControlModifier)
    assert called == ["save"]
