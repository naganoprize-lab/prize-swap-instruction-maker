"""Compact, ID-preserving distributions over the same resolved machine records."""

from collections import Counter, defaultdict
from statistics import mean, median
from PySide6.QtCore import Qt, Signal, QRectF, QPointF, QTimer
from PySide6.QtGui import QPainter, QColor, QPen, QPainterPath, QFontMetricsF
from PySide6.QtWidgets import (
    QWidget,
    QFrame,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QComboBox,
    QScrollArea,
    QGridLayout,
    QToolTip,
)
from .theme import tokens, analysis_color, manager
from .analytics_data import bucket, formatted
from .sales_evaluation import quartiles, classify, boundary_legend
from .analysis_bands import (
    SALES_LABELS,
    SALES_COLORS,
    PO_LABELS,
    PO_COLORS,
    COST_LABELS,
    COST_COLORS,
    AGE_LABELS,
    AGE_COLORS,
    UNKNOWN,
)

TITLES = {
    "ratio_bin": "アベ比の分布",
    "sales_bin": "売上金額の分布",
    "po_bin": "PO率の分布",
    "cost_bin": "原価率の分布",
    "days_bin": "投入日数の分布",
    "machine_type": "機種別の台数分布",
    "method": "運営方法別の台数分布",
}
COLORS = {
    "50%未満": "#F4A6A6",
    "50〜100%未満": "#FFFFFF",
    "100〜200%未満": "#94D8A0",
    "200%以上": "#FFE16A",
    "低い": "#8FC5F4",
    "適正範囲": "#94D8A0",
    "高い": "#F4A6A6",
    "低すぎる・要確認": "#8FC5F4",
    "低め": "#C5E2FA",
    "目標付近": "#94D8A0",
    "0〜6日": "#94D8A0",
    "7〜13日": "#C5E2FA",
    "14〜20日": "#FFE16A",
    "21〜27日": "#F7D5A0",
    "28日以上": "#F4A6A6",
    "投入予定": "#A9D7FF",
    "不明": "#B8BEC6",
    "計算不可": "#B8BEC6",
}
COLORS.update(dict(zip(PO_LABELS, PO_COLORS)))
COLORS.update(dict(zip(COST_LABELS, COST_COLORS)))
COLORS.update(dict(zip(AGE_LABELS, AGE_COLORS)))
COLORS.update({"不明": UNKNOWN, "計算不可": UNKNOWN})
ORDERS = {
    "ratio_bin": ["50%未満", "50〜100%未満", "100〜200%未満", "200%以上", "不明"],
    "po_bin": [*PO_LABELS, "計算不可"],
    "cost_bin": [*COST_LABELS, "不明"],
    "days_bin": [*AGE_LABELS, "投入予定", "不明"],
    "sales_bin": [*SALES_LABELS, "同額", "不明"],
}


def distribution(records, axis, settings, population=None):
    population = records if population is None else population
    from .sales_evaluation import record_bounds, record_evaluation

    bounds = record_bounds(population, settings)
    rows = {}
    types = Counter(r["machine_type"] for r in records)
    top = {k for k, v in types.most_common(10)}
    for r in records:
        if axis == "sales_bin":
            result = record_evaluation(r, settings, bounds)
            color, label = result["color"], result["label"]
            label = label.split("：")[0]
        else:
            label = bucket(r, axis, settings)
            color = COLORS.get(label)
            if axis == "machine_type":
                from .analysis_settings import type_style

                color = (
                    type_style(label, settings)["color"]
                    if label not in ("", "クレーン")
                    else "#B8BEC6"
                )
                label = label if label in top else "その他"
            if color is None:
                from .analysis_settings import type_style

                color = type_style(label, settings)["color"]
        display_label = label
        if axis == "machine_type" and label != "その他":
            from .analysis_settings import type_style

            display_label = type_style(label, settings)["short"]
        row = rows.setdefault(
            label, dict(label=label, display_label=display_label, value=0, uids=[], color=color)
        )
        row["value"] += 1
        row["uids"].append(r["uid"])
    order = ORDERS.get(
        axis,
        (
            ["高売上", "標準", "低売上", "不明"]
            if axis == "sales_bin"
            else sorted(rows, key=lambda k: (-rows[k]["value"], k))
        ),
    )
    output = [rows[k] for k in order if k in rows] + [v for k, v in rows.items() if k not in order]
    unknown = sum(
        v["value"] for v in output if v["label"] in ("不明", "計算不可", "機種不明", "クレーン")
    )
    key = {
        "ratio_bin": "ratio",
        "sales_bin": "sales",
        "po_bin": "po_rate",
        "cost_bin": "cost_rate",
        "days_bin": "days",
    }.get(axis)
    values = [
        r[key] for r in records if key and r.get(key) is not None and (key != "days" or r[key] >= 0)
    ]
    info = f"対象 {len(records)}台 ｜ 不明・計算不可 {unknown}台"
    value_periods = {
        r.get("metric_periods", {}).get(key, r.get("period"))
        for r in records
        if key and r.get(key) is not None
    }
    if values and len(value_periods) <= 1:
        info += f" ｜ 平均 {formatted(key,mean(values))} / 中央 {formatted(key,median(values))}"
    elif values:
        info += " ｜ 異なる期間・取得元の平均は合算しません"
    legend = " / ".join(f'{v["label"]}：{v["value"]}台' for v in output)
    if axis == "sales_bin":
        legend += "\n" + next(
            (r["sales_legend"] for r in population if r.get("sales_legend")),
            boundary_legend(bounds, settings["sales_mode"]),
        )
        current = next(
            (
                r.get("sales_evaluation")
                for r in population
                if r.get("sales_evaluation", {}).get("count")
            ),
            None,
        )
        if current:
            legend += f"／共通基準：売上Excel全{current["count"]}台（未照合実績は基準計算のみ）"
    if axis == "po_bin":
        legend += f'\n原価・料金から算出する理論目安、許容偏差±{settings["po_tolerance"]:.0%}。仮設定は目安。'
    return dict(rows=output, info=info, unknown=unknown, legend=legend)


class DistributionChart(QWidget):
    activated = Signal(list)

    def __init__(self):
        super().__init__()
        self.rows = []
        self.hits = []
        self.kind = "horizontal"
        self.setMinimumHeight(135)
        self.setMouseTracking(True)
        manager().changed.connect(self.update)

    def event(self, event):
        if event.type() == event.Type.ToolTip:
            for rect, r in self.hits:
                if rect.contains(QPointF(event.pos())):
                    QToolTip.showText(event.globalPos(), f'{r["label"]}：{r["value"]}台', self)
                    return True
        return super().event(event)

    def mousePressEvent(self, event):
        for rect, r in self.hits:
            if rect.contains(event.position()):
                self.activated.emit(r["uids"])
                return
        super().mousePressEvent(event)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        t = tokens()
        p.fillRect(self.rect(), QColor(t["card"]))
        self.hits = []
        f = p.font()
        f.setBold(True)
        f.setPointSizeF(9)
        p.setFont(f)
        fm = QFontMetricsF(f)
        width = self.width()
        height = self.height()
        total = sum(r["value"] for r in self.rows)
        if not total:
            p.setPen(QColor(t["muted"]))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "データなし")
            return
        if self.kind in ("pie", "donut"):
            size = min(height - 8, width * 0.47)
            pie = QRectF(4, (height - size) / 2, size, size)
            angle = 90 * 16
            for i, r in enumerate(self.rows):
                span = -r["value"] / total * 360 * 16
                p.setPen(QPen(QColor(t["border"]), 1))
                p.setBrush(QColor(analysis_color(r["color"])))
                p.drawPie(pie, round(angle), round(span))
                path = QPainterPath()
                path.moveTo(pie.center())
                path.arcTo(pie, angle / 16, span / 16)
                path.closeSubpath()
                if self.kind == "donut":
                    inner = QPainterPath()
                    inner.addEllipse(
                        pie.adjusted(size * 0.26, size * 0.26, -size * 0.26, -size * 0.26)
                    )
                    path = path.subtracted(inner)
                self.hits.append((path, r))
                angle += span
            if self.kind == "donut":
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(t["card"]))
                p.drawEllipse(pie.adjusted(size * 0.26, size * 0.26, -size * 0.26, -size * 0.26))
            x = size + 12
            line = height / max(1, len(self.rows))
            for i, r in enumerate(self.rows):
                rect = QRectF(x, i * line, width - x - 4, line)
                p.setPen(QColor(t["text"]))
                p.drawText(
                    rect,
                    Qt.AlignmentFlag.AlignVCenter,
                    fm.elidedText(
                        f'{r.get("display_label",r["label"])} {r["value"]}台',
                        Qt.TextElideMode.ElideRight,
                        rect.width(),
                    ),
                )
                self.hits.append((rect, r))
            return
        step = height / max(1, len(self.rows))
        label_width = min(150, width * 0.43)
        count_width = 88
        bar_width = max(12, width - label_width - count_width - 12)
        largest = max(r["value"] for r in self.rows)
        for i, r in enumerate(self.rows):
            y = i * step
            rect = QRectF(0, y, width, step)
            p.setPen(QColor(t["text"]))
            p.drawText(
                QRectF(2, y, label_width - 6, step),
                Qt.AlignmentFlag.AlignVCenter,
                fm.elidedText(
                    r.get("display_label", r["label"]), Qt.TextElideMode.ElideRight, label_width - 6
                ),
            )
            bar = QRectF(
                label_width, y + step * 0.22, bar_width * r["value"] / largest, max(4, step * 0.56)
            )
            p.setPen(QPen(QColor(t["border"]), 1))
            p.setBrush(QColor(analysis_color(r["color"])))
            p.drawRoundedRect(bar, 3, 3)
            p.setPen(QColor(t["text"]))
            p.drawText(
                QRectF(width - count_width, y, count_width - 2, step),
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                f'{r["value"]}台 {r["value"]/total:.0%}',
            )
            self.hits.append((rect, r))


class DistributionPage(QScrollArea):
    def __init__(self, page):
        super().__init__()
        self.page = page
        self.setWidgetResizable(True)
        self.cards = {}
        self.columns = 0
        body = QWidget()
        self.setWidget(body)
        outer = QVBoxLayout(body)
        outer.setContentsMargins(4, 4, 4, 4)
        row = QHBoxLayout()
        outer.addLayout(row)
        self.meta = QLabel()
        self.meta.setWordWrap(True)
        row.addWidget(self.meta, 1)
        self.form = QComboBox()
        for label, key in [("横棒", "horizontal"), ("円", "pie"), ("ドーナツ", "donut")]:
            self.form.addItem(label, key)
        self.form.setCurrentIndex(self.form.findData(page.owner.analytics_ui["overview_chart"]))
        self.form.currentIndexChanged.connect(self.save_preferences)
        row.addWidget(self.form)
        self.method_button = QPushButton("運営方法分布も表示")
        self.method_button.setCheckable(True)
        self.method_button.setChecked(page.owner.analytics_ui["overview_methods"])
        self.method_button.toggled.connect(self.save_preferences)
        row.addWidget(self.method_button)
        self.grid = QGridLayout()
        self.grid.setSpacing(8)
        outer.addLayout(self.grid)
        outer.addStretch()
        for axis in page.owner.analytics_ui["tile_order"]:
            title = TITLES[axis]
            card = QFrame()
            card.setObjectName("card")
            layout = QVBoxLayout(card)
            layout.setContentsMargins(10, 7, 10, 7)
            layout.setSpacing(3)
            title_row = QHBoxLayout()
            layout.addLayout(title_row)
            label = QLabel(title)
            label.setObjectName("section")
            title_row.addWidget(label, 1)
            up = QPushButton("↑")
            up.setToolTip("このタイルを前へ（表示順を保存）")
            up.clicked.connect(lambda checked=False, a=axis: self.move_tile(a))
            title_row.addWidget(up)
            expand = QPushButton("拡大")
            expand.clicked.connect(lambda checked=False, a=axis: self.expand_chart(a))
            title_row.addWidget(expand)
            button = QPushButton("詳細")
            button.clicked.connect(lambda checked=False, a=axis: self.open_detail(a))
            title_row.addWidget(button)
            info = QLabel()
            info.setObjectName("muted")
            info.setWordWrap(True)
            layout.addWidget(info)
            chart = DistributionChart()
            layout.addWidget(chart, 1)
            chart.activated.connect(page.host_window.highlight_analysis_machines)
            legend = QLabel()
            legend.setObjectName("muted")
            legend.setWordWrap(True)
            legend.setMaximumHeight(35)
            layout.addWidget(legend)
            card.setMinimumHeight(245)
            card.setMaximumHeight(300 if axis != "machine_type" else 350)
            self.cards[axis] = (card, info, chart, legend)
        self.reflow()

    def move_tile(self, axis):
        order = self.page.owner.analytics_ui["tile_order"]
        i = order.index(axis)
        if i:
            order[i - 1], order[i] = order[i], order[i - 1]
        self.page.owner.schedule_save()
        self.reflow()

    def expand_chart(self, axis):
        from PySide6.QtWidgets import QDialog, QDialogButtonBox

        d = QDialog(self)
        d.setWindowTitle(TITLES[axis])
        d.resize(900, 620)
        layout = QVBoxLayout(d)
        chart = DistributionChart()
        chart.rows = self.cards[axis][2].rows
        chart.kind = self.form.currentData()
        chart.activated.connect(self.page.host_window.highlight_analysis_machines)
        layout.addWidget(chart, 1)
        note = QLabel(self.cards[axis][3].toolTip())
        note.setWordWrap(True)
        layout.addWidget(note)
        close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close.rejected.connect(d.reject)
        layout.addWidget(close)
        d.exec()

    def save_preferences(self, *_):
        p = self.page
        p.owner.analytics_ui.update(
            overview_chart=self.form.currentData(), overview_methods=self.method_button.isChecked()
        )
        p._loaded_ui = p.owner.analytics_ui.copy()
        p.owner.schedule_save()
        self.refresh()

    def open_detail(self, axis):
        p = self.page
        p.workspace_tabs.setCurrentWidget(p.detail_page)
        p.axis.setCurrentIndex(p.axis.findData(axis))
        p.metric_choice.setCurrentIndex(p.metric_choice.findData("number"))
        p.operation.setCurrentIndex(p.operation.findData("count"))
        p.chart_type.setCurrentIndex(p.chart_type.findData("horizontal"))
        p.changed()
        p.refresh()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.reflow()

    def reflow(self):
        if not hasattr(self, "cards"):
            return
        cols = 3 if self.viewport().width() >= 1350 else 2 if self.viewport().width() >= 820 else 1
        axes = [
            a
            for a in self.page.owner.analytics_ui["tile_order"]
            if a != "method" or self.method_button.isChecked()
        ]
        layout_key = (cols, tuple(axes))
        if getattr(self, "_layout_key", None) == layout_key:
            return
        self._layout_key = layout_key
        for a, (card, *_) in self.cards.items():
            self.grid.removeWidget(card)
            card.setVisible(a in axes)
        for i, a in enumerate(axes):
            self.grid.addWidget(self.cards[a][0], i // cols, i % cols)
        self.columns = cols

    def refresh(self, *_):
        if not hasattr(self, "cards"):
            return
        p = self.page
        records = p.records
        population = p.owner.analytics.records()
        stamp = (
            p.owner.analytics.builds,
            tuple(r["uid"] for r in records),
            self.form.currentData(),
            self.method_button.isChecked(),
        )
        if getattr(self, "_stamp", None) == stamp:
            self.reflow()
            return
        self._stamp = stamp
        from .performance_validation import period_text

        self.meta.setText(
            f'{period_text(p.owner.analysis_state)} ｜ 更新 {p.owner.analysis_state["loaded_at"] or "未読込"} ｜ 対象 {len(records)}台'
        )
        for axis, (card, info, chart, legend) in self.cards.items():
            d = distribution(records, axis, p.owner.analysis_settings, population)
            info.setText(d["info"])
            info.setToolTip(d["info"])
            legend.setText(d["legend"].split("\n")[0])
            legend.setToolTip(d["legend"])
            chart.rows = d["rows"]
            chart.kind = self.form.currentData()
            chart.setAccessibleDescription(d["legend"])
            chart.update()
        self.reflow()
