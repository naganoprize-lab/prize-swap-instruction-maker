from dataclasses import asdict
from datetime import date
import pytest
from PySide6.QtCore import QDate, QRectF
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QImage, QPainter, QColor
from PySide6.QtTest import QTest
from prize_swap.ui import Window
from prize_swap.floor_data import Machine
from prize_swap.core import Product
from prize_swap.theme import manager, tokens
from prize_swap.instruction_map import plan_map, draw_context_card, draw_map_page, map_layout
from prize_swap.printing import configured_printer, render_pages
from prize_swap.pdf_validation import require_searchable_pdf, normalized_pdf_text

DAY = date(2026, 11, 8)


@pytest.fixture
def window(tmp_path):
    w = Window(layout_storage=tmp_path / "floor.json")
    w.day.setDate(QDate(DAY))
    w.floor.machines = [
        Machine(number="20088左", x=100, y=100, width=180, height=120),
        Machine(
            number="20089右",
            kind="UFO9 third",
            product="周辺商品を印刷しない",
            x=310,
            y=100,
            width=180,
            height=120,
        ),
        Machine(number="99999右", x=100000, y=100000),
    ]
    w.products = [Product(DAY, "", 40, 1, machines=["20088左"])]
    w.floor.rebuild()
    w.refresh()
    w.resize(1600, 1000)
    w.navigation.activate("instructions")
    w.instruction_tabs.setCurrentWidget(w.instruction_map)
    w.show()
    QTest.qWait(60)
    yield w
    w.dirty = False
    w.instruction_save_timer.stop()
    w.floor.timer.stop()
    w.floor.day_timer.stop()
    w.close()


def test_context_default_clear_fill_target_fit_stable_zoom_and_reused_target(window):
    w = window
    p = w.instruction_map
    assert p.context.isChecked() and len(p.context_items) == 2 and len(p.items) == 1
    before = [asdict(m) for m in w.floor.machines]
    item = next(iter(p.items.values()))
    scale = p.view.transform().m11()
    center = p.view.mapToScene(p.view.viewport().rect().center())
    assert p.target_bounds.width() < 1000 and p.scene.sceneRect().width() > 100000
    for flag in (False, True):
        p.context.setChecked(flag)
        QApplication.processEvents()
        assert p.view.transform().m11() == pytest.approx(scale)
        assert p.view.mapToScene(p.view.viewport().rect().center()) == center
        assert next(iter(p.items.values())) is item
    assert [asdict(m) for m in w.floor.machines] == before


@pytest.mark.parametrize("theme,expected", [("light", "#b8c6d8"), ("dark", "#475569")])
def test_context_card_has_opaque_theme_contrast_and_only_identity_and_type(theme, expected):
    manager().set_mode(theme, persist=False)
    try:
        image = QImage(180, 120, QImage.Format.Format_ARGB32)
        image.fill(QColor("transparent"))
        p = QPainter(image)
        draw_context_card(
            p, QRectF(0, 0, 180, 120), Machine(number="20089右", kind="UFO9 third"), screen=True
        )
        p.end()
        color = image.pixelColor(10, 10)
        assert color.name() == expected and color.alpha() == 255
        assert abs(color.lightnessF() - QColor(tokens()["canvas"]).lightnessF()) > 0.15
    finally:
        manager().set_mode("light", persist=False)


@pytest.mark.parametrize("paper", ["A4", "A3"])
def test_pdf_context_keeps_target_fit_and_machine_type_without_product_or_remote_booth(
    window, tmp_path, paper
):
    w = window
    p = plan_map(w.visible, w.floor.machines, [], DAY, paper, context=True)
    assert p.pages[0].bounds.width() < 1000
    assert [m.number for m in p.pages[0].context] == ["20089右"]
    path = tmp_path / f"{paper}.pdf"
    printer = configured_printer(path)
    render_pages(
        printer,
        len(p.pages),
        lambda painter, i: draw_map_page(painter, p, i),
        lambda i: map_layout(p.paper, p.pages[i].orientation),
    )
    text = normalized_pdf_text("".join(v["text"] for v in require_searchable_pdf(path)["pages"]))
    assert (
        "20089右" in text
        and "UFO9third" in text
        and "周辺商品を印刷しない" not in text
        and "99999右" not in text
    )


def test_neighbor_import_identity_and_type_updates_invalidate_cached_card(window):
    p = window.instruction_map
    m = window.floor.machines[1]
    m.number = ""
    m.resolved_number = "20111右"
    m.analysis = {"machine_name": "UFO9 third"}
    p.refresh()
    item = p.context_items[m.uid]
    m.resolved_number = "20110左"
    m.analysis["machine_name"] = "UFO10"
    p.refresh()
    assert p.context_items[m.uid] is item
    assert "20110左" in item.toolTip() and "UFO10" in item.toolTip()
    assert "20111右" not in item.toolTip()
