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
        title = QLabel("プライズ景品 入替指示書メーカー")
        title.setObjectName("pageTitle")
        layout.insertWidget(0, title)
        note = QLabel("売り場の管理・分析・入替指示をまとめて行えます。")
        note.setObjectName("muted")
        layout.insertWidget(1, note)
        self.feature_grid = QGridLayout()
        self.feature_grid.setSpacing(9)
        layout.addLayout(self.feature_grid)
        self.tiles = {}
        for key, name, description, icon in [
            ("floor", "売り場レイアウト", "マシン配置と稼働状況を確認", "floor"),
            ("analysis", "売上分析", "全台の売上・アベ比・原価率を分析", "chart"),
            ("candidates", "入替候補", "確認が必要なマシンと判定理由", "file"),
            ("instructions", "入替指示書", "商品・予定ブース数・割当を編集", "file"),
            ("arrivals", "入荷予定", "登録済み商品の予定日と一覧を確認", "file"),
            ("matching", "データ照合", "管理ナンバーと実績の対応を確認", "file"),
            ("labels", "入荷ラベル", "商品名ラベルの枚数・印刷", "file"),
            ("settings", "設定", "テーマ・目標原価率・分析基準", "settings"),
        ]:
            button = QToolButton()
            button.setText(name + "\n" + description)
            button.setIcon(line_icon(icon))
            button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
            button.setMinimumHeight(92)
            button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            button.clicked.connect(lambda checked=False, k=key: window.navigation.activate(k))
            self.tiles[key] = button
        self.bars = RatioBars()
        self.bars.setFixedHeight(240)
        self.bars.activated.connect(self.locate)
        self.bars.hide()
        summary = QPushButton("アベ比分布の概要を表示")
        summary.setCheckable(True)
        summary.toggled.connect(self.bars.setVisible)
        layout.addWidget(summary)
        layout.addWidget(self.bars)
        import_button = QPushButton("売上分析Excel読込")
        import_button.clicked.connect(window.floor.import_analysis_dialog)
        layout.addWidget(import_button)
        layout.addStretch()
        manager().changed.connect(self.refresh_tile_icons)
        self.reflow()

    def refresh_tile_icons(self, *_):
        for key, button in self.tiles.items():
            button.setIcon(
                line_icon(
                    "floor"
                    if key == "floor"
                    else (
                        "chart"
                        if key == "analysis"
                        else "settings" if key == "settings" else "file"
                    )
                )
            )

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "tiles"):
            self.reflow()

    def reflow(self):
        cols = 3 if self.viewport().width() >= 1100 else 2 if self.viewport().width() >= 650 else 1
        if getattr(self, "_tile_columns", None) == cols:
            return
        self._tile_columns = cols
        for i, button in enumerate(self.tiles.values()):
            self.feature_grid.removeWidget(button)
            self.feature_grid.addWidget(button, i // cols, i % cols)

    def refresh(self):
        floor = self.window.floor
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
            ("dashboard", "ホーム", "dashboard"),
            ("floor", "売り場レイアウト", "floor"),
            ("instructions", "入替指示書", "file"),
            ("candidates", "入替候補", "file"),
            ("analysis", "売上分析", "chart"),
            ("matching", "データ照合", "file"),
            ("arrivals", "入荷予定", "file"),
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
                    "arrivals",
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
    window.selection_state = window.floor.selection_state
    window.instruction_page = window.tabs.widget(0)
    window.dashboard_page = DashboardPage(window)
    window.settings_page = SettingsPage(window)
    window.tabs.addTab(window.dashboard_page, "ホーム")
    window.tabs.addTab(window.settings_page, "設定")
    from .analytics_page import SalesPage

    window.analysis_page = SalesPage(window)
    window.floor.analysis_page = window.analysis_page
    window.tabs.addTab(window.analysis_page, "売上分析")
    from .candidate_page import CandidatePage

    window.candidate_page = CandidatePage(window)
    window.floor.candidate_page = window.candidate_page
    window.tabs.addTab(window.candidate_page, "入替候補")
    from .arrivals_page import ArrivalsPage

    window.arrivals_page = ArrivalsPage(window)
    window.tabs.addTab(window.arrivals_page, "入荷予定")
    window.pages = {
        "instructions": window.instruction_page,
        "floor": window.floor,
        "labels": window.labels,
        "dashboard": window.dashboard_page,
        "settings": window.settings_page,
        "analysis": window.analysis_page,
        "candidates": window.candidate_page,
        "arrivals": window.arrivals_page,
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
