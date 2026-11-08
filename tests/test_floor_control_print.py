from dataclasses import asdict
from datetime import date

import pytest
from pypdf import PdfReader
from PySide6.QtCore import QDate, QSize, Qt
from PySide6.QtPdf import QPdfDocument
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox
from PySide6.QtTest import QTest

from prize_swap.core import Product
from prize_swap.floor_data import Machine
from prize_swap.floor_details import MachineDetailDialog
from prize_swap.floor_print import FloorPrintOptions, floor_printer, save_floor_pdf
from prize_swap.floor_print_ui import FloorPreviewDialog
from prize_swap.pdf_validation import normalized_pdf_text, require_searchable_pdf
from prize_swap.ui import Window

DAY = date(2026, 11, 6)


def machines(count=31):
    return [
        Machine(
            number=str(20000 + i),
            product=f"UNIQUE{i:03} 鬼滅の刃 商品名 フィギュア "
            + "日本語の長い商品名 ABC123 " * (i % 3),
            x=i % 6 * 300,
            y=i // 6 * 300,
            ratio=[49.9, 50, 99.9, 100, 199.9, 200, None][i % 7],
            ratio_from_excel=True,
            installed_on="2026-11-01",
        )
        for i in range(count)
    ]


def pdftext(path):
    return normalized_pdf_text("".join(p["text"] for p in require_searchable_pdf(path)["pages"]))


@pytest.mark.parametrize("paper", ["A4", "A3"])
@pytest.mark.parametrize("orientation", ["portrait", "landscape"])
def test_machine_table_pages_text_and_row_placements(tmp_path, paper, orientation):
    path = tmp_path / "マシン一覧表.pdf"
    source = machines(300)
    before = [asdict(m) for m in source]
    options = FloorPrintOptions(
        mode="list", paper=paper, orientation=orientation, sort="ratio", descending=True
    )
    plan = save_floor_pdf(path, source, {}, options, DAY)
    reader = PdfReader(path)
    assert len(plan.pages) == len(reader.pages) > 1
    assert (float(reader.pages[0].mediabox.width) > float(reader.pages[0].mediabox.height)) == (
        orientation == "landscape"
    )
    ids = []
    for page in plan.pages:
        assert page.table_rows
        assert (
            sum(row[2] for row in page.table_rows) + plan.table_heading <= plan.body.height() + 0.1
        )
        for uid, blocks, height, colour in page.table_rows:
            ids.append(uid)
            assert all(block.height <= height - 12 + 0.1 for block in blocks)
            assert len(blocks) == 3 and all(block.layout.font().bold() for block in blocks)
    assert len(ids) == len(set(ids)) == 300
    expected = sorted(
        [m for m in source if m.ratio is not None], key=lambda m: m.ratio, reverse=True
    ) + [m for m in source if m.ratio is None]
    assert ids == [m.uid for m in expected]
    text = pdftext(path)
    for i in range(300):
        assert text.count(f"UNIQUE{i:03}") == 1
    assert "49.9%" in text and "50.0%" in text and "200.0%" in text and "設置日数" not in text
    assert [asdict(m) for m in source] == before


@pytest.mark.parametrize("mode", ["fit", "tile", "list", "actual"])
def test_shared_pdf_and_direct_print_all_modes(tmp_path, monkeypatch, mode):
    import prize_swap.floor as module

    w = Window(layout_storage=tmp_path / "auto.json")
    w.floor.machines = machines(25)
    w.floor.rebuild()
    w.floor.reference_date.setDate(QDate(2026, 11, 6))
    w.floor.set_print_options(FloorPrintOptions(mode=mode, paper="A3"))
    try:
        pdf = tmp_path / "PDF保存.pdf"
        w.floor.paint_floor(floor_printer(w.floor.print_options, pdf))
        direct = tmp_path / "直接印刷.pdf"

        class Accepted:
            def __init__(self, *args):
                pass

            def setMinMax(self, *args):
                pass

            def setOption(self, *args):
                pass

            def exec(self):
                return QDialog.DialogCode.Accepted

        monkeypatch.setattr(module, "QPrintDialog", Accepted)
        monkeypatch.setattr(module, "floor_printer", lambda options: floor_printer(options, direct))
        monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Yes)
        errors = []
        monkeypatch.setattr(QMessageBox, "warning", lambda *args: errors.append(args))
        w.tabs.setCurrentWidget(w.floor)
        w.print_direct()
        assert not errors and pdftext(pdf) == pdftext(direct)
        a = QPdfDocument()
        b = QPdfDocument()
        a.load(str(pdf))
        b.load(str(direct))
        assert a.pageCount() == b.pageCount()
        for i in range(a.pageCount()):
            assert a.render(i, QSize(842, 595)) == b.render(i, QSize(842, 595))
        a.close()
        b.close()
    finally:
        w.floor.timer.stop()
        w.dirty = False
        w.close()


def test_visible_only_is_explicit_and_selection_of_print_table(tmp_path):
    w = Window(layout_storage=tmp_path / "auto.json")
    try:
        w.floor.machines = machines(10)
        w.floor.rebuild()
        w.floor.filter_by.setCurrentIndex(w.floor.filter_by.findData("poor"))
        assert len(w.floor.visible_nodes()) == 10
        assert len(w.floor._match_uids) == 2
        assert len(w.floor.print_machines()) == 10
        w.floor.set_print_options(FloorPrintOptions(mode="list", visible_only=True))
        assert len(w.floor.print_machines()) == 2
        path = tmp_path / "絞込.pdf"
        w.floor.paint_floor(floor_printer(w.floor.print_options, path))
        text = pdftext(path)
        assert "UNIQUE000" in text and "UNIQUE007" in text and "UNIQUE001" not in text
    finally:
        w.floor.timer.stop()
        w.dirty = False
        w.close()


def test_fullscreen_in_real_tab_and_detail_save_updates_instruction_model(tmp_path, monkeypatch):
    w = Window(layout_storage=tmp_path / "auto.json")
    try:
        w.products = [Product(DAY, "予定商品", machines=["20000"])]
        w.day.setDate(QDate(2026, 11, 6))
        w.refresh()
        w.floor.add_machine(machines(1)[0])
        w.show()
        w.tabs.setCurrentWidget(w.floor)
        QApplication.processEvents()

        def edit_and_accept(dialog):
            assert dialog.name.toPlainText() == w.floor.machines[0].product
            dialog.name.setPlainText("現在商品を編集")
            dialog.products[0].name.setPlainText("予定商品を編集")
            dialog.products[0].methods.change_count("橋渡し", 2)
            dialog.accept()

        w.floor.open_selected_details()
        edit_and_accept(w.floor.detail_controller.panel)
        assert w.floor.machines[0].product == "現在商品を編集"
        assert (
            w.products[0].name == "予定商品を編集" and w.products[0].planned_booths["橋渡し"] == 2
        )
        assert w.model.products[0].name == "予定商品を編集" and w.dirty
        w.floor.history.undo()
        assert w.products[0].name == "予定商品" and w.floor.machines[0].product.startswith(
            "UNIQUE000"
        )
        w.floor.history.redo()
        assert (
            w.products[0].name == "予定商品を編集"
            and w.floor.machines[0].product == "現在商品を編集"
        )
        index = w.tabs.indexOf(w.floor)
        w.floor.toggle_fullscreen()
        QApplication.processEvents()
        host = w.floor._fullscreen_host
        assert host.isFullScreen()
        QTest.keyClick(host, Qt.Key.Key_Escape)
        QApplication.processEvents()
        assert w.tabs.indexOf(w.floor) == index and w.tabs.currentWidget() is w.floor
        assert w.floor.view.height() > w.floor.height() * 0.65
    finally:
        if w.floor._fullscreen_host:
            w.floor._fullscreen_host.close()
        w.floor.timer.stop()
        w.dirty = False
        w.close()


def test_live_preview_switch_to_table_and_portrait(tmp_path):
    w = Window(layout_storage=tmp_path / "auto.json")
    preview = None
    try:
        w.floor.machines = machines(31)
        w.floor.rebuild()
        preview = FloorPreviewDialog(w.floor)
        preview.show()
        QApplication.processEvents()
        controls = preview.controls
        for key, value in (("mode", "list"), ("paper", "A3"), ("orientation", "portrait")):
            controls.combos[key].setCurrentIndex(controls.combos[key].findData(value))
        QApplication.processEvents()
        path = tmp_path / "一覧プレビュー.pdf"
        plan = w.floor.paint_floor(floor_printer(w.floor.print_options, path))
        assert preview.preview.pageCount() == len(plan.pages)
        assert not controls.fields["sales"].isEnabled()
        assert (
            w.floor.print_options.mode == "list" and w.floor.print_options.orientation == "portrait"
        )
    finally:
        if preview:
            preview.close()
        w.floor.timer.stop()
        w.dirty = False
        w.close()
