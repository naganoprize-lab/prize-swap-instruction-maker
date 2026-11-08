"""User interactions and real instruction data, including missing machine numbers."""

from copy import deepcopy
from dataclasses import asdict
from datetime import date
import pytest
from PySide6.QtCore import Qt, QPoint, QPointF, QRectF, QDate
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication, QMessageBox
from PySide6.QtTest import QTest
from prize_swap.ui import Window
from prize_swap.floor_data import Machine
from prize_swap.candidate_transfer import transfer, registered_ids, display_number
from prize_swap.core import Product, save_session, load_session, machine_numbers
from prize_swap.radar import score_record, sales_score


@pytest.fixture
def window(tmp_path):
    w = Window(layout_storage=tmp_path / "auto.json")
    w.floor.follow_today = False
    w.day.setDate(QDate(2026, 11, 8))
    w.floor.machines = [
        Machine(
            number=f"{20000+i}右",
            source_key=f"L_{i}",
            product=f"現行商品{i} 270",
            sales=i * 100 if i < 5 else None,
            ratio=40 if i < 3 else 80,
            installed_on="2026-10-01",
            operation_method="橋渡し",
            x=40 + i % 3 * 220,
            y=40 + i // 3 * 170,
            width=180,
            height=140,
        )
        for i in range(6)
    ]
    w.floor.rebuild()
    w.show()
    w.tabs.setCurrentWidget(w.floor)
    w.floor.set_zoom(100)
    QApplication.processEvents()
    yield w
    w.dirty = False
    w.floor.timer.stop()
    w.floor.day_timer.stop()
    w.analysis_page.timer.stop()
    w.candidate_page.timer.stop()
    w.close()


def drag(view, a, b, mod=Qt.KeyboardModifier.NoModifier):
    QTest.mousePress(view.viewport(), Qt.MouseButton.LeftButton, mod, a)
    move = QMouseEvent(
        QMouseEvent.Type.MouseMove,
        QPointF(b),
        QPointF(view.viewport().mapToGlobal(b)),
        Qt.MouseButton.NoButton,
        Qt.MouseButton.LeftButton,
        mod,
    )
    QApplication.sendEvent(view.viewport(), move)
    QTest.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, mod, b)
    QApplication.processEvents()


def test_shared_range_ctrl_shift_and_individual_no_geometry_changes(window, monkeypatch):
    f = window.floor
    s = f.selection_state
    monkeypatch.setattr(f, "open_details", lambda *_: pytest.fail("選択では詳細を開かない"))
    before = [asdict(m) for m in f.machines]
    f.view.centerOn(350, 220)
    QApplication.processEvents()
    s.set_mode("range")
    a = f.view.mapFromScene(QPointF(39, 39))
    b = f.view.mapFromScene(QPointF(235, 185))
    drag(f.view, a, b)
    assert s.selected_machine_keys == {f.machines[0].uid}
    assert f.nodes[f.machines[0].uid].isSelected()
    a = f.view.mapFromScene(QPointF(259, 39))
    b = f.view.mapFromScene(QPointF(455, 185))
    drag(f.view, a, b, Qt.KeyboardModifier.ShiftModifier)
    assert s.selected_machine_keys == {m.uid for m in f.machines[:2]}
    s.set_mode("individual")
    p = f.view.mapFromScene(f.nodes[f.machines[0].uid].sceneBoundingRect().center())
    QTest.mouseClick(f.view.viewport(), Qt.MouseButton.LeftButton, pos=p)
    assert s.selected_machine_keys == {f.machines[1].uid}
    s.set_mode("normal")
    QTest.mouseClick(
        f.view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.ControlModifier, p
    )
    assert len(s.selected_machine_keys) == 2
    f.view.setFocus()
    QTest.keyClick(f.view, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
    assert len(s.selected_machine_keys) == 6
    QTest.keyClick(f.view, Qt.Key.Key_Escape)
    assert not s.selected_machine_keys
    assert [asdict(m) for m in f.machines] == before


def test_selection_scope_auto_manual_override_and_all_machine_score(window):
    f = window.floor
    p = window.analysis_page
    ids = {f.machines[0].uid, f.machines[-1].uid}
    f.selection_state.set(ids)
    window.navigation.activate("analysis")
    p.refresh()
    assert p.selection_scope.currentData() == "selection"
    assert {r["uid"] for r in p.records} == ids
    assert "データなし：1台" in p.selection_summary.text()
    assert sum(r["value"] for r in p.distributions.cards["sales_bin"][2].rows) == 2
    assert sum(int(p.legacy.table.item(i, 1).text()) for i in range(p.legacy.table.rowCount())) == 2
    assert len(f.visible_nodes()) == 6
    settings = f.analysis_settings
    peers = [score_record(m, f.reference_day(), settings) for m in f.machines]
    assert sales_score(peers[0], peers, settings)[0] == 0
    p.selection_scope.setCurrentIndex(0)
    p.refresh()
    assert len(p.records) == 6
    f.selection_state.set({f.machines[1].uid})
    p.refresh()
    assert len(p.records) == 6
    p.use_layout_selection(explicit=True)
    p.refresh()
    assert len(p.records) == 1
    f.selection_state.set([])
    p.refresh()
    assert len(p.records) == 6


def test_single_bulk_transfer_drafts_identity_unknown_save_restore_and_undo(
    window, tmp_path, monkeypatch
):
    f = window.floor
    f.machines[0].number = "20088-01"
    f.machines[1].number = ""
    f.machines[2].number = ""
    f.machines[2].source_key = ""
    f.rebuild()
    monkeypatch.setattr(QMessageBox, "question", lambda *_: QMessageBox.StandardButton.Yes)
    before = [asdict(m) for m in f.machines]
    result = transfer(f, f.machines[:3], window.candidate_page)
    assert result["added"] == 3 and result["missing_number"] == 2
    drafts = window.products
    assert [p.machines for p in drafts] == [["20088左"], ["L_1"], []]
    assert all(not p.name and not p.included and p.units == p.cases == "" for p in drafts)
    assert [p.candidate_assignment["current_product_name"] for p in drafts] == [
        m.product for m in f.machines[:3]
    ]
    assert all(p.candidate_assignment["confirmed_replacement_date"] is None for p in drafts)
    assert registered_ids(f) == {m.uid for m in f.machines[:3]}
    assert transfer(f, f.machines[:3], window.candidate_page)["skipped"] == 3
    assert [asdict(m) for m in f.machines] == before
    path = tmp_path / "drafts.json"
    save_session(path, window.products, window.selected_day(), "")
    restored, _, _ = load_session(path)
    assert [asdict(p) for p in restored] == [asdict(p) for p in drafts]
    f.history.undo()
    assert not window.products
    f.history.redo()
    assert len(window.products) == 3
    window.candidate_page.refresh()
    assert sum("追加済み" in window.candidate_page.table.item(i, 10).text() for i in range(6)) == 3
    window.tabs.setCurrentWidget(window.instruction_page)
    window.refresh()
    assert window.model.rowCount() == 3


def test_bulk_categories_confirmation_cancel_and_duplicates(window, monkeypatch):
    f = window.floor
    p = window.candidate_page
    monkeypatch.setattr(QMessageBox, "question", lambda *_: QMessageBox.StandardButton.No)
    assert p.add_to_instructions({"要確認"})["cancelled"] and not window.products
    questions = []
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *args: questions.append(args[2]) or QMessageBox.StandardButton.Yes,
    )
    a = p.add_to_instructions({"要確認"})
    assert a["added"] == 3
    b = p.add_to_instructions({"要確認", "入替検討"})
    assert b["added"] == 3 and b["skipped"] == 3
    assert "追加済み：3台" in questions[-1] and "新規追加：3台" in questions[-1]
    assert len(window.products) == 6
    assert "追加しました" in p.summary.text()


def test_reference_resolution_management_collision_and_alias_duplicate(window, monkeypatch):
    f = window.floor
    f.machines[0].number = ""
    f.machines[0].resolved_number = "20111右"
    assert display_number(f.machines[0]) == "20111右"
    assert machine_numbers("20088-01,20088左,L_66") == ["20088左", "L_66"]
    window.products = [Product(window.selected_day(), "入替商品", machines=["20001-02"])]
    assert f.machines[1].uid in registered_ids(f)
    f.machines[1].number = ""
    f.machines[2].number = ""
    f.machines[1].resolved_number = f.machines[2].resolved_number = ""
    f.machines[1].source_key = f.machines[2].source_key = "L_66"
    monkeypatch.setattr(QMessageBox, "question", lambda *_: QMessageBox.StandardButton.Yes)
    result = transfer(f, f.machines[1:3], window.candidate_page)
    assert result["added"] == 2
    assert (
        len(
            {
                p.candidate_assignment["machine_key"]
                for p in window.products
                if p.candidate_assignment
            }
        )
        == 2
    )


def test_range_after_zoom_scroll_center_rule_fullscreen_and_text_shortcuts(window):
    f = window.floor
    s = f.selection_state
    before = [(m.uid, m.x, m.y, m.width, m.height) for m in f.machines]
    s.set_mode("range")
    s.range_rule = "center"
    f.set_zoom(150)
    f.view.centerOn(400, 250)
    QApplication.processEvents()
    box = f.nodes[f.machines[1].uid].sceneBoundingRect()
    a = f.view.mapFromScene(box.topLeft() - QPointF(1, 1))
    b = f.view.mapFromScene(box.bottomRight() + QPointF(1, 1))
    drag(f.view, a, b)
    assert s.selected_machine_keys == {f.machines[1].uid}
    f.toggle_fullscreen()
    QTest.qWait(30)
    assert f._fullscreen_host is not None and f.view.scene().items()
    assert not f.view.grab().isNull()
    f.selection_state.set({m.uid for m in f.machines[:3]})
    f._fullscreen_host.close()
    QTest.qWait(30)
    assert len(s.selected_machine_keys) == 3
    assert [(m.uid, m.x, m.y, m.width, m.height) for m in f.machines] == before
    f.floor_search.setText("入力を選択")
    f.floor_search.setFocus()
    QTest.keyClick(f.floor_search, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
    assert f.floor_search.selectedText() == "入力を選択" and len(s.selected_machine_keys) == 3


def test_edited_draft_prints_new_product_and_management_id_only(window, tmp_path, monkeypatch):
    from prize_swap.printing import save_pdf
    from prize_swap.pdf_validation import normalized_pdf_text, require_searchable_pdf

    f = window.floor
    m = f.machines[0]
    m.number = ""
    m.resolved_number = ""
    m.source_key = "L_66"
    monkeypatch.setattr(QMessageBox, "question", lambda *_: QMessageBox.StandardButton.Yes)
    transfer(f, [m], window.candidate_page)
    window.refresh()
    assert window.model.setData(
        window.model.index(0, 3), "新しく投入する景品", Qt.ItemDataRole.EditRole
    )
    assert window.model.setData(
        window.model.index(0, 0), Qt.CheckState.Checked, Qt.ItemDataRole.CheckStateRole
    )
    p = window.products[0]
    assert p.candidate_assignment["replacement_product_name"] == "新しく投入する景品"
    path = tmp_path / "draft-print.pdf"
    save_pdf(path, [p], window.selected_day())
    text = normalized_pdf_text(
        "".join(page["text"] for page in require_searchable_pdf(path)["pages"])
    )
    assert normalized_pdf_text(p.name) in text and "L_66" in text
    assert normalized_pdf_text(m.product) not in text


def test_candidate_checkboxes_and_detail_use_the_same_selection_state(window):
    from prize_swap.floor_details import MachineDetailDialog

    f = window.floor
    p = window.candidate_page
    window.tabs.setCurrentWidget(p)
    p.refresh()
    QApplication.processEvents()
    for i in (0, 1):
        p.table.scrollToItem(p.table.item(i, 0))
        QApplication.processEvents()
        QTest.mouseClick(
            p.table.viewport(),
            Qt.MouseButton.LeftButton,
            pos=p.table.visualItemRect(p.table.item(i, 0)).center(),
        )
    assert len(f.selection_state.selected_machine_keys) == 2
    assert set(p.selected_uids()) == f.selection_state.selected_machine_keys
    assert sum(node.isSelected() for node in f.nodes.values()) == 2
    d = MachineDetailDialog(f, f.machines[-1])
    d.shared_selection.setChecked(True)
    assert f.machines[-1].uid in f.selection_state.selected_machine_keys
    f.selection_state.set([])
    assert not d.shared_selection.isChecked()
    d.reject()


def test_reload_with_new_uid_bridges_only_unique_confirmed_identity(window, monkeypatch):
    from uuid import uuid4

    f = window.floor
    monkeypatch.setattr(QMessageBox, "question", lambda *_: QMessageBox.StandardButton.Yes)
    transfer(f, [f.machines[0]], window.candidate_page)
    f.machines[0].uid = uuid4().hex
    assert f.machines[0].uid in registered_ids(f)
    f.machines[1].number = f.machines[0].number
    f.machines[1].resolved_number = f.machines[0].resolved_number
    assert f.machines[0].uid not in registered_ids(f)
