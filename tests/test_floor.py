from copy import deepcopy
from dataclasses import asdict
from datetime import date
import hashlib
import json

import pytest
from openpyxl import Workbook
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QImage, QPainter
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QMessageBox, QApplication
from PySide6.QtPdf import QPdfDocument

from prize_swap.pdf_validation import require_searchable_pdf
from prize_swap.core import AppError, Product, load_session, save_session
from prize_swap.floor import FloorWidget, MachineNode
from prize_swap.floor_data import Machine, import_layout, save_layout, validate_state
from prize_swap.printing import configured_printer

DAY = date(2026, 11, 5)


@pytest.fixture
def layout_excel(tmp_path):
    path = tmp_path / "配置参照.xlsx"
    wb = Workbook()
    ws = wb.active
    ws.title = "投入中レイアウト"
    ws["C9"] = "L_66"
    ws["D9"] = "現在の商品A"
    ws["F9"] = "R_66"
    ws["G9"] = "現在の商品B"
    ws["C20"] = "L_999"
    ws["D20"] = "対応未登録"
    ws["C30"] = "L_66"  # duplicate reference ID must not become a duplicate number
    ws["D30"] = "重複枠"
    ws["C10"] = "売上/アベレージ"
    ws["D10"] = 123456
    mapping = wb.create_sheet("管理番号参考値")
    mapping.append(["L_66", "20111左"])
    mapping.append(["R_66", "20111右"])
    wb.save(path)
    wb.close()
    return path


@pytest.fixture
def floor(tmp_path):
    products = [Product(DAY, "入替予定A", machines=["20111左"], included=True)]
    widget = FloorWidget(lambda: products, lambda: DAY, tmp_path / "自動保存.json")
    widget.resize(1000, 700)
    widget.show()
    QApplication.processEvents()
    yield widget
    widget.timer.stop()
    widget.close()


def test_excel_import_preserves_source_and_unknown_ids(layout_excel):
    before = hashlib.sha256(layout_excel.read_bytes()).hexdigest()
    machines, warnings = import_layout(layout_excel)
    assert len(machines) == 4
    assert [m.number for m in machines] == ["20111左", "20111右", "", ""]
    assert machines[0].product == "現在の商品A"
    assert machines[1].x > machines[0].x
    assert machines[2].y > machines[0].y
    assert not any("売上" in m.product for m in machines)
    assert len(warnings) == 2
    assert hashlib.sha256(layout_excel.read_bytes()).hexdigest() == before


def test_add_duplicate_delete_warning_undo_redo(floor, monkeypatch):
    a = Machine(number="20111左", x=100, y=100, product="商品A")
    floor.add_machine(a)
    with pytest.raises(AppError):
        floor.add_machine(Machine(number="20111左"))
    floor.duplicate_selected()
    assert len(floor.machines) == 2
    clone = floor.machines[-1]
    assert clone.number == "" and clone.x == a.x + 20
    assert clone.uid != a.uid
    floor.history.undo()
    assert len(floor.machines) == 1
    floor.history.redo()
    assert len(floor.machines) == 2
    for node in floor.nodes.values():
        node.setSelected(True)
    messages = []

    def reject(*args):
        messages.append(args[2])
        return QMessageBox.StandardButton.No

    monkeypatch.setattr(QMessageBox, "question", reject)
    floor.delete_selected()
    assert len(floor.machines) == 2
    assert "入替予定" in messages[0] and "使用中" in messages[0]
    monkeypatch.setattr(QMessageBox, "question", lambda *a: QMessageBox.StandardButton.Yes)
    floor.delete_selected()
    assert len(floor.machines) == 0
    assert floor.products()[0].machines == ["20111左"]
    floor.history.undo()
    assert len(floor.machines) == 2


def test_actual_drag_multiple_selection_resize_and_undo(floor):
    floor.edit_mode.setChecked(True)  # Viewing clicks now open details; dragging is explicit.
    a = Machine(number="100左", x=100, y=100)
    b = Machine(number="101右", x=360, y=100)
    floor.add_machine(a)
    floor.add_machine(b)
    floor.scene.clearSelection()
    floor.view.resetTransform()
    floor.view.centerOn(350, 250)
    QApplication.processEvents()
    viewport = floor.view.viewport()
    start = floor.view.mapFromScene(QPointF(150, 145))
    second = floor.view.mapFromScene(QPointF(410, 145))
    QTest.mouseClick(viewport, Qt.MouseButton.LeftButton, pos=start)
    QTest.mouseClick(
        viewport, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.ControlModifier, pos=second
    )
    assert len(floor.selected()) == 2
    end = start + floor.view.mapFromScene(QPointF(60, 40)) - floor.view.mapFromScene(QPointF(0, 0))
    QTest.mousePress(viewport, Qt.MouseButton.LeftButton, pos=start)
    QTest.mouseMove(viewport, end, 30)
    QTest.mouseRelease(viewport, Qt.MouseButton.LeftButton, pos=end)
    assert a.x == 160 and a.y == 140
    assert b.x == 420 and b.y == 140
    floor.history.undo()
    assert floor.machines[0].x == 100 and floor.machines[1].x == 360
    floor.history.redo()
    node = floor.nodes[a.uid]
    floor.scene.clearSelection()
    node.setSelected(True)
    # Keep the resize target inside the resized modern viewport, excluding
    # QGraphicsView edge autoscroll from this fixed-distance resize assertion.
    floor.view.ensureVisible(
        node.mapRectToScene(node.boundingRect()).adjusted(0, 0, 80, 80), 20, 20
    )
    QApplication.processEvents()
    corner = floor.view.mapFromScene(
        node.mapToScene(QPointF(node.machine.width, node.machine.height))
    )
    target = (
        corner + floor.view.mapFromScene(QPointF(40, 40)) - floor.view.mapFromScene(QPointF(0, 0))
    )
    old_width, old_height = node.machine.width, node.machine.height
    QTest.mousePress(viewport, Qt.MouseButton.LeftButton, pos=corner)
    QTest.mouseMove(viewport, target, 30)
    QTest.mouseRelease(viewport, Qt.MouseButton.LeftButton, pos=target)
    assert floor.machines[0].width == old_width + 40
    assert floor.machines[0].height == old_height + 40
    floor.history.undo()
    assert floor.machines[0].width == old_width


def test_alignment_and_equal_gaps(floor):
    for i in range(3):
        floor.add_machine(Machine(number=str(i + 1), x=i * 300, y=i * 100, width=100 + i * 20))
    for node in floor.nodes.values():
        node.setSelected(True)
    floor.align_selected(2)
    assert len({m.y for m in floor.machines}) == 1
    for node in floor.nodes.values():
        node.setSelected(True)
    floor.align_selected(3)
    ordered = sorted(floor.machines, key=lambda m: m.x)
    gaps = [b.x - a.x - a.width for a, b in zip(ordered, ordered[1:])]
    assert abs(gaps[0] - gaps[1]) < 0.001


def test_autosave_manual_session_restore_and_reset(floor, layout_excel, tmp_path, monkeypatch):
    floor.import_path(layout_excel)
    imported = floor.state()
    floor.add_machine(Machine(number="999右", x=500, y=500))
    edited = floor.state()
    floor.autosave()
    restored = FloorWidget(lambda: [], lambda: DAY, floor.storage)
    assert restored.state() == edited
    restored.timer.stop()
    restored.close()
    path = tmp_path / "手動.json"
    save_layout(path, edited)
    assert json.loads(path.read_text(encoding="utf-8")) == edited
    session = tmp_path / "商品と配置.json"
    save_session(session, floor.products(), DAY, "", floor_layout=edited)
    assert load_session(session)[2]["floor_layout"] == edited
    floor.import_path(layout_excel, preserve=True)
    assert [m.number for m in floor.machines][-1] == "999右"
    monkeypatch.setattr(QMessageBox, "question", lambda *a: QMessageBox.StandardButton.Yes)
    floor.reset_original()
    assert len(floor.machines) == 4
    assert floor.machines[0].x == imported["machines"][0]["x"]
    floor.history.undo()
    assert len(floor.machines) == 5


def test_floor_colors_and_pdf(floor, tmp_path):
    floor.set_color_filter("planned")
    m = Machine(number="20111左", x=0, y=0, product="現在の商品")
    floor.add_machine(m)
    floor.scene.clearSelection()

    def pixel():
        image = QImage(180, 120, QImage.Format.Format_ARGB32)
        image.fill(Qt.GlobalColor.white)
        painter = QPainter(image)
        floor.scene.render(painter, QRectF(0, 0, 180, 120), QRectF(0, 0, 180, 120))
        painter.end()
        return image.pixelColor(170, 110).name()

    assert pixel() == "#a9d7ff"
    floor.products()[0].priority = True
    floor.sync()
    assert pixel() == "#a9d7ff"  # Priority uses a yellow border; planned blue stays dominant.
    path = tmp_path / "配置図.pdf"
    floor.paint_floor(configured_printer(path))
    doc = QPdfDocument()
    assert doc.load(str(path)) == QPdfDocument.Error.None_
    assert doc.pageCount() == 1
    assert "20111" in doc.getAllText(0).text()
    independent = require_searchable_pdf(path)
    independent_text = "".join(independent["pages"][0]["text"].split())
    assert "20111" in independent_text
    assert "商品" in independent_text
    doc.close()


def test_invalid_floor_json(floor):
    state = floor.state()
    state["machines"] = [asdict(Machine(number="1", x=float("inf")))]
    with pytest.raises(AppError):
        validate_state(state)
    with pytest.raises(AppError):
        validate_state({"version": 1})


def test_keyboard_duplicate_delete_and_automatic_save(floor, monkeypatch):
    floor.add_machine(Machine(number="500左", x=100, y=100))
    floor.activateWindow()
    floor.view.setFocus()
    QApplication.processEvents()
    QTest.keyClick(floor.view.viewport(), Qt.Key.Key_D, Qt.KeyboardModifier.ControlModifier)
    assert len(floor.machines) == 2
    monkeypatch.setattr(QMessageBox, "question", lambda *a: QMessageBox.StandardButton.Yes)
    QTest.keyClick(floor.view.viewport(), Qt.Key.Key_Delete)
    assert len(floor.machines) == 1
    QTest.keyClick(floor.view.viewport(), Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
    assert len(floor.machines) == 2
    QTest.keyClick(floor.view.viewport(), Qt.Key.Key_Y, Qt.KeyboardModifier.ControlModifier)
    assert len(floor.machines) == 1
    QTest.qWait(900)
    assert floor.storage.exists()
    assert len(json.loads(floor.storage.read_text(encoding="utf-8"))["machines"]) == 1


def test_number_change_warns_and_retains_plan_for_review(floor, monkeypatch):
    machine = Machine(number="20111左")
    floor.add_machine(machine)
    warnings = []

    def edit(updated, title):
        updated.number = "30000右"
        return True

    monkeypatch.setattr(floor, "machine_dialog", edit)
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: warnings.append(args[2]))
    floor.edit_machine(machine.uid)
    assert floor.machines[0].number == "30000右"
    assert floor.products()[0].machines == ["20111左"]
    assert warnings and "入替予定" in warnings[0]
    floor.history.undo()
    assert floor.machines[0].number == "20111左"
