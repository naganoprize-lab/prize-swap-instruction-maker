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
    draw_context_card,
    map_bounds,
)
from .instruction_output import validate_options
from .printing import configured_printer, render_pages, save_rendered_pdf
from .core import AppError
from .machine_matching import machine_key
from .theme import manager, tokens


class MapItem(QGraphicsItem):
    def __init__(self, page, group):
        super().__init__()
        self.setCacheMode(QGraphicsItem.CacheMode.DeviceCoordinateCache)
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


class ContextMapItem(QGraphicsItem):
    def __init__(self, page, machine):
        super().__init__()
        self.setCacheMode(QGraphicsItem.CacheMode.DeviceCoordinateCache)
        self.page = page
        self.machine = machine
        self.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self.setZValue(-1)
        self.setPos(machine.x, machine.y)
        from .machine_matching import display_identity
        from .analysis_settings import machine_type

        self.setToolTip(
            display_identity(machine)
            + " / "
            + machine_type(machine, page.owner.floor.analysis_settings)
        )

    def boundingRect(self):
        return QRectF(0, 0, self.machine.width, self.machine.height)

    def paint(self, painter, option, widget=None):
        draw_context_card(
            painter,
            self.boundingRect(),
            self.machine,
            self.page.owner.floor.analysis_settings,
            screen=True,
        )


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
        self.context_items = {}
        self.target_bounds = QRectF()
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
        self.context = QCheckBox("周辺マシンを表示")
        self.context.setToolTip(
            "番号と機種名を中間色で表示。自動フィットは対象マシンだけを基準にします。"
        )
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
        for item in [*self.items.values(), *self.context_items.values()]:
            item.update()
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
        groups = grouped_booths(entries)
        target_signature = tuple(
            (g.machine.uid, g.machine.x, g.machine.y, g.machine.width, g.machine.height)
            for g in groups
        )
        changed_targets = target_signature != getattr(self, "_target_signature", None)
        self._target_signature = target_signature
        self.target_bounds = map_bounds(groups) if groups else QRectF()
        targets = {g.machine.uid for g in groups}
        for uid in set(self.items) - targets:
            self.scene.removeItem(self.items.pop(uid))
        for group in groups:
            uid = group.machine.uid
            if uid not in self.items:
                self.items[uid] = MapItem(self, group)
                self.scene.addItem(self.items[uid])
            else:
                item = self.items[uid]
                key = (
                    group.machine.width,
                    group.machine.height,
                    tuple((b.number, b.product.entry_id, b.token) for b in group.booths),
                )
                if key != getattr(item, "_content_key", None):
                    item.prepareGeometryChange()
                    item.group = group
                    item._content_key = key
                    item.setToolTip(" / ".join(f"{b.number}：{b.token}" for b in group.booths))
                    item.update()
                item.setPos(group.machine.x, group.machine.y)
        neighbors = (
            {m.uid: m for m in w.floor.machines if m.uid not in targets}
            if self.context.isChecked() and groups
            else {}
        )
        for uid in set(self.context_items) - set(neighbors):
            self.scene.removeItem(self.context_items.pop(uid))
        for uid, machine in neighbors.items():
            if uid not in self.context_items:
                self.context_items[uid] = ContextMapItem(self, machine)
                self.scene.addItem(self.context_items[uid])
            else:
                item = self.context_items[uid]
                key = (
                    machine.width,
                    machine.height,
                    machine.number,
                    machine.source_key,
                    machine.kind,
                    repr(w.floor.analysis_settings.get("types")),
                    repr(w.floor.analysis_settings.get("aliases")),
                )
                if key != getattr(item, "_content_key", None):
                    item.prepareGeometryChange()
                    item.machine = machine
                    item._content_key = key
                    from .machine_matching import display_identity
                    from .analysis_settings import machine_type

                    item.setToolTip(
                        display_identity(machine)
                        + " / "
                        + machine_type(machine, w.floor.analysis_settings)
                    )
                    item.update()
                item.setPos(machine.x, machine.y)
        # Keep the scrollable scene stable when neighbors are toggled. Only the
        # target rectangle controls fitting; no surrounding machine changes zoom.
        physical = QRectF()
        for machine in w.floor.machines:
            physical = physical.united(QRectF(machine.x, machine.y, machine.width, machine.height))
        scene_bounds = physical.united(self.target_bounds).adjusted(-40, -40, 40, 40)
        if self.scene.sceneRect() != scene_bounds:
            self.scene.setSceneRect(scene_bounds)
        self.entries = entries
        if changed_targets:
            self.fit()
            QTimer.singleShot(0, self.fit)
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
            context=s["context"],
            settings=w.floor.analysis_settings,
            entries=self.entries,
        )

    def fit(self, *_):
        if not self.target_bounds.isEmpty():
            self.view.fitInView(
                self.target_bounds,
                Qt.AspectRatioMode.KeepAspectRatio,
            )

    def highlight_selection(self, *_):
        self.selected_entries = {p.entry_id for p in self.owner.selected_products()}
        for item in self.items.values():
            item.update()
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

    def check_targets(self, action=None):
        if not self.all_entries.isChecked():
            self.owner.print_products(action)
        elif not self.owner.visible:
            raise AppError("現在の登録日に指示がありません。")

    def preview(self, *_):
        from .instruction_preview import InstructionPreview

        try:
            self.check_targets(self.preview)
            InstructionPreview(self.owner, map_page=self).exec()
        except Exception as exc:
            self.owner.error(exc)

    def export_pdf(self, *_):
        try:
            self.check_targets(self.export_pdf)
            path = self.owner.save_path("入替予定地図PDF", "PDF (*.pdf)", ".pdf")
            if path:
                save_rendered_pdf(
                    path,
                    lambda temp: self.render(configured_printer(temp)),
                    [self.owner.source, self.owner.floor.source],
                )
                if hasattr(self.owner, "workflow"):
                    self.owner.workflow.record_file("output", path, "入替予定地図PDF")
        except Exception as exc:
            self.owner.error(exc)

    def print_direct(self, *_):
        try:
            self.check_targets(self.print_direct)
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
                if hasattr(self.owner, "workflow"):
                    self.owner.workflow.record_file("output", "プリンターへ送信", "入替予定地図")
        except Exception as exc:
            self.owner.error(exc)
