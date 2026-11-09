"""One document selection dialog, then the common embedded check and output renderer."""

from copy import deepcopy
from PySide6.QtWidgets import QDialog, QVBoxLayout, QLabel, QCheckBox, QHBoxLayout, QPushButton
from PySide6.QtPrintSupport import QPrintDialog, QAbstractPrintDialog
from .printing import configured_printer, save_rendered_pdf
from .instruction_output import validate_job, render_output, output_plan


class WorkSetDialog(QDialog):
    def __init__(self, owner):
        super().__init__(owner)
        self.setWindowTitle("作業指示セット印刷")
        self.output_action = None
        outer = QVBoxLayout(self)
        outer.addWidget(
            QLabel(
                f"対象日：{owner.selected_day():%Y/%m/%d} ／ 出力対象：{sum(p.included for p in owner.visible)}指示"
            )
        )
        outer.addWidget(QLabel("指示書 → 入替予定地図 → 作業チェックリストの順で出力します。"))
        self.checks = {}
        for key, title in [
            ("include_instruction", "入替指示書（既存レイアウト）"),
            ("include_map", "入替予定地図"),
            ("include_checklist", "作業チェックリスト（A4縦）"),
        ]:
            c = QCheckBox(title)
            c.setChecked(owner.workflow.data["print_job"][key])
            self.checks[key] = c
            outer.addWidget(c)
        self.error = QLabel()
        outer.addWidget(self.error)
        row = QHBoxLayout()
        outer.addLayout(row)
        for title, action in [
            ("印刷プレビュー", "preview"),
            ("PDF保存", "pdf"),
            ("直接印刷", "direct"),
        ]:
            b = QPushButton(title)
            b.clicked.connect(lambda checked=False, a=action: self.choose(a))
            row.addWidget(b)
        cancel = QPushButton("キャンセル")
        cancel.clicked.connect(self.reject)
        row.addWidget(cancel)

    def config(self):
        return {k: c.isChecked() for k, c in self.checks.items()}

    def choose(self, action):
        try:
            validate_job(self.config())
            self.output_action = action
            self.accept()
        except ValueError as exc:
            self.error.setText(str(exc))


class WorkSetMixin:
    def show_work_set(self, *_):
        d = WorkSetDialog(self)
        if d.exec() != QDialog.DialogCode.Accepted:
            return
        config = d.config()
        self.workflow.data["print_job"] = config
        self.workflow.changed.emit()
        {
            "preview": self.preview_work_set,
            "pdf": self.export_work_set,
            "direct": self.print_work_set,
        }[d.output_action](config)

    def render_work_set(self, printer, config):
        return render_output(
            printer,
            self.visible,
            self.floor.physical_machines,
            self.floor.number_references,
            self.selected_day(),
            self.page_rows.value(),
            self.layout_name(),
            self.print_mode.currentData(),
            self.floor.instruction_print_options,
            job_config=validate_job(config),
        )

    def preview_work_set(self, config):
        try:
            self.print_products(lambda: self.preview_work_set(config))
            from .instruction_preview import InstructionPreview

            InstructionPreview(self, job_config=config).exec()
        except Exception as exc:
            self.error(exc)

    def export_work_set(self, config):
        try:
            self.print_products(lambda: self.export_work_set(config))
            path = self.save_path("作業指示セットPDF", "PDF (*.pdf)", ".pdf")
            if path:
                plan = save_rendered_pdf(
                    path,
                    lambda temp: self.render_work_set(configured_printer(temp), config),
                    [self.source, self.floor.source],
                )
                self.workflow.record_file("output", path, "作業指示セットPDF")
                self.statusBar().showMessage(plan.reason, 15000)
        except Exception as exc:
            self.error(exc)

    def print_work_set(self, config):
        try:
            self.print_products(lambda: self.print_work_set(config))
            printer = configured_printer()
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
                job_config=validate_job(config),
            )
            d = QPrintDialog(printer, self)
            d.setMinMax(1, len(plan.pages))
            d.setOption(QAbstractPrintDialog.PrintDialogOption.PrintPageRange, True)
            if d.exec() == QDialog.DialogCode.Accepted:
                self.render_work_set(printer, config)
                self.workflow.record_file("output", "プリンターへ送信", "作業指示セット")
                self.statusBar().showMessage("作業指示セットをプリンターへ送信しました。", 10000)
        except Exception as exc:
            self.error(exc)
