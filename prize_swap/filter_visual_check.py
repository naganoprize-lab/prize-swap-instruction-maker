"""Native/frozen proof of current-report ranks, light rendering and readable maps."""

import json, os, sys, traceback
from pathlib import Path
from tempfile import TemporaryDirectory
from datetime import date


def run(output):
    if sys.platform != "win32":
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from PySide6.QtCore import QDate
    from PySide6.QtTest import QTest
    from openpyxl import Workbook
    from .ui import Window
    from .floor_data import Machine
    from .core import Product
    from .sales_analysis import HEADERS, load_analysis, apply_snapshot
    from .theme import manager, analysis_color, contrast_text
    from .fonts import initialize_fonts
    from .floor_status import COLOR_FILTERS
    from .sales_evaluation import evaluation
    from .floor_visual_check import inspect_card

    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    report = dict(ok=False, physical_printer_tested=False)
    w = None
    try:
        app = QApplication.instance() or QApplication([])
        initialize_fonts()
        report.update(
            platform=app.platformName(), scale_requested=os.environ.get("QT_SCALE_FACTOR", "1")
        )
        with TemporaryDirectory() as directory:
            tmp = Path(directory)
            w = Window(layout_storage=tmp / "floor.json")
            f = w.floor
            w.day.setDate(QDate(2026, 11, 9))
            f.follow_today = False
            f.reference_date.setDate(QDate(2026, 11, 9))
            book = Workbook()
            sheet = book.active
            sheet.title = "景品運営分析表"
            for c, label in HEADERS.items():
                sheet.cell(1, c + 1, label)
            for i in range(304):
                sale = 16100 if i < 2 else 20000 if i < 32 else 1000
                number = "20111-02" if i == 0 else f"{23000+i}-02"
                sheet.append(
                    [
                        None,
                        number,
                        "UFOキャッチャー9 third",
                        None,
                        "売上登録用",
                        sale,
                        1,
                        sale,
                        sale * 0.4,
                        0.6,
                        0.05,
                        11,
                        sale / 11,
                        None,
                        270,
                        sale * 0.6,
                    ]
                )
            sheet.append(["集計期間：2026/11/06 ～ 2026/11/06"])
            source = tmp / "report.xlsx"
            book.save(source)
            book.close()
            f.machines = [
                Machine(
                    number=("20111右" if i == 0 else f"{23000+i}右") if i < 6 else "",
                    source_key=f"R_{i}",
                    kind="UFOキャッチャー9 third",
                    product=f"景品{i} 270",
                    ratio=39 if i % 2 else 85,
                    sales=i * 100,
                    installed_on=None if i % 3 == 0 else "2026-10-01",
                    x=i % 20 * 240,
                    y=i // 20 * 170,
                    width=215,
                    height=145,
                )
                for i in range(300)
            ]
            f.analysis_state = load_analysis(source)
            apply_snapshot(f.machines, f.analysis_state)
            f.rebuild()
            w.refresh()
            w.resize(
                min(1920, w.screen().availableGeometry().width()),
                min(1080, w.screen().availableGeometry().height()),
            )
            w.show()
            w.navigation.activate("floor")
            QTest.qWait(80)
            target = f.machines[0]
            ids = {m.uid for m in f.machines[:24]}
            geometry = [(m.uid, m.x, m.y, m.width, m.height) for m in f.machines]
            f.selection_state.set(ids)
            nodes = f.nodes.copy()
            f.set_color_filter("sales_metric")
            result = evaluation(target, f.analysis_settings, f.analytics.sales_bounds)
            assert (result["count"], result["rank"], result["color"]) == (
                304,
                31,
                "#FFE16A",
            ), result
            report["sales_example"] = result
            report["matched_booths"] = sum(m.analysis is not None for m in f.machines)
            assert report["matched_booths"] == 6
            for theme in ("light", "dark"):
                manager().set_mode(theme, persist=False)
                for mode in ("normal", "light"):
                    f.render_mode.setCurrentIndex(f.render_mode.findData(mode))
                    for key in COLOR_FILTERS:
                        f.set_color_filter(key)
                        f.view.grab()
                    f.set_color_filter("sales_metric")
                    f.set_zoom(100, save=False)
                    f.view.centerOn(f.nodes[target.uid])
                    QTest.qWait(20)
                    color = analysis_color("#FFE16A")
                    inspect_card(f, target.uid, color, contrast_text(color))
                    assert w.grab().save(str(output / f"floor-{theme}-{mode}.png"))
                    assert all(f.nodes[k] is node for k, node in nodes.items())
                    assert f.selection_state.selected_machine_keys == ids
            f.open_details(target.uid)
            QTest.qWait(60)
            p = f.detail_controller.panel
            assert p.kpis["sales"].value.text().replace(" ", "") == "16,100円"
            assert p.kpis["sales"].toolTip()
            assert w.grab().save(str(output / "detail.png"))
            f.detail_controller.dock.close()
            w.navigation.activate("analysis")
            QTest.qWait(50)
            assert w.grab().save(str(output / "analysis.png"))
            w.products = [
                Product(date(2026, 11, 9), "入替予定", machines=[target.number], included=True)
            ]
            w.refresh()
            w.navigation.activate("instructions")
            w.instruction_tabs.setCurrentWidget(w.instruction_map)
            QTest.qWait(60)
            page = w.instruction_map
            assert len(page.items) == 1 and len(page.context_items) == 299
            page.fit()
            QTest.qWait(20)
            scale = page.view.transform().m11()
            for enabled in (False, True):
                page.context.setChecked(enabled)
                QTest.qWait(20)
                assert page.view.transform().m11() == scale
            for theme in ("light", "dark"):
                manager().set_mode(theme, persist=False)
                QTest.qWait(20)
                assert w.grab().save(str(output / f"map-{theme}.png"))
            assert [(m.uid, m.x, m.y, m.width, m.height) for m in f.machines] == geometry
            report.update(
                light_and_dark=True,
                all_filters=True,
                normal_and_light=True,
                reused_300_items=True,
                selection_preserved=True,
                target_only_fit=True,
                opaque_neighbors=True,
                geometry_preserved=True,
            )
            w.dirty = False
            w.instruction_save_timer.stop()
            f.timer.stop()
            f.day_timer.stop()
            w.preflight.timer.stop()
            w.close()
            w = None
        report["ok"] = True
    except Exception:
        report["error"] = traceback.format_exc()
    finally:
        if w is not None:
            w.dirty = False
            w.close()
        (output / "report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(run(sys.argv[1] if len(sys.argv) > 1 else "filter-visual"))
