from copy import deepcopy
from dataclasses import asdict
from datetime import date
from PySide6.QtCore import QDate, Qt, QItemSelectionModel
from PySide6.QtWidgets import QMessageBox, QDialog, QApplication
from prize_swap.core import Product, save_session, load_session
from prize_swap.ui import Window
from prize_swap.instruction_editor import InstructionDialog
from prize_swap.instructions import prepare_entry, mark_edited, merge_import, booths
from prize_swap.candidate_page import register_selected_candidates
from prize_swap.floor_data import Machine, validate_state
import pytest

DAY = date(2026, 11, 8)


@pytest.fixture
def window(tmp_path):
    w = Window(layout_storage=tmp_path / "auto.json")
    w.day.setDate(QDate(2026, 11, 8))
    w.show()
    yield w
    w.dirty = False
    w.instruction_save_timer.stop()
    w.floor.timer.stop()
    w.floor.day_timer.stop()
    w.close()


def test_crud_cell_edits_blank_manufacturer_notes_undo_and_order(window, monkeypatch):
    w = window

    def select_row(row):
        # QTest mouse events in earlier tests can leave Qt's Ctrl modifier set;
        # request an explicit single row rather than a modifier-sensitive toggle.
        w.table.selectionModel().select(
            w.proxy.index(row, 0),
            QItemSelectionModel.SelectionFlag.ClearAndSelect
            | QItemSelectionModel.SelectionFlag.Rows,
        )

    def create(d):
        d.machines.setPlainText("R_168")
        d.notes.setPlainText("投入商品検討中")
        d.accept()
        return d.result()

    monkeypatch.setattr(InstructionDialog, "exec", create)
    assert w.add_instruction() and len(w.products) == 1
    p = w.products[0]
    assert p.name == "" and p.included and p.source_type == "manual" and p.notes == "投入商品検討中"
    assert w.model.setData(w.model.index(0, 2), "手動メーカー")
    assert w.model.setData(w.model.index(0, 3), "入荷予定表にない商品")
    w.instruction_history.undo()
    assert w.products[0].name == ""
    w.instruction_history.redo()
    assert w.products[0].name == "入荷予定表にない商品"
    select_row(0)
    monkeypatch.setattr(QMessageBox, "question", lambda *_: QMessageBox.StandardButton.Yes)
    assert w.duplicate_instructions() and len(w.products) == 2
    assert len({p.entry_id for p in w.products}) == 2
    assert len({b.key for b in booths(w.visible)}) == 2
    select_row(1)
    w.move_products(-1)
    assert booths(w.visible)[0].product.entry_id == w.products[1].entry_id
    select_row(0)
    assert w.delete_instructions() and len(w.products) == 1
    w.instruction_history.undo()
    assert len(w.products) == 2


def test_manual_edits_survive_excel_refresh_with_explicit_field_choices():
    original = Product(DAY, "Excel景品", 40, 1, "メーカー", source_row=2)
    prepare_entry(original)
    original.name = "手動の品名"
    mark_edited(original, ["name"])
    fresh = Product(DAY, "Excel景品", 60, 2, "新メーカー", source_row=2)
    out, conflicts = merge_import([original], [fresh])
    assert len(out) == 1 and out[0].name == "手動の品名" and out[0].units == 60 and conflicts
    out, _ = merge_import(
        out, [fresh], policy="fields", field_choices={(original.entry_id, "name"): True}
    )
    assert out[0].name == "Excel景品"
    assert (
        merge_import(out, [Product(DAY, "別商品", source_row=3)])[0][0].entry_id
        == original.entry_id
    )


def test_autosave_restart_and_old_blank_data_migration(window, tmp_path):
    w = window
    w.products = [Product(DAY, "", machines=["R_168"], notes="未定メモ", source_type="manual")]
    w.refresh()
    w.layout_choice.setCurrentIndex(1)
    w.page_rows.setValue(12)
    w.print_mode.setCurrentIndex(w.print_mode.findData("compact"))
    w.autosave_instructions()
    other = Window(layout_storage=tmp_path / "auto.json")
    try:
        assert (
            len(other.products) == 1
            and other.products[0].name == ""
            and other.products[0].notes == "未定メモ"
        )
        assert other.products[0].entry_id == w.products[0].entry_id
        assert other.layout_name() == "row" and other.page_rows.value() == 12
        assert other.print_mode.currentData() == "compact"
    finally:
        other.dirty = False
        other.instruction_save_timer.stop()
        other.floor.timer.stop()
        other.floor.day_timer.stop()
        other.close()
    import json

    old = json.loads(w.instruction_autosave_path.read_text())
    for p in old["products"]:
        for k in (
            "entry_id",
            "booth_assignments",
            "source_type",
            "source_method",
            "notes",
            "management_numbers",
            "manually_edited_fields",
            "created_at",
            "updated_at",
            "import_snapshot",
        ):
            p.pop(k, None)
    path = tmp_path / "old.json"
    path.write_text(json.dumps(old))
    ps, _, _ = load_session(path)
    assert ps[0].entry_id and ps[0].name == "" and ps[0].included


def test_manual_candidates_do_not_change_assessments_or_selection(window):
    f = window.floor
    f.machines = [
        Machine(number="20088左", ratio=40, installed_on="2026-10-01"),
        Machine(source_key="R_168"),
    ]
    f.rebuild()
    ids = {m.uid for m in f.machines}
    f.selection_state.set(ids)
    before = deepcopy(f.analytics.records())
    assert register_selected_candidates(f) == 2 and register_selected_candidates(f) == 0
    assert f.selection_state.selected_machine_keys == ids
    assert [r["assessment"] for r in f.analytics.records()] == [r["assessment"] for r in before]
    assert len(validate_state(f.state())["candidate_ui"]["manual_registrations"]) == 2


def test_candidate_duplicate_uses_manual_management_and_reference_links(window, monkeypatch):
    from prize_swap.candidate_transfer import registered_ids, transfer

    w = window
    f = w.floor
    f.machines = [
        Machine(number="20088左", source_key="R_168"),
        Machine(number="20111右", source_key="L_66"),
    ]
    f.number_references = [{"source_key": "L_66", "number": "20111右"}]
    f.rebuild()
    w.products = [Product(DAY, "", machines=["R_168"]), Product(DAY, "", machines=["L_66"])]
    w.refresh()
    assert registered_ids(f) == {m.uid for m in f.machines}
    monkeypatch.setattr(QMessageBox, "question", lambda *_: QMessageBox.StandardButton.Yes)
    result = transfer(f, f.machines, w.candidate_page)
    assert result["added"] == 0 and result["skipped"] == 2
    assert len(w.products) == 2


def test_table_number_badges_normalize_legacy_number_aliases(window):
    w = window
    w.products = [Product(DAY, "", machines=["20088-01", "20088左", "R_168"])]
    w.refresh()
    display = w.model.data(w.model.index(0, 6), Qt.ItemDataRole.DisplayRole)
    assert display == "① 20088左、② R_168"
