from datetime import date
from copy import deepcopy
from dataclasses import asdict
import pytest
from PySide6.QtCore import QDate, Qt
from PySide6.QtWidgets import QApplication, QDialog, QInputDialog
from PySide6.QtPrintSupport import QPrintDialog, QPrintPreviewDialog
from PySide6.QtTest import QTest
from prize_swap.ui import Window
from prize_swap.floor_data import Machine
from prize_swap.core import Product
from prize_swap.pdf_validation import normalized_pdf_text, require_searchable_pdf

DAY = date(2026, 11, 8)


@pytest.fixture
def window(tmp_path):
    w = Window(layout_storage=tmp_path / "map-auto.json")
    w.day.setDate(QDate(2026, 11, 8))
    w.products = [
        Product(DAY, "", machines=["20088左", "R_168"], order=1),
        Product(DAY, "対象外", machines=["20089右"], included=False, order=2),
    ]
    w.floor.machines = [
        Machine(number="20088左", x=100, y=100),
        Machine(source_key="R_168", x=500, y=300),
        Machine(number="20089右", x=900, y=500),
    ]
    w.floor.rebuild()
    w.refresh()
    w.show()
    yield w
    w.dirty = False
    w.instruction_save_timer.stop()
    w.floor.timer.stop()
    w.floor.day_timer.stop()
    w.close()


def test_map_internal_only_targets_bidirectional_fit_and_no_coordinate_edits(window):
    w = window
    page = w.instruction_map
    before = [asdict(m) for m in w.floor.machines]
    assert w.instruction_tabs.tabText(0) == "指示書編集" and not page.isWindow()
    w.instruction_tabs.setCurrentWidget(page)
    QApplication.processEvents()
    assert len(page.items) == 2 and len(page.entries) == 2 and len(page.unresolved_rows) == 0
    w.table.selectRow(0)
    assert page.selected_entries == {w.products[0].entry_id}
    item = next(iter(page.items.values()))
    pos = page.view.mapFromScene(item.sceneBoundingRect().center())
    QTest.mouseClick(page.view.viewport(), Qt.MouseButton.LeftButton, pos=pos)
    assert w.selected_products() == [w.products[0]]
    page.all_entries.setChecked(True)
    assert len(page.items) == 3
    page.context.setChecked(True)
    assert len(page.entries) == 3
    assert [asdict(m) for m in w.floor.machines] == before
    assert not page.view.grab().isNull()


def test_unresolved_ambiguous_manual_fix_and_saved_settings(window, monkeypatch):
    w = window
    page = w.instruction_map
    w.products[0].machines = ["L_66"]
    w.products[0].booth_assignments = []
    w.floor.machines = [Machine(source_key="L_66", x=100), Machine(source_key="L_66", x=600)]
    w.floor.rebuild()
    w.refresh()
    assert len(page.items) == 0 and page.unresolved_rows[0].status == "照合要確認"
    monkeypatch.setattr(QInputDialog, "getItem", lambda *args, **kwargs: (args[3][1], True))
    page.resolve_position(0, 2)
    assert len(page.items) == 1 and next(iter(page.items)) == w.floor.machines[1].uid
    page.paper.setCurrentText("A3")
    page.orientation.setCurrentIndex(page.orientation.findData("portrait"))
    w.instruction_output_mode.setCurrentIndex(w.instruction_output_mode.findData("both"))
    state = w.floor.state()
    w.floor.restore(state)
    assert (
        page.paper.currentText() == "A3"
        and page.orientation.currentData() == "portrait"
        and w.instruction_output_mode.currentData() == "both"
    )


def test_map_pdf_direct_and_instruction_combined_use_same_page_renderer(
    window, tmp_path, monkeypatch
):
    w = window
    page = w.instruction_map
    page.paper.setCurrentText("A3")
    saved = tmp_path / "map-save.pdf"
    direct = tmp_path / "map-direct.pdf"
    monkeypatch.setattr(w, "save_path", lambda *args: str(saved))
    page.export_pdf()
    if w.preflight.pending:
        w.preflight.continue_output()

    def accept(dialog):
        target = dialog.printer()
        target.setOutputFormat(target.OutputFormat.PdfFormat)
        target.setOutputFileName(str(direct))
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(QPrintDialog, "exec", accept)
    page.print_direct()
    read = lambda path: normalized_pdf_text(
        "".join(p["text"] for p in require_searchable_pdf(path)["pages"])
    )
    assert read(saved) == read(direct)
    w.instruction_output_mode.setCurrentIndex(w.instruction_output_mode.findData("both"))
    combined = tmp_path / "both.pdf"
    monkeypatch.setattr(w, "save_path", lambda *args: str(combined))
    w.export_pdf()
    text = read(combined)
    assert "入替予定地図" in text and "入れ替え指示書" in text and "対象外" not in text


def test_mixed_paper_preview_uses_actual_pdf_and_regenerates(window):
    from prize_swap.instruction_preview import InstructionPreview

    w = window
    w.floor.instruction_print_options.update(mode="both", paper="A3", orientation="portrait")
    before = [asdict(m) for m in w.floor.machines]
    dialog = InstructionPreview(w)
    dialog.show()
    QApplication.processEvents()
    assert dialog.document.pageCount() == 2
    assert dialog.document.pagePointSize(0).width() == pytest.approx(842, abs=1)
    assert dialog.document.pagePointSize(1).width() == pytest.approx(842, abs=1)
    assert dialog.document.pagePointSize(1).height() == pytest.approx(1191, abs=1)
    dialog.direction.setCurrentIndex(dialog.direction.findData("landscape"))
    QApplication.processEvents()
    assert dialog.document.pagePointSize(1).width() == pytest.approx(1191, abs=1)
    dialog.scope.setCurrentIndex(dialog.scope.findData("map"))
    QApplication.processEvents()
    assert dialog.document.pageCount() == 1
    path = dialog.path
    dialog.reject()
    assert not path.exists()
    assert [asdict(m) for m in w.floor.machines] == before


def test_map_preview_changes_paper_without_changing_main_output_scope(window):
    from prize_swap.instruction_preview import InstructionPreview

    w = window
    dialog = InstructionPreview(w, map_page=w.instruction_map)
    assert (
        dialog.document.pageCount() == 1
        and w.floor.instruction_print_options["mode"] == "instructions"
    )
    dialog.paper.setCurrentText("A3")
    dialog.direction.setCurrentIndex(dialog.direction.findData("landscape"))
    QApplication.processEvents()
    assert dialog.document.pagePointSize(0).width() == pytest.approx(1191, abs=1)
    assert w.instruction_map.paper.currentText() == "A3"
    assert w.floor.instruction_print_options["mode"] == "instructions"
    dialog.reject()


def test_map_zero_output_uses_the_same_current_scope_selection(window, tmp_path, monkeypatch):
    w = window
    for p in w.products:
        p.included = False
    w.refresh()
    target = tmp_path / "selected-map.pdf"
    monkeypatch.setattr(w, "save_path", lambda *_: str(target))
    monkeypatch.setattr(w, "ask_output_selection", lambda: False)
    w.instruction_map.export_pdf()
    assert not target.exists()
    monkeypatch.setattr(w, "ask_output_selection", lambda: True)
    w.instruction_map.export_pdf()
    if w.preflight.pending:
        w.preflight.continue_output()
    assert target.exists() and all(p.included for p in w.products)
