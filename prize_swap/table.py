"""Product table model and in-cell editors (no modal product editor)."""

from PySide6.QtCore import QAbstractTableModel, QEvent, QModelIndex, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QDoubleValidator, QIntValidator
from PySide6.QtWidgets import (
    QComboBox,
    QLineEdit,
    QPlainTextEdit,
    QStyledItemDelegate,
    QStyle,
    QStyleOptionComboBox,
    QToolButton,
    QMenu,
)

from .core import AppError, LIMITS, machine_numbers, quantity, split_values
from .methods import (
    allocation_status,
    format_allocations,
    format_cell_status,
    set_allocations,
    editor_state,
    sync_machine_methods,
)
from .booth_style import METRIC_COLORS
from .theme import metric_colors, analysis_color
from .methods_editor import MethodsCombo

COLUMNS = [
    "出力",
    "順序",
    "メーカー",
    "商品名",
    "入数",
    "CS数",
    "マシン番号",
    "運営方法",
    "優先",
    "獲得個数制限",
]
ATTRS = [
    "included",
    "order",
    "maker",
    "name",
    "units",
    "cases",
    "machines",
    "methods",
    "priority",
    "limit",
]


class ProductModel(QAbstractTableModel):
    edited = Signal()
    invalid = Signal(str)
    allocation_warning = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.products = []

    def replace(self, products):
        self.beginResetModel()
        self.products = products
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.products)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(COLUMNS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if role == Qt.ItemDataRole.DisplayRole:
            return COLUMNS[section] if orientation == Qt.Orientation.Horizontal else section + 1

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        p, col = self.products[index.row()], index.column()
        value = getattr(p, ATTRS[col])
        if role == Qt.ItemDataRole.TextAlignmentRole and col in (1, 4, 5):
            return Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        if (
            role == Qt.ItemDataRole.DisplayRole
            and col in (4, 5)
            and isinstance(value, (int, float))
        ):
            return f"{value:,}" if abs(value) >= 1000 else value
        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.EditRole):
            if col in (0, 8):
                return None
            if col == 7:
                # QVariantMap sorts keys in the proxy. Pairs retain method order.
                return (
                    editor_state(p) if role == Qt.ItemDataRole.EditRole else format_cell_status(p)
                )
            if (
                col == 3
                and role == Qt.ItemDataRole.DisplayRole
                and p.candidate_assignment
                and not value
            ):
                return "新商品未設定（現在の商品はツールチップで確認）"
            return ", ".join(value) if col == 6 else value
        if role == Qt.ItemDataRole.CheckStateRole and col in (0, 8):
            return Qt.CheckState.Checked if value else Qt.CheckState.Unchecked
        if role == Qt.ItemDataRole.BackgroundRole:
            if col == 7:
                status = allocation_status(p)
                if status.excess:
                    return QColor(metric_colors("excess")[0])
                if status.unknown or status.unassigned or status.unmapped:
                    return QColor(metric_colors("unassigned")[0])
                if status.assigned:
                    return QColor(metric_colors("assigned")[0])
            if p.priority:
                return QColor(analysis_color("#FCE4EC"))
        if role == Qt.ItemDataRole.ForegroundRole and col == 7:
            status = allocation_status(p)
            field = (
                "excess"
                if status.excess
                else (
                    "unassigned"
                    if status.unknown or status.unassigned or status.unmapped
                    else "assigned"
                )
            )
            if (
                status.assigned
                or status.unknown
                or status.unassigned
                or status.unmapped
                or status.excess
            ):
                return QColor(metric_colors(field)[1])
        if role == Qt.ItemDataRole.ToolTipRole:
            if p.candidate_assignment and col in (3, 6):
                a = p.candidate_assignment
                return f"現在の商品：{a['current_product_name']}\n新しい商品：{p.name or '未設定'}\n管理：{a['management_number'] or '—'}\n{a['source_candidate_status']}：{a['source_candidate_reason']}\n入替日は未確定です。選択中の日付に下書きを表示しています。"
            if col == 7:
                return format_allocations(p, "\n") + "\n" + allocation_status(p).message
            return "\n".join(value) if col == 6 else str(value)

    def flags(self, index):
        flags = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        if index.column() in (0, 8):
            return flags | Qt.ItemFlag.ItemIsUserCheckable
        return flags if index.column() == 2 else flags | Qt.ItemFlag.ItemIsEditable

    def setData(self, index, value, role=Qt.ItemDataRole.EditRole):
        if not index.isValid():
            return False
        p, col = self.products[index.row()], index.column()
        try:
            if col in (0, 8) and role == Qt.ItemDataRole.CheckStateRole:
                value = value == Qt.CheckState.Checked or value == Qt.CheckState.Checked.value
            elif role != Qt.ItemDataRole.EditRole or col in (0, 2, 8):
                return False
            elif col == 1:
                value = int(value)
            elif col == 3:
                value = str(value).strip()
                if not value:
                    raise AppError("商品名は必須です。")
            elif col in (4, 5):
                value = quantity(value)
            elif col == 6:
                value = machine_numbers(str(value))
            elif col == 7:
                set_allocations(p, value)
                value = p.methods
            elif col == 9 and value not in LIMITS:
                raise AppError("個数制限を選択してください。")
            if col == 0 and value and not p.name.strip():
                raise AppError("新しい商品名を入力してから出力対象にしてください。")
            setattr(p, ATTRS[col], value)
            if col == 3 and p.candidate_assignment:
                p.candidate_assignment["replacement_product_name"] = value
            if col == 6:
                sync_machine_methods(p)
            self.dataChanged.emit(
                self.index(index.row(), 0), self.index(index.row(), len(COLUMNS) - 1)
            )
            self.edited.emit()
            if col in (6, 7) and (
                allocation_status(p).excess
                or allocation_status(p).planned > allocation_status(p).registered
            ):
                self.allocation_warning.emit(f"{p.name}：{allocation_status(p).message}")
            return True
        except (ValueError, TypeError) as exc:
            self.invalid.emit(
                str(exc) if isinstance(exc, AppError) else "順序は整数で入力してください。"
            )
            return False


class MachineEditor(QPlainTextEdit):
    def __init__(self, options, parent=None):
        super().__init__(parent)
        self.options = options
        self.setViewportMargins(0, 0, 20, 0)
        self.choose = QToolButton(self)
        self.choose.setText("▾")
        self.choose.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.choose.setToolTip("登録マシンから選択（複数可）")
        self.choose.clicked.connect(self.select_registered)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.choose.setGeometry(self.width() - 22, 1, 20, max(20, self.height() - 2))

    def select_registered(self):
        menu = QMenu(self)
        values = split_values(self.toPlainText())
        for number in self.options():
            action = menu.addAction(number)
            action.setCheckable(True)
            action.setChecked(number in values)

            def toggle(checked, n=number):
                current = split_values(self.toPlainText())
                if checked and n not in current:
                    current.append(n)
                elif not checked and n in current:
                    current.remove(n)
                self.setPlainText("\n".join(current))

            action.toggled.connect(toggle)
        if menu.isEmpty():
            menu.addAction("売り場レイアウトで番号を登録してください").setEnabled(False)
        menu.exec(self.choose.mapToGlobal(self.choose.rect().bottomLeft()))


class ProductDelegate(QStyledItemDelegate):
    def eventFilter(self, editor, event):
        if isinstance(editor, MethodsCombo) and event.type() == QEvent.Type.FocusOut:
            if editor.opening_popup or editor.popup.isVisible():
                # This transient popup belongs to the cell editor. Moving to a
                # count control must not make QStyledItemDelegate close it.
                return False
        return super().eventFilter(editor, event)

    def createEditor(self, parent, option, index):
        col = index.column()
        if col == 7:
            editor = MethodsCombo(parent)
            editor.changed.connect(lambda: self.commitData.emit(editor))
        elif col == 9:
            editor = QComboBox(parent)
            editor.addItems(LIMITS)
            editor.activated.connect(lambda: self.commitData.emit(editor))
            QTimer.singleShot(0, editor.showPopup)
        elif col == 6:
            editor = MachineEditor(getattr(self, "machine_options", lambda: []), parent)
            editor.setTabChangesFocus(True)
            editor.setPlaceholderText("20085右,20098左（改行も可）")
        else:
            editor = QLineEdit(parent)
            if col in (4, 5):
                validator = QDoubleValidator(0, 1e12, 8, editor)
                validator.setNotation(QDoubleValidator.Notation.StandardNotation)
                editor.setValidator(validator)
            elif col == 1:
                editor.setValidator(QIntValidator(editor))
        return editor

    def setEditorData(self, editor, index):
        value = index.data(Qt.ItemDataRole.EditRole)
        if isinstance(editor, MethodsCombo):
            if not editor.in_change:
                machines = str(index.siblingAtColumn(6).data(Qt.ItemDataRole.EditRole))
                editor.set_allocations(value, machine_numbers(machines))
        elif isinstance(editor, QComboBox):
            editor.setCurrentText(str(value))
        elif isinstance(editor, QPlainTextEdit):
            editor.setPlainText("\n".join(split_values(value)))
        else:
            editor.setText(str(value))

    def setModelData(self, editor, model, index):
        if isinstance(editor, MethodsCombo):
            value = editor.state()
        elif isinstance(editor, QComboBox):
            value = editor.currentText()
        elif isinstance(editor, QPlainTextEdit):
            value = editor.toPlainText()
        else:
            value = editor.text()
        model.setData(index, value)

    def updateEditorGeometry(self, editor, option, index):
        # Editors stay inside the table cell; multiline numbers can scroll.
        editor.setGeometry(option.rect)

    def paint(self, painter, option, index):
        super().paint(painter, option, index)
        if index.column() in (7, 9):
            combo = QStyleOptionComboBox()
            combo.rect = option.rect.adjusted(option.rect.width() - 18, 0, 0, 0)
            combo.state = option.state
            combo.subControls = QStyle.SubControl.SC_ComboBoxArrow
            option.widget.style().drawComplexControl(
                QStyle.ComplexControl.CC_ComboBox, combo, painter, option.widget
            )
