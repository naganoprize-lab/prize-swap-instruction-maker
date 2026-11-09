"""Embedded preflight with explicit warning approval and actionable row links."""

from dataclasses import asdict
from hashlib import sha256
import json
from PySide6.QtCore import QTimer, Qt, Signal
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QLineEdit
from .analysis_views import make_table
from .analytics_page import fill_table
from .instruction_validation import validate_instructions
from .core import AppError
from .theme import analysis_color, contrast_text
from PySide6.QtGui import QColor


class PreflightPending(AppError):
    pass


class PreflightPanel(QWidget):
    validated = Signal()

    def __init__(self, owner):
        super().__init__()
        self.owner = owner
        self.issues = []
        self.pending = None
        self.approved = None
        self.signature = ""
        outer = QVBoxLayout(self)
        self.summary = QLabel()
        outer.addWidget(self.summary)
        self.search = QLineEdit()
        self.search.setPlaceholderText("マシン・問題・対応方法を検索")
        self.search.textChanged.connect(self.fill)
        outer.addWidget(self.search)
        self.table = make_table([], [])
        outer.addWidget(self.table, 1)
        self.table.cellDoubleClicked.connect(self.locate)
        row = QHBoxLayout()
        outer.addLayout(row)
        self.continue_button = QPushButton("警告を確認して続行")
        self.continue_button.clicked.connect(self.continue_output)
        row.addWidget(self.continue_button)
        refresh = QPushButton("再チェック")
        refresh.clicked.connect(self.refresh)
        row.addWidget(refresh)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(180)
        self.timer.timeout.connect(self.refresh)
        owner.model.edited.connect(self.invalidate)
        owner.floor.changed.connect(self.invalidate)

    def invalidate(self, *_):
        self.timer.start()

    def refresh(self, *_):
        w = self.owner
        self.timer.stop()
        self.signature = sha256(
            json.dumps(
                {
                    "products": [asdict(p) | {"day": p.day.isoformat()} for p in w.visible],
                    "machines": [
                        (m.uid, m.number, m.source_key, m.resolved_number)
                        for m in w.floor.physical_machines
                    ],
                    "refs": w.floor.number_references,
                },
                sort_keys=True,
                ensure_ascii=False,
                default=str,
            ).encode()
        ).hexdigest()
        self.issues = validate_instructions(
            w.visible, w.floor.physical_machines, w.floor.number_references, w.selected_day()
        )
        self.fill()
        self.validated.emit()
        return self.issues

    def fill(self, *_):
        labels = {"error": "エラー", "warning": "警告", "info": "情報"}
        colors = {"error": "#F4A6A6", "warning": "#F8C88A", "info": "#B8BEC6"}
        self.shown = [
            i
            for i in self.issues
            if self.search.text().casefold()
            in " ".join([i.machine_key, i.message, i.suggested_action]).casefold()
        ]
        fill_table(
            self.table,
            ["重要度", "対象マシン", "問題内容", "推奨する修正", "操作"],
            [
                [
                    labels[i.severity],
                    i.machine_key or "—",
                    i.message,
                    i.suggested_action,
                    "対象行へ",
                ]
                for i in self.shown
            ],
        )
        for row, issue in enumerate(self.shown):
            self.table.item(row, 0).setData(Qt.ItemDataRole.UserRole, issue.issue_id)
            self.table.item(row, 0).setBackground(QColor(analysis_color(colors[issue.severity])))
            self.table.item(row, 0).setForeground(
                QColor(contrast_text(analysis_color(colors[issue.severity])))
            )
            b = QPushButton("対象行へ")
            b.clicked.connect(lambda checked=False, i=issue: self.go_to_issue(i))
            self.table.setCellWidget(row, 4, b)
        self.table.setColumnWidth(2, 420)
        self.table.setColumnWidth(3, 420)
        self.counts = {k: sum(i.severity == k for i in self.issues) for k in labels}
        self.summary.setText(
            "印刷前チェック　"
            + " ／ ".join(f"{labels[k]}：{self.counts[k]}件" for k in labels)
            + "\n商品・運営方法の空欄は印刷可能です。エラーは修正し、警告は内容を確認してください。"
        )
        self.continue_button.setEnabled(not self.counts["error"] and self.pending is not None)

    def request(self, action):
        self.refresh()
        if self.counts["error"] or self.counts["warning"] and self.approved != self.signature:
            self.pending = action if not self.counts["error"] else None
            self.fill()
            self.owner.navigation.activate("instructions")
            self.owner.instruction_tabs.setCurrentWidget(self)
            return False
        return True

    def continue_output(self, *_):
        action = self.pending
        self.refresh()
        if self.counts["error"] or action is None:
            return
        self.approved = self.signature
        self.pending = None
        self.fill()
        action()

    def go_to_issue(self, issue):
        if issue.affected_entry_ids:
            self.owner.navigation.activate("instructions")
            self.owner.instruction_tabs.setCurrentIndex(0)
            self.owner.instruction_map.select_instruction(issue.affected_entry_ids[0])

    def locate(self, row, column):
        key = self.table.item(row, 0).data(Qt.ItemDataRole.UserRole)
        issue = next(i for i in self.issues if i.issue_id == key)
        self.go_to_issue(issue)
