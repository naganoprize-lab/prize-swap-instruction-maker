"""Single, focus-aware shortcut route; editors retain their native text operations."""

from copy import deepcopy
import csv, io, json
from uuid import uuid4
from PySide6.QtCore import QObject, QEvent, Qt, QItemSelectionModel, QSignalBlocker
from PySide6.QtWidgets import (
    QApplication,
    QLineEdit,
    QTextEdit,
    QPlainTextEdit,
    QAbstractSpinBox,
    QAbstractItemView,
)
from .core import AppError, Product
from .table import ProductModel
from .instruction_editor import all_state, InstructionCommand


class AppShortcuts(QObject):
    def __init__(self, owner):
        super().__init__(owner)
        self.owner = owner
        QApplication.instance().installEventFilter(self)

    def eventFilter(self, obj, event):
        w = self.owner
        if (
            event.type() != QEvent.Type.KeyPress
            or not hasattr(obj, "parentWidget")
            or not (obj is w or w.isAncestorOf(obj) or w.floor.isAncestorOf(obj))
        ):
            return False
        if QApplication.activeModalWidget() is not None:
            return False
        key = event.key()
        mods = event.modifiers()
        ctrl = bool(mods & Qt.KeyboardModifier.ControlModifier)
        shift = bool(mods & Qt.KeyboardModifier.ShiftModifier)
        editing = isinstance(
            obj, (QLineEdit, QTextEdit, QPlainTextEdit, QAbstractSpinBox)
        ) or isinstance(
            QApplication.focusWidget(), (QLineEdit, QTextEdit, QPlainTextEdit, QAbstractSpinBox)
        )
        if editing:
            if not ctrl or key not in (Qt.Key.Key_S, Qt.Key.Key_O, Qt.Key.Key_P):
                return False
            if w.table.isAncestorOf(obj):
                w.table.setFocus()
            dock = w.floor.detail_controller
            if dock.panel and dock.panel.isAncestorOf(obj) and dock.panel.is_dirty():
                dock.panel.accept()
                if dock.panel.is_dirty():
                    return True
        floor = w.tabs.currentWidget() is w.floor or bool(
            w.floor._fullscreen_host and w.floor.isAncestorOf(obj)
        )
        instruction = w.tabs.currentWidget() is w.instruction_page
        stack = (
            w.floor.history
            if floor or w.tabs.currentWidget() is w.candidate_page
            else w.instruction_history
        )
        try:
            if ctrl and key == Qt.Key.Key_Z:
                (stack.redo if shift else stack.undo)()
                return True
            if ctrl and key == Qt.Key.Key_Y:
                stack.redo()
                return True
            if ctrl and key == Qt.Key.Key_S:
                (w.floor.save_dialog if floor else w.save_json)()
                return True
            if ctrl and key == Qt.Key.Key_O:
                (getattr(w, "import_excel_auto", None) or w.open_excel)()
                return True
            if ctrl and key == Qt.Key.Key_P:
                w.print_direct()
                return True
            if ctrl and key == Qt.Key.Key_F:
                w.page_search.setFocus()
                w.page_search.selectAll()
                return True
            if instruction and (obj is w.table or obj is w.table.viewport()):
                if ctrl and key == Qt.Key.Key_A:
                    w.table.selectAll()
                    return True
                if ctrl and key in (Qt.Key.Key_C, Qt.Key.Key_X):
                    self.copy_instructions()
                    if key == Qt.Key.Key_X:
                        self.cut_instructions()
                    return True
                if ctrl and key == Qt.Key.Key_V:
                    self.paste_instructions()
                    return True
                if key == Qt.Key.Key_F2 and w.table.currentIndex().isValid():
                    w.table.edit(w.table.currentIndex())
                    return True
                if key == Qt.Key.Key_Delete:
                    w.delete_instructions()
                    return True
                if key == Qt.Key.Key_Escape:
                    w.table.clearSelection()
                    return True
            if floor and ctrl and key in (Qt.Key.Key_C, Qt.Key.Key_X):
                selected = w.floor.selected()
                QApplication.clipboard().setText(
                    json.dumps(
                        {
                            "prize_floor_clipboard": [
                                dict(
                                    kind=m.kind,
                                    product=m.product,
                                    x=m.x,
                                    y=m.y,
                                    width=m.width,
                                    height=m.height,
                                    font_size=m.font_size,
                                )
                                for m in selected
                            ]
                        },
                        ensure_ascii=False,
                    )
                )
                if key == Qt.Key.Key_X:
                    w.floor.delete_selected()
                return True
            if floor and ctrl and key == Qt.Key.Key_V:
                from .floor_data import Machine

                data = json.loads(QApplication.clipboard().text())
                records = data.get("prize_floor_clipboard", [])
                if not isinstance(records, list) or len(records) > 5000:
                    raise AppError("貼り付けるマシン数が不正です。")
                before = w.floor.state()
                clones = [Machine(**(r | {"x": r["x"] + 20, "y": r["y"] + 20})) for r in records]
                from .floor_data import validate_machines
                from dataclasses import asdict

                validate_machines([asdict(m) for m in clones])
                w.floor.machines.extend(clones)
                w.floor.rebuild()
                w.floor.selection_state.set(m.uid for m in clones)
                w.floor.record(before, "マシン貼り付け")
                return True
        except (AppError, ValueError, TypeError, KeyError) as exc:
            w.statusBar().showMessage("操作できませんでした：" + str(exc), 10000)
            return True
        return False

    def copy_instructions(self):
        w = self.owner
        indexes = w.table.selectionModel().selectedIndexes()
        if not indexes:
            return
        rows = sorted({i.row() for i in indexes})
        cols = sorted({i.column() for i in indexes})
        out = io.StringIO()
        writer = csv.writer(out, delimiter="\t", lineterminator="\n")
        for row in rows:
            values = []
            for col in cols:
                index = w.proxy.index(row, col)
                if col in (0, 8):
                    value = (
                        "1"
                        if index.data(Qt.ItemDataRole.CheckStateRole) == Qt.CheckState.Checked
                        else "0"
                    )
                elif col == 7:
                    p = w.model.products[w.proxy.mapToSource(index).row()]
                    value = "、".join(p.methods)
                else:
                    value = index.data(Qt.ItemDataRole.EditRole)
                values.append("" if value is None else str(value))
            writer.writerow(values)
        QApplication.clipboard().setText(out.getvalue())

    def apply_cells(self, rows, cut=False):
        w = self.owner
        start = w.table.currentIndex()
        if not start.isValid():
            return
        before = all_state(w)
        after = deepcopy(before)
        byid = {p.entry_id: p for p in after[0]}
        targets = []
        for row in range(w.proxy.rowCount()):
            targets.append(
                w.model.products[w.proxy.mapToSource(w.proxy.index(row, 0)).row()].entry_id
            )
        stage = ProductModel()
        invalid = []
        stage.invalid.connect(invalid.append)
        for offset, values in enumerate(rows):
            row = start.row() + offset
            if row >= len(targets):
                p = Product(
                    w.selected_day(),
                    "",
                    source_type="manual",
                    source_method="貼り付け",
                    order=max([p.order for p in after[0]] + [0]) + 1,
                )
                after[0].append(p)
                byid[p.entry_id] = p
                targets.append(p.entry_id)
            p = byid[targets[row]]
            stage.replace([p])
            for offset_col, value in enumerate(values):
                col = start.column() + offset_col
                if col >= stage.columnCount():
                    break
                if cut and col == 1:
                    continue
                if col in (0, 8):
                    ok = stage.setData(
                        stage.index(0, col),
                        (
                            Qt.CheckState.Checked
                            if str(value).strip().casefold() in ("1", "true", "on", "yes", "✓")
                            else Qt.CheckState.Unchecked
                        ),
                        Qt.ItemDataRole.CheckStateRole,
                    )
                else:
                    ok = stage.setData(stage.index(0, col), value)
                if not ok:
                    raise AppError(invalid[-1] if invalid else "貼り付け値が不正です。")
        if not w.confirm_duplicate_instruction(
            [byid[targets[start.row() + r]] for r in range(len(rows))],
            {targets[start.row() + r] for r in range(len(rows))},
        ):
            return
        w.instruction_history.push(
            InstructionCommand(w, before, after, "指示を切り取り" if cut else "指示を貼り付け")
        )

    def paste_instructions(self):
        rows = list(csv.reader(io.StringIO(QApplication.clipboard().text()), delimiter="\t"))
        if len(rows) > 5000:
            raise AppError("貼り付けは5000行以下で指定してください。")
        self.apply_cells(rows)

    def cut_instructions(self):
        w = self.owner
        indexes = w.table.selectionModel().selectedIndexes()
        if not indexes:
            return
        rows = sorted({i.row() for i in indexes})
        cols = sorted({i.column() for i in indexes})
        w.table.setCurrentIndex(w.proxy.index(rows[0], cols[0]))
        self.apply_cells([["" for _ in cols] for _ in rows], cut=True)
