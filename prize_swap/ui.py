from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QDate, QEvent, QItemSelectionModel, QSortFilterProxyModel, Qt
from PySide6.QtGui import QColor, QTextCharFormat
from PySide6.QtPrintSupport import QAbstractPrintDialog, QPrintDialog, QPrintPreviewDialog, QPrintPreviewWidget, QPrinter
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QComboBox, QDateEdit,
    QDialog, QFileDialog, QFrame, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QMainWindow, QMessageBox, QPushButton, QSizePolicy, QSpinBox, QTableView, QTabWidget, QTableWidget, QTableWidgetItem, QToolButton,
    QVBoxLayout, QWidget)

from .core import AppError, METHODS, for_day, load_session, read_products, save_session
from .exporter import export_excel
from .printing import configured_printer, plan_pages, render_document, save_pdf
from .table import COLUMNS, ProductDelegate, ProductModel
from .methods import allocation_status, format_allocations, aggregate_booths, total_booths, booth_value, BoothTotal
from .booth_style import METRIC_COLORS
from .floor import FloorWidget
from .floor_data import validate_state
from .labels_data import LabelStore
from .labels_ui import LabelsWidget

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
        text = " ".join([p.name, p.maker, *p.machines, format_allocations(p)]).casefold()
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
        self.labels = LabelsWidget(lambda: self.products, self.selected_day, lambda: [self.source, self.floor.source], self)
        self.labels.day_requested.connect(self.day.setDate)
        self.labels.edited.connect(self.mark_dirty)
        self.labels.message.connect(lambda text: self.statusBar().showMessage(text, 10000))
        self.labels.reorder_requested.connect(self.reorder_for_labels)
        self.tabs.addTab(self.labels, "入荷ブース用ラベル印刷")
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
        self.model.edited.connect(self.labels.sync)
        self.model.edited.connect(self.update_allocation_notice)
        self.model.allocation_warning.connect(lambda text: self.statusBar().showMessage(text, 15000))
        self.model.invalid.connect(lambda message: self.statusBar().showMessage(message, 10000))
        self.proxy = ProductFilter(self)
        self.proxy.setSourceModel(self.model)
        self.proxy.sort(1, Qt.SortOrder.AscendingOrder)
        self.search.textChanged.connect(self.filter_changed)
        self.filter_choice.currentIndexChanged.connect(self.filter_changed)
        self.table = QTableView()
        self.table.setModel(self.proxy)
        self.table.selectionModel().selectionChanged.connect(self.update_allocation_notice)
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
        for i, width in enumerate([48, 60, 115, 340, 68, 68, 165, 240, 52, 160]):
            self.table.setColumnWidth(i, width)
        self.table.viewport().installEventFilter(self)
        center.addWidget(self.table, 1)
        hint = QLabel("セルで直接編集 / マシン番号は複数可 / 印刷は検索に関係なく選択日の出力対象すべて")
        self.allocation_notice = hint
        self.default_table_hint = hint.text()
        hint.setObjectName("muted")
        hint.setWordWrap(True)
        hint_row=QHBoxLayout()
        hint_row.addWidget(hint,1)
        self.summary_toggle=QToolButton()
        self.summary_toggle.setText("ブース予定集計 ▾")
        self.summary_toggle.setCheckable(True)
        self.summary_toggle.setMaximumHeight(22)
        hint_row.addWidget(self.summary_toggle)
        center.addLayout(hint_row)
        self.booth_summary=QWidget()
        summary_layout=QVBoxLayout(self.booth_summary)
        summary_layout.setContentsMargins(0,0,0,0)
        self.summary_scope=QComboBox()
        self.summary_scope.addItems(["選択日の出力対象全商品", "選択商品"])
        self.summary_scope.currentIndexChanged.connect(self.update_booth_summary)
        summary_layout.addWidget(self.summary_scope)
        self.summary_table=QTableWidget(0,5)
        self.summary_table.setHorizontalHeaderLabels(["運営方法","予定数","割当済み","未割当","超過"])
        self.summary_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.summary_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.summary_table.setMaximumHeight(170)
        summary_layout.addWidget(self.summary_table)
        center.addWidget(self.booth_summary)
        self.booth_summary.hide()
        self.summary_toggle.toggled.connect(self.booth_summary.setVisible)
        self.summary_toggle.toggled.connect(self.update_booth_summary)

        footer, bottom_layout = card(outer)
        footer.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        bottom = QHBoxLayout()
        bottom.addWidget(button("一時保存", self.save_json))
        bottom.addWidget(button("一時保存を開く", self.open_json))
        bottom.addWidget(button("Excel出力", self.export))
        bottom.addStretch()
        self.instruction_settings = QWidget()
        instruction_layout = QHBoxLayout(self.instruction_settings)
        instruction_layout.setContentsMargins(0, 0, 0, 0)
        instruction_layout.addWidget(QLabel("ページ配置"))
        self.layout_choice = QComboBox()
        self.layout_choice.addItems(["左を上から → 右を上から", "左右交互"])
        self.layout_choice.currentIndexChanged.connect(self.mark_dirty)
        instruction_layout.addWidget(self.layout_choice)
        self.print_mode = QComboBox()
        for label,key in (("自動最適化","auto"),("標準","standard"),("コンパクト","compact")):
            self.print_mode.addItem(label,key)
        self.print_mode.currentIndexChanged.connect(self.mark_dirty)
        instruction_layout.addWidget(self.print_mode)
        instruction_layout.addWidget(QLabel("片側上限"))
        self.page_rows = QSpinBox()
        self.page_rows.setRange(1, 50)
        self.page_rows.setValue(20)
        self.page_rows.setSuffix(" 商品")
        self.page_rows.setToolTip("PDF・直接印刷：片側の最大商品数。Excel出力：従来のマシン行数の目安。")
        self.page_rows.valueChanged.connect(self.mark_dirty)
        instruction_layout.addWidget(self.page_rows)
        bottom.addWidget(self.instruction_settings)
        self.tabs.currentChanged.connect(lambda *_: self.instruction_settings.setVisible(self.tabs.currentWidget() is not self.labels))
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
        self.labels.sync()
        self.update_count()
        self.update_allocation_notice()

    def update_count(self):
        self.count.setText(f"{len(self.visible)}件 ／ 出力 {sum(p.included for p in self.visible)}件")
        if hasattr(self, "proxy"):
            self.statusBar().showMessage(f"表示 {self.proxy.rowCount()}件 / 選択日 {len(self.visible)}件　印刷は出力チェックのある商品が対象です。")

    def update_allocation_notice(self, *_):
        if not hasattr(self, "allocation_notice"):
            return
        selected = self.selected_products()
        products = selected or self.visible
        states = [allocation_status(p) for p in products]
        excess = sum(bool(s.excess) for s in states)
        pending = sum(bool(s.unknown or s.unassigned or s.unmapped) for s in states)
        if len(selected) == 1:
            text = f"運営方法：{states[0].message}"
        elif excess or pending:
            text = f"運営方法（{'選択中' if selected else '表示中'}）：台数超過 {excess}件 / 未設定・未割当 {pending}件。セルのツールチップまたはドロップダウンで確認できます。"
        else:
            text = self.default_table_hint
        self.allocation_notice.setText(text)
        self.allocation_notice.setStyleSheet("color: #B42318;" if excess else "color: #677D72;")
        self.update_booth_summary()

    def update_booth_summary(self, *_):
        if not hasattr(self,"summary_table"):
            return
        products=self.selected_products() if self.summary_scope.currentIndex() else self.visible
        active=aggregate_booths(products)
        mapping={r.method:r for r in active}
        totals=[mapping.get(name,BoothTotal(name,0,0,0,0)) for name in dict.fromkeys([*METHODS,*mapping])]
        totals.append(total_booths(active))
        self.summary_table.setRowCount(len(totals))
        for row,total in enumerate(totals):
            for col,field in enumerate(["method","planned","assigned","unassigned","excess"]):
                value=total.method if col==0 else booth_value(total,field)
                item=QTableWidgetItem(value)
                if field in METRIC_COLORS:
                    background,foreground=METRIC_COLORS[field]
                    item.setBackground(QColor(background))
                    item.setForeground(QColor(foreground))
                if total.method=="合計":
                    font=item.font();font.setBold(True);item.setFont(font)
                if total.unknown:
                    item.setToolTip(f"予定未設定の方法が{total.unknown}件あります。既知の予定数の差分だけを集計します。")
                self.summary_table.setItem(row,col,item)

    def confirm_method_allocations(self):
        problems = [f"{p.name}：{allocation_status(p).message}" for p in self.visible if p.included and allocation_status(p).excess]
        if not problems:
            return True
        return QMessageBox.question(self, "運営方法の台数を確認", "予定ブース数を超える実際の割当があります。\n" + "\n".join(problems) + "\n設定を保持したまま出力を続けますか？") == QMessageBox.StandardButton.Yes

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
        self.reorder_for_labels(self.selected_products(), direction)

    def reorder_for_labels(self, selected_products, direction):
        selected = {id(p) for p in selected_products}
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
        if not self.confirm_method_allocations():
            raise AppError("運営方法の台数を確認してから再実行してください。")
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
            plan = render_document(printer, list(self.visible), self.selected_day(), self.page_rows.value(), self.layout_name(), self.print_mode.currentData())
            self.statusBar().showMessage(plan.reason, 15000)
            return plan
        except Exception as exc:
            self.error(exc)

    def preview(self):
        if self.tabs.currentWidget() is self.labels:
            return self.labels.preview()
        try:
            self.print_products()
            printer = configured_printer()
            dialog = QPrintPreviewDialog(printer, self)
            dialog.setWindowTitle("印刷プレビュー — A4横向き")
            dialog.resize(1300, 850)
            controls=QWidget(dialog)
            controls_layout=QHBoxLayout(controls)
            controls_layout.addWidget(QLabel("印刷レイアウト"))
            mode=QComboBox(controls)
            for label,key in (("自動最適化","auto"),("標準（読みやすさ優先）","standard"),("コンパクト（用紙節約）","compact")):
                mode.addItem(label,key)
            mode.setCurrentIndex(self.print_mode.currentIndex())
            controls_layout.addWidget(mode)
            info=QLabel();info.setWordWrap(True);controls_layout.addWidget(info,1)
            dialog.layout().insertWidget(0,controls)
            def paint(target):
                plan=self.paint_printer(target)
                if plan:info.setText(plan.reason)
            def change(index):
                self.print_mode.setCurrentIndex(index)
                dialog.findChild(QPrintPreviewWidget).updatePreview()
            mode.currentIndexChanged.connect(change)
            dialog.paintRequested.connect(paint)
            dialog.exec()
        except Exception as exc:
            self.error(exc)

    def print_direct(self):
        if self.tabs.currentWidget() is self.labels:
            return self.labels.print_direct()
        try:
            self.print_products()
            printer = configured_printer()
            area = printer.pageRect(QPrinter.Unit.Point)
            plan = plan_pages(self.visible, area.width(), area.height(), self.page_rows.value(), self.layout_name(), self.print_mode.currentData())
            dialog = QPrintDialog(printer, self)
            dialog.setMinMax(1, len(plan.pages))
            dialog.setOption(QAbstractPrintDialog.PrintDialogOption.PrintPageRange, True)
            if dialog.exec() == QDialog.DialogCode.Accepted:
                if self.paint_printer(printer):
                    self.statusBar().showMessage("印刷ジョブを送信しました。実際の出力はプリンターで確認してください。", 10000)
        except Exception as exc:
            self.error(exc)

    def export_pdf(self):
        if self.tabs.currentWidget() is self.labels:
            return self.labels.export_pdf()
        try:
            products = self.print_products()
            path = self.save_path("PDF保存", "PDF (*.pdf)", ".pdf")
            if path:
                plan = save_pdf(path, products, self.selected_day(), self.page_rows.value(), self.layout_name(), self.source, self.print_mode.currentData())
                self.statusBar().showMessage(f"PDFを保存しました：{path} {plan.reason}", 15000)
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
                save_session(path, self.products, self.selected_day(), self.source, self.layout_name(), self.page_rows.value(), floor_layout=self.floor.state(), label_state=self.labels.state(), print_mode=self.print_mode.currentData())
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
                label_store = LabelStore.restore(data.get("label_printing"), products)
                self.products, self.source = products, data.get("source", "")
                self.labels.store = label_store
                self.labels.apply_options()
                self.file_label.setText(Path(self.source).name or "JSONから復元")
                self.update_dates()
                self.day.setDate(QDate(day.year, day.month, day.day))
                self.layout_choice.setCurrentIndex(1 if data.get("layout") == "row" else 0)
                self.page_rows.setValue(data.get("rows_per_page", 20))
                self.print_mode.setCurrentIndex(self.print_mode.findData(data.get("print_mode","auto")))
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
        if not self.confirm_method_allocations():
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
    from .fonts import initialize_fonts
    initialize_fonts()
    app.setApplicationName("PrizeSwapInstructionMaker")
    app.setStyle("Fusion")
    window = Window()
    window.show()
    sys.exit(app.exec())
