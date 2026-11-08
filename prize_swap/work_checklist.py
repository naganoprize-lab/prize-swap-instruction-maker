"""An independent A4 checklist, using the original booth numbers and full product names."""

from dataclasses import dataclass
from math import ceil
from PySide6.QtCore import QRectF, QPointF, Qt
from PySide6.QtGui import QImage, QPageLayout, QColor, QPen, QFontMetricsF
from .printing import text_block
from .fonts import print_font
from .instruction_map import map_layout
from .instruction_badges import draw_badge
from .instructions import booths
from .methods import active_assignments
from .machine_matching import machine_key, normalize_reference


@dataclass
class ChecklistRow:
    booth: object
    cells: list
    height: float
    continuation: bool = False


@dataclass
class ChecklistPlan:
    pages: list
    entries: list
    day: object
    widths: list
    headers: list
    device: object
    layout: object
    width: float
    height: float
    header_height: float

    @property
    def reason(self):
        return f"作業チェックリスト：{len(self.entries)}ブース・{len(self.pages)}ページ（A4縦）"


def checklist_plan(products, machines, references, day):
    layout = map_layout("A4", "portrait")
    area = layout.paintRect(QPageLayout.Unit.Point)
    width, height = area.width(), area.height()
    widths = [width * r for r in [0.06, 0.14, 0.12, 0.27, 0.12, 0.08, 0.21]]
    device = QImage(1, 1, QImage.Format.Format_ARGB32)
    device.setDotsPerMeterX(2835)
    device.setDotsPerMeterY(2835)
    font = print_font(9)
    headers = [
        text_block(v, w - 6, font, device)
        for v, w in zip(
            ["指示番号", "マシン番号", "管理ナンバー", "商品名", "運営方法", "完了", "備考"], widths
        )
    ]
    header_height = max(b.height for b in headers) + 8
    body_height = height - 32 - header_height - 22
    entries = booths(products, machines, references)
    rows = []
    maxlines = max(1, int((body_height - 8) / QFontMetricsF(font, device).height()))
    for b in entries:
        machine = machine_key(b.machine) if b.machine else b.link["machine_number"] or b.token
        manager = (
            b.link["management_number"]
            or (normalize_reference(b.machine.source_key) if b.machine else "")
            or normalize_reference(b.token)
            or ""
        )
        if manager == machine:
            manager = ""
        values = [
            "",
            machine,
            manager,
            b.product.name or "",
            active_assignments(b.product).get(b.token) or "",
            "",
            b.product.notes or "",
        ]
        blocks = [text_block(v, w - 6, font, device) for v, w in zip(values, widths)]
        chunks = []
        for block in blocks:
            n = block.layout.lineCount()
            encoded = block.text.encode("utf-16-le")
            parts = []
            for i in range(0, n, maxlines):
                first = block.layout.lineAt(i)
                last = block.layout.lineAt(min(n - 1, i + maxlines - 1))
                start = first.textStart()
                stop = last.textStart() + last.textLength()
                parts.append(encoded[start * 2 : stop * 2].decode("utf-16-le"))
            chunks.append(parts or [""])
        for index in range(max(len(c) for c in chunks)):
            cells = [
                text_block(c[index] if index < len(c) else "", w - 6, font, device)
                for c, w in zip(chunks, widths)
            ]
            rows.append(
                ChecklistRow(b, cells, max(30, max(c.height for c in cells) + 8), index > 0)
            )
    pages = []
    current = []
    used = 0
    for row in rows:
        if current and used + row.height > body_height:
            pages.append(current)
            current = []
            used = 0
        current.append(row)
        used += row.height
    if current or not pages:
        pages.append(current)
    return ChecklistPlan(
        pages, entries, day, widths, headers, device, layout, width, height, header_height
    )


def draw_checklist_page(painter, plan, index):
    painter.save()
    painter.setRenderHint(painter.RenderHint.Antialiasing)
    painter.fillRect(QRectF(0, 0, plan.width, plan.height), QColor("white"))
    painter.setPen(QColor("#111111"))
    painter.setFont(print_font(13))
    painter.drawText(
        QRectF(0, 0, plan.width, 26),
        Qt.AlignmentFlag.AlignVCenter,
        f"作業チェックリスト　{plan.day:%Y/%m/%d}",
    )
    x = 0
    y = 32
    painter.setPen(QPen(QColor("black"), 0.8))
    for width, block in zip(plan.widths, plan.headers):
        rect = QRectF(x, y, width, plan.header_height)
        painter.fillRect(rect, QColor("#EDF1F5"))
        painter.drawRect(rect)
        block.draw(painter, QPointF(x + 3, y + 4))
        x += width
    y += plan.header_height
    for row in plan.pages[index]:
        x = 0
        for col, (width, block) in enumerate(zip(plan.widths, row.cells)):
            painter.drawRect(QRectF(x, y, width, row.height))
            if col == 0:
                draw_badge(
                    painter,
                    QRectF(x + (width - 16) / 2, y + (row.height - 16) / 2, 16, 16),
                    row.booth.number,
                )
            elif col == 5:
                if not row.continuation:
                    painter.drawRect(
                        QRectF(x + (width - 15) / 2, y + (row.height - 15) / 2, 15, 15)
                    )
            else:
                block.draw(painter, QPointF(x + 3, y + 4))
            x += width
        y += row.height
    painter.setFont(print_font(9, False))
    painter.drawText(
        QRectF(0, plan.height - 18, plan.width, 16),
        Qt.AlignmentFlag.AlignLeft,
        "完了欄は手書きでチェック。指示書・地図と番号が共通です。",
    )
    painter.drawText(
        QRectF(0, plan.height - 18, plan.width, 16),
        Qt.AlignmentFlag.AlignRight,
        f"{index+1} / {len(plan.pages)}",
    )
    painter.restore()
