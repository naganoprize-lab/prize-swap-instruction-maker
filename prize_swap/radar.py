"""Five different state indicators, deliberately without an overall score."""

import math
from PySide6.QtCore import Qt, QPointF, QRectF, Signal, QEvent
from PySide6.QtGui import QPainter, QPen, QColor, QPolygonF
from PySide6.QtWidgets import QWidget, QToolTip
from .analytics_data import machine_record, formatted
from .analysis_settings import validate_settings
from .theme import tokens, manager

AGE_EXPLANATION = "投入日数は設置からの経過期間を示し、長いほど新しさの評価が低くなります。売上不振を意味するものではありません。"


def interpolate(value, points):
    if value is None or not math.isfinite(value):
        return None
    if value <= points[0][0]:
        return float(points[0][1])
    for (a, va), (b, vb) in zip(points, points[1:]):
        if value <= b:
            return va + (vb - va) * (value - a) / (b - a)
    return float(points[-1][1])


def sales_score(machine, peers, settings):
    """Midrank percentile of total sales for all machines in this source/period."""
    r = machine
    values = [
        p["sales"]
        for p in peers
        if p.get("score_comparable")
        and p.get("sales_cohort") == r.get("sales_cohort")
        and p.get("sales") is not None
        and p["sales"] >= 0
    ]
    n = len(values)
    if not r.get("score_comparable") or r.get("sales") is None or n < 2:
        return None, f"同じ集計期間の全台比較：有効{n}台。比較対象不足。"
    if len(set(values)) == 1:
        return 50.0, f"全{n}台が同額のため中立の状態です。"
    if n < 4:
        return None, f"有効{n}台：比較対象不足のため順位で優劣をつけません。"
    low = sum(v < r["sales"] for v in values)
    ties = sum(v == r["sales"] for v in values)
    return (low + (ties - 1) / 2) / (
        n - 1
    ) * 100, f"売上金額・同じ集計期間の全{n}台比較（同額は同順位）。"


def score_record(machine, reference, settings, assignments=None):
    from .replacement import episode
    from .sales_evaluation import cohort

    r = machine_record(machine, reference, settings, assignments)
    a = machine.analysis or {}
    r["sales_cohort"] = cohort(machine)
    r["score_comparable"] = not (
        a.get("quality_issues")
        or a.get("product_context") is not None
        and a["product_context"] != episode(machine)
        or machine.match_method == "review"
    )
    return r


def evaluate(machine, reference, settings=None, peers=(), assignments=None):
    s = validate_settings(settings)
    r = score_record(machine, reference, s, assignments)
    sales, comparison = sales_score(r, peers, s)
    target = s["cost_target"]
    cost_points = list(
        zip([target * k for k in (0.5, 2 / 3, 5 / 6, 1, 7 / 6, 4 / 3)], [0, 50, 75, 100, 75, 0])
    )
    from .po_evaluation import evaluate_po

    po = evaluate_po(machine, s)
    po_target = po["target"]
    age = r["days"]
    specs = [
        (
            "ratio",
            "アベ比",
            interpolate(r["ratio"], [(0, 0), (100, 50), (200, 100)]),
            "アベ比の状態",
            "既存の基準アベレージに対する稼働比率。",
        ),
        ("sales", "売上", sales, "同期間の全台・売上金額で比較", comparison),
        (
            "days",
            "投入日数",
            (
                interpolate(age, [(0, 100), (7, 80), (14, 60), (21, 40), (28, 20), (35, 0)])
                if age is not None and age >= 0
                else None
            ),
            "投入の新しさ",
            AGE_EXPLANATION,
        ),
        (
            "cost_rate",
            "原価率",
            interpolate(r["cost_rate"], cost_points) if target > 0 else None,
            f"目標 {target:.0%}",
            "目標原価率への適合度。低いほど好成績とは限りません。",
        ),
        (
            "po_rate",
            "PO率",
            po["score"],
            f"理論目安 {po_target:.2%}" if po_target is not None else "理論目安：計算不可",
            po["explanation"] + " / " + po["cost_source"] + " / " + po["price_source"],
        ),
    ]
    output = []
    for key, label, score, goal, note in specs:
        actual = formatted(key, r[key])
        if key == "sales":
            actual = "データなし" if r["sales"] is None else f'{formatted("sales",r["sales"])}円'
        elif key == "days" and age is not None and age >= 0:
            actual += "日"
        if r[key] is None:
            actual = "データなし"
        warning = " / ".join(po["warnings"]) if key == "po_rate" else ""
        if key == "cost_rate" and r[key] is not None and r[key] < s["cost_low"]:
            warning = "低原価率・要確認（入替確定ではありません）"
        if key == "days" and age is not None and age < 0:
            warning = "設置日は基準日より未来です。投入予定として未評価。"
        if score is None:
            warning = (warning + " " + "未評価：有効データまたは比較対象不足").strip()
        output.append(
            dict(
                key=key,
                label=label,
                score=score,
                actual=actual,
                goal=goal,
                note=note,
                warning=warning,
                uid=machine.uid,
            )
        )
    return output


class RadarChart(QWidget):
    activated = Signal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.axes = []
        self.hit_targets = []
        self.setMinimumSize(300, 300)
        self.setMouseTracking(True)
        self.setAccessibleName("5項目の状態分析レーダーチャート")
        manager().changed.connect(self.theme_changed)

    def theme_changed(self, *_):
        self.update()

    def set_axes(self, axes):
        self.axes = axes
        self.setAccessibleDescription("\n".join(self.tooltip(a) for a in axes))
        self.update()

    @staticmethod
    def tooltip(a):
        status = "未評価" if a["score"] is None else "状態を表示"
        return f'{a["label"]}\n実測値：{a["actual"]}\n{status}\n{a["goal"]}\n{a["note"]}\n{a["warning"]}'

    def tooltip_at(self, point):
        for rect, a in self.hit_targets:
            if rect.contains(point):
                return self.tooltip(a)
        return ""

    def event(self, event):
        if event.type() == QEvent.Type.ToolTip:
            text = self.tooltip_at(QPointF(event.pos()))
            if text:
                QToolTip.showText(event.globalPos(), text, self)
            else:
                QToolTip.hideText()
            return True
        return super().event(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            for rect, a in self.hit_targets:
                if rect.contains(event.position()):
                    if a.get("uid"):
                        self.activated.emit([a["uid"]])
                    return
        super().mousePressEvent(event)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        t = tokens()
        p.fillRect(self.rect(), QColor(t["card"]))
        cx, cy = self.width() / 2, self.height() / 2 + 5
        radius = max(35, min(self.width() * 0.31, (self.height() - 100) * 0.47))
        vertices = [
            QPointF(
                cx + math.cos(-math.pi / 2 + i * math.tau / 5) * radius,
                cy + math.sin(-math.pi / 2 + i * math.tau / 5) * radius,
            )
            for i in range(5)
        ]
        center = QPointF(cx, cy)
        font = p.font()
        font.setPointSizeF(8)
        p.setFont(font)
        for mark in (20, 40, 60, 80, 100):
            p.setPen(QPen(QColor(t["border"]), 1))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPolygon(QPolygonF([center + (v - center) * mark / 100 for v in vertices]))
            p.setPen(QColor(t["muted"]))
            # Internal normalization marks are intentionally unlabelled.
        for v in vertices:
            p.setPen(QPen(QColor(t["border"]), 1))
            p.drawLine(center, v)
        self.hit_targets = []
        if len(self.axes) != 5:
            p.setPen(QColor(t["text"]))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "評価対象なし")
            return
        points = [
            None if a["score"] is None else center + (v - center) * a["score"] / 100
            for v, a in zip(vertices, self.axes)
        ]
        accent = QColor(t["accent"])
        if all(q is not None for q in points):
            fill = QColor(accent)
            fill.setAlpha(60)
            p.setBrush(fill)
            p.setPen(QPen(accent, 2))
            p.drawPolygon(QPolygonF(points))
        else:
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(accent, 2))
            for a, b in zip(points, points[1:] + points[:1]):
                if a is not None and b is not None:
                    p.drawLine(a, b)
        font.setPointSizeF(9)
        font.setBold(True)
        p.setFont(font)
        for i, (v, a, point) in enumerate(zip(vertices, self.axes, points)):
            label_center = center + (v - center) * (1.27 if i else 1.22)
            rect = QRectF(
                max(1, min(self.width() - 110, label_center.x() - 55)),
                max(1, min(self.height() - 53, label_center.y() - 22)),
                110,
                50,
            )
            p.setPen(QColor(t["text"]))
            text = a["label"] + "\n" + a["actual"] + ("\n未評価" if point is None else "")
            p.drawText(rect, Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap, text)
            self.hit_targets.append((rect, a))
            marker = point if point is not None else v
            p.setPen(QPen(accent if point is not None else QColor(t["muted"]), 2))
            p.setBrush(accent if point is not None else QColor(t["card"]))
            p.drawEllipse(marker, 4, 4)
            self.hit_targets.append((QRectF(marker.x() - 10, marker.y() - 10, 20, 20), a))
        p.setPen(QColor(t["muted"]))
        font.setPointSizeF(9)
        p.setFont(font)
        available = sum(a["score"] is not None for a in self.axes)
        p.drawText(
            QRectF(0, self.height() - 23, self.width(), 22),
            Qt.AlignmentFlag.AlignCenter,
            f"評価可能：{available}/5項目 ／ 総合成績ではありません",
        )
