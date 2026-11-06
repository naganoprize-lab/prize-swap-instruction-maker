"""Generate synthetic PDF evidence on the actual CI runner before building."""
from datetime import date
import json
import os
from pathlib import Path
import sys
import traceback


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
    from .floor_print import FloorPrintOptions, save_floor_pdf
    from .pdf_validation import normalized_pdf_text, require_searchable_pdf

    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    app = QApplication.instance() or QApplication([])
    report = dict(ok=False, platform=sys.platform, qt=qVersion(), pyside=qt_binding_version)
    floor = None
    def compact(text):
        return normalized_pdf_text(text)
    try:
        report["fonts"] = validate_print_fonts()
        day = date(2026, 11, 5)
        products = [Product(day, f"MARK{i:03d} 商品名ぬいぐるみ " * 4, 24, 2, machines=["20111左"], methods=["橋渡し", "リング"], order=i) for i in range(13)]
        products[0].methods = ["リング"]
        products[0].name += " 鬼滅の刃 フィギュア"
        products[0].planned_booths = {"リング": 3}
        products[0].machine_methods = {"20111左": "リング"}
        products[1].machines = ["20112右", "20113左", "20114右"]
        products[1].planned_booths = {"橋渡し": 2, "リング": 1}
        products[1].machine_methods = {"20112右": "橋渡し", "20113左": "リング", "20114右": "橋渡し"}
        products[2].methods = ["フック"]
        products[2].planned_booths = {"フック":3}
        save_pdf(output / "instructions.pdf", products, day, rows=3)
        save_labels_pdf(output / "labels.pdf", [LabelEntry(p) for p in products])
        floor = FloorWidget(lambda: products, lambda: day, output / "floor-auto.json")
        floor.add_machine(Machine(number="20111左", product="現在の商品名", x=100, y=100))
        floor.paint_floor(configured_printer(output / "floor.pdf"))
        for name, required in (("instructions", [f"MARK{i:03d}" for i in range(13)] + ["商品名", "橋渡し", "リング3済1未2超0", "橋渡し2済2未0超0", "フック3済0未3超0", "20112右"]), ("floor", ["20111", "商品名"]), ("labels", [f"MARK{i:03d}" for i in range(13)] + ["商品名"])):
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
            if name in ("instructions", "labels"):
                expected = normalized_pdf_text("鬼滅の刃 フィギュア")
                if qt_text.count(expected) != 1 or other_text.count(expected) != 1:
                    raise ValueError(f"Missing or repeated Japanese product name: {name}")
            report[name] = dict(pages=doc.pageCount(), fonts=independent["fonts"],
                                sizes=[[p["width"], p["height"]] for p in independent["pages"]],
                                qt_text=qt_texts, pypdf_text=[p["text"] for p in independent["pages"]],
                                normalized_qt_text=qt_text, normalized_pypdf_text=other_text,
                                ghost_codepoints=sorted({f"U+{ord(c):04X}" for c in "".join(qt_texts)+"".join(p["text"] for p in independent["pages"]) if c in "鬼⻤⿁"}))
            doc.close()
        report["optimization"] = {}
        for count in (10,20,30,40):
            sample=[]
            for i in range(count):
                ids=[f"{30000+i*3}右",f"{30001+i*3}左"]
                sample.append(Product(day,f"OPT{count:02}-{i:03} クロミ ブランケット 新入荷景品フィギュア",40,1,
                    order=i,machines=ids,planned_booths={"橋渡し":1,"フック":1},
                    machine_methods={ids[0]:"橋渡し",ids[1]:"フック"}))
            path=output/f"optimized-{count}.pdf"
            plan=save_pdf(path,sample,day)
            pdf=require_searchable_pdf(path)
            text=compact("".join(p["text"] for p in pdf["pages"]))
            if len(plan.pages)>(1 if count<=20 else 2) or any(text.count(f"OPT{count:02}-{i:03}")!=1 for i in range(count)):
                raise ValueError(f"Optimized layout target or product coverage failed: {count}")
            doc=QPdfDocument()
            if doc.load(str(path))!=QPdfDocument.Error.None_:
                raise ValueError("Optimized PDF load failed")
            for page in range(doc.pageCount()):
                image=doc.render(page,QSize(1263,893))
                if image.isNull() or not image.save(str(output/f"optimized-{count}-{page+1}.png")):
                    raise ValueError("Optimized PDF rasterization failed")
            doc.close()
            report["optimization"][str(count)]=dict(pages=len(plan.pages),reason=plan.reason,
                name_points=plan.name_size,body_points=plan.body_size,
                column_heights_points=[[sum(g.height for g in col) for col in plan.columns(k)] for k in range(len(plan.pages))])
        machines=[Machine(number=str(50000+i),product=f"FLOOR{i:03} 長い日本語商品名 フィギュア",
            x=(i%20)*240,y=(i//20)*300,sales=1000,average=2000,
            ratio=[49.9,50,99.9,100,199.9,200,None][i%7],ratio_from_excel=True,
            installed_on="2026-11-01") for i in range(298)]
        path=output/"floor-298.pdf"
        floor_plan=save_floor_pdf(path,machines,{},FloorPrintOptions(paper="A3",mode="tile"),day)
        evidence=require_searchable_pdf(path)
        contents=compact("".join(p["text"] for p in evidence["pages"]))
        if any(contents.count(f"FLOOR{i:03}")!=1 for i in range(298)):
            raise ValueError("Missing or repeated floor cards")
        doc=QPdfDocument();doc.load(str(path))
        for page in (0,doc.pageCount()-1):
            image=doc.render(page,QSize(1263,893))
            if image.isNull() or not image.save(str(output/f"floor-298-{page+1}.png")):
                raise ValueError("Floor PDF rasterization failed")
        doc.close()
        report["floor_operations"]=dict(machines=298,pages=len(floor_plan.pages),
            warnings=floor_plan.warnings,fonts=evidence["fonts"],boundary_values=[49.9,50,99.9,100,199.9,200],
            colours=[c.background for c in floor_plan.pages[0].cards],
            text=[p["text"] for p in evidence["pages"]])
        report["ok"] = True
    except Exception:
        report["error"] = traceback.format_exc()
    finally:
        if floor:
            floor.timer.stop()
            floor.close()
        (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k:v for k,v in report.items() if k not in ("instructions", "floor", "labels", "floor_operations")}, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(run(sys.argv[1] if len(sys.argv) > 1 else "build-diagnostics"))
