"""Cached, measured card text shared by canvas and vector printer rendering."""

from collections import OrderedDict
from dataclasses import dataclass

from PySide6.QtCore import QPointF
from PySide6.QtGui import QColor, QFont, QImage, QPen

from .core import parse_date
from .fonts import print_font
from .printing import text_block
from .floor_status import days_text, money, ratio_text

FIELDS = ("number", "product", "sales", "average", "ratio", "installed_on", "days")
FIELD_LABELS = {
    "number": "マシン番号",
    "product": "商品名",
    "sales": "売上",
    "average": "アベレージ",
    "ratio": "アベ比",
    "installed_on": "設置日",
    "days": "投入日数",
}
SUMMARY_FIELDS = ("number", "ratio", "days", "product")
DISPLAY_FIELDS = {
    "compact": SUMMARY_FIELDS,
    "product": ("number", "product"),
    "analysis": ("number", "product", "sales", "average", "ratio"),
    "all": FIELDS,
}


@dataclass
class CardLayout:
    blocks: list
    height: float
    minimum_font: float
    scale: float = 1


class CardRenderer:
    def __init__(self):
        self.device = QImage(1, 1, QImage.Format.Format_ARGB32)
        self.device.setDotsPerMeterX(2835)
        self.device.setDotsPerMeterY(2835)
        self.cache = OrderedDict()
        self.measured = 0
        self.hits = 0

    def content(self, machine, width, base, fields, reference, status, methods=""):
        self.measured += 1
        padding = 5
        gap = max(1, base * 0.12)
        records = []
        if "number" in fields:
            records.append(
                (machine.number or f"未設定 ({machine.source_key or '新規'})", base * 1.35, True)
            )
        if "product" in fields:
            records.append((machine.product or "商品未設定", base * 1.18, True))
        important = []
        if "ratio" in fields:
            important.append("アベ比 " + ratio_text(machine))
        if "days" in fields:
            important.append("投入 " + days_text(machine, reference))
        if important:
            records.append(("　".join(important), base * 1.15, True))
        financial = []
        if "sales" in fields:
            financial.append("売上 " + money(machine.sales))
        if "average" in fields:
            financial.append("アベレージ " + money(machine.average))
        if financial:
            records.append(("　".join(financial), base * 0.85, False))
        if "installed_on" in fields:
            installed = parse_date(machine.installed_on)
            records.append(
                (
                    "設置日 " + (installed.strftime("%Y/%m/%d") if installed else "—"),
                    base * 0.85,
                    False,
                )
            )
        if methods:
            records.append(("運営方法 " + methods, base * 0.85, False))
        records.append((status, base * 0.8, True))
        y = padding
        blocks = []
        minimum = 1000
        for text, size, bold in records:
            size = max(1, round(size))
            font = print_font(size, bold)
            if bold:
                font.setWeight(QFont.Weight.Bold)
            block = text_block(text, max(1, width - 2 * padding), font, self.device)
            blocks.append((QPointF(padding, y), block, size))
            y += block.height + gap
            minimum = min(minimum, size)
        return CardLayout(blocks, y - gap + padding, minimum)

    def summary_content(self, machine, width, base, fields, reference, status, truncate=False):
        self.measured += 1
        records = []
        if "number" in fields:
            number = machine.number or f"未設定 ({machine.source_key or '新規'})"
            if "投入予定" in status:
                number += " / 投入予定"
            if "優先入替" in status:
                number += " / 優先"
            records.append((number, base * 1.05))
        if "ratio" in fields:
            records.append(("アベ比 " + ratio_text(machine), base * 1.8))
        if "days" in fields:
            records.append(("投入 " + days_text(machine, reference), base * 1.4))
        if "product" in fields:
            records.append((machine.product or "商品未設定", base))
        y = 6
        blocks = []
        gap = max(2, base * 0.13)
        minimum = 1000
        for value, size in records:
            size = max(1, round(size))
            font = print_font(size)
            if value == (machine.product or "商品未設定") and truncate:
                value = self.elide_name(value, width - 12, font)
            block = text_block(value, width - 12, font, self.device)
            blocks.append((QPointF(6, y), block, size))
            y += block.height + gap
            minimum = min(minimum, size)
        return CardLayout(blocks, y - gap + 6, minimum)

    def elide_name(self, text, width, font):
        block = text_block(text, width, font, self.device)
        if block.layout.lineCount() <= 3:
            return text
        # Measure candidate prefixes as wrapped text; Python slicing preserves
        # astral characters, unlike directly applying QTextLine UTF-16 offsets.
        low, high, best = 0, len(text), "…"
        while low <= high:
            middle = (low + high) // 2
            candidate = text[:middle].rstrip() + "…"
            measured = text_block(candidate, width, font, self.device)
            if measured.layout.lineCount() <= 3:
                best = candidate
                low = middle + 1
            else:
                high = middle - 1
        return best

    def plan(
        self,
        machine,
        width,
        height,
        font,
        fields,
        reference,
        status,
        methods="",
        grow=False,
        summary=False,
        truncate=False,
    ):
        key = (
            machine.number,
            machine.source_key,
            machine.product,
            machine.sales,
            machine.average,
            machine.ratio,
            machine.ratio_from_excel,
            machine.installed_on,
            width,
            height,
            font,
            tuple(fields),
            reference,
            status,
            methods,
            grow,
            summary,
            truncate,
        )
        if key in self.cache:
            self.hits += 1
            self.cache.move_to_end(key)
            return self.cache[key]

        def content(size):
            return (
                self.summary_content(machine, width, size, fields, reference, status, truncate)
                if summary
                else self.content(machine, width, size, fields, reference, status, methods)
            )

        target = content(font)
        if not grow and target.height > height:
            low, high, best = 2, max(2, int(font)), None
            while low <= high:
                middle = (low + high) // 2
                candidate = content(middle)
                if candidate.height <= height:
                    best = candidate
                    low = middle + 1
                else:
                    high = middle - 1
            target = best or content(2)
            if target.height > height:
                target.scale = height / target.height
        self.cache[key] = target
        if len(self.cache) > 3000:
            self.cache.popitem(last=False)
        return target


def draw_card(painter, rect, layout, background, priority=False, selected=False, overlap=False):
    painter.save()
    painter.setRenderHint(painter.RenderHint.Antialiasing)
    border = (
        "#173EBC" if selected else "#9B6900" if priority else "#B42318" if overlap else "#60736A"
    )
    painter.setPen(QPen(QColor(border), 4 if selected else 3 if priority else 2 if overlap else 1))
    painter.setBrush(QColor(background))
    painter.drawRoundedRect(rect, 5, 5)
    painter.translate(rect.topLeft())
    painter.scale(layout.scale, layout.scale)
    painter.setPen(QColor("#10231B"))
    for point, block, _ in layout.blocks:
        block.draw(painter, point)
    painter.restore()
