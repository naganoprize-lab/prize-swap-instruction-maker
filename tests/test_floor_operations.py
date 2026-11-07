from dataclasses import asdict
from datetime import date, datetime
import hashlib

import pytest
from openpyxl import Workbook
from PySide6.QtCore import QDate, QRectF, Qt
from PySide6.QtGui import QImage, QPainter
from PySide6.QtWidgets import QApplication

from prize_swap.core import Product
from prize_swap.floor import FloorWidget
from prize_swap.floor_data import Machine, import_layout, save_layout
from prize_swap.floor_render import CardRenderer, DISPLAY_FIELDS
from prize_swap.floor_status import (
    COLORS,
    days_text,
    machine_status,
    money,
    percent,
    ratio,
    ratio_text,
)

DAY = date(2026, 11, 6)


@pytest.fixture
def floor(tmp_path):
    products = [Product(DAY, "次の商品", machines=["20111右", "20112左"], priority=True)]
    w = FloorWidget(lambda: products, lambda: DAY, tmp_path / "auto.json")
    w.resize(1400, 850)
    w.show()
    QApplication.processEvents()
    yield w
    w.timer.stop()
    w.day_timer.stop()
    w.close()


@pytest.mark.parametrize(
    "value,key",
    [
        (49.9, "poor"),
        (50, "low"),
        (99.9, "low"),
        (100, "active"),
        (199.9, "active"),
        (200, "high"),
        (None, "unknown"),
    ],
)
def test_ratio_boundaries_and_planned_priority(floor, value, key):
    m = Machine(number="20111右", ratio=value, product="景品")
    background, status = machine_status(m)
    assert background == COLORS[key]
    assert ratio_text(m) == ("—" if value is None else f"{value:.1f}%")
    assert machine_status(m, True, True)[0] == COLORS["planned"]
    assert "投入予定" in machine_status(m, True)[1]
    if value is not None and value < 50:
        assert "50%未満" in status
    floor.add_machine(m)
    floor.scene.clearSelection()
    assert floor.card_context(m)[0] == COLORS["planned"]
    floor.products()[0].included = False
    floor.sync()
    assert floor.card_context(m)[0] == background
    image = QImage(180, 120, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.white)
    painter = QPainter(image)
    floor.scene.render(painter, QRectF(0, 0, 180, 120), QRectF(0, 0, 180, 120))
    painter.end()
    assert image.pixelColor(170, 110).name() == background.lower()


def test_missing_and_explicit_ratio_precedence():
    assert ratio(Machine(sales=100, average=50)) == 200
    assert ratio(Machine(sales=100, average=50, ratio=49.9)) == 49.9
    for m in (Machine(), Machine(sales=0), Machine(sales=10, average=0), Machine(average=10)):
        assert ratio(m) is None and ratio_text(m) == "—"
        assert machine_status(m)[0] == "#FFFFFF"
    assert ratio(Machine(sales=0, average=10)) == 0
    assert money(None) == "—" and money(0) == "0"
    assert percent(0.499, "0.0%") == pytest.approx(49.9)
    assert percent("49.9%", "0.0%") == 49.9


def test_actual_excel_block_headers_and_source_unchanged(tmp_path):
    path = tmp_path / "売り場参考値.xlsx"
    wb = Workbook()
    ws = wb.active
    ws.title = "投入中レイアウト"
    for i, (value, fmt) in enumerate(((0.499, "0.0%"), (50, "General"), (None, "General"))):
        col = 1 + i * 4
        for row, cells in enumerate(
            (
                (f"L_{i}", "景品", None),
                ("売上/ｱﾍﾞﾚｰｼﾞ", 100, 50),
                ("PO率/ｱﾍﾞ比", 99, value),
                ("設置日/設定2", datetime(2026, 11, 5), 123),
            ),
            1,
        ):
            for j, v in enumerate(cells):
                ws.cell(row, col + j, v)
        ws.cell(3, col + 2).number_format = fmt
    wb.save(path)
    wb.close()
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    machines, _ = import_layout(path)
    assert [ratio(m) for m in machines] == [pytest.approx(49.9), 50, 200]
    assert all(
        m.sales == 100 and m.average == 50 and m.installed_on == "2026-11-05" for m in machines
    )
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before


@pytest.mark.parametrize(
    "installed,expected",
    [(None, "—"), ("2026-11-06", "0日"), ("2026-11-05", "1日"), ("2026-11-07", "投入予定")],
)
def test_reference_age(installed, expected):
    assert days_text(Machine(installed_on=installed), DAY) == expected


def test_reference_modes_and_json_do_not_change_data_or_positions(floor, tmp_path):
    m = Machine(
        number="20111右",
        product="非常に長い商品名" * 12,
        x=30,
        y=40,
        sales=120,
        average=50,
        installed_on="2026-11-01",
        ratio=240,
    )
    floor.add_machine(m)
    original = asdict(m)
    floor.reference_date.setDate(QDate(2026, 11, 2))
    assert not floor.follow_today and days_text(m, floor.reference_day()) == "1日"
    assert "現在の読込値" in floor.data_note.text()
    for mode, fields in DISPLAY_FIELDS.items():
        floor.display_mode.setCurrentIndex(floor.display_mode.findData(mode))
        assert floor.card_context(m)[3] == fields
        assert asdict(m) == original
    floor.set_zoom(200)
    floor.print_options.paper = "A2"
    saved = floor.state()
    path = tmp_path / "日本語配置.json"
    save_layout(path, saved)
    other = FloorWidget(lambda: [], lambda: DAY, path)
    assert other.state() == saved
    other.timer.stop()
    other.day_timer.stop()
    other.close()
    floor.today()
    assert floor.follow_today and floor.reference_day() == date.today()
    floor._last_today = QDate(2000, 1, 1)
    floor.check_day()
    assert floor.reference_day() == date.today()
    floor.reference_date.setDate(QDate(2026, 1, 1))
    floor._last_today = QDate(2000, 1, 1)
    floor.check_day()
    assert floor.reference_day() == date(2026, 1, 1)
    legacy = {
        "version": 1,
        "machines": [
            {
                k: v
                for k, v in original.items()
                if k
                in ("number", "kind", "product", "x", "y", "width", "height", "source_key", "uid")
            }
        ],
        "original": [],
    }
    floor.restore(legacy)
    assert floor.machines[0].ratio is None and floor.machines[0].sales is None
    assert floor.print_options.paper == "A4" and floor.display_mode.currentData() == "compact"


def test_size_font_settings_keep_coordinates_and_undo(floor):
    a = Machine(number="20111右", x=20, y=30)
    b = Machine(number="20112左", x=220, y=30)
    floor.add_machine(a)
    floor.add_machine(b)
    messages = []
    floor.message.connect(messages.append)
    floor.apply_card_settings([a.uid], font=20)
    assert (floor.machines[0].x, floor.machines[0].y, floor.machines[0].width) == (20, 30, 180)
    floor.apply_card_settings([a.uid], width=300, height=200)
    assert floor.overlaps == {a.uid, b.uid} and any("重な" in s for s in messages)
    assert (floor.machines[0].x, floor.machines[0].y) == (20, 30)
    floor.history.undo()
    assert floor.machines[0].width == 180 and floor.machines[0].font_size == 20
    floor.history.redo()
    assert floor.machines[0].width == 300
    floor.set_zoom(500)
    assert floor.view.transform().m11() == pytest.approx(3)
    floor.set_zoom(1)
    assert floor.view.transform().m11() == pytest.approx(0.5)
    floor.nodes[a.uid].setSelected(True)
    floor.zoom_selected()
    assert 0.5 <= floor.view.transform().m11() <= 3


def test_298_cards_measured_once_then_cached(floor):
    floor.display_mode.setCurrentIndex(floor.display_mode.findData("all"))
    floor.machines = [
        Machine(
            number=str(10000 + i),
            product=f"CARD{i:03} 日本語商品名" * 4,
            x=(i % 20) * 220,
            y=(i // 20) * 200,
            ratio=i,
            installed_on="2026-11-01",
        )
        for i in range(298)
    ]
    floor.rebuild()
    floor.fit()
    renderer = CardRenderer()
    for m in floor.machines:
        context = floor.card_context(m)
        plan = renderer.plan(m, m.width, m.height, context[4], context[3], DAY, context[1])
        assert plan.height * plan.scale <= m.height + 0.01
        assert len(plan.blocks) == 6
    measured = renderer.measured
    for m in floor.machines:
        c = floor.card_context(m)
        renderer.plan(m, m.width, m.height, c[4], c[3], DAY, c[1])
    assert renderer.measured == measured and renderer.hits == 298
    assert len(floor.nodes) == 298
    assert [(m.x, m.y) for m in floor.machines] == [
        ((i % 20) * 220, (i // 20) * 200) for i in range(298)
    ]
