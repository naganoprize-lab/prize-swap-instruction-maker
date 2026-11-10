"""Two compact control rows; less frequent commands live in named menus."""

from PySide6.QtCore import QDate
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDateEdit,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QSpinBox,
    QToolButton,
    QWidget,
    QWidgetAction,
    QVBoxLayout,
)


def build_toolbar(owner, layout):
    owner.toolbar_rows = []
    owner.compact_toolbar_controls = []

    def row():
        widget = QWidget()
        widget.setObjectName("floorTools")
        from .control_flow import ControlFlow

        line = ControlFlow(widget)
        line.setContentsMargins(0, 0, 0, 0)
        line.setSpacing(4)
        layout.addWidget(widget)
        owner.toolbar_rows.append(widget)
        return line

    def button(line, text, callback):
        b = QToolButton()
        b.setText(text)
        b.clicked.connect(callback)
        line.addWidget(b)
        return b

    def menu(line, text, actions):
        b = QToolButton()
        b.setText(text)
        m = QMenu(b)
        for name, callback in actions:
            m.addAction(name, callback)
        b.setMenu(m)
        b.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        line.addWidget(b)
        return m

    top = row()
    menu(
        top,
        "ファイル",
        [
            (
                "Excel自動判別読込",
                lambda: (
                    owner.app_window.import_excel_auto(preferred="layout")
                    if hasattr(owner.app_window, "import_excel_auto")
                    else owner.import_dialog()
                ),
            ),
            ("レイアウトExcel読込", owner.import_dialog),
            ("配置JSON読込", owner.open_dialog),
            ("保存", owner.save_dialog),
        ],
    )
    owner.analysis_import_button = button(top, "売上分析Excel読込", owner.import_analysis_dialog)
    owner.analysis_import_button.setToolTip("売上分析Excel読込")
    menu(
        top,
        "分析",
        [
            ("選択マシンの詳細", owner.open_selected_details),
            ("実績読込結果・異常一覧", owner.show_analysis_report),
            ("管理ナンバー照合・手動確認", owner.show_matching),
            ("入替候補", owner.show_candidates),
            ("運営方法別分析", owner.show_method_analysis),
            ("売上履歴", owner.show_history),
            ("集計期間を手動設定", owner.manual_period),
        ],
    )
    editing = menu(
        top,
        "マシン編集",
        [
            ("追加", owner.add_dialog),
            ("削除", owner.delete_selected),
            ("複製", owner.duplicate_selected),
            ("元に戻す", owner.history.undo),
            ("やり直し", owner.history.redo),
            ("左揃え", lambda: owner.align_selected(1)),
            ("上揃え", lambda: owner.align_selected(2)),
            ("横に等間隔", lambda: owner.align_selected(3)),
            ("縦に等間隔", lambda: owner.align_selected(4)),
            ("元のExcel配置へ戻す", owner.reset_original),
        ],
    )
    owner.align = QComboBox(owner)
    owner.align.addItems(["整列を選択", "左揃え", "上揃え", "横に等間隔", "縦に等間隔"])
    owner.align.hide()  # Legacy public control; commands are in the menu.
    owner.edit_mode = QCheckBox("配置編集")
    owner.edit_mode.setToolTip("オン：ドラッグで移動・四隅でサイズ変更。オフ：クリックで詳細。")
    top.addWidget(owner.edit_mode)
    owner.edit_mode.toggled.connect(owner.presentation_changed)
    detail_button = button(top, "詳細", owner.open_selected_details)
    owner.layout_mode = QComboBox()
    for label, key in (
        ("実配置", "actual"),
        ("見やすさ優先", "readable"),
        ("一覧", "list"),
    ):
        owner.layout_mode.addItem(label, key)
    top.addWidget(owner.layout_mode)
    owner.render_mode = QComboBox()
    for label, key in (("ノーマル", "normal"), ("軽量", "light")):
        owner.render_mode.addItem(label, key)
    owner.render_mode.setToolTip(
        "軽量は番号・色・選択だけを描画します。計算・印刷品質は変わりません。"
    )
    owner.render_mode.currentIndexChanged.connect(owner.presentation_changed)
    top.addWidget(owner.render_mode)
    from .floor_status import COLOR_FILTERS

    owner.color_filter = QComboBox()
    for key, label in COLOR_FILTERS.items():
        owner.color_filter.addItem(label + "フィルター", key)
    owner.color_filter.setToolTip("色・表示項目の切替です。マシンを非表示にしません。")
    top.addWidget(owner.color_filter)
    owner.color_filter.currentIndexChanged.connect(owner.color_filter_changed)
    owner.layout_mode.currentIndexChanged.connect(owner.presentation_changed)
    owner.sort_by = QComboBox()
    for label, key in (
        ("番号順", "number"),
        ("アベ比順", "ratio"),
        ("投入日数順", "days"),
        ("商品名順", "product"),
    ):
        owner.sort_by.addItem(label, key)
    owner.sort_by.setParent(owner)
    owner.sort_by.hide()
    owner.sort_by.currentIndexChanged.connect(owner.presentation_changed)
    owner.descending = QCheckBox("降順")
    owner.descending.setParent(owner)
    owner.descending.hide()
    owner.descending.toggled.connect(owner.presentation_changed)
    top.addWidget(QLabel("基準日"))
    owner.reference_date = QDateEdit(QDate.currentDate())
    owner.reference_date.setCalendarPopup(True)
    owner.reference_date.setDisplayFormat("yyyy/MM/dd")
    owner.reference_date.setDateRange(QDate(1900, 1, 1), QDate(9999, 12, 31))
    top.addWidget(owner.reference_date)
    owner.reference_date.dateChanged.connect(owner.reference_changed)
    today_button = button(top, "今日", owner.today)
    owner.fullscreen_button = button(top, "全画面", owner.toggle_fullscreen)
    owner.compact_toolbar_controls.extend(
        [
            owner.edit_mode,
            detail_button,
            owner.layout_mode,
            owner.render_mode,
            today_button,
            owner.fullscreen_button,
        ]
    )
    from .selection import selection_controls

    selection_bar = selection_controls(owner)
    owner.selection_bar = selection_bar
    owner.selection_popup = QToolButton()

    def selection_summary():
        owner.selection_popup.setText(
            f"選択（{len(owner.selection_state.selected_machine_keys)}台）"
        )

    owner.selection_state.changed.connect(selection_summary)
    selection_summary()
    selection_menu = QMenu(owner.selection_popup)
    selection_action = QWidgetAction(selection_menu)
    selection_action.setDefaultWidget(selection_controls(owner))
    selection_menu.addAction(selection_action)
    owner.selection_popup.setMenu(selection_menu)
    owner.selection_popup.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
    owner.selection_popup.hide()
    top.addWidget(owner.selection_popup)
    layout.addWidget(selection_bar)
    owner.toolbar_rows.append(selection_bar)
    second = row()
    owner.floor_search = QLineEdit()
    owner.floor_search.setPlaceholderText("番号・商品・機種・方法を検索")
    owner.floor_search.setClearButtonEnabled(True)
    owner.floor_search.setMinimumWidth(160)
    owner.floor_search.setMaximumWidth(360)
    second.addWidget(owner.floor_search)
    owner.floor_search.textChanged.connect(owner.presentation_changed)
    owner.search_count = QLabel("検索結果：0 / 0ブース")
    owner.search_count.setMinimumWidth(0)
    second.addWidget(owner.search_count)
    owner.compact_toolbar_controls.append(button(second, "前へ", lambda: owner.next_match(-1)))
    owner.compact_toolbar_controls.append(button(second, "次へ", lambda: owner.next_match(1)))
    conditions = QToolButton()
    conditions.setText("強調条件")
    conditions_menu = QMenu(conditions)
    conditions_menu.addAction("検索結果の前へ", lambda: owner.next_match(-1))
    conditions_menu.addAction("検索結果の次へ", lambda: owner.next_match(1))
    conditions_page = QWidget()
    conditions_layout = QVBoxLayout(conditions_page)
    action = QWidgetAction(conditions_menu)
    action.setDefaultWidget(conditions_page)
    conditions_menu.addAction(action)
    conditions.setMenu(conditions_menu)
    conditions.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
    second.addWidget(conditions)
    owner.filter_by = QComboBox()
    for label, key in (
        ("強調なし", "all"),
        ("強調：50%未満", "poor"),
        ("強調：200%以上", "high"),
        ("強調：投入予定", "planned"),
    ):
        owner.filter_by.addItem(label, key)
    conditions_layout.addWidget(owner.filter_by)
    owner.filter_by.currentIndexChanged.connect(owner.presentation_changed)
    owner.days_filter = QCheckBox("投入日数")
    conditions_layout.addWidget(owner.days_filter)
    owner.min_days = QSpinBox()
    owner.min_days.setRange(0, 100000)
    owner.max_days = QSpinBox()
    owner.max_days.setRange(0, 100000)
    owner.max_days.setValue(9999)
    conditions_layout.addWidget(owner.min_days)
    conditions_layout.addWidget(QLabel("〜"))
    conditions_layout.addWidget(owner.max_days)
    for c in (owner.min_days, owner.max_days):
        c.valueChanged.connect(owner.presentation_changed)
    owner.days_filter.toggled.connect(owner.presentation_changed)
    owner.min_days.setEnabled(False)
    owner.max_days.setEnabled(False)
    owner.zoom_combo = QComboBox()
    owner.zoom_combo.setEditable(True)
    owner.zoom_combo.addItems(["50%", "75%", "100%", "150%", "200%", "300%"])
    owner.zoom_combo.setCurrentText("100%")
    owner.zoom_combo.setMinimumContentsLength(8)
    owner.zoom_combo.setSizeAdjustPolicy(
        QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
    )
    second.addWidget(owner.zoom_combo)
    owner.zoom_combo.activated.connect(owner.zoom_entered)
    owner.zoom_combo.lineEdit().editingFinished.connect(owner.zoom_entered)
    owner.compact_toolbar_controls.append(
        button(second, "−", lambda: owner.set_zoom(owner.view.transform().m11() * 100 / 1.15))
    )
    owner.compact_toolbar_controls.append(
        button(second, "＋", lambda: owner.set_zoom(owner.view.transform().m11() * 100 * 1.15))
    )
    button(second, "全体", owner.fit)
    button(second, "選択へ", owner.zoom_selected)
    view_menu = menu(
        second,
        "表示設定",
        [
            ("全画面の切替", owner.toggle_fullscreen),
            ("配置編集の切替", owner.edit_mode.toggle),
            (
                "描画モードの切替",
                lambda: owner.render_mode.setCurrentIndex(1 - owner.render_mode.currentIndex()),
            ),
            ("今日を基準日にする", owner.today),
            *[
                (
                    "配置：" + owner.layout_mode.itemText(i),
                    lambda checked=False, i=i: owner.layout_mode.setCurrentIndex(i),
                )
                for i in range(owner.layout_mode.count())
            ],
            ("凡例の表示切替", lambda: owner.legend_toggle.toggle()),
            ("分析・判定・色・照合の設定", owner.analysis_settings_dialog),
            ("カード・文字サイズ", owner.card_settings),
            ("表示カードのプリセット", owner.presentation_settings),
            ("一覧の並び順", owner.sort_menu),
            ("プリセット保存", owner.save_card_preset),
            ("プリセット適用", owner.load_card_preset),
            ("詳細表示項目（従来設定）", owner.display_settings),
        ],
    )
    owner.legend_toggle = QCheckBox("凡例")
    owner.legend_toggle.setChecked(True)
    second.addWidget(owner.legend_toggle)
    owner.compact_toolbar_controls.append(owner.legend_toggle)
    owner.legend_toggle.toggled.connect(owner.presentation_changed)
    for label, control in (
        ("配置編集の切替", owner.edit_mode),
        ("凡例の表示切替", owner.legend_toggle),
    ):
        action = next(action for action in view_menu.actions() if action.text() == label)
        action.setCheckable(True)
        action.setChecked(control.isChecked())
        control.toggled.connect(action.setChecked)
    menu(
        second,
        "印刷",
        [
            ("用紙・印刷設定", owner.print_settings),
            ("印刷プレビュー", owner.preview),
            ("PDF保存", owner.export_floor_pdf),
            ("直接印刷", owner.print_floor),
        ],
    )
    owner.print_note = QLabel(owner)
    owner.print_note.hide()
    owner.data_note = QLabel(owner)
    owner.data_note.hide()
    owner.display_mode = QComboBox(owner)
    for label, key in (
        ("通常（4項目）", "compact"),
        ("商品名のみ", "product"),
        ("売上分析", "analysis"),
        ("すべて表示", "all"),
    ):
        owner.display_mode.addItem(label, key)
    owner.display_mode.hide()
    owner.display_mode.currentIndexChanged.connect(owner.display_changed)
    owner.show_methods = QCheckBox(owner)
    owner.show_methods.hide()
    owner.show_methods.toggled.connect(owner.display_changed)
    owner.snap = QCheckBox("グリッドにスナップ", owner)
    owner.snap.setChecked(True)
    owner.snap.hide()
    owner.snap.toggled.connect(owner.schedule_save)
    owner.info = QLabel()
    owner.info.setWordWrap(True)
    owner.info.setMaximumHeight(44)
    layout.addWidget(owner.info)
    owner.analysis_clear = QToolButton()
    owner.analysis_clear.setText("分析による強調表示を解除")
    owner.analysis_clear.setToolTip(
        "分析の強調だけを解除し、通常の検索・倍率・フィルターを維持します。"
    )
    owner.analysis_clear.clicked.connect(owner.clear_analysis_highlight)
    owner.analysis_clear.hide()
    owner.analysis_note = QLabel()
    owner.analysis_note.setWordWrap(True)
    owner.analysis_note.hide()
    layout.addWidget(owner.analysis_note)
