"""Native/offscreen 300-booth matching, theme, navigation and white-PDF check."""

from dataclasses import asdict
from datetime import date
import json
import os
from pathlib import Path
import sys
import traceback


def run(output):
    if sys.platform != "win32":
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtGui import QColor
    from PySide6.QtCore import QDate, QTimer, QSize, Qt
    from PySide6.QtWidgets import QApplication, QDialog, QMessageBox
    from PySide6.QtTest import QTest
    from PySide6.QtPdf import QPdfDocument
    from openpyxl import Workbook
    from unittest.mock import patch
    from tempfile import TemporaryDirectory
    from .ui import Window
    from .floor_data import Machine
    from .floor_details import MachineDetailDialog
    from .floor_print import FloorPrintOptions, floor_printer
    from .floor_visual_check import inspect_card
    from .floor_status import ratio, ratio_text
    from .theme import analysis_color, contrast_text, manager
    from .sales_analysis import HEADERS
    from .fonts import validate_print_fonts
    from .pdf_validation import require_searchable_pdf, normalized_pdf_text

    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    report = dict(ok=False, physical_printer_tested=False)
    window = None
    runtime = None
    try:
        app = QApplication.instance() or QApplication([])
        report["platform"] = app.platformName()
        report["scale_requested"] = os.environ.get("QT_SCALE_FACTOR", "1")
        report["fonts"] = validate_print_fonts()
        runtime = TemporaryDirectory(dir=output, prefix=".runtime-")
        window = Window(layout_storage=Path(runtime.name) / "auto.json")
        report["initial_theme"] = window.themes.mode
        assert window.themes.mode == "system"
        floor = window.floor
        window.day.setDate(QDate(2026, 11, 7))
        floor.reference_date.setDate(QDate(2026, 11, 7))
        floor.machines = [
            Machine(
                number="20000右" if i == 0 else "",
                source_key=f"L_{i}",
                product=f"VIEW{i:03} 日本語の景品フィギュア",
                ratio=[49.9, 85, 120, 250, None][i % 5],
                average=None if i % 5 == 4 else [2495, 4250, 6000, 12500][i % 5],
                x=i % 20 * 240,
                y=i // 20 * 170,
                width=220,
                height=140,
                installed_on="2026-10-20",
                operation_method="リング",
            )
            for i in range(300)
        ]
        floor.rebuild()
        floor.apply_matching(
            [dict(source_key=f"L_{i}", number=f"{20000+i}-02") for i in range(300)], {}
        )
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "景品運営分析表"
        for col, label in HEADERS.items():
            sheet.cell(1, col + 1, label)
        for i in range(100):
            sheet.append(
                [
                    None,
                    f"{20000+i}-02",
                    "UFO9 third",
                    None,
                    "売上登録用",
                    18000,
                    3,
                    6000,
                    7200,
                    0.6,
                    0.05,
                    10,
                    1800,
                    None,
                    500,
                    10800,
                ]
            )
        sheet.append([None, "期間(自)", "2026/11/04"])
        sheet.append([None, "期間(至)", "2026/11/06"])
        sheet.append([None, "検索日時: 2026/11/07 18:19:35"])
        path = Path(runtime.name) / "analysis.xlsx"
        workbook.save(path)
        workbook.close()
        floor.import_analysis_path(path)
        assert (
            floor.machines[0].match_method == "direct"
            and floor.machines[1].match_method == "reference"
        )
        assert (
            ratio(floor.machines[101]) == 85
            and floor.machines[101].match_method == "layout"
            and ratio(floor.machines[299]) is None
        )
        available = app.primaryScreen().availableGeometry()
        report["available_screen"] = [available.width(), available.height()]
        window.resize(
            max(1100, min(1450, available.width() - 20)),
            max(700, min(900, available.height() - 40)),
        )
        window.show()
        window.tabs.setCurrentWidget(floor)
        app.processEvents()
        floor.set_zoom(100)
        before = [(m.x, m.y, m.width, m.height, m.number, m.source_key) for m in floor.machines]
        nodes = {k: id(v) for k, v in floor.nodes.items()}
        report["themes"] = {}
        for mode in ("light", "dark", "system"):
            window.themes.set_mode(mode, persist=False)
            app.processEvents()
            window.tabs.setCurrentWidget(floor)
            app.processEvents()
            checks = {}
            for index, base in ((1, "#94D8A0"), (101, "#FFFFFF"), (299, "#B8BEC6")):
                color = analysis_color(base)
                checks[str(index)] = inspect_card(
                    floor, floor.machines[index].uid, color, contrast_text(color)
                )
            assert floor._zoom == 100 and len(floor.visible_nodes()) == 300
            floor.floor_search.setText("VIEW001")
            app.processEvents()
            assert len(floor._match_uids) == 1 and len(floor.visible_nodes()) == 300
            floor.floor_search.setText("NO_MATCH")
            assert not floor._match_uids and len(floor.visible_nodes()) == 300
            floor.floor_search.clear()
            app.processEvents()
            assert {k: id(v) for k, v in floor.nodes.items()} == nodes
            assert before == [
                (m.x, m.y, m.width, m.height, m.number, m.source_key) for m in floor.machines
            ]
            floor.view.ensureVisible(floor.nodes[floor.machines[1].uid])
            app.processEvents()
            assert window.grab().save(str(output / f"{mode}-floor.png"))
            window.navigation.buttons["dashboard"].click()
            app.processEvents()
            assert window.dashboard_page.cards["total"].value.text() == "300"
            assert window.grab().save(str(output / f"{mode}-dashboard.png"))
            window.navigation.buttons["floor"].click()
            app.processEvents()
            report["themes"][mode] = checks
        # Bounds are measured inside the real window, including high-DPI layout.
        QTest.qWait(50)
        root = window.centralWidget()
        for control in (
            window.theme_button,
            window.settings_button,
            *window.print_buttons,
        ):
            assert control.isVisible() and root.rect().contains(
                control.mapTo(root, control.rect().bottomRight())
            ), "Header action outside workspace"
        report["workspace_geometry"] = dict(
            window=[window.width(), window.height()],
            viewport=[floor.view.viewport().width(), floor.view.viewport().height()],
            compact_dashboard=floor.dashboard.compact,
        )
        assert (
            floor.view.viewport().height() > 180 and floor.view.viewport().width() > 300
        ), f"Insufficient canvas: {report['workspace_geometry']}; screen={report['available_screen']}"
        opened = []

        def close_details():
            dialog = (
                floor.detail_controller.panel if floor.detail_controller.dock.isVisible() else None
            )
            if dialog is None:
                QTimer.singleShot(30, close_details)
                return
            try:
                assert (
                    dialog.values["source_key"].text() == "L_1"
                    and "管理ナンバーから一致" in dialog.values["match_method"].text()
                )
                assert dialog.values["ratio"].text() == "120.0%"
                opened.append(True)
                dialog.grab().save(str(output / "machine-detail.png"))
            finally:
                floor.detail_controller.dock.close()

        node = floor.nodes[floor.machines[1].uid]
        floor.view.ensureVisible(node)
        app.processEvents()
        QTimer.singleShot(50, close_details)
        QTest.mouseClick(
            floor.view.viewport(),
            Qt.MouseButton.LeftButton,
            pos=floor.view.mapFromScene(node.sceneBoundingRect().center()),
        )
        app.processEvents()
        QTest.qWait(80)
        assert opened
        scene = floor.scene
        floor.toggle_fullscreen()
        app.processEvents()
        assert floor._fullscreen_host.isFullScreen() and floor.view.scene() is scene
        inspect_card(
            floor,
            floor.machines[1].uid,
            analysis_color("#94D8A0"),
            contrast_text(analysis_color("#94D8A0")),
        )
        floor._fullscreen_host.search.setText("VIEW101")
        assert len(floor._match_uids) == 1 and len(floor.visible_nodes()) == 300
        floor._fullscreen_host.search.clear()
        floor._fullscreen_host.grab().save(str(output / "fullscreen.png"))
        floor._fullscreen_host.close()
        app.processEvents()
        assert window.tabs.currentWidget() is floor and before == [
            (m.x, m.y, m.width, m.height, m.number, m.source_key) for m in floor.machines
        ]
        floor.scene.clearSelection()
        for m in floor.machines[:6]:
            floor.nodes[m.uid].setSelected(True)
        floor.set_print_options(FloorPrintOptions(mode="list", selection_only=True, paper="A4"))

        def pdf(name):
            path = output / name
            floor.paint_floor(floor_printer(floor.print_options, path))
            doc = QPdfDocument()
            assert doc.load(str(path)) == QPdfDocument.Error.None_
            image = doc.render(0, QSize(842, 595))
            doc.close()
            text = normalized_pdf_text(
                "".join(p["text"] for p in require_searchable_pdf(path)["pages"])
            )
            return image, text

        window.themes.set_mode("light", persist=False)
        light, text = pdf("light.pdf")
        window.themes.set_mode("dark", persist=False)
        dark, dark_text = pdf("dark.pdf")
        assert light == dark and text == dark_text
        for i in range(6):
            assert text.count(f"VIEW{i:03}") == 1
        assert (
            sum(
                dark.pixelColor(x, y) == QColor("white")
                for y in range(0, dark.height(), 10)
                for x in range(0, dark.width(), 10)
            )
            > 2000
        )
        direct = output / "direct.pdf"

        class Accepted:
            def __init__(self, *args):
                pass

            def setMinMax(self, *args):
                pass

            def setOption(self, *args):
                pass

            def exec(self):
                return QDialog.DialogCode.Accepted

        errors = []
        with patch("prize_swap.floor.QPrintDialog", Accepted), patch(
            "prize_swap.floor.floor_printer",
            lambda options: floor_printer(options, direct),
        ), patch.object(
            QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Yes
        ), patch.object(
            QMessageBox, "warning", lambda *args: errors.append(args)
        ):
            floor.print_floor()
        assert not errors and text == normalized_pdf_text(
            "".join(p["text"] for p in require_searchable_pdf(direct)["pages"])
        )
        manager().set_mode("dark")
        manager().set_mode("light", persist=False)
        manager().load(window.appearance_path)
        assert manager().mode == "dark"
        report.update(
            ok=True,
            machines=300,
            matching={"direct": 1, "reference": 99, "layout": 200},
            layout_ratio=85,
            print_theme_equal=True,
            direct_print_equal=True,
            prefs_restored=True,
            details_opened=True,
            fullscreen=True,
            window_size=[window.width(), window.height()],
        )
    except Exception:
        report["error"] = traceback.format_exc()
    finally:
        if window:
            window.dirty = False
            window.floor.timer.stop()
            window.floor.day_timer.stop()
            window.close()
        if runtime:
            runtime.cleanup()
        (output / "report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(run(sys.argv[1]))
