from datetime import date
from PySide6.QtCore import QDate
from PySide6.QtWidgets import QApplication
from prize_swap.ui import Window
from prize_swap.core import Product
from prize_swap.floor_data import Machine
from prize_swap.theme import manager


def test_home_real_counts_alert_navigation_recent_actions_and_update_tile(tmp_path, monkeypatch):
    w = Window(layout_storage=tmp_path / "floor.json")
    try:
        w.day.setDate(QDate(2026, 11, 8))
        w.floor.machines = [
            Machine(number="20088左", source_key="R_168", ratio=39, installed_on="2026-10-01")
        ]
        w.products = [
            Product(date(2026, 11, 8), "新景品A", 40, 1, machines=["20088左"]),
            Product(date(2026, 11, 8), "新景品B", 40, 1, machines=["R_168"]),
        ]
        w.floor.rebuild()
        w.refresh()
        w.navigation.activate("dashboard")
        w.show()
        QApplication.processEvents()
        home = w.dashboard_page
        home.refresh()
        assert (
            home.cards["total"].value.text() == "1"
            and home.cards["instructions"].value.text() == "2"
            and home.cards["output"].value.text() == "2"
        )
        assert home.cards["preflight"].value.text() == "1"
        next(b for b in home.attention_buttons if "同一マシン" in b.text()).click()
        assert w.tabs.currentWidget() is w.instruction_page and w.selected_products()
        calls = []
        monkeypatch.setattr(w, "import_excel_auto", lambda **kw: calls.append(kw), raising=False)
        home.tiles["update"].click()
        assert calls == [{"preferred": "sales"}]
        w.workflow.record_file("excel", tmp_path / "分析データ.xlsx", "売上分析")
        w.workflow.record_file("saved", tmp_path / "指示書.json", "入替指示書")
        w.workflow.record_file("output", tmp_path / "作業セット.pdf", "PDF")
        home.refresh()
        assert all(
            name in home.updated.text()
            for name in ("分析データ.xlsx", "指示書.json", "作業セット.pdf")
        )
        for theme in ("light", "dark"):
            manager().set_mode(theme, persist=False)
            w.navigation.activate("dashboard")
            w.resize(1920, 1080)
            QApplication.processEvents()
            home.refresh()
            assert not home.grab().isNull()
        assert home._tile_columns == 3
    finally:
        manager().set_mode("light", persist=False)
        w.dirty = False
        w.instruction_save_timer.stop()
        w.floor.timer.stop()
        w.floor.day_timer.stop()
        w.preflight.timer.stop()
        w.close()


def test_empty_home_does_not_invent_recent_files_or_counts(tmp_path):
    w = Window(layout_storage=tmp_path / "empty.json")
    try:
        home = w.dashboard_page
        home.refresh()
        assert all(c.value.text() == "0" for c in home.cards.values())
        assert (
            "記録なし" in home.updated.text()
            and "重大な問題は検出されていません" in home.attention_buttons[0].text()
        )
    finally:
        w.dirty = False
        w.instruction_save_timer.stop()
        w.floor.timer.stop()
        w.floor.day_timer.stop()
        w.preflight.timer.stop()
        w.close()
