from copy import deepcopy
from dataclasses import asdict
from datetime import date
import json
import pytest
from PySide6.QtCore import Qt, QSize, QRectF
from PySide6.QtGui import QColor, QPalette, QImage, QPainter
from PySide6.QtPdf import QPdfDocument
from PySide6.QtWidgets import QApplication, QDialog
from prize_swap.theme import manager, analysis_color, tokens, contrast_text, ThemeCombo, ThemeMenu
from prize_swap.floor import FloorWidget
from prize_swap.floor_data import Machine
from prize_swap.floor_print import FloorPrintOptions, floor_printer
from prize_swap.floor_details import MachineDetailDialog
from prize_swap.ui import Window
from prize_swap.pdf_validation import require_searchable_pdf, normalized_pdf_text

DAY = date(2026, 11, 7)


@pytest.fixture
def theme(tmp_path):
    m = manager()
    original = (m.mode, m.system_scheme, m.storage)
    m.storage = tmp_path / "appearance.json"
    m.set_mode("light", persist=False)
    yield m
    m.storage = None
    m.system_scheme = original[1]
    m.set_mode(original[0], persist=False)
    m.storage = original[2]


def test_system_manual_priority_immediate_switch_and_persistence(theme, tmp_path):
    theme.system_changed(Qt.ColorScheme.Dark)
    theme.set_mode("system")
    assert theme.dark and tokens()["background"] == "#111827"
    theme.set_mode("light")
    assert not theme.dark and tokens()["background"] == "#F5F7FA"
    theme.system_changed(Qt.ColorScheme.Dark)
    assert not theme.dark
    theme.set_mode("dark")
    assert QApplication.palette().color(QPalette.ColorRole.Text) == QColor("#F1F5F9")
    path = theme.storage
    theme.set_mode("light", persist=False)
    theme.load(path)
    assert theme.mode == "dark"
    assert json.loads(path.read_text())["theme"] == "dark"
    combo = ThemeCombo()
    menu = ThemeMenu()
    combo.setCurrentIndex(combo.findData("system"))
    assert menu.actions_by_mode["system"].isChecked() and theme.mode == "system"


def test_300_cards_theme_changes_preserve_geometry_zoom_search_and_selection(theme, tmp_path):
    w = FloorWidget(lambda: [], lambda: DAY, tmp_path / "auto.json")
    try:
        w.machines = [
            Machine(
                number=f"{20000+i}右", product=f"商品{i}", ratio=85, x=i % 20 * 220, y=i // 20 * 160
            )
            for i in range(300)
        ]
        w.rebuild()
        w.show()
        QApplication.processEvents()
        w.set_zoom(125)
        uid = w.machines[0].uid
        w.nodes[uid].setSelected(True)
        w.floor_search.setText("商品0")
        before = [asdict(m) for m in w.machines]
        transform = w.view.transform()
        positions = {k: n.pos() for k, n in w.nodes.items()}
        for mode in ("dark", "light", "system"):
            theme.set_mode(mode)
            QApplication.processEvents()
            assert (
                [asdict(m) for m in w.machines] == before
                and w.view.transform() == transform
                and len(w.visible_nodes()) == 300
            )
            assert {k: n.pos() for k, n in w.nodes.items()} == positions and [
                m.uid for m in w.selected()
            ] == [uid]
            assert w.view.backgroundBrush().color() == QColor(tokens()["canvas"])
    finally:
        w.timer.stop()
        w.day_timer.stop()
        w.close()


def test_dark_card_pixels_detail_palette_and_light_print_unchanged(theme, tmp_path):
    w = FloorWidget(lambda: [], lambda: DAY, tmp_path / "auto.json")
    try:
        w.machines = [
            Machine(number="20088右", product="日本語の商品全文", ratio=120, average=6000)
        ]
        w.rebuild()

        def pdf(name):
            p = tmp_path / name
            plan = w.paint_floor(floor_printer(w.print_options, p))
            doc = QPdfDocument()
            doc.load(str(p))
            image = doc.render(0, QSize(842, 595))
            doc.close()
            return (
                plan,
                image,
                normalized_pdf_text("".join(r["text"] for r in require_searchable_pdf(p)["pages"])),
            )

        plan, light_image, light_text = pdf("light.pdf")
        assert plan.pages[0].cards[0].background == "#94D8A0"
        theme.set_mode("dark")
        w.scene.clearSelection()
        image = QImage(180, 120, QImage.Format.Format_ARGB32)
        image.fill(Qt.GlobalColor.transparent)
        painter = QPainter(image)
        w.scene.render(painter, QRectF(0, 0, 180, 120), QRectF(0, 0, 180, 120))
        painter.end()
        assert image.pixelColor(170, 110) == QColor(analysis_color("#94D8A0"))
        assert contrast_text(analysis_color("#94D8A0")) == "#F1F5F9"
        detail = MachineDetailDialog(w, w.machines[0])
        detail.ensurePolished()
        assert detail.palette().color(QPalette.ColorRole.Window) == QColor(
            "#111827"
        ) and detail.values["ratio"].palette().color(QPalette.ColorRole.WindowText) == QColor(
            "#F1F5F9"
        )
        detail.close()
        plan, dark_image, dark_text = pdf("dark.pdf")
        assert (
            dark_image == light_image
            and dark_text == light_text
            and dark_text.count("日本語の商品全文") == 1
        )
    finally:
        w.timer.stop()
        w.day_timer.stop()
        w.close()


def test_window_theme_menu_and_reopen_restore_preference(theme, tmp_path):
    w = Window(layout_storage=tmp_path / "auto.json")
    second = None
    try:
        w.themes.set_mode("dark")
        assert "ダーク" in w.theme_button.text()
        w.floor.autosave()
        w.dirty = False
        w.close()
        theme.set_mode("light", persist=False)
        second = Window(layout_storage=tmp_path / "auto.json")
        assert second.themes.mode == "dark" and second.floor.state()["theme_mode"] == "dark"
        second.theme_button.menu().actions_by_mode["light"].trigger()
        assert not second.themes.dark
    finally:
        for item in (w, second):
            if item:
                item.dirty = False
                item.floor.timer.stop()
                item.floor.day_timer.stop()
                item.close()


@pytest.mark.parametrize("scale", ["1", "1.25", "1.5"])
def test_actual_workspace_pixels_matching_theme_navigation_and_printing_at_dpi(tmp_path, scale):
    import os, subprocess, sys

    output = tmp_path / f"workspace-{scale}"
    result = subprocess.run(
        [sys.executable, "-m", "prize_swap.workspace_visual_check", str(output)],
        env=os.environ | {"QT_SCALE_FACTOR": scale},
        capture_output=True,
        text=True,
        timeout=90,
    )
    report = json.loads((output / "report.json").read_text(encoding="utf-8"))
    assert result.returncode == 0 and report["ok"], report.get("error", result.stderr)
    assert report["machines"] == 300 and report["matching"] == {
        "direct": 1,
        "reference": 99,
        "layout": 200,
    }
    for mode in ("light", "dark", "system"):
        assert (
            report["themes"][mode]["1"]["background_pixels"] > 100
            and report["themes"][mode]["1"]["text_pixels"] > 15
        )
        assert report["themes"][mode]["1"]["dpr"] >= float(scale) * 0.9
    assert (
        report["print_theme_equal"]
        and report["direct_print_equal"]
        and report["details_opened"]
        and report["fullscreen"]
        and report["prefs_restored"]
    )
