import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from datetime import date

from PySide6.QtCore import QDate, Qt
from PySide6.QtWidgets import QApplication, QAbstractItemView

from prize_swap.core import Product
from prize_swap.ui import Window


def test_table_bulk_edit_and_date_retention():
    app = QApplication.instance() or QApplication([])
    window = Window()
    day = date(2026, 11, 5)
    window.products = [Product(day, "商品A"), Product(day, "商品B")]
    window.day.setDate(QDate(2026, 11, 5))
    window.refresh()
    window.show()
    app.processEvents()
    assert window.table.rowCount() == 2
    window.table.item(0, 3).setText("変更商品A")
    window.table.selectAll()
    assert len(window.selected_products()) == 2
    window.machine_entry.setPlainText("20085右\n20098左")
    window.add_machine()
    window.method_checks[0].setChecked(True)
    window.custom.setText("独自運営")
    window.apply_methods.setChecked(True)
    window.priority.setChecked(True)
    window.apply_priority.setChecked(True)
    window.limit.setCurrentText("各②")
    window.apply_limit.setChecked(True)
    window.apply_details()
    for p in window.products:
        assert p.machines == ["20085右", "20098左"]
        assert p.methods == ["橋渡し", "独自運営"]
        assert p.priority and p.limit == "各②"
    window.day.setDate(QDate(2026, 11, 6))
    assert window.table.rowCount() == 0
    window.day.setDate(QDate(2026, 11, 5))
    assert window.table.rowCount() == 2
    assert window.table.item(0, 3).text() == "変更商品A"
    window.dirty = False
    window.close()


def test_ui_export_preview_and_session(tmp_path, monkeypatch):
    from PySide6.QtWidgets import QDialog, QFileDialog, QMessageBox, QTextBrowser
    from openpyxl import load_workbook
    from prize_swap.core import load_session
    app = QApplication.instance() or QApplication([])
    window = Window()
    day = date(2026, 11, 5)
    window.products = [Product(day, "一括確認", 12, 2, machines=["123右"], priority=True)]
    window.day.setDate(QDate(2026, 11, 5))
    window.refresh()
    monkeypatch.setattr(QMessageBox, "information", lambda *a: None)
    output = tmp_path / "UI出力.xlsx"
    monkeypatch.setattr(window, "save_path", lambda *a: str(output))
    window.export()
    wb = load_workbook(output)
    assert wb.active["C3"].value == "一括確認"
    wb.close()
    def inspect_preview(dialog):
        text = dialog.findChild(QTextBrowser).toPlainText()
        assert "一括確認" in text and "123右" in text
        return 0
    monkeypatch.setattr(QDialog, "exec", inspect_preview)
    window.preview()
    saved = tmp_path / "作業.json"
    monkeypatch.setattr(window, "save_path", lambda *a: str(saved))
    window.save_json()
    assert load_session(saved)[0] == window.products
    window.products = []
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a: (str(saved), ""))
    window.open_json()
    assert window.table.rowCount() == 1
    assert window.products[0].name == "一括確認"
    window.dirty = False
    window.close()
