"""Small, collapsible management overview. Clicks highlight, never filter geometry."""

from datetime import datetime, timezone
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QToolButton
from .floor_status import ratio
from .machine_matching import machine_key
from .performance_validation import period_text

BINS = ("50%未満", "50〜100%未満", "100〜200%未満", "200%以上", "不明")


def distribution(machines):
    groups = {name: [] for name in BINS}
    for m in machines:
        value = ratio(m)
        key = (
            "不明"
            if value is None
            else (
                "50%未満"
                if value < 50
                else (
                    "50〜100%未満"
                    if value < 100
                    else "100〜200%未満" if value < 200 else "200%以上"
                )
            )
        )
        groups[key].append(m.uid)
    total = len(machines)
    return {
        key: dict(count=len(ids), percentage=len(ids) / total * 100 if total else 0, uids=ids)
        for key, ids in groups.items()
    }


def freshness(snapshot, stale_days=7, now=None):
    stamp = snapshot.get("extracted_at")
    if not stamp:
        return "抽出日時不明（鮮度未評価）"
    try:
        point = datetime.fromisoformat(stamp)
        if point.tzinfo is None:
            point = point.replace(tzinfo=timezone.utc)
        age = ((now or datetime.now(timezone.utc)) - point).total_seconds() / 86400
        if age < 0:
            return "抽出日時が未来（要確認）"
        return (
            f"古いデータ（抽出から{int(age)}日）" if age > stale_days else f"抽出から{int(age)}日"
        )
    except ValueError:
        return "抽出日時不明（鮮度未評価）"


class FloorDashboard(QWidget):
    def __init__(self, owner):
        super().__init__()
        self.owner = owner
        self.compact = False
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(2)
        row = QHBoxLayout()
        outer.addLayout(row)
        self.toggle = QToolButton()
        self.toggle.setText("売り場ダッシュボード ▾")
        self.toggle.setCheckable(True)
        self.toggle.setChecked(True)
        row.addWidget(self.toggle)
        self.summary = QLabel()
        row.addWidget(self.summary, 1)
        self.content = QWidget()
        self.content.setObjectName("dashboardCard")
        body = QVBoxLayout(self.content)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(2)
        self.dates = QLabel()
        self.dates.setWordWrap(True)
        body.addWidget(self.dates)
        bins = QHBoxLayout()
        body.addLayout(bins)
        self.buttons = {}
        for key in BINS:
            b = QToolButton()
            b.clicked.connect(
                lambda checked=False, k=key: owner.highlight_group(self.groups[k]["uids"])
            )
            bins.addWidget(b)
            self.buttons[key] = b
        clear = QToolButton()
        clear.setText("強調解除")
        clear.clicked.connect(lambda: owner.highlight_group([]))
        bins.addWidget(clear)
        bins.addStretch()
        outer.addWidget(self.content)
        self.toggle.toggled.connect(self.expanded)
        self.toggle.clicked.connect(self.open_compact_dashboard)
        self.groups = distribution([])

    def set_compact(self, value):
        """Reserve canvas space on short high-DPI desktops, without saving a UI override."""
        if self.compact == value:
            return
        self.compact = value
        self.toggle.blockSignals(True)
        self.toggle.setCheckable(not value)
        self.toggle.setChecked(self.owner.analysis_settings["dashboard_visible"])
        self.toggle.blockSignals(False)
        self.toggle.setText("集計の詳細 ↗" if value else "売り場ダッシュボード ▾")
        self.content.setVisible(not value and self.owner.analysis_settings["dashboard_visible"])

    def open_compact_dashboard(self):
        window = self.owner.window()
        if self.compact and hasattr(window, "dashboard_page"):
            window.tabs.setCurrentWidget(window.dashboard_page)

    def expanded(self, value):
        if self.compact:
            return
        self.content.setVisible(value)
        self.owner.analysis_settings["dashboard_visible"] = value
        self.owner.schedule_save()

    def refresh(self):
        from .sales_analysis import import_summary
        from .replacement import assess

        o = self.owner
        self.groups = distribution(o.machines)
        data = import_summary(o.machines, o.analysis_state)
        from .candidate_page import candidate_key

        excluded = o.candidate_ui["exclusions"]
        candidates = sum(
            assess(m, o.reference_day(), o.analysis_settings)["status"] in ("要確認", "入替検討")
            for m in o.machines
            if candidate_key(m) not in excluded
        )
        plans = sum(bool(o.assignments().get(machine_key(m))) for m in o.machines)
        self.metrics = dict(
            total=len(o.machines),
            candidates=candidates,
            planned=plans,
            unmatched=len(data["unmatched"]) + len(o.analysis_state.get("unmatched_rows", [])),
        )
        self.summary.setText(
            f'管理 {len(o.machines)} ｜ 入替検討 {candidates} ｜ 投入予定 {plans} ｜ 未照合 {len(data["unmatched"])+len(o.analysis_state.get("unmatched_rows",[]))}'
        )
        self.dates.setText(
            f'{period_text(o.analysis_state)} ｜ 抽出 {o.analysis_state.get("extracted_at") or "不明"} ｜ 読込 {o.analysis_state["loaded_at"] or "未読込"} ｜ {freshness(o.analysis_state,o.analysis_settings["stale_days"])}'
        )
        self.dates.setToolTip(o.analysis_state["file_name"])
        self.dates.setVisible(bool(o._fullscreen_host) or not hasattr(o.window(), "page_metadata"))
        for key, b in self.buttons.items():
            d = self.groups[key]
            b.setText(f'{key}：{d["count"]} ({d["percentage"]:.1f}%)')
        self.toggle.blockSignals(True)
        self.toggle.setChecked(o.analysis_settings["dashboard_visible"])
        self.toggle.blockSignals(False)
        self.content.setVisible(not self.compact and o.analysis_settings["dashboard_visible"])
        self.toggle.setToolTip(self.dates.text() + "\n" + o.info.text())
