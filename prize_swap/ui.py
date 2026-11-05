from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QDate, QEvent, QItemSelectionModel, QSortFilterProxyModel, Qt
from PySide6.QtGui import QColor, QTextCharFormat
from PySide6.QtPrintSupport import QAbstractPrintDialog, QPrintDialog, QPrintPreviewDialog, QPrinter
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QComboBox, QDateEdit,
    QDialog, QFileDialog, QFrame, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QMainWindow, QMessageBox, QPushButton, QSizePolicy, QSpinBox, QTableView, QTabWidget,
    QVBoxLayout, QWidget)

from .core import AppError, for_day, load_session, read_products, save_session
from .exporter import export_excel
from .printing import configured_printer, plan_pages, render_document, save_pdf
from .table import COLUMNS, ProductDelegate, ProductModel
from .floor import FloorWidget
from .floor_data import validate_state

STYLE = """
QMainWindow, QWidget#workspace { background: #F0F3F1; }
QWidget { color: #253C33; font-family: 'Yu Gothic UI', 'Noto Sans CJK JP', 'Meiryo'; font-size: 10pt; }
QFrame#card { background: white; border: 1px solid #DDE5DF; border-radius: 10px; }
QLabel { background: transparent; border: none; }
QLabel#title { font-size: 18pt; font-weight: 700; color: #244C3D; }
QLabel#section { font-size: 12pt; font-weight: 700; }
QLabel#muted { color: #677D72; }
QLabel#count { color: #2F6D53; font-weight: 700; }
QPushButton { background: white; border: 1px solid #CCD9D0; border-radius: 6px; padding: 6px 12px; min-height: 20px; }
QPushButton:hover { background: #EAF3ED; border-color: #78A38B; }
QPushButton:pressed { background: #D8E9DF; }
QPushButton#primary { background: #3D795C; color: white; border-color: #3D795C; font-weight: 600; }
QPushButton#primary:hover { background: #32664D; }
QLineEdit, QComboBox, QDateEdit, QSpinBox, QPlainTextEdit { background: white; border: 1px solid #CCD9D0; border-radius: 5px; padding: 4px 6px; min-height: 20px; selection-background-color: #D4E9DD; }
QTableView { background: white; alternate-background-color: #F6F9F7; border: 1px solid #E1E8E3; gridline-color: #E4EBE6; selection-background-color: #CEE6D8; selection-color: #183E2C; }
QHeaderView::section { background: #EDF3EF; border: none; border-right: 1px solid #DBE5DE; border-bottom: 1px solid #DBE5DE; padding: 6px; font-weight: 600; }
QTableView QLineEdit, QTableView QComboBox, QTableView QPlainTextEdit { border: 1px solid #488363; border-radius: 2px; padding: 0px 3px; }
QComboBox QAbstractItemView { background: white; selection-background-color: #CEE6D8; color: #253C33; }
QStatusBar { background: #F0F3F1; color: #536C5F; }
"""


def button(text, callback, primary=False):
    b = QPushButton(text)
    if primary:
        b.setObjectName("primary")
    b.clicked.connect(callback)
    return b


def card(parent_layout, stretch=0):
    frame = QFrame()
    frame.setObjectName("card")
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(12, 8, 12, 8)
    layout.setSpacing(4)
    parent_layout.addWidget(frame, stretch)
    return frame, layout


class ProductFilter(QSortFilterProxyModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.query = ""
        self.mode = 0

    def filterAcceptsRow(self, row, parent):
        p = self.sourceModel().products[row]
        text = " ".join([p.name, p.maker, *p.machines, *p.methods]).casefold()
        return (not self.query or self.query in text) and (self.mode != 1 or p.included) and (self.mode != 2 or p.priority)

    def lessThan(self, left, right):
        return int(left.data()) < int(right.data())


class Window(QMainWindow):
    def __init__(self, layout_storage=None):
        super().__init__()
        self.setWindowTitle("プライズ景品 入替指示書メーカー")
        self.setMinimumSize(1100, 700)
        self.resize(1500, 900)
        self.setStyleSheet(STYLE)
        self.products, self.visible = [], []
        self.source = ""
        self.dirty = False
        root = QWidget()
        root.setObjectName("workspace")
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(16, 12, 16, 8)
        outer.setSpacing(10)
        header, head = card(outer)
        header.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        title_row = QHBoxLayout()
        title = QLabel("入替指示書メーカー")
        title.setObjectName("title")
        title_row.addWidget(title)
        title_row.addStretch()
        title_row.addWidget(button("印刷プレビュー", self.preview))
        title_row.addWidget(button("直接印刷", self.print_direct, True))
        title_row.addWidget(button("PDF保存", self.export_pdf))
        head.addLayout(title_row)
        top = QHBoxLayout()
        top.addWidget(button("Excelを開く", self.open_excel, True))
        self.file_label = QLabel("ファイル未選択")
        self.file_label.setObjectName("muted")
        self.file_label.setMinimumWidth(110)
        self.file_label.setMaximumWidth(260)
        top.addWidget(self.file_label, 1)
        top.addStretch()
        top.addWidget(QLabel("稼働予定日"))
        self.day = QDateEdit(QDate.currentDate())
        self.day.setCalendarPopup(True)
        self.day.setDisplayFormat("yyyy/MM/dd")
        self.day.dateChanged.connect(self.refresh)
        top.addWidget(self.day)
        self.dates = QComboBox()
        self.dates.setPlaceholderText("登録日一覧")
        self.dates.setMinimumWidth(140)
        self.dates.activated.connect(self.pick_date)
        top.addWidget(self.dates)
        self.count = QLabel("0件")
        self.count.setObjectName("count")
        top.addWidget(self.count)
        head.addLayout(top)
        self.notice = QLabel("Excelを開いて開始してください。セルをクリックして編集できます。")
        self.notice.setObjectName("muted")
        self.notice.setWordWrap(True)
        head.addWidget(self.notice)

        self.tabs = QTabWidget()
        outer.addWidget(self.tabs, 1)
        list_page = QWidget()
        list_layout = QVBoxLayout(list_page)
        list_layout.setContentsMargins(0, 0, 0, 0)
        self.tabs.addTab(list_page, "入替指示書")
        self.floor = FloorWidget(lambda: self.products, self.selected_day, layout_storage, self)
        self.floor.message.connect(lambda message: self.statusBar().showMessage(message, 15000))
        self.tabs.addTab(self.floor, "売り場レイアウト")
        self.list_card, center = card(list_layout, 1)
        controls = QHBoxLayout()
        section = QLabel("商品一覧")
        section.setObjectName("section")
        controls.addWidget(section)
        self.search = QLineEdit()
        self.search.setPlaceholderText("商品名・メーカー・マシン番号を検索")
        self.search.setClearButtonEnabled(True)
        controls.addWidget(self.search, 1)
        self.filter_choice = QComboBox()
        self.filter_choice.addItems(["すべて表示", "出力対象のみ", "優先入替のみ"])
        controls.addWidget(self.filter_choice)
        select = button("全選択", lambda: self.set_all(True))
        select.setToolTip("検索・絞り込みで表示中の商品の出力チェックを全てON")
        controls.addWidget(select)
        clear = button("全解除", lambda: self.set_all(False))
        clear.setToolTip("検索・絞り込みで表示中の商品の出力チェックを全てOFF")
        controls.addWidget(clear)
        controls.addWidget(button("↑ 上へ", lambda: self.move_products(-1)))
        controls.addWidget(button("↓ 下へ", lambda: self.move_products(1)))
        center.addLayout(controls)
        self.model = ProductModel(self)
        self.model.edited.connect(self.mark_dirty)
        self.model.edited.connect(self.update_count)
        self.model.edited.connect(self.floor.sync)
        self.model.invalid.connect(lambda message: self.statusBar().showMessage(message, 10000))
        self.proxy = ProductFilter(self)
        self.proxy.setSourceModel(self.model)
        self.proxy.sort(1, Qt.SortOrder.AscendingOrder)
        self.search.textChanged.connect(self.filter_changed)
        self.filter_choice.currentIndexChanged.connect(self.filter_changed)
        self.table = QTableView()
        self.table.setModel(self.proxy)
        self.delegate = ProductDelegate(self.table)
        self.delegate.machine_options = lambda: sorted(m.number for m in self.floor.machines if m.number)
        self.table.setItemDelegate(self.delegate)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.CurrentChanged | QAbstractItemView.EditTrigger.SelectedClicked | QAbstractItemView.EditTrigger.DoubleClicked | QAbstractItemView.EditTrigger.EditKeyPressed)
        self.table.setAlternatingRowColors(True)
        self.table.setWordWrap(False)
        self.table.verticalHeader().setDefaultSectionSize(34)
        self.table.verticalHeader().setMinimumSectionSize(30)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.table.horizontalHeader().setStretchLastSection(False)
        for i, width in enumerate([48, 60, 115, 340, 68, 68, 165, 160, 52, 160]):
            self.table.setColumnWidth(i, width)
        self.table.viewport().installEventFilter(self)
        center.addWidget(self.table, 1)
        hint = QLabel("セルで直接編集 / マシン番号は複数可 / 印刷は検索に関係なく選択日の出力対象すべて")
        hint.setObjectName("muted")
        hint.setWordWrap(True)
        center.addWidget(hint)

        footer, bottom_layout = card(outer)
        footer.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        bottom = QHBoxLayout()
        bottom.addWidget(button("一時保存", self.save_json))
        bottom.addWidget(button("一時保存を開く", self.open_json))
        bottom.addWidget(button("Excel出力", self.export))
        bottom.addStretch()
        bottom.addWidget(QLabel("ページ配置"))
        self.layout_choice = QComboBox()
        self.layout_choice.addItems(["左を上から → 右を上から", "左右交互"])
        self.layout_choice.currentIndexChanged.connect(self.mark_dirty)
        bottom.addWidget(self.layout_choice)
        bottom.addWidget(QLabel("1ブロック上限"))
        self.page_rows = QSpinBox()
        self.page_rows.setRange(1, 50)
        self.page_rows.setValue(20)
        self.page_rows.setSuffix(" 行")
        self.page_rows.valueChanged.connect(self.mark_dirty)
        bottom.addWidget(self.page_rows)
        bottom_layout.addLayout(bottom)
        self.statusBar().showMessage("準備完了 — Excelを読み込んでください。")

    def eventFilter(self, obj, event):
        if obj is self.table.viewport() and event.type() == QEvent.Type.MouseButtonRelease:
            index = self.table.indexAt(event.position().toPoint())
            if index.isValid() and index.column() in (7, 9):
                self.table.setCurrentIndex(index)
                if self.table.state() != QAbstractItemView.State.EditingState:
                    self.table.edit(index)
        return super().eventFilter(obj, event)

    def filter_changed(self, *_):
        self.proxy.query = self.search.text().strip().casefold()
        self.proxy.mode = self.filter_choice.currentIndex()
        self.proxy.invalidateFilter()
        self.update_count()

    def refresh(self, *_):
        self.visible = for_day(self.products, self.selected_day())
        self.model.replace(self.visible)
        self.floor.sync()
        self.update_count()

    def update_count(self):
        self.count.setText(f"{len(self.visible)}件 ／ 出力 {sum(p.included for p in self.visible)}件")
        if hasattr(self, "proxy"):
            self.statusBar().showMessage(f"表示 {self.proxy.rowCount()}件 / 選択日 {len(self.visible)}件　印刷は出力チェックのある商品が対象です。")

    def selected_products(self):
        return [self.model.products[self.proxy.mapToSource(index).row()] for index in self.table.selectionModel().selectedRows()]

    def set_all(self, included):
        # Snapshot indexes first: changing inclusion may immediately filter rows.
        products = [self.model.products[self.proxy.mapToSource(self.proxy.index(r, 0)).row()] for r in range(self.proxy.rowCount())]
        for p in products:
            p.included = included
        if products:
            self.mark_dirty()
            self.refresh()

    def move_products(self, direction):
        if self.search.text() or self.filter_choice.currentIndex():
            self.statusBar().showMessage("並び順変更は検索・絞り込みを解除して行ってください。", 5000)
            return
        selected = {id(p) for p in self.selected_products()}
        if not selected:
            return
        products = sorted(self.visible, key=lambda p: p.order)
        indexes = range(1, len(products)) if direction < 0 else range(len(products) - 2, -1, -1)
        for i in indexes:
            neighbor = i + direction
            if id(products[i]) in selected and id(products[neighbor]) not in selected:
                products[i], products[neighbor] = products[neighbor], products[i]
        for order, p in enumerate(products, 1):
            p.order = order
        self.mark_dirty()
        self.refresh()
        for row, p in enumerate(self.model.products):
            if id(p) in selected:
                index = self.proxy.mapFromSource(self.model.index(row, 0))
                self.table.selectionModel().select(index, QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows)

    def print_products(self):
        self.table.setFocus()
        if not any(p.included for p in self.visible):
            raise AppError("選択日に出力対象の商品がありません。")
        if self.floor.initialized:
            registered = {m.number for m in self.floor.machines if m.number}
            missing = sorted({n for p in self.visible if p.included for n in p.machines if n not in registered})
            if missing:
                answer = QMessageBox.question(self, "入替予定のマシン番号を確認", "配置にないマシン番号が入替予定に残っています：\n" + "、".join(missing) + "\n印刷・保存を続けますか？")
                if answer != QMessageBox.StandardButton.Yes:
                    raise AppError("マシン番号を確認してから再実行してください。")
        return list(self.visible)

    def paint_printer(self, printer):
        try:
            return render_document(printer, list(self.visible), self.selected_day(), self.page_rows.value(), self.layout_name())
        except Exception as exc:
            self.error(exc)

    def preview(self):
        try:
            self.print_products()
            printer = configured_printer()
            dialog = QPrintPreviewDialog(printer, self)
            dialog.setWindowTitle("印刷プレビュー — A4横向き")
            dialog.resize(1300, 850)
            dialog.paintRequested.connect(self.paint_printer)
            dialog.exec()
        except Exception as exc:
            self.error(exc)

    def print_direct(self):
        try:
            self.print_products()
            printer = configured_printer()
            area = printer.pageRect(QPrinter.Unit.Point)
            plan = plan_pages(self.visible, area.width(), area.height(), self.page_rows.value(), self.layout_name())
            dialog = QPrintDialog(printer, self)
            dialog.setMinMax(1, len(plan.pages))
            dialog.setOption(QAbstractPrintDialog.PrintDialogOption.PrintPageRange, True)
            if dialog.exec() == QDialog.DialogCode.Accepted:
                if self.paint_printer(printer):
                    self.statusBar().showMessage("印刷ジョブを送信しました。実際の出力はプリンターで確認してください。", 10000)
        except Exception as exc:
            self.error(exc)

    def export_pdf(self):
        try:
            products = self.print_products()
            path = self.save_path("PDF保存", "PDF (*.pdf)", ".pdf")
            if path:
                plan = save_pdf(path, products, self.selected_day(), self.page_rows.value(), self.layout_name(), self.source)
                self.statusBar().showMessage(f"PDFを保存しました（{len(plan.pages)}ページ）：{path}", 10000)
        except Exception as exc:
            self.error(exc)
    def mark_dirty(self, *_):
        self.dirty = True

    def error(self, exc):
        QMessageBox.warning(self, "処理できませんでした", str(exc))

    def confirm_discard(self):
        return not self.dirty or QMessageBox.question(self, "編集中の内容", "未保存の編集内容があります。破棄して続けますか？") == QMessageBox.StandardButton.Yes

    def closeEvent(self, event):
        if self.confirm_discard():
            if self.floor.timer.isActive():
                self.floor.timer.stop()
                self.floor.autosave()
            event.accept()
        else:
            event.ignore()

    def selected_day(self):
        return self.day.date().toPython()

    def layout_name(self):
        return "row" if self.layout_choice.currentIndex() else "column"

    def update_dates(self):
        self.dates.clear()
        fmt = QTextCharFormat()
        fmt.setBackground(QColor("#DBEAFE"))
        self.day.calendarWidget().setDateTextFormat(QDate(), QTextCharFormat())
        for day in sorted({p.day for p in self.products}):
            self.dates.addItem(day.isoformat())
            self.day.calendarWidget().setDateTextFormat(QDate(day.year, day.month, day.day), fmt)

    def pick_date(self, index):
        self.day.setDate(QDate.fromString(self.dates.itemText(index), "yyyy-MM-dd"))

    def open_excel(self):
        if not self.confirm_discard():
            return
        path, _ = QFileDialog.getOpenFileName(self, "入力Excelを選択", "", "Excel (*.xlsx *.xlsm)")
        if not path:
            return
        try:
            products, warnings = read_products(path)
            self.products, self.source = products, path
            self.file_label.setText(Path(path).name)
            self.file_label.setToolTip(path)
            self.update_dates()
            if products:
                first = min(p.day for p in products)
                self.day.setDate(QDate(first.year, first.month, first.day))
            self.refresh()
            self.dirty = False
            self.notice.setText(f"読込 {len(products)}件 ／ 警告 {len(warnings)}件。重複行は保持します。")
            if warnings:
                dlg = QMessageBox(self)
                dlg.setWindowTitle("読込時の確認事項")
                dlg.setText(f"{len(warnings)}件の確認事項があります。「詳細を表示」で内容を確認できます。")
                dlg.setDetailedText("\n".join(warnings))
                dlg.exec()
        except Exception as exc:
            self.error(exc)

    def save_path(self, title, filter_, suffix):
        # QFileDialog performs the overwrite confirmation for the selected path.
        path, _ = QFileDialog.getSaveFileName(self, title, f"入替指示書_{self.selected_day().isoformat()}{suffix}", filter_)
        if path and not path.lower().endswith(suffix):
            path += suffix
            if Path(path).exists() and QMessageBox.question(self, "上書き確認", f"{path}\nを上書きしますか？") != QMessageBox.StandardButton.Yes:
                return ""
        return path

    def save_json(self):
        path = self.save_path("作業を一時保存", "JSON (*.json)", ".json")
        if path:
            try:
                save_session(path, self.products, self.selected_day(), self.source, self.layout_name(), self.page_rows.value(), floor_layout=self.floor.state())
                self.dirty = False
                self.statusBar().showMessage("一時保存しました。", 5000)
            except Exception as exc:
                self.error(exc)

    def open_json(self):
        if not self.confirm_discard():
            return
        path, _ = QFileDialog.getOpenFileName(self, "一時保存を開く", "", "JSON (*.json)")
        if path:
            try:
                products, day, data = load_session(path)
                if "floor_layout" in data:
                    validate_state(data["floor_layout"])
                self.products, self.source = products, data.get("source", "")
                self.file_label.setText(Path(self.source).name or "JSONから復元")
                self.update_dates()
                self.day.setDate(QDate(day.year, day.month, day.day))
                self.layout_choice.setCurrentIndex(1 if data.get("layout") == "row" else 0)
                self.page_rows.setValue(data.get("rows_per_page", 20))
                if "floor_layout" in data:
                    self.floor.restore(data["floor_layout"])
                    self.floor.schedule_save()
                self.refresh()
                self.dirty = False
                self.notice.setText("一時保存から復元しました。商品一覧と配置を復元できます。")
            except Exception as exc:
                self.error(exc)

    def export(self):
        if not any(p.included for p in self.visible):
            self.error(AppError("選択日に出力対象の商品がありません。"))
            return
        path = self.save_path("Excel出力", "Excel (*.xlsx)", ".xlsx")
        if path:
            try:
                if self.floor.source and Path(path).resolve() == Path(self.floor.source).resolve():
                    raise AppError("元のレイアウトExcelには上書きできません。")
                pages = export_excel(path, self.visible, self.selected_day(), self.source, self.page_rows.value(), self.layout_name())
                QMessageBox.information(self, "出力完了", f"{pages}ページ分を出力しました。\n{path}")
            except Exception as exc:
                self.error(exc)


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("PrizeSwapInstructionMaker")
    app.setStyle("Fusion")
    window = Window()
    window.show()
    sys.exit(app.exec())
