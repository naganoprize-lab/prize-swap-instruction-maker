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
        from PySide6.QtWidgets import QApplication, QDialog
        from PySide6.QtPdf import QPdfDocument
        from PySide6.QtPrintSupport import QPrintPreviewDialog, QPrintPreviewWidget
        from .core import Product, load_session, save_session
        from .exporter import export_excel
        from .printing import configured_printer, save_pdf
        from .floor_data import Machine
        from .floor_print import FloorPrintOptions, floor_printer
        from .floor_print_ui import FloorPreviewDialog
        from unittest.mock import patch
        from .fonts import validate_print_fonts
        from .pdf_validation import normalized_pdf_text, require_searchable_pdf
        from .ui import Window
        from .labels_data import LabelStore
        from .labels_printing import save_labels_pdf
        from tempfile import TemporaryDirectory
        runtime = TemporaryDirectory(prefix=".runtime-", dir=output)
        app = QApplication.instance() or QApplication([])
        report["fonts"] = validate_print_fonts()
        day = date(2026, 11, 5)
        w = Window(layout_storage=Path(runtime.name) / "floor-auto.json")
        w.products = [Product(day, f"ITEM{i:03d} 景品ぬいぐるみ " * (1 + i % 3), 24, 2, order=i) for i in range(45)]
        w.products[0].name = "ITEM000 鬼滅の刃 フィギュア"
        w.day.setDate(QDate(2026, 11, 5))
        w.refresh()
        w.show()
        app.processEvents()
        assert w.table.height() > w.height() / 2
        assert w.model.setData(w.model.index(0, 6), "20085右\n20098左")
        assert w.model.setData(w.model.index(0, 7), {
            "plans": [["橋渡し", 1], ["フック", 1]],
            "assignments": [["20085右", "橋渡し"], ["20098左", "フック"]],
        })
        assert w.model.setData(w.model.index(0, 9), "各②")
        assert w.summary_table.rowCount() == 4
        assert w.summary_table.item(2,0).text() == "フック"
        assert w.summary_table.item(2,2).text() == "1"
        assert w.summary_table.item(2,3).text() == "0"
        w.floor.add_machine(Machine(number="20085右", product="現在の商品A", x=100, y=100,
            sales=1000,average=2000,ratio=49.9,installed_on="2026-11-01"))
        w.floor.add_machine(Machine(number="20098左", product="現在の商品B", x=350, y=100,
            sales=2000,average=1000,ratio=200,installed_on="2026-11-05"))
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
        w.floor.reference_date.setDate(QDate(2026,11,5))
        w.floor.display_mode.setCurrentIndex(w.floor.display_mode.findData("analysis"))
        w.floor.set_print_options(FloorPrintOptions(paper="A3",orientation="portrait"))
        floor_preview=FloorPreviewDialog(w.floor)
        floor_preview.show();app.processEvents();floor_preview.preview.updatePreview()
        assert floor_preview.preview.pageCount()==1
        floor_preview.close()
        floor_reference=output/"売り場A3.pdf"
        w.floor.paint_floor(floor_printer(w.floor.print_options,floor_reference))
        class AcceptedFloorPrintDialog:
            def __init__(self,*args): pass
            def setMinMax(self,*args): pass
            def setOption(self,*args): pass
            def exec(self): return QDialog.DialogCode.Accepted
        floor_direct=output/"売り場直接印刷確認.pdf"
        with patch("prize_swap.floor.QPrintDialog",AcceptedFloorPrintDialog), patch(
            "prize_swap.floor.floor_printer",lambda options:floor_printer(options,floor_direct)):
            w.floor.print_floor()
        reference_text=normalized_pdf_text("".join(p["text"] for p in require_searchable_pdf(floor_reference)["pages"]))
        direct_text=normalized_pdf_text("".join(p["text"] for p in require_searchable_pdf(floor_direct)["pages"]))
        assert reference_text==direct_text and "アベ比49.9%" in direct_text and "投入4日" in direct_text
        report["floor_print"]={"paper":"A3","preview_pages":1,"direct_pdf_equal":True}
        source_name = w.products[0].name
        assert w.labels.model.entries[0].automatic_copies == 2
        assert w.labels.model.setData(w.labels.model.index(0, 1), "LABEL_ONLY 商品名")
        assert w.labels.model.setData(w.labels.model.index(0, 5), 3)
        assert w.products[0].name == source_name
        save_session(output / "作業.json", w.products, day, "", floor_layout=w.floor.state(), label_state=w.labels.state())
        restored_products, _, metadata = load_session(output / "作業.json")
        assert LabelStore.restore(metadata["label_printing"], restored_products).for_day(restored_products, day)[0].copies == 3
        assert load_session(output / "作業.json")[0] == w.products
        assert restored_products[0].planned_booths == {"橋渡し": 1, "フック": 1}
        assert restored_products[0].machine_methods == {"20085右": "橋渡し", "20098左": "フック"}
        export_excel(output / "指示書.xlsx", w.products, day)
        from openpyxl import load_workbook
        workbook = load_workbook(output / "指示書.xlsx")
        assert "D3:D4" in workbook.active.merged_cells
        assert workbook.active["C3"].value == "橋渡し 1ブース\nフック 1ブース"
        workbook.close()
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
        assert normalized_pdf_text(independent_text).count(normalized_pdf_text("鬼滅の刃 フィギュア")) == 1
        assert normalized_pdf_text(text).count(normalized_pdf_text("鬼滅の刃 フィギュア")) == 1
        assert "橋渡し1済1未0超0" in independent_text and "フック1済1未0超0" in independent_text
        assert plan.mode == "auto" and plan.body_size>=9 and plan.name_size>=10
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
        report.update(preview_pages=observed[0])
        label_plan = save_labels_pdf(output / "入荷ブースラベル.pdf", w.labels.model.entries)
        label_report = require_searchable_pdf(output / "入荷ブースラベル.pdf")
        label_text = "".join("".join(p["text"] for p in label_report["pages"]).split())
        assert label_text.count("LABEL_ONLY") == 3 and "ITEM000" not in label_text
        label_doc = QPdfDocument()
        assert label_doc.load(str(output / "入荷ブースラベル.pdf")) == QPdfDocument.Error.None_
        assert label_doc.pageCount() == len(label_plan.pages) == 7
        assert label_doc.pagePointSize(0).width() < label_doc.pagePointSize(0).height()
        for i in range(label_doc.pageCount()):
            assert not label_doc.render(i, label_doc.pagePointSize(i).toSize()).isNull()
        label_doc.close()
        w.tabs.setCurrentWidget(w.labels)
        label_preview = []
        def close_label_preview():
            dialog = w.labels.findChild(QPrintPreviewDialog)
            if dialog and dialog.isVisible():
                label_preview.append(dialog.findChild(QPrintPreviewWidget).pageCount())
                dialog.accept()
            else:
                QTimer.singleShot(20, close_label_preview)
        QTimer.singleShot(100, close_label_preview)
        w.preview()
        assert label_preview == [len(label_plan.pages)]
        report.update(ok=True, label_pages=len(label_plan.pages), label_preview_pages=label_preview[0],
                      label_count=sum(len(p) for p in label_plan.pages), label_fonts=label_report["fonts"])
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
