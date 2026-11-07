from dataclasses import asdict
from datetime import date
import pytest
from PySide6.QtCore import Qt, QDate
from PySide6.QtWidgets import QApplication
from prize_swap.ui import Window
from prize_swap.floor_data import Machine
from prize_swap.theme import manager

DAY = date(2026, 11, 7)


@pytest.fixture
def window(tmp_path):
    original = manager().mode
    w = Window(layout_storage=tmp_path / "auto.json")
    w.themes.set_mode("light", persist=False)
    w.floor.machines = [
        Machine(
            number=f"{20000+i}右",
            product=f"商品{i}",
            ratio=i,
            x=i % 20 * 240,
            y=i // 20 * 180,
        )
        for i in range(300)
    ]
    w.floor.rebuild()
    w.show()
    QApplication.processEvents()
    yield w
    w.dirty = False
    w.floor.timer.stop()
    w.floor.day_timer.stop()
    w.close()
    manager().set_mode(original, persist=False)


def test_navigation_routes_existing_functions_without_empty_pages(window, monkeypatch):
    before = [asdict(m) for m in window.floor.machines]
    for key in ("dashboard", "floor", "instructions", "labels", "settings"):
        window.navigation.buttons[key].click()
        QApplication.processEvents()
        assert (
            window.tabs.currentWidget() is window.pages[key]
            and window.navigation.buttons[key].isChecked()
        )
        window.navigation.buttons[key].click()
        assert window.navigation.buttons[key].isChecked()
    called = []
    for key, method in (
        ("candidates", "show_candidates"),
        ("analysis", "show_method_analysis"),
        ("matching", "show_matching"),
    ):
        monkeypatch.setattr(window.floor, method, lambda k=key: called.append(k))
        window.navigation.buttons[key].click()
    assert called == ["candidates", "analysis", "matching"]
    assert [asdict(m) for m in window.floor.machines] == before


@pytest.mark.parametrize("size", [(1100, 700), (1500, 900), (1920, 1080)])
def test_modern_workspace_resizes_and_controls_remain_accessible(window, size):
    window.resize(*size)
    window.tabs.setCurrentWidget(window.floor)
    QApplication.processEvents()
    assert window.floor.view.viewport().height() > window.height() * 0.38
    assert window.theme_button.isVisible() and window.settings_button.isVisible()
    assert (
        window.centralWidget()
        .rect()
        .contains(
            window.theme_button.mapTo(
                window.centralWidget(), window.theme_button.rect().bottomRight()
            )
        )
    )
    assert (
        window.centralWidget()
        .rect()
        .contains(
            window.instruction_settings.parentWidget().mapTo(
                window.centralWidget(),
                window.instruction_settings.parentWidget().rect().bottomRight(),
            )
        )
    )
    assert window.navigation.compact == (size[0] < 1280)


def test_page_search_keeps_full_floor_and_dashboard_uses_same_values(window):
    window.tabs.setCurrentWidget(window.floor)
    window.page_search.setText("商品299")
    assert len(window.floor.visible_nodes()) == 300 and len(window.floor._match_uids) == 1
    window.floor.floor_search.clear()
    assert window.page_search.text() == ""
    window.tabs.setCurrentWidget(window.dashboard_page)
    window.dashboard_page.refresh()
    assert window.dashboard_page.cards["total"].value.text() == "300"
    assert sum(r["count"] for r in window.dashboard_page.bars.groups.values()) == 300
    window.dashboard_page.locate("50%未満")
    assert window.tabs.currentWidget() is window.floor
    assert len(window.floor._highlight_uids) == 50 and len(window.floor.visible_nodes()) == 300


def test_short_high_dpi_workspace_keeps_canvas_and_dashboard_accessible(window):
    window.setMinimumSize(900, 560)
    window.resize(1000, 560)
    window.tabs.setCurrentWidget(window.floor)
    QApplication.processEvents()
    from PySide6.QtTest import QTest

    QTest.qWait(50)  # Settle the nested layout after compact chrome is applied.
    floor = window.floor
    before = [(m.x, m.y, m.width, m.height) for m in floor.machines]
    expanded = floor.analysis_settings["dashboard_visible"]
    assert floor.dashboard.compact and floor.view.viewport().height() > 200
    floor.dashboard.toggle.click()
    assert window.tabs.currentWidget() is window.dashboard_page
    window.tabs.setCurrentWidget(floor)
    window.resize(1500, 900)
    QApplication.processEvents()
    assert not floor.dashboard.compact
    assert floor.dashboard.content.isVisible() == expanded
    assert floor.analysis_settings["dashboard_visible"] == expanded
    assert [(m.x, m.y, m.width, m.height) for m in floor.machines] == before


def test_settings_theme_control_and_compact_navigation_keep_card_sizes(window):
    before = [(m.x, m.y, m.width, m.height) for m in window.floor.machines]
    window.show_settings()
    assert window.tabs.currentWidget() is window.settings_page
    window.settings_page.theme.setCurrentIndex(window.settings_page.theme.findData("dark"))
    assert window.themes.dark and "ダーク" in window.theme_button.text()
    window.navigation.toggle()
    QApplication.processEvents()
    assert [(m.x, m.y, m.width, m.height) for m in window.floor.machines] == before


def test_product_table_sorts_numeric_and_names_without_changing_print_order(window):
    from prize_swap.core import Product

    window.products = [
        Product(DAY, "商品B", 2000, 2, order=1),
        Product(DAY, "商品A", 40, 1, order=2),
    ]
    window.day.setDate(QDate(DAY))
    window.refresh()
    window.table.sortByColumn(4, Qt.SortOrder.AscendingOrder)
    assert window.proxy.index(0, 3).data() == "商品A" and window.model.index(0, 4).data() == "2,000"
    assert window.model.index(0, 4).data(Qt.ItemDataRole.EditRole) == 2000
    window.table.sortByColumn(3, Qt.SortOrder.AscendingOrder)
    assert window.proxy.index(0, 3).data() == "商品A"
    assert [p.name for p in window.visible] == ["商品B", "商品A"]
