"""Qt geometry probe, using only synthetic data and an isolated temporary store.

Run once per QT_SCALE_FACTOR. Offscreen results do not replace native Windows
DPI, font and printer checks. May also be run interactively on Windows.
"""

import json
import os
import sys
from pathlib import Path
from dataclasses import asdict

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def run(output):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QDialog, QDialogButtonBox, QLineEdit, QTableWidget
    from prize_swap.ui import Window
    from prize_swap.floor_data import Machine

    app = QApplication.instance() or QApplication([])
    from prize_swap.fonts import initialize_fonts

    initialize_fonts()
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    w = Window(layout_storage=output / "unused-floor.json")
    f = w.floor
    f.machines = [
        Machine(
            number=f"{20000+i}右",
            source_key=f"R_{i}",
            product="長い景品名・日本語の表示確認 270",
            sales=1000 + i,
            x=(i % 20) * 220,
            y=(i // 20) * 180,
        )
        for i in range(40)
    ]
    f.rebuild()
    from openpyxl import Workbook
    from prize_swap.sales_analysis import HEADERS

    book = Workbook()
    sheet = book.active
    sheet.title = "景品運営分析表"
    for column, label in HEADERS.items():
        sheet.cell(1, column + 1, label)
    for i in range(300):
        sheet.append(
            [
                None,
                f"{20000+i}-02",
                "UFO9",
                None,
                "売上登録用",
                1000 + i,
                1,
                1000 + i,
                500 + i,
                500 / (1000 + i),
                0.1,
                1,
                1000 + i,
                None,
                500,
                500,
            ]
        )
    report_path = output / "synthetic.xlsx"
    book.save(report_path)
    book.close()
    f.import_analysis_path(report_path)
    f.selection_state.set({m.uid for m in f.machines[:3]})
    initial = [asdict(m) for m in f.machines]
    area = w.screen().availableGeometry()
    w.resize(area.width() - 24, area.height() - 60)
    w.show()
    result = dict(
        platform=app.platformName(),
        scale=os.environ.get("QT_SCALE_FACTOR", "1"),
        screen=[area.width(), area.height()],
        window=[w.width(), w.height()],
        checks=[],
    )

    def inside(parent, child):
        return parent.rect().contains(
            child.mapTo(parent, child.rect().topLeft())
        ) and parent.rect().contains(child.mapTo(parent, child.rect().bottomRight()))

    for theme in ("light", "dark"):
        w.themes.set_mode(theme, persist=False)
        w.navigation.activate("floor")
        QTest.qWait(80)
        assert w.width() <= area.width() and w.height() <= area.height(), (w.size(), area)
        for row in f.toolbar_rows:
            if row.isVisible():
                controls = []
                for index in range(row.layout().count()):
                    control = row.layout().itemAt(index).widget()
                    if control is not None and control.isVisible():
                        assert inside(row, control), ("toolbar", control.geometry(), row.size())
                        assert all(
                            not control.geometry().intersects(other.geometry())
                            for other in controls
                        ), "toolbar overlap"
                        controls.append(control)
        f.set_zoom(75)
        zoom = f.view.transform().m11()
        f.view.centerOn(f.scene.sceneRect().center())
        center = f.view.mapToScene(f.view.viewport().rect().center())
        assert f.open_details(f.machines[0].uid)
        QTest.qWait(120)
        c = f.detail_controller
        p = c.panel
        buttons = p.findChild(QDialogButtonBox)
        assert inside(c.host, buttons), ("detail footer", buttons.geometry(), c.host.size())
        assert f.view.viewport().width() >= 200
        assert p.overview_scroll.verticalScrollBar().maximum() > 0
        w.grab().save(str(output / f"floor-top-{theme}.png"))
        p.overview_scroll.verticalScrollBar().setValue(
            p.overview_scroll.verticalScrollBar().maximum()
        )
        QTest.qWait(20)
        assert p.radar.height() >= 300 and p.radar.width() >= 300
        for position in ("left", "right"):
            c.set_position(position)
            QTest.qWait(40)
            assert inside(c.host, buttons)
        w.grab().save(str(output / f"floor-{theme}.png"))
        c.dock.close()
        QTest.qWait(100)
        assert f.view.transform().m11() == zoom
        after_center = f.view.mapToScene(f.view.viewport().rect().center())
        assert abs(center.x() - after_center.x()) < 3 and abs(center.y() - after_center.y()) < 3, (
            center,
            after_center,
            f._view_center,
        )
        assert len(f.selection_state.selected_machine_keys) == 3
        assert initial == [asdict(m) for m in f.machines]
        # Exercise the actual report builder; keep its modal loop out of automation.
        original_exec = QDialog.exec

        def inspect(dialog):
            dialog.show()
            QTest.qWait(30)
            for size in (
                (area.width() - 60, area.height() - 80),
                (min(800, area.width() - 60), min(480, area.height() - 80)),
            ):
                dialog.resize(*size)
                QTest.qWait(30)
                close = dialog.findChild(QDialogButtonBox)
                search = dialog.findChild(QLineEdit)
                assert inside(dialog, close) and inside(dialog, search)
                for table in dialog.findChildren(QTableWidget):
                    assert table.horizontalHeader().sectionResizeMode(0).name == "Interactive"
            dialog.grab().save(str(output / f"import-{theme}.png"))
            dialog.hide()
            return QDialog.DialogCode.Rejected

        QDialog.exec = inspect
        try:
            f.show_analysis_report()
        finally:
            QDialog.exec = original_exec
        for key, page in w.pages.items():
            w.tabs.setCurrentWidget(page)
            QTest.qWait(25)
            assert w.width() <= area.width() and w.height() <= area.height(), (key, w.size(), area)
            result["checks"].append(theme + ":" + key)
    result["ok"] = True
    (output / "report.json").write_text(
        json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    w.dirty = False
    w.instruction_save_timer.stop()
    f.timer.stop()
    f.day_timer.stop()
    w.close()
    return result


if __name__ == "__main__":
    print(json.dumps(run(sys.argv[1]), ensure_ascii=False))
