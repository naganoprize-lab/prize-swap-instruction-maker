"""CRUD and merge controls for the original Excel-like instruction table."""

from copy import deepcopy
from dataclasses import asdict, fields
from pathlib import Path
from uuid import uuid4
from PySide6.QtCore import Qt, QDate, QEvent, QTimer
from PySide6.QtGui import QUndoCommand, QUndoStack, QKeySequence
from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QFormLayout,
    QScrollArea,
    QWidget,
    QLineEdit,
    QComboBox,
    QPlainTextEdit,
    QDateEdit,
    QCheckBox,
    QDialogButtonBox,
    QMessageBox,
    QToolButton,
    QMenu,
    QInputDialog,
    QTableWidget,
    QTableWidgetItem,
    QAbstractItemView,
)
from .core import Product, AppError, LIMITS, quantity, machine_numbers, save_session, load_session
from .methods import editor_state, set_allocations
from .methods_editor import MethodsCombo
from .instructions import prepare_entry, mark_edited, IMPORT_FIELDS, SOURCE_LABELS, merge_import
from .machine_matching import normalize_reference


def all_state(w):
    return deepcopy(w.products), deepcopy(w.labels.state())


class InstructionCommand(QUndoCommand):
    def __init__(self, w, before, after, label, already_applied=False):
        super().__init__(label)
        self.w = w
        self.before = before
        self.after = after
        self.first = already_applied

    def apply(self, state):
        from .labels_data import LabelStore

        w = self.w
        w._instruction_applying = True
        current = {p.entry_id: p for p in w.products}
        products = []
        for saved in state[0]:
            p = current.get(saved.entry_id, deepcopy(saved))
            for f in fields(Product):
                setattr(p, f.name, deepcopy(getattr(saved, f.name)))
            products.append(p)
        w.products = products
        w.labels.store = LabelStore.restore(state[1], products)
        w.update_dates()
        w.refresh()
        w._instruction_applying = False
        w.mark_dirty()

    def undo(self):
        self.apply(self.before)

    def redo(self):
        if self.first:
            self.first = False
        else:
            self.apply(self.after)


class InstructionDialog(QDialog):
    def __init__(self, w, product):
        super().__init__(w)
        self.w = w
        self.original = product
        self.draft = deepcopy(product)
        self.setWindowTitle("指示の詳細編集" if product in w.products else "手動で指示を追加")
        available = w.screen().availableGeometry()
        self.resize(min(680, available.width() - 30), min(760, available.height() - 50))
        outer = QVBoxLayout(self)
        area = QScrollArea()
        area.setWidgetResizable(True)
        body = QWidget()
        form = QFormLayout(body)
        area.setWidget(body)
        outer.addWidget(area)
        self.day = QDateEdit(QDate(product.day.year, product.day.month, product.day.day))
        self.day.setCalendarPopup(True)
        form.addRow("登録日", self.day)
        self.name = QComboBox()
        self.name.setEditable(True)
        self.name.addItem("")
        for p in w.products:
            if p.name and self.name.findText(p.name) < 0:
                self.name.addItem(p.name)
        self.name.setCurrentText(product.name)
        self.name.lineEdit().setPlaceholderText("空欄可／自由入力／入荷予定表から選択")
        form.addRow("新しく投入する商品名", self.name)
        self.maker = QLineEdit(product.maker)
        form.addRow("メーカー", self.maker)
        self.units = QLineEdit(str(product.units))
        self.cases = QLineEdit(str(product.cases))
        form.addRow("入数（空欄可）", self.units)
        form.addRow("CS数（空欄可）", self.cases)
        self.machines = QPlainTextEdit("\n".join(product.machines))
        self.machines.setMaximumHeight(100)
        form.addRow("マシン番号／管理番号", self.machines)
        self.management = QLineEdit("、".join(product.management_numbers))
        form.addRow("管理ナンバー（別途保持）", self.management)
        self.methods = MethodsCombo()
        self.methods.set_allocations(editor_state(product), product.machines)
        form.addRow("運営方法・予定数・割当", self.methods)
        self.limit = QComboBox()
        self.limit.addItems(LIMITS)
        self.limit.setCurrentText(product.limit)
        form.addRow("獲得個数制限", self.limit)
        self.priority = QCheckBox("優先入替")
        self.priority.setChecked(product.priority)
        form.addRow(self.priority)
        self.included = QCheckBox("出力対象（商品名が空欄でも印刷できます）")
        self.included.setChecked(product.included)
        form.addRow(self.included)
        self.notes = QPlainTextEdit(product.notes)
        self.notes.setMaximumHeight(95)
        form.addRow("備考", self.notes)
        self.order = QLineEdit(str(product.order))
        form.addRow("順序", self.order)
        from PySide6.QtWidgets import QLabel

        form.addRow(
            "追加元",
            QLabel(
                SOURCE_LABELS.get(product.source_type, "自動作成") + " / " + product.source_method
            ),
        )
        current = (product.candidate_assignment or {}).get("current_product_name", "")
        if current:
            label = QLabel(current)
            label.setWordWrap(True)
            form.addRow("現在設置中の商品（参考）", label)
        self.error = QLabel()
        self.error.setWordWrap(True)
        outer.addWidget(self.error)
        self.name.activated.connect(self.choose_product)
        self.machines.textChanged.connect(self.refresh_methods)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        outer.addWidget(buttons)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

    def choose_product(self, *_):
        p = next((p for p in self.w.products if p.name == self.name.currentText() and p.name), None)
        if p:
            self.maker.setText(p.maker)
            self.units.setText(str(p.units))
            self.cases.setText(str(p.cases))

    def refresh_methods(self):
        try:
            self.methods.set_allocations(
                self.methods.state(), machine_numbers(self.machines.toPlainText())
            )
        except AppError:
            pass

    def accept(self):
        try:
            p = self.draft
            p.day = self.day.date().toPython()
            p.name = self.name.currentText().strip()
            p.maker = self.maker.text().strip()
            p.units = quantity(self.units.text())
            p.cases = quantity(self.cases.text())
            p.machines = machine_numbers(self.machines.toPlainText())
            p.management_numbers = []
            for token in self.management.text().replace("、", ",").split(","):
                if not token.strip():
                    continue
                key = normalize_reference(token)
                if not key:
                    raise AppError("管理ナンバーはL_66／R_168の形式で入力してください。")
                if key not in p.management_numbers:
                    p.management_numbers.append(key)
            if not p.machines:
                p.machines = list(p.management_numbers)
            p.limit = self.limit.currentText()
            p.priority = self.priority.isChecked()
            p.included = self.included.isChecked()
            p.notes = self.notes.toPlainText()
            p.order = int(self.order.text())
            set_allocations(p, self.methods.state())
            prepare_entry(p)
            changed = [
                f.name
                for f in fields(Product)
                if f.name
                in __import__(
                    "prize_swap.instructions", fromlist=["EDITABLE_FIELDS"]
                ).EDITABLE_FIELDS
                and getattr(p, f.name) != getattr(self.original, f.name)
            ]
            mark_edited(p, changed)
            if not self.w.confirm_duplicate_instruction([p], {self.original.entry_id}):
                return
            super().accept()
        except (ValueError, TypeError) as exc:
            self.error.setText(str(exc))


class InstructionEditorMixin:
    def setup_instruction_editing(self, controls):
        self.instruction_history = QUndoStack(self)
        self.instruction_history.setUndoLimit(100)
        self._instruction_applying = False
        self._loading_instructions = False
        self.model.edit_requested.connect(self.record_instruction_cell_edit)
        edit = QToolButton()
        edit.setText("指示の編集")
        menu = QMenu(edit)
        for name, callback in [
            ("＋ 手動追加", self.add_instruction),
            ("詳細編集", self.edit_instruction),
            ("複製", self.duplicate_instructions),
            ("削除", self.delete_instructions),
        ]:
            menu.addAction(name, callback)
        menu.addSeparator()
        menu.addAction(self.instruction_history.createUndoAction(self, "元に戻す"))
        menu.addAction(self.instruction_history.createRedoAction(self, "やり直し"))
        edit.setMenu(menu)
        edit.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        controls.addWidget(edit)
        self.instruction_edit_button = edit
        self.table.installEventFilter(self)
        self.table.viewport().installEventFilter(self)
        self.instruction_autosave_path = self.floor.storage.with_name(
            self.floor.storage.stem + "-instructions-autosave.json"
        )
        self.instruction_save_timer = QTimer(self)
        self.instruction_save_timer.setSingleShot(True)
        self.instruction_save_timer.timeout.connect(self.autosave_instructions)

    def ask_output_selection(self):
        dialog = QMessageBox(self)
        dialog.setWindowTitle("出力対象を選択")
        dialog.setText(
            "現在の登録日・表示範囲に出力対象の指示がありません。空欄の指示も出力対象にできます。"
        )
        dialog.setStandardButtons(
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel
        )
        dialog.button(QMessageBox.StandardButton.Yes).setText("出力対象を全選択")
        dialog.button(QMessageBox.StandardButton.Cancel).setText("キャンセル")
        return dialog.exec() == QMessageBox.StandardButton.Yes

    def record_instruction_cell_edit(self, p, before, after):
        if self._instruction_applying:
            return
        old, labels = all_state(self)
        old = [deepcopy(before) if v.entry_id == p.entry_id else v for v in old]
        self.instruction_history.push(
            InstructionCommand(
                self, (old, labels), all_state(self), "指示のセル編集", already_applied=True
            )
        )

    def confirm_duplicate_instruction(self, entries, ignore=()):
        from .instructions import booths

        others = [p for p in self.products if p.entry_id not in ignore]
        seen = {
            b.machine.uid if b.machine else b.token
            for b in booths(
                others, self.floor.machines, self.floor.number_references, all_entries=True
            )
            if b.product.day in {p.day for p in entries} and (b.machine or b.token)
        }
        conflicts = []
        for b in booths(
            entries, self.floor.machines, self.floor.number_references, all_entries=True
        ):
            key = b.machine.uid if b.machine else b.token
            if key and key in seen:
                conflicts.append(b.token or key)
        return (
            not conflicts
            or QMessageBox.question(
                self,
                "同じブースへの複数指示",
                "既存の指示とマシンが重複します："
                + "、".join(conflicts)
                + "\n意図的に別の指示として登録しますか？",
            )
            == QMessageBox.StandardButton.Yes
        )

    def add_instruction(self, *_):
        p = Product(
            self.selected_day(),
            "",
            order=max([v.order for v in self.products] + [0]) + 1,
            source_type="manual",
            source_method="手動フォーム",
            included=True,
        )
        prepare_entry(p)
        d = InstructionDialog(self, p)
        if d.exec() != QDialog.DialogCode.Accepted:
            return False
        before = all_state(self)
        self.products.append(d.draft)
        self.instruction_history.push(
            InstructionCommand(self, before, all_state(self), "指示を手動追加", True)
        )
        self.update_dates()
        self.refresh()
        self.mark_dirty()
        return True

    def edit_instruction(self, *_):
        selected = self.selected_products()
        if not selected:
            return False
        p = selected[0]
        d = InstructionDialog(self, p)
        if d.exec() != QDialog.DialogCode.Accepted:
            return False
        before = all_state(self)
        for f in fields(Product):
            setattr(p, f.name, deepcopy(getattr(d.draft, f.name)))
        self.instruction_history.push(
            InstructionCommand(self, before, all_state(self), "指示の詳細編集", True)
        )
        self.update_dates()
        self.refresh()
        self.mark_dirty()
        return True

    def duplicate_instructions(self, *_):
        selected = self.selected_products()
        if not selected or not self.confirm_duplicate_instruction(selected):
            return False
        before = all_state(self)
        copies = []
        base = max([p.order for p in self.products] + [0])
        for i, p in enumerate(selected):
            new = deepcopy(p)
            new.entry_id = uuid4().hex
            new.order = base + i + 1
            new.source_type = "manual"
            new.source_method = "複製：" + p.entry_id
            new.included = True
            new.created_at = ""
            new.updated_at = ""
            for link in new.booth_assignments:
                link["assignment_id"] = uuid4().hex
            if new.candidate_assignment:
                new.candidate_assignment["assignment_id"] = uuid4().hex
            prepare_entry(new)
            copies.append(new)
        self.products.extend(copies)
        self.instruction_history.push(
            InstructionCommand(self, before, all_state(self), "指示を複製", True)
        )
        self.refresh()
        self.mark_dirty()
        return True

    def delete_instructions(self, *_):
        selected = self.selected_products()
        if not selected:
            return False
        if (
            QMessageBox.question(self, "指示を削除", f"選択中の{len(selected)}件を削除しますか？")
            != QMessageBox.StandardButton.Yes
        ):
            return False
        before = all_state(self)
        ids = {p.entry_id for p in selected}
        self.products = [p for p in self.products if p.entry_id not in ids]
        self.instruction_history.push(
            InstructionCommand(self, before, all_state(self), "指示を削除", True)
        )
        self.update_dates()
        self.refresh()
        self.mark_dirty()
        return True

    def instruction_key_event(self, obj, event):
        if (
            obj not in (self.table, self.table.viewport())
            or event.type() != QEvent.Type.KeyPress
            or self.table.state() == QAbstractItemView.State.EditingState
        ):
            return False
        if event.key() == Qt.Key.Key_Delete:
            self.delete_instructions()
            return True
        if event.matches(QKeySequence.StandardKey.Undo):
            self.instruction_history.undo()
            return True
        if event.matches(QKeySequence.StandardKey.Redo):
            self.instruction_history.redo()
            return True
        return False

    def merge_instruction_excel(self, products, policy="manual", field_choices=None):
        before = all_state(self)
        self.products, conflicts = merge_import(self.products, products, policy, field_choices)
        self.instruction_history.push(
            InstructionCommand(self, before, all_state(self), "Excel更新を指示へ反映", True)
        )
        self.refresh()
        self.mark_dirty()
        return conflicts

    def setup_instruction_map(self, list_layout):
        from PySide6.QtWidgets import QTabWidget, QLabel, QHBoxLayout
        from .instruction_map_ui import InstructionMapPage

        list_layout.removeWidget(self.list_card)
        self.list_card.layout().setContentsMargins(8, 2, 8, 2)
        self.list_card.layout().setSpacing(0)
        self.instruction_tabs = QTabWidget()
        self.instruction_tabs.setObjectName("instructionTabs")
        self.instruction_tabs.setStyleSheet(
            "QTabWidget#instructionTabs::pane {border: 0;} QTabWidget#instructionTabs QTabBar::tab {padding: 3px 8px; min-height: 18px;}"
        )
        self.instruction_tabs.addTab(self.list_card, "指示書編集")
        self.instruction_map = InstructionMapPage(self)
        self.instruction_tabs.addTab(self.instruction_map, "入替予定地図")
        list_layout.addWidget(self.instruction_tabs, 1)
        corner = QWidget()
        row = QHBoxLayout(corner)
        row.setContentsMargins(0, 0, 0, 0)
        self.instruction_status = QLabel()
        row.addWidget(self.instruction_status)
        self.instruction_output_mode = QComboBox()
        self.instruction_output_mode.setFixedHeight(24)
        for text, key in [
            ("指示書のみ印刷", "instructions"),
            ("地図のみ印刷", "map"),
            ("指示書＋地図", "both"),
        ]:
            self.instruction_output_mode.addItem(text, key)
        self.instruction_output_mode.setCurrentIndex(
            self.instruction_output_mode.findData(self.floor.instruction_print_options["mode"])
        )
        self.instruction_output_mode.currentIndexChanged.connect(self.instruction_output_changed)
        row.addWidget(self.instruction_output_mode)
        self.instruction_tabs.setCornerWidget(corner)
        self.table.selectionModel().selectionChanged.connect(
            self.instruction_map.highlight_selection
        )
        self.model.edited.connect(self.instruction_map.refresh)
        self.update_instruction_status()

    def instruction_output_changed(self, *_):
        self.floor.instruction_print_options["mode"] = self.instruction_output_mode.currentData()
        self.floor.schedule_save()
        self.mark_dirty()

    def update_instruction_status(self):
        from .instructions import booths

        refs = booths(
            self.visible, self.floor.machines, self.floor.number_references, all_entries=True
        )
        text = f"登録{len(self.visible)} / 出力{sum(p.included for p in self.visible)} / 手動{sum(p.source_type=='manual' for p in self.visible)} / 商品未定{sum(not p.name for p in self.visible)} / 位置未特定{sum(b.machine is None for b in refs)}"
        self.instruction_status.setText(text)
        self.instruction_status.setToolTip(text)

    def autosave_instructions(self):
        if self._loading_instructions:
            return
        try:
            save_session(
                self.instruction_autosave_path,
                self.products,
                self.selected_day(),
                self.source,
                self.layout_name(),
                self.page_rows.value(),
                floor_layout=self.floor.state(),
                label_state=self.labels.state(),
                print_mode=self.print_mode.currentData(),
            )
        except Exception as exc:
            self.statusBar().showMessage("指示の自動保存に失敗しました：" + str(exc), 10000)

    def restore_instruction_autosave(self):
        if not self.instruction_autosave_path.exists():
            return
        try:
            self._loading_instructions = True
            products, day, data = load_session(self.instruction_autosave_path)
            from .labels_data import LabelStore

            self.products = products
            self.source = data.get("source", "")
            self.labels.store = LabelStore.restore(data.get("label_printing"), products)
            self.update_dates()
            self.day.setDate(QDate(day.year, day.month, day.day))
            self.refresh()
            self.dirty = False
            self.notice.setText("指示の自動保存を復元しました。")
        except Exception as exc:
            self.statusBar().showMessage("指示の自動保存を復元できません：" + str(exc), 10000)
        finally:
            self._loading_instructions = False
