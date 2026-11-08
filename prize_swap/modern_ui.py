"""A compact navigation shell around the existing tabs and functional analysis views."""

from PySide6.QtCore import Qt, QRectF, Signal
from PySide6.QtGui import QPainter, QColor, QFont, QFontMetricsF
from PySide6.QtWidgets import (
    QWidget,
    QFrame,
    QVBoxLayout,
    QHBoxLayout,
    QGridLayout,
    QLabel,
    QToolButton,
    QPushButton,
    QLineEdit,
    QMenu,
    QFormLayout,
    QScrollArea,
    QSizePolicy,
)
from .theme import ThemeCombo, manager, line_icon, tokens, analysis_color, contrast_text
from .floor_dashboard import BINS
from .performance_validation import period_text


class StatCard(QFrame):
    def __init__(self, label, compact=False):
        super().__init__()
        self.setObjectName("card")
        self.setFixedHeight(50 if compact else 92)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(0)
        self.label = QLabel(label)
        self.label.setObjectName("muted")
        layout.addWidget(self.label)
        self.value = QLabel("0")
        self.value.setObjectName("statValue")
        self.value.setProperty("statCompact", compact)
        layout.addWidget(self.value)
        font = self.value.font()
        font.setPointSizeF(13 if compact else 23)
        font.setBold(True)
        self.value.setFont(font)

    def set_value(self, value):
        self.value.setText(f"{value:,}")


class RatioBars(QWidget):
    activated = Signal(str)

    def __init__(self):
        super().__init__()
        self.groups = {}
        self.hit_boxes = {}
        self.setMinimumHeight(230)
        self.setMouseTracking(True)
        manager().changed.connect(self.refresh_theme)

    def refresh_theme(self, *_):
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        t = tokens()
        p.fillRect(self.rect(), QColor(t["card"]))
        colors = ("#F4A6A6", "#FFFFFF", "#94D8A0", "#FFE16A", "#B8BEC6")
        self.hit_boxes = {}
        row_height = max(38, (self.height() - 12) / len(BINS))
        max_count = max([1, *[v["count"] for v in self.groups.values()]])
        label_width = min(145, self.width() * 0.27)
        value_width = 125
        bar_width = max(20, self.width() - label_width - value_width - 20)
        for i, (key, color) in enumerate(zip(BINS, colors)):
            d = self.groups.get(key, dict(count=0, percentage=0))
            y = 6 + i * row_height
            p.setPen(QColor(t["text"]))
            p.drawText(
                QRectF(4, y, label_width - 8, row_height), Qt.AlignmentFlag.AlignVCenter, key
            )
            track = QRectF(label_width, y + 9, bar_width, max(8, row_height - 18))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(t["sidebar"]))
            p.drawRoundedRect(track, 4, 4)
            fill = QRectF(track)
            fill.setWidth(bar_width * d["count"] / max_count)
            p.setBrush(QColor(analysis_color(color)))
            p.drawRoundedRect(fill, 4, 4)
            p.setPen(QColor(t["text"]))
            p.drawText(
                QRectF(label_width + bar_width + 12, y, value_width, row_height),
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight,
                f'{d["count"]:,}台  {d["percentage"]:.1f}%',
            )
            self.hit_boxes[key] = QRectF(0, y, self.width(), row_height)
        p.end()

    def mousePressEvent(self, event):
        for key, rect in self.hit_boxes.items():
            if rect.contains(event.position()):
                self.activated.emit(key)
                break
        super().mousePressEvent(event)


class DashboardPage(QScrollArea):
    def __init__(self, window):
        super().__init__()
        self.window = window
        self.setWidgetResizable(True)
        page = QWidget()
        self.setWidget(page)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        row = QHBoxLayout()
        row.setSpacing(8)
        layout.addLayout(row)
        self.cards = {}
        for key, label in (
            ("total", "管理ブース数"),
            ("candidates", "入替候補数"),
            ("planned", "投入予定数"),
            ("unmatched", "データ未照合件数"),
        ):
            card = StatCard(label)
            self.cards[key] = card
            row.addWidget(card, 1)
        info = QFrame()
        info.setObjectName("card")
        box = QVBoxLayout(info)
        box.setContentsMargins(12, 10, 12, 10)
        self.period = QLabel()
        self.period.setObjectName("section")
        box.addWidget(self.period)
        self.updated = QLabel()
        self.updated.setObjectName("muted")
        self.updated.setWordWrap(True)
        box.addWidget(self.updated)
        layout.addWidget(info)
        chart = QFrame()
        chart.setObjectName("card")
        c = QVBoxLayout(chart)
        title = QLabel("アベ比分布")
        title.setObjectName("section")
        c.addWidget(title)
        hint = QLabel(
            "割合は不明を含む全管理ブースが分母です。区分をクリックすると配置を保って対象を強調します。"
        )
        hint.setObjectName("muted")
        hint.setWordWrap(True)
        c.addWidget(hint)
        self.bars = RatioBars()
        self.bars.activated.connect(self.locate)
        c.addWidget(self.bars, 1)
        layout.addWidget(chart, 1)
        row = QHBoxLayout()
        layout.addLayout(row)
        for text, callback in [
            ("売上分析Excel読込", window.floor.import_analysis_dialog),
            ("管理ナンバー照合", window.floor.show_matching),
            ("入替候補を確認", window.floor.show_candidates),
            ("売り場レイアウトを開く", lambda: window.tabs.setCurrentWidget(window.floor)),
        ]:
            b = QPushButton(text)
            b.clicked.connect(callback)
            row.addWidget(b)

    def refresh(self):
        floor = self.window.floor
        if not hasattr(floor.dashboard, "metrics"):
            floor.dashboard.refresh()
        for key, card in self.cards.items():
            card.set_value(floor.dashboard.metrics[key])
        self.period.setText(period_text(floor.analysis_state))
        self.updated.setText(
            f'抽出日時：{floor.analysis_state.get("extracted_at") or "不明"}　読込日時：{floor.analysis_state["loaded_at"] or "未読込"}\nファイル：{floor.analysis_state["file_name"] or "未読込"}'
        )
        self.bars.groups = floor.dashboard.groups
        self.bars.update()

    def locate(self, key):
        self.window.floor.highlight_group(self.window.floor.dashboard.groups[key]["uids"])
        self.window.tabs.setCurrentWidget(self.window.floor)


class SettingsPage(QScrollArea):
    def __init__(self, window):
        super().__init__()
        self.setWidgetResizable(True)
        body = QWidget()
        self.setWidget(body)
        outer = QVBoxLayout(body)
        outer.setContentsMargins(0, 0, 0, 0)
        for title, actions in [
            ("表示とテーマ", None),
            (
                "データと照合",
                [
                    ("管理ナンバー照合・手動確認", window.floor.show_matching),
                    ("実績読込結果・異常一覧", window.floor.show_analysis_report),
                    ("分析・判定・機種色・許容差", window.floor.analysis_settings_dialog),
                    ("売上履歴・削除と復元", window.floor.show_history),
                ],
            ),
            (
                "売り場表示と印刷",
                [
                    ("カード・文字サイズ", window.floor.card_settings),
                    ("表示カードのプリセット", window.floor.presentation_settings),
                    ("用紙・印刷設定", window.floor.print_settings),
                ],
            ),
        ]:
            frame = QFrame()
            frame.setObjectName("card")
            form = QFormLayout(frame)
            label = QLabel(title)
            label.setObjectName("section")
            form.addRow(label)
            if actions is None:
                self.theme = ThemeCombo()
                form.addRow("アプリのテーマ", self.theme)
                note = QLabel(
                    "システム設定に連動、または手動で指定します。\n印刷・PDFの背景とレイアウトは変更しません。"
                )
                note.setWordWrap(True)
                note.setObjectName("muted")
                form.addRow(note)
            else:
                for name, callback in actions:
                    b = QPushButton(name)
                    b.clicked.connect(callback)
                    form.addRow(b)
            outer.addWidget(frame)
        outer.addStretch()


class Navigation(QWidget):
    def __init__(self, window):
        super().__init__()
        self.setObjectName("sidebar")
        self.window = window
        self.buttons = {}
        self.compact = False
        self.manual = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 8, 6, 6)
        layout.setSpacing(5)
        for key, label, icon in [
            ("dashboard", "ダッシュボード", "dashboard"),
            ("floor", "売り場レイアウト", "floor"),
            ("instructions", "入替指示書", "file"),
            ("candidates", "入替候補", "file"),
            ("analysis", "売上分析", "chart"),
            ("matching", "データ照合", "file"),
            ("labels", "入荷ラベル", "file"),
            ("settings", "設定", "settings"),
        ]:
            b = QToolButton()
            b.setText(label)
            b.setToolTip("入荷ブース用ラベル印刷" if key == "labels" else label)
            b.setProperty("nav", True)
            b.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
            b.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            b.setCheckable(
                key
                in (
                    "dashboard",
                    "floor",
                    "instructions",
                    "labels",
                    "settings",
                    "analysis",
                    "candidates",
                )
            )
            b.clicked.connect(lambda checked=False, k=key: self.activate(k))
            b.setProperty("iconName", icon)
            layout.addWidget(b)
            self.buttons[key] = b
        layout.addStretch()
        self.collapse = QToolButton()
        self.collapse.setText("ナビを縮小")
        self.collapse.setToolTip("ナビゲーションの幅を切り替え")
        self.collapse.clicked.connect(self.toggle)
        layout.addWidget(self.collapse)
        manager().changed.connect(self.refresh_icons)
        self.refresh_icons()
        self.set_compact(False)

    def refresh_icons(self, *_):
        for b in self.buttons.values():
            b.setIcon(line_icon(b.property("iconName")))
        self.collapse.setIcon(line_icon("monitor"))

    def toggle(self):
        self.manual = True
        self.set_compact(not self.compact)

    def set_compact(self, value):
        self.compact = value
        self.setFixedWidth(54 if value else 180)
        for b in self.buttons.values():
            b.setToolButtonStyle(
                Qt.ToolButtonStyle.ToolButtonIconOnly
                if value
                else Qt.ToolButtonStyle.ToolButtonTextBesideIcon
            )
        self.collapse.setText("" if value else "ナビを縮小")
        self.collapse.setToolButtonStyle(
            Qt.ToolButtonStyle.ToolButtonIconOnly
            if value
            else Qt.ToolButtonStyle.ToolButtonTextBesideIcon
        )

    def activate(self, key):
        w = self.window
        if key == "analysis":
            w.floor.show_method_analysis()
        elif key in w.pages:
            w.tabs.setCurrentWidget(w.pages[key])
            self.select(key)
        elif key == "candidates":
            w.floor.show_candidates()
        elif key == "analysis":
            w.floor.show_method_analysis()
        elif key == "matching":
            w.floor.show_matching()

    def select(self, key):
        for name, b in self.buttons.items():
            if b.isCheckable():
                b.setChecked(name == key)


def build_shell(window, outer, header, footer):
    window.instruction_page = window.tabs.widget(0)
    window.dashboard_page = DashboardPage(window)
    window.settings_page = SettingsPage(window)
    window.tabs.addTab(window.dashboard_page, "ダッシュボード")
    window.tabs.addTab(window.settings_page, "設定")
    from .analytics_page import SalesPage

    window.analysis_page = SalesPage(window)
    window.floor.analysis_page = window.analysis_page
    window.tabs.addTab(window.analysis_page, "売上分析")
    from .candidate_page import CandidatePage

    window.candidate_page = CandidatePage(window)
    window.floor.candidate_page = window.candidate_page
    window.tabs.addTab(window.candidate_page, "入替候補")
    window.pages = {
        "instructions": window.instruction_page,
        "floor": window.floor,
        "labels": window.labels,
        "dashboard": window.dashboard_page,
        "settings": window.settings_page,
        "analysis": window.analysis_page,
        "candidates": window.candidate_page,
    }
    window.tabs.tabBar().hide()
    body = QWidget()
    row = QHBoxLayout(body)
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(10)
    window.navigation = Navigation(window)
    row.addWidget(window.navigation)
    content = QWidget()
    layout = QVBoxLayout(content)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(6)
    row.addWidget(content, 1)
    bar = QWidget()
    line = QHBoxLayout(bar)
    line.setContentsMargins(2, 0, 2, 0)
    line.setSpacing(8)
    window.page_title = QLabel("入替指示書")
    window.page_title.setObjectName("pageTitle")
    line.addWidget(window.page_title)
    window.page_search = QLineEdit()
    window.page_search.setPlaceholderText("現在のページを検索")
    window.page_search.setClearButtonEnabled(True)
    window.page_search.setMinimumWidth(100)
    line.addWidget(window.page_search, 1)
    window.page_metadata = QLabel()
    window.page_metadata.setObjectName("muted")
    window.page_metadata.setWordWrap(True)
    window.page_metadata.setMaximumHeight(42)
    line.addWidget(window.page_metadata)
    layout.addWidget(bar)
    outer.removeWidget(window.tabs)
    layout.addWidget(window.tabs, 1)
    outer.removeWidget(footer)
    layout.addWidget(footer)
    outer.addWidget(body, 1)
    outer.setContentsMargins(8, 6, 8, 6)
    outer.setSpacing(7)
    window.tabs.currentChanged.connect(window.page_changed)
    window.page_search.textChanged.connect(window.search_page)
    window.floor.floor_search.textChanged.connect(window.sync_page_search)
    window.search.textChanged.connect(window.sync_page_search)
    window.analysis_page.search.textChanged.connect(window.sync_page_search)
    window.candidate_page.search.textChanged.connect(window.sync_page_search)
    window.floor.analytics_requested.connect(window.show_sales_analysis)
    window.floor.changed.connect(window.refresh_page_metadata)
    window.page_changed()
