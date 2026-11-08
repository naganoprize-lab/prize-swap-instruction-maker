"""Shared, persistent-ID selection; presentation colors and placement stay independent."""

from datetime import datetime, timezone
from PySide6.QtCore import QObject, Signal, Slot, Qt, QRect
from PySide6.QtWidgets import QWidget, QHBoxLayout, QComboBox, QToolButton, QLabel, QRubberBand


class SelectionState(QObject):
    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.selected_machine_keys = set()
        self.selection_mode = "normal"
        self.selection_version = 0
        self.last_updated = ""
        self.range_rule = "intersects"

    def set(self, keys):
        keys = set(keys)
        if keys == self.selected_machine_keys:
            return
        self.selected_machine_keys = keys
        self.selection_version += 1
        self.last_updated = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self.changed.emit()

    def set_mode(self, mode):
        if mode not in ("normal", "range", "individual"):
            raise ValueError("選択モードが不正です。")
        self.selection_mode = mode
        self.changed.emit()


def selection_controls(floor):
    row = QWidget()
    from .control_flow import ControlFlow

    layout = ControlFlow(row)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(4)
    mode = QComboBox()
    for label, key in [("通常操作", "normal"), ("範囲選択", "range"), ("個別選択", "individual")]:
        mode.addItem(label, key)
    mode.setCurrentIndex(mode.findData(floor.selection_state.selection_mode))
    mode.currentIndexChanged.connect(lambda *_: floor.selection_state.set_mode(mode.currentData()))
    layout.addWidget(mode)
    rule = QComboBox()
    rule.addItem("重なった台", "intersects")
    rule.addItem("中心点のみ", "center")
    rule.currentIndexChanged.connect(
        lambda *_: setattr(floor.selection_state, "range_rule", rule.currentData())
    )
    layout.addWidget(rule)
    for label, callback in [
        ("全選択", lambda: floor.selection_state.set(m.uid for m in floor.machines)),
        ("選択解除", lambda: floor.selection_state.set([])),
        ("選択台を売上分析", lambda: open_analysis(floor)),
        (
            "選択台を候補へ追加",
            lambda: __import__(
                "prize_swap.candidate_page", fromlist=["register_selected_candidates"]
            ).register_selected_candidates(floor),
        ),
    ]:
        b = QToolButton()
        b.setText(label)
        b.clicked.connect(callback)
        layout.addWidget(b)
    count = QLabel()
    layout.addWidget(count)

    def refresh():
        mode.blockSignals(True)
        mode.setCurrentIndex(mode.findData(floor.selection_state.selection_mode))
        mode.blockSignals(False)
        count.setText(f"選択中：{len(floor.selection_state.selected_machine_keys)}台")

    class UpdateReceiver(QObject):
        @Slot()
        def apply(self):
            refresh()

    row.selection_receiver = UpdateReceiver(row)
    floor.selection_state.changed.connect(row.selection_receiver.apply)
    refresh()
    return row


def open_analysis(floor):
    window = floor.window()
    if not hasattr(window, "analysis_page") and floor._fullscreen_host:
        floor.toggle_fullscreen()
        window = floor.window()
    if hasattr(window, "analysis_page"):
        window.analysis_page.use_layout_selection(explicit=True)
        window.tabs.setCurrentWidget(window.analysis_page)


def begin_drag(view, event):
    state = view.owner.selection_state
    if event.button() != Qt.MouseButton.LeftButton:
        return False
    if state.selection_mode == "range" or event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
        view._selection_start = event.position().toPoint()
        view._selection_add = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
        view._selection_band = QRubberBand(QRubberBand.Shape.Rectangle, view.viewport())
        view._selection_band.setGeometry(QRect(view._selection_start, view._selection_start))
        view._selection_band.show()
        event.accept()
        return True
    if (
        state.selection_mode == "individual"
        or event.modifiers() & Qt.KeyboardModifier.ControlModifier
    ):
        item = view.itemAt(event.position().toPoint())
        while item is not None and not hasattr(item, "machine"):
            item = item.parentItem()
        if item is not None:
            keys = set(state.selected_machine_keys)
            uid = item.machine.uid
            keys.symmetric_difference_update({uid})
            state.set(keys)
        event.accept()
        return True
    return False


def end_drag(view, event):
    if not hasattr(view, "_selection_start"):
        return False
    rectangle = QRect(view._selection_start, event.position().toPoint()).normalized()
    region = view.mapToScene(rectangle).boundingRect()
    state = view.owner.selection_state
    selected = set(state.selected_machine_keys) if view._selection_add else set()
    for uid, node in view.owner.nodes.items():
        box = node.sceneBoundingRect()
        if node.isVisible() and (
            region.contains(box.center())
            if state.range_rule == "center"
            else region.intersects(box)
        ):
            selected.add(uid)
    view._selection_band.hide()
    view._selection_band.deleteLater()
    del view._selection_start
    state.set(selected)
    event.accept()
    return True
