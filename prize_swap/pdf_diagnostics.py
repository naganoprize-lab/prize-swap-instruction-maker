"""Generate synthetic PDF evidence on the actual CI runner before building."""
from datetime import date
import json
import os
from pathlib import Path
import sys
import traceback
import unicodedata


def run(output):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6 import __version__ as qt_binding_version
    from PySide6.QtCore import QSize, qVersion
    from PySide6.QtWidgets import QApplication
    from PySide6.QtPdf import QPdfDocument
    from .core import Product
    from .fonts import validate_print_fonts
    from .printing import save_pdf, configured_printer
    from .labels_data import LabelEntry
    from .labels_printing import save_labels_pdf
    from .floor import FloorWidget
    from .floor_data import Machine
    from .pdf_validation import require_searchable_pdf

    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    app = QApplication.instance() or QApplication([])
    report = dict(ok=False, platform=sys.platform, qt=qVersion(), pyside=qt_binding_version)
    floor = None
    def compact(text):
        return "".join(unicodedata.normalize("NFKC", text).split())
    try:
        report["fonts"] = validate_print_fonts()
        day = date(2026, 11, 5)
        products = [Product(day, f"MARK{i:03d} 商品名ぬいぐるみ " * 4, 24, 2, machines=["20111左"], methods=["橋渡し", "リング"], order=i) for i in range(13)]
        save_pdf(output / "instructions.pdf", products, day, rows=3)
        save_labels_pdf(output / "labels.pdf", [LabelEntry(p) for p in products])
        floor = FloorWidget(lambda: products, lambda: day, output / "floor-auto.json")
        floor.add_machine(Machine(number="20111左", product="現在の商品名", x=100, y=100))
        floor.paint_floor(configured_printer(output / "floor.pdf"))
        for name, required in (("instructions", [f"MARK{i:03d}" for i in range(13)] + ["商品名", "橋渡し"]), ("floor", ["20111", "商品名"]), ("labels", [f"MARK{i:03d}" for i in range(13)] + ["商品名"])):
            path = output / f"{name}.pdf"
            independent = require_searchable_pdf(path)
            doc = QPdfDocument()
            if doc.load(str(path)) != QPdfDocument.Error.None_:
                raise ValueError(f"Qt could not load {path}")
            qt_texts = []
            for page in range(doc.pageCount()):
                text = doc.getAllText(page).text()
                qt_texts.append(text)
                image = doc.render(page, QSize(1263, 893))
                if image.isNull() or not image.save(str(output / f"{name}-{page+1}.png")):
                    raise ValueError("PDF rasterization failed")
            qt_text = compact("".join(qt_texts))
            other_text = compact("".join(p["text"] for p in independent["pages"]))
            for text in required:
                if text not in qt_text or text not in other_text:
                    raise ValueError(f"Missing searchable text: {name}: {text}")
            report[name] = dict(pages=doc.pageCount(), fonts=independent["fonts"],
                                sizes=[[p["width"], p["height"]] for p in independent["pages"]],
                                qt_text=qt_texts, pypdf_text=[p["text"] for p in independent["pages"]])
            doc.close()
        report["ok"] = True
    except Exception:
        report["error"] = traceback.format_exc()
    finally:
        if floor:
            floor.timer.stop()
            floor.close()
        (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k:v for k,v in report.items() if k not in ("instructions", "floor", "labels")}, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(run(sys.argv[1] if len(sys.argv) > 1 else "build-diagnostics"))
