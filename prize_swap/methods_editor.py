"""Cell dropdown with checkboxes and integer counts; never opens a dialog."""

from PySide6.QtCore import QPoint, Qt, Signal, QTimer
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .core import METHODS, AppError
from types import SimpleNamespace
from .methods import (
    MAX_COUNT,
    parse_allocations,
    format_cell_status,
    allocation_status,
    booth_totals,
    total_booths,
    booth_value,
)
from .booth_style import METRIC_COLORS, METRIC_LABELS


class MethodsCombo(QComboBox):
    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setEditable(True)
        self.lineEdit().setReadOnly(True)
        self.lineEdit().setPlaceholderText("予定数・マシン別割当を設定")
        self._counts, self._assignments, self.registered = {}, {}, 0
        self.in_change = False
        self.opening_popup = False
        self.popup = QFrame(self, Qt.WindowType.Popup)
        self.popup.setObjectName("card")
        self.popup.setMinimumWidth(590)
        self.popup.setMaximumWidth(740)
        outer = QVBoxLayout(self.popup)
        outer.setContentsMargins(10, 10, 10, 10)
        self.status = QLabel()
        self.status.setWordWrap(True)
        outer.addWidget(self.status)
        self.tabs = QTabWidget()
        outer.addWidget(self.tabs)
        plan_panel = QWidget()
        plan_layout = QVBoxLayout(plan_panel)
        plan_layout.setContentsMargins(0, 0, 0, 0)
        self.tabs.addTab(plan_panel, "予定ブース数・商品集計")
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setMinimumHeight(200)
        scroll.setMaximumHeight(340)
        self.row_widget = QWidget()
        self.row_widget.setObjectName("boothGrid")
        self.row_widget.setStyleSheet("QWidget#boothGrid { background-color: white; }")
        self.grid = QGridLayout(self.row_widget)
        self.grid.setContentsMargins(0, 0, 0, 0)
        scroll.setWidget(self.row_widget)
        plan_layout.addWidget(scroll)
        machine_scroll = QScrollArea()
        machine_scroll.setWidgetResizable(True)
        self.machine_widget = QWidget()
        self.machine_grid = QGridLayout(self.machine_widget)
        machine_scroll.setWidget(self.machine_widget)
        self.tabs.addTab(machine_scroll, "マシン別の運営方法")
        custom = QHBoxLayout()
        custom.addWidget(QLabel("自由記載"))
        self.custom_name = QLineEdit()
        self.custom_name.setPlaceholderText("運営方法名を入力（旧方式も自由記載として保持）")
        custom.addWidget(self.custom_name)
        add = QPushButton("追加")
        add.clicked.connect(self.add_custom)
        self.custom_name.returnPressed.connect(self.add_custom)
        custom.addWidget(add)
        outer.addLayout(custom)
        hint = QLabel(
            "数値は予定ブース数です。マシン別の割当とは独立して保存します。\n0＝予定なし / 空欄＝旧設定の予定未設定。Tab・↑↓・Escで操作できます。"
        )
        hint.setWordWrap(True)
        outer.addWidget(hint)
        done = QPushButton("閉じる")
        done.clicked.connect(self.hidePopup)
        outer.addWidget(done)
        self.rows = {}
        self.metric_labels = {}
        self._building = False

    def product_state(self):
        return SimpleNamespace(
            methods=[n for n, c in self._counts.items() if c != 0],
            planned_booths=self._counts,
            machines=list(self._assignments),
            machine_methods=self._assignments,
        )

    def counts(self):
        return dict(self._counts)

    def state(self):
        return dict(
            plans=list(self._counts.items()),
            assignments=list(self._assignments.items()),
        )

    def set_allocations(self, state, machines):
        self._counts = parse_allocations(state["plans"])
        self._assignments = dict(state["assignments"])
        self.registered = len(machines)
        self.setEditText(format_cell_status(self.product_state()))
        self.build_rows()
        self.build_machine_rows()
        self.update_status()

    def build_rows(self):
        self._building = True
        while self.grid.count():
            item = self.grid.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.rows = {}
        self.metric_labels = {}
        self.touched = set()
        for col, title in enumerate(("運営方法", "予定数", "割当済み", "未割当", "超過")):
            self.grid.addWidget(QLabel(title), 0, col)
        names = list(
            dict.fromkeys([*METHODS, *self._counts, *[m for m in self._assignments.values() if m]])
        )
        for row, name in enumerate(names, 1):
            count = self._counts.get(name, 0)
            check = QCheckBox(name if name in METHODS else name + "（自由記載）")
            check.setToolTip(name + (" / 自由記載の運営方法" if name not in METHODS else ""))
            check.setChecked(count is None or count > 0)
            spin = QSpinBox()
            spin.setRange(0, MAX_COUNT)
            spin.setSuffix(" ブース")
            spin.setMinimumWidth(90)
            spin.setValue(count or 0)
            if count is None:
                spin.lineEdit().setPlaceholderText("未設定")
                spin.clear()
            check.toggled.connect(lambda checked, n=name: self.toggle(n, checked))
            spin.lineEdit().textEdited.connect(lambda text, n=name: self.touched.add(n))
            spin.valueChanged.connect(lambda value, n=name: self.change_count(n, value))
            spin.editingFinished.connect(lambda n=name: self.finish_count(n))
            self.grid.addWidget(check, row, 0)
            self.grid.addWidget(spin, row, 1)
            self.rows[name] = check, spin
            self.metric_labels[name] = self.add_metric_labels(row)
        total_row = len(names) + 1
        self.grid.addWidget(QLabel("合計"), total_row, 0)
        self.total_plan = QLabel()
        self.total_plan.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.total_plan.setStyleSheet("font-weight: bold;")
        self.grid.addWidget(self.total_plan, total_row, 1)
        self.total_metrics = self.add_metric_labels(total_row, bold=True)
        self._building = False

    def add_metric_labels(self, row, bold=False):
        result = {}
        for column, field in enumerate(("assigned", "unassigned", "excess"), 2):
            label = QLabel()
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            label.setMinimumWidth(66)
            label.setWordWrap(True)
            background, foreground = METRIC_COLORS[field]
            label.setStyleSheet(
                "QLabel {"
                + f"background-color: {background}; color: {foreground}; padding: 4px; border-radius: 3px;"
                + ("font-weight: bold;" if bold else "")
                + "}"
            )
            self.grid.addWidget(label, row, column)
            result[field] = label
        return result

    def emit_change(self):
        self.setEditText(format_cell_status(self.product_state()))
        self.update_status()
        self.in_change = True
        try:
            self.changed.emit()
        finally:
            self.in_change = False

    def toggle(self, name, checked):
        if self._building:
            return
        value = (self._counts.get(name) or 1) if checked else 0
        self.change_count(name, value)

    def change_count(self, name, value):
        if self._building:
            return
        check, spin = self.rows[name]
        self.touched.add(name)
        self._counts[name] = value
        check.blockSignals(True)
        check.setChecked(value > 0)
        check.blockSignals(False)
        spin.blockSignals(True)
        spin.setValue(value)
        spin.blockSignals(False)
        self.emit_change()

    def finish_count(self, name):
        if self._building or name not in self.rows:
            return
        spin = self.rows[name][1]
        if name not in self.touched:
            if name in self._counts and self._counts[name] is None:
                QTimer.singleShot(0, spin.clear)
            return
        if not spin.lineEdit().text().strip():
            # Only an already selected legacy method can remain unknown.
            if name in self._counts:
                self._counts[name] = None
                self.emit_change()
        else:
            self.change_count(name, spin.value())

    def add_custom(self):
        name = self.custom_name.text().strip()
        if not name:
            return
        if name not in self.rows:
            self._counts[name] = 1
            self.build_rows()
            self.build_machine_rows()
            self.emit_change()
        else:
            self.rows[name][1].setFocus()
        self.custom_name.clear()

    def build_machine_rows(self):
        while self.machine_grid.count():
            item = self.machine_grid.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.assignment_editors = {}
        available = list(
            dict.fromkeys([*METHODS, *self._counts, *[m for m in self._assignments.values() if m]])
        )
        if not self._assignments:
            self.machine_grid.addWidget(
                QLabel("マシン番号を登録すると個別に割り当てられます。"), 0, 0
            )
        for row, (number, method) in enumerate(self._assignments.items()):
            self.machine_grid.addWidget(QLabel(number), row, 0)
            combo = QComboBox()
            combo.addItem("未設定", None)
            for name in available:
                combo.addItem(name if name in METHODS else name + "（自由記載）", name)
            combo.setCurrentIndex(combo.findData(method))
            combo.currentIndexChanged.connect(
                lambda index, n=number, c=combo: self.change_assignment(n, c.currentData())
            )
            self.machine_grid.addWidget(combo, row, 1)
            self.assignment_editors[number] = combo
        self.machine_grid.setRowStretch(len(self._assignments) + 1, 1)

    def change_assignment(self, number, method):
        self._assignments[number] = method
        if method and method not in self._counts:
            self._counts[method] = None
            check, spin = self.rows[method]
            check.blockSignals(True)
            check.setChecked(True)
            check.blockSignals(False)
            spin.clear()
            spin.lineEdit().setPlaceholderText("未設定")
        self.emit_change()

    def update_status(self):
        product = self.product_state()
        status = allocation_status(product)
        self.status.setText(status.message)
        self.status.setStyleSheet(
            "color: #B42318; font-weight: bold;" if status.excess else "color: #334D40;"
        )
        totals = {r.method: r for r in booth_totals(product)}
        for name, (check, spin) in self.rows.items():
            row = totals.get(name)
            for field, label in self.metric_labels[name].items():
                label.setText(booth_value(row, field) if row else "0")
                label.setToolTip(
                    "予定未設定の方法は差分を確定しません。"
                    if row and row.unknown
                    else f"{name}：{METRIC_LABELS[field]} {label.text()}"
                )
        total = total_booths(list(totals.values()))
        self.total_plan.setText(booth_value(total, "planned"))
        for field, label in self.total_metrics.items():
            label.setText(booth_value(total, field))

    def showPopup(self):
        if self.popup.isVisible():
            return
        self.opening_popup = True
        self.build_rows()
        self.build_machine_rows()
        self.update_status()
        self.popup.adjustSize()
        self.popup.resize(max(590, min(740, self.width())), self.popup.height())
        point = self.mapToGlobal(QPoint(0, self.height()))
        screen = self.screen().availableGeometry()
        point.setX(max(screen.left(), min(point.x(), screen.right() - self.popup.width())))
        if point.y() + self.popup.height() > screen.bottom():
            point.setY(
                max(
                    screen.top(),
                    self.mapToGlobal(QPoint(0, 0)).y() - self.popup.height(),
                )
            )
        self.popup.move(point)
        self.popup.show()
        self.popup.setFocus()
        self.opening_popup = False

    def hideEvent(self, event):
        if hasattr(self, "popup"):
            self.popup.hide()
        super().hideEvent(event)

    def hidePopup(self):
        self.popup.hide()
        super().hidePopup()
