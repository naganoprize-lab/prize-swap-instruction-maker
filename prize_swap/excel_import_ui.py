"""Review file kind, sheet, columns and normalized samples before atomic application."""

from copy import deepcopy
from pathlib import Path
from PySide6.QtCore import QDate, Qt
from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QFormLayout,
    QScrollArea,
    QWidget,
    QLabel,
    QComboBox,
    QSpinBox,
    QPushButton,
    QCheckBox,
    QPlainTextEdit,
    QFileDialog,
    QMessageBox,
)
from openpyxl import load_workbook
from openpyxl.utils import get_column_letter
from .excel_profiles import (
    TYPES,
    FIELDS,
    TYPE_FIELDS,
    detect_workbook,
    make_profile,
    recognize_headers,
    validate_profile,
    IGNORED,
    canonical,
)
from .excel_staging import prepare_import
from .core import AppError


class AutoImportDialog(QDialog):
    def __init__(self, owner, path, preferred=None):
        super().__init__(owner)
        self.owner = owner
        self.path = str(path)
        self.prepared = None
        self._building = True
        self.detected = detect_workbook(path, owner.workflow.data["import_profiles"])
        self.setWindowTitle("Excel自動判別・読込前プレビュー")
        a = owner.screen().availableGeometry()
        self.resize(min(1040, a.width() - 30), min(850, a.height() - 50))
        outer = QVBoxLayout(self)
        name = QLabel(Path(path).name + "（元ファイルは変更せず、マクロを実行しません）")
        name.setTextFormat(Qt.TextFormat.PlainText)
        outer.addWidget(name)
        row = QHBoxLayout()
        outer.addLayout(row)
        self.kind = QComboBox()
        for key, title in TYPES.items():
            self.kind.addItem(title, key)
        row.addWidget(self.kind)
        self.sheet = QComboBox()
        self.sheet.addItems(self.detected["sheets"])
        row.addWidget(self.sheet, 1)
        row.addWidget(QLabel("ヘッダー行"))
        self.header = QSpinBox()
        self.header.setRange(1, 1000)
        row.addWidget(self.header)
        self.layout_mode = QComboBox()
        self.layout_mode.addItem("見出しのある表", "table")
        self.layout_mode.addItem("Excelセルの配置", "cells")
        row.addWidget(self.layout_mode)
        self.recognition = QLabel()
        self.recognition.setWordWrap(True)
        outer.addWidget(self.recognition)
        area = QScrollArea()
        area.setWidgetResizable(True)
        body = QWidget()
        self.form = QFormLayout(body)
        area.setWidget(body)
        outer.addWidget(area, 2)
        self.merge_policy = QComboBox()
        self.merge_policy.addItem("手動編集を優先", "manual")
        self.merge_policy.addItem("Excelの最新値を使用", "excel")
        self.merge_policy.addItem("項目ごとに確認", "fields")
        outer.addWidget(self.merge_policy)
        self.preserve = QCheckBox("現在のマシン配置・サイズ・手動追加を保持")
        self.preserve.setChecked(bool(owner.floor.machines))
        outer.addWidget(self.preserve)
        self.report = QPlainTextEdit()
        self.report.setReadOnly(True)
        outer.addWidget(self.report, 1)
        buttons = QHBoxLayout()
        outer.addLayout(buttons)
        preview = QPushButton("プレビューを更新")
        preview.clicked.connect(self.preview)
        buttons.addWidget(preview)
        self.apply_button = QPushButton("確認したデータを反映")
        self.apply_button.clicked.connect(self.accept)
        buttons.addWidget(self.apply_button)
        cancel = QPushButton("キャンセル")
        cancel.clicked.connect(self.reject)
        buttons.addWidget(cancel)
        candidates = self.detected["candidates"]
        first = next(
            (c for c in candidates if c["profile"]["file_type"] == preferred),
            candidates[0] if candidates else None,
        )
        if first:
            self.set_profile(first["profile"])
            self.recognition.setText(first["confidence"] + "。内容を確認して反映してください。")
        else:
            kind = preferred if preferred in TYPES else "sales"
            self.kind.setCurrentIndex(self.kind.findData(kind))
            self.header.setValue(1)
            self.rebuild_mapping()
            self.recognition.setText(
                "自動判別できません。ファイル種別・シート・列を手動で指定してください。"
            )
        self._building = False
        self.kind.currentIndexChanged.connect(self.use_defaults)
        self.sheet.currentIndexChanged.connect(self.use_defaults)
        self.header.valueChanged.connect(self.rebuild_mapping)
        self.layout_mode.currentIndexChanged.connect(self.rebuild_mapping)
        self.preview()

    def headers(self):
        wb = load_workbook(self.path, data_only=True, read_only=True)
        try:
            return list(
                next(
                    wb[self.sheet.currentText()].iter_rows(
                        min_row=self.header.value(),
                        max_row=self.header.value(),
                        max_col=min(1024, wb[self.sheet.currentText()].max_column),
                        values_only=True,
                    ),
                    (),
                )
            )
        finally:
            wb.close()

    def set_profile(self, p):
        self.kind.setCurrentIndex(self.kind.findData(p["file_type"]))
        self.sheet.setCurrentText(p["sheet_name"])
        self.header.setValue(p["header_row"])
        self.layout_mode.setCurrentIndex(self.layout_mode.findData(p["layout_mode"]))
        self.rebuild_mapping(p)

    def use_defaults(self, *_):
        if self._building:
            return
        self._building = True
        p = next(
            (
                c["profile"]
                for c in self.detected["candidates"]
                if c["profile"]["file_type"] == self.kind.currentData()
                and c["profile"]["sheet_name"] == self.sheet.currentText()
            ),
            None,
        )
        if p:
            self.set_profile(p)
        else:
            self.rebuild_mapping()
        self._building = False
        self.invalidate()

    def rebuild_mapping(self, p=None):
        if not isinstance(p, dict):
            p = None
        building = self._building
        self._building = True
        headers = self.headers()
        mapping = recognize_headers(headers)[0] if p is None else p["column_mapping"]
        units = {} if p is None else p["rate_units"]
        while self.form.rowCount():
            self.form.removeRow(0)
        self.columns = {}
        self.units = {}
        kind = self.kind.currentData()
        cells = kind == "layout" and self.layout_mode.currentData() == "cells"
        self.layout_mode.setVisible(kind == "layout")
        self.merge_policy.setVisible(kind == "arrivals")
        self.preserve.setVisible(kind == "layout")
        if cells:
            self.form.addRow(
                QLabel(
                    "セルの相対位置・列幅・行高から既存の配置読込処理で読み取ります。座標を新しく推測しません。"
                )
            )
        else:
            for key in TYPE_FIELDS[kind]:
                widget = QWidget()
                line = QHBoxLayout(widget)
                line.setContentsMargins(0, 0, 0, 0)
                combo = QComboBox()
                combo.addItem("未指定", None)
                for i, label in enumerate(headers):
                    if canonical(label) not in IGNORED:
                        combo.addItem(
                            f'{get_column_letter(i+1)}列：{label if label not in (None,"") else "見出しなし"}',
                            i,
                        )
                combo.setCurrentIndex(max(0, combo.findData(mapping.get(key))))
                line.addWidget(combo, 1)
                combo.currentIndexChanged.connect(self.invalidate)
                self.columns[key] = combo
                if key in ("po_rate", "cost_rate"):
                    unit = QComboBox()
                    unit.addItem("比率 0.05＝5%", "fraction")
                    unit.addItem("百分率 5＝5%", "percent")
                    unit.setCurrentIndex(unit.findData(units.get(key, "fraction")))
                    unit.currentIndexChanged.connect(self.invalidate)
                    line.addWidget(unit)
                    self.units[key] = unit
                self.form.addRow(FIELDS[key], widget)
        self._building = building
        self.invalidate()

    def profile(self):
        headers = self.headers()
        p = make_profile(
            self.kind.currentData(),
            self.sheet.currentText(),
            self.header.value(),
            headers,
            {k: c.currentData() for k, c in self.columns.items() if c.currentData() is not None},
            self.layout_mode.currentData() if self.kind.currentData() == "layout" else "table",
        )
        p["rate_units"] = {
            k: c.currentData() for k, c in self.units.items() if k in p["column_mapping"]
        }
        return validate_profile(p)

    def invalidate(self, *_):
        if self._building:
            return
        self.prepared = None
        self.apply_button.setEnabled(False)
        self.report.setPlainText(
            "対応を変更しました。「プレビューを更新」で読み取る内容を確認してください。"
        )

    def preview(self, *_):
        try:
            f = self.owner.floor
            t = dict(
                absolute=f.analysis_settings["tolerance_absolute"],
                relative=f.analysis_settings["tolerance_relative"],
            )
            p = self.profile()
            self.prepared = prepare_import(self.path, p, f, t)
            r = self.prepared
            errors = len(r.errors) + (
                len(r.data.get("errors", []))
                + sum(i.get("severity") == "エラー" for i in r.data.get("issues", []))
                if p["file_type"] == "sales" and isinstance(r.data, dict)
                else 0
            )
            self.report.setPlainText(
                f'ファイル種別：{TYPES[p["file_type"]]}\n対象シート：{p["sheet_name"]} ／ ヘッダー{p["header_row"]}行目\n認識件数：{r.count}件 ／ エラー：{errors}件 ／ 警告：{len(r.warnings)}件\n認識した列：'
                + "、".join(FIELDS[k] for k in p["column_mapping"])
                + "\n\n読込後の値（先頭3件）\n"
                + "\n".join(r.samples)
                + "\n\n不明な列（使用しません）\n"
                + "\n".join(r.unknown_columns)
                + "\n\n確認事項\n"
                + "\n".join(r.errors + r.warnings[:30])
            )
            self.apply_button.setEnabled(not r.errors)
        except Exception as exc:
            self.prepared = None
            self.apply_button.setEnabled(False)
            self.report.setPlainText(str(exc))

    def accept(self):
        if self.prepared is None:
            self.preview()
        if self.prepared is not None and not self.prepared.errors:
            super().accept()


class ExcelImportMixin:
    def import_excel_auto(self, *_, preferred=None):
        path, _ = QFileDialog.getOpenFileName(
            self, "Excelを自動判別して開く", "", "Excel (*.xlsx *.xlsm)"
        )
        if path:
            self.import_auto_path(path, preferred)

    def import_auto_path(self, path, preferred=None):
        try:
            d = AutoImportDialog(self, path, preferred)
            if d.exec() != QDialog.DialogCode.Accepted:
                return False
            self.apply_prepared_import(
                d.prepared,
                preserve=d.preserve.isChecked(),
                merge_policy=d.merge_policy.currentData(),
            )
            return True
        except Exception as exc:
            self.error(exc)
            return False

    def apply_prepared_import(self, prepared, preserve=True, merge_policy="manual"):
        if prepared is None or prepared.errors:
            raise AppError("読込前のエラーを修正してください。既存データは変更しません。")
        from .instruction_editor import all_state
        from .labels_data import LabelStore

        validate_profile(prepared.profile)
        if self.floor.temporal.active and not self.floor.temporal.to_live():
            return False
        before = all_state(self)
        floor_before = self.floor.state()
        workflow_before = self.workflow.state()
        source_before = self.source
        day_before = self.selected_day()
        label_before = (self.file_label.text(), self.file_label.toolTip())
        try:
            p = prepared.profile
            kind = p["file_type"]
            path = prepared.source
            if kind == "sales":
                summary = self.floor.apply_analysis_snapshot(deepcopy(prepared.data))
                detail = f'読取 {prepared.count}件 ／ 照合 {summary["matched"]}件 ／ 未照合 {summary["unmatched_count"]}件'
            elif kind == "layout":
                machines, warnings, refs = deepcopy(prepared.data)
                self.floor.apply_layout_import(
                    path, machines, warnings, refs, preserve and bool(self.floor.machines)
                )
                detail = f"読取 {prepared.count}枠 ／ 管理 {len(self.floor.machines)}枠"
            elif kind == "references":
                self.floor.number_references = deepcopy(prepared.data)
                self.floor.sync()
                self.floor.record(floor_before, "管理ナンバー参考表読込")
                self.floor.schedule_save()
                self.workflow.record_file("excel", path, "管理ナンバー参考表")
                detail = f"対応 {prepared.count}件"
            else:
                from .instructions import merge_import, prepare_entry, import_values

                choices = {}
                if merge_policy == "fields":
                    _, conflicts = merge_import(
                        deepcopy(self.products), deepcopy(prepared.data), "manual"
                    )
                    for entry, key, value in conflicts:
                        if key != "identity":
                            choices[(entry, key)] = (
                                QMessageBox.question(
                                    self,
                                    "項目ごとの更新確認",
                                    f"{key} をExcelの値「{value}」に更新しますか？",
                                )
                                == QMessageBox.StandardButton.Yes
                            )
                incoming = deepcopy(prepared.data)
                for product in incoming:
                    prepare_entry(product)
                    product.import_snapshot = import_values(product)
                self.merge_instruction_excel(incoming, merge_policy, choices)
                self.source = path
                self.file_label.setText(Path(path).name)
                self.file_label.setToolTip(path)
                self.update_dates()
                if not before[0] and incoming:
                    first = min(p.day for p in incoming)
                    self.day.setDate(QDate(first.year, first.month, first.day))
                self.workflow.record_file("excel", path, "入荷予定表")
                detail = f"読取 {prepared.count}件 ／ 登録合計 {len(self.products)}件"
            from .workflow_state import now

            saved = deepcopy(p)
            saved["last_used_at"] = now()
            profiles = self.workflow.data["import_profiles"]
            self.workflow.data["import_profiles"] = [
                r for r in profiles if r.get("profile_id") != saved["profile_id"]
            ] + [saved]
            self.workflow.changed.emit()
            self.refresh()
            self.mark_dirty()
            self.workflow.capture_assessments()
            self.notice.setText(
                f"{TYPES[kind]}：{detail}を反映。列対応を保存しました。元Excelは変更していません。"
            )
        except Exception:
            self._loading_instructions = True
            try:
                self.products = before[0]
                self.labels.store = LabelStore.restore(before[1], self.products)
                self.source = source_before
                self.file_label.setText(label_before[0])
                self.file_label.setToolTip(label_before[1])
                self.floor.restore(floor_before, keep_history=True)
                self.workflow.restore(workflow_before)
                self.day.setDate(QDate(day_before.year, day_before.month, day_before.day))
                self.refresh()
            finally:
                self._loading_instructions = False
            raise
