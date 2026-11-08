"""Responsive overview around the existing, fully retained machine editors."""

from PySide6.QtCore import Qt, QTimer, QRectF
from PySide6.QtGui import QPainter, QColor, QPen
from PySide6.QtWidgets import (
    QWidget,
    QFrame,
    QLabel,
    QVBoxLayout,
    QHBoxLayout,
    QGridLayout,
    QSplitter,
    QScrollArea,
    QPushButton,
    QComboBox,
    QSizePolicy,
)
from .analytics_data import machine_record, formatted
from .machine_matching import display_identity, MATCH_LABELS
from .floor_status import filter_status
from .theme import manager, analysis_color, contrast_text, tokens


class KpiCard(QFrame):
    def __init__(self, title, parent=None):
        super().__init__(parent)
        self.background = "#FFFFFF"
        self.setMinimumWidth(120)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 7, 10, 7)
        layout.setSpacing(2)
        self.title = QLabel(title)
        self.value = QLabel("—")
        self.value.setObjectName("kpiValue")
        self.value.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.value.setWordWrap(True)
        self.note = QLabel()
        self.note.setWordWrap(True)
        self.note.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.title.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        for label in (self.title, self.value, self.note):
            layout.addWidget(label)

    def set_value(self, text, background, note):
        self.value.setText(text)
        self.background = analysis_color(background)
        self.note.setText(note)
        ink = "light" if contrast_text(self.background) == "#F1F5F9" else "dark"
        for label in (self.title, self.value, self.note):
            label.setProperty("kpiInk", ink)
            label.style().unpolish(label)
            label.style().polish(label)
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setBrush(QColor(self.background))
        p.setPen(QPen(QColor(tokens()["border"]), 1))
        p.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 7, 7)


def build_overview(dialog, tabs, scroll, panel):
    d = dialog
    d.header = QFrame()
    d.header.setObjectName("card")
    head = QVBoxLayout(d.header)
    head.setContentsMargins(12, 8, 12, 8)
    head.setSpacing(3)
    row = QHBoxLayout()
    head.addLayout(row)
    d.identity_label = QLabel(display_identity(d.updated))
    d.identity_label.setObjectName("pageTitle")
    row.addWidget(d.identity_label)
    d.header_meta = QLabel()
    d.header_meta.setTextFormat(Qt.TextFormat.PlainText)
    d.header_meta.setWordWrap(True)
    d.header_meta.setMaximumHeight(42)
    row.addStretch(1)
    d.analysis_scope = QComboBox()
    for text, key in (
        ("このマシン", "machine"),
        ("同じ機種と比較", "machine_type"),
        ("同じ運営方法と比較", "method"),
    ):
        d.analysis_scope.addItem(text, key)
    row.addWidget(d.analysis_scope)
    d.analysis_button = QPushButton("売上分析で表示")
    d.analysis_button.setToolTip(
        "未保存の編集は分析に反映されません。保存後の値で分析する場合は先に保存してください。"
    )
    row.addWidget(d.analysis_button)
    d.analysis_button.clicked.connect(lambda: open_analysis(d))
    head.addWidget(d.header_meta)
    d.header_product = QLabel(d.updated.product or "—")
    d.header_product.setTextFormat(Qt.TextFormat.PlainText)
    d.header_product.setWordWrap(True)
    d.header_product.setMaximumHeight(42)
    d.header_product.setToolTip(d.updated.product)
    head.addWidget(d.header_product)
    d.layout().insertWidget(0, d.header)
    d.splitter = QSplitter(Qt.Orientation.Horizontal)
    d.splitter.setChildrenCollapsible(False)
    left = QWidget()
    left_layout = QVBoxLayout(left)
    left_layout.setContentsMargins(0, 0, 0, 0)
    grid = QGridLayout()
    d.kpi_grid = grid
    grid.setSpacing(6)
    for column in range(3):
        grid.setColumnStretch(column, 1)
    left_layout.addLayout(grid)
    d.kpis = {}
    for index, (key, label) in enumerate(
        (
            ("ratio", "アベ比"),
            ("sales", "売上金額"),
            ("po_target", "適正PO率"),
            ("cost_rate", "原価率"),
            ("po_rate", "PO率"),
            ("days", "投入日数"),
        )
    ):
        card = KpiCard(label)
        grid.addWidget(card, index // 3, index % 3)
        d.kpis[key] = card
    scroll.takeWidget()  # setWidget deletes the previous owned widget unless released.
    panel.setParent(left)
    left_layout.addWidget(panel)
    scroll.setWidget(left)
    tabs.removeTab(0)
    d.splitter.addWidget(scroll)
    scroll.show()  # removeTab explicitly hides its page; reparenting does not undo it.
    right_scroll = QScrollArea()
    right_scroll.setWidgetResizable(True)
    d.evaluation_panel = QWidget()
    d.evaluation_layout = QVBoxLayout(d.evaluation_panel)
    d.evaluation_layout.setContentsMargins(10, 8, 10, 8)
    d.evaluation_layout.addWidget(QLabel("5項目の状態分析"))
    d.evaluation_note = QLabel("各指標を個別に確認します。総合成績の平均点は作りません。")
    d.evaluation_note.setWordWrap(True)
    d.evaluation_layout.addWidget(d.evaluation_note)
    from .radar import RadarChart

    d.radar = RadarChart()
    d.radar.setMinimumHeight(340)
    d.evaluation_layout.addWidget(d.radar)
    from .analysis_views import make_table

    d.score_table = make_table(["項目", "実測値", "状態", "目標・意味"], [])
    d.score_table.setMinimumHeight(200)
    d.evaluation_layout.addWidget(d.score_table)
    d.evaluation_layout.addStretch()
    right_scroll.setWidget(d.evaluation_panel)
    d.splitter.addWidget(right_scroll)
    d.splitter.setSizes([560, 440])
    if d.width() < 960:
        d.splitter.setOrientation(Qt.Orientation.Vertical)
    d.overview_scroll = QScrollArea()
    d.overview_scroll.setWidgetResizable(True)
    d.overview_scroll.setWidget(d.splitter)
    d.splitter.setMinimumHeight(500 if d.width() < 960 else 320)
    tabs.insertTab(0, d.overview_scroll, "現在投入中の商品・状態分析")
    tabs.setCurrentIndex(0)
    d.name.textChanged.connect(lambda: d.header_product.setText(d.name.toPlainText() or "—"))
    d.method.currentTextChanged.connect(lambda *_: refresh_overview(d))
    manager().changed.connect(d.refresh_overview)
    refresh_overview(d)


def refresh_overview(d):
    m, o = d.updated, d.owner
    r = machine_record(m, o.reference_day(), o.analysis_settings, o.assignments())
    d.header_meta.setText(
        f'管理 {m.source_key or "—"} ｜ {r["machine_type"] or "機種 —"} ｜ {d.method.currentText() or "方法 —"}\n'
        f'{MATCH_LABELS[m.match_method]} ｜ 更新 {r["updated_at"] or "不明"} ｜ {r["period_text"]}'
    )
    from .po_evaluation import evaluate_po

    po = evaluate_po(m, o.analysis_settings)
    r["po_target"] = po["target"]
    r["sources"]["po_target"] = dict(source=po["cost_source"] + " / " + po["price_source"])
    for key, card in d.kpis.items():
        mode = {
            "ratio": "ratio",
            "sales": "sales_metric",
            "po_target": "po_rate",
            "cost_rate": "cost_rate",
            "po_rate": "po_rate",
            "days": "age",
        }[key]
        from .analysis_settings import sales_boundaries

        metric_settings = o.analysis_settings | {
            "_sales_bounds": sales_boundaries(o.machines, o.analysis_settings)
        }
        color, label = filter_status(m, mode, o.reference_day(), settings=metric_settings)
        text = formatted("po_rate" if key == "po_target" else key, r[key])
        if key == "po_target":
            label = (
                (
                    f'原価 {po["prize_cost"]:,.0f}円'
                    if po["prize_cost"] is not None
                    else "原価未確認"
                )
                + (
                    f'／1プレイ {po["play_price"]:,.0f}円'
                    if po["play_price"] is not None
                    else "／料金不明"
                )
                + ("（仮設定）" if po["provisional"] else "")
            )
        if r[key] is not None and key in ("sales", "average"):
            text += " 円"
        elif r[key] is not None and key == "days" and r[key] >= 0:
            text += " 日"
        card.set_value(text, color, label)
        card.setToolTip(r["sources"][key]["source"])
    if hasattr(d, "radar"):
        from .radar import evaluate, score_record, AGE_EXPLANATION
        from PySide6.QtWidgets import QTableWidgetItem

        peers = [score_record(m, o.reference_day(), o.analysis_settings) for m in o.machines]
        d.scores = evaluate(m, o.reference_day(), o.analysis_settings, peers, o.assignments())
        d.radar.set_axes(d.scores)
        d.evaluation_note.setText(
            AGE_EXPLANATION + "\n" + "\n".join(a["warning"] for a in d.scores if a["warning"])
        )
        d.score_table.setSortingEnabled(False)
        d.score_table.setRowCount(5)
        for i, a in enumerate(d.scores):
            for j, value in enumerate(
                (
                    a["label"],
                    a["actual"],
                    "未評価" if a["score"] is None else "評価可能",
                    a["goal"],
                )
            ):
                item = QTableWidgetItem(value)
                item.setToolTip(d.radar.tooltip(a))
                d.score_table.setItem(i, j, item)
        d.score_table.setSortingEnabled(True)
        d.score_table.setColumnWidth(0, 80)
        d.score_table.setColumnWidth(1, 170)
        d.score_table.setColumnWidth(2, 75)


def open_analysis(d):
    from PySide6.QtWidgets import QMessageBox

    if (
        d.name.toPlainText().strip() != d.original.product
        or d.method.currentText().strip() != d.original.operation_method
    ):
        if (
            QMessageBox.question(
                d, "未保存の編集", "未保存の編集を反映せず売上分析へ移動しますか？"
            )
            != QMessageBox.StandardButton.Yes
        ):
            return
    uid, scope, owner = d.original.uid, d.analysis_scope.currentData(), d.owner
    d.reject()
    QTimer.singleShot(0, lambda: owner.analytics_requested.emit(uid, scope))
