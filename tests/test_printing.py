from datetime import date
import re
import unicodedata

import pytest
from PySide6.QtCore import QSize
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtPdf import QPdfDocument
from PySide6.QtPrintSupport import QPrinter

from prize_swap.core import AppError, Product, ordered
from prize_swap.exporter import positions
from prize_swap.printing import (FOOTER_HEIGHT, GAP, PADDING, TITLE_HEIGHT,
    configured_printer, draw_page, plan_pages, render_document, save_pdf)

DAY = date(2026, 11, 5)


def flatten(plan):
    # Column order per page; independent of painter's left/right row iteration.
    return [p for page in plan.pages for side in ("left", "right") for row in page if (p := getattr(row, side)) is not None]


def compact(text):
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text))


def make_products(count=87):
    return [Product(DAY, f"[P{i:03d}] " + "長い日本語の商品名ABCぬいぐるみ " * (1 + i % 4) + f" [E{i:03d}]", 24, 1.5,
                    machines=["20085右", "20098左"] * (1 + i % 3),
                    methods=["橋渡し", "リング", "自由運営"],
                    priority=i % 3 == 0, order=i, limit="トータル⑩") for i in range(count)]


@pytest.mark.parametrize("layout", ["column", "row"])
def test_real_pdf_a4_japanese_multipage_and_every_product(tmp_path, layout):
    products = make_products()
    # Make one item excluded without modifying the rest of the stress data.
    products[17].included = False
    path = tmp_path / "日本語印刷テスト.pdf"
    plan = save_pdf(path, products, DAY, rows=20, layout=layout)
    doc = QPdfDocument()
    assert doc.load(str(path)) == QPdfDocument.Error.None_
    assert doc.pageCount() == len(plan.pages) > 2
    texts = []
    for i in range(doc.pageCount()):
        size = doc.pagePointSize(i)
        assert abs(size.width() - 841.89) < 1
        assert abs(size.height() - 595.28) < 1
        page_text = doc.getAllText(i).text()
        texts.append(compact(page_text))
        assert "印刷日" in compact(page_text)
        assert compact(f"{i+1} / {doc.pageCount()}") in compact(page_text)
        image = doc.render(i, QSize(842, 595))
        assert not image.isNull()  # Actually rasterize every generated PDF page.
    text = "".join(texts)
    for p in products:
        start = p.name.split()[0]
        end = p.name.split()[-1]
        assert text.count(start) == (1 if p.included else 0)
        assert text.count(end) == (1 if p.included else 0)
    # Noto's embedded CJK cmap can extract 長 as the equivalent radical ⻑;
    # verify Japanese body content using a phrase without duplicate glyph aliases.
    phrase = "日本語の商品名ABCぬいぐるみ"
    assert text.count(phrase) == sum(1 + i % 4 for i, p in enumerate(products) if p.included)
    assert "20085右" in text and "20098左" in text
    assert "橋渡し" in text and "リング" in text
    assert "トータル10" in text  # NFKC normalization of the circled number
    doc.close()


@pytest.mark.parametrize("layout", ["column", "row"])
def test_row_bounds_wrapping_and_aligned_grid(qt_app, layout):
    products = make_products(49)
    plan = plan_pages(products, 807.98, 560.98, 50, layout)
    emitted = []
    for page in plan.pages:
        assert len(page) <= 50
        y = TITLE_HEIGHT + plan.header_height
        for row in page:
            assert y + row.height <= plan.height - FOOTER_HEIGHT + .001
            for side, p in enumerate((row.left, row.right)):
                if p is None:
                    continue
                emitted.append(id(p))
                for block in row.cells[side]:
                    assert block.height + 2 * PADDING <= row.height + .001
                    n = block.layout.lineCount()
                    if n:
                        last = block.layout.lineAt(n - 1)
                        assert last.textStart() + last.textLength() == len(block.text.replace("\n", "\u2028").encode("utf-16-le")) // 2
                    for line_no in range(n):
                        line = block.layout.lineAt(line_no)
                        assert line.naturalTextWidth() <= block.width + .01
                        assert line.y() + line.height() <= block.height + .01
            y += row.height  # Both sides use exactly this same height and y.
        assert y <= plan.height - FOOTER_HEIGHT + .001
    assert sorted(emitted) == sorted(id(p) for p in products)


def test_painter_pink_and_black_borders(tmp_path):
    plan = plan_pages([Product(DAY, "優先景品", priority=True)], 808, 561)
    image = QImage(808, 561, QImage.Format.Format_ARGB32)
    image.fill(QColor("white"))
    painter = QPainter(image)
    draw_page(painter, plan, 0, DAY)
    painter.end()
    y = int(TITLE_HEIGHT + plan.header_height)
    assert image.pixelColor(3, y + 3).name() == "#fce4ec"
    assert image.pixelColor(int(plan.widths[0]), y + 10).lightness() < 150
    assert image.pixelColor(int((plan.width + GAP) / 2) + 3, y + 3).name() == "#ffffff"


@pytest.mark.parametrize("layout", ["column", "row"])
def test_layout_matches_excel_when_height_fits(layout):
    products = [Product(DAY, f"商品{i}", order=i) for i in range(11)]
    plan = plan_pages(products, 808, 561, rows=3, layout=layout)
    actual = {(page_no, r, side): p.name for page_no, page in enumerate(plan.pages) for r, row in enumerate(page) for side, p in enumerate((row.left, row.right)) if p}
    expected = {(page, row, side): p.name for page, row, side, p in positions(products, rows=3, layout=layout)}
    assert actual == expected


def test_unprintable_item_fails_without_overwriting_pdf(tmp_path):
    path = tmp_path / "previous.pdf"
    path.write_bytes(b"keep previous")
    with pytest.raises(AppError, match="1ページ"):
        save_pdf(path, [Product(DAY, "異常に長い商品" * 2000)], DAY)
    assert path.read_bytes() == b"keep previous"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["previous.pdf"]


def test_page_range_and_reverse_order(tmp_path):
    path = tmp_path / "range.pdf"
    products = [Product(DAY, f"MARK{i}", order=i) for i in range(8)]
    printer = configured_printer(path)
    printer.setFromTo(2, 3)
    printer.setPageOrder(QPrinter.PageOrder.LastPageFirst)
    render_document(printer, products, DAY, rows=1)
    doc = QPdfDocument()
    assert doc.load(str(path)) == QPdfDocument.Error.None_
    assert doc.pageCount() == 2
    assert "MARK4" in doc.getAllText(0).text()
    assert "MARK2" in doc.getAllText(1).text()
    doc.close()


def test_pdf_protects_input_path(tmp_path):
    source = tmp_path / "source.xlsx"
    source.write_bytes(b"original")
    with pytest.raises(AppError):
        save_pdf(source, [Product(DAY, "商品")], DAY, source=source)
    assert source.read_bytes() == b"original"


def test_unicode_supplementary_characters_and_pdf_copies(tmp_path):
    products = [Product(DAY, "絵文字🌟😀景品 " * 5, order=i) for i in range(4)]
    path = tmp_path / "copies.pdf"
    printer = configured_printer(path)
    printer.setCopyCount(2)
    printer.setCollateCopies(True)
    plan = render_document(printer, products, DAY, rows=1)
    for page in plan.pages:
        for row in page:
            for side in row.cells:
                for block in side:
                    n = block.layout.lineCount()
                    if n:
                        line = block.layout.lineAt(n-1)
                        assert line.textStart()+line.textLength() == len(block.text.replace("\n","\u2028").encode("utf-16-le")) // 2
    doc=QPdfDocument()
    assert doc.load(str(path)) == QPdfDocument.Error.None_
    expected = len(plan.pages) if printer.supportsMultipleCopies() else len(plan.pages) * 2
    assert doc.pageCount() == expected
    doc.close()
