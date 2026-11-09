from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import (
    QDate,
    QEvent,
    QItemSelectionModel,
    QSortFilterProxyModel,
    Qt,
    QStandardPaths,
)
from PySide6.QtGui import QColor, QTextCharFormat
from PySide6.QtPrintSupport import (
    QAbstractPrintDialog,
    QPrintDialog,
    QPrintPreviewDialog,
    QPrintPreviewWidget,
    QPrinter,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QDateEdit,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QTableView,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .core import AppError, METHODS, for_day, load_session, read_products, save_session
from .exporter import export_excel
from .printing import configured_printer, plan_pages, render_document, save_pdf
from .table import COLUMNS, ProductDelegate, ProductModel
from .methods import (
    allocation_status,
    format_allocations,
    aggregate_booths,
    total_booths,
    booth_value,
    BoothTotal,
)
from .booth_style import METRIC_COLORS
from .floor import FloorWidget
from .floor_data import validate_state
from .machine_matching import machine_key
from .labels_data import LabelStore
from .labels_ui import LabelsWidget


def button(text, callback, primary=False):
    b = QPushButton(text)
    if primary:
        b.setObjectName("primary")
    b.clicked.connect(callback)
    return b


def card(parent_layout, stretch=0):
    frame = QFrame()
    frame.setObjectName("card")
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(12, 8, 12, 8)
    layout.setSpacing(4)
    parent_layout.addWidget(frame, stretch)
    return frame, layout


class ProductFilter(QSortFilterProxyModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.query = ""
        self.mode = 0

    def filterAcceptsRow(self, row, parent):
        p = self.sourceModel().products[row]
        text = " ".join([p.name, p.maker, *p.machines, format_allocations(p)]).casefold()
        return (
            (not self.query or self.query in text)
            and (self.mode != 1 or p.included)
            and (self.mode != 2 or p.priority)
        )

    def lessThan(self, left, right):
        column = left.column()
        if column in (1, 4, 5):

            def numeric_key(index):
                value = index.data(Qt.ItemDataRole.EditRole)
                return (value in (None, ""), float(value) if value not in (None, "") else 0)

            return numeric_key(left) < numeric_key(right)
        if column in (0, 8):
            attr = "included" if column == 0 else "priority"
            return getattr(self.sourceModel().products[left.row()], attr) < getattr(
                self.sourceModel().products[right.row()], attr
            )
        return str(left.data() or "").casefold() < str(right.data() or "").casefold()


from .instruction_editor import InstructionEditorMixin


from .work_set_ui import WorkSetMixin
from .excel_import_ui import ExcelImportMixin


class Window(ExcelImportMixin, WorkSetMixin, InstructionEditorMixin, QMainWindow):
    def __init__(self, layout_storage=None):
        super().__init__()
        self.setWindowTitle("プライズ景品 入替指示書メーカー")
        self.setMinimumSize(1100, 700)
        self.resize(1500, 900)
        from .theme import manager

        self.themes = manager()
        self.appearance_path = (
            (Path(layout_storage).parent / "appearance.json")
            if layout_storage
            else Path(
                QStandardPaths.writableLocation(
                    QStandardPaths.StandardLocation.AppLocalDataLocation
                )
            )
            / "PrizeSwapInstructionMaker"
            / "appearance.json"
        )
        self.themes.load(self.appearance_path)
        self.products, self.visible = [], []
        self.source = ""
        self.dirty = False
        root = QWidget()
        root.setObjectName("workspace")
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(16, 12, 16, 8)
        outer.setSpacing(10)
        header, head = card(outer)
        header.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        title_row = QHBoxLayout()
        title = QLabel("入替指示書メーカー")
        title.setObjectName("title")
        title_row.addWidget(title)
        title_row.addStretch()
        self.print_buttons = []
        for label, callback, primary in (
            ("印刷プレビュー", self.preview, False),
            ("直接印刷", self.print_direct, True),
            ("PDF保存", self.export_pdf, False),
        ):
            b = button(label, callback, primary)
            title_row.addWidget(b)
            self.print_buttons.append(b)
        from .theme import ThemeMenu

        self.theme_button = QToolButton()
        self.theme_button.setFixedWidth(150)
        self.theme_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.theme_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.theme_button.setMenu(ThemeMenu(self.theme_button))
        title_row.addWidget(self.theme_button)
        self.settings_button = QToolButton()
        self.settings_button.setText("設定")
        self.settings_button.clicked.connect(self.show_settings)
        title_row.addWidget(self.settings_button)
        self.themes.changed.connect(self.theme_changed)
        head.addLayout(title_row)
        top = QHBoxLayout()
        top.addWidget(button("Excelを開く", self.open_excel, True))
        self.file_label = QLabel("ファイル未選択")
        self.file_label.setObjectName("muted")
        self.file_label.setMinimumWidth(110)
        self.file_label.setMaximumWidth(260)
        top.addWidget(self.file_label, 1)
        top.addStretch()
        top.addWidget(QLabel("稼働予定日"))
        self.day = QDateEdit(QDate.currentDate())
        self.day.setCalendarPopup(True)
        self.day.setDisplayFormat("yyyy/MM/dd")
        self.day.dateChanged.connect(self.refresh)
        top.addWidget(self.day)
        self.dates = QComboBox()
        self.dates.setPlaceholderText("登録日一覧")
        self.dates.setMinimumWidth(140)
        self.dates.activated.connect(self.pick_date)
        top.addWidget(self.dates)
        self.count = QLabel("0件")
        self.count.setObjectName("count")
        top.addWidget(self.count)
        head.addLayout(top)
        self.notice = QLabel("Excelを開いて開始してください。セルをクリックして編集できます。")
        self.notice.setObjectName("muted")
        self.notice.setWordWrap(True)
        head.addWidget(self.notice)

        self.tabs = QTabWidget()
        outer.addWidget(self.tabs, 1)
        list_page = QWidget()
        list_layout = QVBoxLayout(list_page)
        list_layout.setContentsMargins(0, 0, 0, 0)
        self.tabs.addTab(list_page, "入替指示書")
        self.floor = FloorWidget(lambda: self.products, self.selected_day, layout_storage, self)
        self.floor.product_edited.connect(self.refresh)
        self.floor.product_edited.connect(self.mark_dirty)
        self.floor.message.connect(lambda message: self.statusBar().showMessage(message, 15000))
        self.tabs.addTab(self.floor, "売り場レイアウト")
        self.labels = LabelsWidget(
            lambda: self.products, self.selected_day, lambda: [self.source, self.floor.source], self
        )
        self.labels.day_requested.connect(self.day.setDate)
        self.labels.edited.connect(self.mark_dirty)
        self.labels.message.connect(lambda text: self.statusBar().showMessage(text, 10000))
        self.labels.reorder_requested.connect(self.reorder_for_labels)
        self.tabs.addTab(self.labels, "入荷ブース用ラベル印刷")
        self.list_card, center = card(list_layout, 1)
        controls = QHBoxLayout()
        section = QLabel("商品一覧")
        section.setObjectName("section")
        controls.addWidget(section)
        self.search = QLineEdit()
        self.search.setPlaceholderText("商品名・メーカー・マシン番号を検索")
        self.search.setClearButtonEnabled(True)
        controls.addWidget(self.search, 1)
        self.filter_choice = QComboBox()
        self.filter_choice.addItems(["すべて表示", "出力対象のみ", "優先入替のみ"])
        controls.addWidget(self.filter_choice)
        select = button("全選択", lambda: self.set_all(True))
        select.setToolTip("検索・絞り込みで表示中の商品の出力チェックを全てON")
        controls.addWidget(select)
        clear = button("全解除", lambda: self.set_all(False))
        clear.setToolTip("検索・絞り込みで表示中の商品の出力チェックを全てOFF")
        controls.addWidget(clear)
        controls.addWidget(button("↑ 上へ", lambda: self.move_products(-1)))
        controls.addWidget(button("↓ 下へ", lambda: self.move_products(1)))
        center.addLayout(controls)
        self.model = ProductModel(self)
        self.model.edited.connect(self.mark_dirty)
        self.model.edited.connect(self.update_count)
        self.model.edited.connect(self.floor.sync)
        self.model.edited.connect(self.labels.sync)
        self.model.edited.connect(self.update_allocation_notice)
        self.model.allocation_warning.connect(
            lambda text: self.statusBar().showMessage(text, 15000)
        )
        self.model.invalid.connect(lambda message: self.statusBar().showMessage(message, 10000))
        self.proxy = ProductFilter(self)
        self.proxy.setSourceModel(self.model)
        self.proxy.sort(1, Qt.SortOrder.AscendingOrder)
        self.search.textChanged.connect(self.filter_changed)
        self.filter_choice.currentIndexChanged.connect(self.filter_changed)
        self.table = QTableView()
        self.table.setModel(self.proxy)
        self.table.setSortingEnabled(True)
        self.table.sortByColumn(1, Qt.SortOrder.AscendingOrder)
        self.table.selectionModel().selectionChanged.connect(self.update_allocation_notice)
        self.delegate = ProductDelegate(self.table)
        self.delegate.machine_options = lambda: sorted(
            machine_key(m) for m in self.floor.machines if machine_key(m)
        )
        self.table.setItemDelegate(self.delegate)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(
            QAbstractItemView.EditTrigger.CurrentChanged
            | QAbstractItemView.EditTrigger.SelectedClicked
            | QAbstractItemView.EditTrigger.DoubleClicked
            | QAbstractItemView.EditTrigger.EditKeyPressed
        )
        self.table.setMouseTracking(True)
        self.table.setAlternatingRowColors(True)
        self.table.setWordWrap(False)
        self.table.verticalHeader().setDefaultSectionSize(34)
        self.table.verticalHeader().setMinimumSectionSize(30)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.table.horizontalHeader().setStretchLastSection(False)
        for i, width in enumerate([48, 60, 115, 340, 68, 68, 165, 240, 52, 160]):
            self.table.setColumnWidth(i, width)
        self.table.viewport().installEventFilter(self)
        self.setup_instruction_editing(controls)
        center.addWidget(self.table, 1)
        hint = QLabel(
            "セルで直接編集 / マシン番号は複数可 / 印刷は検索に関係なく選択日の出力対象すべて"
        )
        self.allocation_notice = hint
        self.default_table_hint = hint.text()
        hint.setObjectName("muted")
        hint.setWordWrap(True)
        hint_row = QHBoxLayout()
        hint_row.addWidget(hint, 1)
        self.summary_toggle = QToolButton()
        self.summary_toggle.setText("ブース予定集計 ▾")
        self.summary_toggle.setCheckable(True)
        self.summary_toggle.setMaximumHeight(22)
        hint_row.addWidget(self.summary_toggle)
        center.addLayout(hint_row)
        self.booth_summary = QWidget()
        summary_layout = QVBoxLayout(self.booth_summary)
        summary_layout.setContentsMargins(0, 0, 0, 0)
        self.summary_scope = QComboBox()
        self.summary_scope.addItems(["選択日の出力対象全商品", "選択商品"])
        self.summary_scope.currentIndexChanged.connect(self.update_booth_summary)
        summary_layout.addWidget(self.summary_scope)
        self.summary_table = QTableWidget(0, 5)
        self.summary_table.setHorizontalHeaderLabels(
            ["運営方法", "予定数", "割当済み", "未割当", "超過"]
        )
        self.summary_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.summary_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.summary_table.setMaximumHeight(170)
        summary_layout.addWidget(self.summary_table)
        center.addWidget(self.booth_summary)
        self.booth_summary.hide()
        self.summary_toggle.toggled.connect(self.booth_summary.setVisible)
        self.summary_toggle.toggled.connect(self.update_booth_summary)

        footer, bottom_layout = card(outer)
        footer.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        bottom = QHBoxLayout()
        bottom.addWidget(button("一時保存", self.save_json))
        bottom.addWidget(button("一時保存を開く", self.open_json))
        bottom.addWidget(button("Excel出力", self.export))
        bottom.addStretch()
        self.instruction_settings = QWidget()
        instruction_layout = QHBoxLayout(self.instruction_settings)
        instruction_layout.setContentsMargins(0, 0, 0, 0)
        instruction_layout.addWidget(QLabel("ページ配置"))
        self.layout_choice = QComboBox()
        self.layout_choice.addItems(["左を上から → 右を上から", "左右交互"])
        self.layout_choice.currentIndexChanged.connect(self.mark_dirty)
        instruction_layout.addWidget(self.layout_choice)
        self.print_mode = QComboBox()
        for label, key in (("自動最適化", "auto"), ("標準", "standard"), ("コンパクト", "compact")):
            self.print_mode.addItem(label, key)
        self.print_mode.currentIndexChanged.connect(self.mark_dirty)
        instruction_layout.addWidget(self.print_mode)
        instruction_layout.addWidget(QLabel("片側上限"))
        self.page_rows = QSpinBox()
        self.page_rows.setRange(1, 50)
        self.page_rows.setValue(20)
        self.page_rows.setSuffix(" 商品")
        self.page_rows.setToolTip(
            "PDF・直接印刷：片側の最大商品数。Excel出力：従来のマシン行数の目安。"
        )
        self.page_rows.valueChanged.connect(self.mark_dirty)
        instruction_layout.addWidget(self.page_rows)
        bottom.addWidget(self.instruction_settings)
        self.tabs.currentChanged.connect(
            lambda *_: self.instruction_settings.setVisible(self.tabs.currentIndex() == 0)
        )
        bottom_layout.addLayout(bottom)
        self.statusBar().showMessage("準備完了 — Excelを読み込んでください。")
        from .modern_ui import build_shell

        build_shell(self, outer, header, footer)
        from .temporal_ui import install as install_temporal

        install_temporal(self)
        self.setup_instruction_map(list_layout)
        from .preflight_ui import PreflightPanel

        self.preflight = PreflightPanel(self)
        self.instruction_tabs.addTab(self.preflight, "印刷前チェック")
        from .workflow_state import WorkflowStore
        from .operation_history_ui import OperationHistoryPanel
        from .app_shortcuts import AppShortcuts

        self.workflow = WorkflowStore(self)
        self.operation_history = OperationHistoryPanel(self)
        self.instruction_tabs.addTab(self.operation_history, "編集履歴")
        self.shortcuts = AppShortcuts(self)
        self.preflight.validated.connect(
            lambda: (
                self.dashboard_page.refresh(False)
                if self.tabs.currentWidget() is self.dashboard_page
                else None
            )
        )
        self.workflow.changed.connect(
            lambda: (
                self.dashboard_page.refresh(False)
                if self.tabs.currentWidget() is self.dashboard_page
                else None
            )
        )
        self.workflow.changed.connect(
            lambda: (
                self.instruction_save_timer.start(800) if not self._loading_instructions else None
            )
        )
        self.themes.load(self.appearance_path)
        self.theme_changed()
        self.restore_instruction_autosave()
        from .mobile_ui import install as install_mobile

        install_mobile(self)

    def show_settings(self):
        if hasattr(self, "settings_page"):
            self.tabs.setCurrentWidget(self.settings_page)
        else:
            from .theme import appearance_dialog

            appearance_dialog(self)

    def show_sales_analysis(self, uid="", scope="all"):
        if self.floor._fullscreen_host:
            self.floor._fullscreen_host.close()
        self.tabs.setCurrentWidget(self.analysis_page)
        if uid:
            self.analysis_page.focus_machine(uid, scope)

    def highlight_analysis_machines(self, uids, select=False):
        valid = set(uids) & set(self.floor.nodes)
        if not valid:
            self.analysis_page.notice.setText(
                "対象マシンを特定できないため、売り場への連携を行いません。"
            )
            return
        if self.floor._fullscreen_host:
            self.floor._fullscreen_host.close()
        self.tabs.setCurrentWidget(self.floor)
        self.floor.set_analysis_highlight(valid)
        if select and len(valid) == 1:
            self.floor.nodes[next(iter(valid))].setSelected(True)

    def theme_changed(self, *_):
        from .theme import MODES, line_icon

        self.theme_button.setText("テーマ：" + MODES[self.themes.mode])
        self.theme_button.setIcon(
            line_icon({"system": "monitor", "light": "sun", "dark": "moon"}[self.themes.mode])
        )
        if hasattr(self, "model"):
            from PySide6.QtCore import QModelIndex

            if self.model.rowCount():
                self.model.dataChanged.emit(
                    self.model.index(0, 0),
                    self.model.index(self.model.rowCount() - 1, self.model.columnCount() - 1),
                    [],
                )
        if hasattr(self, "summary_table"):
            self.update_booth_summary()
        if hasattr(self, "labels"):
            (
                self.labels.table.viewport().update()
                if hasattr(self.labels, "table")
                else self.labels.update()
            )

    def page_changed(self, *_):
        if not hasattr(self, "pages"):
            return
        key = next(
            (name for name, page in self.pages.items() if page is self.tabs.currentWidget()), None
        )
        if key is None:
            return
        self.navigation.select(key)
        self.notice.setVisible(key == "instructions")
        if key == "floor":
            self.floor.dashboard.dates.hide()
        self.page_title.setText(
            {
                "instructions": "入替指示書",
                "floor": "売り場レイアウト",
                "labels": "入荷ラベル",
                "dashboard": "ホーム",
                "arrivals": "入荷予定",
                "settings": "設定",
                "analysis": "売上分析",
                "candidates": "入替候補",
                "mobile": "Android連携",
            }[key]
        )
        self.page_search.setVisible(
            key in ("instructions", "floor", "dashboard", "analysis", "candidates")
        )
        self.sync_page_search()
        for b in self.print_buttons:
            b.setEnabled(key in ("instructions", "floor", "labels"))
        self.refresh_page_metadata()
        if key == "dashboard":
            self.dashboard_page.refresh()

    def search_page(self, text):
        if self.tabs.currentWidget() is self.instruction_page:
            self.search.setText(text)
        elif self.tabs.currentWidget() is self.floor:
            self.floor.floor_search.setText(text)
        elif self.tabs.currentWidget() is self.dashboard_page:
            self.tabs.setCurrentWidget(self.floor)
            self.floor.floor_search.setText(text)
        elif self.tabs.currentWidget() is self.analysis_page:
            self.analysis_page.search.setText(text)
        elif self.tabs.currentWidget() is self.candidate_page:
            self.candidate_page.search.setText(text)

    def sync_page_search(self, *_):
        if not hasattr(self, "page_search"):
            return
        widget = self.tabs.currentWidget()
        text = (
            self.search.text()
            if widget is self.instruction_page
            else (
                self.floor.floor_search.text()
                if widget is self.floor
                else (
                    self.analysis_page.search.text()
                    if widget is self.analysis_page
                    else self.candidate_page.search.text() if widget is self.candidate_page else ""
                )
            )
        )
        self.page_search.blockSignals(True)
        self.page_search.setText(text)
        self.page_search.blockSignals(False)

    def refresh_page_metadata(self, *_):
        if not hasattr(self, "page_metadata"):
            return
        from .performance_validation import period_text

        data = self.floor.analysis_state
        self.page_metadata.setText(
            period_text(data) + "\n更新：" + (data["loaded_at"].replace("T", " ")[:16] or "未読込")
        )
        self.page_metadata.setToolTip(self.floor.analysis_summary_text())
        if self.tabs.currentWidget() is self.dashboard_page:
            self.dashboard_page.refresh()

    def showEvent(self, event):
        super().showEvent(event)
        # On high-DPI Windows desktops the available logical height may be
        # smaller than 700px. Keep the action/footer area inside that work area.
        if QApplication.platformName() == "windows":
            available = self.screen().availableGeometry()
            width = max(900, available.width() - 20)
            height = max(560, available.height() - 50)
            self.setMinimumSize(min(1100, width), min(700, height))
            self.resize(min(self.width(), width), min(self.height(), height))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "work_set_button"):
            self.work_set_button.setText(
                "作業セット" if self.width() < 1300 else "作業指示セット印刷"
            )
            self.work_set_button.setToolTip(
                "作業指示セット印刷：指示書・入替予定地図・チェックリスト"
            )
        if hasattr(self, "navigation") and not self.navigation.manual:
            self.navigation.set_compact(self.width() < 1280)

    def eventFilter(self, obj, event):
        if hasattr(self, "instruction_history") and self.instruction_key_event(obj, event):
            return True
        if obj is self.table.viewport() and event.type() == QEvent.Type.MouseButtonRelease:
            index = self.table.indexAt(event.position().toPoint())
            if index.isValid() and index.column() in (7, 9):
                self.table.setCurrentIndex(index)
                if self.table.state() != QAbstractItemView.State.EditingState:
                    self.table.edit(index)
        return super().eventFilter(obj, event)

    def filter_changed(self, *_):
        self.proxy.query = self.search.text().strip().casefold()
        self.proxy.mode = self.filter_choice.currentIndex()
        self.proxy.invalidateFilter()
        self.update_count()

    def refresh(self, *_):
        self.visible = for_day(self.products, self.selected_day())
        self.model.replace(self.visible)
        self.floor.sync()
        self.labels.sync()
        self.update_count()
        self.update_allocation_notice()
        if hasattr(self, "instruction_map"):
            self.instruction_map.refresh()
        if hasattr(self, "preflight"):
            self.preflight.invalidate()

    def update_count(self):
        self.count.setText(
            f"{len(self.visible)}件 ／ 出力 {sum(p.included for p in self.visible)}件"
        )
        if hasattr(self, "proxy"):
            self.statusBar().showMessage(
                f"表示 {self.proxy.rowCount()}件 / 選択日 {len(self.visible)}件　印刷は出力チェックのある商品が対象です。"
            )

    def update_allocation_notice(self, *_):
        if not hasattr(self, "allocation_notice"):
            return
        selected = self.selected_products()
        products = selected or self.visible
        states = [allocation_status(p) for p in products]
        excess = sum(bool(s.excess) for s in states)
        pending = sum(bool(s.unknown or s.unassigned or s.unmapped) for s in states)
        if len(selected) == 1:
            text = f"運営方法：{states[0].message}"
        elif excess or pending:
            text = f"運営方法（{'選択中' if selected else '表示中'}）：台数超過 {excess}件 / 未設定・未割当 {pending}件。セルのツールチップまたはドロップダウンで確認できます。"
        else:
            text = self.default_table_hint
        self.allocation_notice.setText(text)
        from .theme import tone

        tone(self.allocation_notice, "danger" if excess else "muted")
        self.update_booth_summary()

    def update_booth_summary(self, *_):
        if not hasattr(self, "summary_table"):
            return
        products = self.selected_products() if self.summary_scope.currentIndex() else self.visible
        active = aggregate_booths(products)
        mapping = {r.method: r for r in active}
        totals = [
            mapping.get(name, BoothTotal(name, 0, 0, 0, 0))
            for name in dict.fromkeys([*METHODS, *mapping])
        ]
        totals.append(total_booths(active))
        self.summary_table.setRowCount(len(totals))
        for row, total in enumerate(totals):
            for col, field in enumerate(["method", "planned", "assigned", "unassigned", "excess"]):
                value = total.method if col == 0 else booth_value(total, field)
                item = QTableWidgetItem(value)
                if field in METRIC_COLORS:
                    from .theme import metric_colors

                    background, foreground = metric_colors(field)
                    item.setBackground(QColor(background))
                    item.setForeground(QColor(foreground))
                if total.method == "合計":
                    font = item.font()
                    font.setBold(True)
                    item.setFont(font)
                if total.unknown:
                    item.setToolTip(
                        f"予定未設定の方法が{total.unknown}件あります。既知の予定数の差分だけを集計します。"
                    )
                self.summary_table.setItem(row, col, item)

    def confirm_method_allocations(self):
        problems = [
            f"{p.name}：{allocation_status(p).message}"
            for p in self.visible
            if p.included and allocation_status(p).excess
        ]
        if not problems:
            return True
        return (
            QMessageBox.question(
                self,
                "運営方法の台数を確認",
                "予定ブース数を超える実際の割当があります。\n"
                + "\n".join(problems)
                + "\n設定を保持したまま出力を続けますか？",
            )
            == QMessageBox.StandardButton.Yes
        )

    def selected_products(self):
        return [
            self.model.products[self.proxy.mapToSource(index).row()]
            for index in self.table.selectionModel().selectedRows()
        ]

    def set_all(self, included):
        # Snapshot indexes first: changing inclusion may immediately filter rows.
        products = [
            self.model.products[self.proxy.mapToSource(self.proxy.index(r, 0)).row()]
            for r in range(self.proxy.rowCount())
        ]
        from .instruction_editor import all_state, InstructionCommand

        before = all_state(self)
        for p in products:
            p.included = included
            from .instructions import mark_edited

            mark_edited(p, ["included"])
        if products:
            self.instruction_history.push(
                InstructionCommand(self, before, all_state(self), "出力対象を変更", True)
            )
            self.mark_dirty()
            self.refresh()

    def move_products(self, direction):
        if self.search.text() or self.filter_choice.currentIndex():
            self.statusBar().showMessage(
                "並び順変更は検索・絞り込みを解除して行ってください。", 5000
            )
            return
        self.reorder_for_labels(self.selected_products(), direction)

    def reorder_for_labels(self, selected_products, direction):
        selected = {id(p) for p in selected_products}
        if not selected:
            return
        products = sorted(self.visible, key=lambda p: p.order)
        indexes = range(1, len(products)) if direction < 0 else range(len(products) - 2, -1, -1)
        for i in indexes:
            neighbor = i + direction
            if id(products[i]) in selected and id(products[neighbor]) not in selected:
                products[i], products[neighbor] = products[neighbor], products[i]
        from .instruction_editor import all_state, InstructionCommand

        before = all_state(self)
        for order, p in enumerate(products, 1):
            p.order = order
            from .instructions import mark_edited

            mark_edited(p, ["order"])
        self.instruction_history.push(
            InstructionCommand(self, before, all_state(self), "指示の順序変更", True)
        )
        self.mark_dirty()
        self.refresh()
        for row, p in enumerate(self.model.products):
            if id(p) in selected:
                index = self.proxy.mapFromSource(self.model.index(row, 0))
                self.table.selectionModel().select(
                    index,
                    QItemSelectionModel.SelectionFlag.Select
                    | QItemSelectionModel.SelectionFlag.Rows,
                )

    def print_products(self, action=None):
        self.table.setFocus()
        if (
            self.floor.instruction_print_options["mode"] == "map"
            and self.floor.instruction_print_options["all_entries"]
        ):
            return list(self.visible)
        if not any(p.included for p in self.visible):
            if not self.ask_output_selection():
                raise AppError("出力対象の選択をキャンセルしました。")
            self.set_all(True)
            if not any(p.included for p in self.visible):
                raise AppError("現在の登録日・表示範囲に指示がありません。")
        if hasattr(self, "preflight"):
            self.preflight.refresh()
            if action is not None and not self.preflight.request(action):
                from .preflight_ui import PreflightPending

                raise PreflightPending("印刷前チェックで内容を確認してください。")
        return list(self.visible)

    def paint_printer(self, printer):
        try:
            from .instruction_output import render_output

            plan = render_output(
                printer,
                list(self.visible),
                self.floor.physical_machines,
                self.floor.number_references,
                self.selected_day(),
                self.page_rows.value(),
                self.layout_name(),
                self.print_mode.currentData(),
                self.floor.instruction_print_options,
            )
            self.statusBar().showMessage(plan.reason, 15000)
            return plan
        except Exception as exc:
            self.error(exc)

    def preview(self):
        if self.tabs.currentWidget() is self.floor:
            return self.floor.preview()
        if self.tabs.currentWidget() is self.labels:
            return self.labels.preview()
        try:
            self.print_products(self.preview)
            if self.floor.instruction_print_options["mode"] != "instructions":
                from .instruction_preview import InstructionPreview

                InstructionPreview(self).exec()
                return
            printer = configured_printer()
            dialog = QPrintPreviewDialog(printer, self)
            dialog.setWindowTitle("印刷プレビュー — A4横向き")
            dialog.resize(1300, 850)
            controls = QWidget(dialog)
            controls_layout = QHBoxLayout(controls)
            controls_layout.addWidget(QLabel("印刷レイアウト"))
            mode = QComboBox(controls)
            for label, key in (
                ("自動最適化", "auto"),
                ("標準（読みやすさ優先）", "standard"),
                ("コンパクト（用紙節約）", "compact"),
            ):
                mode.addItem(label, key)
            mode.setCurrentIndex(self.print_mode.currentIndex())
            controls_layout.addWidget(mode)
            info = QLabel()
            info.setWordWrap(True)
            controls_layout.addWidget(info, 1)
            dialog.layout().insertWidget(0, controls)

            def paint(target):
                plan = self.paint_printer(target)
                if plan:
                    info.setText(plan.reason)

            def change(index):
                self.print_mode.setCurrentIndex(index)
                dialog.findChild(QPrintPreviewWidget).updatePreview()

            mode.currentIndexChanged.connect(change)
            dialog.paintRequested.connect(paint)
            dialog.exec()
        except Exception as exc:
            self.error(exc)

    def print_direct(self):
        if self.tabs.currentWidget() is self.floor:
            return self.floor.print_floor()
        if self.tabs.currentWidget() is self.labels:
            return self.labels.print_direct()
        try:
            self.print_products(self.print_direct)
            printer = configured_printer()
            area = printer.pageRect(QPrinter.Unit.Point)
            from .instruction_output import output_plan

            plan = output_plan(
                printer,
                self.visible,
                self.floor.physical_machines,
                self.floor.number_references,
                self.selected_day(),
                self.page_rows.value(),
                self.layout_name(),
                self.print_mode.currentData(),
                self.floor.instruction_print_options,
            )
            dialog = QPrintDialog(printer, self)
            dialog.setMinMax(1, len(plan.pages))
            dialog.setOption(QAbstractPrintDialog.PrintDialogOption.PrintPageRange, True)
            if dialog.exec() == QDialog.DialogCode.Accepted:
                if self.paint_printer(printer):
                    self.workflow.record_file("output", "プリンターへ送信", "直接印刷")
                    self.statusBar().showMessage(
                        "印刷ジョブを送信しました。実際の出力はプリンターで確認してください。",
                        10000,
                    )
        except Exception as exc:
            self.error(exc)

    def export_pdf(self):
        if self.tabs.currentWidget() is self.floor:
            return self.floor.export_floor_pdf()
        if self.tabs.currentWidget() is self.labels:
            return self.labels.export_pdf()
        try:
            products = self.print_products(self.export_pdf)
            path = self.save_path("PDF保存", "PDF (*.pdf)", ".pdf")
            if path:
                from .printing import save_rendered_pdf

                plan = save_rendered_pdf(
                    path,
                    lambda temp: self.paint_printer(configured_printer(temp)),
                    [self.source, self.floor.source],
                )
                self.workflow.record_file(
                    "output",
                    path,
                    {
                        "instructions": "指示書PDF",
                        "map": "入替予定地図PDF",
                        "both": "指示書＋地図PDF",
                    }[self.floor.instruction_print_options["mode"]],
                )
                self.statusBar().showMessage(f"PDFを保存しました：{path} {plan.reason}", 15000)
        except Exception as exc:
            self.error(exc)

    def mark_dirty(self, *_):
        self.dirty = True
        if hasattr(self, "instruction_save_timer") and not self._loading_instructions:
            self.instruction_save_timer.start(800)

    def error(self, exc):
        from .preflight_ui import PreflightPending

        if isinstance(exc, PreflightPending):
            return
        if str(exc) == "出力対象の選択をキャンセルしました。":
            return
        QMessageBox.warning(self, "処理できませんでした", str(exc))

    def confirm_discard(self):
        return (
            not self.dirty
            or QMessageBox.question(
                self,
                "編集中の内容",
                "一時保存ファイルへ未保存の編集があります。自動保存を保持して続けますか？",
            )
            == QMessageBox.StandardButton.Yes
        )

    def closeEvent(self, event):
        if self.confirm_discard():
            if hasattr(self, "instruction_save_timer") and self.instruction_save_timer.isActive():
                self.instruction_save_timer.stop()
                self.autosave_instructions()
            if self.floor.timer.isActive():
                self.floor.timer.stop()
                self.floor.autosave()
            if getattr(self.floor, "temporal", None):
                self.floor.temporal.shutdown()
            event.accept()
        else:
            event.ignore()

    def selected_day(self):
        return self.day.date().toPython()

    def layout_name(self):
        return "row" if self.layout_choice.currentIndex() else "column"

    def update_dates(self):
        self.dates.clear()
        fmt = QTextCharFormat()
        from .theme import analysis_color, contrast_text

        calendar_bg = analysis_color("#DBEAFE")
        fmt.setBackground(QColor(calendar_bg))
        fmt.setForeground(QColor(contrast_text(calendar_bg)))
        self.day.calendarWidget().setDateTextFormat(QDate(), QTextCharFormat())
        for day in sorted({p.day for p in self.products}):
            self.dates.addItem(day.isoformat())
            self.day.calendarWidget().setDateTextFormat(QDate(day.year, day.month, day.day), fmt)

    def pick_date(self, index):
        self.day.setDate(QDate.fromString(self.dates.itemText(index), "yyyy-MM-dd"))

    def open_excel(self):
        path, _ = QFileDialog.getOpenFileName(self, "入力Excelを選択", "", "Excel (*.xlsx *.xlsm)")
        if not path:
            return
        try:
            products, warnings = read_products(path)
            if self.products:
                from PySide6.QtWidgets import QInputDialog

                option, ok = QInputDialog.getItem(
                    self,
                    "Excel更新時の手動編集",
                    "更新方針",
                    ["手動編集を優先", "Excelの最新値を使用", "項目ごとに確認"],
                    0,
                    False,
                )
                if not ok:
                    return
                policy = {
                    "手動編集を優先": "manual",
                    "Excelの最新値を使用": "excel",
                    "項目ごとに確認": "fields",
                }[option]
                choices = {}
                if policy == "fields":
                    from copy import deepcopy
                    from .instructions import merge_import

                    _, conflicts = merge_import(
                        deepcopy(self.products), deepcopy(products), "manual"
                    )
                    for entry, key, new in conflicts:
                        if key == "identity":
                            continue
                        choices[(entry, key)] = (
                            QMessageBox.question(
                                self,
                                "更新項目を確認",
                                f"{key} のExcel最新値：{new}\nこの項目をExcelの値へ更新しますか？",
                            )
                            == QMessageBox.StandardButton.Yes
                        )
                self.merge_instruction_excel(products, policy, choices)
            else:
                from .instructions import prepare_entry, import_values

                for p in products:
                    prepare_entry(p)
                    p.import_snapshot = import_values(p)
                self.products = products
            self.source = path
            self.workflow.record_file("excel", path, "入荷予定表")
            self.file_label.setText(Path(path).name)
            self.file_label.setToolTip(path)
            self.update_dates()
            if products:
                first = min(p.day for p in products)
                self.day.setDate(QDate(first.year, first.month, first.day))
            self.refresh()
            self.mark_dirty()
            self.notice.setText(
                f"読込 {len(products)}件 ／ 警告 {len(warnings)}件。重複行は保持します。"
            )
            if warnings:
                dlg = QMessageBox(self)
                dlg.setWindowTitle("読込時の確認事項")
                dlg.setText(
                    f"{len(warnings)}件の確認事項があります。「詳細を表示」で内容を確認できます。"
                )
                dlg.setDetailedText("\n".join(warnings))
                dlg.exec()
        except Exception as exc:
            self.error(exc)

    def save_path(self, title, filter_, suffix):
        # QFileDialog performs the overwrite confirmation for the selected path.
        path, _ = QFileDialog.getSaveFileName(
            self, title, f"入替指示書_{self.selected_day().isoformat()}{suffix}", filter_
        )
        if path and not path.lower().endswith(suffix):
            path += suffix
            if (
                Path(path).exists()
                and QMessageBox.question(self, "上書き確認", f"{path}\nを上書きしますか？")
                != QMessageBox.StandardButton.Yes
            ):
                return ""
        return path

    def save_json(self):
        path = self.save_path("作業を一時保存", "JSON (*.json)", ".json")
        if path:
            try:
                save_session(
                    path,
                    self.products,
                    self.selected_day(),
                    self.source,
                    self.layout_name(),
                    self.page_rows.value(),
                    floor_layout=self.floor.state(),
                    label_state=self.labels.state(),
                    print_mode=self.print_mode.currentData(),
                    workflow_state=self.workflow.state(),
                )
                self.workflow.record_file("saved", path, "入替指示書")
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
                self._loading_instructions = True
                products, day, data = load_session(path)
                if "floor_layout" in data:
                    validate_state(data["floor_layout"])
                label_store = LabelStore.restore(data.get("label_printing"), products)
                self.workflow.restore(data.get("workflow_state"))
                self.products, self.source = products, data.get("source", "")
                self.instruction_history.clear()
                self.labels.store = label_store
                self.labels.apply_options()
                self.file_label.setText(Path(self.source).name or "JSONから復元")
                self.update_dates()
                self.day.setDate(QDate(day.year, day.month, day.day))
                self.layout_choice.setCurrentIndex(1 if data.get("layout") == "row" else 0)
                self.page_rows.setValue(data.get("rows_per_page", 20))
                self.print_mode.setCurrentIndex(
                    self.print_mode.findData(data.get("print_mode", "auto"))
                )
                if "floor_layout" in data:
                    self.floor.restore(data["floor_layout"])
                    self.floor.schedule_save()
                self.refresh()
                self.dirty = False
                self.workflow.reset_watchers()
                self.notice.setText("一時保存から復元しました。商品一覧と配置を復元できます。")
            except Exception as exc:
                self.error(exc)
            finally:
                self._loading_instructions = False

    def export(self):
        try:
            self.print_products()
        except AppError as exc:
            self.error(exc)
            return
        path = self.save_path("Excel出力", "Excel (*.xlsx)", ".xlsx")
        if path:
            try:
                if self.floor.source and Path(path).resolve() == Path(self.floor.source).resolve():
                    raise AppError("元のレイアウトExcelには上書きできません。")
                pages = export_excel(
                    path,
                    self.visible,
                    self.selected_day(),
                    self.source,
                    self.page_rows.value(),
                    self.layout_name(),
                )
                QMessageBox.information(
                    self, "出力完了", f"{pages}ページ分を出力しました。\n{path}"
                )
            except Exception as exc:
                self.error(exc)


def main():
    app = QApplication(sys.argv)
    from .fonts import initialize_fonts

    initialize_fonts()
    app.setApplicationName("PrizeSwapInstructionMaker")
    app.setStyle("Fusion")
    window = Window()
    window.tabs.setCurrentWidget(window.dashboard_page)
    window.show()
    sys.exit(app.exec())
