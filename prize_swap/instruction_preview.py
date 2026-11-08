"""Preview actual rendered PDF pages when an output mixes A4 instructions and A3 maps."""

from pathlib import Path
from tempfile import TemporaryDirectory
from PySide6.QtCore import Qt
from PySide6.QtPdf import QPdfDocument
from PySide6.QtPdfWidgets import QPdfView
from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QGridLayout,
    QComboBox,
    QPushButton,
    QLabel,
    QDialogButtonBox,
)
from .printing import configured_printer, save_rendered_pdf


class InstructionPreview(QDialog):
    def __init__(self, owner, map_page=None):
        super().__init__(owner)
        self.owner = owner
        self.map_page = map_page
        self.runtime = TemporaryDirectory(prefix="prize-instruction-preview-")
        self.path = Path(self.runtime.name) / "preview.pdf"
        self.setWindowTitle("指示書・入替予定地図の印刷プレビュー")
        available = owner.screen().availableGeometry()
        self.resize(min(1250, available.width() - 30), min(850, available.height() - 50))
        outer = QVBoxLayout(self)
        tools = QGridLayout()
        outer.addLayout(tools)
        self.scope = QComboBox()
        for title, key in [
            ("指示書のみ", "instructions"),
            ("地図のみ", "map"),
            ("指示書＋地図", "both"),
        ]:
            self.scope.addItem(title, key)
        self.scope.setCurrentIndex(
            self.scope.findData(owner.floor.instruction_print_options["mode"])
        )
        if map_page:
            self.scope.setCurrentIndex(self.scope.findData("map"))
            self.scope.setEnabled(False)
        tools.addWidget(self.scope, 0, 0)
        self.paper = QComboBox()
        self.paper.addItems(["A4", "A3"])
        self.paper.setCurrentText(owner.floor.instruction_print_options["paper"])
        tools.addWidget(self.paper, 0, 1)
        self.direction = QComboBox()
        for title, key in [("自動方向", "auto"), ("縦向き", "portrait"), ("横向き", "landscape")]:
            self.direction.addItem(title, key)
        self.direction.setCurrentIndex(
            self.direction.findData(owner.floor.instruction_print_options["orientation"])
        )
        tools.addWidget(self.direction, 0, 2)
        self.mode = QComboBox()
        for title, key in [("自動最適化", "auto"), ("標準", "standard"), ("コンパクト", "compact")]:
            self.mode.addItem(title, key)
        self.mode.setCurrentIndex(self.mode.findData(owner.print_mode.currentData()))
        self.mode.setVisible(map_page is None)
        tools.addWidget(self.mode, 0, 3)
        self.info = QLabel()
        self.info.setWordWrap(True)
        tools.addWidget(self.info, 1, 0, 1, 4)
        self.view = QPdfView()
        self.document = QPdfDocument(self)
        self.view.setDocument(self.document)
        self.view.setPageMode(QPdfView.PageMode.MultiPage)
        self.view.setZoomMode(QPdfView.ZoomMode.FitInView)
        outer.addWidget(self.view, 1)
        buttons = QHBoxLayout()
        outer.addLayout(buttons)
        save = QPushButton("PDF保存")
        save.clicked.connect(map_page.export_pdf if map_page else owner.export_pdf)
        buttons.addWidget(save)
        direct = QPushButton("直接印刷")
        direct.clicked.connect(map_page.print_direct if map_page else owner.print_direct)
        buttons.addWidget(direct)
        close = QPushButton("閉じる")
        close.clicked.connect(self.accept)
        buttons.addWidget(close)
        for control in (self.scope, self.paper, self.direction, self.mode):
            control.currentIndexChanged.connect(self.refresh)
        self.refresh()

    def refresh(self, *_):
        o = self.owner
        s = o.floor.instruction_print_options
        if self.map_page is None:
            s["mode"] = self.scope.currentData()
        s.update(
            paper=self.paper.currentText(),
            orientation=self.direction.currentData(),
        )
        o.instruction_output_mode.blockSignals(True)
        o.instruction_output_mode.setCurrentIndex(o.instruction_output_mode.findData(s["mode"]))
        o.instruction_output_mode.blockSignals(False)
        if self.map_page is None:
            o.print_mode.setCurrentIndex(o.print_mode.findData(self.mode.currentData()))
        else:
            self.map_page.load_options()
        o.floor.schedule_save()
        self.document.close()
        render = self.map_page.render if self.map_page else o.paint_printer
        plan = save_rendered_pdf(self.path, lambda temp: render(configured_printer(temp)))
        self.document.load(str(self.path))
        self.info.setText(plan.reason)

    def done(self, result):
        self.document.close()
        self.runtime.cleanup()
        super().done(result)
