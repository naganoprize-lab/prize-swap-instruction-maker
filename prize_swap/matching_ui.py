"""Reviewable, reversible matching overrides without editing the source workbook."""

from copy import deepcopy
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QDialogButtonBox,
    QPushButton,
    QFileDialog,
    QMessageBox,
    QLineEdit,
)
from .machine_matching import (
    normalize_number,
    normalize_reference,
    read_reference_rows,
    prepare_references,
    resolve_matches,
    MATCH_LABELS,
    display_identity,
)
from .core import AppError


def show_matching(owner):
    dialog = QDialog(owner)
    dialog.setWindowTitle("管理ナンバー照合・手動確認")
    dialog.resize(1150, 700)
    outer = QVBoxLayout(dialog)
    note = QLabel(
        "元Excel・配置・商品名は変更しません。手動設定は「自動」「照合しない」または左右を含む機械番号です。矛盾した自動対応は実績を割り当てず、レイアウト由来の値だけを使います。"
    )
    note.setWordWrap(True)
    outer.addWidget(note)
    search = QLineEdit()
    search.setPlaceholderText("管理ナンバー・機械番号・照合要確認の理由を検索")
    outer.addWidget(search)
    tabs = QTabWidget()
    outer.addWidget(tabs, 1)
    from .theme import style_table

    table = QTableWidget(len(owner.machines), 6)
    style_table(table)
    table.setHorizontalHeaderLabels(
        ["管理ナンバー", "機械番号（レイアウト）", "照合先", "照合方法", "状態・理由", "手動設定"]
    )
    for i, m in enumerate(owner.machines):
        values = [
            m.source_key or "—",
            m.direct_number_raw or m.number or "—",
            m.resolved_number or "—",
            MATCH_LABELS[m.match_method],
            m.match_message,
            "自動" if m.manual_number is None else m.manual_number or "照合しない",
        ]
        for j, text in enumerate(values):
            item = QTableWidgetItem(text)
            if j != 5:
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            item.setData(Qt.ItemDataRole.UserRole, m.uid)
            table.setItem(i, j, item)
    table.setColumnWidth(4, 360)
    table.setColumnWidth(3, 190)
    table.setColumnWidth(5, 150)
    table.setSortingEnabled(True)
    tabs.addTab(table, "マシンごとの照合")
    page = QDialog()
    page.setWindowFlags(Qt.WindowType.Widget)
    layout = QVBoxLayout(page)
    references = QTableWidget(len(owner.number_references), 2)
    style_table(references)
    references.setHorizontalHeaderLabels(["管理ナンバー", "機械番号"])
    for i, row in enumerate(owner.number_references):
        references.setItem(i, 0, QTableWidgetItem(row["source_key"]))
        references.setItem(i, 1, QTableWidgetItem(row["number"]))
    references.horizontalHeader().setStretchLastSection(True)
    layout.addWidget(references)
    row = QHBoxLayout()
    layout.addLayout(row)

    def add():
        references.insertRow(references.rowCount())

    def remove():
        for i in sorted({p.row() for p in references.selectedIndexes()}, reverse=True):
            references.removeRow(i)

    def load():
        path, _ = QFileDialog.getOpenFileName(
            dialog, "管理ナンバー参考表のあるExcel", "", "Excel (*.xlsx *.xlsm)"
        )
        if not path:
            return
        try:
            from openpyxl import load_workbook

            workbook = load_workbook(path, read_only=True, data_only=True)
            try:
                rows = read_reference_rows(workbook)
            finally:
                workbook.close()
            if not rows:
                raise AppError("管理ナンバー参考表が見つかりません。")
            references.setRowCount(len(rows))
            for i, r in enumerate(rows):
                for j, k in enumerate(("source_key", "number")):
                    references.setItem(i, j, QTableWidgetItem(r[k]))
        except Exception as exc:
            QMessageBox.warning(dialog, "参考表読込", str(exc))

    for label, callback in [("行追加", add), ("選択行削除", remove), ("参考表Excel読込", load)]:
        b = QPushButton(label)
        b.clicked.connect(callback)
        row.addWidget(b)
    tabs.addTab(page, "管理ナンバー参考表")

    def filter_rows(text):
        for i in range(table.rowCount()):
            table.setRowHidden(
                i,
                bool(text)
                and not any(
                    text.casefold() in table.item(i, j).text().casefold()
                    for j in range(table.columnCount())
                ),
            )

    search.textChanged.connect(filter_rows)
    buttons = QDialogButtonBox(
        QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
    )
    outer.addWidget(buttons)
    buttons.rejected.connect(dialog.reject)

    def save():
        try:
            rows = []
            for i in range(references.rowCount()):
                key = references.item(i, 0).text().strip() if references.item(i, 0) else ""
                number = references.item(i, 1).text().strip() if references.item(i, 1) else ""
                if key or number:
                    rows.append(dict(source_key=key, number=number))
            overrides = {}
            for i in range(table.rowCount()):
                text = table.item(i, 5).text().strip()
                uid = table.item(i, 5).data(Qt.ItemDataRole.UserRole)
                if text in ("", "自動"):
                    overrides[uid] = None
                elif text == "照合しない":
                    overrides[uid] = ""
                else:
                    number = normalize_number(text)
                    if not number:
                        raise AppError(
                            "手動の機械番号は「20111右」「20111-02」のように左右まで指定してください。"
                        )
                    overrides[uid] = number
            owner.apply_matching(rows, overrides)
            dialog.accept()
        except Exception as exc:
            QMessageBox.warning(dialog, "照合設定", str(exc))

    buttons.accepted.connect(save)
    dialog.exec()
