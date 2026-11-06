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
)


def build_toolbar(owner, layout):
    def row():
        widget = QWidget()
        widget.setObjectName("floorTools")
        widget.setStyleSheet(
            "QWidget#floorTools QToolButton {padding:3px 6px; border:1px solid #CCD9D0; border-radius:4px; background:white;} QWidget#floorTools QComboBox, QWidget#floorTools QLineEdit, QWidget#floorTools QDateEdit, QWidget#floorTools QSpinBox {padding:2px 4px; min-height:18px;}"
        )
        line = QHBoxLayout(widget)
        line.setContentsMargins(0, 0, 0, 0)
        line.setSpacing(4)
        layout.addWidget(widget)
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
            ("レイアウトExcel読込", owner.import_dialog),
            ("配置JSON読込", owner.open_dialog),
            ("保存", owner.save_dialog),
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
    button(top, "詳細", owner.open_selected_details)
    owner.layout_mode = QComboBox()
    for label, key in (("実配置", "actual"), ("見やすさ優先", "readable"), ("一覧", "list")):
        owner.layout_mode.addItem(label, key)
    top.addWidget(owner.layout_mode)
    owner.layout_mode.currentIndexChanged.connect(owner.presentation_changed)
    owner.sort_by = QComboBox()
    for label, key in (
        ("番号順", "number"),
        ("アベ比順", "ratio"),
        ("投入日数順", "days"),
        ("商品名順", "product"),
    ):
        owner.sort_by.addItem(label, key)
    top.addWidget(owner.sort_by)
    owner.sort_by.currentIndexChanged.connect(owner.presentation_changed)
    owner.descending = QCheckBox("降順")
    top.addWidget(owner.descending)
    owner.descending.toggled.connect(owner.presentation_changed)
    top.addWidget(QLabel("基準日"))
    owner.reference_date = QDateEdit(QDate.currentDate())
    owner.reference_date.setCalendarPopup(True)
    owner.reference_date.setDisplayFormat("yyyy/MM/dd")
    owner.reference_date.setDateRange(QDate(1900, 1, 1), QDate(9999, 12, 31))
    top.addWidget(owner.reference_date)
    owner.reference_date.dateChanged.connect(owner.reference_changed)
    button(top, "今日", owner.today)
    top.addStretch()
    owner.fullscreen_button = button(top, "全画面", owner.toggle_fullscreen)
    second = row()
    owner.floor_search = QLineEdit()
    owner.floor_search.setPlaceholderText("マシン番号・商品名を検索")
    owner.floor_search.setClearButtonEnabled(True)
    second.addWidget(owner.floor_search, 1)
    owner.floor_search.textChanged.connect(owner.presentation_changed)
    owner.filter_by = QComboBox()
    for label, key in (
        ("すべて", "all"),
        ("50%未満", "poor"),
        ("200%以上", "high"),
        ("投入予定のみ", "planned"),
    ):
        owner.filter_by.addItem(label, key)
    second.addWidget(owner.filter_by)
    owner.filter_by.currentIndexChanged.connect(owner.presentation_changed)
    owner.days_filter = QCheckBox("投入日数")
    second.addWidget(owner.days_filter)
    owner.min_days = QSpinBox()
    owner.min_days.setRange(0, 100000)
    owner.max_days = QSpinBox()
    owner.max_days.setRange(0, 100000)
    owner.max_days.setValue(9999)
    second.addWidget(owner.min_days)
    second.addWidget(QLabel("〜"))
    second.addWidget(owner.max_days)
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
    button(second, "−", lambda: owner.set_zoom(owner.view.transform().m11() * 100 / 1.15))
    button(second, "＋", lambda: owner.set_zoom(owner.view.transform().m11() * 100 * 1.15))
    button(second, "全体", owner.fit)
    button(second, "選択へ", owner.zoom_selected)
    menu(
        second,
        "表示設定",
        [
            ("カード・文字サイズ", owner.card_settings),
            ("表示カードのプリセット", owner.presentation_settings),
            ("プリセット保存", owner.save_card_preset),
            ("プリセット適用", owner.load_card_preset),
            ("詳細表示項目（従来設定）", owner.display_settings),
        ],
    )
    owner.legend_toggle = QCheckBox("凡例")
    owner.legend_toggle.setChecked(True)
    second.addWidget(owner.legend_toggle)
    owner.legend_toggle.toggled.connect(owner.presentation_changed)
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
    layout.addWidget(owner.info)
