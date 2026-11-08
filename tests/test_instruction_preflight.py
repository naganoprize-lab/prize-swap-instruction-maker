from datetime import date
from dataclasses import asdict
import pytest
from PySide6.QtCore import QDate
from prize_swap.core import Product
from prize_swap.floor_data import Machine
from prize_swap.instructions import prepare_entry
from prize_swap.instruction_validation import validate_instructions
from prize_swap.ui import Window

DAY = date(2026, 11, 8)


def test_duplicate_aliases_and_contradictory_instructions_are_booth_based():
    ms = [Machine(number="20088左", source_key="R_168"), Machine(number="20088右")]
    normal = [Product(DAY, "A", 40, 1, machines=["20088左", "20088右"])]
    assert not any(
        i.issue_type in ("duplicate_machine", "contradictory_instruction")
        for i in validate_instructions(normal, ms)
    )
    ps = [
        Product(DAY, "A", 40, 1, machines=["20088-01"]),
        Product(DAY, "B", 40, 1, machines=["R_168"]),
    ]
    issues = validate_instructions(ps, ms)
    assert any(i.issue_type == "contradictory_instruction" and not i.can_override for i in issues)
    ps[1].name = ""
    assert any(
        i.issue_type == "duplicate_machine" and i.severity == "warning"
        for i in validate_instructions(ps, ms)
    )


def test_blank_product_and_method_are_information_but_quantity_missing_is_warning():
    p = Product(DAY, "", machines=["20088左"], source_type="manual")
    ms = [Machine(number="20088左")]
    issues = validate_instructions([p], ms)
    assert not any(i.severity == "error" for i in issues)
    assert {"product_pending", "method_pending", "manual_product"} <= {
        i.issue_type for i in issues if i.severity == "info"
    }
    assert any(i.issue_type == "quantity_pending" and i.severity == "warning" for i in issues)
    p.units = -1
    assert any(
        i.issue_type == "invalid_units" and i.severity == "error"
        for i in validate_instructions([p], ms)
    )


def test_reference_contradictions_positions_plans_and_off_rows():
    ms = [Machine(number="20111右", source_key="L_66")]
    p = Product(DAY, "", machines=["L_66"], planned_booths={"橋渡し": 2})
    issues = validate_instructions(
        [p],
        ms,
        [{"source_key": "L_66", "number": "20111左"}, {"source_key": "L_66", "number": "20111右"}],
    )
    assert any(i.issue_type == "ambiguous_reference" and i.severity == "error" for i in issues)
    assert any(i.issue_type == "unassigned_booths" for i in issues)
    p.included = False
    assert not any(i.issue_type == "ambiguous_reference" for i in validate_instructions([p], ms))


def test_internal_id_does_not_hide_number_reference_mismatch():
    ms = [Machine(number="20088左"), Machine(number="20088右")]
    p = Product(DAY, "A", 40, 1, machines=["20088左"])
    prepare_entry(p)
    p.booth_assignments[0]["machine_key"] = ms[1].uid
    assert any(
        i.issue_type == "reference_mismatch" and i.severity == "error"
        for i in validate_instructions([p], ms)
    )


def test_embedded_check_stops_errors_and_requires_warning_acknowledgement(tmp_path, monkeypatch):
    w = Window(layout_storage=tmp_path / "floor.json")
    w.day.setDate(QDate(2026, 11, 8))
    w.products = [Product(DAY, "", machines=["20088左"])]
    w.floor.machines = [Machine(number="20088左")]
    w.floor.rebuild()
    w.refresh()
    path = tmp_path / "approved.pdf"
    monkeypatch.setattr(w, "save_path", lambda *_: str(path))
    try:
        w.export_pdf()
        assert not path.exists() and w.instruction_tabs.currentWidget() is w.preflight
        assert w.preflight.counts["warning"] and not w.preflight.counts["error"]
        w.preflight.continue_output()
        assert path.exists()
        w.products[0].units = -2
        w.refresh()
        path.unlink()
        w.export_pdf()
        assert not path.exists() and not w.preflight.continue_button.isEnabled()
        issue = next(i for i in w.preflight.issues if i.severity == "error")
        w.preflight.go_to_issue(issue)
        assert w.selected_products()[0].entry_id == w.products[0].entry_id
    finally:
        w.dirty = False
        w.instruction_save_timer.stop()
        w.floor.timer.stop()
        w.floor.day_timer.stop()
        w.preflight.timer.stop()
        w.close()
