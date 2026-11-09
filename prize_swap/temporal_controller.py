"""Shared period state, background imports and reversible view-only projections."""

from collections import OrderedDict
from copy import deepcopy
from dataclasses import asdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import threading
from PySide6.QtCore import (
    QObject,
    Signal,
    QTimer,
    QRunnable,
    QThreadPool,
    QFileSystemWatcher,
    QDate,
    Qt,
)
from .core import AppError
from .daily_reference import ReferenceLibrary, digest, stamp
from .reference_dates import business_date
from .temporal_data import build_frame, attach_comparison
from .machine_matching import MANUAL_FIELDS

MODES = {
    "live": "現在の編集データ",
    "day": "指定日",
    "7days": "直近7日",
    "30days": "直近30日",
    "month": "今月",
    "last_month": "先月",
    "custom": "任意期間",
}
DEFAULTS = dict(
    folder="",
    auto_update=True,
    mode="live",
    start="",
    end="",
    play_mode="daily",
    speed=1.0,
    loop=False,
    skip_missing=True,
    highlight=True,
    timeline_open=False,
    play_start="",
    play_end="",
)


def preferences(value=None):
    if value is not None and (not isinstance(value, dict) or set(value) - set(DEFAULTS)):
        raise AppError("日別参照の設定形式が不正です。")
    s = DEFAULTS | deepcopy(value or {})
    if (
        not isinstance(s["folder"], str)
        or s["mode"] not in MODES
        or s["play_mode"] not in ("daily", "monthly")
        or s["speed"] not in (0.5, 1.0, 2.0, 4.0)
    ):
        raise AppError("日別参照の設定値が不正です。")
    for k in ("auto_update", "loop", "skip_missing", "highlight", "timeline_open"):
        if type(s[k]) is not bool:
            raise AppError("再生・自動更新の設定値が不正です。")
    for k in ("start", "end", "play_start", "play_end"):
        if s[k] and not business_date(s[k]):
            raise AppError("日別参照の日付が不正です。")
    return s


def period_range(mode, start, end, today=None):
    today = today or datetime.now(timezone(timedelta(hours=9))).date()
    a = date.fromisoformat(start) if start else today
    b = date.fromisoformat(end) if end else a
    if mode == "day":
        b = a
    elif mode in ("7days", "30days"):
        b = a
        a = b - timedelta(days=6 if mode == "7days" else 29)
    elif mode == "month":
        a = today.replace(day=1)
        b = (a.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
    elif mode == "last_month":
        b = today.replace(day=1) - timedelta(days=1)
        a = b.replace(day=1)
    if a > b or (b - a).days > 3660:
        raise AppError("開始日≦終了日、最大10年の期間を指定してください。")
    return a.isoformat(), b.isoformat()


class ImportSignals(QObject):
    finished = Signal(object)
    progress = Signal(int, int, str)


class ImportWork(QRunnable):
    def __init__(self, db, folder, token, cancel):
        super().__init__()
        self.db = db
        self.folder = folder
        self.token = token
        self.cancel = cancel
        self.signals = ImportSignals()

    def run(self):
        try:
            result = ReferenceLibrary(self.db).scan(
                self.folder, self.cancel, self.signals.progress.emit
            )
        except Exception as exc:
            result = dict(
                files=0,
                parsed=0,
                cached=0,
                failed=1,
                warnings=[str(exc)],
                finished_at=stamp(),
                cancelled=False,
            )
        self.signals.finished.emit((self.token, result))


class FrameWork(QRunnable):
    def __init__(self, db, folder, masters, start, end, key, cancel, settings):
        super().__init__()
        self.db = db
        self.folder = folder
        self.masters = masters
        self.start = start
        self.end = end
        self.key = key
        self.cancel = cancel
        self.settings = settings
        self.signals = ImportSignals()

    def run(self):
        try:
            lib = ReferenceLibrary(self.db)
            docs, coverage = lib.selection(self.start, self.end, self.folder)
            overrides = {d["id"]: lib.preference("overrides:" + d["id"], {}) for d in docs}
            frame = build_frame(
                self.masters, docs, self.start, self.end, coverage, overrides, cancel=self.cancel
            )
            attach_comparison(frame, self.masters, lib, self.folder, self.settings, self.cancel)
            result = (self.key, frame, None)
        except Exception as exc:
            result = (self.key, None, str(exc))
        self.signals.finished.emit(result)


class TemporalController(QObject):
    changed = Signal()
    import_progress = Signal(int, int, str)
    imported = Signal(object)

    def __init__(self, floor):
        super().__init__(floor)
        self.floor = floor
        self.settings = floor.temporal_settings
        self.active = False
        self.master = None
        self.frame = None
        self.docs = []
        self.last_result = {}
        self._library = None
        self.cache = OrderedDict()
        self.builds = 0
        self._busy = False
        self._token = 0
        self._work = None
        self._cancel = threading.Event()
        self.playing = False
        self._playing_apply = False
        self.frames = []
        self.frame_index = 0
        self.differences = {}
        self._revision = 0
        self._closed = False
        self._jobs = {}
        self._requested = None
        self.aggregate_busy = False
        self.aggregate_error = ""
        self.watch = QFileSystemWatcher(self)
        self.watch.directoryChanged.connect(self.request_scan)
        self.auto_timer = QTimer(self)
        self.auto_timer.setInterval(20000)
        self.auto_timer.timeout.connect(self.request_scan)
        self.reload_timer = QTimer(self)
        self.reload_timer.setSingleShot(True)
        self.reload_timer.setInterval(500)
        self.reload_timer.timeout.connect(self.scan_async)
        self.play_timer = QTimer(self)
        self.play_timer.timeout.connect(self.next_frame)
        self.prefetch_timer = QTimer(self)
        self.prefetch_timer.setSingleShot(True)
        self.prefetch_timer.timeout.connect(self.prefetch)
        self.startup_timer = QTimer(self)
        self.startup_timer.setSingleShot(True)
        self.startup_timer.timeout.connect(self.startup)
        if self.settings["folder"]:
            self.startup_timer.start(0)

    @property
    def library(self):
        if self._library is None:
            self._library = ReferenceLibrary(self.floor.storage.with_suffix(".daily.sqlite3"))
        return self._library

    @property
    def physical_machines(self):
        return self.master["machines"] if self.active else self.floor.machines

    def startup(self):
        self.reload_documents()
        self.configure_watch()
        if self.settings["mode"] != "live" and self.docs:
            self.request_apply(self.settings["mode"], self.settings["start"], self.settings["end"])
        self.scan_async(initial=True)

    def configure_watch(self):
        if self.watch.directories():
            self.watch.removePaths(self.watch.directories())
        folder = self.settings["folder"]
        if self.settings["auto_update"] and folder:
            if Path(folder).is_dir():
                self.watch.addPath(folder)
            self.auto_timer.start()
        else:
            self.auto_timer.stop()

    def set_folder(self, folder):
        if not Path(folder).is_dir():
            raise AppError("参照フォルダーが見つかりません。")
        self.pause()
        if not self.to_live(save=False):
            return False
        self.settings.update(folder=str(Path(folder).resolve()), mode="live")
        self.cache.clear()
        self._token += 1
        self._cancel.set()
        self.configure_watch()
        self.floor.schedule_save()
        self.scan_async(initial=True)
        self.changed.emit()

    def request_scan(self, *_):
        if self.settings["auto_update"] and self.settings["folder"] and not self._busy:
            self.reload_timer.start()

    def scan_async(self, initial=False):
        if self._closed or self._busy or not self.settings["folder"]:
            return
        self._busy = True
        self._initial_scan = initial
        self._token += 1
        self._cancel = threading.Event()
        work = ImportWork(self.library.path, self.settings["folder"], self._token, self._cancel)
        self._work = work
        work.signals.progress.connect(self.import_progress)
        work.signals.finished.connect(self.scan_finished)
        QThreadPool.globalInstance().start(work)
        self.changed.emit()

    def scan_finished(self, payload):
        token, result = payload
        if self._closed:
            self._busy = False
            return
        if token != self._token:
            self._busy = False
            if self.settings["folder"]:
                self.scan_async(initial=True)
            return
        self._busy = False
        self._work = None
        self.last_result = result
        self.reload_documents()
        self.cache.clear()
        self._revision += 1
        if not result.get("cancelled"):
            self.ensure_initial_layout()
            if self._initial_scan and self.settings["mode"] == "live":
                usable, _ = self.library.usable(self.settings["folder"], full=False)
                if usable:
                    day = max(d["metadata"]["end"] for d in usable)
                    self.request_apply("day", day, day)
            elif self.active:
                self.request_apply(
                    self.settings["mode"], self.settings["start"], self.settings["end"]
                )
        self.imported.emit(result)
        self.changed.emit()
        self.floor.schedule_save()

    def reload_documents(self):
        self.docs = (
            self.library.documents(self.settings["folder"], full=False)
            if self.settings["folder"]
            else []
        )
        self.last_result = (
            self.library.preference("last_scan", {}) if self.settings["folder"] else {}
        )
        self.changed.emit()

    def ensure_initial_layout(self):
        f = self.floor
        if self.physical_machines or f.initialized:
            return
        usable, _ = self.library.usable(self.settings["folder"], full=False)
        doc = next((d for d in reversed(usable) if d["layout_count"]), None)
        if doc:
            doc = self.library.document(doc["id"])
            from .floor_data import validate_machines

            before = f.state()
            f.machines = validate_machines(doc["layouts"])
            f.original = deepcopy(doc["layouts"])
            f.number_references = deepcopy(doc["references"])
            f.source = doc["path"]
            f.loaded_at = doc["imported_at"]
            f.initialized = True
            f.rebuild()
            f.record(before, "日別参照の配置を初期読込")

    def frame_key(self, start, end):
        return (
            start,
            end,
            self._revision,
            digest([asdict(m) for m in self.physical_machines]),
            digest(self.floor.analysis_settings),
        )

    def queue_frame(self, key):
        if key in self._jobs:
            return
        cancel = threading.Event()
        work = FrameWork(
            self.library.path,
            self.settings["folder"],
            deepcopy(self.physical_machines),
            key[0],
            key[1],
            key,
            cancel,
            deepcopy(self.floor.analysis_settings),
        )
        self._jobs[key] = (work, cancel)
        work.signals.finished.connect(self.frame_finished)
        QThreadPool.globalInstance().start(work)

    def request_apply(self, mode, start="", end="", from_player=False):
        if self._closed:
            return False
        if mode == "live":
            return self.to_live()
        if not from_player:
            self.pause()
        if not self.floor.detail_controller.protect_edits():
            return False
        a, b = period_range(mode, start, end)
        key = self.frame_key(a, b)
        if key in self.cache:
            self._requested = None
            self.aggregate_busy = False
            return self.apply(mode, a, b, from_player, prepared_frame=self.cache[key])
        self._requested = (key, mode, a, b, from_player)
        self.aggregate_busy = True
        self.aggregate_error = ""
        for k, (_, cancel) in list(self._jobs.items()):
            if k != key:
                cancel.set()
        self.queue_frame(key)
        self.changed.emit()
        return True

    def frame_finished(self, result):
        key, frame, error = result
        self._jobs.pop(key, None)
        if self._closed:
            return
        if frame is not None and key == self.frame_key(*key[:2]):
            self.cache[key] = frame
            self.cache.move_to_end(key)
            self.builds += 1
            while len(self.cache) > 12:
                self.cache.popitem(last=False)
        if self._requested and self._requested[0] == key:
            _, mode, a, b, playing = self._requested
            self._requested = None
            self.aggregate_busy = False
            if frame is not None and key in self.cache:
                self.apply(mode, a, b, playing, prepared_frame=frame)
            else:
                self.aggregate_error = error or "集計をキャンセルしました。"
            self.changed.emit()

    def _frame(self, start, end):
        masters = self.physical_machines
        key = self.frame_key(start, end)
        if key not in self.cache:
            docs, coverage = self.library.selection(start, end, self.settings["folder"])
            overrides = {d["id"]: self.library.preference("overrides:" + d["id"], {}) for d in docs}
            self.cache[key] = build_frame(masters, docs, start, end, coverage, overrides)
            attach_comparison(
                self.cache[key],
                masters,
                self.library,
                self.settings["folder"],
                self.floor.analysis_settings,
            )
            self.builds += 1
            if len(self.cache) > 12:
                self.cache.popitem(last=False)
        self.cache.move_to_end(key)
        return self.cache[key]

    def apply(self, mode, start="", end="", from_player=False, prepared_frame=None):
        if mode == "live":
            return self.to_live()
        f = self.floor
        if not from_player:
            self.pause()
        if not f.detail_controller.protect_edits():
            return False
        a, b = period_range(mode, start, end)
        frame = prepared_frame if prepared_frame is not None else self._frame(a, b)
        old_frame = self.frame
        transform = f.view.transform()
        center = f.view.mapToScene(f.view.viewport().rect().center())
        zoom = f._zoom
        if not self.active:
            self.master = dict(
                machines=f.machines,
                analysis=f.analysis_state,
                edit=f.presentation["edit"],
                reference=f.reference_date.date(),
                follow=f.follow_today,
            )
        f.follow_today = False
        self.active = True
        self.frame = frame
        self.settings.update(mode=mode, start=a, end=b)
        self._playing_apply = from_player
        self.update_differences(old_frame, frame)
        f.machines = frame.machines
        f.analysis_state = frame.snapshot
        f.presentation["edit"] = False
        f.edit_mode.blockSignals(True)
        f.edit_mode.setChecked(False)
        f.edit_mode.blockSignals(False)
        f.edit_mode.setEnabled(False)
        f.reference_date.blockSignals(True)
        f.reference_date.setDate(QDate.fromString(b, "yyyy-MM-dd"))
        f.reference_date.blockSignals(False)
        f.reference_date.setEnabled(False)
        f.rebuild()
        f.view.setTransform(transform)
        f.view.centerOn(center)
        f._zoom = zoom
        if f.detail_controller.panel:
            f.detail_controller.refresh_current()
            self.protect_detail()
        f.changed.emit()
        self.changed.emit()
        if not from_player:
            f.schedule_save()
        self.prefetch_timer.start(0)
        return True

    def to_live(self, save=True):
        self.pause()
        self._requested = None
        self.aggregate_busy = False
        for _, cancel in self._jobs.values():
            cancel.set()
        if not self.active:
            self.settings["mode"] = "live"
            self.changed.emit()
            return True
        if not self.floor.detail_controller.protect_edits():
            return False
        f = self.floor
        transform = f.view.transform()
        center = f.view.mapToScene(f.view.viewport().rect().center())
        zoom = f._zoom
        f.machines = self.master["machines"]
        f.analysis_state = self.master["analysis"]
        f.presentation["edit"] = self.master["edit"]
        f.edit_mode.setEnabled(True)
        f.edit_mode.blockSignals(True)
        f.edit_mode.setChecked(self.master["edit"])
        f.edit_mode.blockSignals(False)
        f.reference_date.blockSignals(True)
        f.reference_date.setDate(self.master["reference"])
        f.reference_date.blockSignals(False)
        f.reference_date.setEnabled(True)
        f.follow_today = self.master["follow"]
        self.active = False
        self.master = None
        self.frame = None
        self.settings["mode"] = "live"
        self.differences = {}
        self.protect_detail()
        f.rebuild()
        f.view.setTransform(transform)
        f.view.centerOn(center)
        f._zoom = zoom
        f.changed.emit()
        self.changed.emit()
        if save:
            f.schedule_save()
        return True

    def protect_detail(self):
        from PySide6.QtWidgets import (
            QLineEdit,
            QPlainTextEdit,
            QComboBox,
            QAbstractSpinBox,
            QDialogButtonBox,
        )

        p = self.floor.detail_controller.panel
        if not p:
            return
        period = bool(
            self.active
            and self.frame
            and (self.frame.start != self.frame.end or not self.frame.source_ids)
        )
        # Daily edits are date-specific; period totals are reference-only. Planned products remain live.
        for i in range(1, p.tabs.count()):
            p.tabs.setTabEnabled(i, not self.active)
        buttons = p.findChild(QDialogButtonBox)
        if buttons:
            buttons.button(QDialogButtonBox.StandardButton.Save).setEnabled(not period)
        editors = [
            *p.findChildren(QLineEdit),
            *p.findChildren(QPlainTextEdit),
            *p.findChildren(QComboBox),
            *p.findChildren(QAbstractSpinBox),
        ]
        for editor in editors:
            if period and not editor.property("temporalLocked"):
                editor.setProperty("temporalWasEnabled", editor.isEnabled())
                editor.setProperty("temporalLocked", True)
                editor.setEnabled(False)
            elif not period and editor.property("temporalLocked"):
                editor.setEnabled(bool(editor.property("temporalWasEnabled")))
                editor.setProperty("temporalLocked", False)
        if hasattr(p, "header_meta") and self.active:
            base = p.header_meta.text().split("\n履歴参照：")[0]
            v = self.frame.coverage
            differences = self.differences.get(p.original.uid, [])
            p.header_meta.setText(
                base
                + f'\n履歴参照：{self.frame.start}〜{self.frame.end} ／ 取得{v["acquired"]}/{v["expected"]}日・未取得{v["missing"]}日\n期間集計は参照専用／指定日の編集は日別キャッシュに保存'
                + ("\n" + " / ".join(differences) if differences else "")
            )

    def save_daily(self, panel):
        if not self.active:
            return False
        if self.frame.start != self.frame.end or len(self.frame.source_ids) != 1:
            raise AppError("期間集計値は編集できません。指定日へ切り替えてください。")
        from PySide6.QtGui import QUndoCommand

        key = "overrides:" + self.frame.source_ids[0]
        before = self.library.preference(key, {})
        after = deepcopy(before)
        values = after.setdefault(panel.original.uid, {})
        allowed = set(MANUAL_FIELDS) | {
            "prize_cost_product",
            "prize_cost_origin",
            "play_pricing",
            "po_definition",
            "po_definition_fingerprint",
        }
        for field in allowed:
            if getattr(panel.updated, field) != getattr(panel.baseline, field):
                values[field] = deepcopy(getattr(panel.updated, field))
        values["_updated_at"] = stamp()
        controller = self
        mode = self.settings["mode"]
        a, b = self.frame.start, self.frame.end

        class EditDay(QUndoCommand):
            def __init__(self):
                super().__init__("日別実績の手動補正")

            def set_value(self, value):
                controller.library.set_preference(key, value)
                controller.cache.clear()
                controller._revision += 1
                # Discard only the just-saved draft before refreshing the same machine.
                panel.baseline = deepcopy(panel.updated)
                controller.apply(mode, a, b)
                machine = next(m for m in controller.floor.machines if m.uid == panel.original.uid)
                panel.load_machine(machine)
                controller.protect_detail()

            def redo(self):
                self.set_value(after)

            def undo(self):
                self.set_value(before)

        self.floor.history.push(EditDay())
        self.floor.schedule_save()
        return True

    def update_differences(self, old, new):
        from .floor_status import effective_stat, ratio
        from .sales_evaluation import evaluation
        from .analysis_settings import sales_boundaries
        from .replacement import assess

        self.differences = {}
        if not old:
            return
        if (date.fromisoformat(old.end) - date.fromisoformat(old.start)) != (
            date.fromisoformat(new.end) - date.fromisoformat(new.start)
        ):
            return
        s = self.floor.analysis_settings
        oldmap = {m.uid: m for m in old.machines}
        ob = sales_boundaries(old.machines, s, old.snapshot)
        nb = sales_boundaries(new.machines, s, new.snapshot)
        for m in new.machines:
            prior = oldmap.get(m.uid)
            if not prior:
                continue
            differences = []
            sale = effective_stat(m, "sales")
            previous = effective_stat(prior, "sales")
            r, prev = ratio(m), ratio(prior)
            if sale != previous:
                differences.append(
                    "売上："
                    + ("データなし" if sale is None else f"{sale:,.0f}円")
                    + (
                        f"（前回差 {sale-previous:+,.0f}円）"
                        if sale is not None and previous is not None
                        else ""
                    )
                )
            if evaluation(m, s, nb)["index"] != evaluation(prior, s, ob)["index"]:
                differences.append("売上評価が変化")
            if r != prev:
                differences.append(
                    "アベ比："
                    + ("不明" if r is None else f"{r:.1f}%")
                    + (f"（{r-prev:+.1f}pt）" if r is not None and prev is not None else "")
                )
            if m.product != prior.product:
                differences.append(
                    "商品変更：" + (prior.product or "不明") + " → " + (m.product or "不明")
                )
            if (
                assess(m, date.fromisoformat(new.end), s, sales_bounds=nb)["status"]
                != assess(prior, date.fromisoformat(old.end), s, sales_bounds=ob)["status"]
            ):
                differences.append("入替候補の判定が変化")
            if differences:
                self.differences[m.uid] = differences

    def timeline_frames(self):
        a, b = self.settings["start"], self.settings["end"]
        if not a or not b:
            return []
        start, end = date.fromisoformat(a), date.fromisoformat(b)
        out = []
        if self.settings["play_mode"] == "monthly":
            cursor = start.replace(day=1)
            while cursor <= end:
                stop = (cursor.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(
                    days=1
                )
                out.append((cursor.isoformat(), stop.isoformat()))
                cursor = stop + timedelta(days=1)
        else:
            available = set()
            for d in self.library.usable(self.settings["folder"], full=False)[0]:
                if d["metadata"]["data_type"] == "daily":
                    available.add(d["metadata"]["start"])
            cursor = start
            while cursor <= end:
                if not self.settings["skip_missing"] or cursor.isoformat() in available:
                    out.append((cursor.isoformat(), cursor.isoformat()))
                cursor += timedelta(days=1)
        return out

    def prepare_playback(self, start, end):
        a, b = period_range("custom", start, end)
        self.pause()
        self._play_range = (a, b)
        old_a, old_b = self.settings["start"], self.settings["end"]
        self.settings.update(start=a, end=b)
        self.frames = self.timeline_frames()
        self.settings.update(start=old_a, end=old_b)
        self.frame_index = 0
        if self.frames:
            self.seek(0)
        self.changed.emit()
        return len(self.frames)

    def play(self):
        if not self.frames:
            return
        if not self.floor.detail_controller.protect_edits():
            return
        self.playing = True
        self.play_timer.start(round(1000 / self.settings["speed"]))
        self.changed.emit()

    def pause(self):
        self.playing = False
        self.play_timer.stop()
        self.prefetch_timer.stop()
        if not self._closed:
            self.changed.emit()

    def next_frame(self):
        if self.aggregate_busy:
            return
        if self.frame_index + 1 >= len(self.frames):
            if self.settings["loop"]:
                self.seek(0)
            else:
                self.pause()
        else:
            self.seek(self.frame_index + 1)

    def seek(self, index):
        if not self.frames:
            return
        self.frame_index = max(0, min(len(self.frames) - 1, index))
        a, b = self.frames[self.frame_index]
        if not self.request_apply("custom" if a != b else "day", a, b, from_player=True):
            self.pause()
        self.changed.emit()

    def prefetch(self):
        if self.playing and self.frames and self.frame_index + 1 < len(self.frames):
            key = self.frame_key(*self.frames[self.frame_index + 1])
            if key not in self.cache:
                self.queue_frame(key)

    def shutdown(self):
        self._closed = True
        page = getattr(self.floor, "analysis_page", None)
        if page:
            for i in range(page.workspace_tabs.count()):
                widget = page.workspace_tabs.widget(i)
                if hasattr(widget, "shutdown"):
                    widget.shutdown()
        self._requested = None
        for _, cancel in self._jobs.values():
            cancel.set()
        self.pause()
        self._cancel.set()
        self._token += 1
        self.auto_timer.stop()
        self.reload_timer.stop()
        self.startup_timer.stop()
