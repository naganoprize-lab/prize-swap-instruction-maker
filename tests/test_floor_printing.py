from copy import deepcopy
from dataclasses import asdict
from datetime import date

import pytest
from PySide6.QtCore import QPointF, QSize, QDate
from PySide6.QtGui import QColor, QPageLayout
from PySide6.QtPdf import QPdfDocument
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox
from pypdf import PdfReader

from prize_swap.core import Product
from prize_swap.floor import FloorWidget
from prize_swap.floor_data import Machine
from prize_swap.floor_print import (
    FloorPrintOptions,
    PAPERS,
    floor_printer,
    plan_floor,
    save_floor_pdf,
)
from prize_swap.floor_print_ui import FloorPreviewDialog
from prize_swap.floor_render import FIELDS
from prize_swap.floor_status import COLORS, machine_status
from prize_swap.pdf_validation import normalized_pdf_text, require_searchable_pdf

DAY = date(2026, 11, 6)


def sample(count=7):
    values = [49.9, 50, 99.9, 100, 199.9, 200, None]
    return [
        Machine(
            number=str(20000 + i),
            product=f"FLOOR{i:03} 鬼滅の刃 フィギュア " * 2,
            x=i % 4 * 300,
            y=i // 4 * 400,
            sales=1000 + i,
            average=2000,
            ratio=values[i % 7],
            ratio_from_excel=True,
            installed_on="2026-11-01",
        )
        for i in range(count)
    ]


def text(path):
    report = require_searchable_pdf(path)
    return normalized_pdf_text("".join(p["text"] for p in report["pages"]))


@pytest.mark.parametrize("paper", PAPERS)
@pytest.mark.parametrize("orientation", ["portrait", "landscape"])
def test_paper_dimensions_and_japanese_text(tmp_path, paper, orientation):
    options = FloorPrintOptions(paper=paper, orientation=orientation)
    path = tmp_path / f"売り場{paper}-{orientation}.pdf"
    plan = save_floor_pdf(path, sample(1), {}, options, DAY)
    w, h = PAPERS[paper]
    if orientation == "landscape":
        w, h = h, w
    page = PdfReader(path).pages[0]
    assert float(page.mediabox.width) == pytest.approx(w * 72 / 25.4, abs=1)
    assert float(page.mediabox.height) == pytest.approx(h * 72 / 25.4, abs=1)
    assert "アベ比49.9%" in text(path) and "設置日数" not in text(path)
    assert text(path).count("FLOOR000") == 2
    assert len(plan.pages) == 1


def test_custom_paper_and_empty_missing_values(tmp_path):
    options = FloorPrintOptions(
        paper="custom",
        width_mm=250,
        height_mm=350,
        orientation="portrait",
        fields=list(FIELDS),
        card_style="detailed",
    )
    path = tmp_path / "独自用紙.pdf"
    save_floor_pdf(path, [Machine(number="1", product="欠測値の商品")], {}, options, DAY)
    page = PdfReader(path).pages[0]
    assert float(page.mediabox.width) == pytest.approx(250 * 72 / 25.4, abs=1)
    assert "アベ比—" in text(path) and "設置日—" in text(path)


def test_pdf_boundary_colours_are_visible_and_text_is_searchable(tmp_path):
    machines = sample()
    options = FloorPrintOptions(card_size="auto")
    path = tmp_path / "境界色.pdf"
    plan = save_floor_pdf(path, machines, {}, options, DAY)
    doc = QPdfDocument()
    assert doc.load(str(path)) == QPdfDocument.Error.None_
    size = doc.pagePointSize(0)
    image = doc.render(0, size.toSize() * 2)
    sx = image.width() / size.width()
    sy = image.height() / size.height()
    layout = floor_printer(options, path).pageLayout()
    margins = layout.margins(QPageLayout.Unit.Point)
    for card, key in zip(
        plan.pages[0].cards, ("poor", "low", "low", "active", "active", "high", "unknown")
    ):
        point = QPointF(card.rect.right() - 8, card.rect.bottom() - 8) - plan.pages[0].origin
        x = (margins.left() + plan.body.left() + point.x() * plan.scale) * sx
        y = (margins.top() + plan.body.top() + point.y() * plan.scale) * sy
        actual = image.pixelColor(round(x), round(y))
        expected = QColor(COLORS[key])
        assert max(abs(a - b) for a, b in zip(actual.getRgb()[:3], expected.getRgb()[:3])) < 4
    content = text(path)
    for value in (49.9, 50, 99.9, 100, 199.9, 200):
        assert f"アベ比{value:.1f}%" in content
    assert "アベ比—" in content and "低稼働(50%未満)" in content
    doc.close()
    options.colour = False
    mono = tmp_path / "白黒.pdf"
    save_floor_pdf(mono, machines, {}, options, DAY)
    assert text(mono) == content
    assert machine_status(machines[0], True)[0] == COLORS["planned"]
    planned = Product(DAY, "次の商品", machines=[machines[0].number], priority=True)
    blue = tmp_path / "投入予定.pdf"
    options.colour = True
    options.color_filter = "planned"  # Blue background is reserved for the planned-location filter.
    plan = save_floor_pdf(blue, machines, {machines[0].number: [planned]}, options, DAY)
    assert plan.pages[0].cards[0].background == COLORS["planned"]
    doc.load(str(blue))
    size = doc.pagePointSize(0)
    image = doc.render(0, size.toSize() * 2)
    card = plan.pages[0].cards[0]
    point = QPointF(card.rect.right() - 8, card.rect.bottom() - 8) - plan.pages[0].origin
    x = (margins.left() + point.x() * plan.scale) * image.width() / size.width()
    y = (margins.top() + plan.body.top() + point.y() * plan.scale) * image.height() / size.height()
    actual = image.pixelColor(round(x), round(y))
    expected = QColor(COLORS["planned"])
    assert max(abs(a - b) for a, b in zip(actual.getRgb()[:3], expected.getRgb()[:3])) < 4
    assert "アベ比49.9%" in text(blue) and "投入予定" in text(blue)
    doc.close()


def test_whole_card_pagination_298_and_cache(tmp_path):
    machines = sample(298)
    options = FloorPrintOptions(mode="tile", paper="A3")
    original = deepcopy([asdict(m) for m in machines])
    path = tmp_path / "298台の配置.pdf"
    plan = save_floor_pdf(path, machines, {}, options, DAY)
    assert 1 < len(plan.pages) < 100
    assert len(plan.frames) == len(plan.pages)
    ids = []
    for page, frame in zip(plan.pages, plan.frames):
        for c in page.cards:
            ids.append(c.machine.uid)
            assert frame.contains(c.rect)
            assert c.text.height * c.text.scale <= c.rect.height() + 0.01
    assert len(ids) == len(set(ids)) == 298
    content = text(path)
    for i in range(298):
        assert content.count(f"FLOOR{i:03}") == 2
    assert [asdict(m) for m in machines] == original
    assert "ページ1/" in content


def test_screen_view_independent_from_pdf_and_direct_print(tmp_path, monkeypatch):
    import prize_swap.floor as module

    w = FloorWidget(lambda: [], lambda: DAY, tmp_path / "auto.json")
    try:
        w.machines = sample()
        w.rebuild()
        w.resize(1400, 850)
        w.show()
        w.reference_date.setDate(QDate(2026, 11, 6))
        options = FloorPrintOptions(mode="tile", paper="A3", font_size="large")
        w.set_print_options(options)
        reference = tmp_path / "pdf.pdf"
        a = w.paint_floor(floor_printer(options, reference))
        positions = [(m.x, m.y) for m in w.machines]
        w.set_zoom(300)
        w.view.horizontalScrollBar().setValue(300)
        w.display_mode.setCurrentIndex(w.display_mode.findData("product"))
        w.apply_card_settings([m.uid for m in w.machines], width=400, height=500, font=30)

        class Accepted:
            def __init__(self, *args):
                pass

            def setMinMax(self, *args):
                pass

            def setOption(self, *args):
                pass

            def exec(self):
                return QDialog.DialogCode.Accepted

        direct = tmp_path / "直接印刷.pdf"
        monkeypatch.setattr(module, "QPrintDialog", Accepted)
        monkeypatch.setattr(module, "floor_printer", lambda opts: floor_printer(opts, direct))
        monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Yes)
        errors = []
        monkeypatch.setattr(QMessageBox, "warning", lambda *args: errors.append(args))
        w.print_floor()
        assert not errors and text(reference) == text(direct)
        assert [(m.x, m.y) for m in w.machines] == positions
        doc1 = QPdfDocument()
        doc2 = QPdfDocument()
        doc1.load(str(reference))
        doc2.load(str(direct))
        assert doc1.pageCount() == doc2.pageCount() == len(a.pages)
        for i in range(doc1.pageCount()):
            assert doc1.render(i, QSize(1000, 700)) == doc2.render(i, QSize(1000, 700))
        doc1.close()
        doc2.close()
    finally:
        w.timer.stop()
        w.day_timer.stop()
        w.close()


def test_live_preview_reuses_pdf_layout_and_settings(tmp_path):
    w = FloorWidget(lambda: [], lambda: DAY, tmp_path / "auto.json")
    dialog = None
    try:
        w.machines = sample(20)
        w.rebuild()
        dialog = FloorPreviewDialog(w)
        dialog.show()
        QApplication.processEvents()
        dialog.preview.updatePreview()
        QApplication.processEvents()
        assert dialog.preview.pageCount() == 1
        combos = dialog.controls.combos
        combos["mode"].setCurrentIndex(combos["mode"].findData("tile"))
        combos["paper"].setCurrentIndex(combos["paper"].findData("A3"))
        QApplication.processEvents()
        path = tmp_path / "プレビュー.pdf"
        plan = w.paint_floor(floor_printer(w.print_options, path))
        assert dialog.preview.pageCount() == len(plan.pages) > 1
        assert "A3" in dialog.info.text() and dialog.pdf.isEnabled()
        assert w.print_options.paper == "A3" and len(w.page_frames) == len(plan.pages)
    finally:
        if dialog:
            dialog.close()
        w.timer.stop()
        w.day_timer.stop()
        w.close()


def test_selection_fit_warning_and_larger_font_preserve_positions():
    machines = sample(40)
    before = [asdict(m) for m in machines]
    fit = plan_floor(machines, {}, FloorPrintOptions(), DAY)
    assert any("複数ページ" in message for message in fit.warnings)
    selection = plan_floor(
        machines, {}, FloorPrintOptions(mode="selection"), DAY, selected=[machines[0].uid]
    )
    assert len(selection.pages[0].cards) == 1
    big = plan_floor(
        machines,
        {},
        FloorPrintOptions(font_size="xlarge", card_size="enlarge", card_width=250),
        DAY,
    )
    assert any("重な" in message for message in big.warnings)
    assert [asdict(m) for m in machines] == before


def test_long_japanese_name_measurement_has_no_clipped_or_missing_letters(tmp_path):
    name = "LONGNAME クロミ ブランケット ぬいぐるみ ABC123 " * 18
    machine = Machine(number="20111右", product=name, ratio=49.9)
    options = FloorPrintOptions(card_size="standard", card_width=350, card_height=400)
    path = tmp_path / "長い商品名.pdf"
    plan = save_floor_pdf(path, [machine], {}, options, DAY)
    card = plan.pages[0].cards[0]
    assert card.text.height * card.text.scale <= card.rect.height()
    for point, block, _ in card.text.blocks:
        assert point.y() + block.height <= card.rect.height() + 0.01
        for i in range(block.layout.lineCount()):
            line = block.layout.lineAt(i)
            assert line.naturalTextWidth() <= block.width + 0.1
    assert normalized_pdf_text(name) in text(path)
    assert text(path).count("LONGNAME") == 18


def test_native_unsupported_paper_is_stopped_before_render(tmp_path, monkeypatch):
    import prize_swap.floor as module

    w = FloorWidget(lambda: [], lambda: DAY, tmp_path / "auto.json")
    try:
        w.machines = sample(1)
        w.rebuild()

        class Accepted:
            def __init__(self, *args):
                pass

            def setMinMax(self, *args):
                pass

            def setOption(self, *args):
                pass

            def exec(self):
                return QDialog.DialogCode.Accepted

        monkeypatch.setattr(module, "QPrintDialog", Accepted)
        monkeypatch.setattr(
            module, "floor_printer", lambda opts: floor_printer(opts, tmp_path / "unused.pdf")
        )
        monkeypatch.setattr(module, "paper_supported", lambda *args: False)
        messages = []
        monkeypatch.setattr(QMessageBox, "warning", lambda *args: messages.append(args[1]))
        w.print_floor()
        assert messages == ["用紙サイズ非対応"]
        assert not (tmp_path / "unused.pdf").exists()
    finally:
        w.timer.stop()
        w.day_timer.stop()
        w.close()
