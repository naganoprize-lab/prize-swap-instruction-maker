"""Native/DPI and packaged-EXE verification of the actual editable instruction workspace."""

import os, sys, json, traceback
from pathlib import Path
from tempfile import TemporaryDirectory
from dataclasses import asdict
from datetime import date


def run(output):
    if sys.platform != "win32":
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import Qt, QDate, QTimer
    from PySide6.QtWidgets import (
        QApplication,
        QMessageBox,
        QInputDialog,
        QFileDialog,
        QPlainTextEdit,
        QLineEdit,
        QDialog,
    )
    from PySide6.QtPrintSupport import QPrintDialog, QPrintPreviewDialog, QPrintPreviewWidget
    from PySide6.QtTest import QTest
    from .fonts import validate_print_fonts
    from .ui import Window
    from .core import Product
    from .floor_data import Machine
    from .instruction_editor import InstructionDialog
    from .instruction_preview import InstructionPreview
    from .instructions import booths
    from .printing import configured_printer
    from .pdf_validation import require_searchable_pdf, normalized_pdf_text
    from .theme import manager

    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    report = dict(ok=False, physical_printer_tested=False)
    w = None
    runtime = TemporaryDirectory(dir=output, prefix=".runtime-")
    original_exec = QPrintDialog.exec
    original_preview = QPrintPreviewDialog.exec
    original_get = QFileDialog.getOpenFileName
    try:
        app = QApplication.instance() or QApplication([])
        validate_print_fonts()
        report["platform"] = app.platformName()
        report["scale_requested"] = os.environ.get("QT_SCALE_FACTOR", "1")
        w = Window(layout_storage=Path(runtime.name) / "layout.json")
        f = w.floor
        w.day.setDate(QDate(2026, 11, 8))
        f.machines = [
            Machine(
                number=f"{22000+i}右",
                source_key=f"R_{i}",
                product=f"現行景品{i}",
                x=i % 20 * 250,
                y=i // 20 * 180,
                width=220,
                height=150,
            )
            for i in range(300)
        ]
        w.products = [
            Product(
                date(2026, 11, 8),
                "",
                machines=["R_168"],
                notes="投入景品検討中",
                source_type="manual",
                order=0,
            )
        ]
        w.products += [
            Product(
                date(2026, 11, 8),
                f"CHECK{i:03} 日本語の景品 フィギュア",
                40,
                1,
                machines=[f.machines[i].number],
                order=i + 1,
                source_row=i + 2,
            )
            for i in range(28)
        ]
        w.products += [
            Product(date(2026, 11, 8), "出力対象外", machines=["R_299"], included=False, order=40),
            Product(date(2026, 11, 8), "", machines=["R_999"], source_type="manual", order=30),
        ]
        f.rebuild()
        w.refresh()
        w.show()
        app.processEvents()
        QTest.qWait(35)
        before = [asdict(m) for m in f.machines]
        p = w.products[0]
        assert w.model.data(w.model.index(0, 3), Qt.ItemDataRole.EditRole) == "" and p.included
        # Edit a formerly blank product using the real table delegate, then clear it again.
        index = w.proxy.mapFromSource(w.model.index(0, 3))
        w.table.scrollTo(index)
        QTest.mouseDClick(
            w.table.viewport(), Qt.MouseButton.LeftButton, pos=w.table.visualRect(index).center()
        )
        app.processEvents()
        editor = next(
            (v for v in w.table.viewport().findChildren(QPlainTextEdit) if v.isVisible()), None
        )
        if editor is None:
            editor = next(
                (v for v in w.table.viewport().findChildren(QLineEdit) if v.isVisible()), None
            )
        assert editor is not None, "商品名セルを編集できません。"
        if isinstance(editor, QPlainTextEdit):
            editor.setPlainText("手動の商品")
            editor.setPlainText("")
        else:
            editor.setText("手動の商品")
            editor.clear()
        QTest.keyClick(editor, Qt.Key.Key_Tab)
        app.processEvents()
        assert p.name == "" and p.included

        # A genuine modal form, not an alternative instruction generator.
        def fill_manual():
            d = next(
                v
                for v in app.topLevelWidgets()
                if isinstance(v, InstructionDialog) and v.isVisible()
            )
            d.name.setCurrentText("入荷予定外 手動景品")
            d.machines.setPlainText("R_250")
            d.notes.setPlainText("手動で追加した指示")
            d.accept()

        QTimer.singleShot(40, fill_manual)
        assert w.add_instruction()
        assert w.products[-1].source_type == "manual" and w.products[-1].included
        w.table.selectRow(0)

        def confirm_duplicate():
            d = next(
                v for v in app.topLevelWidgets() if isinstance(v, QMessageBox) and v.isVisible()
            )
            d.button(QMessageBox.StandardButton.Yes).click()

        QTimer.singleShot(40, confirm_duplicate)
        assert w.duplicate_instructions()
        w.instruction_history.undo()
        for theme in ("light", "dark"):
            manager().set_mode(theme, persist=False)
            w.instruction_tabs.setCurrentIndex(0)
            app.processEvents()
            assert w.table.visibleRegion().boundingRect().height() > 150
            w.grab().save(str(output / f"{theme}-editable-instructions.png"))
        page = w.instruction_map
        w.instruction_tabs.setCurrentWidget(page)
        app.processEvents()
        QTest.qWait(25)
        assert len(page.entries) == 31 and len(page.unresolved_rows) == 1 and len(f.machines) == 300
        assert len(page.items) == 30 and not page.isWindow()
        item = page.items[f.machines[168].uid]
        point = page.view.mapFromScene(item.sceneBoundingRect().center())
        QTest.mouseClick(page.view.viewport(), Qt.MouseButton.LeftButton, pos=point)
        assert w.selected_products()[0].entry_id == p.entry_id
        page.buttons["＋"].click()
        page.buttons["自動フィット"].click()
        app.processEvents()
        w.grab().save(str(output / "numbered-map.png"))
        before_numbers = {b.key: b.number for b in page.entries}
        assert max(before_numbers.values()) > 20

        def pick_position():
            d = next(
                v for v in app.topLevelWidgets() if isinstance(v, QInputDialog) and v.isVisible()
            )
            combo = d.findChild(__import__("PySide6.QtWidgets", fromlist=["QComboBox"]).QComboBox)
            combo.setCurrentIndex(299)
            d.accept()

        QTimer.singleShot(40, pick_position)
        page.resolve_position(0, 2)
        assert not page.unresolved_rows and len(page.items) == 31
        assert before_numbers == {b.key: b.number for b in page.entries}
        # Preview, PDF and accepted direct-print dialog share the same render functions.
        paths = []
        for paper in ("A4", "A3"):
            for direction in ("portrait", "landscape"):
                page.paper.setCurrentText(paper)
                page.orientation.setCurrentIndex(page.orientation.findData(direction))
                file = output / f"map-{paper}-{direction}.pdf"
                page.render(configured_printer(file))
                paths.append(file)
        page.paper.setCurrentText("A3")
        page.orientation.setCurrentIndex(page.orientation.findData("portrait"))
        direct = output / "map-direct.pdf"

        def accept_direct(d):
            printer = d.printer()
            printer.setOutputFormat(printer.OutputFormat.PdfFormat)
            printer.setOutputFileName(str(direct))
            return QDialog.DialogCode.Accepted

        QPrintDialog.exec = accept_direct
        page.print_direct()

        def pdf_text(path):
            return normalized_pdf_text(
                "".join(v["text"] for v in require_searchable_pdf(path)["pages"])
            )

        assert pdf_text(direct) == pdf_text(output / "map-A3-portrait.pdf")

        preview = InstructionPreview(w, map_page=page)
        preview.show()
        app.processEvents()
        QTest.qWait(40)
        assert (
            preview.document.pageCount() == len(page.plan().pages)
            and not preview.view.grab().isNull()
        )
        preview.paper.setCurrentText("A4")
        app.processEvents()
        assert preview.document.pagePointSize(0).height() < 900
        preview.paper.setCurrentText("A3")
        app.processEvents()
        preview.grab().save(str(output / "map-print-preview.png"))
        preview.reject()
        w.instruction_output_mode.setCurrentIndex(w.instruction_output_mode.findData("both"))
        combined = output / "instructions-plus-map.pdf"
        plan = w.paint_printer(configured_printer(combined))
        assert plan is not None
        text = pdf_text(combined)
        assert (
            "出力対象外" not in text
            and "入れ替え指示書" in text
            and "入替予定地図" in text
            and "商品未定" not in text
        )
        for i in range(28):
            assert text.count(f"CHECK{i:03}") == 1
        preview = InstructionPreview(w)
        preview.show()
        app.processEvents()
        QTest.qWait(40)
        assert preview.document.pageCount() == len(plan.pages)
        assert (
            preview.document.pagePointSize(0).height()
            < preview.document.pagePointSize(preview.document.pageCount() - 1).height()
        )
        preview.grab().save(str(output / "combined-print-preview.png"))
        preview.direction.setCurrentIndex(preview.direction.findData("landscape"))
        app.processEvents()
        assert preview.document.pageCount() > 1 and not preview.view.grab().isNull()
        preview.reject()
        entries = [p.entry_id for p in w.products]
        numbers = {b.key: b.number for b in booths(w.visible, f.machines, f.number_references)}
        session = output / "session.json"
        w.save_path = lambda *args: str(session)
        w.save_json()
        QFileDialog.getOpenFileName = lambda *args, **kwargs: (str(session), "")
        w.open_json()
        assert entries == [p.entry_id for p in w.products] and numbers == {
            b.key: b.number for b in booths(w.visible, f.machines, f.number_references)
        }
        assert before == [asdict(m) for m in f.machines]
        report.update(
            ok=True,
            machines=300,
            blank_product_printable=True,
            manual_form=True,
            duplicate_confirmation_and_undo=True,
            numbered_booths=len(numbers),
            above_20=True,
            real_map_click=True,
            unresolved_manual_resolution=True,
            a4_a3_pdfs=len(paths),
            direct_pdf_equal=True,
            combined_pages=len(plan.pages),
            mixed_paper_preview=True,
            save_restore=True,
            physical_coordinates_unchanged=True,
        )
    except Exception:
        report["error"] = traceback.format_exc()
    finally:
        QPrintDialog.exec = original_exec
        QPrintPreviewDialog.exec = original_preview
        QFileDialog.getOpenFileName = original_get
        if w:
            w.dirty = False
            w.instruction_save_timer.stop()
            w.floor.timer.stop()
            w.floor.day_timer.stop()
            w.close()
        runtime.cleanup()
        (output / "report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(run(sys.argv[1]))
