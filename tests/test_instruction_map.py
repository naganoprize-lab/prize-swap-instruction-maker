from datetime import date
from copy import deepcopy
from dataclasses import asdict
import importlib.machinery, importlib.util, sys
from pathlib import Path
import pytest
from PySide6.QtCore import QSize, Qt
from PySide6.QtPdf import QPdfDocument
from prize_swap.core import Product
from prize_swap.floor_data import Machine
from prize_swap.instructions import booths, mark_edited
from prize_swap.instruction_map import plan_map, map_layout, draw_map_page
from prize_swap.printing import configured_printer, save_pdf, render_pages
from prize_swap.instruction_output import render_output
from prize_swap.pdf_validation import normalized_pdf_text, require_searchable_pdf

DAY = date(2026, 11, 8)


def test_numbering_per_booth_stable_ids_reorder_and_safe_matching():
    a = Product(DAY, "A", machines=["20088左", "20088右"], order=1)
    b = Product(DAY, "", machines=["R_168"], order=2)
    ms = [
        Machine(number="20088左", x=100),
        Machine(number="20088右", x=300),
        Machine(source_key="R_168", x=1000),
    ]
    refs = booths([a, b], ms)
    assert [r.number for r in refs] == [1, 2, 3] and all(r.machine for r in refs)
    ids = [r.key for r in refs]
    b.order = 0
    refs = booths([a, b], ms)
    assert [r.product for r in refs] == [b, a, a] and set(r.key for r in refs) == set(ids)
    b.machines = ["L_66"]
    mark_edited(b, ["machines"])
    ms.append(Machine(number="20111右"))
    refs = booths([b], ms, [dict(source_key="L_66", number="20111-02")])
    assert refs[0].machine is ms[-1]
    ms.append(Machine(number="20111右"))
    assert booths([b], ms, [dict(source_key="L_66", number="20111右")])[0].status == "照合要確認"


@pytest.mark.parametrize(
    "paper,orientation",
    [
        ("A4", "portrait"),
        ("A4", "landscape"),
        ("A3", "portrait"),
        ("A3", "landscape"),
        ("A4", "auto"),
    ],
)
def test_map_uses_real_positions_readable_numbers_and_no_product_data(tmp_path, paper, orientation):
    ms = [
        Machine(number=f"{20000+i}右", product="印刷しない商品名", x=i % 10 * 250, y=i // 10 * 220)
        for i in range(45)
    ]
    ps = [Product(DAY, "指示の商品名", machines=[m.number], order=i) for i, m in enumerate(ms)]
    before = [asdict(m) for m in ms]
    p = plan_map(ps, ms, [], DAY, paper, orientation)
    assert len({b.key for page in p.pages for g in page.groups for b in g.booths}) == 45
    assert max(b.number for b in p.entries) == 45
    file = tmp_path / f"{paper}-{orientation}.pdf"
    printer = configured_printer(file)
    render_pages(
        printer,
        len(p.pages),
        lambda painter, index: draw_map_page(painter, p, index),
        lambda index: map_layout(p.paper, p.pages[index].orientation),
    )
    text = normalized_pdf_text("".join(v["text"] for v in require_searchable_pdf(file)["pages"]))
    assert (
        "入替予定地図" in text
        and "45" in text
        and "指示の商品名" not in text
        and "印刷しない商品名" not in text
    )
    assert [asdict(m) for m in ms] == before


def test_output_scope_and_combined_mixed_papers_share_targets(tmp_path):
    ms = [Machine(number="20088左"), Machine(source_key="R_168", x=300)]
    ps = [
        Product(DAY, "", machines=["20088左"]),
        Product(DAY, "除外", machines=["R_168"], included=False),
    ]
    for mode in ("instructions", "map", "both"):
        path = tmp_path / f"{mode}.pdf"
        p = render_output(
            configured_printer(path),
            ps,
            ms,
            [],
            DAY,
            options=dict(mode=mode, paper="A3", orientation="portrait"),
        )
        text = normalized_pdf_text(
            "".join(v["text"] for v in require_searchable_pdf(path)["pages"])
        )
        assert "除外" not in text
        assert ("入替予定地図" in text) == (mode != "instructions")
        if mode == "both":
            doc = QPdfDocument()
            doc.load(str(path))
            assert doc.pageCount() == 2
            assert doc.pagePointSize(0).width() > doc.pagePointSize(0).height()
            assert (
                doc.pagePointSize(1).height() > doc.pagePointSize(1).width()
                and doc.pagePointSize(1).height() > 1000
            )
            doc.close()


def test_existing_instruction_layout_geometry_preserved_and_baseline_saved(tmp_path):
    fixture = Path("tests/baselines/instructions/printing_before.py.txt")
    loader = importlib.machinery.SourceFileLoader("prize_swap._printing_reference", str(fixture))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    legacy = importlib.util.module_from_spec(spec)
    sys.modules[loader.name] = legacy
    loader.exec_module(legacy)
    try:
        for count in (20, 40):
            ps = [
                Product(
                    DAY,
                    f"[P{i:02}] クロミ ブランケット 新入荷景品フィギュア [E{i:02}]",
                    40,
                    1,
                    order=i,
                    machines=[f"{20000+i*3}右", f"{20001+i*3}左"],
                    planned_booths={"橋渡し": 1, "リング": 1},
                    machine_methods={f"{20000+i*3}右": "橋渡し", f"{20001+i*3}左": "リング"},
                )
                for i in range(count)
            ]
            before = legacy.save_pdf(tmp_path / f"reference-{count}.pdf", deepcopy(ps), DAY)
            after = save_pdf(tmp_path / f"current-{count}.pdf", ps, DAY)
            assert before.widths == after.widths and len(before.pages) == len(after.pages)
            assert (
                before.title_height,
                before.header_height,
                before.footer_height,
                before.name_size,
                before.body_size,
            ) == (
                after.title_height,
                after.header_height,
                after.footer_height,
                after.name_size,
                after.body_size,
            )
            for n in range(len(before.pages)):
                for oldcol, newcol in zip(before.columns(n), after.columns(n)):
                    assert [(g.product.name, g.height, g.heights) for g in oldcol] == [
                        (g.product.name, g.height, g.heights) for g in newcol
                    ]
            assert Path(f"tests/baselines/instructions/before-{count}.pdf").is_file()
    finally:
        sys.modules.pop(loader.name, None)
