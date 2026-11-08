"""Embedded replacement management; assessments never silently register assignments."""

from copy import deepcopy
from datetime import datetime, timezone
from PySide6.QtCore import Qt, QTimer, QEvent, QItemSelectionModel
from PySide6.QtGui import QColor, QBrush
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QComboBox,
    QPushButton,
    QCheckBox,
    QInputDialog,
    QMessageBox,
    QMenu,
    QToolButton,
    QAbstractItemView,
    QSplitter,
)
from .analysis_views import make_table, ValueItem, propose_assignments
from .analytics_page import fill_table
from .machine_matching import machine_key, display_identity
from .analytics_data import formatted, normalized
from .analysis_settings import machine_type
from .replacement import assess
from .theme import manager, analysis_color, contrast_text

STATUS_COLORS = {
    "要確認": "#F4A6A6",
    "入替検討": "#F7D5A0",
    "経過観察": "#FFE16A",
    "好調": "#94D8A0",
    "データ不足": "#B8BEC6",
    "入替予定登録済み": "#A9D7FF",
    "除外": "#B8BEC6",
}


def candidate_key(m):
    return (
        "management:" + normalized(m.source_key)
        if m.source_key
        else "machine:" + machine_key(m) if machine_key(m) else "uid:" + m.uid
    )


def validate_candidate_state(value=None):
    from .core import AppError

    default = dict(
        version=1,
        exclusions={},
        manual_registrations={},
        search="",
        kind="",
        method="",
        status="",
        show_excluded=False,
    )
    if value is None:
        return default
    if not isinstance(value, dict) or set(value) - set(default):
        raise AppError("入替候補の保存形式が不正です。")
    s = default | deepcopy(value)
    if (
        s["version"] != 1
        or type(s["show_excluded"]) is not bool
        or any(
            not isinstance(s[k], str) or len(s[k]) > 2000
            for k in ("search", "kind", "method", "status")
        )
    ):
        raise AppError("入替候補の表示設定が不正です。")
    if not isinstance(s["exclusions"], dict) or len(s["exclusions"]) > 10000:
        raise AppError("候補の除外情報が不正です。")
    for key, row in s["exclusions"].items():
        if (
            not isinstance(key, str)
            or not isinstance(row, dict)
            or set(row) != {"reason", "updated_at"}
            or any(not isinstance(v, str) or len(v) > 2000 for v in row.values())
        ):
            raise AppError("候補の除外理由が不正です。")
    if (
        not isinstance(s["manual_registrations"], dict)
        or len(s["manual_registrations"]) > 10000
        or any(
            not isinstance(k, str) or not isinstance(v, str)
            for k, v in s["manual_registrations"].items()
        )
    ):
        raise AppError("手動候補の登録形式が不正です。")
    return s


def register_selected_candidates(floor):
    from .instructions import stamp

    before = floor.state()
    ids = set(floor.selection_state.selected_machine_keys) & {m.uid for m in floor.machines}
    old = floor.candidate_ui["manual_registrations"]
    new = ids - set(old)
    for uid in new:
        old[uid] = stamp()
    if new:
        floor.record(before, "入替候補を手動登録")
    floor.schedule_save()
    page = getattr(floor, "candidate_page", None)
    if page:
        page.refresh()
    floor.message.emit(
        f"入替候補に手動登録：{len(new)}台 / 登録済み：{len(ids)-len(new)}台。選択状態を保持しています。"
    )
    return len(new)


class CandidatePage(QWidget):
    def __init__(self, window):
        super().__init__()
        self.host_window = window
        self.owner = window.floor
        self._building = False
        outer = QVBoxLayout(self)
        outer.setContentsMargins(5, 5, 5, 5)
        outer.setSpacing(7)
        row = QHBoxLayout()
        outer.addLayout(row)
        from .detail_widgets import KpiCard

        self.cards = {}
        for status in ("要確認", "入替検討", "好調", "データ不足", "入替予定登録済み"):
            card = KpiCard("追加済み" if status == "入替予定登録済み" else status)
            card.setFixedHeight(80)
            card.setMinimumWidth(100)
            row.addWidget(card, 1)
            self.cards[status] = card
        note = QLabel(
            "ルールによる参考判定です。選択だけでは登録せず、入替先商品を選んで明示的に確認した場合に指示書へ反映します。"
        )
        note.setWordWrap(True)
        outer.addWidget(note)
        bulk = QHBoxLayout()
        outer.addLayout(bulk)
        self.transfer_buttons = {}
        for title, statuses in [
            ("選択候補を追加", None),
            ("要確認を一括追加", {"要確認"}),
            ("入替検討を一括追加", {"入替検討"}),
        ]:
            b = QPushButton(title)
            b.clicked.connect(lambda checked=False, s=statuses: self.add_to_instructions(s))
            bulk.addWidget(b)
            self.transfer_buttons[title] = b
        menu_button = QToolButton()
        menu_button.setText("一括追加")
        menu = QMenu(menu_button)
        for title, statuses in [
            ("要確認", {"要確認"}),
            ("入替検討", {"入替検討"}),
            ("要確認＋入替検討", {"要確認", "入替検討"}),
            ("現在選択中の候補", None),
        ]:
            menu.addAction(title, lambda checked=False, s=statuses: self.add_to_instructions(s))
        menu_button.setMenu(menu)
        menu_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        bulk.addWidget(menu_button)
        open_button = QPushButton("入替指示書を開く")
        open_button.clicked.connect(lambda: window.tabs.setCurrentWidget(window.instruction_page))
        bulk.addWidget(open_button)
        self._syncing_selection = False
        row = QHBoxLayout()
        outer.addLayout(row)
        self.search = QLineEdit()
        self.search.setPlaceholderText("機械番号・管理ナンバー・商品・判定理由を検索")
        row.addWidget(self.search, 1)
        self.kinds = QComboBox()
        self.methods = QComboBox()
        self.status = QComboBox()
        for c in (self.kinds, self.methods, self.status):
            row.addWidget(c)
        self.show_excluded = QCheckBox("除外済みも表示")
        row.addWidget(self.show_excluded)
        self.table = make_table([], [])
        from .candidate_evidence import CandidateReasonPanel

        self.reason_panel = CandidateReasonPanel(self)
        self.reason_panel.hide()
        self.reason_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.reason_splitter.addWidget(self.table)
        self.reason_splitter.addWidget(self.reason_panel)
        self.reason_splitter.setStretchFactor(0, 3)
        self.reason_splitter.setStretchFactor(1, 1)
        outer.addWidget(self.reason_splitter, 1)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self.context_menu)
        self.table.cellClicked.connect(lambda *_: self.highlight(False))
        self.table.itemChanged.connect(self.checked_changed)
        self.table.viewport().installEventFilter(self)
        self.table.installEventFilter(self)
        self.table.setAutoScroll(False)
        self._check_pressed = None
        for label, callback in [
            ("候補を全選択", self.select_all_candidates),
            ("選択解除", lambda: self.owner.selection_state.set(set())),
        ]:
            b = QPushButton(label)
            bulk.addWidget(b)
            b.clicked.connect(callback)
        self.table.selectionModel().selectionChanged.connect(self.table_selection_changed)
        self.owner.selection_state.changed.connect(self.sync_selection_widgets)
        self.table.cellDoubleClicked.connect(lambda *_: self.open_detail())
        actions = QHBoxLayout()
        outer.addLayout(actions)
        self.buttons = {}
        for label, callback in [
            ("理由を見る", self.show_reasons),
            ("マシン詳細", self.open_detail),
            ("売り場で場所を確認", lambda: self.highlight(True)),
            ("入替予定へ登録", self.register),
            ("理由を付けて除外", self.exclude),
            ("除外を解除", self.restore_exclusion),
        ]:
            b = QPushButton(label)
            b.clicked.connect(callback)
            actions.addWidget(b)
            self.buttons[label] = b
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        outer.addWidget(self.summary)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(120)
        self.timer.timeout.connect(self.refresh)
        self.owner.changed.connect(self.invalidate)
        manager().changed.connect(self.invalidate)
        self.search.textChanged.connect(self.changed)
        for c in (self.kinds, self.methods, self.status):
            c.currentIndexChanged.connect(self.changed)
        self.show_excluded.toggled.connect(self.changed)
        self.refresh()

    def invalidate(self, *_):
        if self.isVisible():
            self.timer.start()

    def showEvent(self, e):
        super().showEvent(e)
        self.refresh()

    def selected_uids(self):
        return list(self.owner.selection_state.selected_machine_keys)

    def select_all_candidates(self, *_):
        keys = {
            self.table.item(i, 0).data(Qt.ItemDataRole.UserRole)[0]
            for i in range(self.table.rowCount())
        }
        self.owner.selection_state.set(set(self.selected_uids()) | keys)

    def reset_pointer(self):
        self._check_pressed = None
        self.table.setState(QAbstractItemView.State.NoState)

    def eventFilter(self, obj, event):
        if obj not in (self.table, self.table.viewport()):
            return super().eventFilter(obj, event)
        kind = event.type()
        if kind in (
            QEvent.Type.FocusOut,
            QEvent.Type.WindowDeactivate,
            QEvent.Type.UngrabMouse,
            QEvent.Type.Hide,
        ):
            self.reset_pointer()
        if kind == QEvent.Type.MouseMove and event.buttons() == Qt.MouseButton.NoButton:
            self.reset_pointer()
            return True
        if kind == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
            index = self.table.indexAt(event.position().toPoint())
            if index.isValid() and index.column() == 0:
                self._check_pressed = self.table.item(index.row(), 0).data(
                    Qt.ItemDataRole.UserRole
                )[0]
                return True
        if kind == QEvent.Type.MouseButtonRelease and event.button() == Qt.MouseButton.LeftButton:
            pressed = self._check_pressed
            if pressed is not None:
                index = self.table.indexAt(event.position().toPoint())
                self.reset_pointer()
                if (
                    index.isValid()
                    and index.column() == 0
                    and self.table.item(index.row(), 0).data(Qt.ItemDataRole.UserRole)[0] == pressed
                ):
                    keys = set(self.selected_uids())
                    keys.symmetric_difference_update({pressed})
                    self.owner.selection_state.set(keys)
                return True
        if kind == QEvent.Type.KeyPress and event.key() == Qt.Key.Key_Space:
            indexes = self.table.selectionModel().selectedRows()
            if not indexes and self.table.currentIndex().isValid():
                indexes = [self.table.currentIndex()]
            keys = set(self.selected_uids())
            ids = {
                self.table.item(index.row(), 0).data(Qt.ItemDataRole.UserRole)[0]
                for index in indexes
            }
            if ids <= keys:
                keys -= ids
            else:
                keys |= ids
            self.owner.selection_state.set(keys)
            return True
        return super().eventFilter(obj, event)

    def table_selection_changed(self, *_):
        # Row focus / drag highlight is deliberately independent of work selection.
        if not self._building and not self._syncing_selection:
            self.highlight(False)

    def sync_selection_widgets(self):
        if self._building or self._syncing_selection:
            return
        self._syncing_selection = True
        keys = self.owner.selection_state.selected_machine_keys
        for i in range(self.table.rowCount()):
            item = self.table.item(i, 0)
            uid = item.data(Qt.ItemDataRole.UserRole)[0]
            item.setCheckState(Qt.CheckState.Checked if uid in keys else Qt.CheckState.Unchecked)
        self._syncing_selection = False

    def selected_machines(self):
        ids = set(self.selected_uids())
        return [m for m in self.owner.machines if m.uid in ids]

    def changed(self, *_):
        if self._building:
            return
        s = self.owner.candidate_ui
        s.update(
            search=self.search.text(),
            kind=self.kinds.currentData() or "",
            method=self.methods.currentData() or "",
            status=self.status.currentData() or "",
            show_excluded=self.show_excluded.isChecked(),
        )
        self.owner.schedule_save()
        self.timer.start()

    def refresh(self):
        if self._building:
            return
        self.timer.stop()
        self._building = True
        vscroll = self.table.verticalScrollBar().value()
        hscroll = self.table.horizontalScrollBar().value()
        sort_column = self.table.horizontalHeader().sortIndicatorSection()
        sort_order = self.table.horizontalHeader().sortIndicatorOrder()
        current = self.table.currentRow()
        focus_uid = (
            self.table.item(current, 0).data(Qt.ItemDataRole.UserRole)[0]
            if current >= 0 and self.table.item(current, 0)
            else None
        )
        s = self.owner.candidate_ui
        selection = set(self.selected_uids())
        from .candidate_transfer import registered_ids

        added_ids = registered_ids(self.owner)
        rs = self.owner.analytics.records()
        byuid = {r["uid"]: r for r in rs}
        for control, key, label, values in (
            (self.kinds, "kind", "全機種", sorted({r["machine_type"] for r in rs})),
            (self.methods, "method", "全運営方法", sorted({r["method"] for r in rs})),
            (self.status, "status", "全判定", [*STATUS_COLORS, "手動登録"]),
        ):
            control.clear()
            control.addItem(label, "")
            for val in values:
                control.addItem(val, val)
            if s[key] and control.findData(s[key]) < 0:
                control.addItem(s[key] + "（該当なし）", s[key])
            control.setCurrentIndex(max(0, control.findData(s[key])))
        self.search.setText(s["search"])
        self.show_excluded.setChecked(s["show_excluded"])
        rows = []
        identities = []
        colors = []
        counts = {k: 0 for k in self.cards}
        for m in self.owner.machines:
            r = byuid[m.uid]
            a = r["assessment_detail"]
            status = a["status"]
            if status == "判定保留":
                status = (
                    "経過観察"
                    if r["ratio"] is not None
                    and r["days"] is not None
                    and r["days"] >= 0
                    and a["reasons"][0] == "投入日数が判定条件に達していません。"
                    else "データ不足"
                )
            planned = r["planned"]
            excluded = s["exclusions"].get(candidate_key(m))
            if not excluded:
                if status in counts:
                    counts[status] += 1
                if planned or m.uid in added_ids:
                    counts["入替予定登録済み"] += 1
            added = m.uid in added_ids
            shown = "除外" if excluded else "入替予定登録済み" if planned or added else status
            reason = ("手動登録 / " if m.uid in s["manual_registrations"] else "") + " / ".join(
                a["reasons"]
            )
            if excluded:
                reason = f'除外理由：{excluded["reason"]} / ' + reason
            manual = m.uid in s["manual_registrations"]
            if (
                excluded
                and not s["show_excluded"]
                or s["kind"]
                and r["machine_type"] != s["kind"]
                or s["method"]
                and r["method"] != s["method"]
                or s["status"]
                and (not manual if s["status"] == "手動登録" else shown != s["status"])
            ):
                continue
            if s["search"] and normalized(s["search"]) not in normalized(
                " ".join(
                    [
                        display_identity(m),
                        m.source_key,
                        m.product,
                        r["machine_type"],
                        r["method"],
                        reason,
                        shown,
                    ]
                )
            ):
                continue
            rows.append(
                [
                    (f'{a["priority"]+1} {status}', a["priority"]),
                    machine_key(m) or "—",
                    m.source_key or "—",
                    m.product or "—",
                    (formatted("ratio", r["ratio"]), r["ratio"]),
                    (formatted("sales", r["sales"]), r["sales"]),
                    (formatted("po_rate", r["po_rate"]), r["po_rate"]),
                    (formatted("cost_rate", r["cost_rate"]), r["cost_rate"]),
                    (formatted("days", r["days"]), r["days"]),
                    reason,
                    "✓ 追加済み" if added else shown,
                    "指示書へ追加",
                    "理由を見る",
                ]
            )
            identities.append(m.uid)
            colors.append(STATUS_COLORS[shown])
        fill_table(
            self.table,
            [
                "優先順位",
                "機械番号",
                "管理ナンバー",
                "商品名",
                "アベ比",
                "売上金額（円）",
                "PO率",
                "原価率",
                "投入日数",
                "判定理由",
                "状態",
                "操作",
                "判定根拠",
            ],
            rows,
        )
        for i, (uid, color) in enumerate(zip(identities, colors)):
            self.table.item(i, 0).setData(Qt.ItemDataRole.UserRole, [uid])
            self.table.item(i, 0).setFlags(
                self.table.item(i, 0).flags() | Qt.ItemFlag.ItemIsUserCheckable
            )
            self.table.item(i, 0).setCheckState(
                Qt.CheckState.Checked
                if uid in self.owner.selection_state.selected_machine_keys
                else Qt.CheckState.Unchecked
            )
            add = QPushButton("✓ 追加済み" if uid in added_ids else "指示書へ追加")
            add.setEnabled(uid not in added_ids)
            add.clicked.connect(
                lambda checked=False, u=uid: self.add_to_instructions(
                    uids={u}, source_method="単体"
                )
            )
            self.table.setCellWidget(i, 11, add)
            reason_button = QPushButton("理由を見る")
            reason_button.clicked.connect(lambda checked=False, u=uid: self.show_reasons(u))
            self.table.setCellWidget(i, 12, reason_button)
            for j in (0, 10):
                self.table.item(i, j).setBackground(QBrush(QColor(analysis_color(color))))
                self.table.item(i, j).setForeground(
                    QBrush(QColor(contrast_text(analysis_color(color))))
                )
        self.table.setSortingEnabled(True)
        self.table.sortItems(max(0, sort_column), sort_order)
        self.table.setColumnWidth(3, 280)
        self.table.setColumnWidth(9, 410)
        if focus_uid:
            for i in range(self.table.rowCount()):
                if self.table.item(i, 0).data(Qt.ItemDataRole.UserRole)[0] == focus_uid:
                    self.table.setCurrentCell(i, 0)
                    break
        self.table.verticalScrollBar().setValue(vscroll)
        self.table.horizontalScrollBar().setValue(hscroll)
        for status, card in self.cards.items():
            card.set_value(
                str(counts[status]),
                STATUS_COLORS[status],
                "明示登録のみ反映" if status == "入替予定登録済み" else "ルールによる参考判定",
            )
            card.value.setToolTip(status)
        self.summary.setText(
            f'表示 {len(rows)} / {len(rs)}台 ｜ 除外 {len(s["exclusions"])}件 ｜ ダブルクリックで詳細、場所確認で配置へ移動します。'
        )
        self._building = False
        if hasattr(self.host_window, "workflow"):
            self.host_window.workflow.capture_assessments()

    def checked_changed(self, item):
        if self._building or self._syncing_selection or item.column() != 0:
            return
        ids = item.data(Qt.ItemDataRole.UserRole)
        if not ids:
            return
        keys = set(self.selected_uids())
        if item.checkState() == Qt.CheckState.Checked:
            keys.update(ids)
        else:
            keys.difference_update(ids)
        self.owner.selection_state.set(keys)

    def add_to_instructions(self, statuses=None, uids=None, source_method=None):
        from .candidate_transfer import transfer

        if uids is None:
            if statuses is None:
                uids = set(self.selected_uids())
            else:
                records = self.owner.analytics.records()
                uids = {r["uid"] for r in records if r["assessment"] in statuses}
                uids -= {
                    m.uid
                    for m in self.owner.machines
                    if candidate_key(m) in self.owner.candidate_ui["exclusions"]
                }
        machines = [m for m in self.owner.machines if m.uid in uids]
        result = transfer(
            self.owner,
            machines,
            self,
            category="＋".join(sorted(statuses)) if statuses else "選択候補",
            source_method=source_method,
        )
        if result["cancelled"]:
            return result
        self.refresh()
        self.summary.setText(
            f"入替指示書に{result['added']}台追加しました。追加済みのためスキップ：{result['skipped']}台 ／ 機械番号未取得：{result['missing_number']}台 ／ エラー：{result['errors']}台"
        )
        return result

    def highlight(self, activate=True):
        ids = self.selected_uids()
        if not ids:
            return
        if activate:
            self.host_window.highlight_analysis_machines(ids)
        else:
            self.owner.set_analysis_highlight(ids)

    def current_uid(self):
        row = self.table.currentRow()
        if row >= 0 and self.table.item(row, 0):
            return self.table.item(row, 0).data(Qt.ItemDataRole.UserRole)[0]
        return next(iter(self.selected_uids()), None)

    def show_reasons(self, uid=None):
        if not isinstance(uid, str):
            uid = self.current_uid()
        if uid:
            self.reason_panel.show_uid(uid)

    def open_detail(self):
        uid = self.current_uid()
        if uid:
            self.host_window.navigation.activate("floor")
            self.owner.open_details(uid)

    def register(self):
        selected = self.selected_machines()
        if propose_assignments(self.owner, [machine_key(m) for m in selected], self):
            self.refresh()

    def set_exclusions(self, reason):
        reason = reason.strip()
        if not reason:
            return False
        before = self.owner.state()
        for m in self.selected_machines():
            self.owner.candidate_ui["exclusions"][candidate_key(m)] = {
                "reason": reason,
                "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            }
        self.owner.record(before, "入替候補から除外")
        self.owner.schedule_save()
        self.refresh()
        return True

    def exclude(self):
        if not self.selected_machines():
            return
        reason, ok = QInputDialog.getText(self, "候補から除外", "除外理由（保存します）")
        if ok and not self.set_exclusions(reason):
            QMessageBox.information(self, "除外理由", "理由を入力してください。")

    def restore_exclusion(self):
        before = self.owner.state()
        for m in self.selected_machines():
            self.owner.candidate_ui["exclusions"].pop(candidate_key(m), None)
        self.owner.record(before, "入替候補の除外を解除")
        self.owner.schedule_save()
        self.refresh()

    def context_menu(self, pos):
        menu = QMenu(self)
        for b in self.buttons.values():
            menu.addAction(b.text(), b.click)
        menu.exec(self.table.viewport().mapToGlobal(pos))
