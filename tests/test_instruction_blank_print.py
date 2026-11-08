from datetime import date
from dataclasses import asdict
from PySide6.QtCore import Qt, QDate
from PySide6.QtWidgets import QMessageBox
from prize_swap.core import Product, save_session, load_session
from prize_swap.table import ProductModel
from prize_swap.printing import save_pdf
from prize_swap.pdf_validation import require_searchable_pdf, normalized_pdf_text
from prize_swap.ui import Window
from prize_swap.exporter import export_excel
from openpyxl import load_workbook

DAY = date(2026, 11, 8)


def test_blank_manual_machine_method_and_amounts_print_and_restore(tmp_path):
    p = Product(DAY, "", machines=["R_168"], notes="投入商品検討中", source_type="manual")
    model = ProductModel()
    model.replace([p])
    assert model.setData(model.index(0, 0), Qt.CheckState.Checked, Qt.ItemDataRole.CheckStateRole)
    assert model.setData(model.index(0, 2), "自由なメーカー")
    path = tmp_path / "blank.pdf"
    plan = save_pdf(path, [p], DAY)
    text = normalized_pdf_text("".join(v["text"] for v in require_searchable_pdf(path)["pages"]))
    assert "R_168" in text and all(v not in text for v in ("None", "null", "商品未定"))
    assert plan.pages and plan.columns(0)[0][0].cells[3].text == ""
    save_session(tmp_path / "blank.json", [p], DAY, "")
    restored, _, _ = load_session(tmp_path / "blank.json")
    assert asdict(restored[0]) == asdict(p)
    export_excel(tmp_path / "blank.xlsx", [p], DAY)
    wb = load_workbook(tmp_path / "blank.xlsx")
    values = [c.value for row in wb.active for c in row]
    assert any("R_168" in str(v) for v in values) and "未設定" not in values
    wb.close()


def test_zero_output_selects_only_current_visible_date_and_keeps_old_off(tmp_path, monkeypatch):
    w = Window(layout_storage=tmp_path / "auto.json")
    try:
        w.day.setDate(QDate(2026, 11, 8))
        w.products = [
            Product(DAY, "", machines=["R_168"], included=False),
            Product(date(2026, 11, 9), "別の日", included=False),
        ]
        w.refresh()
        assert not w.products[0].included
        monkeypatch.setattr(w, "ask_output_selection", lambda: True)
        out = w.print_products()
        assert out == [w.products[0]] and w.products[0].included and not w.products[1].included
        w.set_all(False)
        assert not w.products[0].included
    finally:
        w.dirty = False
        w.floor.timer.stop()
        w.floor.day_timer.stop()
        w.close()
