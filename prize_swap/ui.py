from __future__ import annotations

import html
import sys
from datetime import date
from pathlib import Path

from PySide6.QtCore import QDate, Qt
from PySide6.QtGui import QColor, QTextCharFormat
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDateEdit, QDialog, QFileDialog,
    QFormLayout, QGridLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QListWidget, QMainWindow, QMessageBox, QPlainTextEdit, QPushButton, QScrollArea, QSpinBox, QSplitter,
    QTableWidget, QTableWidgetItem, QTextBrowser, QVBoxLayout, QWidget,
)

from .core import (AppError, LIMITS, METHODS, for_day, load_session, machine_numbers,
                   quantity, read_products, save_session, split_values)
from .exporter import LABELS, export_excel, positions, product_values

COLUMNS = ["出力", "順序", "メーカー", "商品名", "入数", "CS数", "マシン番号", "運営方法", "優先", "獲得個数制限"]


def button(text, callback):
    b = QPushButton(text)
    b.clicked.connect(callback)
    return b


class Window(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("プライズ景品 入替指示書メーカー")
        self.setMinimumSize(1100, 700)
        self.resize(1500, 900)
        self.products, self.visible = [], []
        self.source = ""
        self.dirty = False
        root = QWidget()
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        top = QHBoxLayout()
        top.addWidget(button("Excelを開く", self.open_excel))
        self.file_label = QLabel("ファイル未選択")
        self.file_label.setMaximumWidth(420)
        top.addWidget(self.file_label)
        top.addWidget(QLabel("稼働予定日"))
        self.day = QDateEdit(QDate.currentDate())
        self.day.setCalendarPopup(True)
        self.day.setDisplayFormat("yyyy/MM/dd")
        self.day.dateChanged.connect(self.refresh)
        top.addWidget(self.day)
        self.dates = QComboBox()
        self.dates.setMinimumWidth(145)
        self.dates.activated.connect(self.pick_date)
        top.addWidget(self.dates)
        self.count = QLabel("0件")
        top.addWidget(self.count)
        top.addStretch()
        outer.addLayout(top)
        self.notice = QLabel("Excelの「縦」シートから商品を読み込みます。日付を変えても編集内容は保持します。")
        self.notice.setWordWrap(True)
        outer.addWidget(self.notice)
        splitter = QSplitter()
        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.ExtendedSelection)
        self.table.setAlternatingRowColors(True)
        self.table.setWordWrap(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        for i, width in enumerate([48, 62, 110, 310, 65, 65, 150, 130, 48, 130]):
            self.table.setColumnWidth(i, width)
        self.table.itemChanged.connect(self.cell_changed)
        self.table.itemSelectionChanged.connect(self.selection_changed)
        splitter.addWidget(self.table)
        details = QWidget()
        details.setMinimumWidth(285)
        details.setMinimumHeight(850)
        details.setMaximumWidth(400)
        form = QVBoxLayout(details)
        self.selection_label = QLabel("商品を選択してください")
        form.addWidget(self.selection_label)
        form.addWidget(QLabel("マシン番号（カンマ区切り・改行貼付可）"))
        self.machine_entry = QPlainTextEdit()
        self.machine_entry.setMaximumHeight(64)
        self.machine_entry.setPlaceholderText("20085右,20098左")
        form.addWidget(self.machine_entry)
        form.addWidget(button("マシン番号を追加", self.add_machine))
        self.machine_list = QListWidget()
        self.machine_list.setMaximumHeight(100)
        self.machine_list.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        form.addWidget(self.machine_list)
        form.addWidget(button("選択した番号を削除", self.remove_machine))
        form.addWidget(QLabel("運営方法（複数選択）"))
        grid = QGridLayout()
        self.method_checks = []
        for i, method in enumerate(METHODS):
            check = QCheckBox(method)
            self.method_checks.append(check)
            grid.addWidget(check, i // 2, i % 2)
        form.addLayout(grid)
        self.custom = QLineEdit()
        self.custom.setPlaceholderText("任意の運営方法（カンマ区切り）")
        form.addWidget(self.custom)
        self.priority = QCheckBox("優先入替")
        self.included = QCheckBox("出力対象")
        form.addWidget(self.priority)
        form.addWidget(self.included)
        self.limit = QComboBox()
        self.limit.addItems(LIMITS)
        form.addWidget(self.limit)
        self.apply_machines = QCheckBox("マシン番号を適用")
        self.apply_methods = QCheckBox("運営方法を適用")
        self.apply_priority = QCheckBox("優先入替を適用")
        self.apply_limit = QCheckBox("個数制限を適用")
        self.apply_included = QCheckBox("出力対象を適用")
        self.apply_flags = [self.apply_machines, self.apply_methods, self.apply_priority, self.apply_limit, self.apply_included]
        form.addWidget(QLabel("一括編集：変更する項目だけチェック"))
        for flag in self.apply_flags:
            form.addWidget(flag)
        form.addWidget(button("選択商品に適用", self.apply_details))
        hint = QLabel("Ctrl/Shiftで複数選択できます。\n商品名・数量・出力順序は表で直接編集。\n一括編集値は先頭の選択商品を表示します。")
        hint.setWordWrap(True)
        form.addWidget(hint)
        form.addStretch()
        detail_scroll = QScrollArea()
        detail_scroll.setWidgetResizable(True)
        detail_scroll.setMinimumWidth(310)
        detail_scroll.setMaximumWidth(420)
        detail_scroll.setWidget(details)
        splitter.addWidget(detail_scroll)
        splitter.setStretchFactor(0, 1)
        outer.addWidget(splitter)
        bottom = QHBoxLayout()
        bottom.addWidget(button("一時保存（JSON）", self.save_json))
        bottom.addWidget(button("一時保存を開く", self.open_json))
        bottom.addStretch()
        bottom.addWidget(QLabel("配置"))
        self.layout_choice = QComboBox()
        self.layout_choice.addItems(["左を上から→右を上から", "左右交互"])
        self.layout_choice.currentIndexChanged.connect(self.mark_dirty)
        bottom.addWidget(self.layout_choice)
        bottom.addWidget(QLabel("1ブロックの行数"))
        self.page_rows = QSpinBox()
        self.page_rows.setRange(1, 50)
        self.page_rows.setValue(20)
        self.page_rows.valueChanged.connect(self.mark_dirty)
        bottom.addWidget(self.page_rows)
        bottom.addWidget(button("プレビュー", self.preview))
        bottom.addWidget(button("Excel出力", self.export))
        outer.addLayout(bottom)

    def mark_dirty(self, *_):
        self.dirty = True

    def error(self, exc):
        QMessageBox.warning(self, "処理できませんでした", str(exc))

    def confirm_discard(self):
        return not self.dirty or QMessageBox.question(self, "編集中の内容", "未保存の編集内容があります。破棄して続けますか？") == QMessageBox.StandardButton.Yes

    def closeEvent(self, event):
        event.accept() if self.confirm_discard() else event.ignore()

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

    def refresh(self, *_):
        self.table.blockSignals(True)
        self.visible = for_day(self.products, self.selected_day())
        self.table.setRowCount(len(self.visible))
        for row, p in enumerate(self.visible):
            vals = [p.included, p.order, p.maker, p.name, p.units, p.cases, ", ".join(p.machines), ", ".join(p.methods), p.priority, p.limit]
            for col, value in enumerate(vals):
                item = QTableWidgetItem(str(value))
                if col in (0, 8):
                    item.setText("")
                    item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsUserCheckable)
                    item.setCheckState(Qt.CheckState.Checked if value else Qt.CheckState.Unchecked)
                elif col in (2, 9):
                    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                if p.priority:
                    item.setBackground(QColor("#FCE4EC"))
                self.table.setItem(row, col, item)
        self.table.blockSignals(False)
        self.count.setText(f"{len(self.visible)}件（出力 {sum(p.included for p in self.visible)}件）")
        self.selection_changed()

    def cell_changed(self, item):
        p = self.visible[item.row()]
        col, value = item.column(), item.text().strip()
        try:
            if col == 0:
                p.included = item.checkState() == Qt.CheckState.Checked
            elif col == 1:
                p.order = int(value)
            elif col == 3:
                if not value:
                    raise AppError("商品名は必須です。")
                p.name = value
            elif col in (4, 5):
                setattr(p, "units" if col == 4 else "cases", quantity(value))
            elif col == 6:
                p.machines = machine_numbers(value)
            elif col == 7:
                p.methods = split_values(value)
            elif col == 8:
                p.priority = item.checkState() == Qt.CheckState.Checked
            self.dirty = True
        except (ValueError, AppError) as exc:
            self.error(exc if col != 1 else AppError("出力順序は整数で入力してください。"))
        # Preserve selection while updating computed display and highlighting.
        self.table.blockSignals(True)
        for c in range(self.table.columnCount()):
            self.table.item(item.row(), c).setBackground(QColor("#FCE4EC") if p.priority else QColor("white"))
        restored = {1: p.order, 3: p.name, 4: p.units, 5: p.cases, 6: ", ".join(p.machines), 7: ", ".join(p.methods)}
        if col in restored:
            item.setText(str(restored[col]))
        self.table.blockSignals(False)
        self.count.setText(f"{len(self.visible)}件（出力 {sum(p.included for p in self.visible)}件）")
        self.selection_changed()

    def selected_products(self):
        return [self.visible[i.row()] for i in self.table.selectionModel().selectedRows()]

    def selection_changed(self):
        products = self.selected_products()
        self.selection_label.setText(f"{len(products)}件を選択中")
        for flag in self.apply_flags:
            flag.setChecked(len(products) == 1)
        self.machine_list.clear()
        self.machine_entry.clear()
        if not products:
            return
        p = products[0]
        self.machine_list.addItems(p.machines)
        for check in self.method_checks:
            check.setChecked(check.text() in p.methods)
        self.custom.setText(", ".join(m for m in p.methods if m not in METHODS))
        self.priority.setChecked(p.priority)
        self.included.setChecked(p.included)
        self.limit.setCurrentText(p.limit)

    def add_machine(self):
        try:
            numbers = machine_numbers(self.machine_entry.toPlainText())
            existing = [self.machine_list.item(i).text() for i in range(self.machine_list.count())]
            for number in numbers:
                if number not in existing:
                    self.machine_list.addItem(number)
                    existing.append(number)
            self.machine_entry.clear()
            self.apply_machines.setChecked(True)
        except AppError as exc:
            self.error(exc)

    def remove_machine(self):
        for item in self.machine_list.selectedItems():
            self.machine_list.takeItem(self.machine_list.row(item))
        self.apply_machines.setChecked(True)

    def apply_details(self):
        products = self.selected_products()
        if not products:
            self.error(AppError("商品を選択してください。"))
            return
        try:
            numbers = [self.machine_list.item(i).text() for i in range(self.machine_list.count())]
            if self.apply_machines.isChecked():
                numbers = machine_numbers("\n".join(numbers + machine_numbers(self.machine_entry.toPlainText())))
            methods = [c.text() for c in self.method_checks if c.isChecked()] + split_values(self.custom.text())
            for p in products:
                if self.apply_machines.isChecked(): p.machines = list(numbers)
                if self.apply_methods.isChecked(): p.methods = list(dict.fromkeys(methods))
                if self.apply_priority.isChecked(): p.priority = self.priority.isChecked()
                if self.apply_limit.isChecked(): p.limit = self.limit.currentText()
                if self.apply_included.isChecked(): p.included = self.included.isChecked()
            self.dirty = True
            self.refresh()
        except AppError as exc:
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
                save_session(path, self.products, self.selected_day(), self.source, self.layout_name(), self.page_rows.value())
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
                self.products, self.source = products, data.get("source", "")
                self.file_label.setText(Path(self.source).name or "JSONから復元")
                self.update_dates()
                self.day.setDate(QDate(day.year, day.month, day.day))
                self.layout_choice.setCurrentIndex(1 if data.get("layout") == "row" else 0)
                self.page_rows.setValue(data.get("rows_per_page", 20))
                self.refresh()
                self.dirty = False
                self.notice.setText("一時保存から復元しました。元Excelがなくても標準レイアウトで出力できます。")
            except Exception as exc:
                self.error(exc)

    def preview(self):
        placements = list(positions(self.visible, self.page_rows.value(), self.layout_name()))
        pages = max((p[0] + 1 for p in placements), default=1)
        lookup = {(page, row, side): p for page, row, side, p in placements}
        doc = ["<p>配置確認用プレビュー：文字の実際の折返し・改ページはExcelの印刷プレビューで最終確認してください。</p>"]
        escape = lambda v: html.escape(str(v)).replace("\n", "<br>")
        for page in range(pages):
            doc.append(f"<h3>入れ替え指示書　{self.selected_day()}　{page + 1}/{pages}</h3><table border='1' cellspacing='0' cellpadding='5' width='100%'><tr>")
            doc.extend(f"<th>{label}</th>" for label in LABELS * 2)
            doc.append("</tr>")
            for row in range(self.page_rows.value()):
                doc.append("<tr>")
                for side in range(2):
                    p = lookup.get((page, row, side))
                    color = "#FCE4EC" if p and p.priority else "#FFFFFF"
                    doc.extend(f"<td bgcolor='{color}'>{escape(v)}</td>" for v in (product_values(p) if p else [""] * 7))
                doc.append("</tr>")
            doc.append("</table><br>")
        dialog = QDialog(self)
        dialog.setWindowTitle("出力レイアウト確認")
        dialog.resize(1300, 750)
        layout = QVBoxLayout(dialog)
        view = QTextBrowser()
        view.setHtml("".join(doc))
        layout.addWidget(view)
        layout.addWidget(button("閉じる", dialog.accept))
        dialog.exec()

    def export(self):
        if not any(p.included for p in self.visible):
            self.error(AppError("選択日に出力対象の商品がありません。"))
            return
        path = self.save_path("Excel出力", "Excel (*.xlsx)", ".xlsx")
        if path:
            try:
                pages = export_excel(path, self.visible, self.selected_day(), self.source, self.page_rows.value(), self.layout_name())
                QMessageBox.information(self, "出力完了", f"{pages}ページ分を出力しました。\n{path}")
            except Exception as exc:
                self.error(exc)


def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setStyleSheet("QWidget { font-family: 'Yu Gothic UI', 'Noto Sans CJK JP', sans-serif; font-size: 12px; color: #172033; } QWidget { background: #F8FAFC; } QTableWidget, QLineEdit, QListWidget, QComboBox, QDateEdit, QSpinBox, QTextBrowser { background: white; } QTableWidget::item:selected { background: #BFDBFE; color: #102A43; } QPushButton { padding: 6px 12px; border: 1px solid #CBD5E1; border-radius: 4px; background: white; } QPushButton:hover { background: #DBEAFE; } QHeaderView::section { background: #E2E8F0; padding: 5px; }")
    window = Window()
    window.show()
    sys.exit(app.exec())
