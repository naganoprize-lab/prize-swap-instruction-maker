"""Independent floor-print controls and a live, shared-engine preview."""

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QSplitter,
    QVBoxLayout,
    QWidget,
)
from PySide6.QtPrintSupport import QPrintPreviewWidget

from .core import AppError
from .floor_status import COLOR_FILTERS
from .floor_render import FIELDS, FIELD_LABELS
from .floor_print import FloorPrintOptions, floor_printer


class FloorPrintControls(QWidget):
    changed = Signal(object)
    invalid = Signal(str)

    def __init__(self, options, parent=None):
        super().__init__(parent)
        self.analysis_settings = dict(options.analysis_settings)
        form = QFormLayout(self)
        self.combos = {}
        self.numbers = {}
        self.checks = {}
        choices = {
            "color_filter": [(label, key) for key, label in COLOR_FILTERS.items()],
            "paper": [(p, p) for p in ("A4", "A3", "A2", "A1", "A0")]
            + [("ユーザー指定", "custom")],
            "orientation": [("横向き", "landscape"), ("縦向き", "portrait")],
            "mode": [
                ("1枚に全体を収める", "fit"),
                ("エリア別に分割して拡大", "tile"),
                ("選択範囲だけを1枚に", "selection"),
                ("指定倍率で複数ページ", "actual"),
                ("マシン一覧表", "list"),
            ],
            "font_size": [("標準", "standard"), ("大", "large"), ("特大", "xlarge")],
            "card_style": [("通常（フィルターの3項目）", "summary"), ("詳細項目", "detailed")],
            "sort": [
                ("マシン番号", "number"),
                ("アベ比", "ratio"),
                ("投入日数", "days"),
                ("商品名", "product"),
            ],
            "card_size": [
                ("自動（文字を優先）", "auto"),
                ("標準", "standard"),
                ("拡大", "enlarge"),
            ],
        }
        labels = {
            "color_filter": "印刷用の色フィルター",
            "paper": "用紙",
            "orientation": "方向",
            "mode": "印刷方式",
            "font_size": "印刷文字",
            "card_size": "印刷カード",
            "card_style": "カード表示",
            "sort": "一覧表の順序",
        }
        for key, items in choices.items():
            c = QComboBox()
            for label, value in items:
                c.addItem(label, value)
            c.setCurrentIndex(c.findData(getattr(options, key)))
            self.combos[key] = c
            form.addRow(labels[key], c)
        for key, label, low, high in (
            ("width_mm", "用紙の短辺(mm)", 50, 5000),
            ("height_mm", "用紙の長辺(mm)", 50, 5000),
            ("scale_percent", "倍率(%)", 10, 400),
            ("card_width", "印刷カード幅", 80, 2000),
            ("card_height", "印刷カード高さ", 60, 2000),
        ):
            spin = QDoubleSpinBox()
            spin.setRange(low, high)
            spin.setValue(getattr(options, key))
            spin.setDecimals(1)
            form.addRow(label, spin)
            self.numbers[key] = spin
        for key, label in (
            ("colour", "カラー（解除で白黒）"),
            ("descending", "一覧表を降順"),
            ("visible_only", "画面の検索・強調一致だけを印刷"),
            ("show_methods", "運営方法を印刷"),
            ("selection_only", "選択したマシンだけ"),
            ("boundaries", "画面にページ境界を表示"),
        ):
            check = QCheckBox(label)
            check.setChecked(getattr(options, key))
            self.checks[key] = check
            form.addRow(check)
        self.fields = {}
        form.addRow(QLabel("詳細項目モードのみ選択可（通常・一覧表はフィルターの3項目）"))
        for field in FIELDS:
            check = QCheckBox(FIELD_LABELS[field])
            check.setChecked(field in options.fields)
            self.fields[field] = check
            form.addRow(check)
        hint = QLabel(
            "画面の倍率・スクロール・表示モード・文字サイズとは独立します。\n100%は画面100%時のカード寸法相当です。実際の筐体寸法ではありません。\n分割印刷ではカードを切らず、各カードを1回ずつ印刷します。"
        )
        hint.setWordWrap(True)
        form.addRow(hint)
        for c in self.combos.values():
            c.currentIndexChanged.connect(self.notify)
        for spin in self.numbers.values():
            spin.valueChanged.connect(self.notify)
        for check in [*self.checks.values(), *self.fields.values()]:
            check.toggled.connect(self.notify)
        self.update_enabled()

    def update_enabled(self):
        table = self.combos["mode"].currentData() == "list"
        for field in self.fields.values():
            field.setEnabled(not table and self.combos["card_style"].currentData() == "detailed")
        for key in ("card_width", "card_height"):
            self.numbers[key].setEnabled(not table)
        for key in ("card_style", "card_size"):
            self.combos[key].setEnabled(not table)
        self.checks["show_methods"].setEnabled(not table)
        custom = self.combos["paper"].currentData() == "custom"
        for key in ("width_mm", "height_mm"):
            self.numbers[key].setEnabled(custom)
        self.numbers["scale_percent"].setEnabled(
            self.combos["mode"].currentData() in ("tile", "actual")
        )

    def options(self):
        data = {key: c.currentData() for key, c in self.combos.items()}
        data.update({key: s.value() for key, s in self.numbers.items()})
        data.update({key: c.isChecked() for key, c in self.checks.items()})
        data["analysis_settings"] = dict(self.analysis_settings)
        data["fields"] = [key for key, c in self.fields.items() if c.isChecked()]
        return FloorPrintOptions(**data).validate()

    def notify(self, *_):
        self.update_enabled()
        try:
            self.changed.emit(self.options())
        except AppError as exc:
            self.invalid.emit(str(exc))


class FloorPreviewDialog(QDialog):
    def __init__(self, owner):
        super().__init__(owner)
        self.owner = owner
        self.setWindowTitle("売り場レイアウト印刷プレビュー")
        self.resize(1500, 950)
        outer = QVBoxLayout(self)
        self.info = QLabel()
        self.info.setWordWrap(True)
        outer.addWidget(self.info)
        split = QSplitter()
        outer.addWidget(split, 1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setMaximumWidth(380)
        self.controls = FloorPrintControls(owner.print_options)
        scroll.setWidget(self.controls)
        split.addWidget(scroll)
        self.printer = floor_printer(owner.print_options, preview=True)
        self.preview = QPrintPreviewWidget(self.printer, self)
        split.addWidget(self.preview)
        split.setSizes([350, 1100])
        split.setStretchFactor(1, 1)
        self.preview.paintRequested.connect(self.paint)
        self.controls.changed.connect(self.update_options)
        self.controls.invalid.connect(self.invalid)
        bottom = QHBoxLayout()
        outer.addLayout(bottom)
        bottom.addWidget(QLabel("ページ"))
        self.page = QSpinBox()
        self.page.setMinimum(1)
        self.page.valueChanged.connect(self.preview.setCurrentPage)
        bottom.addWidget(self.page)
        for label, callback in (
            ("拡大", self.preview.zoomIn),
            ("縮小", self.preview.zoomOut),
            ("全体", self.preview.fitInView),
        ):
            b = QPushButton(label)
            b.clicked.connect(callback)
            bottom.addWidget(b)
        bottom.addStretch()
        self.pdf = QPushButton("PDF保存")
        self.pdf.clicked.connect(owner.export_floor_pdf)
        bottom.addWidget(self.pdf)
        self.direct = QPushButton("直接印刷")
        self.direct.clicked.connect(owner.print_floor)
        bottom.addWidget(self.direct)
        close = QPushButton("閉じる")
        close.clicked.connect(self.accept)
        bottom.addWidget(close)

    def invalid(self, text):
        self.info.setText(text)
        self.pdf.setEnabled(False)
        self.direct.setEnabled(False)

    def update_options(self, options):
        self.owner.set_print_options(options)
        configured = floor_printer(options, preview=True)
        self.printer.setPageLayout(configured.pageLayout())
        self.printer.setColorMode(configured.colorMode())
        self.preview.updatePreview()

    def paint(self, printer):
        try:
            plan = self.owner.paint_floor(printer)
            self.page.blockSignals(True)
            self.page.setMaximum(len(plan.pages))
            self.page.setValue(self.preview.currentPage())
            self.page.blockSignals(False)
            text = f"{plan.options.paper} / {len(plan.pages)}ページ / 基準日{plan.reference:%Y/%m/%d}。売上等は現在の読込値です。"
            if plan.warnings:
                text += "\n" + "\n".join(plan.warnings)
            self.info.setText(text)
            self.pdf.setEnabled(True)
            self.direct.setEnabled(True)
        except Exception as exc:
            self.invalid(str(exc))
