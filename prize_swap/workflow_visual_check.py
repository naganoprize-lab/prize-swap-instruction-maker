"""Native and packaged workflow checks using synthetic data and real Qt widgets."""

import os, sys, json, traceback
from pathlib import Path
from tempfile import TemporaryDirectory
from datetime import date


def run(output):
    if sys.platform != "win32":
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import Qt, QDate, QPoint, QPointF, QEvent
    from PySide6.QtGui import QWheelEvent
    from PySide6.QtWidgets import QApplication, QDialog, QFileDialog, QDialogButtonBox
    from PySide6.QtTest import QTest
    from PySide6.QtPrintSupport import QPrintDialog
    from openpyxl import Workbook
    from .ui import Window
    from .floor_data import Machine
    from .core import Product, load_session
    from .costing import cost_profile
    from .floor_status import effective_stat
    from .theme import manager
    from .fonts import validate_print_fonts
    from .excel_import_ui import AutoImportDialog
    from .instruction_preview import InstructionPreview
    from .pdf_validation import require_searchable_pdf, normalized_pdf_text
    from .work_set_ui import WorkSetDialog

    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    report = dict(ok=False, physical_printer_tested=False)
    w = None
    runtime = TemporaryDirectory(prefix=".workflow-", dir=output)
    original_exec = QPrintDialog.exec
    original_save = None
    try:
        app = QApplication.instance() or QApplication([])
        validate_print_fonts()
        report.update(
            platform=app.platformName(), scale_requested=os.environ.get("QT_SCALE_FACTOR", "1")
        )
        w = Window(layout_storage=Path(runtime.name) / "floor.json")
        f = w.floor
        w.day.setDate(QDate(2026, 11, 8))
        f.follow_today = False
        f.reference_date.setDate(QDate(2026, 11, 8))
        f.machines = [
            Machine(
                number=f"{23000+i}右",
                source_key=f"R_{i}",
                product=f"景品{i} 270",
                sales=6000 + i * 100,
                dispensed=11,
                cost_rate=0.9,
                po_rate=0.2,
                unit_price=999,
                ratio=40 if i % 2 else 120,
                installed_on="2026-10-01",
                x=i % 20 * 240,
                y=i // 20 * 170,
                width=215,
                height=145,
            )
            for i in range(300)
        ]
        f.rebuild()
        w.refresh()
        w.show()
        w.resize(
            min(1920, w.screen().availableGeometry().width()),
            min(1080, w.screen().availableGeometry().height()),
        )
        QTest.qWait(80)
        positions = lambda: [(m.uid, m.x, m.y, m.width, m.height) for m in f.machines]
        geometry = positions()
        uids = {m.uid for m in f.machines[:3]}
        f.selection_state.set(uids)
        w.navigation.activate("floor")
        f.open_details(f.machines[0].uid)
        QTest.qWait(80)
        c = f.detail_controller
        p = c.panel
        assert (
            not p.isWindow()
            and not c.dock.isFloating()
            and 0.32 < c.dock.width() / c.host.width() < 0.68
        )
        buttons = p.findChild(QDialogButtonBox)
        assert c.host.rect().contains(buttons.mapTo(c.host, buttons.rect().bottomRight()))
        assert p.confirmed_cost.value() == 270 and cost_profile(p.updated)["source"] == "商品名末尾"
        p.confirmed_cost.setValue(300)
        assert (
            p.kpis["cost_rate"].value.text() == "55.00%" and p.radar.axes[3]["actual"] == "55.00%"
        )
        assert p.radar.axes[4]["actual"] == "20.00%" and p.radar.axes[4]["score"] == 100
        p.accept()
        QTest.qWait(40)
        assert (
            f.machines[0].prize_cost == 300
            and f.machines[0].cost_rate == 0.9
            and f.machines[0].po_rate == 0.2
        )
        f.view.setFocus()
        QTest.keyClick(f.view, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
        QTest.qWait(30)
        assert f.machines[0].prize_cost is None
        QTest.keyClick(f.view, Qt.Key.Key_Y, Qt.KeyboardModifier.ControlModifier)
        QTest.qWait(30)
        assert f.machines[0].prize_cost == 300
        for position in ("left", "floating", "right"):
            c.set_position(position)
            QTest.qWait(35)
            assert c.dock.isFloating() == (position == "floating")
        assert (
            f.open_details(f.machines[1].uid)
            and c.panel is p
            and f.selection_state.selected_machine_keys == uids
        )
        for theme in ("light", "dark"):
            manager().set_mode(theme, persist=False)
            QTest.qWait(35)
            assert w.grab().save(str(output / f"detail-{theme}.png"))
        report.update(
            cost_priority=True,
            cost_rate_recalculation=True,
            raw_po_preserved=True,
            radar_actual_values=True,
            reusable_dock=True,
            dock_left_right_float=True,
            selection_preserved=True,
            shortcut_undo_redo=True,
        )
        c.dock.close()
        w.navigation.activate("candidates")
        QTest.qWait(80)
        page = w.candidate_page
        t = page.table
        assert t.rowCount() == 300
        for theme in ("light", "dark"):
            manager().set_mode(theme, persist=False)
            QTest.qWait(30)
            first = t.item(0, 0)
            QTest.mouseClick(
                t.viewport(), Qt.MouseButton.LeftButton, pos=t.visualItemRect(first).center()
            )
            keys = set(page.selected_uids())
            t.verticalScrollBar().setValue(t.verticalScrollBar().maximum() // 2)
            scroll = t.verticalScrollBar().value()
            for pos in (QPoint(15, 20), QPoint(100, t.viewport().height() - 3), QPoint(150, 40)):
                QTest.mouseMove(t.viewport(), pos)
            QTest.qWait(120)
            assert t.verticalScrollBar().value() == scroll and set(page.selected_uids()) == keys
            wheel = QWheelEvent(
                QPointF(100, 100),
                QPointF(t.viewport().mapToGlobal(QPoint(100, 100))),
                QPoint(),
                QPoint(0, -120),
                Qt.MouseButton.NoButton,
                Qt.KeyboardModifier.NoModifier,
                Qt.ScrollPhase.ScrollUpdate,
                False,
            )
            QApplication.sendEvent(t.viewport(), wheel)
            assert t.verticalScrollBar().value() > scroll and set(page.selected_uids()) == keys
            QApplication.sendEvent(t, QEvent(QEvent.Type.WindowDeactivate))
            page.refresh()
            assert set(page.selected_uids()) == keys
            assert w.grab().save(str(output / f"candidates-{theme}.png"))
        page.select_all_candidates()
        assert len(page.selected_uids()) == 300
        f.selection_state.set(uids)
        page.show_reasons(f.machines[0].uid)
        assert page.reason_panel.isVisible()
        report.update(
            candidate_hover_stable=True,
            candidate_wheel_stable=True,
            candidate_count=300,
            numerical_reason_panel=True,
        )
        # The input uses unknown labels so the actual manual mapping UI is exercised.
        book = Workbook()
        sheet = book.active
        sheet.title = "独自売上"
        sheet.append(["識別", "金額", "払い出し", "正式単価", "PO率", "原価率", "商品名"])
        sheet.append(["23000-02", "6,000円", 11, 270, 0.2, 0.495, "売上登録用"])
        sheet.append(["集計期間：2026/11/06 ～ 2026/11/06"])
        source = Path(runtime.name) / "sales.xlsx"
        book.save(source)
        raw = source.read_bytes()
        d = AutoImportDialog(w, source, "sales")
        d.show()
        QTest.qWait(30)
        for key, column in dict(
            number=0, sales=1, dispensed=2, unit_price=3, po_rate=4, cost_rate=5, product=6
        ).items():
            d.columns[key].setCurrentIndex(d.columns[key].findData(column))
        d.preview()
        assert d.prepared and not d.prepared.errors and d.prepared.count == 1
        assert d.grab().save(str(output / "excel-preview.png"))
        prepared = d.prepared
        d.reject()
        w.apply_prepared_import(prepared)
        assert source.read_bytes() == raw and len(w.workflow.data["import_profiles"]) == 1
        assert (
            f.machines[0].product == "景品0 270"
            and positions() == geometry
            and f.selection_state.selected_machine_keys == uids
        )
        assert (
            effective_stat(f.machines[0], "cost_rate") == 0.55
            and f.machines[0].analysis["values"]["cost_rate"] == 0.495
        )
        report["excel_reviewed_mapping"] = True
        w.products = [
            Product(date(2026, 11, 8), "", 40, 1, machines=[m.number], order=i)
            for i, m in enumerate(f.machines[:25])
        ]
        w.refresh()
        w.products[0].name = "新商品A"
        w.products.append(
            Product(
                date(2026, 11, 8),
                "矛盾する新商品",
                40,
                1,
                machines=[f.machines[0].number],
                order=25,
            )
        )
        w.refresh()
        assert (
            not w.preflight.request(lambda: None)
            and w.preflight.counts["error"] > 0
            and not w.preflight.continue_button.isEnabled()
        )
        w.products.pop()
        w.products[0].name = ""
        w.products[0].units = None
        w.refresh()
        continued = []
        assert (
            not w.preflight.request(lambda: continued.append(True))
            and not w.preflight.counts["error"]
        )
        w.preflight.continue_button.click()
        assert continued == [True]
        assert w.grab().save(str(output / "preflight.png"))
        w.products[0].units = 40
        w.refresh()
        config = dict(include_instruction=True, include_map=True, include_checklist=True)
        dialog = WorkSetDialog(w)
        assert all(c.isChecked() for c in dialog.checks.values())
        dialog.reject()
        saved = output / "work-set.pdf"
        original_save = w.save_path
        w.save_path = lambda *_: str(saved)
        w.export_work_set(config)
        if w.preflight.pending:
            w.preflight.continue_button.click()
        assert saved.exists()
        direct = output / "direct-set.pdf"

        def accept_print(dialog):
            printer = dialog.printer()
            printer.setOutputFormat(printer.OutputFormat.PdfFormat)
            printer.setOutputFileName(str(direct))
            return QDialog.DialogCode.Accepted

        QPrintDialog.exec = accept_print
        w.print_work_set(config)
        if w.preflight.pending:
            w.preflight.continue_button.click()
        read = lambda path: normalized_pdf_text(
            "".join(p["text"] for p in require_searchable_pdf(path)["pages"])
        )
        assert read(saved) == read(direct) and "作業チェックリスト" in read(saved)
        preview = InstructionPreview(w, job_config=config)
        assert preview.document.pageCount() >= 3
        preview.show()
        QTest.qWait(50)
        assert preview.grab().save(str(output / "work-set-preview.png"))
        preview.reject()
        report.update(
            preflight_error_blocks=True,
            preflight_warning_approval=True,
            blank_product_printable=True,
            work_set_pdf=True,
            direct_print_renderer_matches_pdf=True,
        )
        w.navigation.activate("dashboard")
        QTest.qWait(40)
        w.dashboard_page.refresh()
        for theme in ("light", "dark"):
            manager().set_mode(theme, persist=False)
            QTest.qWait(30)
            assert w.grab().save(str(output / f"home-{theme}.png"))
        assert (
            w.dashboard_page.cards["total"].value.text() == "300"
            and w.dashboard_page.cards["output"].value.text() == "25"
        )
        saved_state = Path(runtime.name) / "saved.json"
        w.save_path = lambda *_: str(saved_state)
        w.save_json()
        products, day, data = load_session(saved_state)
        assert (
            data["workflow_state"]["import_profiles"]
            and data["workflow_state"]["candidate_evaluations"]
            and data["workflow_state"]["audit_log"]
        )
        assert data["floor_layout"]["detail_dock"]["position"] == "right"
        assert positions() == geometry
        report.update(
            home_live_counts=True,
            profile_audit_evidence_roundtrip=True,
            positions_unchanged=True,
            ok=True,
        )
    except Exception:
        report["error"] = traceback.format_exc()
    finally:
        QPrintDialog.exec = original_exec
        if w:
            if original_save:
                w.save_path = original_save
            w.dirty = False
            w.instruction_save_timer.stop()
            w.floor.timer.stop()
            w.floor.day_timer.stop()
            w.preflight.timer.stop()
            if w.floor.detail_controller.panel:
                machine = next(
                    (
                        m
                        for m in w.floor.machines
                        if m.uid == w.floor.detail_controller.panel.original.uid
                    ),
                    None,
                )
                if machine:
                    w.floor.detail_controller.panel.load_machine(machine)
            w.close()
        runtime.cleanup()
        (output / "report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(run(sys.argv[1]))
