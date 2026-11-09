"""Native and frozen-EXE integration proof using generated, non-private daily files."""

import json, os, sys, time, traceback
from pathlib import Path
from tempfile import TemporaryDirectory
from dataclasses import asdict
from copy import deepcopy
from datetime import datetime


def fixture(path, day, offset=0, invalid=False):
    from openpyxl import Workbook
    from .sales_analysis import HEADERS

    book = Workbook()
    s = book.active
    s.title = "景品運営分析表"
    for col, label in HEADERS.items():
        s.cell(1, col + 1, label)
    for j, label in enumerate(("プレイ回数", "景品獲得数", "投入金額"), 17):
        s.cell(1, j, label)
    for i in range(304):
        sale = (i + 1) * 100 + offset
        cost = 2700
        s.append(
            [
                None,
                f"{22000+i}-02",
                "UFOキャッチャー9 third",
                None,
                "売上登録用",
                sale,
                1,
                sale,
                sale - cost,
                cost / sale,
                0.05,
                10,
                sale / 10,
                None,
                270,
                cost,
                200,
                10,
                sale,
            ]
        )
    s["B310"] = "期間(自)"
    s["C310"] = day
    s["B311"] = "期間(至)"
    s["C311"] = "2026/11/06" if invalid else day
    l = book.create_sheet("投入中レイアウト")
    refs = book.create_sheet("管理番号参考値")
    for i in range(300):
        row = 1 + (i // 20) * 6
        col = 1 + (i % 20) * 4
        l.cell(row, col, f"R_{i}")
        l.cell(row, col + 1, f"景品{i} 270")
        l.cell(row + 1, col, "売上/ｱﾍﾞﾚｰｼﾞ")
        l.cell(row + 1, col + 1, (i + 1) * 100 + offset)
        l.cell(row + 1, col + 2, (i + 1) * 100 + offset)
        l.cell(row + 2, col, "PO率/ｱﾍﾞ比")
        l.cell(row + 2, col + 1, 0.05)
        c = l.cell(row + 2, col + 2, (i % 5 + 1) * 0.4)
        c.number_format = "0.0%"
        l.cell(row + 3, col, "設置日/設定2")
        l.cell(row + 3, col + 1, datetime(2026, 11, 1))
        l.cell(row + 4, col, "運営方法")
        l.cell(row + 4, col + 1, "橋渡し")
        refs.append([f"R_{i}", f"{22000+i}右"])
    book.save(path)
    book.close()


def run(output):
    if sys.platform != "win32":
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from PySide6.QtCore import QTimer
    from PySide6.QtTest import QTest
    from .ui import Window
    from .fonts import initialize_fonts
    from .floor_status import effective_stat, COLOR_FILTERS
    from .theme import manager
    from .daily_reference import digest

    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    report = dict(ok=False, physical_printer_tested=False)
    w = None
    qt_errors = []
    original_hook = sys.excepthook
    sys.excepthook = lambda kind, value, tb: qt_errors.append(
        "".join(traceback.format_exception(kind, value, tb))
    )

    def wait(predicate, seconds=60):
        stop = time.monotonic() + seconds
        while not predicate() and time.monotonic() < stop:
            QApplication.processEvents()
            QTest.qWait(10)
        assert predicate(), "Background work timed out"

    try:
        app = QApplication.instance() or QApplication([])
        initialize_fonts()
        report["platform"] = app.platformName()
        report["scale_requested"] = os.getenv("QT_SCALE_FACTOR", "1")
        with TemporaryDirectory() as folder:
            root = Path(folder)
            refs = root / "daily"
            refs.mkdir()
            for name, day, offset, bad in [
                ("one.xlsx", "2026/11/06", 0, False),
                ("two.xlsm", "2026/11/07", 500, False),
                ("bad.xlsx", "2026/11/08", 500, True),
            ]:
                fixture(refs / name, day, offset, bad)
            before = {p.name: digest(p.read_bytes().hex()) for p in refs.iterdir()}
            w = Window(layout_storage=root / "floor.json")
            f = w.floor
            c = f.temporal
            w.resize(
                min(1920, w.screen().availableGeometry().width()),
                min(1080, w.screen().availableGeometry().height()),
            )
            w.show()
            w.navigation.activate("floor")
            t = time.perf_counter()
            c.set_folder(refs)
            wait(lambda: not c._busy and not c.aggregate_busy)
            report["initial_import_seconds"] = time.perf_counter() - t
            assert len(f.machines) == 300 and c.frame.end == "2026-11-07"
            assert len(c.library.usable(refs, full=False)[0]) == 2
            report["uncertain_files"] = sum(not d["eligible"] for d in c.docs)
            assert report["uncertain_files"] == 1
            original = [asdict(m) for m in c.physical_machines]
            nodes = dict(f.nodes)
            keys = {m.uid for m in f.machines[:24]}
            f.selection_state.set(keys)
            f.set_zoom(50, save=False)
            camera = f.view.transform()
            beats = []
            heart = QTimer(w)
            heart.setInterval(10)
            heart.timeout.connect(lambda: beats.append(time.perf_counter()))
            heart.start()
            c.request_apply("custom", "2026-11-06", "2026-11-08")
            wait(lambda: not c.aggregate_busy)
            heart.stop()
            assert c.frame.coverage["acquired"] == 2 and c.frame.coverage["missing"] == 1
            assert effective_stat(f.machines[0], "sales") == 700
            assert (
                f.state()["machines"] == original
                and f.selection_state.selected_machine_keys == keys
            )
            assert f.view.transform() == camera and all(
                f.nodes[k] is node for k, node in nodes.items()
            )
            report["ui_heartbeat_during_aggregation"] = len(beats)
            assert beats
            w.timeline.toggle.setChecked(True)
            w.timeline.start.setDate(w.period_controls["floor"].start.date())
            w.timeline.set_range()
            wait(lambda: not c.aggregate_busy)
            assert len(c.frames) == 2
            for theme in ("light", "dark"):
                manager().set_mode(theme, persist=False)
                for mode in ("normal", "light"):
                    f.render_mode.setCurrentIndex(f.render_mode.findData(mode))
                    for key in COLOR_FILTERS:
                        f.set_color_filter(key)
                        f.view.grab()
                f.set_color_filter("sales_metric")
                f.render_mode.setCurrentIndex(f.render_mode.findData("normal"))
                w.grab().save(str(output / f"floor-{theme}.png"))
            c.seek(1)
            wait(lambda: not c.aggregate_busy)
            f.open_details(f.machines[0].uid)
            QTest.qWait(80)
            assert not c.playing and f.detail_controller.panel is not None
            w.grab().save(str(output / "detail-current-frame.png"))
            c.settings["skip_missing"] = False
            c.prepare_playback("2026-11-06", "2026-11-08")
            c.seek(2)
            wait(lambda: not c.aggregate_busy)
            assert all(effective_stat(m, "sales") is None for m in f.machines)
            c.request_apply("custom", "2026-11-06", "2026-11-08")
            wait(lambda: not c.aggregate_busy)
            w.navigation.activate("analysis")
            w.analysis_page.workspace_tabs.setCurrentWidget(w.temporal_analysis)
            w.temporal_analysis.refresh()
            wait(lambda: w.temporal_analysis.data is not None)
            assert len(w.temporal_analysis.charts) == 7
            assert w.temporal_analysis.data["monthly"][0]["sales"] == sum(
                2 * (i + 1) * 100 + 500 for i in range(24)
            )
            w.grab().save(str(output / "selected-daily-trends.png"))
            c.to_live()
            assert [asdict(m) for m in f.machines] == original
            assert before == {p.name: digest(p.read_bytes().hex()) for p in refs.iterdir()}
            assert not qt_errors, qt_errors
            report.update(
                ok=True,
                machines=300,
                selected=24,
                source_files_unchanged=True,
                master_unchanged=True,
                all_graphs=7,
                builds=c.builds,
            )
            w.dirty = False
            w.instruction_save_timer.stop()
            f.timer.stop()
            w.close()
            wait(lambda: not c._jobs and not w.temporal_analysis._jobs)
    except Exception:
        report["error"] = traceback.format_exc()
    finally:
        if w:
            w.dirty = False
            w.floor.temporal.shutdown()
            w.close()
        sys.excepthook = original_hook
        if qt_errors:
            report.update(ok=False, qt_errors=qt_errors)
        (output / "report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    print(json.dumps({k: v for k, v in report.items() if k != "error"}, ensure_ascii=False))
    if report.get("error"):
        print(report["error"])
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(run(sys.argv[1]))
