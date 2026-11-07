"""Responsive overview around the existing, fully retained machine editors."""

from PySide6.QtCore import Qt, QTimer, QRectF
from PySide6.QtGui import QPainter, QColor, QPen
from PySide6.QtWidgets import QWidget, QFrame, QLabel, QVBoxLayout, QHBoxLayout, QGridLayout, QSplitter, QScrollArea, QPushButton, QComboBox
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
        self.note = QLabel()
        self.note.setWordWrap(True)
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
        p.drawRoundedRect(QRectF(self.rect()).adjusted(.5,.5,-.5,-.5), 7, 7)


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
    d.header_meta.setWordWrap(True)
    row.addWidget(d.header_meta, 1)
    d.analysis_scope = QComboBox()
    for text, key in (("このマシン", "machine"), ("同じ機種と比較", "machine_type"), ("同じ運営方法と比較", "method")):
        d.analysis_scope.addItem(text, key)
    row.addWidget(d.analysis_scope)
    d.analysis_button = QPushButton("売上分析で表示")
    d.analysis_button.setToolTip("未保存の編集は分析に反映されません。保存後の値で分析する場合は先に保存してください。")
    row.addWidget(d.analysis_button)
    d.analysis_button.clicked.connect(lambda: open_analysis(d))
    d.header_product = QLabel(d.updated.product or "—")
    d.header_product.setWordWrap(True)
    d.header_product.setMaximumHeight(80)
    d.header_product.setToolTip(d.updated.product)
    head.addWidget(d.header_product)
    d.layout().insertWidget(0, d.header)
    d.splitter = QSplitter(Qt.Orientation.Horizontal)
    left = QWidget()
    left_layout = QVBoxLayout(left)
    left_layout.setContentsMargins(0,0,0,0)
    grid = QGridLayout()
    grid.setSpacing(6)
    left_layout.addLayout(grid)
    d.kpis = {}
    for index, (key, label) in enumerate((("ratio","アベ比"),("sales","売上金額"),("average","平均売上"),("cost_rate","原価率"),("po_rate","PO率"),("days","投入日数"))):
        card = KpiCard(label)
        grid.addWidget(card, index//3, index%3)
        d.kpis[key] = card
    scroll.takeWidget()  # setWidget deletes the previous owned widget unless released.
    panel.setParent(left)
    left_layout.addWidget(panel)
    scroll.setWidget(left)
    tabs.removeTab(0)
    d.splitter.addWidget(scroll)
    right_scroll = QScrollArea()
    right_scroll.setWidgetResizable(True)
    d.evaluation_panel = QWidget()
    d.evaluation_layout = QVBoxLayout(d.evaluation_panel)
    d.evaluation_layout.setContentsMargins(10,8,10,8)
    d.evaluation_layout.addWidget(QLabel("5項目の状態分析"))
    d.evaluation_note = QLabel("各指標を個別に確認します。総合成績の平均点は作りません。")
    d.evaluation_note.setWordWrap(True)
    d.evaluation_layout.addWidget(d.evaluation_note)
    d.evaluation_layout.addStretch()
    right_scroll.setWidget(d.evaluation_panel)
    d.splitter.addWidget(right_scroll)
    d.splitter.setSizes([560,440])
    if d.width() < 960:
        d.splitter.setOrientation(Qt.Orientation.Vertical)
    tabs.insertTab(0,d.splitter,"現在投入中の商品・状態分析")
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
        f'{MATCH_LABELS[m.match_method]} ｜ 更新 {r["loaded_at"] or "不明"}'
    )
    for key, card in d.kpis.items():
        mode = {"ratio":"ratio","sales":"sales_metric","average":"sales_metric","cost_rate":"cost_rate","po_rate":"po_rate","days":"age"}[key]
        color, label = filter_status(m, mode, o.reference_day(), settings=o.analysis_settings)
        text = formatted(key,r[key])
        if r[key] is not None and key in ("sales","average"):
            text += " 円"
        elif r[key] is not None and key == "days" and r[key]>=0:
            text += " 日"
        card.set_value(text, color, label)
        card.setToolTip(r["sources"][key]["source"])


def open_analysis(d):
    from PySide6.QtWidgets import QMessageBox
    if d.name.toPlainText().strip()!=d.original.product or d.method.currentText().strip()!=d.original.operation_method:
        if QMessageBox.question(d,"未保存の編集", "未保存の編集を反映せず売上分析へ移動しますか？") != QMessageBox.StandardButton.Yes:
            return
    uid, scope, owner = d.original.uid, d.analysis_scope.currentData(), d.owner
    d.reject()
    QTimer.singleShot(0, lambda: owner.analytics_requested.emit(uid, scope))
