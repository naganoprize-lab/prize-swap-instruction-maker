"""Real widget paint/input check, also runnable inside the packaged Windows EXE."""

from dataclasses import asdict
from datetime import date, timedelta
import json
import math
import os
from pathlib import Path
import sys
import traceback


def inspect_card(floor, uid, expected, foreground=None):
    from PySide6.QtGui import QColor
    from PySide6.QtWidgets import QApplication

    node = floor.nodes[uid]
    floor.view.ensureVisible(node, 10, 10)
    QApplication.processEvents()
    viewport = floor.view.viewport()
    image = viewport.grab().toImage()
    dpr = image.devicePixelRatio()
    rect = (
        floor.view.mapFromScene(node.sceneBoundingRect())
        .boundingRect()
        .intersected(viewport.rect())
    )
    assert floor.isVisible() and floor.view.isVisible() and rect.width() > 40 and rect.height() > 30
    expected = QColor(expected)
    foreground = QColor(foreground) if foreground else None
    background = ink = 0
    # Check pixels from the actual viewport, not scene.render(): scene.render()
    # succeeds even when the containing widget is explicitly hidden.
    for y in range(math.ceil((rect.top() + 5) * dpr), math.floor((rect.bottom() - 5) * dpr), 2):
        for x in range(math.ceil((rect.left() + 5) * dpr), math.floor((rect.right() - 5) * dpr), 2):
            c = image.pixelColor(x, y)
            if max(abs(a - b) for a, b in zip(c.getRgb()[:3], expected.getRgb()[:3])) <= 3:
                background += 1
            if (
                foreground
                and max(abs(a - b) for a, b in zip(c.getRgb()[:3], foreground.getRgb()[:3])) < 35
            ) or (foreground is None and max(c.red(), c.green(), c.blue()) < 120):
                ink += 1
    assert background > 100, f"Card background absent: {background}"
    assert ink > 15, f"Card text absent: {ink}"
    return dict(
        background_pixels=background,
        text_pixels=ink,
        dpr=dpr,
        viewport=[viewport.width(), viewport.height()],
    )


def run(output):
    if sys.platform != "win32":
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QDate, QPointF, Qt, QTimer
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication
    from .core import Product
    from .floor_data import Machine
    from .floor_details import MachineDetailDialog
    from .fonts import validate_print_fonts
    from .ui import Window

    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    report = dict(ok=False, physical_printer_tested=False)
    window = None
    try:
        app = QApplication.instance() or QApplication([])
        report["fonts"] = validate_print_fonts()
        report["platform"] = app.platformName()
        report["scale_requested"] = os.environ.get("QT_SCALE_FACTOR", "1")
        day = date(2026, 11, 7)
        window = Window(layout_storage=output / "unused-autosave.json")
        window.themes.set_mode("light", persist=False)
        floor = window.floor
        window.products = [Product(day, "入替予定商品", machines=["20000右"])]
        window.day.setDate(QDate(day))
        floor.reference_date.setDate(QDate(day))
        floor.follow_today = False
        floor.machines = [
            Machine(
                number=f"{20000+i}右",
                product=f"マシン描画検証 商品{i}",
                po_rate=0.05,
                unit_price=1200,
                cost_rate=0.6,
                kind="UFO9 third",
                ratio=[49.9, 200, 49.9, None][i],
                x=40 + (i % 2) * 300,
                y=40 + (i // 2) * 220,
                width=220,
                height=160,
                installed_on=(day - timedelta(days=10 - i)).isoformat() if i < 3 else None,
            )
            for i in range(4)
        ]
        floor.rebuild()
        window.show()
        window.tabs.setCurrentWidget(floor)
        app.processEvents()
        floor.set_zoom(100)
        floor.nodes[floor.machines[0].uid].setSelected(True)
        before = [asdict(m) for m in floor.machines]
        scene = floor.scene
        uid = floor.machines[0].uid
        floor.toggle_fullscreen()
        app.processEvents()
        QTest.qWait(60)
        host = floor._fullscreen_host
        assert host.isFullScreen() and host.width() > 100 and host.height() > 100
        assert floor.isVisibleTo(host) and floor.view.scene() is scene
        for b in host.buttons.values():
            assert b.isVisibleTo(host) and b.width() > 0 and b.height() > 0
        report["ratio"] = inspect_card(floor, uid, "#F4A6A6")
        assert report["ratio"]["dpr"] >= float(report["scale_requested"]) * 0.9
        assert host.grab().save(str(output / "fullscreen-ratio.png"))
        from .analysis_settings import type_style

        for mode, colour in (
            ("planned", "#A9D7FF"),
            ("age", "#C5A5ED"),
            ("po_rate", "#94D8A0"),
            ("cost_rate", "#94D8A0"),
            ("machine_type", type_style("UFO9 third")["color"]),
            ("assessment", "#F4A6A6"),
            ("ratio", "#F4A6A6"),
        ):
            host.filter_combo.setCurrentIndex(host.filter_combo.findData(mode))
            app.processEvents()
            report[mode] = inspect_card(floor, uid, colour)
            assert [asdict(m) for m in floor.machines] == before
            assert host.grab().save(str(output / f"fullscreen-{mode}.png"))
        node_ids = {k: id(n) for k, n in floor.nodes.items()}
        QTest.keyClicks(host.search, "20000")
        app.processEvents()
        assert len(floor._match_uids) == 1 and len(floor.visible_nodes()) == 4
        host.search.setText("NO_MATCH")
        app.processEvents()
        assert not floor._match_uids and len(floor.visible_nodes()) == 4
        host.search.clear()
        app.processEvents()
        assert {k: id(n) for k, n in floor.nodes.items()} == node_ids
        report["search_keeps_scene"] = True
        scale = floor.view.transform().m11()
        QTest.mouseClick(host.buttons["zoom_in"], Qt.MouseButton.LeftButton)
        assert floor.view.transform().m11() > scale
        QTest.mouseClick(host.buttons["zoom_out"], Qt.MouseButton.LeftButton)
        QTest.mouseClick(host.buttons["fit"], Qt.MouseButton.LeftButton)
        app.processEvents()
        assert 0 < floor.view.transform().m11() < 100
        floor.set_zoom(100)
        floor.view.ensureVisible(floor.nodes[uid])
        app.processEvents()
        opened = []
        detail_fits = []

        def close_detail():
            dialog = next(
                (
                    d
                    for d in app.topLevelWidgets()
                    if isinstance(d, MachineDetailDialog) and d.isVisible()
                ),
                None,
            )
            if dialog is not None:
                available = dialog.screen().availableGeometry()
                detail_fits.append(
                    dialog.width() <= available.width() and dialog.height() <= available.height()
                )
                dialog.grab().save(str(output / "details.png"))
                opened.append(dialog.name.toPlainText())
                dialog.reject()

        timer = QTimer()
        timer.timeout.connect(close_detail)
        timer.start(10)
        point = floor.view.mapFromScene(floor.nodes[uid].sceneBoundingRect().center())
        QTest.mouseClick(floor.view.viewport(), Qt.MouseButton.LeftButton, pos=point)
        app.processEvents()
        timer.stop()
        assert len(opened) == 1, "Click did not open machine details"
        assert all(detail_fits), "Details do not fit the DPI-scaled screen"
        QTest.mouseClick(host.edit, Qt.MouseButton.LeftButton)
        app.processEvents()
        assert floor.presentation["edit"]
        node = floor.nodes[uid]
        node.setSelected(True)
        floor.view.centerOn(node)
        app.processEvents()
        start = floor.view.mapFromScene(node.pos() + QPointF(45, 35))
        delta = floor.view.mapFromScene(QPointF(60, 40)) - floor.view.mapFromScene(QPointF())
        QTest.mousePress(floor.view.viewport(), Qt.MouseButton.LeftButton, pos=start)
        QTest.mouseMove(floor.view.viewport(), start + delta, 40)
        QTest.mouseRelease(floor.view.viewport(), Qt.MouseButton.LeftButton, pos=start + delta)
        app.processEvents()
        moved = next(m for m in floor.machines if m.uid == uid)
        assert (moved.x, moved.y) == (
            before[0]["x"] + 60,
            before[0]["y"] + 40,
        ), "Fullscreen drag failed"
        floor.history.undo()
        app.processEvents()
        assert [asdict(m) for m in floor.machines] == before
        floor.nodes[uid].setSelected(True)
        settings = dict(floor.presentation)
        QTest.keyClick(host, Qt.Key.Key_Escape)
        app.processEvents()
        QTest.qWait(30)
        assert (
            floor._fullscreen_host is None
            and window.tabs.currentWidget() is floor
            and floor.isVisible()
        )
        assert floor.presentation == settings and [m.uid for m in floor.selected()] == [uid]
        assert floor.view.scene() is scene and [asdict(m) for m in floor.machines] == before
        report["restored"] = inspect_card(floor, uid, "#F4A6A6")
        assert window.grab().save(str(output / "restored.png"))
        report.update(
            ok=True, details_opened=len(opened), drag_and_undo=True, selection_preserved=True
        )
    except Exception:
        report["error"] = traceback.format_exc()
    finally:
        if window:
            if window.floor._fullscreen_host:
                window.floor._fullscreen_host.close()
            window.floor.timer.stop()
            window.floor.day_timer.stop()
            window.dirty = False
            window.close()
        (output / "report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(run(sys.argv[1]))
