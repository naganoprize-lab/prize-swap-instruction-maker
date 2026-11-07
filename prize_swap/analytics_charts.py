"""Qt-only charts with actual hit shapes and immutable source UID membership."""

from collections import defaultdict
import math
from PySide6.QtCore import Qt, QRectF, QPointF, Signal, QEvent
from PySide6.QtGui import QPainter, QPainterPath, QPen, QColor, QPolygonF, QPixmap, QIcon
from PySide6.QtWidgets import QWidget, QToolTip
from .theme import tokens, manager, contrast_text
from .analytics_data import formatted, NUMERIC

CHARTS = {
    "horizontal": "横棒グラフ",
    "vertical": "縦棒グラフ",
    "pie": "円グラフ",
    "donut": "ドーナツグラフ",
    "line": "折れ線グラフ",
    "area": "面グラフ",
    "stacked": "積み上げ棒グラフ",
    "scatter": "散布図",
    "histogram": "ヒストグラム",
    "radar": "レーダーチャート",
    "cross": "クロス集計表",
}


def histogram(records, metric, bins=None):
    valid = [r for r in records if r.get(metric) is not None]
    if not valid:
        return []
    low = min(r[metric] for r in valid)
    high = max(r[metric] for r in valid)
    count = 1 if low == high else (bins or min(20, max(5, math.ceil(math.log2(len(valid)) + 1))))
    width = (high - low) / count if high != low else 1
    output = [
        dict(
            label=f"{formatted(metric,low+i*width)} 〜 {formatted(metric,low+(i+1)*width)}"
            + (" 以下" if i == count - 1 else " 未満"),
            value=0,
            uids=[],
            count=0,
            series="",
            low=low + i * width,
            high=low + (i + 1) * width,
        )
        for i in range(count)
    ]
    for r in valid:
        i = min(count - 1, int((r[metric] - low) / width))
        output[i]["value"] += 1
        output[i]["count"] += 1
        output[i]["uids"].append(r["uid"])
    return output


def scatter(records, x, y):
    return [
        dict(
            label=r["number"],
            series=r["machine_type"],
            x=r[x],
            y=r[y],
            x_metric=x,
            value=r[y],
            uids=[r["uid"]],
            actual=f"{NUMERIC[x][0]}：{formatted(x,r[x])} ／ {NUMERIC[y][0]}：{formatted(y,r[y])}",
        )
        for r in records
        if r.get(x) is not None and r.get(y) is not None
    ]


def chart_constraints(kind, records, results, metric, operation, timeline):
    if kind in ("pie", "donut"):
        if operation not in ("count", "share"):
            return "円・ドーナツは構成件数または件数割合専用です。比率の平均や合計は使いません。"
        if operation == "share" and len({r["period"] for r in results}) > 1:
            return "複数期間の割合を1つの円に合算できません。期間を指定するか件数に切り替えてください。"
    if kind in ("line", "area"):
        if metric not in NUMERIC or metric in ("ratio", "days"):
            return "履歴に保存された実測項目を選択してください。"
        if not timeline:
            return "比較可能な複数期間の履歴が不足しています（期間・商品・運営方法の一致が必要）。"
    if kind == "radar" and len(records) != 1:
        return "レーダーはマシンを1台指定してください。"
    if kind in ("scatter", "histogram") and metric not in NUMERIC:
        return "数値項目を選択してください。"
    return ""


def series_color(name):
    import hashlib, colorsys

    hue = int(hashlib.sha256(str(name).encode()).hexdigest()[:6], 16) / 0xFFFFFF
    rgb = colorsys.hsv_to_rgb(hue, 0.55, 0.85 if manager().dark else 0.7)
    return QColor.fromRgbF(*rgb)


def chart_icon(kind):
    image = QPixmap(32, 32)
    image.fill(Qt.GlobalColor.transparent)
    p = QPainter(image)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(QPen(QColor(tokens()["accent"]), 2))
    p.setBrush(Qt.BrushStyle.NoBrush)
    if kind in ("pie", "donut"):
        p.drawEllipse(QRectF(4, 4, 24, 24))
        p.drawLine(16, 16, 16, 4)
        p.drawLine(16, 16, 28, 16)
        if kind == "donut":
            p.setBrush(QColor(tokens()["card"]))
            p.drawEllipse(QRectF(12, 12, 8, 8))
    elif kind == "radar":
        p.drawPolygon(
            QPolygonF(
                [
                    QPointF(
                        16 + 12 * math.cos(-math.pi / 2 + i * math.tau / 5),
                        16 + 12 * math.sin(-math.pi / 2 + i * math.tau / 5),
                    )
                    for i in range(5)
                ]
            )
        )
    elif kind == "cross":
        p.drawRect(4, 4, 24, 24)
        for i in (12, 20):
            p.drawLine(i, 4, i, 28)
            p.drawLine(4, i, 28, i)
    elif kind == "scatter":
        for x, y in ((7, 24), (11, 16), (19, 20), (22, 8), (27, 13)):
            p.drawEllipse(QPointF(x, y), 2, 2)
    elif kind in ("line", "area"):
        polygon = QPolygonF([QPointF(4, 25), QPointF(11, 15), QPointF(18, 20), QPointF(28, 5)])
        p.drawPolyline(polygon)
        if kind == "area":
            p.setBrush(QColor(tokens()["selection"]))
            p.drawPolygon(QPolygonF(list(polygon) + [QPointF(28, 28), QPointF(4, 28)]))
    else:
        for i, length in enumerate((20, 13, 25)):
            if kind == "horizontal":
                p.drawRect(3, 4 + i * 9, length, 5)
            else:
                p.drawRect(4 + i * 9, 28 - length, 5, length)
                if kind == "stacked":
                    p.drawLine(
                        QPointF(4 + i * 9, 28 - length / 2), QPointF(9 + i * 9, 28 - length / 2)
                    )
    p.end()
    return QIcon(image)


class ChartCanvas(QWidget):
    activated = Signal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.kind = "horizontal"
        self.data = []
        self.hits = []
        self.legend = True
        self.title = ""
        self.x_label = ""
        self.y_label = ""
        self.value_key = "average"
        self.colors = {}
        self.setMinimumSize(420, 220)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName("売上分析グラフ")
        manager().changed.connect(self.theme_changed)

    def theme_changed(self, *_):
        self.update()

    def set_data(
        self,
        kind,
        data,
        title="",
        metric="average",
        x_label="",
        y_label="",
        legend=True,
        colors=None,
    ):
        self.kind = kind
        self.data = data
        self.title = title
        self.value_key = metric
        self.x_label = x_label
        self.y_label = y_label
        self.legend = legend
        self.colors = colors or {}
        self.setMinimumHeight(max(260, len(data) * 36 + 70) if kind == "horizontal" else 320)
        self.setMinimumWidth(
            max(420, len(data) * 52 + 130) if kind in ("vertical", "stacked", "histogram") else 420
        )
        self.setAccessibleDescription(title + "\n" + "\n".join(self.tooltip(d) for d in data))
        self.update()

    def tooltip(self, d):
        val = formatted(self.value_key, d.get("value"))
        return f'{d.get("label","")} {d.get("series","")}\n{d.get("actual",val)}\n{d.get("period_text","")}\n対象 {len(d.get("uids",[]))}台\n{d.get("mode","")}\n{d.get("source_kind","")} {d.get("source_file","")} {d.get("loaded_at","")}'

    def tooltip_at(self, point):
        for shape, d in reversed(self.hits):
            if shape.contains(point):
                return self.tooltip(d)
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
            for shape, d in reversed(self.hits):
                if shape.contains(event.position()):
                    if d.get("uids"):
                        self.activated.emit(list(dict.fromkeys(d["uids"])))
                    return
        super().mousePressEvent(event)

    def color(self, name):
        return QColor(self.colors[name]) if name in self.colors else series_color(name)

    def add_rect(self, rect, d):
        path = QPainterPath()
        path.addRect(rect)
        self.hits.append((path, d))

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        t = tokens()
        p.fillRect(self.rect(), QColor(t["card"]))
        font = p.font()
        font.setPointSizeF(9)
        p.setFont(font)
        p.setPen(QColor(t["text"]))
        p.drawText(
            QRectF(10, 3, self.width() - 20, 32),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            self.title,
        )
        self.hits = []
        data = [d for d in self.data if d.get("value") is not None]
        if not data:
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "評価可能なデータがありません。")
            return
        plot = QRectF(
            175 if self.kind == "horizontal" else 78,
            45,
            self.width() - (210 if self.kind == "horizontal" else 110),
            self.height() - 105,
        )
        if self.kind in ("pie", "donut"):
            self.paint_pie(p, plot, data)
            return
        values = [d.get("y", d["value"]) for d in data]
        ymin = min(0, min(values))
        ymax = max(0, max(values))
        span = ymax - ymin or 1
        if self.kind == "stacked":
            groups = defaultdict(list)
            for d in data:
                groups[(d.get("period"), d["label"])].append(d)
            ymin = min(0, min(sum(min(0, d["value"]) for d in group) for group in groups.values()))
            ymax = max(0, max(sum(max(0, d["value"]) for d in group) for group in groups.values()))
            span = ymax - ymin or 1

        def yy(value):
            return plot.bottom() - (value - ymin) / span * plot.height()

        def xx(value):
            return plot.left() + (value - ymin) / span * plot.width()

        p.setPen(QPen(QColor(t["border"]), 1))
        for i in range(5):
            value = ymin + span * i / 4
            if self.kind == "horizontal":
                x = xx(value)
                p.drawLine(QPointF(x, plot.top()), QPointF(x, plot.bottom()))
                p.setPen(QColor(t["muted"]))
                p.drawText(
                    QRectF(x - 36, plot.bottom() + 5, 72, 20),
                    Qt.AlignmentFlag.AlignCenter,
                    formatted(self.value_key, value),
                )
                p.setPen(QColor(t["border"]))
            else:
                y = yy(value)
                p.drawLine(QPointF(plot.left(), y), QPointF(plot.right(), y))
                p.setPen(QColor(t["muted"]))
                p.drawText(
                    QRectF(1, y - 10, 72, 20),
                    Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                    formatted(self.value_key, value),
                )
                p.setPen(QColor(t["border"]))
        if self.kind == "horizontal":
            step = plot.height() / len(data)
            for i, d in enumerate(data):
                y = plot.top() + i * step
                bar = QRectF(
                    min(xx(0), xx(d["value"])),
                    y + 4,
                    max(1, abs(xx(d["value"]) - xx(0))),
                    max(3, step - 10),
                )
                p.fillRect(bar, self.color(d["label"]))
                p.setPen(QColor(t["text"]))
                p.drawText(
                    QRectF(4, y, 165, step - 2),
                    Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                    d["label"],
                )
                self.add_rect(QRectF(plot.left(), y, plot.width(), step), d)
        elif self.kind in ("vertical", "histogram"):
            step = plot.width() / len(data)
            for i, d in enumerate(data):
                x = plot.left() + i * step
                bar = QRectF(
                    x + step * 0.15,
                    min(yy(0), yy(d["value"])),
                    step * 0.7,
                    max(1, abs(yy(0) - yy(d["value"]))),
                )
                p.fillRect(bar, self.color(d["label"]))
                self.add_rect(QRectF(x, plot.top(), step, plot.height()), d)
                p.save()
                p.translate(x + step * 0.7, plot.bottom() + 8)
                p.rotate(24)
                p.setPen(QColor(t["text"]))
                p.drawText(QRectF(0, 0, 120, 30), Qt.TextFlag.TextSingleLine, d["label"])
                p.restore()
        elif self.kind == "stacked":
            step = plot.width() / len(groups)
            for i, ((period, label), group) in enumerate(groups.items()):
                positive = negative = 0
                for d in group:
                    base = positive if d["value"] >= 0 else negative
                    end = base + d["value"]
                    if d["value"] >= 0:
                        positive = end
                    else:
                        negative = end
                    rect = QRectF(
                        plot.left() + i * step + step * 0.15,
                        min(yy(base), yy(end)),
                        step * 0.7,
                        max(1, abs(yy(base) - yy(end))),
                    )
                    p.fillRect(rect, self.color(d["series"]))
                    self.add_rect(rect, d)
                p.setPen(QColor(t["text"]))
                p.drawText(
                    QRectF(plot.left() + i * step, plot.bottom() + 5, step, 40),
                    Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap,
                    label,
                )
        elif self.kind == "scatter":
            xs = [d["x"] for d in data]
            xlo = min(xs)
            xhi = max(xs)
            xspan = xhi - xlo or 1
            for d in data:
                point = QPointF(plot.left() + (d["x"] - xlo) / xspan * plot.width(), yy(d["y"]))
                p.setBrush(self.color(d["series"]))
                p.setPen(QPen(QColor(t["card"]), 1))
                p.drawEllipse(point, 5, 5)
                shape = QPainterPath()
                shape.addEllipse(point, 9, 9)
                self.hits.append((shape, d))
            p.setPen(QColor(t["text"]))
            p.drawText(
                QRectF(plot.left(), plot.bottom() + 4, plot.width(), 22),
                Qt.AlignmentFlag.AlignCenter,
                f'{self.x_label}：{formatted(data[0]["x_metric"],xlo)} 〜 {formatted(data[0]["x_metric"],xhi)}',
            )
        elif self.kind in ("line", "area"):
            labels = sorted({d["label"] for d in data})
            series = defaultdict(list)
            for d in data:
                series[d["series"]].append(d)
            for name, items in series.items():
                items = sorted(items, key=lambda d: d["label"])
                points = [
                    QPointF(
                        plot.left()
                        + labels.index(d["label"]) / max(1, len(labels) - 1) * plot.width(),
                        yy(d["value"]),
                    )
                    for d in items
                ]
                colour = self.color(name)
                if self.kind == "area" and len(points) > 1:
                    fill = QColor(colour)
                    fill.setAlpha(50)
                    p.setBrush(fill)
                    p.setPen(Qt.PenStyle.NoPen)
                    p.drawPolygon(
                        QPolygonF(
                            [QPointF(points[0].x(), yy(0))]
                            + points
                            + [QPointF(points[-1].x(), yy(0))]
                        )
                    )
                p.setBrush(colour)
                p.setPen(QPen(colour, 2))
                p.drawPolyline(QPolygonF(points))
                for point, d in zip(points, items):
                    p.drawEllipse(point, 4, 4)
                    shape = QPainterPath()
                    shape.addEllipse(point, 9, 9)
                    self.hits.append((shape, d))
            for i, label in enumerate(labels):
                x = plot.left() + i / max(1, len(labels) - 1) * plot.width()
                p.setPen(QColor(t["text"]))
                p.drawText(
                    QRectF(x - 65, plot.bottom() + 6, 130, 38),
                    Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap,
                    label,
                )
        if self.legend and self.kind in ("line", "area", "stacked", "scatter"):
            self.paint_legend(p, sorted({d["series"] for d in data}))
        p.setPen(QColor(t["muted"]))
        p.drawText(
            QRectF(0, self.height() - 22, self.width(), 20),
            Qt.AlignmentFlag.AlignCenter,
            self.y_label or "クリックで該当マシンを売り場上で強調／数値・期間はホバーと下表で確認",
        )

    def paint_legend(self, p, names):
        p.setPen(QColor(tokens()["muted"]))
        if len(names) > max(1, min(6, self.width() // 145)):
            p.drawText(
                QRectF(10, 29, self.width() - 20, 18),
                Qt.AlignmentFlag.AlignRight,
                f"{len(names)}系列（各系列名はホバー・下表で確認）",
            )
            return
        x = 12
        for name in names:
            p.fillRect(QRectF(x, 31, 9, 9), self.color(name))
            p.setPen(QColor(tokens()["text"]))
            p.drawText(QRectF(x + 12, 27, 130, 18), Qt.AlignmentFlag.AlignLeft, name)
            x += 145

    def paint_pie(self, p, plot, data):
        total = sum(max(0, d["value"]) for d in data)
        if total <= 0:
            p.setPen(QColor(tokens()["text"]))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "正の構成件数がありません。")
            return
        diameter = min(self.width() * 0.65, self.height() - 90)
        rect = QRectF((self.width() - diameter) / 2, 42, diameter, diameter)
        angle = 0
        for d in data:
            extent = max(0, d["value"]) / total * 360
            if extent == 0:
                continue
            path = QPainterPath()
            path.moveTo(rect.center())
            path.arcTo(rect, angle, extent)
            path.closeSubpath()
            if self.kind == "donut":
                hole = QPainterPath()
                hole.addEllipse(
                    rect.adjusted(diameter * 0.3, diameter * 0.3, -diameter * 0.3, -diameter * 0.3)
                )
                path = path.subtracted(hole)
            p.setBrush(self.color(d["label"]))
            p.setPen(QPen(QColor(tokens()["card"]), 2))
            p.drawPath(path)
            self.hits.append((path, d))
            if self.legend and extent >= 20:
                mid = math.radians(angle + extent / 2)
                point = rect.center() + QPointF(
                    math.cos(mid) * diameter * 0.39, -math.sin(mid) * diameter * 0.39
                )
                p.setPen(QColor(contrast_text(self.color(d["label"]))))
                p.drawText(
                    QRectF(point.x() - 60, point.y() - 20, 120, 40),
                    Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap,
                    f"{extent/3.6:.1f}%",
                )
            angle += extent
        if self.legend:
            self.paint_legend(p, list(dict.fromkeys(d["label"] for d in data)))
