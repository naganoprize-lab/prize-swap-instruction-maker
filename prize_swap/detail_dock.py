"""A page-local QMainWindow owns a reusable, non-modal machine editor dock."""

from copy import deepcopy
from dataclasses import asdict, fields
from PySide6.QtCore import Qt, QEvent, QTimer
from PySide6.QtWidgets import (
    QMainWindow,
    QDockWidget,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QComboBox,
    QPushButton,
    QMessageBox,
    QApplication,
)
from .core import AppError


def validate_preferences(value=None):
    s = dict(position="right", ratio=0.5, geometry=None, open=False, machine_uid="")
    if value is not None and (not isinstance(value, dict) or set(value) - set(s)):
        raise AppError("詳細ドックの保存形式が不正です。")
    s.update(value or {})
    if (
        s["position"] not in ("left", "right", "floating")
        or type(s["ratio"]) not in (int, float)
        or not 0.15 <= s["ratio"] <= 0.85
        or type(s["open"]) is not bool
        or not isinstance(s["machine_uid"], str)
    ):
        raise AppError("詳細ドックの設定が不正です。")
    g = s["geometry"]
    if g is not None and (
        not isinstance(g, list)
        or len(g) != 4
        or any(type(v) is not int or abs(v) > 100000 for v in g)
        or g[2] < 250
        or g[3] < 250
    ):
        raise AppError("独立詳細ウィンドウの位置が不正です。")
    return s


class DetailHost(QMainWindow):
    def showEvent(self, event):
        super().showEvent(event)
        if hasattr(self, "controller"):
            QTimer.singleShot(0, self.controller.apply_ratio)


class DetailDock(QDockWidget):
    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "controller"):
            QTimer.singleShot(0, self.controller.capture)

    def moveEvent(self, event):
        super().moveEvent(event)
        if hasattr(self, "controller") and self.isFloating():
            QTimer.singleShot(0, self.controller.capture)

    def closeEvent(self, event):
        if self.controller.protect_edits():
            self.controller.owner.detail_settings["open"] = False
            super().closeEvent(event)
            self.controller.capture()
        else:
            event.ignore()


class DetailDockController:
    def __init__(self, owner, view, layout):
        self.owner = owner
        self.panel = None
        self._restoring = False
        self._saving = False
        self._context = None
        self.host = DetailHost(owner)
        self.host.controller = self
        self.host.setWindowFlags(Qt.WindowType.Widget)
        self.host.setObjectName("floorDetailHost")
        self.host.setDockNestingEnabled(True)
        self.host.setDockOptions(
            QMainWindow.DockOption.AllowNestedDocks | QMainWindow.DockOption.AllowTabbedDocks
        )
        self.host.setCentralWidget(view)
        layout.addWidget(self.host, 1)
        self.dock = DetailDock("マシン詳細", self.host)
        self.dock.controller = self
        self.dock.setObjectName("machineDetailDock")
        self.dock.setAllowedAreas(
            Qt.DockWidgetArea.LeftDockWidgetArea | Qt.DockWidgetArea.RightDockWidgetArea
        )
        self.container = QWidget()
        outer = QVBoxLayout(self.container)
        outer.setContentsMargins(3, 3, 3, 3)
        row = QHBoxLayout()
        outer.addLayout(row)
        self.position = QComboBox()
        for name, key in [("右側", "right"), ("左側", "left"), ("独立ウィンドウ", "floating")]:
            self.position.addItem(name, key)
        row.addWidget(self.position)
        row.addStretch()
        close = QPushButton("閉じる")
        close.clicked.connect(self.dock.close)
        row.addWidget(close)
        self.body = outer
        self.dock.setWidget(self.container)
        self.host.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.dock)
        self.dock.hide()
        self.position.currentIndexChanged.connect(
            lambda *_: self.set_position(self.position.currentData())
        )
        self.dock.dockLocationChanged.connect(self.location_changed)
        self.dock.topLevelChanged.connect(lambda *_: self.capture())
        self.dock.visibilityChanged.connect(lambda *_: self.capture())
        owner.changed.connect(self.refresh_current)

    def location_changed(self, area):
        if not self._restoring:
            if not self.dock.isFloating():
                self.owner.detail_settings["position"] = (
                    "left" if area == Qt.DockWidgetArea.LeftDockWidgetArea else "right"
                )
            self.capture()

    def capture(self):
        if self._restoring:
            return
        s = self.owner.detail_settings
        s["open"] = not self.dock.isHidden()
        s["position"] = (
            "floating"
            if self.dock.isFloating()
            else s["position"] if s["position"] != "floating" else "right"
        )
        if self.dock.isFloating():
            s["geometry"] = [
                self.dock.x(),
                self.dock.y(),
                max(250, self.dock.width()),
                max(250, self.dock.height()),
            ]
        elif self.host.isVisible() and self.host.width() > 600 and s["open"]:
            s["ratio"] = max(0.15, min(0.85, self.dock.width() / self.host.width()))
        self.position.blockSignals(True)
        self.position.setCurrentIndex(self.position.findData(s["position"]))
        self.position.blockSignals(False)
        self.owner.schedule_save()

    def apply_ratio(self):
        if not self.dock.isFloating() and self.owner.detail_settings["open"]:
            self._restoring = True
            self.host.resizeDocks(
                [self.dock],
                [int(self.host.width() * self.owner.detail_settings["ratio"])],
                Qt.Orientation.Horizontal,
            )
            self._restoring = False
            self.capture()

    def set_position(self, position):
        self._restoring = True
        self.owner.detail_settings["position"] = position
        if position == "floating":
            self.dock.setFloating(True)
            g = self.owner.detail_settings["geometry"]
            if g:
                from PySide6.QtCore import QRect

                rect = QRect(*g)
                screen = next(
                    (
                        screen
                        for screen in QApplication.screens()
                        if screen.availableGeometry().intersects(rect)
                    ),
                    self.owner.screen(),
                )
                area = screen.availableGeometry()
                width = min(g[2], area.width())
                height = min(g[3], area.height())
                self.dock.setGeometry(
                    max(area.left(), min(g[0], area.right() - width)),
                    max(area.top(), min(g[1], area.bottom() - height)),
                    width,
                    height,
                )
            else:
                area = self.owner.screen().availableGeometry()
                self.dock.resize(min(800, area.width() - 30), min(800, area.height() - 50))
                self.dock.move(area.topLeft())
        else:
            self.dock.setFloating(False)
            self.host.addDockWidget(
                (
                    Qt.DockWidgetArea.LeftDockWidgetArea
                    if position == "left"
                    else Qt.DockWidgetArea.RightDockWidgetArea
                ),
                self.dock,
            )
            self.host.resizeDocks(
                [self.dock],
                [max(300, int(self.host.width() * self.owner.detail_settings["ratio"]))],
                Qt.Orientation.Horizontal,
            )
        self.position.blockSignals(True)
        self.position.setCurrentIndex(self.position.findData(position))
        self.position.blockSignals(False)
        self._restoring = False
        self.capture()

    def protect_edits(self):
        if self.panel is None or not self.panel.is_dirty():
            return True
        answer = QMessageBox.question(
            self.dock,
            "未保存のマシン編集",
            "変更を保存して続けますか？",
            QMessageBox.StandardButton.Save
            | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Save,
        )
        if answer == QMessageBox.StandardButton.Cancel:
            return False
        if answer == QMessageBox.StandardButton.Save:
            self.panel.accept()
            return not self.panel.is_dirty()
        machine = next((m for m in self.owner.machines if m.uid == self.panel.original.uid), None)
        if machine:
            self.panel.load_machine(machine)
        return True

    def show_machine(self, uid):
        if self.panel and self.panel.original.uid != uid and not self.protect_edits():
            return False
        machine = next((m for m in self.owner.machines if m.uid == uid), None)
        if machine is None:
            return False
        from .floor_details import MachineDetailPanel

        if self.panel is None:
            self.panel = MachineDetailPanel(self.owner, machine)
            self.panel.setMinimumWidth(320)
            self.body.addWidget(self.panel, 1)
            self.panel.accepted.connect(self.save)
            self.panel.rejected.connect(self.cancel)
        elif self.panel.original.uid != uid or not self.panel.is_dirty():
            self.panel.load_machine(machine)
        self._context = self.calculation_context()
        self.owner.capture_view(False)
        self.owner.detail_settings["machine_uid"] = uid
        was_hidden = self.dock.isHidden()
        saved = deepcopy(self.owner.detail_settings)
        self._restoring = True
        self.dock.show()
        self._restoring = False
        self.owner.detail_settings = saved
        if was_hidden:
            self.set_position(saved["position"])
        self.capture()
        QTimer.singleShot(0, self.owner.restore_view_center)
        return True

    def cancel(self):
        machine = next((m for m in self.owner.machines if m.uid == self.panel.original.uid), None)
        if machine:
            self.panel.load_machine(machine)

    def save(self):
        if self._saving:
            return
        self._saving = True
        try:
            from .floor_details import DetailCommand

            p = self.panel
            o = self.owner
            current = next((m for m in o.machines if m.uid == p.original.uid), None)
            if current is None:
                return
            before = o.state()
            changes = []
            replacement = deepcopy(current)
            for f in fields(type(current)):
                if getattr(p.updated, f.name) != getattr(p.baseline, f.name):
                    setattr(replacement, f.name, deepcopy(getattr(p.updated, f.name)))
            for editor in p.products:
                values = {
                    key: (
                        deepcopy(getattr(editor.source, key)),
                        deepcopy(getattr(editor.draft, key)),
                    )
                    for key in (
                        "name",
                        "maker",
                        "units",
                        "cases",
                        "limit",
                        "methods",
                        "planned_booths",
                        "machine_methods",
                        "retired_machine_methods",
                    )
                    if getattr(editor.source, key) != getattr(editor.draft, key)
                }
                if values:
                    changes.append((editor.source, values))
            o.machines[o.machines.index(current)] = replacement
            p.apply_products()
            after = o.state()
            if before != after or changes:
                o.history.push(DetailCommand(o, before, after, changes))
            o.rebuild()
            o.product_edited.emit()
            o.sync()
            o.schedule_save()
            p.load_machine(next(m for m in o.machines if m.uid == replacement.uid))
        finally:
            self._saving = False

    def calculation_context(self):
        return deepcopy(self.owner.analysis_settings), self.owner.reference_day()

    def refresh_current(self, *_):
        if self._saving or self._restoring or self.panel is None:
            return
        machine = next((m for m in self.owner.machines if m.uid == self.panel.original.uid), None)
        context = self.calculation_context()
        if (
            machine
            and (asdict(machine) != asdict(self.panel.baseline) or context != self._context)
            and not self.panel.is_dirty()
        ):
            self._context = context
            self.panel.load_machine(machine)
        elif machine and context != self._context:
            self._context = context
            self.panel.refresh_po_inputs()

    def restore(self):
        s = deepcopy(self.owner.detail_settings)
        if (
            s["open"]
            and s["machine_uid"]
            and any(m.uid == s["machine_uid"] for m in self.owner.machines)
        ):
            self.show_machine(s["machine_uid"])
        else:
            self.dock.hide()
