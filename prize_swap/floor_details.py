"""Explicit current-machine and planned-product editors; no guessed associations."""

from copy import deepcopy
from dataclasses import asdict
from PySide6.QtCore import Qt, Signal, QSignalBlocker
from PySide6.QtGui import QUndoCommand
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QDoubleSpinBox,
    QSpinBox,
    QCheckBox,
    QLabel,
    QMessageBox,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)
from .core import AppError, LIMITS, METHODS, quantity
from .floor_data import validate_machines
from .machine_matching import (
    display_identity,
    machine_key,
    MATCH_LABELS,
    value_source,
    metric_info,
)
from .machine_matching import machine_updated_at
from .floor_status import (
    money,
    ratio_text,
    days_text,
    effective_stat,
    reference_average,
)
from .methods import editor_state, set_allocations, format_counts, plans
from .methods_editor import MethodsCombo


def missing(value):
    return "—" if value is None or value == "" else str(value)


class DetailCommand(QUndoCommand):
    """Undo current and planned edits together, preserving later table edits."""

    def __init__(self, owner, before, after, changes):
        super().__init__("マシン詳細の編集")
        self.owner = owner
        self.before = before
        self.after = after
        self.changes = changes
        self.first = True

    def apply(self, forward):
        self.owner.restore(self.after if forward else self.before, keep_history=True)
        conflicts = False
        for source, values in self.changes:
            if not any(p is source for p in self.owner.products()):
                continue
            for key, (old, new) in values.items():
                expected, value = (old, new) if forward else (new, old)
                if getattr(source, key) == expected:
                    setattr(source, key, deepcopy(value))
                else:
                    conflicts = True
        self.owner.product_edited.emit()
        self.owner.sync()
        self.owner.schedule_save()
        if conflicts:
            self.owner.message.emit(
                "商品一覧で後から編集した項目は保持しました。詳細の変更と競合する項目は元に戻していません。"
            )

    def undo(self):
        self.apply(False)

    def redo(self):
        if self.first:
            self.first = False
        else:
            self.apply(True)


class ProductPanel(QWidget):
    def __init__(self, product, parent=None):
        super().__init__(parent)
        self.source = product
        self.draft = deepcopy(product)
        form = QFormLayout(self)
        form.addRow(QLabel("選択日の入替予定商品です。現在投入中の商品とは別に保存します。"))
        self.name = QPlainTextEdit(product.name)
        self.name.setMaximumHeight(100)
        form.addRow("商品名全文", self.name)
        self.maker = QLineEdit(product.maker)
        self.maker.setPlaceholderText("—")
        form.addRow("メーカー", self.maker)
        self.units = QLineEdit(str(product.units))
        self.units.setPlaceholderText("—")
        form.addRow("入数", self.units)
        self.cases = QLineEdit(str(product.cases))
        self.cases.setPlaceholderText("—")
        form.addRow("CS数", self.cases)
        self.limit = QComboBox()
        self.limit.addItems(LIMITS)
        self.limit.setCurrentText(product.limit)
        form.addRow("獲得個数制限", self.limit)
        form.addRow("割当マシン", QLabel("、".join(product.machines) or "—"))
        self.methods = MethodsCombo()
        self.methods.set_allocations(editor_state(product), product.machines)
        form.addRow("運営方法・予定ブース数", self.methods)
        self.summary = QLabel(format_counts(plans(product)) or "—")
        self.summary.setWordWrap(True)
        form.addRow("予定数", self.summary)
        self.methods.changed.connect(
            lambda: self.summary.setText(format_counts(self.methods.counts()) or "—")
        )

    def validate(self):
        self.draft.name = self.name.toPlainText().strip()
        self.draft.maker = self.maker.text().strip()
        self.draft.units = quantity(self.units.text())
        self.draft.cases = quantity(self.cases.text())
        self.draft.limit = self.limit.currentText()
        set_allocations(self.draft, self.methods.state())

    def apply(self):
        for field in (
            "name",
            "maker",
            "units",
            "cases",
            "limit",
            "methods",
            "planned_booths",
            "machine_methods",
            "retired_machine_methods",
        ):
            setattr(self.source, field, deepcopy(getattr(self.draft, field)))
        if self.source.candidate_assignment:
            self.source.candidate_assignment["replacement_product_name"] = self.source.name


class MachineDetailPanel(QWidget):
    accepted = Signal()
    rejected = Signal()

    def __init__(self, owner, machine):
        super().__init__(owner)
        self.owner = owner
        self.original = machine
        self.updated = deepcopy(machine)
        self.baseline = deepcopy(machine)
        self.setWindowTitle(f"マシン詳細 — {display_identity(machine)}")
        available = self.screen().availableGeometry()
        size = owner.analytics_ui["detail_size"]
        self.resize(
            min(size[0], max(360, available.width() - 30)),
            min(size[1], max(300, available.height() - 50)),
        )
        outer = QVBoxLayout(self)
        self.shared_selection = QCheckBox("このマシンを選択（売上分析の選択対象と共通）")
        self.shared_selection.setChecked(machine.uid in owner.selection_state.selected_machine_keys)
        self.shared_selection.toggled.connect(self.toggle_shared_selection)
        owner.selection_state.changed.connect(self.refresh_shared_selection)
        outer.addWidget(self.shared_selection)
        tabs = QTabWidget()
        self.tabs = tabs
        outer.addWidget(tabs, 1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        panel = QWidget()
        form = QFormLayout(panel)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        scroll.setWidget(panel)
        tabs.addTab(scroll, "現在投入中の商品")
        self.values = {}
        for key, label, value in (
            ("number", "マシン番号", display_identity(machine)),
            ("source_key", "管理ナンバー", machine.source_key),
            (
                "direct_number_raw",
                "機械番号（レイアウト原文）",
                machine.direct_number_raw or machine.number,
            ),
            ("resolved_number", "照合先の機械番号", machine.resolved_number),
            ("match_method", "照合方法", MATCH_LABELS[machine.match_method]),
            ("match_message", "照合の確認事項", machine.match_message),
            (
                "provenance",
                "実績データの取得元",
                (
                    "景品運営分析表"
                    if machine.analysis
                    else (
                        "手動入力・レイアウト情報" if machine.manual_updates else "レイアウト由来"
                    )
                ),
            ),
            ("ratio_source", "アベ比の取得元", value_source(machine, "ratio")),
            (
                "updated_at",
                "データ更新日時",
                machine_updated_at(machine),
            ),
            ("sales", "売上金額", money(effective_stat(machine, "sales"))),
            ("average", "平均売上", money(effective_stat(machine, "average"))),
            ("ratio", "アベ比", ratio_text(machine)),
            (
                "po_rate",
                "PO率",
                (
                    "—"
                    if effective_stat(machine, "po_rate") is None
                    else f"{effective_stat(machine,'po_rate') * 100:.2f}%"
                ),
            ),
            ("installed_on", "設置日", machine.installed_on),
            ("reference", "基準日", owner.reference_day().isoformat()),
            ("days", "投入日数", days_text(machine, owner.reference_day())),
        ):
            widget = QLabel(missing(value))
            widget.setTextInteractionFlags(
                widget.textInteractionFlags() | Qt.TextInteractionFlag.TextSelectableByMouse
            )
            self.values[key] = widget
            widget.setWordWrap(True)
            if key in ("sales", "average", "ratio", "po_rate"):
                widget.setToolTip(
                    value_source(machine, key)
                    + " / 更新（読込） "
                    + (metric_info(machine, key)["updated_at"] or "不明")
                )
            if key != "average":
                form.addRow(label, widget)
            else:
                widget.setParent(self)
                widget.hide()
        for key, label in (
            ("operating_days", "稼働日数（売上集計期間）"),
            ("gross_profit", "粗利益"),
            ("cost_rate", "原価率"),
            ("dispensed", "払出数"),
            ("per_item", "個当たり"),
            ("unit_price", "景品単価"),
            ("cost", "原価"),
        ):
            value = effective_stat(machine, key)
            text = (
                "—"
                if value is None
                else f"{value * 100:.2f}%" if key == "cost_rate" else money(value)
            )
            widget = QLabel(text)
            self.values[key] = widget
            form.addRow(label, widget)
        form.addRow("基準アベレージ（維持）", QLabel(money(reference_average(machine))))
        source = machine.analysis
        label = QLabel(
            (
                f"売上分析優先：{source['file_name']} ／ 読込（JST） {source['loaded_at']}"
                if source
                else "レイアウト由来の実績値（未取得は —）"
            )
        )
        label.setWordWrap(True)
        form.addRow(label)
        if source:
            from .performance_validation import period_text

            form.addRow("実績対象期間", QLabel(period_text(source)))
            form.addRow("機器（分析表）", QLabel(source.get("machine_name") or "—"))
            if source.get("quality_issues"):
                quality = QLabel("\n".join(i["message"] for i in source["quality_issues"]))
                quality.setWordWrap(True)
                form.addRow("実績の要確認事項", quality)
        from .po_evaluation import evaluate_po

        po = evaluate_po(machine, owner.analysis_settings)
        from .po_evaluation import PO_DEFINITIONS, po_definition

        self.po_definition = QComboBox()
        for key, title in PO_DEFINITIONS.items():
            self.po_definition.addItem(title, key)
        self.po_definition.setCurrentIndex(self.po_definition.findData(po_definition(machine)))
        form.addRow("このマシンのP/O率の定義", self.po_definition)
        self.po_summary = QLabel()
        self.po_summary.setWordWrap(True)
        form.addRow("適正PO率との比較", self.po_summary)
        self.po_origin = QLabel()
        self.po_origin.setWordWrap(True)
        form.addRow("原価・料金の取得元", self.po_origin)
        self.confirmed_cost = QDoubleSpinBox()
        self.confirmed_cost.setRange(0, 10000000)
        self.confirmed_cost.setSpecialValueText("未設定")
        from .costing import cost_profile

        profile = cost_profile(machine)
        self._cost_was_edited = profile["confirmed"] is not None
        self.confirmed_cost.setValue(
            profile["confirmed"] or (profile["candidate"] if profile["candidate_safe"] else 0) or 0
        )
        self.cost_values = QLabel()
        self.cost_values.setWordWrap(True)
        form.addRow("使用原価・元の実績", self.cost_values)
        form.addRow("確認済み景品原価（円）", self.confirmed_cost)
        self.cost_candidate_button = QPushButton("商品名の原価候補を確認して設定")
        form.addRow(self.cost_candidate_button)

        def confirm_candidate():
            from .po_evaluation import cost_candidate

            candidate = cost_candidate(self.name.toPlainText())
            if candidate is None:
                QMessageBox.information(self, "原価候補", "末尾に独立した金額候補はありません。")
            elif (
                QMessageBox.question(
                    self,
                    "景品原価の確認",
                    f"{candidate:,.0f}円は型番や数量ではなく、景品1個の原価ですか？",
                )
                == QMessageBox.StandardButton.Yes
            ):
                self._cost_was_edited = True
                self.confirmed_cost.setValue(candidate)
                self.updated.prize_cost_origin = "product_suffix"
                self.refresh_po_inputs()

        self.cost_candidate_button.clicked.connect(confirm_candidate)
        self.play_amount = QDoubleSpinBox()
        self.play_amount.setRange(0, 1000000)
        self.play_amount.setSpecialValueText("アプリ標準（仮設定）")
        self.play_amount.setValue((machine.play_pricing or {}).get("amount", 0))
        form.addRow("マシン料金（円／下記回数）", self.play_amount)
        self.play_count = QSpinBox()
        self.play_count.setRange(1, 10000)
        self.play_count.setValue((machine.play_pricing or {}).get("plays", 1))
        form.addRow("上記料金でのプレイ回数", self.play_count)
        self.price_confirmed = QCheckBox("料金条件を確認済み")
        self.price_confirmed.setChecked((machine.play_pricing or {}).get("confirmed", False))
        form.addRow(self.price_confirmed)
        self.name = QPlainTextEdit(machine.product)
        self.name.setMaximumHeight(130)
        self.name.setPlaceholderText("—")
        form.addRow("商品名全文", self.name)
        self.maker = QLineEdit(machine.maker)
        self.maker.setPlaceholderText("—")
        form.addRow("メーカー", self.maker)
        self.units = QLineEdit(str(machine.units))
        self.units.setPlaceholderText("—")
        form.addRow("入数", self.units)
        self.cases = QLineEdit(str(machine.cases))
        self.cases.setPlaceholderText("—")
        form.addRow("CS数", self.cases)
        self.limit = QComboBox()
        self.limit.setEditable(True)
        self.limit.addItem("")
        self.limit.addItems(LIMITS)
        self.limit.setCurrentText(machine.limit)
        self.limit.lineEdit().setPlaceholderText("—")
        form.addRow("獲得個数制限", self.limit)
        self.method = QComboBox()
        self.method.setEditable(True)
        self.method.addItem("")
        self.method.addItems(METHODS)
        self.method.setCurrentText(machine.operation_method)
        self.method.lineEdit().setPlaceholderText("— / 自由記載")
        form.addRow("現在の運営方法", self.method)
        form.addRow(
            "現在商品の予定ブース数",
            QLabel("—（Excelから未取得。入替予定は別タブで編集）"),
        )
        edit = QPushButton("番号・稼働値を編集")
        form.addRow(edit)
        edit.clicked.connect(self.edit_metrics)
        note = QLabel(
            "売上等は現在の読込値です。取得できない項目は — と表示し、商品名の一致だけで入替予定と結び付けません。"
        )
        note.setWordWrap(True)
        form.addRow(note)
        self.products = []
        for product in owner.products():
            if (
                product.day == owner.selected_day()
                and machine_key(machine)
                and machine_key(machine) in product.machines
            ):
                product_panel = ProductPanel(product)
                self.products.append(product_panel)
                area = QScrollArea()
                area.setWidgetResizable(True)
                area.setWidget(product_panel)
                tabs.addTab(area, f"入替予定：{product.name[:18]}")
        self.error = QLabel()
        outer.addWidget(self.error)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("保存")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("キャンセル")
        outer.addWidget(buttons)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        from .detail_widgets import build_overview

        build_overview(self, tabs, scroll, panel)
        self.confirmed_cost.valueChanged.connect(self.cost_edited)
        for control in (self.confirmed_cost, self.play_amount, self.play_count):
            control.valueChanged.connect(self.refresh_po_inputs)
        self.price_confirmed.toggled.connect(self.refresh_po_inputs)
        self.po_definition.currentIndexChanged.connect(self.change_po_definition)
        self.confirmed_cost.valueChanged.connect(
            lambda *_: setattr(self.updated, "prize_cost_origin", "manual")
        )
        self.name.textChanged.connect(self.po_product_changed)
        self.refresh_po_inputs()
        self.update_responsive_layout()

    def toggle_shared_selection(self, checked):
        keys = set(self.owner.selection_state.selected_machine_keys)
        if checked:
            keys.add(self.original.uid)
        else:
            keys.discard(self.original.uid)
        self.owner.selection_state.set(keys)

    def refresh_shared_selection(self):
        self.shared_selection.blockSignals(True)
        self.shared_selection.setChecked(
            self.original.uid in self.owner.selection_state.selected_machine_keys
        )
        self.shared_selection.blockSignals(False)

    def refresh_overview(self, *_):
        from .detail_widgets import refresh_overview

        refresh_overview(self)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.update_responsive_layout()

    def update_responsive_layout(self):
        if hasattr(self, "splitter"):
            columns = 1 if self.width() < 500 else 2 if self.width() <= 800 else 3
            if columns != getattr(self, "_kpi_columns", None):
                for i, card in enumerate(self.kpis.values()):
                    self.kpi_grid.removeWidget(card)
                    self.kpi_grid.addWidget(card, i // columns, i % columns)
                self._kpi_columns = columns
            orientation = (
                Qt.Orientation.Vertical if self.width() <= 800 else Qt.Orientation.Horizontal
            )
            reflow = orientation != self.splitter.orientation() or not getattr(
                self, "_split_initialized", False
            )
            self.splitter.setOrientation(orientation)
            # On narrow docks show the chart after the KPIs, before the long
            # editing form. On wider docks keep the form on the left.
            self.splitter.insertWidget(0 if self.width() <= 800 else 1, self.evaluation_panel)
            # A single outer scroll area owns the full content height. Never
            # squeeze the radar and form into separately scrolling viewports.
            content_height = sum(
                self.splitter.widget(i).minimumSizeHint().height()
                for i in range(self.splitter.count())
            )
            self.splitter.setMinimumHeight(
                content_height
                if self.width() <= 800
                else max(
                    self.splitter.widget(i).minimumSizeHint().height()
                    for i in range(self.splitter.count())
                )
            )
            if reflow:
                self.splitter.setSizes([900, 700] if self.width() <= 800 else [560, 440])
                self._split_initialized = True
            self.analysis_scope.setMinimumContentsLength(3)
            self.analysis_scope.setSizeAdjustPolicy(
                QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
            )
            self.analysis_scope.setMaximumWidth(110 if self.width() < 700 else 220)
            self.analysis_button.setText("分析へ" if self.width() < 700 else "売上分析で表示")

    def reject(self):
        self.rejected.emit()

    def is_dirty(self):
        m = self.baseline
        if (
            self.name.toPlainText().strip() != m.product
            or self.maker.text().strip() != m.maker
            or self.units.text() != str(m.units)
            or self.cases.text() != str(m.cases)
            or self.limit.currentText() != m.limit
            or self.method.currentText() != m.operation_method
        ):
            return True
        if self._cost_was_edited and self.confirmed_cost.value() != (m.prize_cost or 0):
            return True
        pricing = m.play_pricing or {}
        if (
            self.play_amount.value() != pricing.get("amount", 0)
            or self.play_count.value() != pricing.get("plays", 1)
            or self.price_confirmed.isChecked() != pricing.get("confirmed", False)
        ):
            return True
        if asdict(self.updated) != asdict(m):
            return True
        for panel in self.products:
            try:
                panel.validate()
            except AppError:
                return True
            if any(
                getattr(panel.source, k) != getattr(panel.draft, k)
                for k in ("name", "maker", "units", "cases", "limit")
            ) or editor_state(panel.source) != editor_state(panel.draft):
                return True
        return False

    def load_machine(self, machine):
        self.original = machine
        self.baseline = deepcopy(machine)
        self.updated = deepcopy(machine)
        controls = [
            self.shared_selection,
            self.name,
            self.maker,
            self.units,
            self.cases,
            self.limit,
            self.method,
            self.confirmed_cost,
            self.play_amount,
            self.play_count,
            self.price_confirmed,
            self.po_definition,
        ]
        blockers = [QSignalBlocker(c) for c in controls]
        self.shared_selection.setChecked(
            machine.uid in self.owner.selection_state.selected_machine_keys
        )
        self.name.setPlainText(machine.product)
        self.maker.setText(machine.maker)
        self.units.setText(str(machine.units))
        self.cases.setText(str(machine.cases))
        self.limit.setCurrentText(machine.limit)
        self.method.setCurrentText(machine.operation_method)
        from .costing import cost_profile
        from .po_evaluation import po_definition

        c = cost_profile(machine)
        self._cost_was_edited = c["confirmed"] is not None
        self.confirmed_cost.setValue(
            c["confirmed"] or (c["candidate"] if c["candidate_safe"] else 0) or 0
        )
        pricing = machine.play_pricing or {}
        self.play_amount.setValue(pricing.get("amount", 0))
        self.play_count.setValue(pricing.get("plays", 1))
        self.price_confirmed.setChecked(pricing.get("confirmed", False))
        self.po_definition.setCurrentIndex(self.po_definition.findData(po_definition(machine)))
        del blockers
        from .analytics_data import NUMERIC, formatted

        for key in self.values:
            if key in NUMERIC:
                self.values[key].setText(formatted(key, effective_stat(machine, key)))
        for key, value in dict(
            number=display_identity(machine),
            source_key=machine.source_key,
            direct_number_raw=machine.direct_number_raw or machine.number,
            resolved_number=machine.resolved_number,
            match_method=MATCH_LABELS[machine.match_method],
            match_message=machine.match_message,
            provenance="景品運営分析表" if machine.analysis else "レイアウト由来",
            ratio_source=value_source(machine, "ratio"),
            updated_at=machine_updated_at(machine),
            ratio=ratio_text(machine),
            installed_on=machine.installed_on,
            reference=self.owner.reference_day().isoformat(),
            days=days_text(machine, self.owner.reference_day()),
        ).items():
            self.values[key].setText(missing(value))
        while self.tabs.count() > 1:
            widget = self.tabs.widget(1)
            self.tabs.removeTab(1)
            widget.deleteLater()
        self.products = []
        from .instructions import booths

        related = {
            b.product.entry_id
            for b in booths(
                [p for p in self.owner.products() if p.day == self.owner.selected_day()],
                self.owner.machines,
                self.owner.number_references,
                all_entries=True,
            )
            if b.machine and b.machine.uid == machine.uid
        }
        for p in self.owner.products():
            if p.entry_id in related:
                panel = ProductPanel(p)
                self.products.append(panel)
                area = QScrollArea()
                area.setWidgetResizable(True)
                area.setWidget(panel)
                self.tabs.addTab(area, "入替予定：" + (p.name[:18] or "商品未定"))
        self.identity_label.setText(display_identity(machine))
        self.header_product.setText(machine.product or "—")
        self.header_product.setToolTip(machine.product)
        self.error.clear()
        self.refresh_po_inputs()
        self.update_responsive_layout()

    def edit_metrics(self):
        draft = deepcopy(self.updated)
        if self.owner.machine_dialog(draft, "番号・稼働値を編集"):
            from .machine_matching import prepare_references, mark_manual_updates
            from .sales_analysis import apply_snapshot
            from .analytics_data import NUMERIC, formatted

            mark_manual_updates(draft, self.updated)
            if draft.number != self.updated.number:
                draft.resolved_number = ""
                draft.manual_number = None
                draft.number_origin = "direct"
                draft.direct_number_raw = draft.number
            drafts = [deepcopy(m) if m.uid != draft.uid else draft for m in self.owner.machines]
            if self.owner.number_references:
                prepare_references(drafts, self.owner.number_references)
            apply_snapshot(drafts, self.owner.analysis_state)
            self.updated = draft
            self.name.setPlainText(draft.product)
            for key, value in (
                ("number", display_identity(draft)),
                ("resolved_number", draft.resolved_number),
                ("match_method", MATCH_LABELS[draft.match_method]),
                ("match_message", draft.match_message),
                ("ratio", ratio_text(draft)),
                ("ratio_source", value_source(draft, "ratio")),
                ("updated_at", machine_updated_at(draft)),
                (
                    "provenance",
                    (
                        "景品運営分析表"
                        if draft.analysis
                        else (
                            "手動入力・レイアウト情報" if draft.manual_updates else "レイアウト由来"
                        )
                    ),
                ),
                ("installed_on", draft.installed_on),
                ("days", days_text(draft, self.owner.reference_day())),
            ):
                self.values[key].setText(missing(value))
            for key in NUMERIC:
                if key not in ("ratio", "days") and key in self.values:
                    self.values[key].setText(formatted(key, effective_stat(draft, key)))
                    info = metric_info(draft, key)
                    self.values[key].setToolTip(
                        info["source"] + " / " + (info["updated_at"] or "不明")
                    )
            self.refresh_overview()

    def po_product_changed(self):
        self.updated.product = self.name.toPlainText().strip()
        self.confirmed_cost.blockSignals(True)
        self.confirmed_cost.setValue(0)
        self.confirmed_cost.blockSignals(False)
        self.refresh_po_inputs()

    def change_po_definition(self, *_):
        self.updated.po_definition = self.po_definition.currentData()
        self.updated.po_definition_fingerprint = (self.updated.analysis or {}).get(
            "fingerprint", ""
        )
        self.refresh_po_inputs()

    def cost_edited(self, *_):
        self._cost_was_edited = True
        self.updated.prize_cost_origin = "manual"

    def refresh_po_inputs(self, *_):
        self.updated.prize_cost = (
            (self.confirmed_cost.value() or None) if self._cost_was_edited else None
        )
        self.updated.prize_cost_product = (
            self.name.toPlainText().strip() if self.updated.prize_cost is not None else ""
        )
        self.updated.play_pricing = (
            dict(
                amount=self.play_amount.value(),
                plays=self.play_count.value(),
                confirmed=self.price_confirmed.isChecked(),
            )
            if self.play_amount.value()
            else None
        )
        from .po_evaluation import evaluate_po

        po = evaluate_po(self.updated, self.owner.analysis_settings)
        from .analytics_data import formatted

        self.values["po_rate"].setText(formatted("po_rate", po["actual"]))
        self.po_summary.setText(
            "実際PO率："
            + formatted("po_rate", po["actual"])
            + " ／ 適正PO率："
            + formatted("po_rate", po["target"])
            + (
                f" ／ 差 {po['deviation_points']:+.2f}pt"
                if po["deviation_points"] is not None
                else " ／ 差 —"
            )
            + f"\n目標原価率：{self.owner.analysis_settings['cost_target']:.1%} ／ Excel P/O原値："
            + formatted("po_rate", po["raw_actual"])
        )
        c = po["cost_details"]

        def currency(value):
            return "—" if value is None else f"{value:,.0f}円"

        self.cost_values.setText(
            f"使用原価：{currency(c['effective'])} ／ 取得元：{c['source']}\n末尾候補：{currency(c['candidate'])} ／ 正式単価：{currency(c['official'])} ／ 確認済み：{currency(c['confirmed'])}\n景品原価総額：{currency(c['calculated_cost'])} ／ 再計算原価率：{formatted('cost_rate',c['calculated_cost_rate']) if c['calculated_cost_rate'] is not None else '算出不可（'+c['reason']+'）'} ／ Excel原価率：{formatted('cost_rate',c['excel_cost_rate'])} ／ Excel原価総額：{currency(c['excel_cost'])}"
        )
        self.values["cost_rate"].setText(
            formatted("cost_rate", effective_stat(self.updated, "cost_rate"))
            if effective_stat(self.updated, "cost_rate") is not None
            else "算出不可"
        )
        self.values["cost"].setText(
            currency(effective_stat(self.updated, "cost"))
            if effective_stat(self.updated, "cost") is not None
            else "算出不可"
        )
        self.values["cost"].setToolTip(
            "使用原価による再計算値。Excelの元の原価は上の取得元欄で確認できます。"
            if c["overridden"]
            else "Excel・レイアウトの元の実績値"
        )
        self.po_origin.setText(
            po["cost_source"]
            + " / "
            + po["price_source"]
            + "\n"
            + " / ".join(po["warnings"])
            + "\n"
            + po["explanation"]
        )
        self.refresh_overview()

    def accept(self):
        try:
            if not self._cost_was_edited and self.confirmed_cost.value() > 0:
                self.updated.prize_cost_origin = "product_suffix"
                self._cost_was_edited = True
            self.refresh_po_inputs()
            if self.updated.number != self.original.number:
                self.updated.resolved_number = ""
                self.updated.manual_number = None
                self.updated.number_origin = "direct"
                self.updated.direct_number_raw = self.updated.number
            self.updated.product = self.name.toPlainText().strip()
            self.updated.maker = self.maker.text().strip()
            self.updated.units = quantity(self.units.text())
            self.updated.cases = quantity(self.cases.text())
            self.updated.limit = self.limit.currentText().strip()
            self.updated.operation_method = self.method.currentText().strip()
            from .machine_matching import mark_manual_updates

            mark_manual_updates(self.updated, self.original)
            validate_machines(
                [
                    asdict(self.updated) if m.uid == self.updated.uid else asdict(m)
                    for m in self.owner.machines
                ]
            )
            for panel in self.products:
                panel.validate()
            if machine_key(self.updated) != machine_key(self.original) and self.owner.related(
                {machine_key(self.original)}
            ):
                QMessageBox.warning(
                    self,
                    "入替予定との整合性",
                    "変更前のマシン番号が入替予定で使われています。予定の番号は自動変更しません。商品一覧で変更先を確認・修正してください。",
                )
            self.accepted.emit()
        except AppError as exc:
            self.error.setText(str(exc))

    def apply_products(self):
        for panel in self.products:
            panel.apply()


class MachineDetailDialog(QDialog):
    """Compatibility wrapper; the operational floor uses the same panel in a real dock."""

    def __init__(self, owner, machine):
        super().__init__(owner)
        self.panel = MachineDetailPanel(owner, machine)
        self.setWindowTitle(self.panel.windowTitle())
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.panel)
        self.resize(self.panel.size())
        self.panel.accepted.connect(lambda: self.done(QDialog.DialogCode.Accepted))
        self.panel.rejected.connect(lambda: self.done(QDialog.DialogCode.Rejected))

    def __getattr__(self, name):
        panel = self.__dict__.get("panel")
        if panel is not None:
            return getattr(panel, name)
        raise AttributeError(name)

    def accept(self):
        self.panel.accept()

    def reject(self):
        self.panel.reject()

    def done(self, result):
        self.panel.owner.analytics_ui["detail_size"] = [
            max(300, self.width()),
            max(300, self.height()),
        ]
        self.panel.owner.schedule_save()
        super().done(result)
