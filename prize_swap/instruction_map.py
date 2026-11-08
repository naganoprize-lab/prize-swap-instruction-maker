"""Read-only, numbered plan of the existing floor coordinates and its print planner."""

from dataclasses import dataclass
from math import ceil, sqrt
from PySide6.QtCore import QRectF, Qt, QMarginsF
from PySide6.QtGui import QPageLayout, QPageSize, QColor, QPen
from .instructions import booths
from .instruction_badges import draw_badge
from .fonts import print_font
from .core import AppError


@dataclass
class MapGroup:
    machine: object
    booths: list

    @property
    def rect(self):
        m = self.machine
        return QRectF(m.x, m.y, m.width, m.height)


@dataclass
class MapPage:
    groups: list
    bounds: QRectF
    orientation: str
    unresolved: list = None


@dataclass
class MapPlan:
    pages: list
    paper: str
    day: object
    entries: list
    min_number_size: float = 11

    @property
    def reason(self):
        return f"入替予定地図：{len(self.entries)}ブース・{len(self.pages)}ページ / 位置未特定{sum(b.machine is None for b in self.entries)}件"


def map_layout(paper="A4", orientation="landscape"):
    if paper not in ("A4", "A3"):
        raise AppError("地図の用紙はA4またはA3を指定してください。")
    return QPageLayout(
        QPageSize(QPageSize.PageSizeId.A4 if paper == "A4" else QPageSize.PageSizeId.A3),
        (
            QPageLayout.Orientation.Portrait
            if orientation == "portrait"
            else QPageLayout.Orientation.Landscape
        ),
        QMarginsF(6, 6, 6, 6),
        QPageLayout.Unit.Millimeter,
    )


def map_bounds(groups):
    rect = QRectF()
    for g in groups:
        rect = rect.united(g.rect)
    pad = max(8, min(rect.width(), rect.height()) * 0.035)
    return rect.adjusted(-pad, -pad, pad, pad)


def grouped_booths(entries):
    groups = {}
    for b in entries:
        if b.machine:
            groups.setdefault(b.machine.uid, MapGroup(b.machine, [])).booths.append(b)
    return list(groups.values())


def content_rect(paper, orientation):
    paint = map_layout(paper, orientation).paintRect(QPageLayout.Unit.Point)
    return QRectF(0, 29, paint.width(), max(1, paint.height() - 51))


def choose_orientation(groups, paper, direction):
    if direction != "auto":
        return direction
    bounds = map_bounds(groups)
    return max(
        ("portrait", "landscape"),
        key=lambda d: min(
            content_rect(paper, d).width() / max(1, bounds.width()),
            content_rect(paper, d).height() / max(1, bounds.height()),
        ),
    )


def marker_size(group, scale):
    columns = ceil(sqrt(len(group.booths)))
    return (
        min(group.rect.width() / columns, group.rect.height() / ceil(len(group.booths) / columns))
        * 0.38
        * scale
    )


def plan_map(
    products,
    machines,
    references,
    day,
    paper="A4",
    orientation="auto",
    all_entries=False,
    min_number_size=11,
):
    if orientation not in ("auto", "portrait", "landscape") or not 7 <= min_number_size <= 40:
        raise AppError("地図の印刷設定が不正です。")
    entries = booths(products, machines, references, all_entries)
    groups = grouped_booths(entries)
    pages = []
    if orientation == "auto":
        orientation = choose_orientation(groups, paper, orientation) if groups else "portrait"

    def split(part):
        if not part:
            return
        bounds = map_bounds(part)
        direction = choose_orientation(part, paper, orientation)
        area = content_rect(paper, direction)
        scale = min(area.width() / bounds.width(), area.height() / bounds.height())
        if (
            len(part) == 1
            and len(part[0].booths) == 1
            or min(marker_size(g, scale) for g in part) >= min_number_size
        ):
            pages.append(MapPage(part, bounds, direction))
            return
        if len(part) == 1:
            group = part[0]
            pivot = len(group.booths) // 2
            split([MapGroup(group.machine, group.booths[:pivot])])
            split([MapGroup(group.machine, group.booths[pivot:])])
            return
        axis = "x" if bounds.width() / area.width() >= bounds.height() / area.height() else "y"
        part = sorted(part, key=lambda g: getattr(g.rect.center(), axis)())
        # Keep real coordinates and complete cards; divide at the largest meaningful gap.
        lo, hi = max(1, len(part) // 4), min(len(part) - 1, 3 * len(part) // 4)
        pivot = max(
            range(lo, hi + 1),
            key=lambda i: (
                getattr(part[i].rect.center(), axis)() - getattr(part[i - 1].rect.center(), axis)(),
                -abs(i - len(part) / 2),
            ),
        )
        split(part[:pivot])
        split(part[pivot:])

    split(groups)
    missing = [b for b in entries if b.machine is None]
    for i in range(0, len(missing), 25):
        pages.append(
            MapPage(
                [],
                QRectF(),
                orientation if orientation != "auto" else "portrait",
                missing[i : i + 25],
            )
        )
    if not pages:
        pages = [MapPage([], QRectF(), orientation if orientation != "auto" else "portrait", [])]
    return MapPlan(pages, paper, day, entries, min_number_size)


def marker_rects(rect, count):
    cols = ceil(sqrt(count))
    rows = ceil(count / cols)
    width, height = rect.width() / cols, rect.height() / rows
    size = min(width, height) * 0.72
    return [
        QRectF(
            rect.x() + i % cols * width + (width - size) / 2,
            rect.y() + i // cols * height + (height - size) / 2,
            size,
            size,
        )
        for i in range(count)
    ]


def draw_map_card(painter, rect, group, selected=None):
    painter.save()
    painter.setPen(QPen(QColor("#174D7D"), 2 if selected else 1))
    painter.setBrush(QColor("#A9D7FF"))
    painter.drawRect(rect)
    markers = marker_rects(rect, len(group.booths))
    for r, b in zip(markers, group.booths):
        draw_badge(painter, r, b.number, fill="#FFFFFF", ink="#0F2540")
    painter.restore()
    return markers


def draw_map_page(painter, plan, index):
    page = plan.pages[index]
    area = content_rect(plan.paper, page.orientation)
    painter.save()
    painter.fillRect(QRectF(0, 0, area.width(), area.bottom() + 22), QColor("white"))
    painter.setPen(QColor("#111111"))
    painter.setFont(print_font(13))
    painter.drawText(
        QRectF(0, 0, area.width(), 24),
        Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
        f"入替予定地図　{plan.day:%Y/%m/%d}",
    )
    if page.groups:
        scale = min(area.width() / page.bounds.width(), area.height() / page.bounds.height())
        x = area.x() + (area.width() - page.bounds.width() * scale) / 2 - page.bounds.x() * scale
        y = area.y() + (area.height() - page.bounds.height() * scale) / 2 - page.bounds.y() * scale
        for group in page.groups:
            r = group.rect
            draw_map_card(
                painter,
                QRectF(x + r.x() * scale, y + r.y() * scale, r.width() * scale, r.height() * scale),
                group,
            )
    else:
        painter.setFont(print_font(11))
        painter.drawText(0, 45, "位置未特定（架空の配置は作成しません）")
        for i, b in enumerate(page.unresolved or []):
            y = 62 + i * 18
            draw_badge(painter, QRectF(1, y, 14, 14), b.number)
            painter.drawText(
                QRectF(22, y, area.width() - 22, 18),
                Qt.AlignmentFlag.AlignVCenter,
                (b.token or "番号未入力") + "　" + b.status,
            )
    painter.setFont(print_font(9))
    painter.drawText(
        QRectF(0, area.bottom() + 5, area.width(), 16),
        Qt.AlignmentFlag.AlignRight,
        f"{index+1} / {len(plan.pages)}",
    )
    painter.restore()
