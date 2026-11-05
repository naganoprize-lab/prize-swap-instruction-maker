"""Product table model and in-cell editors (no modal product editor)."""
from PySide6.QtCore import QAbstractTableModel, QEvent, QModelIndex, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QDoubleValidator, QIntValidator, QStandardItem, QStandardItemModel
from PySide6.QtWidgets import QComboBox, QLineEdit, QPlainTextEdit, QStyledItemDelegate, QStyle, QStyleOptionComboBox, QToolButton, QMenu

from .core import AppError, LIMITS, METHODS, machine_numbers, quantity, split_values

COLUMNS = ["出力", "順序", "メーカー", "商品名", "入数", "CS数", "マシン番号", "運営方法", "優先", "獲得個数制限"]
ATTRS = ["included", "order", "maker", "name", "units", "cases", "machines", "methods", "priority", "limit"]


class ProductModel(QAbstractTableModel):
    edited = Signal()
    invalid = Signal(str)

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
        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.EditRole):
            if col in (0, 8):
                return None
            return ", ".join(value) if col in (6, 7) else value
        if role == Qt.ItemDataRole.CheckStateRole and col in (0, 8):
            return Qt.CheckState.Checked if value else Qt.CheckState.Unchecked
        if role == Qt.ItemDataRole.BackgroundRole and p.priority:
            return QColor("#FCE4EC")
        if role == Qt.ItemDataRole.ToolTipRole:
            return "\n".join(value) if col in (6, 7) else str(value)

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
                value = split_values(str(value))
            elif col == 9 and value not in LIMITS:
                raise AppError("個数制限を選択してください。")
            setattr(p, ATTRS[col], value)
            self.dataChanged.emit(self.index(index.row(), 0), self.index(index.row(), len(COLUMNS) - 1))
            self.edited.emit()
            return True
        except (ValueError, TypeError) as exc:
            self.invalid.emit(str(exc) if isinstance(exc, AppError) else "順序は整数で入力してください。")
            return False


class MethodsCombo(QComboBox):
    """Editable text plus checkable dropdown; clicking a checkbox keeps it open."""
    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setEditable(True)
        self.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.setModel(QStandardItemModel(self))
        self.view().viewport().installEventFilter(self)
        self.view().installEventFilter(self)
        self.lineEdit().setPlaceholderText("複数選択 / 自由入力（カンマ区切り）")

    def set_values(self, values):
        self.clear()
        for text in dict.fromkeys(METHODS + values):
            item = QStandardItem(text)
            item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            item.setData(Qt.CheckState.Checked if text in values else Qt.CheckState.Unchecked, Qt.ItemDataRole.CheckStateRole)
            self.model().appendRow(item)
        self.setEditText(", ".join(values))

    def eventFilter(self, obj, event):
        click = obj is self.view().viewport() and event.type() == QEvent.Type.MouseButtonRelease
        space = obj is self.view() and event.type() == QEvent.Type.KeyPress and event.key() == Qt.Key.Key_Space
        if click or space:
            index = self.view().indexAt(event.position().toPoint()) if click else self.view().currentIndex()
            if index.isValid():
                values = split_values(self.currentText())
                text = self.model().item(index.row()).text()
                if text in values:
                    values.remove(text)
                else:
                    values.append(text)
                self.setEditText(", ".join(values))
                for row in range(self.count()):
                    item = self.model().item(row)
                    item.setData(Qt.CheckState.Checked if item.text() in values else Qt.CheckState.Unchecked, Qt.ItemDataRole.CheckStateRole)
                self.changed.emit()
            return True
        return super().eventFilter(obj, event)


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
        value = str(index.data(Qt.ItemDataRole.EditRole))
        if isinstance(editor, MethodsCombo):
            editor.set_values(split_values(value))
        elif isinstance(editor, QComboBox):
            editor.setCurrentText(value)
        elif isinstance(editor, QPlainTextEdit):
            editor.setPlainText("\n".join(split_values(value)))
        else:
            editor.setText(value)

    def setModelData(self, editor, model, index):
        if isinstance(editor, QComboBox):
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
            option.widget.style().drawComplexControl(QStyle.ComplexControl.CC_ComboBox, combo, painter, option.widget)
