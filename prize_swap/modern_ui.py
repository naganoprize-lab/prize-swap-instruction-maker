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
        self._refreshing = False
        self.setWidgetResizable(True)
        page = QWidget()
        self.setWidget(page)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        title = QLabel("売り場管理ホーム")
        title.setObjectName("pageTitle")
        layout.addWidget(title)
        note = QLabel("今日の作業と売り場の状況。目的から操作を選べます。")
        note.setObjectName("muted")
        layout.addWidget(note)
        self.metrics_grid = QGridLayout()
        self.metrics_grid.setSpacing(7)
        layout.addLayout(self.metrics_grid)
        self.cards = {}
        for key, label in [
            ("total", "管理ブース数"),
            ("candidates", "入替候補数"),
            ("instructions", "指示書登録件数"),
            ("output", "出力対象件数"),
            ("unmatched", "データ未照合件数"),
            ("preflight", "印刷前の要修正"),
            ("planned", "投入予定数"),
        ]:
            card = StatCard(label, compact=True)
            card.setFixedHeight(62)
            font = card.value.font()
            font.setPointSizeF(18)
            card.value.setFont(font)
            self.cards[key] = card
        attention = QFrame()
        attention.setObjectName("card")
        self.attention_layout = QVBoxLayout(attention)
        self.attention_layout.setContentsMargins(10, 7, 10, 7)
        self.attention_title = QLabel("注意・お知らせ")
        self.attention_title.setObjectName("section")
        self.attention_layout.addWidget(self.attention_title)
        self.attention_buttons = []
        layout.addWidget(attention)
        label = QLabel("何をしたいですか？")
        label.setObjectName("section")
        layout.addWidget(label)
        self.feature_grid = QGridLayout()
        self.feature_grid.setSpacing(8)
        layout.addLayout(self.feature_grid)
        self.tiles = {}
        self.tile_descriptions = {}
        for key, name, description, icon in [
            ("update", "売上データを更新", "Excelを読み込み、実績と照合結果を更新", "file"),
            ("floor", "売り場レイアウトを見る", "マシン配置と稼働状況を確認", "floor"),
            ("candidates", "入替候補を確認", "要確認・入替検討の根拠を見る", "file"),
            ("instructions", "入替指示書を作成", "編集・印刷前チェック・作業セット", "file"),
            ("analysis", "売上分析を見る", "全台・選択した台の分布を確認", "chart"),
            ("arrivals", "入荷予定を確認", "登録済み商品の予定日と一覧", "file"),
            ("matching", "データ照合", "管理ナンバーと実績の対応を確認", "file"),
            ("labels", "入荷ラベル", "商品名ラベルの枚数・印刷", "file"),
            ("settings", "設定", "テーマ・目標原価率・分析基準", "settings"),
        ]:
            button = QToolButton()
            button.setText(name + "\n" + description)
            button.setIcon(line_icon(icon))
            button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
            button.setMinimumHeight(78)
            button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            button.clicked.connect(lambda checked=False, k=key: self.navigate(k))
            self.tiles[key] = button
            self.tile_descriptions[key] = (name, description, icon)
        recent = QFrame()
        recent.setObjectName("card")
        box = QVBoxLayout(recent)
        box.setContentsMargins(10, 7, 10, 7)
        title = QLabel("最近の作業")
        title.setObjectName("section")
        box.addWidget(title)
        self.period = QLabel()
        self.period.setTextFormat(Qt.TextFormat.PlainText)
        self.period.setObjectName("section")
        box.addWidget(self.period)
        self.updated = QLabel()
        self.updated.setTextFormat(Qt.TextFormat.PlainText)
        self.updated.setWordWrap(True)
        self.updated.setObjectName("muted")
        box.addWidget(self.updated)
        self.recent_edits = QLabel()
        self.recent_edits.setTextFormat(Qt.TextFormat.PlainText)
        self.recent_edits.setWordWrap(True)
        box.addWidget(self.recent_edits)
        history = QPushButton("編集履歴を見る")
        history.clicked.connect(self.show_history)
        box.addWidget(history)
        layout.addWidget(recent)
        self.bars = RatioBars()
        self.bars.setFixedHeight(240)
        self.bars.activated.connect(self.locate)
        self.bars.hide()
        summary = QPushButton("アベ比分布の概要を表示")
        summary.setCheckable(True)
        summary.toggled.connect(self.bars.setVisible)
        layout.addWidget(summary)
        layout.addWidget(self.bars)
        layout.addStretch()
        manager().changed.connect(self.refresh_tile_icons)
        self.reflow()

    def navigate(self, key):
        w = self.window
        if key == "update":
            if hasattr(w, "import_excel_auto"):
                w.import_excel_auto(preferred="sales")
            else:
                w.floor.import_analysis_dialog()
        else:
            w.navigation.activate(key)

    def show_history(self):
        w = self.window
        w.navigation.activate("instructions")
        w.instruction_tabs.setCurrentWidget(w.operation_history)

    def refresh_tile_icons(self, *_):
        for key, button in self.tiles.items():
            button.setIcon(line_icon(self.tile_descriptions[key][2]))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "tiles"):
            self.reflow()

    def showEvent(self, event):
        super().showEvent(event)
        self.refresh()

    def reflow(self):
        cols = 3 if self.viewport().width() >= 1100 else 2 if self.viewport().width() >= 650 else 1
        self._tile_columns = cols
        for i, b in enumerate(self.tiles.values()):
            self.feature_grid.removeWidget(b)
            self.feature_grid.addWidget(b, i // cols, i % cols)
        metric_cols = (
            4 if self.viewport().width() >= 1000 else 3 if self.viewport().width() >= 650 else 2
        )
        for i, card in enumerate(self.cards.values()):
            self.metrics_grid.removeWidget(card)
            self.metrics_grid.addWidget(card, i // metric_cols, i % metric_cols)

    def refresh(self, validate=True):
        if self._refreshing:
            return
        self._refreshing = True
        try:
            w = self.window
            floor = w.floor
            floor.dashboard.refresh()
            metrics = dict(floor.dashboard.metrics)
            issues = []
            if hasattr(w, "preflight"):
                if validate:
                    w.preflight.refresh()
                issues = w.preflight.issues
            metrics.update(
                instructions=len(w.visible),
                output=sum(p.included for p in w.visible),
                preflight=sum(i.severity == "error" for i in issues),
            )
            for key, card in self.cards.items():
                card.set_value(metrics[key])
            self.period.setText(period_text(floor.analysis_state))
            recent = w.workflow.data["recent"] if hasattr(w, "workflow") else {}
            from pathlib import Path

            def file_line(key, label):
                r = recent.get(key)
                if r:
                    return f"{label}：{Path(r['file']).name} ｜ {r['timestamp'].replace('T',' ')}"
                if key == "excel" and floor.analysis_state.get("file_name"):
                    return f"{label}：{floor.analysis_state['file_name']} ｜ {floor.analysis_state.get('loaded_at') or '読込日時不明'}"
                return label + "：記録なし"

            self.updated.setText(
                "\n".join(
                    [
                        file_line("excel", "最後のExcel"),
                        file_line("saved", "最後の保存"),
                        file_line("output", "最後の出力"),
                    ]
                )
            )
            audit = w.workflow.data["audit_log"] if hasattr(w, "workflow") else []
            edits = [r for r in reversed(audit) if r["operation_type"] in ("edit", "undo", "redo")][
                :3
            ]
            self.recent_edits.setText(
                "最近の編集："
                + " ／ ".join(r["timestamp"][11:19] + " " + r["description"] for r in edits)
                if edits
                else "最近の編集：記録なし"
            )
            for b in self.attention_buttons:
                self.attention_layout.removeWidget(b)
                b.deleteLater()
            self.attention_buttons = []

            def attention(text, callback):
                b = QPushButton(text)
                b.setStyleSheet("text-align: left;")
                b.clicked.connect(callback)
                self.attention_layout.addWidget(b)
                self.attention_buttons.append(b)

            errors = [i for i in issues if i.severity == "error"]
            warnings = [i for i in issues if i.severity == "warning"]
            for kinds, title in [
                ({"contradictory_instruction", "duplicate_machine"}, "同一マシンへの複数指示"),
                (
                    {"ambiguous_reference", "reference_mismatch", "management_unmatched"},
                    "管理ナンバーの照合",
                ),
                ({"unassigned_booths"}, "未割当ブース"),
            ]:
                found = [i for i in issues if i.issue_type in kinds]
                if found:
                    attention(
                        f"{title}：{len(found)}件",
                        lambda checked=False, i=found[0]: w.preflight.go_to_issue(i),
                    )
            if errors or warnings:
                attention(
                    f"印刷前の確認：エラー{len(errors)}件 ／ 警告{len(warnings)}件",
                    lambda: self.show_preflight(),
                )
            if floor.analysis_state.get("loaded_at") and metrics["unmatched"]:
                attention(
                    f"売上実績の未照合：{metrics['unmatched']}件",
                    lambda: w.navigation.activate("matching"),
                )
            if not self.attention_buttons:
                attention("重大な問題は検出されていません", lambda: self.show_preflight())
            records = floor.analytics.records()
            poor = sum(r["assessment"] == "要確認" for r in records)
            consider = sum(r["assessment"] == "入替検討" for r in records)
            name, description, icon = self.tile_descriptions["candidates"]
            self.tiles["candidates"].setText(f"{name}\n要確認 {poor}台 ／ 入替検討 {consider}台")
            self.bars.groups = floor.dashboard.groups
            self.bars.update()
        finally:
            self._refreshing = False

    def show_preflight(self):
        w = self.window
        w.navigation.activate("instructions")
        w.preflight.refresh()
        w.instruction_tabs.setCurrentWidget(w.preflight)

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
