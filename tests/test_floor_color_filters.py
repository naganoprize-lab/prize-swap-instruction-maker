from dataclasses import asdict
from datetime import date, timedelta
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
from PySide6.QtCore import QDate, QSize
from PySide6.QtPdf import QPdfDocument
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox

from prize_swap.core import Product
from prize_swap.floor import FloorWidget
from prize_swap.floor_data import Machine
from prize_swap.floor_status import filter_status, FILTER_FIELDS
from prize_swap.floor_print import FloorPrintOptions, floor_printer, save_floor_pdf
from prize_swap.floor_print_ui import FloorPreviewDialog
from prize_swap.pdf_validation import normalized_pdf_text, require_searchable_pdf

DAY = date(2026, 11, 7)


@pytest.mark.parametrize(
    "age,colour,label",
    [
        (9, "#FFFFFF", "9日"),
        (10, "#C5A5ED", "10日"),
        (0, "#FFFFFF", "0日"),
        (-1, "#FFFFFF", "投入予定"),
        (None, "#B8BEC6", "—"),
    ],
)
def test_age_boundary_unknown_and_future(age, colour, label):
    m = Machine(
        ratio=250, installed_on=(DAY - timedelta(days=age)).isoformat() if age is not None else None
    )
    background, status = filter_status(m, "age", DAY, True)
    assert background == colour and label in status
    assert "-1" not in status


def test_planned_filter_neutralizes_ratio_colours():
    for value in (0, 49.9, 50, 100, 200, None):
        m = Machine(ratio=value)
        assert filter_status(m, "planned", DAY)[0] == "#FFFFFF"
        assert filter_status(m, "planned", DAY, True)[0] == "#A9D7FF"
    assert filter_status(Machine(ratio=49.9), "ratio", DAY)[0] == "#F4A6A6"


def test_filters_keep_all_300_nodes_selected_coordinates_sizes_and_roundtrip(tmp_path):
    products = [Product(DAY, "予定", machines=["20000右", "20001右"])]
    w = FloorWidget(lambda: products, lambda: DAY, tmp_path / "auto.json")
    try:
        w.machines = [
            Machine(
                number=f"{20000+i}右",
                product=f"商品{i}",
                ratio=i,
                x=i % 20 * 250,
                y=i // 20 * 180,
                installed_on=(DAY - timedelta(days=i % 15)).isoformat(),
            )
            for i in range(300)
        ]
        w.reference_date.setDate(QDate(DAY))
        w.rebuild()
        before = [asdict(m) for m in w.machines]
        uid = w.machines[0].uid
        w.nodes[uid].setSelected(True)
        for mode in ("planned", "ratio", "age"):
            w.color_filter.setCurrentIndex(w.color_filter.findData(mode))
            QApplication.processEvents()
            assert len(w.visible_nodes()) == 300
            assert [asdict(m) for m in w.machines] == before and [m.uid for m in w.selected()] == [
                uid
            ]
            assert w.card_context(w.machines[0])[3] == FILTER_FIELDS[mode]
        w.autosave()
        other = FloorWidget(lambda: products, lambda: DAY, w.storage)
        assert (
            other.presentation["color_filter"] == "age"
            and other.color_filter.currentData() == "age"
        )
        assert other.print_options.color_filter == "ratio"
        other.timer.stop()
        other.close()
    finally:
        w.timer.stop()
        w.day_timer.stop()
        w.close()


def pdf_text(path):
    return normalized_pdf_text("".join(p["text"] for p in require_searchable_pdf(path)["pages"]))


@pytest.mark.parametrize("paper", ["A4", "A3"])
@pytest.mark.parametrize("mode", ["ratio", "planned", "age"])
def test_pdf_filters_colour_and_visible_information(tmp_path, paper, mode):
    source = [
        Machine(
            number="20000右",
            product="FILTER_NAME 商品名",
            ratio=49.9,
            installed_on=(DAY - timedelta(days=10)).isoformat(),
        )
    ]
    path = tmp_path / f"{paper}-{mode}.pdf"
    options = FloorPrintOptions(paper=paper, color_filter=mode)
    plan = save_floor_pdf(path, source, {}, options, DAY)
    card = plan.pages[0].cards[0]
    assert card.background == {"ratio": "#F4A6A6", "planned": "#FFFFFF", "age": "#C5A5ED"}[mode]
    content = pdf_text(path)
    assert content.count("FILTER_NAME") == 1
    if mode == "age":
        assert "設置日数10日" in content and "49.9%" not in content
    else:
        assert "アベ比49.9%" in content and "設置日数10日" not in content
    # Measure the actual raster from QPdfDocument, including printable margins.
    doc = QPdfDocument()
    doc.load(str(path))
    image = doc.render(0, QSize(842, 595))
    assert not image.isNull()
    expected = card.background.lower()
    hits = 0
    for y in range(30, image.height() - 30, 10):
        for x in range(30, image.width() - 30, 10):
            if image.pixelColor(x, y).name() == expected:
                hits += 1
    assert hits > 100
    doc.close()


@pytest.mark.parametrize("mode", ["ratio", "planned", "age"])
@pytest.mark.parametrize("printing_mode", ["tile", "list"])
def test_independent_preview_pdf_and_direct_print_match(tmp_path, monkeypatch, mode, printing_mode):
    import prize_swap.floor as module

    w = FloorWidget(lambda: [], lambda: DAY, tmp_path / "auto.json")
    preview = None
    try:
        w.machines = [
            Machine(
                number=f"{20000+i}右",
                product=f"MATCH{i} 日本語商品名",
                ratio=49.9,
                x=i * 300,
                installed_on=(DAY - timedelta(days=10)).isoformat(),
            )
            for i in range(7)
        ]
        w.reference_date.setDate(QDate(DAY))
        w.rebuild()
        w.set_color_filter("planned")
        w.set_print_options(FloorPrintOptions(color_filter=mode, mode=printing_mode))
        preview = FloorPreviewDialog(w)
        preview.show()
        QApplication.processEvents()
        preview.preview.updatePreview()
        first = tmp_path / "PDF.pdf"
        plan = w.paint_floor(floor_printer(w.print_options, first))
        assert preview.preview.pageCount() == len(plan.pages)
        assert w.presentation["color_filter"] == "planned"
        second = tmp_path / "直接.pdf"

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
        monkeypatch.setattr(module, "floor_printer", lambda options: floor_printer(options, second))
        monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Yes)
        errors = []
        monkeypatch.setattr(QMessageBox, "warning", lambda *args: errors.append(args))
        w.print_floor()
        assert not errors
        assert pdf_text(first) == pdf_text(second)
        a = QPdfDocument()
        b = QPdfDocument()
        a.load(str(first))
        b.load(str(second))
        assert a.pageCount() == b.pageCount()
        for i in range(a.pageCount()):
            assert a.render(i, QSize(842, 595)) == b.render(i, QSize(842, 595))
        a.close()
        b.close()
    finally:
        if preview:
            preview.close()
        w.timer.stop()
        w.day_timer.stop()
        w.close()


@pytest.mark.parametrize("scale", ["1", "1.5", "2"])
def test_fullscreen_visible_pixels_and_input_at_high_dpi(tmp_path, scale):
    output = tmp_path / f"scale-{scale}"
    result = subprocess.run(
        [sys.executable, "-m", "prize_swap.floor_visual_check", str(output)],
        env=os.environ | {"QT_SCALE_FACTOR": scale},
        capture_output=True,
        text=True,
        timeout=60,
    )
    report = json.loads((output / "report.json").read_text(encoding="utf-8"))
    assert result.returncode == 0 and report["ok"], report.get("error", result.stderr)
    assert report["ratio"]["background_pixels"] > 100 and report["ratio"]["text_pixels"] > 15
    assert report["age"]["dpr"] >= float(scale) * 0.9
    assert (
        report["selection_preserved"] and report["drag_and_undo"] and report["details_opened"] == 1
    )
