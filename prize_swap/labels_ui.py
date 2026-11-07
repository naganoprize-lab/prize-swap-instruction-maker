"""Arrival-booth labels: live source products with independent print overrides."""
from pathlib import Path
import math

from PySide6.QtCore import QAbstractTableModel, QDate, QModelIndex, Qt, Signal, QItemSelectionModel
from PySide6.QtGui import QColor
from PySide6.QtPrintSupport import QAbstractPrintDialog, QPrintDialog, QPrintPreviewDialog, QPrinter
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QDateEdit, QDialog, QDoubleSpinBox,
    QFileDialog, QFormLayout, QFrame, QHBoxLayout, QHeaderView, QLabel, QMessageBox,
    QPushButton, QSizePolicy, QSpinBox, QSplitter, QStyledItemDelegate, QTableView, QVBoxLayout, QWidget)

from .core import AppError
from .methods import allocation_status
from .labels_data import LabelOptions, LabelStore
from .labels_printing import label_printer, plan_labels, render_labels, save_labels_pdf

HEADERS = ["印刷", "商品名（ラベル表示名）", "投入予定マシン番号", "マシン数", "自動枚数", "手動枚数", "印刷枚数"]


class LabelModel(QAbstractTableModel):
    edited = Signal()
    invalid = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.entries = []

    def replace(self, entries):
        self.beginResetModel()
        self.entries = entries
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.entries)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(HEADERS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if role == Qt.ItemDataRole.DisplayRole:
            return HEADERS[section] if orientation == Qt.Orientation.Horizontal else section + 1

    def flags(self, index):
        flags = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        e = self.entries[index.row()]
        if index.column() == 0 and e.product.included:
            flags |= Qt.ItemFlag.ItemIsUserCheckable
        if index.column() in (1, 5):
            flags |= Qt.ItemFlag.ItemIsEditable
        return flags

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        e, col = self.entries[index.row()], index.column()
        if role == Qt.ItemDataRole.CheckStateRole and col == 0:
            return Qt.CheckState.Checked if e.included else Qt.CheckState.Unchecked
        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.EditRole):
            values = [None, e.name, ", ".join(e.machines) or "未設定", len(e.machines), e.automatic_copies,
                      (e.manual_copies or 0) if role == Qt.ItemDataRole.EditRole else (e.manual_copies or "自動"), e.copies]
            return values[col]
        if role == Qt.ItemDataRole.ToolTipRole:
            if not e.product.included:
                return "入替指示書の出力対象外です。指示書側の出力チェックを有効にすると印刷できます。"
            if col in (2, 3, 4) and not e.machines:
                return "マシン番号未設定のため、自動計算は1枚です。"
            if col == 1:
                return f"ラベル表示名：{e.name}\n元の商品名：{e.product.name}"
            if col == 5:
                return "0（自動）でマシン数に戻ります。対象外にする場合は印刷チェックを外してください。"
        if role == Qt.ItemDataRole.BackgroundRole:
            if not e.product.included:
                return QColor(__import__("prize_swap.theme",fromlist=["analysis_color"]).analysis_color("#EDF0EE"))
            if not e.machines and col in (2, 3, 4):
                return QColor(__import__("prize_swap.theme",fromlist=["analysis_color"]).analysis_color("#FFF4BF"))

    def setData(self, index, value, role=Qt.ItemDataRole.EditRole):
        if not index.isValid():
            return False
        e, col = self.entries[index.row()], index.column()
        try:
            if col == 0 and role == Qt.ItemDataRole.CheckStateRole and e.product.included:
                e.included_override = value in (Qt.CheckState.Checked, Qt.CheckState.Checked.value)
            elif col == 1 and role == Qt.ItemDataRole.EditRole:
                value = str(value).strip()
                if not value:
                    raise AppError("ラベルの商品名は必須です。")
                e.name_override = None if value == e.product.name else value
            elif col == 5 and role == Qt.ItemDataRole.EditRole:
                if type(value) not in (int, str) or not 0 <= int(value) <= 9999:
                    raise AppError("枚数は1〜9999枚、または0（自動）で指定してください。")
                e.manual_copies = int(value) or None
            else:
                return False
        except (ValueError, TypeError) as exc:
            self.invalid.emit(str(exc))
            return False
        self.dataChanged.emit(self.index(index.row(), 0), self.index(index.row(), 6))
        self.edited.emit()
        return True


class LabelDelegate(QStyledItemDelegate):
    def createEditor(self, parent, option, index):
        if index.column() == 5:
            editor = QSpinBox(parent)
            editor.setRange(0, 9999)
            editor.setSpecialValueText("自動")
            return editor
        return super().createEditor(parent, option, index)


class LabelsWidget(QWidget):
    edited = Signal()
    day_requested = Signal(QDate)
    reorder_requested = Signal(object, int)
    message = Signal(str)

    def __init__(self, products, selected_day, sources=lambda: [], parent=None):
        super().__init__(parent)
        self.products, self.selected_day, self.sources = products, selected_day, sources
        self.store = LabelStore()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 8, 12, 8)
        top = QHBoxLayout()
        top.addWidget(QLabel("対象日"))
        self.day = QDateEdit(QDate.currentDate())
        self.day.setDisplayFormat("yyyy/MM/dd")
        self.day.setCalendarPopup(True)
        self.day.dateChanged.connect(self.day_requested)
        top.addWidget(self.day)
        self.count = QLabel("0商品 / 0枚")
        self.count.setObjectName("count")
        top.addWidget(self.count)
        top.addStretch()
        top.addWidget(QLabel("A4縦 / 商品名のみ印刷"))
        outer.addLayout(top)
        split = QSplitter()
        table_panel = QWidget()
        center = QVBoxLayout(table_panel)
        center.setContentsMargins(0, 0, 8, 0)
        tools = QHBoxLayout()
        def tool(text, callback):
            b = QPushButton(text)
            b.clicked.connect(callback)
            tools.addWidget(b)
            return b
        tool("全選択", lambda: self.select_all(True))
        tool("全解除", lambda: self.select_all(False))
        tool("自動枚数に戻す", self.reset_copies).setToolTip("選択行、未選択なら表示中の全商品を自動計算に戻す")
        tool("元の商品名に戻す", self.reset_names)
        tool("↑", lambda: self.move(-1))
        tool("↓", lambda: self.move(1))
        center.addLayout(tools)
        self.model = LabelModel(self)
        self.model.edited.connect(self.edited)
        self.model.edited.connect(self.update_count)
        self.model.invalid.connect(lambda text: self.message.emit(text))
        self.table = QTableView()
        self.table.setModel(self.model)
        self.table.setItemDelegate(LabelDelegate(self.table))
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setAlternatingRowColors(True)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked | QAbstractItemView.EditTrigger.EditKeyPressed | QAbstractItemView.EditTrigger.SelectedClicked)
        self.table.verticalHeader().setDefaultSectionSize(34)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        for col, width in enumerate([48, 340, 220, 72, 76, 96, 76]):
            self.table.setColumnWidth(col, width)
        center.addWidget(self.table, 1)
        note = QLabel("黄色：マシン未設定（自動1枚） / 表示名は元商品を変更しません。\n↑↓の並び替えは入替指示書と共通です。同じ商品のラベルは連続して印刷します。")
        note.setWordWrap(True)
        center.addWidget(note)
        split.addWidget(table_panel)
        settings = QFrame()
        settings.setObjectName("card")
        settings.setFixedWidth(270)
        settings.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        side = QVBoxLayout(settings)
        title = QLabel("ラベル印刷設定")
        title.setObjectName("section")
        side.addWidget(title)
        form = QFormLayout()
        self.per_page = QSpinBox()
        self.per_page.setRange(4, 10)
        self.mode = QComboBox()
        self.mode.addItems(["自動（最大サイズ）", "手動"])
        self.font_size = QSpinBox()
        self.font_size.setRange(8, 256)
        self.font_size.setSuffix(" pt")
        self.weight = QComboBox()
        self.weight.addItems(["太字", "極太"])
        self.alignment = QComboBox()
        self.alignment.addItems(["中央", "左揃え"])
        self.border = QDoubleSpinBox()
        self.border.setRange(.5, 4)
        self.border.setSingleStep(.1)
        self.border.setSuffix(" pt")
        for text, control in [("1ページ", self.per_page), ("文字サイズ", self.mode), ("手動サイズ", self.font_size), ("太さ", self.weight), ("配置", self.alignment), ("枠線", self.border)]:
            form.addRow(text, control)
        side.addLayout(form)
        hint = QLabel("初期設定：7枚 / 太字 / 中央\n文字幅と行高を実測して折り返し、枠内に収まる最大の整数ptを選びます。長すぎる商品名は警告します。")
        hint.setWordWrap(True)
        side.addWidget(hint)
        side.addStretch()
        split.addWidget(settings)
        split.setStretchFactor(0, 1)
        split.setSizes([1000, 270])
        outer.addWidget(split, 1)
        bottom = QHBoxLayout()
        self.status = QLabel("商品一覧は入替指示書と自動連携します。")
        bottom.addWidget(self.status, 1)
        for text, callback in [("印刷プレビュー", self.preview), ("PDF保存", self.export_pdf), ("直接印刷", self.print_direct)]:
            b = QPushButton(text)
            if text == "直接印刷": b.setObjectName("primary")
            b.clicked.connect(callback)
            bottom.addWidget(b)
        outer.addLayout(bottom)
        self.apply_options()
        for control in (self.per_page, self.mode, self.font_size, self.weight, self.alignment, self.border):
            signal = control.currentIndexChanged if isinstance(control, QComboBox) else control.valueChanged
            signal.connect(self.options_changed)

    def apply_options(self):
        o = self.store.options
        values = [(self.per_page, o.per_page), (self.mode, int(o.mode == "manual")), (self.font_size, o.font_size),
                  (self.weight, int(o.weight == "black")), (self.alignment, int(o.alignment == "left")), (self.border, o.border)]
        for widget, value in values:
            widget.blockSignals(True)
            widget.setCurrentIndex(value) if isinstance(widget, QComboBox) else widget.setValue(value)
            widget.blockSignals(False)
        self.font_size.setEnabled(o.mode == "manual")

    def options_changed(self, *_):
        self.store.options = LabelOptions(self.per_page.value(), "manual" if self.mode.currentIndex() else "auto",
            self.font_size.value(), "black" if self.weight.currentIndex() else "bold", "left" if self.alignment.currentIndex() else "center", self.border.value())
        self.font_size.setEnabled(self.mode.currentIndex() == 1)
        self.edited.emit()
        self.update_count()

    def selected_entries(self):
        return [self.model.entries[index.row()] for index in self.table.selectionModel().selectedRows()]

    def sync(self, *_):
        selected = {id(e.product) for e in self.selected_entries()}
        entries = self.store.for_day(self.products(), self.selected_day())
        self.model.replace(entries)
        self.day.blockSignals(True)
        self.day.setDate(QDate(self.selected_day().year, self.selected_day().month, self.selected_day().day))
        self.day.blockSignals(False)
        for row, entry in enumerate(entries):
            if id(entry.product) in selected:
                self.table.selectionModel().select(self.model.index(row, 0), QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows)
        self.update_count()

    def update_count(self):
        total = sum(e.copies for e in self.model.entries if e.included)
        self.count.setText(f"{len(self.model.entries)}商品 / 印刷 {total}枚 / {math.ceil(total/self.store.options.per_page)}ページ")

    def select_all(self, included):
        for e in self.model.entries:
            if e.product.included:
                e.included_override = included
        self.model.replace(list(self.model.entries))
        self.edited.emit()
        self.update_count()

    def reset_copies(self):
        for e in self.selected_entries() or self.model.entries:
            e.manual_copies = None
        self.model.replace(list(self.model.entries))
        self.edited.emit()
        self.update_count()

    def reset_names(self):
        for e in self.selected_entries() or self.model.entries:
            e.name_override = None
        self.model.replace(list(self.model.entries))
        self.edited.emit()

    def move(self, direction):
        selected = [e.product for e in self.selected_entries()]
        if selected:
            self.reorder_requested.emit(selected, direction)

    def state(self):
        return self.store.state(self.products())

    def prepare(self):
        self.table.setFocus()
        printer = label_printer()
        area = printer.pageRect(QPrinter.Unit.Point)
        entries, options = list(self.model.entries), self.store.options
        plan = plan_labels(entries, area.width(), area.height(), options)
        mismatches=[f"{e.product.name}：{allocation_status(e.product).message}" for e in entries
                    if e.included and (allocation_status(e.product).unassigned or allocation_status(e.product).excess)]
        if mismatches:
            QMessageBox.warning(self,"予定ブース数と割当の差分", "ラベル枚数は予定数ではなく登録マシン数（または手動枚数）を使用します。\n"+"\n".join(mismatches))
        if plan.warnings:
            QMessageBox.warning(self, "長い商品名の確認", "\n".join(plan.warnings))
        return entries, options

    def error(self, exc):
        QMessageBox.warning(self, "ラベル印刷エラー", str(exc))

    def preview(self):
        try:
            entries, options = self.prepare()
            printer = label_printer()
            dialog = QPrintPreviewDialog(printer, self)
            dialog.setWindowTitle("入荷ブース用ラベル — A4縦")
            dialog.resize(950, 850)
            def paint(target):
                try:
                    render_labels(target, entries, options)
                except Exception as exc:
                    self.error(exc)
            dialog.paintRequested.connect(paint)
            dialog.exec()
        except Exception as exc:
            self.error(exc)

    def save_path(self):
        path, _ = QFileDialog.getSaveFileName(self, "入荷ブース用ラベルをPDF保存", f"入荷ブース用ラベル_{self.selected_day().isoformat()}.pdf", "PDF (*.pdf)")
        if path and not path.lower().endswith(".pdf"):
            path += ".pdf"
            if Path(path).exists() and QMessageBox.question(self, "上書き確認", "既存PDFを上書きしますか？") != QMessageBox.StandardButton.Yes:
                return ""
        return path

    def export_pdf(self):
        try:
            entries, options = self.prepare()
            path = self.save_path()
            if path:
                plan = save_labels_pdf(path, entries, options, self.sources())
                self.status.setText(f"保存しました：{len(plan.pages)}ページ")
                self.message.emit(f"ラベルPDFを保存しました：{path}")
        except Exception as exc:
            self.error(exc)

    def print_direct(self):
        try:
            entries, options = self.prepare()
            printer = label_printer()
            area = printer.pageRect(QPrinter.Unit.Point)
            plan = plan_labels(entries, area.width(), area.height(), options)
            dialog = QPrintDialog(printer, self)
            dialog.setMinMax(1, len(plan.pages))
            dialog.setOption(QAbstractPrintDialog.PrintDialogOption.PrintPageRange, True)
            if dialog.exec() == QDialog.DialogCode.Accepted:
                render_labels(printer, entries, options)
                self.status.setText("印刷ジョブを送信しました。用紙の出力はプリンターで確認してください。")
        except Exception as exc:
            self.error(exc)
