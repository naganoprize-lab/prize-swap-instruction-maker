"""Compact entry point; settings live in a scrollable dialog, not over the canvas."""

from copy import deepcopy
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QVBoxLayout,
    QTabWidget,
    QWidget,
    QFormLayout,
    QDoubleSpinBox,
    QSpinBox,
    QComboBox,
    QLabel,
    QCheckBox,
    QTableWidget,
    QTableWidgetItem,
    QPushButton,
    QMessageBox,
    QScrollArea,
)
from .analysis_settings import validate_settings, machine_type, type_style, normalize_type

NUMBERS = {
    "po_low": ("PO率・低い境界 (%)", 100),
    "po_high": ("PO率・高い境界 (%)", 100),
    "po_reference": ("PO率・基準 (%)", 100),
    "cost_low": ("原価率・低すぎる境界 (%)", 100),
    "cost_near": ("原価率・目標付近下限 (%)", 100),
    "cost_high": ("原価率・高い境界 (%)", 100),
    "cost_target": ("目標原価率 (%)", 100),
    "default_play_price": ("標準プレイ料金（円・仮設定）", 1),
    "po_tolerance": ("理論PO率からの許容偏差 (%)", 100),
    "sales_benchmark": ("基準平均売上 (円・0で未設定)", 1),
    "sales_low": ("固定金額・低い境界 (円)", 1),
    "sales_middle": ("固定金額・中間境界 (円)", 1),
    "sales_high": ("固定金額・高い境界 (円)", 1),
    "tolerance_absolute": ("照合許容差・絶対額 (円)", 1),
    "tolerance_relative": ("照合許容差・相対差 (%)", 100),
    "poor_ratio": ("要確認・アベ比未満 (%)", 1),
    "consider_ratio": ("入替検討・アベ比未満 (%)", 1),
    "poor_days": ("要確認・最低投入日数", 1),
    "consider_days": ("入替検討・最低投入日数", 1),
    "stale_days": ("鮮度警告までの日数", 1),
    "decline_ratio": ("売上低下・前回比未満 (%)", 100),
    "long_days": ("長期投入の日数", 1),
}


def edit_analysis_settings(owner):
    dialog = QDialog(owner)
    dialog.setWindowTitle("分析・照合・判定・機種色の設定")
    dialog.resize(680, 680)
    outer = QVBoxLayout(dialog)
    tabs = QTabWidget()
    outer.addWidget(tabs, 1)
    controls = {}
    s = owner.analysis_settings
    disabled = {"po_low", "po_high", "po_reference", "sales_benchmark", "sales_middle"}
    rule_keys = {
        "poor_ratio",
        "consider_ratio",
        "poor_days",
        "consider_days",
        "stale_days",
        "decline_ratio",
        "long_days",
    }
    for title, keys in [
        ("指標と照合", [k for k in NUMBERS if k not in disabled | rule_keys]),
        ("入替判定", [k for k in NUMBERS if k in rule_keys]),
    ]:
        page = QWidget()
        form = QFormLayout(page)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(page)
        tabs.addTab(scroll, title)
        for key in keys:
            label, multiplier = NUMBERS[key]
            control = QSpinBox() if key.endswith("_days") else QDoubleSpinBox()
            control.setRange(0, 1000000000)
            control.setValue((s[key] or 0) * multiplier)
            if isinstance(control, QDoubleSpinBox):
                control.setDecimals(3)
            form.addRow(label, control)
            controls[key] = control
        if title == "指標と照合":
            mode = QComboBox()
            for label, key in [
                ("固定金額", "fixed"),
                ("全台の売上金額・上位25%／中位50%／下位25%", "quantile"),
            ]:
                mode.addItem(label, key)
            mode.setCurrentIndex(mode.findData(s["sales_mode"]))
            form.addRow("売上比較方式", mode)
            form.addRow(
                QLabel(
                    "売上は現在のExcelの集計期間ごとに比較します。\nPO率は原価・料金から算出する理論目安です。未確認原価や料金は確定扱いしません。"
                )
            )
    types = QTableWidget(0, 4)
    types.setHorizontalHeaderLabels(
        ["元の機種名（表記対応）", "統一する機種名", "短縮名", "色 #RRGGBB"]
    )
    names = sorted(
        set([machine_type(m, s) for m in owner.machines] + list(s["aliases"]) + list(s["types"]))
    )

    def add_row(raw=""):
        i = types.rowCount()
        types.insertRow(i)
        name = s["aliases"].get(raw, raw)
        style = type_style(name, s)
        for j, v in enumerate((raw, name, style["short"], style["color"])):
            types.setItem(i, j, QTableWidgetItem(v))

    for name in names:
        add_row(name)
    typepage = QWidget()
    tlay = QVBoxLayout(typepage)
    tlay.addWidget(
        QLabel("別機種を自動統合しません。表記対応・短縮名・色はここで明示的に指定します。")
    )
    tlay.addWidget(types)
    add = QPushButton("機種を追加")
    add.clicked.connect(lambda: add_row())
    tlay.addWidget(add)
    tabs.addTab(typepage, "機種・色")
    radar_page = QWidget()
    radar_form = QFormLayout(radar_page)
    radar_note = QLabel(
        "レーダーは5項目の状態を示します。内部正規化は座標だけに使い、画面には実測値を表示します。\n売上：同じExcel・期間の全台順位。投入日数：新しさ。原価率：目標への適合度。\n理論PO率＝1プレイ料金×目標原価率÷景品1個の原価。PO評価は理論値からの相対偏差です。\n景品原価と料金はマシン詳細で確認・設定します。標準料金は仮設定です。"
    )
    radar_note.setWordWrap(True)
    radar_form.addRow(radar_note)
    tabs.addTab(radar_page, "5項目の状態分析")
    buttons = QDialogButtonBox(
        QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
    )
    outer.addWidget(buttons)
    buttons.rejected.connect(dialog.reject)

    def save():
        try:
            data = deepcopy(s)
            for key, c in controls.items():
                data[key] = c.value() / NUMBERS[key][1] if not key.endswith("_days") else c.value()
            if data["sales_benchmark"] == 0:
                data["sales_benchmark"] = None
            data["sales_mode"] = mode.currentData()
            data["aliases"] = {}
            data["types"] = {}
            for i in range(types.rowCount()):
                raw, name, short, color = [
                    types.item(i, j).text().strip() if types.item(i, j) else "" for j in range(4)
                ]
                if not raw and not name:
                    continue
                raw, name = normalize_type(raw), normalize_type(name)
                data["aliases"][raw] = name
                data["types"][name] = dict(short=short, color=color)
            owner.analysis_settings = validate_settings(data)
            owner.print_options.analysis_settings = deepcopy(owner.analysis_settings)
            owner.sync()
            owner.update_info()
            owner.schedule_save()
            dialog.accept()
        except Exception as exc:
            QMessageBox.warning(dialog, "設定エラー", str(exc))

    buttons.accepted.connect(save)
    dialog.exec()
