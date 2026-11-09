"""Compact, shared date controls and an embedded player; no separate playback window."""

from datetime import datetime, timezone, timedelta
from pathlib import Path
from PySide6.QtCore import Qt, QDate, QTimer, QUrl
from PySide6.QtGui import QTextCharFormat, QColor, QDesktopServices
from PySide6.QtWidgets import (
    QWidget,
    QFrame,
    QVBoxLayout,
    QHBoxLayout,
    QFormLayout,
    QLabel,
    QPushButton,
    QToolButton,
    QComboBox,
    QDateEdit,
    QCheckBox,
    QFileDialog,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QSlider,
    QDialog,
    QDialogButtonBox,
    QMessageBox,
)
from .temporal_controller import MODES
from .theme import manager, analysis_color, contrast_text


def iso(edit):
    return edit.date().toString("yyyy-MM-dd")


def date_edit():
    d = QDateEdit(QDate.currentDate())
    d.setCalendarPopup(True)
    d.setDisplayFormat("yyyy/MM/dd")
    return d


def updated_text(raw):
    if not raw:
        return "未更新"
    try:
        return (
            datetime.fromisoformat(raw)
            .astimezone(timezone(timedelta(hours=9)))
            .strftime("%Y/%m/%d %H:%M:%S JST")
        )
    except ValueError:
        return raw


class PeriodControl(QFrame):
    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.controller = controller
        self._updating = False
        self._marks = None
        from .control_flow import ControlFlow

        row = ControlFlow(self)
        row.setContentsMargins(2, 2, 2, 2)
        self.mode = QComboBox()
        for key, label in MODES.items():
            self.mode.addItem(label, key)
        row.addWidget(self.mode)
        self.start = date_edit()
        self.end = date_edit()
        row.addWidget(self.start)
        row.addWidget(QLabel("〜"))
        row.addWidget(self.end)
        self.apply_button = QPushButton("表示")
        row.addWidget(self.apply_button)
        self.note = QLabel()
        self.note.setObjectName("muted")
        self.note.setWordWrap(True)
        row.addWidget(self.note)
        self.delay = QTimer(self)
        self.delay.setSingleShot(True)
        self.delay.setInterval(180)
        self.delay.timeout.connect(self.apply)
        self.mode.activated.connect(self.apply)
        self.apply_button.clicked.connect(self.apply)
        for control in (self.start, self.end):
            control.dateChanged.connect(self.dates_changed)
        controller.changed.connect(self.refresh)
        manager().changed.connect(self.refresh)
        self.refresh()

    def dates_changed(self, *_):
        if not self._updating:
            self.delay.start()

    def apply(self, *_):
        mode = self.mode.currentData()
        a, b = iso(self.start), iso(self.end)
        try:
            self.controller.request_apply(mode, a, b)
        except Exception as exc:
            self.note.setText(str(exc))

    def refresh(self, *_):
        c = self.controller
        s = c.settings
        self.setVisible(bool(s["folder"]))
        self._updating = True
        self.mode.setCurrentIndex(self.mode.findData(s["mode"]))
        for control, key in ((self.start, "start"), (self.end, "end")):
            if s[key]:
                control.setDate(QDate.fromString(s[key], "yyyy-MM-dd"))
        self.start.setEnabled(s["mode"] in ("day", "custom"))
        self.end.setEnabled(s["mode"] in ("custom", "7days", "30days"))
        if c.aggregate_busy:
            self.note.setText("集計中…（現在の表示と編集データを保持しています）")
        elif c.aggregate_error:
            self.note.setText(c.aggregate_error)
        elif c.active:
            v = c.frame.coverage
            self.note.setText(
                f'取得 {v["acquired"]}/{v["expected"]}日・未取得 {v["missing"]}日'
                + (" ／ 部分集計" if v["partial"] else "")
            )
            self.note.setToolTip("\n".join(v["warnings"]) or "欠損日は0売上として扱いません。")
        else:
            self.note.setText("現在の編集データ。日付切替でマスター・指示書を変更しません。")
        mark_key = (c._revision, manager().dark)
        if mark_key != self._marks:
            self._marks = mark_key
            dates = {
                d["metadata"]["start"]
                for d in c.docs
                if d.get("eligible") and d["metadata"]["data_type"] == "daily"
            }
            fmt = QTextCharFormat()
            bg = analysis_color("#DBEAFE")
            fmt.setBackground(QColor(bg))
            fmt.setForeground(QColor(contrast_text(bg)))
            fmt.setFontWeight(700)
            for control in (self.start, self.end):
                cal = control.calendarWidget()
                cal.setDateTextFormat(QDate(), QTextCharFormat())
                for day in dates:
                    cal.setDateTextFormat(QDate.fromString(day, "yyyy-MM-dd"), fmt)
        self._updating = False


class DailySettings(QFrame):
    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.controller = controller
        self._building = False
        self._table_revision = None
        self.setObjectName("card")
        outer = QVBoxLayout(self)
        outer.addWidget(QLabel("日別参照データ"))
        row = QHBoxLayout()
        outer.addLayout(row)
        for text, callback in [
            ("参照フォルダーを選択", self.choose),
            ("フォルダーを開く", self.open_folder),
            ("今すぐ再読込", controller.scan_async),
        ]:
            b = QPushButton(text)
            b.clicked.connect(callback)
            row.addWidget(b)
        self.auto = QCheckBox("自動更新")
        row.addWidget(self.auto)
        self.auto.toggled.connect(self.auto_changed)
        self.path = QLabel()
        self.path.setTextFormat(Qt.TextFormat.PlainText)
        self.path.setWordWrap(True)
        self.path.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        outer.addWidget(self.path)
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        outer.addWidget(self.summary)
        self.status = QLabel()
        self.status.setWordWrap(True)
        outer.addWidget(self.status)
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(
            ["ファイル", "対象日・期間", "区分", "実績／ブース", "状態", "警告"]
        )
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setMinimumHeight(180)
        self.table.setMaximumHeight(300)
        self.table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)
        self.table.setColumnWidth(0, 230)
        self.table.setColumnWidth(1, 190)
        outer.addWidget(self.table)
        confirm = QPushButton("選択ファイルの日付・区分・採用を確認")
        confirm.clicked.connect(self.confirm)
        outer.addWidget(confirm)
        controller.changed.connect(self.refresh)
        controller.import_progress.connect(self.progress)
        controller.imported.connect(lambda *_: self.refresh())
        self.refresh()

    def choose(self, *_):
        p = QFileDialog.getExistingDirectory(
            self,
            "日別参照フォルダーを選択",
            self.controller.settings["folder"]
            or str(Path.home() / "Documents" / "入替指示書メーカー_参照値"),
        )
        if p:
            try:
                self.controller.set_folder(p)
            except Exception as exc:
                self.status.setText(str(exc))

    def open_folder(self, *_):
        p = self.controller.settings["folder"]
        if p and Path(p).is_dir():
            QDesktopServices.openUrl(QUrl.fromLocalFile(p))
        else:
            self.status.setText("参照フォルダーが存在しません。選択し直してください。")

    def auto_changed(self, value):
        if self._building:
            return
        self.controller.settings["auto_update"] = value
        self.controller.configure_watch()
        self.controller.floor.schedule_save()

    def progress(self, n, total, name):
        self.status.setText(f"読込中 {n}/{total}：{name}")

    def refresh(self, *_):
        c = self.controller
        if c._closed:
            return
        self._building = True
        self.path.setText(
            c.settings["folder"] or "未登録（標準フォルダー名：入替指示書メーカー_参照値）"
        )
        self.auto.setChecked(c.settings["auto_update"])
        r = c.last_result
        if getattr(self, "_usable_revision", None) != c._revision:
            self._usable_revision = c._revision
            self._usable = c.library.usable(c.settings["folder"], full=False)[0] if c.docs else []
        usable = self._usable
        days = {d["metadata"]["start"] for d in usable if d["metadata"]["data_type"] == "daily"}
        self.summary.setText(
            f'ファイル {r.get("files",len(c.docs))} ／ 成功日数 {len(days)} ／ 失敗 {r.get("failed",0)} ／ キャッシュ {r.get("cached",0)} ／ 最終更新 {updated_text(r.get("finished_at"))}'
        )
        self.status.setText(
            "読込中…"
            if c._busy
            else "\n".join(r.get("warnings", []))
            or "日付・重複・集計区分は下の一覧で確認できます。"
        )
        if c.settings["folder"] and not Path(c.settings["folder"]).is_dir():
            self.status.setText(
                "参照フォルダーが見つかりません。保存済みデータを保持して起動しています。"
            )
        if getattr(self, "_table_revision", None) == (c._revision, tuple(d["id"] for d in c.docs)):
            self._building = False
            return
        self._table_revision = (c._revision, tuple(d["id"] for d in c.docs))
        self.rows = c.docs
        self.table.setRowCount(len(self.rows))
        for i, d in enumerate(self.rows):
            m = d["metadata"]
            period = f'{m["start"]}〜{m["end"]}' if m["start"] else "日付未確定"
            values = [
                d["name"],
                period,
                {"daily": "単日", "period": "期間累計", "unknown": "判定不能"}[m["data_type"]],
                f'{d["record_count"]}/{d["layout_count"]}',
                "要確認" if d["warnings"] else "正常",
                " / ".join(d["warnings"]),
            ]
            for j, v in enumerate(values):
                item = QTableWidgetItem(v)
                item.setToolTip(v)
                self.table.setItem(i, j, item)
        self._building = False

    def confirm(self, *_):
        row = self.table.currentRow()
        if row < 0:
            return
        doc = self.rows[row]
        d = QDialog(self)
        d.setWindowTitle("対象日・集計区分の確認")
        layout = QVBoxLayout(d)
        warning = QLabel(
            doc["name"]
            + "\n"
            + "\n".join(doc["warnings"])
            + "\n原本は変更せず、確認結果をキャッシュに保存します。"
        )
        warning.setWordWrap(True)
        layout.addWidget(warning)
        form = QFormLayout()
        layout.addLayout(form)
        a = date_edit()
        b = date_edit()
        hint = doc["metadata"]["start"] or doc["metadata"]["file_day"]
        if hint:
            a.setDate(QDate.fromString(hint, "yyyy-MM-dd"))
            b.setDate(a.date())
        kind = QComboBox()
        kind.addItem("単日データ", "daily")
        kind.addItem("期間累計", "period")
        kind.setCurrentIndex(
            kind.findData(doc["metadata"]["data_type"])
            if doc["metadata"]["data_type"] != "unknown"
            else 0
        )
        form.addRow("開始日", a)
        form.addRow("終了日", b)
        form.addRow("区分", kind)
        independent = QCheckBox("同一売上でも、別日の独立した実績と確認しました")
        independent.setChecked(doc.get("duplicate_approved", False))
        form.addRow(independent)
        preferred = QCheckBox("同じ対象日の採用ファイルにする")
        preferred.setChecked(True)
        form.addRow(preferred)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        layout.addWidget(buttons)
        buttons.rejected.connect(d.reject)

        def save():
            try:
                self.controller.library.confirm(
                    doc["id"],
                    iso(a),
                    iso(b),
                    kind.currentData(),
                    independent.isChecked(),
                    preferred.isChecked(),
                )
                self.controller.cache.clear()
                self.controller._revision += 1
                self.controller.reload_documents()
                if self.controller.active:
                    self.controller.request_apply(
                        self.controller.settings["mode"],
                        self.controller.settings["start"],
                        self.controller.settings["end"],
                    )
                d.accept()
            except Exception as exc:
                warning.setText(str(exc))

        buttons.accepted.connect(save)
        d.resize(650, 380)
        d.exec()


class Timeline(QFrame):
    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.c = controller
        self._building = False
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(2)
        self.toggle = QToolButton()
        self.toggle.setText("タイムラプス ▾")
        self.toggle.setCheckable(True)
        outer.addWidget(self.toggle)
        self.body = QWidget()
        outer.addWidget(self.body)
        layout = QVBoxLayout(self.body)
        layout.setContentsMargins(2, 2, 2, 2)
        from .control_flow import ControlFlow

        row = ControlFlow()
        layout.addLayout(row)
        self.start = date_edit()
        self.end = date_edit()
        row.addWidget(self.start)
        row.addWidget(QLabel("〜"))
        row.addWidget(self.end)
        self.mode = QComboBox()
        self.mode.addItem("日別", "daily")
        self.mode.addItem("月間トータル", "monthly")
        row.addWidget(self.mode)
        self.buttons = {}
        for key, label, fn in [
            ("range", "範囲を設定", self.set_range),
            ("first", "|◀", lambda: self.seek(0)),
            ("prev", "◀", lambda: self.seek(self.c.frame_index - 1)),
            ("play", "▶ 再生", self.play),
            ("next", "▶", lambda: self.seek(self.c.frame_index + 1)),
            ("last", "▶|", lambda: self.seek(len(self.c.frames) - 1)),
        ]:
            button = QPushButton(label)
            button.clicked.connect(fn)
            row.addWidget(button)
            self.buttons[key] = button
        self.speed = QComboBox()
        for v in (0.5, 1.0, 2.0, 4.0):
            self.speed.addItem(f"{v:g}倍", v)
        row.addWidget(self.speed)
        self.loop = QCheckBox("ループ")
        row.addWidget(self.loop)
        self.skip = QCheckBox("データのある日だけ")
        row.addWidget(self.skip)
        self.highlight = QCheckBox("変化を強調")
        row.addWidget(self.highlight)
        self.slider = QSlider(Qt.Orientation.Horizontal)
        layout.addWidget(self.slider)
        self.slider.sliderPressed.connect(controller.pause)
        self.slider.valueChanged.connect(self.slider_changed)
        self.note = QLabel()
        self.note.setWordWrap(True)
        layout.addWidget(self.note)
        self.toggle.toggled.connect(self.toggle_changed)
        self.mode.activated.connect(self.preferences_changed)
        self.speed.activated.connect(self.preferences_changed)
        for x in (self.loop, self.skip, self.highlight):
            x.toggled.connect(self.preferences_changed)
        controller.changed.connect(self.refresh)
        self.refresh()

    def toggle_changed(self, value):
        if self._building:
            return
        self.c.settings["timeline_open"] = value
        self.body.setVisible(value)
        self.c.floor.schedule_save()

    def preferences_changed(self, *_):
        if self._building:
            return
        old_mode = self.c.settings["play_mode"]
        old_skip = self.c.settings["skip_missing"]
        self.c.settings.update(
            play_mode=self.mode.currentData(),
            speed=self.speed.currentData(),
            loop=self.loop.isChecked(),
            skip_missing=self.skip.isChecked(),
            highlight=self.highlight.isChecked(),
        )
        if self.c.playing:
            self.c.play_timer.setInterval(round(1000 / self.c.settings["speed"]))
        if old_mode != self.c.settings["play_mode"] or old_skip != self.c.settings["skip_missing"]:
            current = (self.c.frame.start, self.c.frame.end) if self.c.frame else None
            self.set_range()
            if current in self.c.frames:
                self.c.seek(self.c.frames.index(current))
        for node in self.c.floor.nodes.values():
            node.update()
        self.c.floor.view.viewport().update()
        self.c.floor.schedule_save()

    def set_range(self, *_):
        try:
            self.c.settings["play_start"] = iso(self.start)
            self.c.settings["play_end"] = iso(self.end)
            n = self.c.prepare_playback(iso(self.start), iso(self.end))
            self.note.setText(f"{n}フレーム" if n else "再生できる確定データがありません。")
        except Exception as exc:
            self.note.setText(str(exc))

    def play(self, *_):
        if self.c.playing:
            self.c.pause()
        else:
            if not self.c.frames:
                self.set_range()
            self.c.play()

    def seek(self, index):
        self.c.pause()
        self.c.seek(index)

    def slider_changed(self, index):
        if not self._building:
            self.seek(index)

    def refresh(self, *_):
        c = self.c
        s = c.settings
        self._building = True
        self.setVisible(bool(s["folder"]))
        self.toggle.setChecked(s["timeline_open"])
        self.body.setVisible(s["timeline_open"])
        self.mode.setCurrentIndex(self.mode.findData(s["play_mode"]))
        self.speed.setCurrentIndex(self.speed.findData(s["speed"]))
        self.loop.setChecked(s["loop"])
        self.skip.setChecked(s["skip_missing"])
        self.highlight.setChecked(s["highlight"])
        for control, k in ((self.start, "play_start"), (self.end, "play_end")):
            value = s.get(k) or s["start" if k == "play_start" else "end"]
            if value and not control.hasFocus():
                control.setDate(QDate.fromString(value, "yyyy-MM-dd"))
        self.slider.setRange(0, max(0, len(c.frames) - 1))
        self.slider.setValue(c.frame_index)
        self.buttons["play"].setText("Ⅱ 一時停止" if c.playing else "▶ 再生")
        if c.aggregate_busy:
            self.note.setText("集計中…（現在の表示と編集データを保持しています）")
        elif c.aggregate_error:
            self.note.setText(c.aggregate_error)
        elif c.active:
            v = c.frame.coverage
            frame_label = (
                f"{c.frame_index+1}/{len(c.frames)}フレーム" if c.frames else "再生範囲未設定"
            )
            self.note.setText(
                f'{c.frame.start}〜{c.frame.end} ／ {frame_label} ／ 取得{v["acquired"]}/{v["expected"]}日'
                + (" ／ 部分集計" if v["partial"] else "")
                + " ／ ズーム・配置・選択を保持"
            )
        self._building = False


def install(window):
    c = window.floor.temporal
    window.period_controls = {}
    for key, page in [
        ("floor", window.floor),
        ("dashboard", window.dashboard_page),
        ("analysis", window.analysis_page),
        ("candidates", window.candidate_page),
    ]:
        body = page.widget() if hasattr(page, "widget") else page
        control = PeriodControl(c, body)
        body.layout().insertWidget(0, control)
        window.period_controls[key] = control
        if key == "floor":
            window.floor.toolbar_rows.append(control)
    window.daily_settings = DailySettings(c)
    window.settings_page.widget().layout().insertWidget(1, window.daily_settings)
    from .temporal_analysis import TemporalAnalysis

    window.temporal_analysis = TemporalAnalysis(window.analysis_page)
    window.analysis_page.workspace_tabs.addTab(window.temporal_analysis, "日別・月別の推移")
    window.timeline = Timeline(c, window.floor)
    window.floor.layout().addWidget(window.timeline)
