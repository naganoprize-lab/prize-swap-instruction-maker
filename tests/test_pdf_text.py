"""Font cmap aliases must not hide missing, repeated, or unsearchable names."""

from datetime import date
import re
import unicodedata

import pytest
from pypdf import PdfReader, PdfWriter
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtPdf import QPdfDocument

from prize_swap.core import Product
from prize_swap.labels_data import LabelEntry
from prize_swap.labels_printing import draw_labels_page, plan_labels, save_labels_pdf
from prize_swap.pdf_validation import normalized_pdf_text, require_searchable_pdf
from prize_swap.printing import (
    TextBlock,
    configured_printer,
    draw_page,
    plan_pages,
    render_document,
    save_pdf,
)

DAY = date(2026, 11, 5)
NAME = "鬼滅の刃 フィギュア"


@pytest.mark.parametrize(
    "text",
    [
        NAME,
        "⻤滅の刃\nフィギュア",
        "⿁滅の刃\tフィギュア",
        "鬼滅の刃 フィキ\n\u3099ュア",
        "鬼滅の刃 ﾌｨｷﾞｭｱ",
    ],
)
def test_known_font_aliases_whitespace_and_split_marks(text):
    assert normalized_pdf_text(text) == normalized_pdf_text(NAME)


def test_windows_log_failure_replays_without_discarding_missing_or_repeated_names():
    # Exact character from Actions #14/#15: NFKC does not map U+2EE4.
    windows_name = "\u2ee4滅の刃フィギュア"
    old = lambda value: re.sub(r"\s+", "", unicodedata.normalize("NFKC", value))
    assert old(windows_name).count(old(NAME)) == 0
    assert normalized_pdf_text(windows_name).count(normalized_pdf_text(NAME)) == 1
    assert normalized_pdf_text(windows_name * 3).count(normalized_pdf_text(NAME)) == 3
    assert normalized_pdf_text(windows_name * 2).count(normalized_pdf_text(NAME)) == 2
    assert normalized_pdf_text("").count(normalized_pdf_text(NAME)) == 0
    assert normalized_pdf_text("鬼滅の刃フィギア") != normalized_pdf_text(NAME)
    assert normalized_pdf_text("鬼滅の刃フィギュア!") != normalized_pdf_text(NAME)


def test_long_character_font_alias_preserves_the_character():
    assert normalized_pdf_text("\u2ed1い商品") == normalized_pdf_text("長い商品")
    assert normalized_pdf_text("い商品") != normalized_pdf_text("長い商品")


def pdf_image(path):
    document = QPdfDocument()
    assert document.load(str(path)) == QPdfDocument.Error.None_
    size = document.pagePointSize(0)
    result = document.render(0, size.toSize() * 2)
    assert not result.isNull()
    text = document.getAllText(0).text()
    pages = document.pageCount()
    document.close()
    return result, text, size, pages


def windows_cmap_alias(source, target):
    """Reproduce the Windows font's reverse-cmap alias, keeping every drawing.

    Only a test fixture's ToUnicode map changes. The application never rewrites
    or deletes PDF text. This makes the same failure testable on Linux too.
    """
    original = PdfReader(source)
    writer = PdfWriter(clone_from=source)
    found = False
    for page in writer.pages:
        for ref in page["/Resources"]["/Font"].values():
            font = ref.get_object()
            stream = font["/ToUnicode"].get_object()
            data = stream.get_data()
            if any(token in data for token in (b"<2FC1>", b"<9B3C>", b"<2EE4>")):
                found = True
                stream.set_data(data.replace(b"<2FC1>", b"<2EE4>").replace(b"<9B3C>", b"<2EE4>"))
    assert found, "The fixture must contain a real searchable ghost glyph."
    writer.write(target)
    changed = PdfReader(target)
    assert len(original.pages) == len(changed.pages)
    for before, after in zip(original.pages, changed.pages):
        assert before.get_contents().get_data() == after.get_contents().get_data()


@pytest.mark.parametrize("kind,expected", [("instructions", 1), ("labels", 3), ("direct", 1)])
def test_real_pdf_windows_alias_keeps_pixels_searchable_text_and_exact_counts(
    tmp_path, kind, expected
):
    product = Product(
        DAY,
        NAME,
        40,
        1,
        machines=["20098右", "20083左", "20103右"],
        planned_booths={"橋渡し": 2, "リング": 3},
        machine_methods={"20098右": "橋渡し", "20083左": "リング", "20103右": "橋渡し"},
    )
    source, alias = tmp_path / "original.pdf", tmp_path / "windows-alias.pdf"
    if kind == "labels":
        save_labels_pdf(source, [LabelEntry(product)])
    elif kind == "direct":
        render_document(configured_printer(source), [product], DAY, mode="standard")
    else:
        save_pdf(source, [product], DAY, mode="standard")
    windows_cmap_alias(source, alias)
    original = require_searchable_pdf(source)
    changed = require_searchable_pdf(alias)
    raw = "".join(p["text"] for p in changed["pages"])
    assert "\u2ee4" in raw
    assert normalized_pdf_text(raw).count(normalized_pdf_text(NAME)) == expected
    assert normalized_pdf_text(raw) == normalized_pdf_text(
        "".join(p["text"] for p in original["pages"])
    )
    before, _, before_size, before_pages = pdf_image(source)
    after, qt_text, size, pages = pdf_image(alias)
    assert before == after  # Actually rasterize and compare; no visual content changed.
    assert size == before_size and pages == before_pages == 1
    assert normalized_pdf_text(qt_text).count(normalized_pdf_text(NAME)) == expected
    if kind == "labels":
        assert size.width() < size.height()
        assert "リング" not in raw and "20098" not in raw
    else:
        assert size.width() > size.height()
        assert "リング3ブース" in normalized_pdf_text(raw)
        for machine in product.machines:
            assert normalized_pdf_text(raw).count(machine) == 1


def test_product_name_is_drawn_once_in_merged_cell_and_once_per_label(monkeypatch):
    product = Product(DAY, NAME, machines=["20098右", "20083左", "20103右"])
    calls = []
    real_draw = TextBlock.draw

    def record(self, painter, origin):
        if self.text == NAME:
            calls.append((origin.x(), origin.y()))
        return real_draw(self, painter, origin)

    monkeypatch.setattr(TextBlock, "draw", record)
    image = QImage(808, 561, QImage.Format.Format_ARGB32)
    image.fill(QColor("white"))
    painter = QPainter(image)
    plan = plan_pages([product], 808, 561)
    draw_page(painter, plan, 0, DAY)
    painter.end()
    assert len(calls) == 1
    assert plan.pages[0][0].groups[0].cells[3].text == NAME
    calls.clear()
    image = QImage(561, 808, QImage.Format.Format_ARGB32)
    image.fill(QColor("white"))
    painter = QPainter(image)
    labels = plan_labels([LabelEntry(product)], 561, 808)
    draw_labels_page(painter, labels, 0)
    painter.end()
    assert len(calls) == len(set(calls)) == 3  # Three separate requested labels.
