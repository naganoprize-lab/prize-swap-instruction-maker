import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from datetime import date

import pytest
from PySide6.QtCore import QDate, Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QComboBox, QLineEdit, QPlainTextEdit
from PySide6.QtPrintSupport import QPrintPreviewDialog, QPrintPreviewWidget

from prize_swap.core import Product, load_session
from prize_swap.table import MethodsCombo
from prize_swap.ui import Window

DAY = date(2026, 11, 5)


@pytest.fixture
def window(tmp_path):
    app = QApplication.instance() or QApplication([])
    w = Window(layout_storage=tmp_path / "floor-auto.json")
    w.products = [Product(DAY, "商品A", 24, 2), Product(DAY, "商品B", 12, 1)]
    w.day.setDate(QDate(2026, 11, 5))
    w.refresh()
    w.show()
    app.processEvents()
    yield w
    w.dirty = False
    w.close()
    app.processEvents()


@pytest.mark.parametrize("size", [(1100,700),(1500,900),(1920,1080)])
def test_table_fills_center_without_top_gap(window, size):
    window.resize(*size)
    QApplication.processEvents()
    table_top = window.table.mapTo(window.centralWidget(), window.table.rect().topLeft()).y()
    assert table_top < 260
    assert window.table.height() > size[1] * .5
    assert window.table.width() > size[0] * .85
    assert window.table.verticalHeader().defaultSectionSize() == 34
    assert not hasattr(window, "apply_details")


def test_table_edit_date_retention_priority_and_validation(window):
    model = window.model
    assert model.setData(model.index(0, 3), "変更商品A")
    assert model.setData(model.index(0, 4), "36")
    assert model.setData(model.index(0, 5), "1.5")
    assert not model.setData(model.index(0, 4), "-1")
    assert not model.setData(model.index(0, 3), "")
    assert model.setData(model.index(0, 6), "20085右\n20098左")
    assert model.setData(model.index(0, 8), Qt.CheckState.Checked, Qt.ItemDataRole.CheckStateRole)
    assert model.data(model.index(0, 0), Qt.ItemDataRole.BackgroundRole).name() == "#fce4ec"
    assert window.products[0].units == 36
    assert window.products[0].cases == 1.5
    assert window.products[0].machines == ["20085右", "20098左"]
    assert "変更商品A" in model.data(model.index(0, 3), Qt.ItemDataRole.ToolTipRole)
    window.day.setDate(QDate(2026, 11, 6))
    assert model.rowCount() == 0
    window.day.setDate(QDate(2026, 11, 5))
    assert model.rowCount() == 2
    assert window.products[0].name == "変更商品A"


def test_real_delegates_inline_edit_and_dropdown(window):
    # Actual delegate editors live in the table viewport, not in a new window.
    proxy = window.proxy
    index = proxy.index(0, 3)
    window.table.setCurrentIndex(index)
    QApplication.processEvents()
    editor = window.table.viewport().findChild(QLineEdit)
    assert editor is not None and not editor.isWindow()
    editor.setText("セルで変更")
    QTest.keyClick(editor, Qt.Key.Key_Return)
    QApplication.processEvents()
    assert window.products[0].name == "セルで変更"
    index = proxy.index(0, 6)
    window.table.setCurrentIndex(index)
    QApplication.processEvents()
    machine_editor = window.table.viewport().findChild(QPlainTextEdit)
    assert machine_editor is not None and not machine_editor.isWindow()
    machine_editor.setPlainText("20085右\n20098左")
    QTest.keyClick(machine_editor, Qt.Key.Key_Tab)
    QApplication.processEvents()
    assert window.products[0].machines == ["20085右", "20098左"]

    window.table.scrollTo(proxy.index(0, 9))
    QTest.mouseClick(window.table.viewport(), Qt.MouseButton.LeftButton, pos=window.table.visualRect(proxy.index(0, 9)).center())
    QApplication.processEvents()
    limit_editor = next(e for e in window.table.viewport().findChildren(QComboBox) if not isinstance(e, MethodsCombo))
    assert limit_editor.view().isVisible()  # opens with the first click
    limit_editor.setCurrentText("各②")
    limit_editor.activated.emit(limit_editor.currentIndex())
    limit_editor.hidePopup()
    window.search.setFocus()
    QApplication.processEvents()
    assert window.products[0].limit == "各②"

    window.table.scrollTo(proxy.index(0, 7))
    window.table.setCurrentIndex(proxy.index(0, 7))
    QApplication.processEvents()
    methods = window.table.viewport().findChild(MethodsCombo)
    assert methods is not None and not methods.isWindow()
    methods.showPopup()
    QApplication.processEvents()
    for row in (0, 1):
        rect = methods.view().visualRect(methods.model().index(row, 0))
        QTest.mouseClick(methods.view().viewport(), Qt.MouseButton.LeftButton, pos=rect.center())
        QApplication.processEvents()
        assert methods.view().isVisible()
    assert window.products[0].methods == ["橋渡し", "リング"]
    methods.hidePopup()
    methods.setEditText(methods.currentText() + ", 独自運営")
    window.search.setFocus()
    QApplication.processEvents()
    assert window.products[0].methods == ["橋渡し", "リング", "独自運営"]


def test_search_selection_and_order(window):
    window.search.setText("商品A")
    assert window.proxy.rowCount() == 1
    window.set_all(False)
    assert not window.products[0].included and window.products[1].included
    window.search.clear()
    window.filter_choice.setCurrentIndex(1)
    assert window.proxy.rowCount() == 1
    window.set_all(False)
    assert window.proxy.rowCount() == 0
    window.filter_choice.setCurrentIndex(0)
    window.set_all(True)
    window.table.selectRow(1)
    window.move_products(-1)
    assert window.proxy.index(0, 3).data() == "商品B"
    window.move_products(1)
    assert window.proxy.index(0, 3).data() == "商品A"


def test_ui_export_preview_pdf_and_session(window, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QFileDialog, QMessageBox
    from openpyxl import load_workbook
    monkeypatch.setattr(QMessageBox, "information", lambda *a: None)
    output = tmp_path / "UI出力.xlsx"
    monkeypatch.setattr(window, "save_path", lambda *a: str(output))
    window.export()
    wb = load_workbook(output)
    assert wb.active["C3"].value == "商品A"
    wb.close()
    saved = tmp_path / "作業.json"
    window.model.setData(window.model.index(0, 7), "橋渡し,自由運営")
    window.model.setData(window.model.index(0, 9), "トータル⑩")
    monkeypatch.setattr(window, "save_path", lambda *a: str(saved))
    window.save_json()
    assert load_session(saved)[0] == window.products
    window.products = []
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a: (str(saved), ""))
    window.open_json()
    assert window.model.rowCount() == 2
    assert window.products[0].methods == ["橋渡し", "自由運営"]
    assert window.products[0].limit == "トータル⑩"
    pdf = tmp_path / "画面から.pdf"
    monkeypatch.setattr(window, "save_path", lambda *a: str(pdf))
    window.export_pdf()
    assert pdf.read_bytes().startswith(b"%PDF")

    # Exercise the real modal preview and its paintRequested rendering, closing
    # via a timer rather than replacing exec() with a no-op.
    observed = []
    def inspect_and_close():
        dialog = window.findChild(QPrintPreviewDialog)
        if dialog and dialog.isVisible():
            widget = dialog.findChild(QPrintPreviewWidget)
            observed.append(widget.pageCount())
            dialog.accept()
        else:
            QTimer.singleShot(20, inspect_and_close)
    QTimer.singleShot(100, inspect_and_close)
    window.preview()
    assert observed and observed[0] >= 1


def test_registered_floor_machine_is_available_in_cell(window, monkeypatch):
    from prize_swap.floor_data import Machine
    from prize_swap.table import MachineEditor
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QMenu, QMessageBox
    window.floor.add_machine(Machine(number="20085右", product="新規マシン"))
    window.table.setCurrentIndex(window.proxy.index(0,6))
    QApplication.processEvents()
    editor = window.table.viewport().findChild(MachineEditor)
    assert editor is not None
    assert "20085右" in editor.options()
    def select():
        menu = editor.findChild(QMenu)
        action = next(a for a in menu.actions() if a.text() == "20085右")
        action.setChecked(True)
        menu.close()
    QTimer.singleShot(20, select)
    editor.select_registered()
    window.search.setFocus()
    QApplication.processEvents()
    assert window.products[0].machines == ["20085右"]
    assert window.floor.assignments()["20085右"][0].name == "商品A"
    window.floor.nodes[window.floor.machines[0].uid].setSelected(True)
    monkeypatch.setattr(QMessageBox, "question", lambda *a: QMessageBox.StandardButton.Yes)
    window.floor.delete_selected()
    warnings = []
    def capture(*args):
        warnings.append(args[2])
        return QMessageBox.StandardButton.Yes
    monkeypatch.setattr(QMessageBox, "question", capture)
    window.print_products()
    assert warnings and "20085右" in warnings[0]


def test_excel_export_cannot_overwrite_floor_source(window, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    source = tmp_path / "元レイアウト.xlsx"
    source.write_bytes(b"original")
    window.floor.source = str(source)
    monkeypatch.setattr(window, "save_path", lambda *a: str(source))
    errors = []
    monkeypatch.setattr(window, "error", lambda exc: errors.append(str(exc)))
    window.export()
    assert errors and "上書き" in errors[0]
    assert source.read_bytes() == b"original"
