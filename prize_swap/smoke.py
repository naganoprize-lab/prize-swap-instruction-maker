"""Headless packaged-EXE check. Never sends a job to a physical printer."""
from datetime import date
import json
import os
from pathlib import Path
import traceback


def run(output):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    report = {"ok": False, "physical_printer_tested": False}
    w = None
    runtime = None
    try:
        from PySide6.QtCore import QDate, QTimer
        from PySide6.QtWidgets import QApplication
        from PySide6.QtPdf import QPdfDocument
        from PySide6.QtPrintSupport import QPrintPreviewDialog, QPrintPreviewWidget
        from .core import Product, load_session, save_session
        from .exporter import export_excel
        from .printing import configured_printer, save_pdf
        from .floor_data import Machine
        from .fonts import validate_print_fonts
        from .pdf_validation import require_searchable_pdf
        from .ui import Window
        from tempfile import TemporaryDirectory
        runtime = TemporaryDirectory(prefix=".runtime-", dir=output)
        app = QApplication.instance() or QApplication([])
        report["fonts"] = validate_print_fonts()
        day = date(2026, 11, 5)
        w = Window(layout_storage=Path(runtime.name) / "floor-auto.json")
        w.products = [Product(day, f"ITEM{i:03d} 景品ぬいぐるみ " * (1 + i % 3), 24, 2, order=i) for i in range(45)]
        w.day.setDate(QDate(2026, 11, 5))
        w.refresh()
        w.show()
        app.processEvents()
        assert w.table.height() > w.height() / 2
        assert w.model.setData(w.model.index(0, 6), "20085右\n20098左")
        assert w.model.setData(w.model.index(0, 7), "橋渡し,リング,自由運営")
        assert w.model.setData(w.model.index(0, 9), "各②")
        w.floor.add_machine(Machine(number="20085右", product="現在の商品A", x=100, y=100))
        w.floor.add_machine(Machine(number="20098左", product="現在の商品B", x=350, y=100))
        w.floor.duplicate_selected()
        assert len(w.floor.machines) == 3
        w.floor.history.undo()
        assert len(w.floor.machines) == 2
        w.floor.autosave()
        w.floor.paint_floor(configured_printer(output / "売り場.pdf"))
        floor_pdf = require_searchable_pdf(output / "売り場.pdf")
        assert "20085" in "".join(floor_pdf["pages"][0]["text"].split())
        report["floor_pdf_fonts"] = floor_pdf["fonts"]
        report["floor_machines"] = len(w.floor.machines)
        save_session(output / "作業.json", w.products, day, "", floor_layout=w.floor.state())
        assert load_session(output / "作業.json")[0] == w.products
        export_excel(output / "指示書.xlsx", w.products, day)
        plan = save_pdf(output / "印刷確認.pdf", w.products, day)
        independent = require_searchable_pdf(output / "印刷確認.pdf")
        report["pdf_fonts"] = independent["fonts"]
        doc = QPdfDocument()
        assert doc.load(str(output / "印刷確認.pdf")) == QPdfDocument.Error.None_
        assert doc.pageCount() == len(plan.pages) > 1
        size = doc.pagePointSize(0)
        assert abs(size.width() - 842) < 1 and abs(size.height() - 595) < 1
        assert not doc.render(0, size.toSize()).isNull()
        text = "".join(doc.getAllText(i).text() for i in range(doc.pageCount()))
        independent_text = "".join("".join(p["text"] for p in independent["pages"]).split())
        for i in range(45):
            assert text.count(f"ITEM{i:03d}") == 1 + i % 3
            assert independent_text.count(f"ITEM{i:03d}") == 1 + i % 3
        assert "商品名" in independent_text
        report.update(pdf_pages=doc.pageCount(), page_points=[size.width(), size.height()], products=45)
        doc.close()
        observed = []
        def close_preview():
            dialog = w.findChild(QPrintPreviewDialog)
            if dialog and dialog.isVisible():
                preview = dialog.findChild(QPrintPreviewWidget)
                observed.append(preview.pageCount())
                dialog.accept()
            else:
                QTimer.singleShot(20, close_preview)
        QTimer.singleShot(100, close_preview)
        w.preview()
        assert observed and observed[0] > 0
        report.update(ok=True, preview_pages=observed[0])
    except Exception:
        report["error"] = traceback.format_exc()
    finally:
        if w:
            w.dirty = False
            w.close()
        if runtime:
            runtime.cleanup()
        (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if report["ok"] else 1
