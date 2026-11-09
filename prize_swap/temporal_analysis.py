"""Missing-aware time series built off-thread from the same period projections."""

from collections import Counter
from copy import deepcopy
from datetime import date, timedelta
from PySide6.QtCore import QObject, Signal, QRunnable, QThreadPool, QTimer, Qt, QRectF, QPointF
from PySide6.QtGui import QPainter, QColor, QPen, QPainterPath
from PySide6.QtWidgets import (
    QWidget,
    QScrollArea,
    QVBoxLayout,
    QGridLayout,
    QFrame,
    QLabel,
    QPushButton,
    QToolTip,
)
import threading
from .daily_reference import ReferenceLibrary, digest
from .temporal_data import build_frame, rollup_samples
from .analytics_data import machine_record, bucket, formatted
from .analysis_settings import sales_boundaries
from .theme import tokens, manager
from .analytics_charts import series_color
from .distributions import COLORS


def series_summary(frame, settings, uids=None):
    """All-Excel ranking stays global even when the plotted floor scope is selected."""
    machines = [m for m in frame.machines if uids is None or m.uid in uids]
    bounds = sales_boundaries(frame.machines, settings, frame.snapshot)
    records = [
        machine_record(m, date.fromisoformat(frame.end), settings, sales_bounds=bounds)
        for m in machines
    ]
    known = [m for m in machines if getattr(m, "temporal_summary", {}).get("sales_days", 0)]
    values, detail = rollup_samples(known, [1] * len(known))
    # This is a cross-machine total over one period; no averaging of machine ratios.
    value_sales = [r["sales"] for r in records if r["sales"] is not None]
    return dict(
        start=frame.start,
        end=frame.end,
        label=frame.start[5:] if frame.start == frame.end else frame.start[:7],
        sales=sum(value_sales) if value_sales else None,
        cost_rate=values["cost_rate"],
        po_rate=values["po_rate"],
        target=len(machines),
        valid_sales=len(value_sales),
        coverage=frame.coverage,
        uids=[m.uid for m in machines],
        ratio_bins=dict(Counter(bucket(r, "ratio_bin", settings) for r in records)),
        sales_bins=dict(Counter(bucket(r, "sales_bin", settings) for r in records)),
        candidates=sum(r["assessment"] in ("要確認", "入替検討") for r in records),
        candidate_bins=dict(Counter(r["assessment"] for r in records)),
        members={
            axis: {
                key: [
                    r["uid"]
                    for r in records
                    if (bucket(r, axis, settings) if axis != "assessment" else r["assessment"])
                    == key
                ]
                for key in {
                    bucket(r, axis, settings) if axis != "assessment" else r["assessment"]
                    for r in records
                }
            }
            for axis in ("ratio_bin", "sales_bin", "assessment")
        },
    )


def time_series(db, folder, masters, start, end, settings, uids=None, cancel=None):
    lib = ReferenceLibrary(db)
    usable = lib.usable(folder, full=False)
    daily = []
    monthly = []

    def one(a, b):
        if cancel and cancel.is_set():
            raise RuntimeError("集計キャンセル")
        docs, coverage = lib.selection(a, b, folder, usable_docs=usable)
        overrides = {d["id"]: lib.preference("overrides:" + d["id"], {}) for d in docs}
        if not docs:
            keys = [m.uid for m in masters if uids is None or m.uid in uids]
            n = len(keys)
            return dict(
                start=a,
                end=b,
                label=a[5:] if a == b else a[:7],
                sales=None,
                cost_rate=None,
                po_rate=None,
                target=n,
                valid_sales=0,
                coverage=coverage,
                uids=keys,
                ratio_bins={"不明": n},
                sales_bins={"不明": n},
                candidates=None,
                candidate_bins={"判定保留": n},
                members={
                    "ratio_bin": {"不明": keys},
                    "sales_bin": {"不明": keys},
                    "assessment": {"判定保留": keys},
                },
            )
        frame = build_frame(masters, docs, a, b, coverage, overrides, cancel)
        return series_summary(frame, settings, uids)

    cursor = date.fromisoformat(start)
    stop = date.fromisoformat(end)
    while cursor <= stop:
        key = cursor.isoformat()
        daily.append(one(key, key))
        cursor += timedelta(days=1)
    cursor = date.fromisoformat(start).replace(day=1)
    while cursor <= stop:
        last = (cursor.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
        monthly.append(one(max(start, cursor.isoformat()), min(end, last.isoformat())))
        cursor = last + timedelta(days=1)
    return dict(daily=daily, monthly=monthly)


class Signals(QObject):
    finished = Signal(object)


class SeriesWork(QRunnable):
    def __init__(self, args, key, cancel):
        super().__init__()
        self.args = args
        self.key = key
        self.cancel = cancel
        self.signals = Signals()

    def run(self):
        try:
            result = (self.key, time_series(*self.args, cancel=self.cancel), None)
        except Exception as exc:
            result = (self.key, None, str(exc))
        self.signals.finished.emit(result)


class TrendChart(QWidget):
    activated = Signal(object)

    def __init__(self, key, title, parent=None):
        super().__init__(parent)
        self.key = key
        self.title = title
        self.rows = []
        self.hits = []
        self.setMinimumHeight(210)
        self.setMinimumWidth(220)
        self.setMouseTracking(True)
        manager().changed.connect(self.update)

    def set_rows(self, rows):
        self.rows = rows
        self.setAccessibleDescription(
            self.title + "\n" + "\n".join(self.description(r) for r in rows)
        )
        self.update()

    def description(self, r):
        val = r.get(self.key)
        return (
            f'{r["start"]}〜{r["end"]} / '
            + (str(val) if val is not None else "データなし")
            + f'\n対象{r["target"]}台・売上有効{r["valid_sales"]}台・取得{r["coverage"]["acquired"]}/{r["coverage"]["expected"]}日'
        )

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        t = tokens()
        p.fillRect(self.rect(), QColor(t["card"]))
        p.setPen(QColor(t["text"]))
        font = p.font()
        font.setBold(True)
        p.setFont(font)
        p.drawText(
            QRectF(10, 5, self.width() - 20, 25),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            self.title,
        )
        self.hits = []
        if not self.rows:
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "期間を選択してください")
            return
        area = QRectF(52, 38, max(1, self.width() - 64), max(1, self.height() - 78))
        step = area.width() / len(self.rows)
        stacked = self.key.endswith("_bins")
        maxval = max(
            [sum(r[self.key].values()) if stacked else r.get(self.key) or 0 for r in self.rows]
            + [1]
        )
        for i in range(3):
            y = area.bottom() - area.height() * i / 2
            p.setPen(QColor(t["border"]))
            p.drawLine(QPointF(area.left(), y), QPointF(area.right(), y))
            p.setPen(QColor(t["muted"]))
            p.drawText(
                QRectF(0, y - 10, 48, 20),
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                (
                    f"{maxval*i/2:.0%}"
                    if self.key in ("cost_rate", "po_rate")
                    else f"{maxval*i/2:,.0f}"
                ),
            )
        last = None
        for i, r in enumerate(self.rows):
            x = area.left() + step * (i + 0.5)
            if stacked:
                y = area.bottom()
                axis = (
                    "ratio_bin"
                    if self.key == "ratio_bins"
                    else "sales_bin" if self.key == "sales_bins" else "assessment"
                )
                for label, value in r[self.key].items():
                    h = value / maxval * area.height()
                    rect = QRectF(x - step * 0.35, y - h, max(1, step * 0.7), h)
                    color = COLORS.get(label, series_color(label).name())
                    p.fillRect(rect, QColor(color))
                    p.setPen(QColor(t["border"]))
                    p.drawRect(rect)
                    self.hits.append((rect, r, label, r["members"][axis].get(label, [])))
                    y -= h
            elif r.get(self.key) is not None:
                value = r[self.key]
                point = QPointF(x, area.bottom() - value / maxval * area.height())
                p.setPen(QPen(QColor(t["accent"]), 2))
                if last is not None:
                    p.drawLine(last, point)
                p.setBrush(QColor(t["accent"]))
                p.drawEllipse(point, 3, 3)
                last = point
                self.hits.append(
                    (QRectF(x - step / 2, area.top(), step, area.height()), r, "", r["uids"])
                )
            else:
                last = None
            if i in {0, len(self.rows) // 2, len(self.rows) - 1}:
                p.setPen(QColor(t["text"]))
                p.drawText(
                    QRectF(x - 40, area.bottom() + 5, 80, 25),
                    Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop,
                    r["label"],
                )
        p.end()

    def mouseMoveEvent(self, event):
        for rect, row, label, uids in reversed(self.hits):
            if rect.contains(event.position()):
                QToolTip.showText(
                    event.globalPosition().toPoint(),
                    self.description(row) + ("\n" + label if label else ""),
                    self,
                )
                return
        QToolTip.hideText()
        super().mouseMoveEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            for rect, row, label, uids in reversed(self.hits):
                if rect.contains(event.position()):
                    self.activated.emit((row, uids))
                    return
        super().mousePressEvent(event)


class TemporalAnalysis(QScrollArea):
    def __init__(self, page):
        super().__init__(page)
        self.page = page
        self.c = page.owner.temporal
        self._key = None
        self._jobs = {}
        self.data = None
        self.setWidgetResizable(True)
        body = QWidget()
        self.setWidget(body)
        root = QVBoxLayout(body)
        self.note = QLabel("参照フォルダーを登録し、日付・期間を選択すると推移を表示します。")
        self.note.setWordWrap(True)
        root.addWidget(self.note)
        self.tiles = QWidget()
        self.grid = QGridLayout(self.tiles)
        root.addWidget(self.tiles)
        self.charts = []
        for key, title in [
            ("sales", "日別売上推移"),
            ("monthly_sales", "月別売上推移"),
            ("ratio_bins", "アベ比分布の推移"),
            ("sales_bins", "売上評価分布の推移"),
            ("cost_rate", "原価率の推移（合計原価÷合計売上）"),
            ("po_rate", "P/O率の推移（獲得数÷プレイ回数）"),
            ("candidate_bins", "入替候補件数の推移"),
        ]:
            chart = TrendChart("sales" if key == "monthly_sales" else key, title)
            chart.series_key = key
            chart.activated.connect(self.navigate)
            self.charts.append(chart)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(120)
        self.timer.timeout.connect(self.refresh)
        self.c.changed.connect(self.invalidate)
        page.owner.selection_state.changed.connect(self.invalidate)
        page.selection_scope.currentIndexChanged.connect(self.invalidate)
        page.owner.changed.connect(self.invalidate)
        self.reflow()

    def invalidate(self, *_):
        if self.isVisible():
            self.timer.start()

    def showEvent(self, event):
        super().showEvent(event)
        self.refresh()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.reflow()

    def reflow(self):
        cols = 3 if self.viewport().width() >= 1300 else 2 if self.viewport().width() >= 650 else 1
        for i, chart in enumerate(self.charts):
            self.grid.addWidget(chart, i // cols, i % cols)

    def refresh(self):
        c = self.c
        if not c.active:
            self._key = None
            self.data = None
            for chart in self.charts:
                chart.set_rows([])
            self.note.setText("日付・期間を選択すると推移を表示します。")
            return
        a, b = c.frame.start, c.frame.end
        uids = self.page.target_uids()
        settings = c.floor.analysis_settings
        key = (c.frame_key(a, b), tuple(sorted(uids)) if uids is not None else None)
        if key == self._key:
            return
        self._key = key
        for _, cancel in self._jobs.values():
            cancel.set()
        cancel = threading.Event()
        args = (
            c.library.path,
            c.settings["folder"],
            deepcopy(c.physical_machines),
            a,
            b,
            deepcopy(settings),
            set(uids) if uids is not None else None,
        )
        work = SeriesWork(args, key, cancel)
        self._jobs[key] = (work, cancel)
        work.signals.finished.connect(self.finished)
        QThreadPool.globalInstance().start(work)
        self.note.setText("時系列を集計中…（欠損日を0として補完しません）")

    def finished(self, result):
        key, data, error = result
        self._jobs.pop(key, None)
        if key != self._key or self.c._closed:
            return
        if error:
            self.note.setText(error)
            return
        self.data = data
        self.note.setText(
            "選択期間内の実データのみ。欠損箇所では線を接続しません。比率は対応する分子・分母から算出します。分布をクリックすると当日・該当台を表示します。"
        )
        for chart in self.charts:
            chart.set_rows(data["monthly" if chart.series_key == "monthly_sales" else "daily"])

    def navigate(self, payload):
        row, uids = payload
        if not uids:
            return
        c = self.c
        called = [False]

        def done():
            if (
                c.aggregate_busy
                or called[0]
                or not c.active
                or (c.frame.start, c.frame.end) != (row["start"], row["end"])
            ):
                return
            called[0] = True
            c.changed.disconnect(done)
            self.page.host_window.highlight_analysis_machines(uids, True)

        c.changed.connect(done)
        c.request_apply("day" if row["start"] == row["end"] else "custom", row["start"], row["end"])
        done()

    def shutdown(self):
        self.timer.stop()
        for _, cancel in self._jobs.values():
            cancel.set()
