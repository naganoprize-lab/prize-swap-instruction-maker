"""View state, fullscreen hosting and details, independent of placement history."""

from copy import deepcopy
from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QDoubleSpinBox,
    QInputDialog,
    QLabel,
    QTabWidget,
    QVBoxLayout,
    QWidget,
    QMenu,
    QGraphicsItem,
    QComboBox,
    QHBoxLayout,
    QToolButton,
    QCheckBox,
    QSizePolicy,
    QLineEdit,
)
from .floor_presentation import (
    validate_presentation,
    visible_machines,
    project_machines,
    matching_machines,
)
from .floor_status import COLOR_FILTERS


class FullscreenHost(QDialog):
    def __init__(self, floor):
        super().__init__(None)
        self.floor = floor
        self.setStyleSheet(floor.window().styleSheet())
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setWindowTitle("売り場レイアウト — Escで戻る")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        # removeTab() hides its page explicitly. Reparenting alone does not undo
        # that hidden state, even when the new top-level window is shown.
        self.hidden_controls = [
            (w, not w.isHidden())
            for w in [*floor.toolbar_rows, floor.info, floor.analysis_note, floor.dashboard]
        ]
        for widget, _ in self.hidden_controls:
            widget.hide()
        self.toolbar = QWidget(self)
        self.toolbar.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        row = QHBoxLayout(self.toolbar)
        row.setContentsMargins(6, 4, 6, 4)
        self.filter_combo = QComboBox()
        for key, label in COLOR_FILTERS.items():
            self.filter_combo.addItem(label, key)
        self.filter_combo.setCurrentIndex(
            self.filter_combo.findData(floor.presentation["color_filter"])
        )
        self.filter_combo.currentIndexChanged.connect(
            lambda *_: floor.set_color_filter(self.filter_combo.currentData())
        )
        row.addWidget(self.filter_combo)
        self.search = QLineEdit()
        self.search.setPlaceholderText("番号・商品・機種・方法を検索")
        self.search.setClearButtonEnabled(True)
        self.search.setText(floor.floor_search.text())
        self.search.textChanged.connect(floor.floor_search.setText)
        floor.floor_search.textChanged.connect(self.search.setText)
        row.addWidget(self.search, 1)
        for label, direction in (("前へ", -1), ("次へ", 1)):
            b = QToolButton()
            b.setText(label)
            b.clicked.connect(lambda checked=False, d=direction: floor.next_match(d))
            row.addWidget(b)

        self.buttons = {}
        for key, label, callback in (
            ("zoom_in", "＋", lambda: floor.set_zoom(floor.view.transform().m11() * 115)),
            ("zoom_out", "−", lambda: floor.set_zoom(floor.view.transform().m11() * 100 / 1.15)),
            ("fit", "全体", floor.fit),
            ("details", "詳細", floor.open_selected_details),
        ):
            button = QToolButton()
            button.setText(label)
            button.clicked.connect(callback)
            row.addWidget(button)
            self.buttons[key] = button
        self.edit = QCheckBox("配置編集")
        self.edit.setChecked(floor.edit_mode.isChecked())
        self.edit.toggled.connect(floor.edit_mode.setChecked)
        row.addWidget(self.edit)
        dashboard = QToolButton()
        dashboard.setText("概要")
        dashboard.setCheckable(True)
        dashboard.setChecked(floor.analysis_settings["fullscreen_dashboard"])
        floor.dashboard.setVisible(dashboard.isChecked())

        def toggle_dashboard(value):
            floor.analysis_settings["fullscreen_dashboard"] = value
            floor.dashboard.setVisible(value)
            floor.schedule_save()

        dashboard.toggled.connect(toggle_dashboard)
        row.addWidget(dashboard)
        row.addStretch()
        close = QToolButton()
        close.setText("解除（Esc）")
        close.clicked.connect(self.close)
        row.addWidget(close)
        self.buttons["close"] = close
        layout.addWidget(self.toolbar)
        layout.addWidget(floor, 1)
        floor.show()
        self.resize(self.screen().availableGeometry().size())
        layout.activate()
        shortcut = QShortcut(QKeySequence("Esc"), self)
        shortcut.activated.connect(self.close)

    def showEvent(self, event):
        super().showEvent(event)
        QTimer.singleShot(0, self.floor.finish_screen_transition)

    def reject(self):
        self.close()

    def closeEvent(self, event):
        self.floor.leave_fullscreen()
        event.accept()


from .sales_analysis_ui import SalesAnalysisMixin


class FloorWorkspaceMixin(SalesAnalysisMixin):
    def presentation_changed(self, *_):
        if getattr(self, "_building_view", True):
            return
        if self.days_filter.isChecked() and self.min_days.value() > self.max_days.value():
            if self.sender() is self.max_days:
                self.min_days.setValue(self.max_days.value())
            else:
                self.max_days.setValue(self.min_days.value())
        self.min_days.setEnabled(self.days_filter.isChecked())
        self.max_days.setEnabled(self.days_filter.isChecked())
        previous = self.presentation["mode"]
        old_query = self.presentation["search"]
        new_query = self.floor_search.text()
        if new_query and not old_query:
            p = self.view.mapToScene(self.view.viewport().rect().center())
            self._pre_search_view = (self._zoom, [p.x(), p.y()])

        self.presentation.update(
            mode=self.layout_mode.currentData(),
            sort=self.sort_by.currentData(),
            descending=self.descending.isChecked(),
            search=self.floor_search.text(),
            filter=self.filter_by.currentData(),
            min_days=self.min_days.value() if self.days_filter.isChecked() else None,
            max_days=self.max_days.value() if self.days_filter.isChecked() else None,
            edit=self.edit_mode.isChecked(),
            legend=self.legend_toggle.isChecked(),
        )
        if self._fullscreen_host:
            self._fullscreen_host.edit.blockSignals(True)
            self._fullscreen_host.edit.setChecked(self.presentation["edit"])
            self._fullscreen_host.edit.blockSignals(False)
        self.apply_presentation()
        if old_query and not new_query and getattr(self, "_pre_search_view", None):
            zoom, center = self._pre_search_view
            if zoom is None:
                self.fit(save=False)
            else:
                self.set_zoom(zoom, save=False)
            self.view.centerOn(QPointF(*center))
            self._pre_search_view = None
        if previous != self.presentation["mode"]:
            self.set_zoom(100, save=False)
            if self.selected():
                self.zoom_selected()
            elif self.visible_nodes():
                self.view.centerOn(self.visible_nodes()[0])
        self.schedule_save()
        self.changed.emit()

    def restore_presentation(self, state):
        self.presentation = validate_presentation(state.get("presentation", {}))
        self.card_presets = deepcopy(state.get("card_presets", {}))
        self._view_center = state.get("view_center")
        self._building_view = True
        for control, key in (
            (self.layout_mode, "mode"),
            (self.sort_by, "sort"),
            (self.filter_by, "filter"),
        ):
            control.setCurrentIndex(control.findData(self.presentation[key]))
        self.color_filter.setCurrentIndex(
            self.color_filter.findData(self.presentation["color_filter"])
        )
        self.floor_search.setText(self.presentation["search"])
        self.descending.setChecked(self.presentation["descending"])
        self.edit_mode.setChecked(self.presentation["edit"])
        self.legend_toggle.setChecked(self.presentation["legend"])
        self.days_filter.setChecked(
            self.presentation["min_days"] is not None or self.presentation["max_days"] is not None
        )
        self.min_days.setValue(self.presentation["min_days"] or 0)
        self.max_days.setValue(
            self.presentation["max_days"] if self.presentation["max_days"] is not None else 9999
        )
        self._building_view = False

    def visible_nodes(self):
        return [n for n in self.nodes.values() if n.isVisible()]

    def apply_presentation(self):
        if not hasattr(self, "nodes"):
            return
        from .analysis_settings import sales_boundaries

        self._effective_metric_settings = self.analysis_settings | {
            "_sales_bounds": sales_boundaries(self.machines, self.analysis_settings)
        }
        visible = visible_machines(
            self.machines, self.assignments(), self.presentation, self.reference_day()
        )
        matches = matching_machines(
            self.machines,
            self.assignments(),
            self.presentation,
            self.reference_day(),
            self.analysis_settings,
        )
        self._match_uids = [m.uid for m in matches]
        self.search_count.setText(f"検索結果：{len(matches)} / {len(self.machines)}ブース")
        active = bool(
            self.presentation["search"].strip()
            or self.presentation["filter"] != "all"
            or self.presentation["min_days"] is not None
            or self.presentation["max_days"] is not None
        )
        matched = set(self._match_uids)
        width = self.view.viewport().width()
        rects = project_machines(visible, self.presentation, self.reference_day(), width)
        self._projecting = True
        try:
            for uid, node in self.nodes.items():
                visible = uid in rects
                if not visible:
                    node.setSelected(False)
                node.setVisible(visible)
                node.search_match = active and uid in matched
                node.setOpacity(1 if not active or uid in matched else 0.35)
                if not visible:
                    continue
                rect = rects[uid]
                projected = QRectF(0, 0, rect.width(), rect.height())
                if node.display_rect != projected:
                    node.prepareGeometryChange()
                    node.display_rect = projected
                if node.pos() != rect.topLeft():
                    node.setPos(rect.topLeft())
                node.setFlag(
                    QGraphicsItem.GraphicsItemFlag.ItemIsMovable,
                    self.presentation["edit"] and self.presentation["mode"] == "actual",
                )
                node.update_handles()
                node.update()
        finally:
            self._projecting = False
        self.sort_by.setEnabled(self.presentation["mode"] == "list")
        self.descending.setEnabled(self.presentation["mode"] == "list")
        self.update_scene_rect()
        self.view.viewport().update()

    def highlight_group(self, uids):
        self._highlight_uids = set(uids)
        for node in self.nodes.values():
            node.update()

    def next_match(self, direction=1):
        ids = getattr(self, "_match_uids", [])
        if not ids:
            return
        current = next((m.uid for m in self.selected() if m.uid in ids), None)
        index = (
            (ids.index(current) + direction) % len(ids)
            if current
            else (0 if direction > 0 else len(ids) - 1)
        )
        self.scene.clearSelection()
        node = self.nodes[ids[index]]
        node.setSelected(True)
        self.view.ensureVisible(node, 40, 40)

    def apply_matching(self, rows, overrides):
        from .machine_matching import validate_references, prepare_references, normalize_number
        from .floor_data import validate_machines
        from dataclasses import asdict

        validate_references(rows)
        updated = deepcopy(self.machines)
        for m in updated:
            if m.uid in overrides:
                value = overrides[m.uid]
                if value is not None and value:
                    value = normalize_number(value)
                    if not value:
                        raise ValueError("手動の機械番号が不正です。")
                m.manual_number = value
        prepare_references(updated, rows)
        validate_machines([asdict(m) for m in updated])
        before = self.state()
        self.number_references = deepcopy(rows)
        self.machines = updated
        by_uid = {m.uid: m for m in self.machines}
        for uid, node in self.nodes.items():
            node.machine = by_uid[uid]
        self.refresh_assignments()
        self.record(before, "管理ナンバー照合の修正")
        self.sync()
        self.update_info()

    def show_matching(self):
        from .matching_ui import show_matching

        show_matching(self)

    def show_history(self):
        from .history_ui import show_history

        show_history(self)

    def manual_period(self):
        from .history_ui import manual_period

        manual_period(self)

    def show_method_analysis(self):
        if getattr(self,"analysis_page",None) is not None:
            self.analytics_requested.emit("","all")
            return
        from .analysis_views import show_method_analysis

        show_method_analysis(self)

    def show_candidates(self):
        from .analysis_views import show_candidates

        show_candidates(self)

    def analysis_settings_dialog(self):
        from .analysis_settings_ui import edit_analysis_settings

        edit_analysis_settings(self)

    def capture_view(self, save=True):
        if not hasattr(self, "view") or self._projecting or self._fullscreen_transition:
            return
        p = self.view.mapToScene(self.view.viewport().rect().center())
        self._view_center = [p.x(), p.y()]
        if save:
            self.timer.start(800)

    def restore_view_center(self):
        if self._view_center is not None:
            self.view.centerOn(QPointF(*self._view_center))

    def display_settings(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("表示項目・配置編集設定")
        form = QFormLayout(dialog)
        # Reparent existing controls only during the dialog, retaining legacy JSON settings.
        for label, control in (
            ("表示内容", self.display_mode),
            ("運営方法（詳細表示時）", self.show_methods),
            ("配置編集時", self.snap),
        ):
            form.addRow(label, control)
            control.show()
        close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        form.addRow(close)
        close.rejected.connect(dialog.reject)
        dialog.exec()
        for c in (self.display_mode, self.show_methods, self.snap):
            c.setParent(self)
            c.hide()

    def sort_menu(self):
        from PySide6.QtGui import QCursor

        menu = QMenu(self)
        for i in range(self.sort_by.count()):
            action = menu.addAction(self.sort_by.itemText(i))
            action.setCheckable(True)
            action.setChecked(i == self.sort_by.currentIndex())
            action.triggered.connect(
                lambda checked=False, index=i: self.sort_by.setCurrentIndex(index)
            )
        menu.addSeparator()
        desc = menu.addAction("降順")
        desc.setCheckable(True)
        desc.setChecked(self.descending.isChecked())
        desc.triggered.connect(self.descending.setChecked)
        menu.exec(QCursor.pos())

    def presentation_settings(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("見やすさ優先・一覧の表示カード")
        form = QFormLayout(dialog)
        controls = {}
        for key, label, low, high in (
            ("width", "幅", 80, 2000),
            ("height", "高さ", 60, 2000),
            ("font", "基準文字サイズ", 6, 72),
        ):
            spin = QDoubleSpinBox()
            spin.setRange(low, high)
            spin.setValue(self.presentation[key])
            form.addRow(label, spin)
            controls[key] = spin
        form.addRow(
            QLabel(
                "実配置の座標・サイズを変更しません。実配置のカード変更は「カード・文字サイズ」を使います。"
            )
        )
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        form.addRow(buttons)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.presentation.update({key: spin.value() for key, spin in controls.items()})
            self.apply_presentation()
            self.schedule_save()

    def save_card_preset(self):
        name, ok = QInputDialog.getText(
            self, "カードプリセット保存", "名前（現在のカード幅・高さ・文字サイズを保存）"
        )
        if ok and name.strip():
            if len(self.card_presets) >= 50 and name.strip() not in self.card_presets:
                self.message.emit("プリセットは50件までです。")
                return
            targets = self.selected() or self.machines
            if self.presentation["mode"] == "actual" and targets:
                m = targets[0]
                values = [m.width, m.height, m.font_size]
            else:
                values = [self.presentation[k] for k in ("width", "height", "font")]
            self.card_presets[name.strip()] = values
            self.schedule_save()

    def load_card_preset(self):
        if not self.card_presets:
            self.message.emit("保存したプリセットがありません。")
            return
        name, ok = QInputDialog.getItem(
            self, "プリセット適用", "名前", list(self.card_presets), editable=False
        )
        if ok:
            values = self.card_presets[name]
            if self.presentation["mode"] == "actual":
                self.apply_card_settings(
                    [m.uid for m in (self.selected() or self.machines)], *values
                )
            else:
                self.presentation.update(zip(("width", "height", "font"), values))
                self.apply_presentation()
            self.schedule_save()

    def canvas_menu(self, point):
        menu = QMenu(self)
        for label, callback in (
            ("選択マシンの詳細", self.open_selected_details),
            ("保存", self.save_dialog),
            ("売上分析Excel読込", self.import_analysis_dialog),
            ("実績読込結果", self.show_analysis_report),
            ("印刷プレビュー", self.preview),
            ("PDF保存", self.export_floor_pdf),
            ("直接印刷", self.print_floor),
        ):
            menu.addAction(label, callback)
        from .theme import ThemeMenu

        menu.addMenu(ThemeMenu(menu))
        menu.exec(self.view.viewport().mapToGlobal(point))

    def toggle_fullscreen(self):
        if self._fullscreen_host:
            self._fullscreen_host.close()
            return
        self.capture_view(False)
        self._fullscreen_transition = True
        parent = self.parentWidget()
        tabs = None
        while parent:
            if isinstance(parent, QTabWidget):
                tabs = parent
                break
            parent = parent.parentWidget()
        self._fullscreen_return = (tabs, tabs.indexOf(self) if tabs else -1, self.parentWidget())
        if tabs:
            index = tabs.indexOf(self)
            tabs.removeTab(index)
            self._placeholder = QWidget()
            tabs.insertTab(index, self._placeholder, "売り場（全画面表示中）")
            tabs.setCurrentIndex(index)
        self._fullscreen_host = FullscreenHost(self)
        self.fullscreen_button.setText("全画面解除（Esc）")
        self._fullscreen_host.showFullScreen()
        QTimer.singleShot(0, self.finish_screen_transition)

    def finish_screen_transition(self):
        if not self._fullscreen_transition:
            return
        if self._fullscreen_host:
            self._fullscreen_host.layout().activate()
        self.layout().activate()
        self._fullscreen_transition = False
        self.apply_presentation()
        if self._zoom is None:
            self.fit(save=False)
        else:
            self.restore_view_center()
        self.view.viewport().update()
        self.view.setFocus()

    def color_filter_changed(self, *_):
        if self._building_view:
            return
        self.set_color_filter(self.color_filter.currentData())

    def set_color_filter(self, mode):
        if mode not in COLOR_FILTERS:
            raise ValueError("色フィルターが不正です。")
        self.presentation["color_filter"] = mode
        for combo in [self.color_filter] + (
            [self._fullscreen_host.filter_combo] if self._fullscreen_host else []
        ):
            combo.blockSignals(True)
            combo.setCurrentIndex(combo.findData(mode))
            combo.blockSignals(False)
        self.display_mode.blockSignals(True)
        self.display_mode.setCurrentIndex(self.display_mode.findData("compact"))
        self.display_mode.blockSignals(False)
        self.sync()
        self.schedule_save()
        self.changed.emit()

    def leave_fullscreen(self):
        self.capture_view(False)
        self._fullscreen_transition = True
        host = self._fullscreen_host
        tabs, index, parent = self._fullscreen_return
        self.setParent(parent)
        if tabs:
            tabs.removeTab(index)
            tabs.insertTab(index, self, "売り場レイアウト")
            tabs.setCurrentIndex(index)
            self._placeholder.deleteLater()
        for widget, was_visible in host.hidden_controls:
            widget.setVisible(was_visible)
        self._fullscreen_host = None
        self.update_analysis_note()
        self.fullscreen_button.setText("全画面")
        self.show()
        QTimer.singleShot(0, self.finish_screen_transition)

    def open_selected_details(self):
        if self.selected():
            self.open_details(self.selected()[0].uid)
        else:
            self.message.emit("マシンを選択してください。")

    def open_details(self, uid):
        if self._detail_open or not self.isVisible():
            return
        from .floor_details import MachineDetailDialog, DetailCommand

        machine = next((m for m in self.machines if m.uid == uid), None)
        if machine is None:
            return
        self._detail_open = True
        try:
            dialog = MachineDetailDialog(self, machine)
            if dialog.exec() == QDialog.DialogCode.Accepted:
                before = self.state()
                changes = []
                for panel in dialog.products:
                    values = {
                        key: (
                            deepcopy(getattr(panel.source, key)),
                            deepcopy(getattr(panel.draft, key)),
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
                        if getattr(panel.source, key) != getattr(panel.draft, key)
                    }
                    if values:
                        changes.append((panel.source, values))
                self.machines[self.machines.index(machine)] = dialog.updated
                self.rebuild()
                dialog.apply_products()
                after = self.state()
                if before != after or changes:
                    self.history.push(DetailCommand(self, before, after, changes))
                self.product_edited.emit()
                self.sync()
                self.schedule_save()
        finally:
            self._detail_open = False

    def print_machines(self):
        if self.print_options.visible_only:
            return matching_machines(
                self.machines,
                self.assignments(),
                self.presentation,
                self.reference_day(),
                self.analysis_settings,
            )
        return self.machines
