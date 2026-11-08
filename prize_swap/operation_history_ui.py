"""Searchable embedded audit log; persisted records are not executable undo commands."""

import json
from pathlib import Path
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QPlainTextEdit,
)
from .analysis_views import make_table
from .analytics_page import fill_table


class OperationHistoryPanel(QWidget):
    def __init__(self, owner):
        super().__init__()
        self.owner = owner
        outer = QVBoxLayout(self)
        self.search = QLineEdit()
        self.search.setPlaceholderText("日時・操作・対象を検索")
        outer.addWidget(self.search)
        row = QHBoxLayout()
        outer.addLayout(row)
        for label, callback in [
            ("元に戻す", owner.instruction_history.undo),
            ("やり直す", owner.instruction_history.redo),
        ]:
            b = QPushButton(label)
            b.clicked.connect(callback)
            row.addWidget(b)
        row.addWidget(QLabel("Undo/Redoは起動中の履歴です。下の監査履歴は保存・復元されます。"), 1)
        self.table = make_table([], [])
        outer.addWidget(self.table, 2)
        self.detail = QPlainTextEdit()
        self.detail.setReadOnly(True)
        outer.addWidget(self.detail, 1)
        self.search.textChanged.connect(self.refresh)
        self.table.itemSelectionChanged.connect(self.show_detail)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(80)
        self.timer.timeout.connect(self.refresh)
        owner.workflow.changed.connect(self.invalidate)
        self.refresh()

    def invalidate(self, *_):
        if self.isVisible():
            self.timer.start()

    def showEvent(self, event):
        super().showEvent(event)
        self.refresh()

    def refresh(self, *_):
        needle = self.search.text().casefold()
        self.records = [
            r
            for r in reversed(self.owner.workflow.data["audit_log"])
            if needle in " ".join([r["timestamp"], r["description"], r["target_id"]]).casefold()
        ]

        def summary(value):
            if value is None:
                return "—"
            text = json.dumps(value, ensure_ascii=False, default=str)
            return text[:140] + ("…" if len(text) > 140 else "")

        fill_table(
            self.table,
            ["操作日時", "操作内容", "対象", "変更前", "変更後"],
            [
                [
                    r["timestamp"].replace("T", " "),
                    r["description"],
                    r["target_id"],
                    summary(r["before_value"]),
                    summary(r["after_value"]),
                ]
                for r in self.records
            ],
        )
        for row, r in enumerate(self.records):
            self.table.item(row, 0).setData(Qt.ItemDataRole.UserRole, r["operation_id"])
        self.table.setColumnWidth(0, 200)
        self.table.setColumnWidth(1, 220)
        self.table.setColumnWidth(2, 180)

    def show_detail(self):
        selected = self.table.selectionModel().selectedRows()
        if not selected:
            return
        item = self.table.item(selected[0].row(), 0)
        if item is None:
            return
        key = item.data(Qt.ItemDataRole.UserRole)
        r = next((r for r in self.records if r["operation_id"] == key), None)
        if r:
            self.detail.setPlainText(json.dumps(r, ensure_ascii=False, indent=2, default=str))
