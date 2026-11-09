from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
from datetime import date, datetime
import json
from pathlib import Path
import re

from PySide6.QtCore import QDate, QPointF, QRectF, QStandardPaths, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QKeySequence,
    QPainter,
    QPageLayout,
    QPageSize,
    QPen,
    QShortcut,
    QUndoCommand,
    QUndoStack,
)
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QDoubleSpinBox,
    QGraphicsItem,
    QGraphicsObject,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsView,
    QScrollArea,
    QLabel,
    QLineEdit,
    QMessageBox,
    QVBoxLayout,
    QWidget,
)
from PySide6.QtPrintSupport import QPrintDialog

from .theme import manager as theme_manager, tokens as theme_tokens
from .core import AppError, machine_numbers
from .machine_matching import machine_key, display_identity
from .floor_data import Machine, import_layout, save_layout, validate_machines, validate_state
from .fonts import print_font
from .floor_status import (
    machine_status,
    days_text,
    money,
    ratio_text,
    overlapping,
    filter_status,
    filter_legend,
    FILTER_FIELDS,
)
from .floor_status import numeric, effective_stat
from .core import parse_date
from .floor_render import CardRenderer, DISPLAY_FIELDS, draw_card
from .floor_print import (
    FloorPrintOptions,
    floor_printer,
    plan_floor,
    render_floor,
    save_floor_pdf,
    paper_supported,
)
from .floor_print_ui import FloorPrintControls, FloorPreviewDialog
from .floor_toolbar import build_toolbar
from .floor_workspace import FloorWorkspaceMixin
from .floor_presentation import DEFAULTS

GRID = 20


class StateCommand(QUndoCommand):
    def __init__(self, widget, before, after, text):
        super().__init__(text)
        self.widget, self.before, self.after = widget, before, after
        self.first = True

    def undo(self):
        self.widget.restore(self.before, keep_history=True)
        self.widget.schedule_save()

    def redo(self):
        if self.first:
            self.first = False
        else:
            self.widget.restore(self.after, keep_history=True)
        self.widget.schedule_save()


class ResizeHandle(QGraphicsRectItem):
    def __init__(self, node, corner):
        super().__init__(-5, -5, 10, 10, node)
        self.node, self.corner = node, corner
        self.setBrush(QColor("#3D795C"))
        self.setPen(QPen(Qt.PenStyle.NoPen))
        self.setZValue(5)
        self.setCursor(
            Qt.CursorShape.SizeFDiagCursor if corner in (0, 3) else Qt.CursorShape.SizeBDiagCursor
        )

    def mousePressEvent(self, event):
        self._resizing = True
        self.before = self.node.owner.state()
        m = self.node.machine
        self.initial = QRectF(m.x, m.y, m.width, m.height)
        event.accept()

    def mouseMoveEvent(self, event):
        if not getattr(self, "_resizing", False):
            event.accept()
            return
        point = event.scenePos()
        if self.node.owner.snap.isChecked():
            point = QPointF(round(point.x() / GRID) * GRID, round(point.y() / GRID) * GRID)
        rect = QRectF(self.initial)
        if self.corner in (0, 2):
            rect.setLeft(max(0, min(point.x(), rect.right() - 80)))
        else:
            rect.setRight(max(point.x(), rect.left() + 80))
        if self.corner in (0, 1):
            rect.setTop(max(0, min(point.y(), rect.bottom() - 60)))
        else:
            rect.setBottom(max(point.y(), rect.top() + 60))
        self.node.resize_to(rect)
        event.accept()

    def mouseReleaseEvent(self, event):
        self._resizing = False
        self.node.owner.record(self.before, "マシンのサイズ変更")
        event.accept()


class MachineNode(QGraphicsObject):
    def __init__(self, owner, machine):
        super().__init__()
        self.owner, self.machine = owner, machine
        self.display_rect = QRectF(0, 0, machine.width, machine.height)
        self.setCacheMode(QGraphicsItem.CacheMode.DeviceCoordinateCache)
        self.setPos(machine.x, machine.y)
        self.setFlags(
            QGraphicsItem.GraphicsItemFlag.ItemIsMovable
            | QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
            | QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges
        )
        self.selection_halo = QGraphicsRectItem(self.display_rect.adjusted(-4, -4, 4, 4), self)
        self.selection_halo.setPen(QPen(QColor(90, 165, 255, 90), 5))
        self.selection_halo.setBrush(Qt.BrushStyle.NoBrush)
        self.selection_halo.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self.selection_halo.setZValue(-1)
        self.selection_halo.hide()
        self.handles = [ResizeHandle(self, i) for i in range(4)]
        self.update_handles()

    def boundingRect(self):
        return self.display_rect

    def update_handles(self):
        m = self.machine
        w, h = self.boundingRect().width(), self.boundingRect().height()
        for handle, point in zip(self.handles, [(0, 0), (w, 0), (0, h), (w, h)]):
            handle.setPos(*point)
            handle.setVisible(
                self.isSelected()
                and self.owner.presentation["edit"]
                and self.owner.presentation["mode"] == "actual"
            )
        self.selection_halo.setRect(self.display_rect.adjusted(-4, -4, 4, 4))
        self.selection_halo.setVisible(self.isSelected())
        plans = self.owner.assignments().get(machine_key(m), [])
        installed = m.installed_on or "—"
        self.setToolTip(
            f"{display_identity(m)} / {m.kind}\n{m.product}\n売上 {money(effective_stat(m,"sales"))} / アベ比 {ratio_text(m)}\n設置日 {installed} / 投入日数 {days_text(m,self.owner.reference_day())}\n参照ID：{m.source_key or '追加マシン'}"
            + (
                "\n入替予定："
                + "、".join(
                    p.name + "（" + (p.machine_methods.get(machine_key(m)) or "方法未設定") + "）"
                    for p in plans
                )
                if plans
                else ""
            )
        )

        temporal = getattr(self.owner, "temporal", None)
        if temporal and temporal.active:
            summary = getattr(m, "temporal_summary", {})
            self.setToolTip(
                self.toolTip()
                + f'\n対象期間 {temporal.frame.start}〜{temporal.frame.end} / 取得{summary.get("acquired",0)}/{summary.get("expected",0)}日 / 未取得{summary.get("missing",0)}日'
                + "\n"
                + "\n".join(temporal.differences.get(m.uid, []))
            )

    def paint(self, painter, option, widget=None):
        background, status, priority, fields, font, methods = self.owner.card_context(self.machine)
        if self.owner.presentation["render_mode"] == "light":
            from .light_render import paint_light

            paint_light(self, painter, background, priority)
        else:
            layout = self.owner.renderer.plan(
                self.machine,
                self.boundingRect().width(),
                self.boundingRect().height(),
                font,
                fields,
                self.owner.reference_day(),
                status,
                methods,
                summary=self.owner.display_mode.currentData() == "compact",
                truncate=True,
                settings=self.owner.metric_settings(),
            )
            draw_card(
                painter,
                self.boundingRect(),
                layout,
                background,
                priority,
                self.isSelected(),
                self.machine.uid in self.owner.overlaps,
                screen=True,
                search_match=getattr(self, "search_match", False)
                or self.machine.uid in getattr(self.owner, "_highlight_uids", set())
                or self.machine.uid in (self.owner.analysis_highlight_uids or set()),
            )
        temporal = getattr(self.owner, "temporal", None)
        if (
            temporal
            and temporal.active
            and temporal.settings["highlight"]
            and self.machine.uid in temporal.differences
        ):
            painter.save()
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor("#F59E0B"), 3, Qt.PenStyle.DashLine))
            painter.drawRoundedRect(self.boundingRect().adjusted(7, 7, -7, -7), 5, 5)
            painter.restore()
        if self.isSelected():
            painter.save()
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor(72, 156, 255, 75), 10))
            painter.drawRoundedRect(self.boundingRect().adjusted(5, 5, -5, -5), 6, 6)
            painter.setPen(QPen(QColor("#2563EB"), 3))
            painter.drawRoundedRect(self.boundingRect().adjusted(3, 3, -3, -3), 6, 6)
            painter.restore()
        if (self.machine.analysis or {}).get(
            "quality_issues"
        ) or self.machine.match_method == "review":
            painter.save()
            from .theme import tokens

            painter.setPen(QPen(QColor(tokens()["danger"]), 2))
            painter.drawText(
                self.boundingRect().adjusted(0, 12, -6, 0),
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop,
                "⚠",
            )
            painter.restore()

    def itemChange(self, change, value):
        if getattr(self.owner, "_projecting", False):
            return super().itemChange(change, value)
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionChange:
            x, y = max(0, value.x()), max(0, value.y())
            if self.owner.snap.isChecked():
                x, y = round(x / GRID) * GRID, round(y / GRID) * GRID
            return QPointF(x, y)
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionHasChanged:
            self.machine.x, self.machine.y = self.pos().x(), self.pos().y()
        if change == QGraphicsItem.GraphicsItemChange.ItemSelectedHasChanged:
            self.selection_halo.setVisible(bool(value))
            for h in self.handles:
                h.setVisible(
                    bool(value)
                    and self.owner.presentation["edit"]
                    and self.owner.presentation["mode"] == "actual"
                )
        return super().itemChange(change, value)

    def resize_to(self, rect):
        self.prepareGeometryChange()
        self.machine.width, self.machine.height = rect.width(), rect.height()
        self.display_rect = QRectF(0, 0, rect.width(), rect.height())
        self.setPos(rect.topLeft())
        self.update_handles()
        self.update()

    def mousePressEvent(self, event):
        self.press_position = event.screenPos()
        self.before = self.owner.state()
        self.ctrl_select = bool(event.modifiers() & Qt.KeyboardModifier.ControlModifier)
        self.details_only_press = (
            not self.owner.presentation["edit"]
            and not self.ctrl_select
            and len(self.owner.selection_state.selected_machine_keys) > 1
        )
        if self.details_only_press:
            event.accept()
            return
        if self.ctrl_select:
            self.setSelected(not self.isSelected())
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        if getattr(self, "details_only_press", False):
            self.details_only_press = False
            if (event.screenPos() - self.press_position).manhattanLength() < 5:
                QTimer.singleShot(0, lambda: self.owner.open_details(self.machine.uid))
            event.accept()
            return
        if getattr(self, "ctrl_select", False):
            event.accept()
            return
        super().mouseReleaseEvent(event)
        if hasattr(self, "before") and self.owner.presentation["edit"]:
            self.owner.record(self.before, "マシンの移動")
        if (
            not self.owner.presentation["edit"]
            and event.button() == Qt.MouseButton.LeftButton
            and (event.screenPos() - self.press_position).manhattanLength() < 5
        ):
            QTimer.singleShot(0, lambda: self.owner.open_details(self.machine.uid))

    def mouseDoubleClickEvent(self, event):
        self.owner.open_details(self.machine.uid)
        event.accept()


class FloorView(QGraphicsView):
    def __init__(self, scene, owner):
        super().__init__(scene)
        self.owner = owner
        self.setDragMode(QGraphicsView.DragMode.RubberBandDrag)
        self.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing)
        from .theme import tokens

        self.setBackgroundBrush(QColor(tokens()["canvas"]))
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.BoundingRectViewportUpdate)

    def wheelEvent(self, event):
        delta = event.angleDelta().y() or event.pixelDelta().y()
        if not delta:
            event.ignore()
            return
        factor = 1.15 if delta > 0 else 1 / 1.15
        self.owner.set_zoom(self.transform().m11() * 100 * factor)
        self.owner.capture_view()
        event.accept()

    def mousePressEvent(self, event):
        from .selection import begin_drag

        if begin_drag(self, event):
            return
        if event.button() == Qt.MouseButton.MiddleButton or (
            event.button() == Qt.MouseButton.LeftButton
            and (
                getattr(self, "_space", False)
                or (
                    not self.owner.presentation["edit"]
                    and self.itemAt(event.position().toPoint()) is None
                    and not event.modifiers() & Qt.KeyboardModifier.ControlModifier
                )
            )
        ):
            self._pan = event.position().toPoint()
            self.viewport().setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if hasattr(self, "_selection_start"):
            from PySide6.QtCore import QRect

            self._selection_band.setGeometry(
                QRect(self._selection_start, event.position().toPoint()).normalized()
            )
            event.accept()
            return
        if getattr(self, "_pan", None) is not None:
            point = event.position().toPoint()
            delta = point - self._pan
            self._pan = point
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - delta.x())
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() - delta.y())
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        from .selection import end_drag

        if end_drag(self, event):
            return
        if (
            self.owner.selection_state.selection_mode == "individual"
            or event.modifiers() & Qt.KeyboardModifier.ControlModifier
        ):
            event.accept()
            return
        if getattr(self, "_pan", None) is not None:
            self._pan = None
            self.viewport().unsetCursor()
            self.owner.capture_view()
            event.accept()
        else:
            super().mouseReleaseEvent(event)

    def keyPressEvent(self, event):
        if event.matches(QKeySequence.StandardKey.SelectAll):
            self.owner.selection_state.set(m.uid for m in self.owner.machines)
            event.accept()
            return
        if event.key() == Qt.Key.Key_Escape:
            self.owner.selection_state.set([])
            event.accept()
            return
        if event.key() == Qt.Key.Key_Space:
            self._space = True
            event.accept()
        else:
            super().keyPressEvent(event)

    def keyReleaseEvent(self, event):
        if event.key() == Qt.Key.Key_Space:
            self._space = False
            event.accept()
        else:
            super().keyReleaseEvent(event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if getattr(self.owner, "_fullscreen_transition", False) or getattr(
            self.owner, "_theme_transition", False
        ):
            return
        if self.owner.presentation["mode"] == "list" and hasattr(self.owner, "nodes"):
            self.owner.apply_presentation()
        if getattr(self.owner, "_zoom", 100) is None and hasattr(self.owner, "nodes"):
            self.owner.fit(save=False)

    def drawForeground(self, painter, rect):
        super().drawForeground(painter, rect)
        if not self.owner.print_options.boundaries:
            return
        pen = QPen(QColor("#5B7183"), 0, Qt.PenStyle.DashLine)
        painter.setPen(pen)
        painter.setFont(print_font(12))
        for i, frame in enumerate(self.owner.page_frames):
            if frame.intersects(rect):
                painter.drawRect(frame)
                painter.drawText(frame.topLeft() + QPointF(4, 16), f"印刷ページ {i+1}")

    def drawBackground(self, painter, rect):
        super().drawBackground(painter, rect)
        if self.owner.presentation["render_mode"] == "light" or self.transform().m11() < 0.3:
            return
        painter.setPen(QPen(QColor(theme_tokens()["grid"]), 0))
        step = GRID
        left, top = int(rect.left()) // step * step, int(rect.top()) // step * step
        # Limit visible-grid work; normal viewports require only a few thousand dots.
        for x in range(left, int(rect.right()) + step, step):
            for y in range(top, int(rect.bottom()) + step, step):
                painter.drawPoint(x, y)


class FloorWidget(FloorWorkspaceMixin, QWidget):
    analytics_requested = Signal(str, str)
    product_edited = Signal()
    changed = Signal()
    message = Signal(str)

    def __init__(self, products, selected_day, storage_path=None, parent=None):
        super().__init__(parent)
        self.app_window = parent
        self.products, self.selected_day = products, selected_day
        self.machines, self.original = [], []
        from .selection import SelectionState

        self.selection_state = SelectionState(self)
        self._syncing_selection = False
        self.selection_state.changed.connect(self.apply_shared_selection)
        self.source = ""
        self.initialized = False
        self._assignments = {}
        self.storage = (
            Path(storage_path)
            if storage_path
            else Path(
                QStandardPaths.writableLocation(
                    QStandardPaths.StandardLocation.AppLocalDataLocation
                )
            )
            / "floor-autosave.json"
        )
        from .workflow_state import AuditUndoStack

        self.history = AuditUndoStack(self)
        self.history.setUndoLimit(100)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.autosave)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(4)
        self.renderer = CardRenderer()
        self.print_renderer = CardRenderer()
        self.print_options = FloorPrintOptions()
        from .instruction_output import validate_options

        self.instruction_print_options = validate_options()
        self.overlaps = set()
        self.page_frames = []
        self.loaded_at = ""
        self._zoom = 100
        self._fitting = False
        self._setting_day = False
        self.follow_today = True
        self._last_today = QDate.currentDate()
        self._active_preview = None
        from .sales_analysis import empty_snapshot

        self.number_references = []
        self.analysis_state = empty_snapshot()
        self.performance_history = []
        from .analysis_settings import validate_settings

        self.analysis_settings = validate_settings()
        self.presentation = dict(DEFAULTS)
        from .temporal_controller import preferences as temporal_preferences

        self.temporal_settings = temporal_preferences()
        self.card_presets = {}
        self._view_center = None
        self._projecting = False
        self._building_view = True
        self._fullscreen_host = None
        self._fullscreen_transition = False
        self._detail_open = False
        self._highlight_uids = set()
        self.analysis_highlight_uids = None
        from .analytics_data import AnalyticsService, validate_ui_state

        self.analytics = AnalyticsService(self)
        self.analytics_ui = validate_ui_state()
        from .candidate_page import validate_candidate_state

        self.candidate_ui = validate_candidate_state()

        build_toolbar(self, layout)
        from .floor_dashboard import FloorDashboard

        self.dashboard = FloorDashboard(self)
        self.dashboard.layout().itemAt(0).layout().addWidget(self.analysis_clear)
        layout.addWidget(self.dashboard)
        self.scene = QGraphicsScene(self)
        self.view = FloorView(self.scene, self)
        self.view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.view.customContextMenuRequested.connect(self.canvas_menu)
        from .detail_dock import DetailDockController, validate_preferences

        self.detail_settings = validate_preferences()
        self.detail_controller = DetailDockController(self, self.view, layout)
        self.nodes = {}
        from .theme import manager

        manager().about_to_change.connect(self.theme_changing)
        manager().changed.connect(self.theme_changed)
        for key, callback in [
            ("Delete", self.delete_selected),
            ("Ctrl+D", self.duplicate_selected),
            ("Ctrl+Z", self.history.undo),
            ("Ctrl+Y", self.history.redo),
        ]:
            shortcut = QShortcut(QKeySequence(key), self.view)
            shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            shortcut.activated.connect(callback)
        self.day_timer = QTimer(self)
        self.day_timer.setInterval(30000)
        self.day_timer.timeout.connect(self.check_day)
        self.day_timer.start()
        self._building_view = False
        self.scene.selectionChanged.connect(self.selection_changed)
        for bar in (self.view.horizontalScrollBar(), self.view.verticalScrollBar()):
            bar.sliderReleased.connect(self.capture_view)
            bar.actionTriggered.connect(lambda *_: QTimer.singleShot(0, self.capture_view))
        self.update_print_note()
        if self.storage.exists():
            try:
                self.restore(json.loads(self.storage.read_text(encoding="utf-8")))
                self.info.setText(f"自動保存から{len(self.machines)}枠を復元しました。")
            except Exception as exc:
                self.info.setText(f"自動保存を復元できません：{exc}")

        from .temporal_controller import TemporalController

        self.temporal = TemporalController(self)

    @property
    def physical_machines(self):
        t = getattr(self, "temporal", None)
        return t.physical_machines if t else self.machines

    def theme_changing(self):
        if hasattr(self, "view"):
            self._theme_camera = (
                self.view.transform(),
                self.view.mapToScene(self.view.viewport().rect().center()),
            )
            self._theme_transition = True

    def theme_changed(self, *_):
        from .theme import tokens

        self.view.setBackgroundBrush(QColor(tokens()["canvas"]))
        for node in self.nodes.values():
            node.update()
        if hasattr(self, "_theme_camera"):
            transform, center = self._theme_camera
            self.view.setTransform(transform)
            self.view.centerOn(center)
        self._theme_transition = False
        self.view.viewport().update()
        self.timer.start(800)

    def closeEvent(self, event):
        if getattr(self, "temporal", None):
            self.temporal.shutdown()
        self.day_timer.stop()
        super().closeEvent(event)

    def showEvent(self, event):
        self.check_day()
        self.day_timer.start()
        super().showEvent(event)
        if not self._fullscreen_transition:
            QTimer.singleShot(0, self.restore_view_center)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "dashboard"):
            compact = self.height() < 500 and hasattr(self.window(), "dashboard_page")
            self.dashboard.set_compact(compact)
            self.info.setVisible(
                not compact and not self._fullscreen_host and not self._fullscreen_transition
            )
            if hasattr(self, "selection_bar") and not self._fullscreen_host:
                short = self.height() < 420
                self.selection_bar.setVisible(not short)
                self.selection_popup.setVisible(short)

    def assignments(self):
        return self._assignments

    def refresh_assignments(self):
        from .sales_analysis import apply_snapshot

        from .machine_matching import prepare_references

        if not (getattr(self, "temporal", None) and self.temporal.active):
            if self.number_references:
                prepare_references(self.machines, self.number_references)
            apply_snapshot(self.machines, self.analysis_state)
        result = {}
        for p in self.products():
            if p.day == self.selected_day() and p.included:
                for number in p.machines:
                    result.setdefault(number, []).append(p)
        self._assignments = result

    def related(self, numbers):
        return [p for p in self.products() if any(n in numbers for n in p.machines)]

    def state(self):
        t = getattr(self, "temporal", None)
        master = t.master if t and t.active else None
        return dict(
            temporal_settings=deepcopy(self.temporal_settings),
            version=3,
            detail_dock=deepcopy(self.detail_settings),
            instruction_print_options=deepcopy(self.instruction_print_options),
            theme_mode=theme_manager().mode,
            analytics_ui=deepcopy(self.analytics_ui),
            candidate_ui=deepcopy(self.candidate_ui),
            number_references=deepcopy(self.number_references),
            performance_history=deepcopy(self.performance_history),
            analysis_settings=deepcopy(self.analysis_settings),
            sales_analysis=deepcopy(master["analysis"] if master else self.analysis_state),
            initialized=self.initialized,
            source=self.source,
            machines=[asdict(m) for m in (master["machines"] if master else self.machines)],
            original=deepcopy(self.original),
            snap=self.snap.isChecked(),
            show_methods=self.show_methods.isChecked(),
            display_mode=self.display_mode.currentData(),
            reference_date=(
                master["reference"].toString("yyyy-MM-dd")
                if master
                else self.reference_day().isoformat()
            ),
            follow_today=master["follow"] if master else self.follow_today,
            zoom=self._zoom,
            loaded_at=self.loaded_at,
            print_options=asdict(self.print_options),
            presentation=dict(self.presentation) | ({"edit": master["edit"]} if master else {}),
            card_presets=deepcopy(self.card_presets),
            view_center=deepcopy(self._view_center),
        )

    def restore(self, state, keep_history=False):
        state = validate_state(state)
        t = getattr(self, "temporal", None)
        if t and t.active:
            t.to_live(save=False)
        from .temporal_controller import preferences as temporal_preferences

        self.temporal_settings = temporal_preferences(state.get("temporal_settings"))
        if t:
            t.settings = self.temporal_settings
            t.cache.clear()
        from .sales_analysis import validate_snapshot

        from .theme import manager

        if "theme_mode" in state and not keep_history:
            manager().set_mode(state["theme_mode"], persist=False)
        from .instruction_output import validate_options

        self.instruction_print_options = validate_options(state.get("instruction_print_options"))
        from .detail_dock import validate_preferences

        if not keep_history:
            self.detail_settings = validate_preferences(state.get("detail_dock"))
        self.number_references = deepcopy(state.get("number_references", []))
        from .analytics_data import validate_ui_state

        self.analytics_ui = validate_ui_state(state.get("analytics_ui"))
        from .candidate_page import validate_candidate_state

        self.candidate_ui = validate_candidate_state(state.get("candidate_ui"))
        self.analysis_state = deepcopy(validate_snapshot(state.get("sales_analysis")))
        from .performance_history import validate_history

        self.performance_history = validate_history(state.get("performance_history"))
        from .analysis_settings import validate_settings

        self.analysis_settings = validate_settings(state.get("analysis_settings"))
        self.restore_presentation(state)
        self.machines = validate_machines(state["machines"])
        self.original = deepcopy(state.get("original", []))
        self.source = state.get("source", "")
        self.initialized = state.get(
            "initialized", bool(self.machines or self.original or self.source)
        )
        self.loaded_at = state.get("loaded_at", "")
        self.print_options = FloorPrintOptions.restore(state.get("print_options"))
        self.follow_today = state.get("follow_today", True)
        reference = (
            QDate.currentDate()
            if self.follow_today
            else QDate.fromString(
                state.get("reference_date", date.today().isoformat()), "yyyy-MM-dd"
            )
        )
        self._setting_day = True
        self.reference_date.setDate(reference)
        self._setting_day = False
        self.display_mode.blockSignals(True)
        self.display_mode.setCurrentIndex(
            self.display_mode.findData(
                state.get("display_mode", "compact") if "presentation" in state else "compact"
            )
        )
        self.display_mode.blockSignals(False)
        self._zoom = state.get("zoom", 100)
        self.show_methods.blockSignals(True)
        self.show_methods.setChecked(state.get("show_methods", False))
        self.show_methods.blockSignals(False)
        self.snap.blockSignals(True)
        self.snap.setChecked(state.get("snap", True))
        self.snap.blockSignals(False)
        if not keep_history:
            self.history.clear()
        self.rebuild()
        if not keep_history:
            self.detail_controller.restore()
        if self._zoom is None:
            self.fit(save=False)
        else:
            self.set_zoom(self._zoom, save=False)
        self._view_center = deepcopy(state.get("view_center"))
        self.restore_view_center()
        self.update_print_note()
        self.changed.emit()
        if t:
            t.configure_watch()
            if t.settings["mode"] != "live" and t.settings["folder"]:
                t.apply(t.settings["mode"], t.settings["start"], t.settings["end"])

    def rebuild(self):
        self._syncing_selection = True
        self.refresh_assignments()
        previous = getattr(self, "nodes", {})
        current = {m.uid for m in self.machines}
        for uid in set(previous) - current:
            self.scene.removeItem(previous.pop(uid))
        self._projecting = True
        try:
            for m in self.machines:
                if m.uid in previous:
                    previous[m.uid].machine = m
                else:
                    previous[m.uid] = MachineNode(self, m)
                    self.scene.addItem(previous[m.uid])
        finally:
            self._projecting = False
        self.nodes = previous
        self._syncing_selection = False
        self.selection_state.set(self.selection_state.selected_machine_keys & set(self.nodes))
        self.apply_shared_selection()
        self.apply_presentation()
        self.update_scene_rect()
        self.update_info()

    def update_scene_rect(self):
        rect = self.machine_bounds().adjusted(-60, -60, 300, 300)
        self.scene.setSceneRect(rect if self.machines else QRectF(0, 0, 1500, 1000))

    def update_info(self):
        unresolved = sum(not machine_key(m) for m in self.machines)
        self.overlaps = overlapping(self.machines)
        self.apply_presentation()
        text = (
            f"表示 {len(self.visible_nodes())}/{len(self.machines)}台 ／ 番号未設定 {unresolved}台"
        )
        if self.presentation["legend"]:
            text += "　" + filter_legend(
                self.presentation["color_filter"],
                self.analysis_settings,
                self._effective_metric_settings.get("_sales_bounds"),
            )
        if self.overlaps:
            text += (
                f"　重なり {len(self.overlaps)}台：ドラッグまたはカードサイズで調整してください。"
            )
        self.info.setText(text)
        self.update_analysis_note()
        self.dashboard.refresh()
        self.info.setToolTip(self.data_note.text())
        self.data_note.setText(
            "売上等は現在の読込値です。基準日は投入日数だけに適用。"
            + (f" 読込 {self.loaded_at[:16]}" if self.loaded_at else "（履歴なし）")
        )
        for node in self.nodes.values():
            node.update()
        self.refresh_frames()
        self.scene.update()

    def sync(self):
        self.refresh_assignments()
        self.dashboard.refresh()
        self.apply_presentation()
        for node in self.nodes.values():
            node.update_handles()
            node.update()
        self.refresh_frames()
        self.scene.update()
        self.changed.emit()

    def record(self, before, text):
        after = self.state()
        if before != after:
            self.history.push(StateCommand(self, before, after, text))
            self.update_scene_rect()
            self.changed.emit()
            self.update_info()

    def schedule_save(self, *_):
        self.timer.start(800)
        if hasattr(self, "scene"):
            self.update_info()

    def autosave(self):
        try:
            self.storage.parent.mkdir(parents=True, exist_ok=True)
            save_layout(self.storage, self.state())
            self.message.emit("売り場レイアウトを自動保存しました。")
        except Exception as exc:
            self.message.emit(f"売り場レイアウトの自動保存に失敗しました：{exc}")

    def machine_bounds(self, selected=False):
        nodes = (
            [n for n in self.visible_nodes() if n.isSelected()]
            if selected
            else self.visible_nodes()
        )
        if not nodes:
            return QRectF(0, 0, 1500, 1000)
        rect = nodes[0].sceneBoundingRect()
        for node in nodes[1:]:
            rect = rect.united(node.sceneBoundingRect())
        return rect

    def fit(self, *_, save=True):
        if not self.machines or self._fitting:
            return
        self._fitting = True
        try:
            self._zoom = None
            self.view.fitInView(
                self.machine_bounds().adjusted(-20, -20, 20, 20), Qt.AspectRatioMode.KeepAspectRatio
            )
            self.update_zoom_text()
        finally:
            self._fitting = False
        self.capture_view(False)
        if save:
            self.schedule_save()
            self.changed.emit()

    def set_zoom(self, value, save=True):
        value = max(50, min(300, float(value)))
        current = self.view.transform().m11()
        self._zoom = value
        self.view.scale(value / 100 / current, value / 100 / current)
        self.update_zoom_text()
        self.capture_view(False)
        if save:
            self.schedule_save()
            self.changed.emit()

    def update_zoom_text(self):
        self.zoom_combo.blockSignals(True)
        self.zoom_combo.setCurrentText(
            ("全体 " if self._zoom is None else "") + f"{self.view.transform().m11()*100:.0f}%"
        )
        self.zoom_combo.blockSignals(False)

    def zoom_entered(self, *_):
        text = self.zoom_combo.currentText().replace("%", "").strip()
        if text.startswith("全体"):
            return
        try:
            value = float(text)
            if not 50 <= value <= 300:
                raise ValueError()
            self.set_zoom(value)
        except ValueError:
            self.message.emit("表示倍率は50〜300%です。全体表示は売り場全体に合わせます。")
            self.update_zoom_text()

    def zoom_selected(self):
        if not self.selected():
            self.message.emit("ズームするマシンを選択してください。")
            return
        rect = self.machine_bounds(True).adjusted(-15, -15, 15, 15)
        scale = min(
            self.view.viewport().width() / rect.width(),
            self.view.viewport().height() / rect.height(),
        )
        self.set_zoom(max(50, min(300, scale * 100)))
        self.view.centerOn(rect.center())
        self.capture_view()

    def reference_day(self):
        return self.reference_date.date().toPython()

    def reference_changed(self, *_):
        if self._setting_day:
            return
        self.follow_today = False
        self.display_changed()

    def today(self):
        self.follow_today = True
        self._setting_day = True
        self.reference_date.setDate(QDate.currentDate())
        self._setting_day = False
        self.display_changed()

    def check_day(self):
        today = QDate.currentDate()
        if today != self._last_today:
            self._last_today = today
            if self.follow_today:
                self.today()

    def display_changed(self, *_):
        if not hasattr(self, "nodes"):
            return
        self.sync()
        self.schedule_save()
        self.changed.emit()

    def metric_settings(self):
        return getattr(self, "_effective_metric_settings", self.analysis_settings)

    def card_context(self, m):
        cache_key = (m.uid, self.presentation["color_filter"])
        cached = getattr(self, "_card_context_cache", {}).get(cache_key)
        if cached is not None:
            return cached
        plans = self.assignments().get(machine_key(m), [])
        priority = any(p.priority for p in plans)
        background, status = filter_status(
            m,
            self.presentation["color_filter"],
            self.reference_day(),
            bool(plans),
            priority,
            self.metric_settings(),
        )
        mode = self.display_mode.currentData()
        if mode == "product":
            status = ("投入予定" if plans else "通常") + (" / 優先入替" if priority else "")
        if days_text(m, self.reference_day()) == "投入予定":
            status += " / 投入予定（日付）"
        if m.uid in self.overlaps:
            status += " / 重なり"
        methods = (
            "／".join(
                dict.fromkeys(p.machine_methods.get(machine_key(m)) or "未設定" for p in plans)
            )
            if self.show_methods.isChecked()
            else ""
        )
        result = (
            background,
            status,
            priority,
            (
                FILTER_FIELDS[self.presentation["color_filter"]]
                if mode == "compact"
                else DISPLAY_FIELDS[mode]
            ),
            (self.presentation["font"] if self.presentation["mode"] != "actual" else m.font_size)
            * (1.35 if mode == "product" else 1.1 if mode == "analysis" else 1),
            methods,
        )
        self._card_context_cache[cache_key] = result
        return result

    def apply_shared_selection(self):
        if not hasattr(self, "scene") or self._syncing_selection:
            return
        self._syncing_selection = True
        self.scene.blockSignals(True)
        for uid, node in self.nodes.items():
            node.setSelected(uid in self.selection_state.selected_machine_keys)
            node.update_handles()
        self.scene.blockSignals(False)
        self._syncing_selection = False
        self.view.viewport().setCursor(
            Qt.CursorShape.CrossCursor
            if self.selection_state.selection_mode == "range"
            else Qt.CursorShape.ArrowCursor
        )
        self.view.viewport().update()

    def selection_changed(self):
        if self._syncing_selection:
            return
        if hasattr(self, "nodes"):
            self.selection_state.set(
                item.machine.uid
                for item in self.scene.selectedItems()
                if isinstance(item, MachineNode)
            )
            self.refresh_frames()
            self.view.viewport().update()

    def refresh_frames(self):
        self.page_frames = []
        if (
            self.machines
            and self.print_options.boundaries
            and self.print_options.mode in ("tile", "actual")
        ):
            try:
                self.page_frames = plan_floor(
                    self.print_machines(),
                    self.assignments(),
                    self.print_options,
                    self.reference_day(),
                    self.print_renderer,
                    [m.uid for m in self.selected()],
                    loaded_at=self.print_data_timestamp(),
                    population=self.machines,
                    snapshot=self.analysis_state,
                ).frames
            except AppError:
                pass

    def update_print_note(self):
        mode = {
            "fit": "全体1枚",
            "tile": "分割",
            "selection": "選択範囲",
            "actual": "指定倍率",
            "list": "マシン一覧表",
        }[self.print_options.mode]
        self.print_note.setText(
            f"{self.print_options.paper} / {'横' if self.print_options.orientation=='landscape' else '縦'} / {mode}（画面倍率とは独立）"
        )

    def set_print_options(self, options):
        self.print_options = options.validate()
        self.update_print_note()
        self.schedule_save()
        self.changed.emit()

    def card_settings(self):
        selected = self.selected()
        targets = selected or self.machines
        if not targets:
            self.message.emit("マシンを追加・読込してから設定してください。")
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("カード・文字サイズ")
        form = QFormLayout(dialog)
        scope = QComboBox()
        scope.addItem(f"選択中の{len(selected)}台", True) if selected else None
        scope.addItem(f"全{len(self.machines)}台", False)
        form.addRow("対象", scope)
        size = QCheckBox("カードサイズを変更")
        font = QCheckBox("文字サイズを変更")
        font.setChecked(True)
        form.addRow(size)
        w = QDoubleSpinBox()
        w.setRange(80, 2000)
        w.setValue(targets[0].width)
        h = QDoubleSpinBox()
        h.setRange(60, 2000)
        h.setValue(targets[0].height)
        form.addRow("幅", w)
        form.addRow("高さ", h)
        form.addRow(font)
        f = QDoubleSpinBox()
        f.setRange(6, 72)
        f.setValue(targets[0].font_size)
        form.addRow("基準文字サイズ", f)
        hint = QLabel(
            "座標は変更しません。文字はカード内に収まるよう調整します。拡大後に重なる場合は警告し、ドラッグで調整できます。"
        )
        hint.setWordWrap(True)
        form.addRow(hint)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        form.addRow(buttons)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            ids = (
                [m.uid for m in selected] if scope.currentData() else [m.uid for m in self.machines]
            )
            self.apply_card_settings(
                ids,
                w.value() if size.isChecked() else None,
                h.value() if size.isChecked() else None,
                f.value() if font.isChecked() else None,
            )

    def apply_card_settings(self, ids, width=None, height=None, font=None):
        if getattr(self, "temporal", None) and self.temporal.active:
            self.message.emit(
                "配置の編集は『現在の編集データ』で行ってください。履歴の配置は変更していません。"
            )
            return False
        before = self.state()
        updated = deepcopy(self.machines)
        for m in updated:
            if m.uid in ids:
                if width is not None:
                    m.width = width
                if height is not None:
                    m.height = height
                if font is not None:
                    m.font_size = font
        validate_machines([asdict(m) for m in updated])
        self.machines = updated
        self.rebuild()
        self.record(before, "カード・文字サイズ変更")
        if self.overlaps:
            self.message.emit(
                f"拡大したカードが重なっています（{len(self.overlaps)}台）。座標は保持しています。ドラッグで調整してください。"
            )

    def selected(self):
        return [
            item.machine for item in self.scene.selectedItems() if isinstance(item, MachineNode)
        ]

    def add_machine(self, machine):
        if getattr(self, "temporal", None) and self.temporal.active:
            self.message.emit(
                "配置の編集は『現在の編集データ』で行ってください。履歴の配置は変更していません。"
            )
            return False
        validate_machines([asdict(m) for m in self.machines] + [asdict(machine)])
        if machine_key(machine) and any(
            machine_key(m) == machine_key(machine) for m in self.machines
        ):
            raise AppError("同じマシン番号が登録済みです。")
        before = self.state()
        self.initialized = True
        self.machines.append(machine)
        self.rebuild()
        self.selection_state.set({machine.uid})
        self.record(before, "マシン追加")
        self.view.centerOn(self.nodes[machine.uid])
        self.capture_view(False)

    def machine_dialog(self, machine, title):
        initial_data = deepcopy(machine)
        dlg = QDialog(self)
        dlg.setWindowTitle(title)
        form = QFormLayout(dlg)
        number = QLineEdit()
        side = QComboBox()
        side.addItems(["指定なし", "左", "右"])
        initial_number = machine_key(machine)
        match = re.fullmatch(r"(\d+)([左右]?)", initial_number)
        if match:
            number.setText(match[1])
            side.setCurrentText(match[2] or "指定なし")
        kind = QComboBox()
        kind.setEditable(True)
        kind.addItems(["クレーン", "2本爪", "3本爪", "その他"])
        kind.setCurrentText(machine.kind)
        product = QLineEdit(machine.product)
        error = QLabel()
        form.addRow("マシン番号（半角数字）", number)
        form.addRow("左右指定", side)
        form.addRow("マシン種別", kind)
        form.addRow("現在の商品名", product)
        sales = QLineEdit("" if machine.sales is None else str(machine.sales))
        sales.setPlaceholderText("未入力は —")
        average = QLineEdit("" if machine.average is None else str(machine.average))
        average.setPlaceholderText("未入力・0の場合は比率を計算しません")
        ratio_input = QLineEdit("" if machine.ratio is None else str(machine.ratio))
        ratio_input.setPlaceholderText("空欄なら売上÷アベレージから計算")
        installed = QLineEdit(machine.installed_on or "")
        installed.setPlaceholderText("YYYY/MM/DD、未入力可")
        if machine.analysis:
            form.addRow(QLabel("以下は従来データです。売上分析Excelの実績値が優先されます。"))
        form.addRow("売上", sales)
        # Average sales is retained internally, temporarily excluded from editing UI.
        form.addRow("アベ比（%）", ratio_input)
        form.addRow("設置日", installed)
        form.addRow(error)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        form.addRow(buttons)

        def accept():
            value = number.text().strip() + (side.currentText() if side.currentIndex() else "")
            try:
                if not value or machine_numbers(value) != [value]:
                    raise AppError("番号を入力してください。")
                if any(m.uid != machine.uid and machine_key(m) == value for m in self.machines):
                    raise AppError("同じマシン番号が登録済みです。左右指定も確認してください。")
                values = []
                for label, control in (
                    ("売上", sales),
                    ("アベレージ", average),
                    ("アベ比", ratio_input),
                ):
                    parsed = numeric(control.text())
                    if control.text().strip() and parsed is None:
                        raise AppError(f"{label}は数値または空欄で指定してください。")
                    values.append(parsed)
                if values[1] is not None and values[1] < 0:
                    raise AppError("アベレージは0以上で指定してください。")
                installation = parse_date(installed.text())
                if installed.text().strip() and installation is None:
                    raise AppError("設置日を日付形式で入力してください。")
                if initial_number != value:
                    machine.number = value
                    machine.number_origin = "direct"
                    machine.manual_number = None
                    machine.resolved_number = ""
                    machine.direct_number_raw = value
                machine.kind, machine.product = kind.currentText(), product.text()
                machine.sales, machine.average, machine.ratio = values
                machine.ratio_from_excel = bool(ratio_input.text().strip())
                machine.installed_on = installation.isoformat() if installation else None
                from .machine_matching import mark_manual_updates

                mark_manual_updates(machine, initial_data)
                dlg.accept()
            except AppError as exc:
                error.setText(str(exc))

        buttons.accepted.connect(accept)
        buttons.rejected.connect(dlg.reject)
        return dlg.exec() == QDialog.DialogCode.Accepted

    def add_dialog(self):
        center = self.view.mapToScene(self.view.viewport().rect().center())
        m = Machine(
            x=max(0, round(center.x() / GRID) * GRID), y=max(0, round(center.y() / GRID) * GRID)
        )
        if self.machine_dialog(m, "マシン追加"):
            self.add_machine(m)

    def edit_machine(self, uid):
        if getattr(self, "temporal", None) and self.temporal.active:
            self.message.emit(
                "配置の編集は『現在の編集データ』で行ってください。履歴の配置は変更していません。"
            )
            return False
        old = next(m for m in self.machines if m.uid == uid)
        updated = deepcopy(old)
        if not self.machine_dialog(updated, "マシン編集"):
            return
        from .machine_matching import mark_manual_updates

        mark_manual_updates(updated, old)
        if updated.number != old.number:
            updated.resolved_number = ""
            updated.manual_number = None
            updated.number_origin = "direct"
            updated.direct_number_raw = updated.number
        if (
            machine_key(old)
            and machine_key(old) != machine_key(updated)
            and self.related({machine_key(old)})
        ):
            QMessageBox.warning(
                self,
                "入替予定との整合性",
                "変更前のマシン番号が入替予定で使われています。予定の番号は自動変更しません。商品一覧で変更先を確認・修正してください。",
            )
        before = self.state()
        self.machines[self.machines.index(old)] = updated
        self.rebuild()
        self.record(before, "マシン編集")

    def delete_selected(self):
        if getattr(self, "temporal", None) and self.temporal.active:
            self.message.emit(
                "配置の編集は『現在の編集データ』で行ってください。履歴の配置は変更していません。"
            )
            return False
        selected = self.selected()
        if not selected:
            return
        used = self.related({machine_key(m) for m in selected if machine_key(m)})
        warning = (
            f"\n注意：{len(used)}件の入替予定で使用中です。予定のマシン番号は残ります。商品一覧で修正してください。"
            if used
            else ""
        )
        if (
            QMessageBox.question(
                self,
                "マシン削除",
                f"選択した{len(selected)}枠を削除しますか？元Excelは変更しません。{warning}",
            )
            != QMessageBox.StandardButton.Yes
        ):
            return
        before = self.state()
        ids = {m.uid for m in selected}
        self.machines = [m for m in self.machines if m.uid not in ids]
        self.rebuild()
        self.record(before, "マシン削除")
        if used:
            self.message.emit(
                "削除したマシンが入替予定に残っています。商品一覧で確認してください。"
            )

    def duplicate_selected(self):
        if getattr(self, "temporal", None) and self.temporal.active:
            self.message.emit(
                "配置の編集は『現在の編集データ』で行ってください。履歴の配置は変更していません。"
            )
            return False
        selected = self.selected()
        if not selected:
            return
        before = self.state()
        clones = [
            Machine(
                kind=m.kind,
                product=m.product,
                x=m.x + GRID,
                y=m.y + GRID,
                width=m.width,
                height=m.height,
                font_size=m.font_size,
            )
            for m in selected
        ]
        self.machines.extend(clones)
        self.rebuild()
        self.selection_state.set(m.uid for m in clones)
        self.record(before, "マシン複製")
        self.message.emit(
            "複製しました。番号は未設定です。ダブルクリックで新しい番号を登録してください。"
        )

    def align_selected(self, mode):
        if getattr(self, "temporal", None) and self.temporal.active:
            self.message.emit(
                "配置の編集は『現在の編集データ』で行ってください。履歴の配置は変更していません。"
            )
            return False
        self.align.setCurrentIndex(0)
        selected = self.selected()
        if mode == 0 or len(selected) < 2:
            return
        before = self.state()
        if mode in (1, 2):
            value = min(m.x if mode == 1 else m.y for m in selected)
            for m in selected:
                if mode == 1:
                    m.x = value
                else:
                    m.y = value
        elif mode in (3, 4) and len(selected) >= 3:
            axis, size = ("x", "width") if mode == 3 else ("y", "height")
            selected.sort(key=lambda m: getattr(m, axis))
            start = getattr(selected[0], axis)
            end = getattr(selected[-1], axis) + getattr(selected[-1], size)
            gap = (end - start - sum(getattr(m, size) for m in selected)) / (len(selected) - 1)
            if gap < 0:
                self.message.emit(
                    "重なるマシンは等間隔にできません。先に両端の間隔を広げてください。"
                )
                return
            pos = start
            for m in selected:
                setattr(m, axis, pos)
                pos += getattr(m, size) + gap
        self.rebuild()
        self.record(before, "マシン整列")

    def import_path(self, path, preserve=False):
        machines, warnings, references = import_layout(path, with_references=True)
        return self.apply_layout_import(path, machines, warnings, references, preserve)

    def apply_layout_import(self, path, machines, warnings, references, preserve=False):
        if getattr(self, "temporal", None) and self.temporal.active:
            if not self.temporal.to_live():
                return False
        validate_machines([asdict(m) for m in machines])
        from .machine_matching import validate_references

        validate_references(references)
        before = self.state()
        self.number_references = references
        self.initialized = True
        self.source = str(path)
        self.loaded_at = datetime.now().astimezone().isoformat(timespec="minutes")
        self.original = [asdict(m) for m in machines]
        if not preserve:
            self.machines = machines
        else:

            def unique(field):
                result = {}
                duplicates = set()
                for m in machines:
                    key = getattr(m, field)
                    if key:
                        if key in result:
                            duplicates.add(key)
                        result[key] = m
                return {key: value for key, value in result.items() if key not in duplicates}

            by_key, by_number = unique("source_key"), unique("number")
            for m in self.machines:
                fresh = by_key.get(m.source_key) or by_number.get(m.number)
                if fresh:
                    for field in (
                        "product",
                        "sales",
                        "average",
                        "ratio",
                        "ratio_from_excel",
                        "installed_on",
                        "maker",
                        "po_rate",
                        "units",
                        "cases",
                        "limit",
                        "operation_method",
                        "reference_average",
                        "reference_number",
                        "reference_warning",
                        "number_origin",
                        "layout_loaded_at",
                        "direct_number_raw",
                    ):
                        setattr(m, field, getattr(fresh, field))
        self.rebuild()
        if not preserve:
            self.set_zoom(100, save=False)
            if self.visible_nodes():
                self.view.centerOn(self.visible_nodes()[0])
            self.capture_view(False)
        else:
            self.restore_view_center()
        self.record(before, "レイアウトExcel読込")
        self.message.emit(f"{len(machines)}枠を読み込みました。" + " ".join(warnings))
        return warnings

    def import_dialog(self):
        path, _ = QFileDialog.getOpenFileName(self, "レイアウトExcel", "", "Excel (*.xlsx *.xlsm)")
        if not path:
            return
        preserve = False
        if self.machines:
            result = QMessageBox.question(
                self,
                "配置の維持",
                "現在の編集済み配置を維持しますか？\n「はい」：位置・サイズ・追加マシンを保持し、対応枠の商品・稼働値を更新\n「いいえ」：Excelの配置に置換",
                QMessageBox.StandardButton.Yes
                | QMessageBox.StandardButton.No
                | QMessageBox.StandardButton.Cancel,
            )
            if result == QMessageBox.StandardButton.Cancel:
                return
            preserve = result == QMessageBox.StandardButton.Yes
        try:
            warnings = self.import_path(path, preserve)
            if warnings:
                QMessageBox.warning(self, "レイアウト読込時の確認", "\n".join(warnings))
        except Exception as exc:
            QMessageBox.warning(self, "レイアウト読込エラー", str(exc))

    def reset_original(self):
        if getattr(self, "temporal", None) and self.temporal.active:
            self.message.emit(
                "配置の編集は『現在の編集データ』で行ってください。履歴の配置は変更していません。"
            )
            return False
        if not self.original:
            return
        if (
            QMessageBox.question(
                self, "元の配置に戻す", "追加・削除・移動した配置を元のExcel配置に戻しますか？"
            )
            != QMessageBox.StandardButton.Yes
        ):
            return
        before = self.state()
        self.machines = validate_machines(self.original)
        self.rebuild()
        self.record(before, "元のExcel配置へ戻す")
        self.fit()

    def save_dialog(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "配置を保存", "売り場レイアウト.json", "JSON (*.json)"
        )
        if path:
            if not path.lower().endswith(".json"):
                path += ".json"
                if (
                    Path(path).exists()
                    and QMessageBox.question(self, "上書き確認", "既存の配置JSONを上書きしますか？")
                    != QMessageBox.StandardButton.Yes
                ):
                    return
            try:
                save_layout(path, self.state())
                if hasattr(self.app_window, "workflow"):
                    self.app_window.workflow.record_file("saved", path, "売り場レイアウト")
                self.message.emit("配置を保存しました。")
            except Exception as exc:
                QMessageBox.warning(self, "保存エラー", str(exc))

    def open_dialog(self):
        path, _ = QFileDialog.getOpenFileName(self, "配置JSONを開く", "", "JSON (*.json)")
        if path:
            try:
                state = json.loads(Path(path).read_text(encoding="utf-8"))
                validate_state(state)
                before = self.state()
                self.restore(state, keep_history=True)
                self.record(before, "配置JSON読込")
            except Exception as exc:
                QMessageBox.warning(self, "復元エラー", str(exc))

    def paint_floor(self, printer):
        return render_floor(
            printer,
            self.print_machines(),
            self.assignments(),
            self.print_options,
            self.reference_day(),
            self.print_renderer,
            [m.uid for m in self.selected()],
            self.print_data_timestamp(),
            population=self.machines,
            snapshot=self.analysis_state,
        )

    def print_settings(self, *_):
        dialog = QDialog(self)
        dialog.setWindowTitle("売り場の用紙・印刷設定")
        layout = QVBoxLayout(dialog)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        controls = FloorPrintControls(self.print_options)
        scroll.setWidget(controls)
        layout.addWidget(scroll)
        info = QLabel("画面表示とは独立した設定です。")
        info.setWordWrap(True)
        layout.addWidget(info)
        controls.invalid.connect(info.setText)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        layout.addWidget(buttons)

        def accept():
            try:
                self.set_print_options(controls.options())
                dialog.accept()
            except AppError as exc:
                info.setText(str(exc))

        buttons.accepted.connect(accept)
        buttons.rejected.connect(dialog.reject)
        dialog.resize(440, 850)
        dialog.exec()

    def preview(self, *_):
        if not self.machines:
            self.message.emit("印刷するマシン配置がありません。")
            return
        try:
            dialog = FloorPreviewDialog(self)
            self._active_preview = dialog
            dialog.exec()
        except Exception as exc:
            QMessageBox.warning(self, "売り場プレビュー", str(exc))
        finally:
            self._active_preview = None

    def prepare_print(self):
        plan = plan_floor(
            self.print_machines(),
            self.assignments(),
            self.print_options,
            self.reference_day(),
            self.print_renderer,
            [m.uid for m in self.selected()],
            loaded_at=self.print_data_timestamp(),
            population=self.machines,
            snapshot=self.analysis_state,
        )
        if plan.warnings:
            parent = self._active_preview or self
            if (
                QMessageBox.question(
                    parent,
                    "売り場印刷の確認",
                    "\n".join(plan.warnings) + "\n設定を保持して出力を続けますか？",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No,
                )
                != QMessageBox.StandardButton.Yes
            ):
                return None
        return plan

    def export_floor_pdf(self, *_):
        parent = self._active_preview or self
        try:
            if not self.prepare_print():
                return
            path, _ = QFileDialog.getSaveFileName(
                parent, "売り場レイアウトをPDF保存", "売り場レイアウト.pdf", "PDF (*.pdf)"
            )
            if not path:
                return
            if not path.lower().endswith(".pdf"):
                path += ".pdf"
                if (
                    Path(path).exists()
                    and QMessageBox.question(parent, "上書き確認", "既存PDFを上書きしますか？")
                    != QMessageBox.StandardButton.Yes
                ):
                    return
            sources = (self.source, getattr(self.window(), "source", ""))
            plan = save_floor_pdf(
                path,
                self.print_machines(),
                self.assignments(),
                self.print_options,
                self.reference_day(),
                self.print_renderer,
                [m.uid for m in self.selected()],
                self.print_data_timestamp(),
                sources,
                population=self.machines,
                snapshot=self.analysis_state,
            )
            self.message.emit(f"売り場PDFを保存しました（{len(plan.pages)}ページ）：{path}")
        except Exception as exc:
            QMessageBox.warning(parent, "売り場PDF保存", str(exc))

    def print_floor(self, *_):
        parent = self._active_preview or self
        try:
            plan = self.prepare_print()
            if plan is None:
                return
            printer = floor_printer(self.print_options)
            dialog = QPrintDialog(printer, parent)
            dialog.setMinMax(1, len(plan.pages))
            from PySide6.QtPrintSupport import QAbstractPrintDialog

            dialog.setOption(QAbstractPrintDialog.PrintDialogOption.PrintPageRange, True)
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return
            if not paper_supported(printer, self.print_options):
                QMessageBox.warning(
                    parent,
                    "用紙サイズ非対応",
                    f"選択したプリンターは{self.print_options.paper}に対応していません。対応用紙に変更して再プレビューしてください。",
                )
                return
            expected = self.print_options.paper_size().size(QPageSize.Unit.Millimeter)
            actual = printer.pageLayout().pageSize().size(QPageSize.Unit.Millimeter)
            orientation = (
                QPageLayout.Orientation.Landscape
                if self.print_options.orientation == "landscape"
                else QPageLayout.Orientation.Portrait
            )
            if (
                abs(expected.width() - actual.width()) > 1
                or abs(expected.height() - actual.height()) > 1
                or printer.pageLayout().orientation() != orientation
            ):
                QMessageBox.warning(
                    parent,
                    "用紙設定を確認",
                    "プリンター側の用紙がプレビューと異なります。用紙設定を合わせてから印刷してください。",
                )
                return
            self.paint_floor(printer)
            self.message.emit(
                "売り場の印刷ジョブを送信しました。用紙出力はプリンターで確認してください。"
            )
        except Exception as exc:
            QMessageBox.warning(parent, "売り場印刷", str(exc))
