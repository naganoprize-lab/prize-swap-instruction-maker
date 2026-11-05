from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
import json
from pathlib import Path
import re

from PySide6.QtCore import QPointF, QRectF, QStandardPaths, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QKeySequence, QPainter, QPen, QShortcut, QUndoCommand, QUndoStack
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog,
    QFormLayout, QGraphicsItem, QGraphicsObject, QGraphicsRectItem, QGraphicsScene,
    QGraphicsView, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton, QVBoxLayout, QWidget)
from PySide6.QtPrintSupport import QPrintDialog, QPrinter

from .core import AppError, machine_numbers
from .floor_data import Machine, import_layout, save_layout, validate_machines, validate_state
from .printing import configured_printer
from .fonts import print_font

GRID = 20


class StateCommand(QUndoCommand):
    def __init__(self, widget, before, after, text):
        super().__init__(text)
        self.widget, self.before, self.after = widget, before, after
        self.first = True

    def undo(self):
        self.widget.restore(self.before, keep_history=True)
        self.widget.schedule_save()

    def redo(self):
        if self.first:
            self.first = False
        else:
            self.widget.restore(self.after, keep_history=True)
        self.widget.schedule_save()


class ResizeHandle(QGraphicsRectItem):
    def __init__(self, node, corner):
        super().__init__(-5, -5, 10, 10, node)
        self.node, self.corner = node, corner
        self.setBrush(QColor("#3D795C"))
        self.setPen(QPen(Qt.PenStyle.NoPen))
        self.setZValue(5)
        self.setCursor(Qt.CursorShape.SizeFDiagCursor if corner in (0, 3) else Qt.CursorShape.SizeBDiagCursor)

    def mousePressEvent(self, event):
        self.before = self.node.owner.state()
        m = self.node.machine
        self.initial = QRectF(m.x, m.y, m.width, m.height)
        event.accept()

    def mouseMoveEvent(self, event):
        point = event.scenePos()
        if self.node.owner.snap.isChecked():
            point = QPointF(round(point.x() / GRID) * GRID, round(point.y() / GRID) * GRID)
        rect = QRectF(self.initial)
        if self.corner in (0, 2):
            rect.setLeft(max(0, min(point.x(), rect.right() - 80)))
        else:
            rect.setRight(max(point.x(), rect.left() + 80))
        if self.corner in (0, 1):
            rect.setTop(max(0, min(point.y(), rect.bottom() - 60)))
        else:
            rect.setBottom(max(point.y(), rect.top() + 60))
        self.node.resize_to(rect)
        event.accept()

    def mouseReleaseEvent(self, event):
        self.node.owner.record(self.before, "マシンのサイズ変更")
        event.accept()


class MachineNode(QGraphicsObject):
    def __init__(self, owner, machine):
        super().__init__()
        self.owner, self.machine = owner, machine
        self.setPos(machine.x, machine.y)
        self.setFlags(QGraphicsItem.GraphicsItemFlag.ItemIsMovable | QGraphicsItem.GraphicsItemFlag.ItemIsSelectable | QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges)
        self.handles = [ResizeHandle(self, i) for i in range(4)]
        self.update_handles()

    def boundingRect(self):
        return QRectF(0, 0, self.machine.width, self.machine.height)

    def update_handles(self):
        m = self.machine
        for handle, point in zip(self.handles, [(0, 0), (m.width, 0), (0, m.height), (m.width, m.height)]):
            handle.setPos(*point)
            handle.setVisible(self.isSelected())
        plans = self.owner.assignments().get(m.number, [])
        self.setToolTip(f"{m.number or '番号未設定'} / {m.kind}\n{m.product}\n参照ID：{m.source_key or '追加マシン'}" + ("\n入替予定：" + "、".join(p.name for p in plans) if plans else ""))

    def paint(self, painter, option, widget=None):
        rect = self.boundingRect()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        plans = self.owner.assignments().get(self.machine.number, [])
        fill = "#FCE4EC" if any(p.priority for p in plans) else "#FFF4BF" if plans else "#FFFFFF"
        painter.setBrush(QColor(fill))
        painter.setPen(QPen(QColor("#3D795C" if self.isSelected() else "#8EA89A"), 2 if self.isSelected() else 1))
        painter.drawRoundedRect(rect, 6, 6)
        font = print_font(16)
        painter.setFont(font)
        painter.setPen(QColor("#244C3D"))
        label = self.machine.number or f"未設定 ({self.machine.source_key or '新規'})"
        painter.drawText(rect.adjusted(8, 6, -8, -rect.height() + 32), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, label)
        font = print_font(12, False)
        painter.setFont(font)
        painter.setPen(QColor("#263E32"))
        painter.drawText(rect.adjusted(8, 38, -8, -8), Qt.TextFlag.TextWordWrap | Qt.TextFlag.TextWrapAnywhere, self.machine.product or "商品未設定")

    def itemChange(self, change, value):
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionChange:
            x, y = max(0, value.x()), max(0, value.y())
            if self.owner.snap.isChecked():
                x, y = round(x / GRID) * GRID, round(y / GRID) * GRID
            return QPointF(x, y)
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionHasChanged:
            self.machine.x, self.machine.y = self.pos().x(), self.pos().y()
        if change == QGraphicsItem.GraphicsItemChange.ItemSelectedHasChanged:
            for h in self.handles:
                h.setVisible(bool(value))
        return super().itemChange(change, value)

    def resize_to(self, rect):
        self.prepareGeometryChange()
        self.machine.width, self.machine.height = rect.width(), rect.height()
        self.setPos(rect.topLeft())
        self.update_handles()
        self.update()

    def mousePressEvent(self, event):
        self.before = self.owner.state()
        self.ctrl_select = bool(event.modifiers() & Qt.KeyboardModifier.ControlModifier)
        if self.ctrl_select:
            self.setSelected(not self.isSelected())
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        if getattr(self, "ctrl_select", False):
            event.accept()
            return
        super().mouseReleaseEvent(event)
        if hasattr(self, "before"):
            self.owner.record(self.before, "マシンの移動")

    def mouseDoubleClickEvent(self, event):
        self.owner.edit_machine(self.machine.uid)
        event.accept()


class FloorView(QGraphicsView):
    def __init__(self, scene, owner):
        super().__init__(scene)
        self.owner = owner
        self.setDragMode(QGraphicsView.DragMode.RubberBandDrag)
        self.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing)
        self.setBackgroundBrush(QColor("#EFF3F0"))
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.BoundingRectViewportUpdate)

    def wheelEvent(self, event):
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            factor = 1.15 if event.angleDelta().y() > 0 else 1 / 1.15
            scale = self.transform().m11() * factor
            if .05 <= scale <= 4:
                self.scale(factor, factor)
            event.accept()
        else:
            super().wheelEvent(event)

    def drawBackground(self, painter, rect):
        super().drawBackground(painter, rect)
        if self.transform().m11() < .3:
            return
        painter.setPen(QPen(QColor("#CED9D1"), 0))
        step = GRID
        left, top = int(rect.left()) // step * step, int(rect.top()) // step * step
        # Limit visible-grid work; normal viewports require only a few thousand dots.
        for x in range(left, int(rect.right()) + step, step):
            for y in range(top, int(rect.bottom()) + step, step):
                painter.drawPoint(x, y)


class FloorWidget(QWidget):
    changed = Signal()
    message = Signal(str)

    def __init__(self, products, selected_day, storage_path=None, parent=None):
        super().__init__(parent)
        self.products, self.selected_day = products, selected_day
        self.machines, self.original = [], []
        self.source = ""
        self.initialized = False
        self._assignments = {}
        self.storage = Path(storage_path) if storage_path else Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation)) / "floor-autosave.json"
        self.history = QUndoStack(self)
        self.history.setUndoLimit(100)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.autosave)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        tools = QHBoxLayout()
        def tool(text, callback):
            b = QPushButton(text)
            b.clicked.connect(callback)
            tools.addWidget(b)
            return b
        tool("マシン追加", self.add_dialog)
        tool("削除", self.delete_selected)
        tool("複製", self.duplicate_selected)
        undo = QPushButton("元に戻す")
        undo.clicked.connect(self.history.undo)
        undo.setEnabled(False)
        self.history.canUndoChanged.connect(undo.setEnabled)
        tools.addWidget(undo)
        redo = QPushButton("やり直し")
        redo.clicked.connect(self.history.redo)
        redo.setEnabled(False)
        self.history.canRedoChanged.connect(redo.setEnabled)
        tools.addWidget(redo)
        self.align = QComboBox()
        self.align.addItems(["整列を選択", "左揃え", "上揃え", "横に等間隔", "縦に等間隔"])
        self.align.activated.connect(self.align_selected)
        tools.addWidget(self.align)
        tool("保存", self.save_dialog)
        tool("印刷", self.print_floor)
        layout.addLayout(tools)
        sources = QHBoxLayout()
        b = QPushButton("レイアウトExcel読込")
        b.clicked.connect(self.import_dialog)
        sources.addWidget(b)
        b = QPushButton("配置JSON読込")
        b.clicked.connect(self.open_dialog)
        sources.addWidget(b)
        b = QPushButton("元のExcel配置へ戻す")
        b.clicked.connect(self.reset_original)
        sources.addWidget(b)
        self.snap = QCheckBox("グリッドにスナップ")
        self.snap.setChecked(True)
        self.snap.toggled.connect(self.schedule_save)
        self.snap.toggled.connect(self.changed.emit)
        sources.addWidget(self.snap)
        sources.addStretch()
        b = QPushButton("全体表示")
        b.clicked.connect(self.fit)
        sources.addWidget(b)
        b = QPushButton("100%")
        b.clicked.connect(lambda: self.view.resetTransform())
        sources.addWidget(b)
        layout.addLayout(sources)
        self.info = QLabel("レイアウトExcelを読み込んで開始。Ctrl+ホイールで拡大縮小 / ダブルクリックで番号・商品を編集。")
        self.info.setWordWrap(True)
        layout.addWidget(self.info)
        self.scene = QGraphicsScene(self)
        self.view = FloorView(self.scene, self)
        layout.addWidget(self.view, 1)
        self.nodes = {}
        for key, callback in [("Delete", self.delete_selected), ("Ctrl+D", self.duplicate_selected), ("Ctrl+Z", self.history.undo), ("Ctrl+Y", self.history.redo)]:
            shortcut = QShortcut(QKeySequence(key), self)
            shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            shortcut.activated.connect(callback)
        if self.storage.exists():
            try:
                self.restore(json.loads(self.storage.read_text(encoding="utf-8")))
                self.info.setText(f"自動保存から{len(self.machines)}枠を復元しました。")
            except Exception as exc:
                self.info.setText(f"自動保存を復元できません：{exc}")

    def assignments(self):
        return self._assignments

    def refresh_assignments(self):
        result = {}
        for p in self.products():
            if p.day == self.selected_day() and p.included:
                for number in p.machines:
                    result.setdefault(number, []).append(p)
        self._assignments = result

    def related(self, numbers):
        return [p for p in self.products() if any(n in numbers for n in p.machines)]

    def state(self):
        return dict(version=1, initialized=self.initialized, source=self.source, machines=[asdict(m) for m in self.machines], original=deepcopy(self.original), snap=self.snap.isChecked())

    def restore(self, state, keep_history=False):
        validate_state(state)
        self.machines = validate_machines(state["machines"])
        self.original = deepcopy(state.get("original", []))
        self.source = state.get("source", "")
        self.initialized = state.get("initialized", bool(self.machines or self.original or self.source))
        self.snap.blockSignals(True)
        self.snap.setChecked(state.get("snap", True))
        self.snap.blockSignals(False)
        if not keep_history:
            self.history.clear()
        self.rebuild()
        self.changed.emit()

    def rebuild(self):
        self.refresh_assignments()
        self.scene.clear()
        self.nodes = {}
        for m in self.machines:
            node = MachineNode(self, m)
            self.nodes[m.uid] = node
            self.scene.addItem(node)
        self.update_scene_rect()
        self.update_info()

    def update_scene_rect(self):
        rect = self.scene.itemsBoundingRect().adjusted(-60, -60, 300, 300)
        self.scene.setSceneRect(rect if self.machines else QRectF(0, 0, 1500, 1000))

    def update_info(self):
        unresolved = sum(not m.number for m in self.machines)
        self.info.setText(f"{len(self.machines)}枠 ／ 番号未設定 {unresolved}枠　黄色：入替予定 ／ ピンク：優先入替　Ctrl+ホイール：拡大縮小 ／ ダブルクリック：編集")
        self.scene.update()

    def sync(self):
        self.refresh_assignments()
        for node in self.nodes.values():
            node.update_handles()
        self.scene.update()

    def record(self, before, text):
        after = self.state()
        if before != after:
            self.history.push(StateCommand(self, before, after, text))
            self.update_scene_rect()
            self.changed.emit()
            self.update_info()

    def schedule_save(self, *_):
        self.timer.start(800)
        if hasattr(self, "scene"):
            self.update_info()

    def autosave(self):
        try:
            self.storage.parent.mkdir(parents=True, exist_ok=True)
            save_layout(self.storage, self.state())
            self.message.emit("売り場レイアウトを自動保存しました。")
        except Exception as exc:
            self.message.emit(f"売り場レイアウトの自動保存に失敗しました：{exc}")

    def fit(self):
        if self.machines:
            self.view.fitInView(self.scene.itemsBoundingRect().adjusted(-20, -20, 20, 20), Qt.AspectRatioMode.KeepAspectRatio)

    def selected(self):
        return [item.machine for item in self.scene.selectedItems() if isinstance(item, MachineNode)]

    def add_machine(self, machine):
        validate_machines([asdict(m) for m in self.machines] + [asdict(machine)])
        before = self.state()
        self.initialized = True
        self.machines.append(machine)
        self.rebuild()
        self.nodes[machine.uid].setSelected(True)
        self.record(before, "マシン追加")
        self.view.centerOn(self.nodes[machine.uid])

    def machine_dialog(self, machine, title):
        dlg = QDialog(self)
        dlg.setWindowTitle(title)
        form = QFormLayout(dlg)
        number = QLineEdit()
        side = QComboBox()
        side.addItems(["指定なし", "左", "右"])
        match = re.fullmatch(r"(\d+)([左右]?)", machine.number)
        if match:
            number.setText(match[1])
            side.setCurrentText(match[2] or "指定なし")
        kind = QComboBox()
        kind.setEditable(True)
        kind.addItems(["クレーン", "2本爪", "3本爪", "その他"])
        kind.setCurrentText(machine.kind)
        product = QLineEdit(machine.product)
        error = QLabel()
        form.addRow("マシン番号（半角数字）", number)
        form.addRow("左右指定", side)
        form.addRow("マシン種別", kind)
        form.addRow("現在の商品名", product)
        form.addRow(error)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        form.addRow(buttons)
        def accept():
            value = number.text().strip() + (side.currentText() if side.currentIndex() else "")
            try:
                if not value or machine_numbers(value) != [value]:
                    raise AppError("番号を入力してください。")
                if any(m.uid != machine.uid and m.number == value for m in self.machines):
                    raise AppError("同じマシン番号が登録済みです。左右指定も確認してください。")
                machine.number, machine.kind, machine.product = value, kind.currentText(), product.text()
                dlg.accept()
            except AppError as exc:
                error.setText(str(exc))
        buttons.accepted.connect(accept)
        buttons.rejected.connect(dlg.reject)
        return dlg.exec() == QDialog.DialogCode.Accepted

    def add_dialog(self):
        center = self.view.mapToScene(self.view.viewport().rect().center())
        m = Machine(x=max(0, round(center.x()/GRID)*GRID), y=max(0, round(center.y()/GRID)*GRID))
        if self.machine_dialog(m, "マシン追加"):
            self.add_machine(m)

    def edit_machine(self, uid):
        old = next(m for m in self.machines if m.uid == uid)
        updated = deepcopy(old)
        if not self.machine_dialog(updated, "マシン編集"):
            return
        if old.number and old.number != updated.number and self.related({old.number}):
            QMessageBox.warning(self, "入替予定との整合性", "変更前のマシン番号が入替予定で使われています。予定の番号は自動変更しません。商品一覧で変更先を確認・修正してください。")
        before = self.state()
        self.machines[self.machines.index(old)] = updated
        self.rebuild()
        self.record(before, "マシン編集")

    def delete_selected(self):
        selected = self.selected()
        if not selected:
            return
        used = self.related({m.number for m in selected if m.number})
        warning = f"\n注意：{len(used)}件の入替予定で使用中です。予定のマシン番号は残ります。商品一覧で修正してください。" if used else ""
        if QMessageBox.question(self, "マシン削除", f"選択した{len(selected)}枠を削除しますか？元Excelは変更しません。{warning}") != QMessageBox.StandardButton.Yes:
            return
        before = self.state()
        ids = {m.uid for m in selected}
        self.machines = [m for m in self.machines if m.uid not in ids]
        self.rebuild()
        self.record(before, "マシン削除")
        if used:
            self.message.emit("削除したマシンが入替予定に残っています。商品一覧で確認してください。")

    def duplicate_selected(self):
        selected = self.selected()
        if not selected:
            return
        before = self.state()
        clones = [Machine(kind=m.kind, product=m.product, x=m.x + GRID, y=m.y + GRID, width=m.width, height=m.height) for m in selected]
        self.machines.extend(clones)
        self.rebuild()
        for m in clones:
            self.nodes[m.uid].setSelected(True)
        self.record(before, "マシン複製")
        self.message.emit("複製しました。番号は未設定です。ダブルクリックで新しい番号を登録してください。")

    def align_selected(self, mode):
        self.align.setCurrentIndex(0)
        selected = self.selected()
        if mode == 0 or len(selected) < 2:
            return
        before = self.state()
        if mode in (1, 2):
            value = min(m.x if mode == 1 else m.y for m in selected)
            for m in selected:
                if mode == 1: m.x = value
                else: m.y = value
        elif mode in (3, 4) and len(selected) >= 3:
            axis, size = ("x", "width") if mode == 3 else ("y", "height")
            selected.sort(key=lambda m: getattr(m, axis))
            start = getattr(selected[0], axis)
            end = getattr(selected[-1], axis) + getattr(selected[-1], size)
            gap = (end - start - sum(getattr(m, size) for m in selected)) / (len(selected) - 1)
            if gap < 0:
                self.message.emit("重なるマシンは等間隔にできません。先に両端の間隔を広げてください。")
                return
            pos = start
            for m in selected:
                setattr(m, axis, pos)
                pos += getattr(m, size) + gap
        self.rebuild()
        self.record(before, "マシン整列")

    def import_path(self, path, preserve=False):
        machines, warnings = import_layout(path)
        before = self.state()
        self.initialized = True
        self.source = str(path)
        self.original = [asdict(m) for m in machines]
        if not preserve:
            self.machines = machines
        self.rebuild()
        self.record(before, "レイアウトExcel読込")
        self.fit()
        self.message.emit(f"{len(machines)}枠を読み込みました。" + " ".join(warnings))
        return warnings

    def import_dialog(self):
        path, _ = QFileDialog.getOpenFileName(self, "レイアウトExcel", "", "Excel (*.xlsx *.xlsm)")
        if not path:
            return
        preserve = False
        if self.machines:
            result = QMessageBox.question(self, "配置の維持", "現在の編集済み配置を維持しますか？\n「はい」：現在の配置を保持し、元配置だけ更新\n「いいえ」：Excelの配置に置換", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No | QMessageBox.StandardButton.Cancel)
            if result == QMessageBox.StandardButton.Cancel:
                return
            preserve = result == QMessageBox.StandardButton.Yes
        try:
            warnings = self.import_path(path, preserve)
            if warnings:
                QMessageBox.warning(self, "レイアウト読込時の確認", "\n".join(warnings))
        except Exception as exc:
            QMessageBox.warning(self, "レイアウト読込エラー", str(exc))

    def reset_original(self):
        if not self.original:
            return
        if QMessageBox.question(self, "元の配置に戻す", "追加・削除・移動した配置を元のExcel配置に戻しますか？") != QMessageBox.StandardButton.Yes:
            return
        before = self.state()
        self.machines = validate_machines(self.original)
        self.rebuild()
        self.record(before, "元のExcel配置へ戻す")
        self.fit()

    def save_dialog(self):
        path, _ = QFileDialog.getSaveFileName(self, "配置を保存", "売り場レイアウト.json", "JSON (*.json)")
        if path:
            if not path.lower().endswith(".json"):
                path += ".json"
                if Path(path).exists() and QMessageBox.question(self, "上書き確認", "既存の配置JSONを上書きしますか？") != QMessageBox.StandardButton.Yes:
                    return
            try:
                save_layout(path, self.state())
                self.message.emit("配置を保存しました。")
            except Exception as exc:
                QMessageBox.warning(self, "保存エラー", str(exc))

    def open_dialog(self):
        path, _ = QFileDialog.getOpenFileName(self, "配置JSONを開く", "", "JSON (*.json)")
        if path:
            try:
                state = json.loads(Path(path).read_text(encoding="utf-8"))
                validate_state(state)
                before = self.state()
                self.restore(state, keep_history=True)
                self.record(before, "配置JSON読込")
                self.fit()
            except Exception as exc:
                QMessageBox.warning(self, "復元エラー", str(exc))

    def paint_floor(self, printer):
        painter = QPainter()
        if not painter.begin(printer):
            self.message.emit("レイアウトを印刷できません。")
            return
        selected = [m.uid for m in self.selected()]
        self.scene.clearSelection()
        try:
            rect = printer.pageRect(QPrinter.Unit.DevicePixel)
            self.scene.render(painter, QRectF(0, 0, rect.width(), rect.height()), self.scene.itemsBoundingRect().adjusted(-10, -10, 10, 10))
        finally:
            painter.end()
            for uid in selected:
                self.nodes[uid].setSelected(True)

    def print_floor(self):
        if not self.machines:
            self.message.emit("印刷するマシン配置がありません。")
            return
        printer = configured_printer()
        dialog = QPrintDialog(printer, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.paint_floor(printer)
