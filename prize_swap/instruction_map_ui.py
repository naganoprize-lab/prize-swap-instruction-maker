"""Embedded, read-only map linked by entry IDs to the existing instruction table."""

from PySide6.QtCore import Qt, QRectF, QTimer
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QGraphicsView,
    QGraphicsScene,
    QGraphicsItem,
    QComboBox,
    QPushButton,
    QCheckBox,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QLabel,
    QInputDialog,
    QDialog,
)
from PySide6.QtPrintSupport import (
    QPrintPreviewDialog,
    QPrintDialog,
    QAbstractPrintDialog,
    QPrinter,
    QPrinterInfo,
)
from .instructions import booths, mark_edited, prepare_entry
from .instruction_map import (
    grouped_booths,
    draw_map_card,
    marker_rects,
    plan_map,
    map_layout,
    draw_map_page,
)
from .instruction_output import validate_options
from .printing import configured_printer, render_pages, save_rendered_pdf
from .core import AppError
from .machine_matching import machine_key
from .theme import manager, tokens


class MapItem(QGraphicsItem):
    def __init__(self, page, group):
        super().__init__()
        self.page = page
        self.group = group
        self.setPos(group.machine.x, group.machine.y)
        self.setAcceptedMouseButtons(Qt.MouseButton.LeftButton)
        self.setToolTip(" / ".join(f"{b.number}：{b.token}" for b in group.booths))

    def boundingRect(self):
        return QRectF(0, 0, self.group.machine.width, self.group.machine.height)

    def paint(self, painter, option, widget):
        selected = bool(
            {b.product.entry_id for b in self.group.booths} & self.page.selected_entries
        )
        draw_map_card(painter, self.boundingRect(), self.group, selected)

    def mouseReleaseEvent(self, event):
        rects = marker_rects(self.boundingRect(), len(self.group.booths))
        b = next(
            (b for rect, b in zip(rects, self.group.booths) if rect.contains(event.pos())),
            self.group.booths[0],
        )
        self.page.select_instruction(b.product.entry_id)
        event.accept()

    def mousePressEvent(self, event):
        event.accept()


class MapView(QGraphicsView):
    def wheelEvent(self, event):
        scale = 1.15 if event.angleDelta().y() > 0 else 1 / 1.15
        self.scale(scale, scale)
        event.accept()


class InstructionMapPage(QWidget):
    def __init__(self, window):
        super().__init__()
        self.owner = window
        self.selected_entries = set()
        self.items = {}
        self._building = False
        self.entries = []
        outer = QVBoxLayout(self)
        outer.setContentsMargins(3, 3, 3, 3)
        tools = QHBoxLayout()
        outer.addLayout(tools)
        self.paper = QComboBox()
        self.paper.addItems(["A4", "A3"])
        tools.addWidget(self.paper)
        self.orientation = QComboBox()
        for label, key in [("自動方向", "auto"), ("縦向き", "portrait"), ("横向き", "landscape")]:
            self.orientation.addItem(label, key)
        tools.addWidget(self.orientation)
        self.all_entries = QCheckBox("全登録指示")
        tools.addWidget(self.all_entries)
        self.context = QCheckBox("周辺を薄く表示")
        tools.addWidget(self.context)
        self.buttons = {}
        for name, callback in [
            ("自動フィット", self.fit),
            ("＋", lambda: self.view.scale(1.2, 1.2)),
            ("−", lambda: self.view.scale(1 / 1.2, 1 / 1.2)),
            ("印刷プレビュー", self.preview),
            ("PDF保存", self.export_pdf),
            ("直接印刷", self.print_direct),
        ]:
            b = QPushButton(name)
            b.clicked.connect(callback)
            tools.addWidget(b)
            self.buttons[name] = b
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        outer.addWidget(self.summary)
        self.splitter = QSplitter(Qt.Orientation.Vertical)
        outer.addWidget(self.splitter, 1)
        self.scene = QGraphicsScene(self)
        self.view = MapView(self.scene)
        self.view.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.view.setRenderHints(
            QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing
        )
        self.splitter.addWidget(self.view)
        self.unresolved = QTableWidget(0, 3)
        self.unresolved.setHorizontalHeaderLabels(
            ["対応番号", "マシン／管理番号", "照合状態（ダブルクリックで確認）"]
        )
        self.unresolved.setMaximumHeight(180)
        self.splitter.addWidget(self.unresolved)
        self.unresolved.cellDoubleClicked.connect(self.resolve_position)
        self.unresolved.setColumnWidth(1, 190)
        self.unresolved.setColumnWidth(2, 380)
        for control in (self.paper, self.orientation):
            control.currentIndexChanged.connect(self.settings_changed)
        for control in (self.all_entries, self.context):
            control.toggled.connect(self.settings_changed)
        window.floor.changed.connect(self.refresh)
        manager().changed.connect(self.theme_changed)
        self.load_options()
        self.theme_changed()

    def theme_changed(self, *_):
        self.view.setBackgroundBrush(QColor(tokens()["canvas"]))
        self.scene.update()

    def load_options(self):
        s = self.owner.floor.instruction_print_options
        self._building = True
        if hasattr(self.owner, "instruction_output_mode"):
            self.owner.instruction_output_mode.blockSignals(True)
            self.owner.instruction_output_mode.setCurrentIndex(
                self.owner.instruction_output_mode.findData(s["mode"])
            )
            self.owner.instruction_output_mode.blockSignals(False)
        self.paper.setCurrentText(s["paper"])
        self.orientation.setCurrentIndex(self.orientation.findData(s["orientation"]))
        self.all_entries.setChecked(s["all_entries"])
        self.context.setChecked(s["context"])
        self._building = False

    def settings_changed(self, *_):
        if self._building:
            return
        s = self.owner.floor.instruction_print_options
        s.update(
            paper=self.paper.currentText(),
            orientation=self.orientation.currentData(),
            all_entries=self.all_entries.isChecked(),
            context=self.context.isChecked(),
        )
        self.owner.floor.schedule_save()
        self.refresh()

    def showEvent(self, event):
        super().showEvent(event)
        self.refresh()
        QTimer.singleShot(0, self.fit)

    def refresh(self, *_):
        if self._building:
            return
        w = self.owner
        self.load_options()
        entries = booths(
            w.visible, w.floor.machines, w.floor.number_references, self.all_entries.isChecked()
        )
        signature = (
            tuple(
                (
                    b.key,
                    b.number,
                    b.token,
                    b.machine.uid if b.machine else None,
                    (
                        (b.machine.x, b.machine.y, b.machine.width, b.machine.height)
                        if b.machine
                        else None
                    ),
                )
                for b in entries
            ),
            self.context.isChecked(),
            (
                tuple((m.uid, m.x, m.y, m.width, m.height) for m in w.floor.machines)
                if self.context.isChecked()
                else ()
            ),
        )
        self.entries = entries
        if signature != getattr(self, "signature", None):
            self.signature = signature
            self.scene.clear()
            self.items = {}
            groups = grouped_booths(entries)
            if self.context.isChecked():
                targets = {g.machine.uid for g in groups}
                for m in w.floor.machines:
                    if m.uid in targets:
                        continue
                    item = self.scene.addRect(QRectF(m.x, m.y, m.width, m.height))
                    item.setOpacity(0.15)
            for group in groups:
                item = MapItem(self, group)
                self.scene.addItem(item)
                self.items[group.machine.uid] = item
            self.scene.setSceneRect(self.scene.itemsBoundingRect().adjusted(-20, -20, 20, 20))
            self.fit()
        self.unresolved_rows = [b for b in entries if not b.machine]
        self.unresolved.setRowCount(len(self.unresolved_rows))
        self.unresolved.setVisible(bool(self.unresolved_rows))
        for i, b in enumerate(self.unresolved_rows):
            for j, text in enumerate((str(b.number), b.token or "番号未入力", b.status)):
                self.unresolved.setItem(i, j, QTableWidgetItem(text))
        p = self.plan()
        self.summary.setText(p.reason + " ／ 画面の倍率は印刷に影響しません。")
        self.highlight_selection()
        if hasattr(w, "update_instruction_status"):
            w.update_instruction_status()

    def plan(self):
        w = self.owner
        s = w.floor.instruction_print_options
        return plan_map(
            w.visible,
            w.floor.machines,
            w.floor.number_references,
            w.selected_day(),
            s["paper"],
            s["orientation"],
            s["all_entries"],
        )

    def fit(self, *_):
        if self.scene.items():
            self.view.fitInView(
                self.scene.itemsBoundingRect().adjusted(-15, -15, 15, 15),
                Qt.AspectRatioMode.KeepAspectRatio,
            )

    def highlight_selection(self, *_):
        self.selected_entries = {p.entry_id for p in self.owner.selected_products()}
        self.scene.update()

    def select_instruction(self, entry_id):
        w = self.owner
        w.table.clearSelection()
        from PySide6.QtCore import QItemSelectionModel

        for row, p in enumerate(w.model.products):
            if p.entry_id == entry_id:
                index = w.proxy.mapFromSource(w.model.index(row, 0))
                if not index.isValid():
                    w.search.clear()
                    w.filter_choice.setCurrentIndex(0)
                    index = w.proxy.mapFromSource(w.model.index(row, 0))
                if index.isValid():
                    w.table.selectionModel().select(
                        index,
                        QItemSelectionModel.SelectionFlag.Select
                        | QItemSelectionModel.SelectionFlag.Rows,
                    )
                    w.table.scrollTo(index)
                break
        self.highlight_selection()

    def resolve_position(self, row, column):
        b = self.unresolved_rows[row]
        candidates = list(b.choices) or self.owner.floor.machines
        if not candidates:
            return
        labels = [
            f'{machine_key(m) or m.source_key or "番号なし"} / 管理 {m.source_key} / ({m.x:g},{m.y:g}) / ID {m.uid[:8]}'
            for m in candidates
        ]
        text, ok = QInputDialog.getItem(
            self,
            "地図の対象マシンを確認",
            "候補を確認して選択（配置は変更しません）",
            labels,
            0,
            False,
        )
        if not ok:
            return
        from .instruction_editor import all_state, InstructionCommand

        before = all_state(self.owner)
        b.link["machine_key"] = candidates[labels.index(text)].uid
        mark_edited(b.product, ["booth_assignments"])
        self.owner.instruction_history.push(
            InstructionCommand(
                self.owner, before, all_state(self.owner), "地図の位置照合を確認", True
            )
        )
        self.owner.mark_dirty()
        self.refresh()

    def render(self, printer):
        plan = self.plan()
        render_pages(
            printer,
            len(plan.pages),
            lambda painter, index: draw_map_page(painter, plan, index),
            lambda index: map_layout(plan.paper, plan.pages[index].orientation),
        )
        return plan

    def check_targets(self):
        if not self.all_entries.isChecked():
            self.owner.print_products()
        elif not self.owner.visible:
            raise AppError("現在の登録日に指示がありません。")

    def preview(self, *_):
        from .instruction_preview import InstructionPreview

        try:
            self.check_targets()
            InstructionPreview(self.owner, map_page=self).exec()
        except Exception as exc:
            self.owner.error(exc)

    def export_pdf(self, *_):
        try:
            self.check_targets()
            path = self.owner.save_path("入替予定地図PDF", "PDF (*.pdf)", ".pdf")
            if path:
                save_rendered_pdf(
                    path,
                    lambda temp: self.render(configured_printer(temp)),
                    [self.owner.source, self.owner.floor.source],
                )
        except Exception as exc:
            self.owner.error(exc)

    def print_direct(self, *_):
        try:
            self.check_targets()
            printer = configured_printer()
            p = self.plan()
            printer.setPageLayout(map_layout(p.paper, p.pages[0].orientation))
            d = QPrintDialog(printer, self)
            d.setMinMax(1, len(p.pages))
            d.setOption(QAbstractPrintDialog.PrintDialogOption.PrintPageRange, True)
            if d.exec() == QDialog.DialogCode.Accepted:
                sizes = QPrinterInfo(printer).supportedPageSizes()
                if (
                    printer.outputFormat() != QPrinter.OutputFormat.PdfFormat
                    and sizes
                    and all(
                        v.id() != map_layout(p.paper, p.pages[0].orientation).pageSize().id()
                        for v in sizes
                    )
                ):
                    raise AppError(
                        f"選択したプリンターは{p.paper}をサポートしていません。用紙設定を確認してください。"
                    )
                self.render(printer)
        except Exception as exc:
            self.owner.error(exc)
