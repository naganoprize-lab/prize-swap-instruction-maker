from copy import deepcopy
from dataclasses import asdict
from datetime import date

import pytest
from PySide6.QtCore import QDate, QPoint, QPointF, QRectF, Qt
from PySide6.QtGui import QImage, QPainter, QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QInputDialog, QMessageBox
from openpyxl import Workbook

from prize_swap.core import Product
from prize_swap.floor import FloorWidget
from prize_swap.floor_details import MachineDetailDialog
from prize_swap.floor_data import Machine, import_layout, validate_state
from prize_swap.floor_presentation import (
    DEFAULTS,
    ordered_machines,
    project_machines,
    visible_machines,
)
from prize_swap.floor_render import CardRenderer, SUMMARY_FIELDS
from prize_swap.floor_status import machine_status

DAY = date(2026, 11, 6)


@pytest.fixture
def floor(tmp_path):
    products = [
        Product(
            DAY,
            "次回の商品",
            40,
            2,
            "次回メーカー",
            machines=["20000右"],
            planned_booths={"リング": 3},
            machine_methods={"20000右": "リング"},
        )
    ]
    w = FloorWidget(lambda: products, lambda: DAY, tmp_path / "auto.json")
    w.resize(1400, 850)
    w.show()
    w.reference_date.setDate(QDate(2026, 11, 6))
    w.machines = [
        Machine(
            number=f"{20000+i}右",
            product=f"現在の商品{i}",
            x=i * 250,
            y=100,
            ratio=value,
            installed_on=f"2026-11-0{1+i}",
        )
        for i, value in enumerate((49.9, 50, 99.9, 100, 199.9, 200, None))
    ]
    w.rebuild()
    QApplication.processEvents()
    yield w
    if w._fullscreen_host:
        w._fullscreen_host.close()
    w.timer.stop()
    w.day_timer.stop()
    w.close()


@pytest.mark.parametrize(
    "value,colour",
    [
        (49.9, "#F4A6A6"),
        (50, "#FFFFFF"),
        (99.9, "#FFFFFF"),
        (100, "#94D8A0"),
        (199.9, "#94D8A0"),
        (200, "#FFE16A"),
        (None, "#FFFFFF"),
    ],
)
def test_new_palette_literal_and_blue_priority(value, colour):
    m = Machine(ratio=value)
    assert machine_status(m)[0] == colour
    assert machine_status(m, True, True)[0] == "#A9D7FF"


def test_summary_hierarchy_and_ellipsis_only_on_screen(floor):
    m = floor.machines[0]
    m.product = "日本語の非常に長い商品名 フィギュア ABC123 🎁 " * 40
    background, status, _, fields, font, _ = floor.card_context(m)
    assert tuple(fields) == SUMMARY_FIELDS
    renderer = CardRenderer()
    layout = renderer.plan(m, 260, 210, 14, fields, DAY, status, summary=True, truncate=True)
    assert len(layout.blocks) == 4
    number, ratio, days, name = layout.blocks
    assert ratio[2] > days[2] > name[2]
    assert ratio[1].layout.font().bold() and days[1].layout.font().bold()
    assert name[1].layout.lineCount() <= 3 and name[1].text.endswith("…")
    assert name[1].text != m.product and m.product.endswith("🎁 " * 1)
    assert "現在" not in name[1].text
    printed = renderer.plan(m, 260, 210, 14, fields, DAY, status, summary=True, grow=True)
    assert printed.blocks[-1][1].text == m.product and "…" not in printed.blocks[-1][1].text
    assert not any("売上" in block.text or "設置日" in block.text for _, block, _ in layout.blocks)


def test_view_mode_click_details_and_edit_drag_are_separate(floor, monkeypatch):
    opened = []
    monkeypatch.setattr(floor, "open_details", opened.append)
    m = floor.machines[0]
    floor.view.centerOn(floor.nodes[m.uid])
    QApplication.processEvents()
    point = floor.view.mapFromScene(QPointF(m.x + 50, m.y + 35))
    QTest.mouseClick(floor.view.viewport(), Qt.MouseButton.LeftButton, pos=point)
    QApplication.processEvents()
    assert opened == [m.uid]
    floor.edit_mode.setChecked(True)
    opened.clear()
    point = floor.view.mapFromScene(floor.nodes[m.uid].pos() + QPointF(50, 35))
    QTest.mousePress(floor.view.viewport(), Qt.MouseButton.LeftButton, pos=point)
    QTest.mouseMove(floor.view.viewport(), point + QPoint(60, 40), 30)
    QTest.mouseRelease(floor.view.viewport(), Qt.MouseButton.LeftButton, pos=point + QPoint(60, 40))
    QApplication.processEvents()
    assert not opened and m.x == 60 and m.y == 140
    floor.history.undo()
    assert floor.machines[0].x == 0 and floor.machines[0].y == 100


def test_details_missing_current_data_and_existing_plan_edit(floor):
    dialog = MachineDetailDialog(floor, floor.machines[0])
    assert dialog.values["sales"].text() == "—" and dialog.values["po_rate"].text() == "—"
    assert dialog.maker.text() == ""  # Next product's maker must not leak into current data.
    assert dialog.name.toPlainText() == "現在の商品0"
    assert (
        dialog.values["days"].text() == "5日" and dialog.values["reference"].text() == "2026-11-06"
    )
    assert len(dialog.products) == 1
    p = dialog.products[0]
    assert p.name.toPlainText() == "次回の商品"
    p.name.setPlainText("次回の商品を編集")
    p.methods.change_count("リング", 4)
    p.methods.change_assignment("20000右", "フック")
    dialog.name.setPlainText("現在の商品を編集")
    dialog.method.setCurrentText("自由記載方式")
    dialog.accept()
    assert dialog.result() == QDialog.DialogCode.Accepted
    assert floor.products()[0].name == "次回の商品"  # Cancel/reject never commits drafts.
    dialog.apply_products()
    assert floor.products()[0].planned_booths["リング"] == 4
    assert floor.products()[0].machine_methods["20000右"] == "フック"
    assert floor.products()[0].name == "次回の商品を編集"
    assert (
        dialog.updated.product == "現在の商品を編集"
        and dialog.updated.operation_method == "自由記載方式"
    )
    dialog.close()


def test_projection_sorting_filters_leave_300_positions_unchanged(floor):
    floor.machines = [
        Machine(
            number=str(30000 + i),
            product=f"商品{i:03}",
            x=i % 20 * 190,
            y=i // 20 * 130,
            ratio=i,
            installed_on="2026-11-01",
        )
        for i in range(300)
    ]
    floor.rebuild()
    before = [asdict(m) for m in floor.machines]
    for mode in ("actual", "readable", "list"):
        floor.layout_mode.setCurrentIndex(floor.layout_mode.findData(mode))
        QApplication.processEvents()
        assert len(floor.visible_nodes()) == 300
        for key in ("number", "ratio", "days", "product"):
            floor.sort_by.setCurrentIndex(floor.sort_by.findData(key))
            floor.descending.setChecked(True)
            assert [asdict(m) for m in floor.machines] == before
    floor.filter_by.setCurrentIndex(floor.filter_by.findData("poor"))
    assert len(floor.visible_nodes()) == 50
    floor.filter_by.setCurrentIndex(floor.filter_by.findData("high"))
    assert len(floor.visible_nodes()) == 100
    floor.floor_search.setText("商品299")
    assert len(floor.visible_nodes()) == 1
    assert [asdict(m) for m in floor.machines] == before


def test_filter_days_and_planned_uses_real_values(floor):
    floor.filter_by.setCurrentIndex(floor.filter_by.findData("planned"))
    assert [n.machine.number for n in floor.visible_nodes()] == ["20000右"]
    floor.products()[0].included = False
    floor.sync()
    assert not floor.visible_nodes()
    floor.filter_by.setCurrentIndex(0)
    floor.days_filter.setChecked(True)
    floor.min_days.setValue(2)
    floor.max_days.setValue(3)
    assert {n.machine.number for n in floor.visible_nodes()} == {"20002右", "20003右"}
    floor.reference_date.setDate(QDate(2026, 11, 7))
    assert {n.machine.number for n in floor.visible_nodes()} == {"20003右", "20004右"}


def test_fullscreen_esc_keeps_selection_and_coordinates(floor, monkeypatch):
    before = [asdict(m) for m in floor.machines]
    node = floor.nodes[floor.machines[0].uid]
    node.setSelected(True)
    floor.toggle_fullscreen()
    QApplication.processEvents()
    host = floor._fullscreen_host
    assert host.isFullScreen() and node.isSelected()
    opened = []
    monkeypatch.setattr(floor, "open_details", opened.append)
    floor.open_selected_details()
    assert opened == [node.machine.uid]
    floor.edit_mode.setChecked(True)
    assert node.flags() & node.GraphicsItemFlag.ItemIsMovable
    QTest.keyClick(host, Qt.Key.Key_Escape)
    QApplication.processEvents()
    assert floor._fullscreen_host is None and [asdict(m) for m in floor.machines] == before


@pytest.mark.parametrize(
    "modifiers", [Qt.KeyboardModifier.NoModifier, Qt.KeyboardModifier.ControlModifier]
)
def test_wheel_zoom_does_not_change_positions(floor, modifiers):
    before = [asdict(m) for m in floor.machines]
    floor.set_zoom(100)
    event = QWheelEvent(
        QPointF(100, 100),
        QPointF(100, 100),
        QPoint(),
        QPoint(0, 120),
        Qt.MouseButton.NoButton,
        modifiers,
        Qt.ScrollPhase.NoScrollPhase,
        False,
    )
    QApplication.sendEvent(floor.view.viewport(), event)
    assert floor.view.transform().m11() > 1 and [asdict(m) for m in floor.machines] == before


def test_presets_pan_and_view_settings_json(floor, tmp_path, monkeypatch):
    floor.presentation.update(width=320, height=230, font=18)
    floor.layout_mode.setCurrentIndex(floor.layout_mode.findData("readable"))
    monkeypatch.setattr(QInputDialog, "getText", lambda *args: ("大きいカード", True))
    floor.save_card_preset()
    floor.floor_search.setText("現在")
    floor.legend_toggle.setChecked(False)
    floor.set_zoom(150)
    floor.view.centerOn(400, 500)
    floor.capture_view(False)
    state = floor.state()
    floor.autosave()
    restored = FloorWidget(lambda: [], lambda: DAY, floor.storage)
    try:
        assert restored.state() == state
        assert restored.card_presets["大きいカード"] == [320, 230, 18]
        assert (
            restored.presentation["legend"] is False
            and restored.state()["view_center"] == state["view_center"]
        )
        assert restored.view.transform().m11() == pytest.approx(1.5)
    finally:
        restored.timer.stop()
        restored.day_timer.stop()
        restored.close()


def test_po_rate_excel_percent_import(tmp_path):
    path = tmp_path / "PO率.xlsx"
    wb = Workbook()
    ws = wb.active
    ws.title = "投入中レイアウト"
    ws.append(["L_1", "商品"])
    ws.append(["売上/ｱﾍﾞﾚｰｼﾞ", 1000, 500])
    ws.append(["PO率/ｱﾍﾞ比", 0.25, 0.499])
    ws["B3"].number_format = "0%"
    ws["C3"].number_format = "0.0%"
    wb.save(path)
    wb.close()
    machines, _ = import_layout(path)
    assert machines[0].po_rate == 25 and machines[0].ratio == pytest.approx(49.9)


def test_search_delete_and_undo_edit_text_without_deleting_machine(floor, monkeypatch):
    floor.nodes[floor.machines[0].uid].setSelected(True)
    floor.floor_search.setText("現在")
    floor.floor_search.setFocus()
    floor.activateWindow()
    QApplication.processEvents()
    floor.floor_search.setSelection(0, 1)
    confirmations = []
    monkeypatch.setattr(QMessageBox, "question", lambda *args: confirmations.append(args))
    before = [asdict(m) for m in floor.machines]
    QTest.keyClick(floor.floor_search, Qt.Key.Key_Delete)
    assert floor.floor_search.text() == "在"
    QTest.keyClick(floor.floor_search, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
    assert floor.floor_search.text() == "現在"
    assert not confirmations and [asdict(m) for m in floor.machines] == before


def test_manual_json_open_restores_zoom_and_pan(floor, tmp_path, monkeypatch):
    import json
    from PySide6.QtWidgets import QFileDialog

    floor.set_zoom(175)
    floor.view.centerOn(500, 250)
    floor.capture_view(False)
    state = floor.state()
    path = tmp_path / "表示位置.json"
    path.write_text(json.dumps(state), encoding="utf-8")
    floor.set_zoom(50)
    floor.view.centerOn(100, 100)
    floor.capture_view(False)
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *args: (str(path), "JSON"))
    floor.open_dialog()
    assert floor.view.transform().m11() == pytest.approx(1.75)
    assert floor.state()["view_center"] == state["view_center"]


def test_detail_number_change_warns_without_rewriting_planned_assignments(floor, monkeypatch):
    dialog = MachineDetailDialog(floor, floor.machines[0])
    dialog.updated.number = "99999右"
    messages = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: messages.append(args[2]))
    dialog.accept()
    assert dialog.result() == QDialog.DialogCode.Accepted and messages
    assert "自動変更しません" in messages[0]
    assert floor.products()[0].machines == ["20000右"]
    assert floor.machines[0].number == "20000右"
    dialog.close()
