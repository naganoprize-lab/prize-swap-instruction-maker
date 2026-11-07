"""Real 300-booth analytics/detail interaction and export probe, also in the EXE."""

import json
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from time import perf_counter
import traceback


def run(output):
    if sys.platform != "win32":
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from PySide6.QtCore import QDate, Qt, QPointF
    from PySide6.QtTest import QTest
    from openpyxl import Workbook, load_workbook
    from .fonts import validate_print_fonts
    from .ui import Window
    from .floor_data import Machine
    from .floor_details import MachineDetailDialog
    from .analytics_charts import CHARTS
    from .theme import manager
    from .floor_status import ratio, effective_stat
    from .sales_analysis import HEADERS
    from .floor_print import FloorPrintOptions, floor_printer
    from .pdf_validation import require_searchable_pdf, normalized_pdf_text
    from PySide6.QtPdf import QPdfDocument
    from PySide6.QtCore import QSize

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
        f = window.floor
        p = window.analysis_page
        f.reference_date.setDate(QDate(2026, 11, 7))
        f.follow_today = False
        kinds = ["UFO9 third", "UFO10", "UFO8X"]
        methods = ["橋渡し", "リング", "フック"]
        f.machines = [
            Machine(
                number=f"{22000+i}右",
                source_key=f"L_{i}",
                product=f"CHECK{i:03} 日本語景品 フィギュア ABC123",
                kind=kinds[i % 3],
                operation_method=methods[i % 3],
                installed_on="2026-09-01",
                reference_average=5000,
                x=i % 20 * 240,
                y=i // 20 * 170,
                width=220,
                height=140,
            )
            for i in range(300)
        ]
        f.rebuild()
        for period_index in range(2):
            book = Workbook()
            sheet = book.active
            sheet.title = "景品運営分析表"
            for col, label in HEADERS.items():
                sheet.cell(1, col + 1, label)
            for i in range(300):
                average = 2500 + i * 30 + period_index * 100
                sales = average * 3
                cost = sales * 0.6
                sheet.append(
                    [
                        None,
                        f"{22000+i}-02",
                        kinds[i % 3],
                        None,
                        "売上登録用",
                        sales,
                        3,
                        average,
                        sales - cost,
                        0.6,
                        0.05,
                        10,
                        sales / 10,
                        None,
                        500,
                        cost,
                    ]
                )
            sheet.append([None, "期間(自)", f"2026/11/0{1+period_index*3}"])
            sheet.append([None, "期間(至)", f"2026/11/0{3+period_index*3}"])
            sheet.append([None, "検索日時: 2026/11/07 18:19:35"])
            path = Path(runtime.name) / f"analysis-{period_index}.xlsx"
            book.save(path)
            book.close()
            f.import_analysis_path(path)
        before = [
            (m.uid, m.x, m.y, m.width, m.height, m.product, m.operation_method) for m in f.machines
        ]
        node_ids = {uid: id(n) for uid, n in f.nodes.items()}
        screen = app.primaryScreen().availableGeometry()
        window.resize(
            min(1500, max(1000, screen.width() - 20)), min(950, max(700, screen.height() - 40))
        )
        window.show()
        window.tabs.setCurrentWidget(f)
        QTest.qWait(50)
        f.set_zoom(125)
        f.floor_search.setText("CHECK29")
        app.processEvents()
        timer = perf_counter()
        rs = f.analytics.records()
        f.analytics.query()
        f.analytics.query()
        report["query_seconds"] = perf_counter() - timer
        assert report["query_seconds"] < 3 and len(rs) == 300
        assert rs[0]["average"] == effective_stat(f.machines[0], "average") and rs[0][
            "ratio"
        ] == ratio(f.machines[0])
        assert f.analysis_state["period_start"] == "2026-11-04" and f.analysis_state[
            "extracted_at"
        ].startswith("2026-11-07")
        QTest.mouseClick(window.navigation.buttons["analysis"], Qt.MouseButton.LeftButton)
        QTest.qWait(50)
        assert window.tabs.currentWidget() is p and p.detail_table.rowCount() == 300

        def settle_history():
            for _ in range(500):
                app.processEvents()
                if not p.history_pending:
                    return
                QTest.qWait(10)
            raise AssertionError("History worker did not finish")

        p.refresh()
        settle_history()
        assert len(p.timeline) == 600
        report["charts"] = {}
        for mode in ("light", "dark", "system"):
            manager().set_mode(mode, persist=False)
            QTest.qWait(20)
            for kind in CHARTS:
                # All formats in both palettes; system mode exercises the same data
                # and its OS-resolved theme with a representative chart.
                if mode == "system" and kind != "horizontal":
                    continue
                p.clear_focus()
                p.axis.setCurrentIndex(p.axis.findData("machine_type"))
                p.metric_choice.setCurrentIndex(p.metric_choice.findData("average"))
                p.operation.setCurrentIndex(
                    p.operation.findData("count" if kind in ("pie", "donut") else "mean")
                )
                if kind == "radar":
                    p.focus_machine(f.machines[0].uid)
                p.chart_type.setCurrentIndex(p.chart_type.findData(kind))
                p.refresh()
                settle_history()
                p.refresh()
                app.processEvents()
                assert (
                    p.chart_type.model().item(p.chart_type.findData(kind)).isEnabled()
                ), f"Chart disabled: {kind} {p.notice.text()}"
                assert p.save_image(output / f"{mode}-{kind}.png")
                if kind == "radar":
                    widget = p.radar
                    widget.grab()
                    shape, a = widget.hit_targets[-1]
                    point = shape.center()
                    expected = {a["uid"]}
                    scroll = p.radar_scroll
                    assert sum(a["score"] is not None for a in widget.axes) == 5
                elif kind == "cross":
                    widget = p.cross_table
                    item = next(
                        widget.item(i, j)
                        for i in range(widget.rowCount())
                        for j in range(1, widget.columnCount())
                        if widget.item(i, j) is not None
                        and widget.item(i, j).data(Qt.ItemDataRole.UserRole)
                    )
                    expected = set(item.data(Qt.ItemDataRole.UserRole))
                    widget.scrollToItem(item)
                    app.processEvents()
                    point = widget.visualItemRect(item).center()
                    QTest.mouseClick(widget.viewport(), Qt.MouseButton.LeftButton, pos=point)
                    scroll = None
                else:
                    widget = p.canvas
                    widget.grab()
                    shape, d = widget.hits[-1]
                    expected = set(d["uids"])
                    rect = shape.boundingRect()
                    point = next(
                        QPointF(x, y)
                        for x in range(int(rect.left()), int(rect.right()) + 1, 2)
                        for y in range(int(rect.top()), int(rect.bottom()) + 1, 2)
                        if shape.contains(QPointF(x, y))
                    )
                    assert widget.tooltip_at(point)
                    scroll = p.chart_scroll
                if kind != "cross":
                    scroll.ensureVisible(round(point.x()), round(point.y()), 12, 12)
                    app.processEvents()
                    assert (
                        scroll.viewport()
                        .rect()
                        .contains(widget.mapTo(scroll.viewport(), point.toPoint()))
                    ), f"Hit is outside viewport: {kind}"
                    viewport = scroll.viewport().grab().toImage()
                    colours = {
                        viewport.pixelColor(x, y).name()
                        for x in range(0, viewport.width(), 4)
                        for y in range(0, viewport.height(), 4)
                    }
                    assert len(colours) > 4, f"Chart not visible: {kind}"
                    QTest.mouseClick(widget, Qt.MouseButton.LeftButton, pos=point.toPoint())
                app.processEvents()
                assert (
                    window.tabs.currentWidget() is f and f.analysis_highlight_uids == expected
                ), f"Click did not locate {kind}"
                assert (
                    len(f.visible_nodes()) == 300
                    and f._zoom == 125
                    and f.floor_search.text() == "CHECK29"
                )
                assert before == [
                    (m.uid, m.x, m.y, m.width, m.height, m.product, m.operation_method)
                    for m in f.machines
                ]
                assert node_ids == {uid: id(n) for uid, n in f.nodes.items()}
                f.clear_analysis_highlight()
                assert f.analysis_highlight_uids is None
                window.navigation.buttons["analysis"].click()
                app.processEvents()
                report["charts"][mode + "-" + kind] = dict(ok=True, target_count=len(expected))
            window.grab().save(str(output / f"{mode}-workspace.png"))
        # Detail uses the current effective record, with responsive editing and radar.
        window.tabs.setCurrentWidget(f)
        app.processEvents()
        d = MachineDetailDialog(f, f.machines[0])
        d.show()
        QTest.qWait(40)
        assert (
            d.kpis["sales"].value.text() == "7,800 円"
            and d.kpis["average"].value.text() == "2,600 円"
        )
        assert d.values["source_key"].text() == "L_0" and len(d.radar.axes) == 5
        assert not d.kpis["ratio"].visibleRegion().isEmpty() and all(
            size > 0 for size in d.splitter.sizes()
        ), "KPI pane is not visible"
        assert (
            d.width() <= d.screen().availableGeometry().width()
            and d.height() <= d.screen().availableGeometry().height()
        )
        assert d.grab().save(str(output / "machine-detail.png"))
        from PySide6.QtWidgets import QScrollArea

        ancestor = d.radar.parentWidget()
        while ancestor is not None and ancestor is not d:
            if isinstance(ancestor, QScrollArea):
                ancestor.ensureWidgetVisible(d.radar, 15, 15)
                app.processEvents()
            ancestor = ancestor.parentWidget()
        assert not d.radar.visibleRegion().isEmpty(), "Detail radar cannot be reached by scrolling"
        d.grab().save(str(output / "machine-detail-radar.png"))
        d.analysis_scope.setCurrentIndex(1)
        d.analysis_button.click()
        QTest.qWait(30)
        assert window.tabs.currentWidget() is p and len(p.records) == 100
        p.clear_focus()
        p.chart_type.setCurrentIndex(p.chart_type.findData("horizontal"))
        p.refresh()
        assert p.save_csv(output / "分析.csv", table="詳細") and p.save_excel(output / "分析.xlsx")
        book = load_workbook(output / "分析.xlsx", read_only=True)
        assert book["詳細"].max_row == 301
        book.close()
        window.tabs.setCurrentWidget(f)
        f.scene.clearSelection()
        for m in f.machines[:6]:
            f.nodes[m.uid].setSelected(True)
        f.set_print_options(FloorPrintOptions(mode="list", selection_only=True, paper="A4"))

        def pdf(mode):
            manager().set_mode(mode, persist=False)
            path = output / f"{mode}.pdf"
            f.paint_floor(floor_printer(f.print_options, path))
            document = QPdfDocument()
            assert document.load(str(path)) == QPdfDocument.Error.None_
            image = document.render(0, QSize(842, 595))
            document.close()
            text = normalized_pdf_text(
                "".join(p["text"] for p in require_searchable_pdf(path)["pages"])
            )
            return image, text

        light, text = pdf("light")
        dark, dark_text = pdf("dark")
        assert light == dark and text == dark_text
        assert all(text.count(f"CHECK{i:03}") == 1 for i in range(6))
        f.autosave()
        state = f.state()
        second = Window(layout_storage=f.storage)
        assert (
            second.floor.analytics_ui == state["analytics_ui"] and len(second.floor.machines) == 300
        )
        second.dirty = False
        second.floor.timer.stop()
        second.floor.day_timer.stop()
        second.close()
        report.update(
            ok=True,
            machines=300,
            history_points=600,
            pdf_theme_equal=True,
            csv_excel=True,
            detail_navigation=True,
            save_restore=True,
            physical_coordinates_unchanged=True,
            dpr=window.devicePixelRatioF(),
        )
    except Exception:
        report["error"] = traceback.format_exc()
    finally:
        if window:
            window.dirty = False
            window.floor.timer.stop()
            window.floor.day_timer.stop()
            window.analysis_page.timer.stop()
            if window.floor._fullscreen_host:
                window.floor._fullscreen_host.close()
            window.close()
        if runtime:
            runtime.cleanup()
        (output / "report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(run(sys.argv[1]))
