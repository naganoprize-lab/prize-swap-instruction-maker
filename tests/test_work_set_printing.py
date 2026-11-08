from datetime import date
from copy import deepcopy
from dataclasses import asdict
import pytest
from PySide6.QtCore import QDate
from PySide6.QtPdf import QPdfDocument
from PySide6.QtWidgets import QDialog
from PySide6.QtPrintSupport import QPrintDialog
from prize_swap.core import Product
from prize_swap.floor_data import Machine
from prize_swap.instructions import booths
from prize_swap.instruction_output import render_output
from prize_swap.printing import configured_printer, save_pdf
from prize_swap.pdf_validation import require_searchable_pdf, normalized_pdf_text
from prize_swap.work_checklist import checklist_plan
from prize_swap.ui import Window
from prize_swap.work_set_ui import WorkSetDialog

DAY = date(2026, 11, 8)


@pytest.mark.parametrize(
    "flags", [(True, False, False), (False, True, False), (False, False, True), (True, True, True)]
)
def test_individual_and_combined_documents_keep_numbers_layout_and_real_paper(tmp_path, flags):
    ms = [
        Machine(number=f"{20000+i}右", source_key=f"R_{i}", x=i % 10 * 200, y=i // 10 * 200)
        for i in range(25)
    ]
    ps = [
        Product(DAY, f"景品{i:03} 日本語商品名" if i else "", 40, 1, machines=[m.number], order=i)
        for i, m in enumerate(ms)
    ] + [Product(DAY, "出力しない", machines=["99999右"], included=False)]
    before = [asdict(m) for m in ms]
    path = tmp_path / "set.pdf"
    config = dict(zip(["include_instruction", "include_map", "include_checklist"], flags))
    p = render_output(
        configured_printer(path), ps, ms, [], DAY, options=dict(paper="A3"), job_config=config
    )
    text = normalized_pdf_text("".join(v["text"] for v in require_searchable_pdf(path)["pages"]))
    assert (
        ("入れ替え指示書" in text) == flags[0]
        and ("入替予定地図" in text) == flags[1]
        and ("作業チェックリスト" in text) == flags[2]
    )
    assert "出力しない" not in text and "商品未定" not in text
    assert [kind for kind, _ in p.pages] == sorted(
        [kind for kind, _ in p.pages], key=lambda k: ["instructions", "map", "checklist"].index(k)
    )
    if flags[0]:
        original = save_pdf(tmp_path / "original.pdf", deepcopy(ps), DAY)
        assert p.instruction.widths == original.widths and len(p.instruction.pages) == len(
            original.pages
        )
    if flags[2]:
        assert [(b.key, b.number) for b in p.checklist.entries] == [
            (b.key, b.number) for b in booths(ps, ms)
        ]
    if flags[1] and flags[2]:
        assert [(b.key, b.number) for b in p.mapping.entries] == [
            (b.key, b.number) for b in p.checklist.entries
        ]
    assert [asdict(m) for m in ms] == before
    doc = QPdfDocument()
    doc.load(str(path))
    assert doc.pageCount() == len(p.pages)
    if flags[2]:
        assert (
            doc.pagePointSize(len(p.pages) - 1).height()
            > doc.pagePointSize(len(p.pages) - 1).width()
        )
    doc.close()


def test_checklist_very_long_japanese_name_is_split_without_missing_or_duplicating_characters():
    name = "非常に長い日本語商品名と絵文字🎁" * 180
    p = Product(DAY, name, machines=["R_168"])
    plan = checklist_plan([p], [Machine(source_key="R_168")], [], DAY)
    assert len(plan.pages) > 1
    assert "".join(row.cells[3].text for page in plan.pages for row in page) == name
    assert all(row.booth.number == 1 for page in plan.pages for row in page)


def test_set_config_preview_pdf_direct_and_preflight_use_same_renderer(tmp_path, monkeypatch):
    w = Window(layout_storage=tmp_path / "floor.json")
    w.day.setDate(QDate(2026, 11, 8))
    w.products = [Product(DAY, "", 40, 1, machines=["20088左"])]
    w.floor.machines = [Machine(number="20088左")]
    w.floor.rebuild()
    w.refresh()
    config = dict(include_instruction=True, include_map=True, include_checklist=True)
    try:
        d = WorkSetDialog(w)
        assert all(c.isChecked() for c in d.checks.values())
        d.reject()
        saved = tmp_path / "saved.pdf"
        monkeypatch.setattr(w, "save_path", lambda *_: str(saved))
        w.export_work_set(config)
        assert saved.exists()
        direct = tmp_path / "direct.pdf"

        def accept(dialog):
            p = dialog.printer()
            p.setOutputFormat(p.OutputFormat.PdfFormat)
            p.setOutputFileName(str(direct))
            return QDialog.DialogCode.Accepted

        monkeypatch.setattr(QPrintDialog, "exec", accept)
        w.print_work_set(config)
        read = lambda path: normalized_pdf_text(
            "".join(p["text"] for p in require_searchable_pdf(path)["pages"])
        )
        assert read(saved) == read(direct)
        from prize_swap.instruction_preview import InstructionPreview

        preview = InstructionPreview(w, job_config=config)
        assert preview.document.pageCount() >= 3
        preview.reject()
        assert w.floor.instruction_print_options["mode"] == "instructions"
    finally:
        w.dirty = False
        w.instruction_save_timer.stop()
        w.floor.timer.stop()
        w.floor.day_timer.stop()
        w.preflight.timer.stop()
        w.close()
