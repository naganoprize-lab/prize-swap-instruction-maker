"""Measured A4 fitting, ordered split search, and all public print paths."""

from datetime import date

import pytest
from PySide6.QtCore import QDate
from PySide6.QtPdf import QPdfDocument
from PySide6.QtPrintSupport import QPrintDialog, QPrintPreviewDialog, QPrintPreviewWidget
from PySide6.QtWidgets import QApplication, QComboBox, QDialog, QLabel

from prize_swap.core import Product, load_session, save_session
from prize_swap.instruction_layout import ProductGroup
from prize_swap.pdf_validation import normalized_pdf_text, require_searchable_pdf
from prize_swap.print_layout import balanced_pages
from prize_swap.printing import configured_printer, plan_pages, save_pdf
from prize_swap.ui import Window

DAY = date(2026, 11, 5)


def products(count):
    result = []
    for i in range(count):
        ids = [f"{20000+i*3}右", f"{20001+i*3}左"]
        result.append(
            Product(
                DAY,
                f"[P{i:02}] クロミ ブランケット 新入荷景品フィギュア [E{i:02}]",
                40,
                1,
                order=i,
                machines=ids,
                planned_booths={"橋渡し": 1, "リング": 1},
                machine_methods={ids[0]: "橋渡し", ids[1]: "リング"},
            )
        )
    return result


def emitted(plan):
    return [g.product for i in range(len(plan.pages)) for col in plan.columns(i) for g in col]


@pytest.mark.parametrize("count", [10, 20, 30, 40])
@pytest.mark.parametrize("mode", ["auto", "compact"])
def test_a4_target_pages_actual_pdf_names_machines_and_bounds(tmp_path, count, mode):
    source = products(count)
    path = tmp_path / f"{count}-{mode}.pdf"
    plan = save_pdf(path, source, DAY, mode=mode)
    assert len(plan.pages) <= (1 if count <= 20 else 2)
    assert plan.name_size >= 10 and plan.body_size >= 9
    assert plan.widths[3] > max(plan.widths[:3] + plan.widths[4:])
    assert emitted(plan) == source
    pdf = require_searchable_pdf(path)
    assert len(pdf["pages"]) == len(plan.pages)
    text = normalized_pdf_text("".join(p["text"] for p in pdf["pages"]))
    for p in source:
        assert text.count(normalized_pdf_text(p.name)) == 1
        for machine in p.machines:
            assert text.count(machine) == 1
    document = QPdfDocument()
    assert document.load(str(path)) == QPdfDocument.Error.None_
    for index in range(len(plan.pages)):
        size = document.pagePointSize(index)
        assert abs(size.width() - 841.89) < 1 and abs(size.height() - 595.28) < 1
        assert not document.render(index, size.toSize()).isNull()
        for column in plan.columns(index):
            assert sum(g.height for g in column) <= plan.body_height + 0.001
            for group in column:
                assert group.machines == group.product.machines and not group.continuation
                for block in group.cells[2:]:
                    assert block.height + 2 * plan.padding <= group.height + 0.001
                    for n in range(block.layout.lineCount()):
                        assert block.layout.lineAt(n).naturalTextWidth() <= block.width + 0.01
    document.close()


@pytest.mark.parametrize("heights", [[20] * 20, [20] * 9 + [10] * 11, [10, 20, 150, 10, 10]])
def test_every_order_preserving_split_is_considered_and_minimum_difference_chosen(heights):
    groups = [ProductGroup(Product(DAY, str(i), order=i), [""], [h]) for i, h in enumerate(heights)]
    pages = balanced_pages(groups, 1000)
    assert len(pages) == 1
    left = [p.groups[0] for p in pages[0] if p.groups[0]]
    right = [p.groups[1] for p in pages[0] if p.groups[1]]
    assert left + right == groups
    difference = abs(sum(g.height for g in left) - sum(g.height for g in right))
    all_candidates = [abs(sum(heights[:k]) - sum(heights[k:])) for k in range(len(heights) + 1)]
    assert difference == min(all_candidates)
    if heights == [20] * 9 + [10] * 11:
        assert len(left) != len(right)  # Height, not product count, determines the split.


def test_page_density_is_even_and_products_have_real_variable_heights():
    groups = [ProductGroup(Product(DAY, str(i), order=i), [""], [49]) for i in range(30)]
    pages = balanced_pages(groups, 500)
    totals = [sum(g.height for p in page for g in p.groups if g) for page in pages]
    assert len(pages) == 2 and max(totals) == min(totals)
    p = products(3)
    p[0].name = "短い景品"
    p[1].name = "非常に長い日本語の商品名" * 15
    p[2].machines = [f"{30000+i}右" for i in range(6)]
    plan = plan_pages(p, 808, 561)
    measured = {
        id(g.product): g for i in range(len(plan.pages)) for col in plan.columns(i) for g in col
    }
    assert measured[id(p[0])].height < measured[id(p[1])].height
    assert measured[id(p[0])].height < measured[id(p[2])].height
    assert emitted(plan) == p


def test_unavoidable_extra_pages_are_explained_and_long_names_not_cut(tmp_path):
    p = products(20)
    for i, item in enumerate(p):
        item.name = f"[LONG{i:02}] " + "非常に長い日本語の商品名" * 16 + f" [END{i:02}]"
    path = tmp_path / "長文.pdf"
    plan = save_pdf(path, p, DAY)
    assert len(plan.pages) > 1
    assert "目標1ページ" in plan.reason and "商品名最大" in plan.reason and "欠落" in plan.reason
    text = normalized_pdf_text(
        "".join(page["text"] for page in require_searchable_pdf(path)["pages"])
    )
    for item in p:
        assert text.count(normalized_pdf_text(item.name)) == 1


@pytest.mark.parametrize("mode", ["auto", "standard", "compact"])
def test_modes_are_saved_and_old_sessions_default_to_auto(tmp_path, mode):
    path = tmp_path / "設定.json"
    save_session(path, products(1), DAY, "", print_mode=mode)
    assert load_session(path)[2]["print_mode"] == mode
    import json

    old = json.loads(path.read_text())
    old.pop("print_mode")
    path.write_text(json.dumps(old), encoding="utf-8")
    assert load_session(path)[2].get("print_mode", "auto") == "auto"


@pytest.fixture
def window(tmp_path):
    w = Window(layout_storage=tmp_path / "floor.json")
    w.products = products(20)
    w.day.setDate(QDate(2026, 11, 5))
    w.refresh()
    w.show()
    QApplication.processEvents()
    yield w
    w.dirty = False
    w.close()
    QApplication.processEvents()


@pytest.mark.parametrize("mode", ["auto", "standard", "compact"])
def test_direct_print_and_pdf_save_use_identical_optimized_layout(
    window, tmp_path, monkeypatch, mode
):
    import prize_swap.ui as ui

    direct = tmp_path / "直接印刷.pdf"
    saved = tmp_path / "保存.pdf"
    window.print_mode.setCurrentIndex(window.print_mode.findData(mode))
    monkeypatch.setattr(ui, "configured_printer", lambda: configured_printer(direct))
    monkeypatch.setattr(QPrintDialog, "exec", lambda self: QDialog.DialogCode.Accepted)
    window.print_direct()
    plan = save_pdf(saved, window.products, DAY, mode=mode)
    assert normalized_pdf_text(
        "".join(p["text"] for p in require_searchable_pdf(direct)["pages"])
    ) == normalized_pdf_text("".join(p["text"] for p in require_searchable_pdf(saved)["pages"]))
    a, b = QPdfDocument(), QPdfDocument()
    assert a.load(str(direct)) == b.load(str(saved)) == QPdfDocument.Error.None_
    assert a.pageCount() == b.pageCount() == len(plan.pages)
    for page in range(a.pageCount()):
        size = a.pagePointSize(page).toSize()
        assert a.render(page, size) == b.render(page, size)
    a.close()
    b.close()


def test_preview_mode_selector_repaints_and_explains_actual_pages(window, monkeypatch):
    observed = []

    def inspect(dialog):
        dialog.show()
        QApplication.processEvents()
        mode = next(c for c in dialog.findChildren(QComboBox) if c.findData("compact") >= 0)
        mode.setCurrentIndex(mode.findData("compact"))
        QApplication.processEvents()
        widget = dialog.findChild(QPrintPreviewWidget)
        widget.updatePreview()
        observed.append(widget.pageCount())
        assert any(
            "コンパクト" in label.text() and "ページ" in label.text()
            for label in dialog.findChildren(QLabel)
        )
        dialog.hide()
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(QPrintPreviewDialog, "exec", inspect)
    window.preview()
    assert observed and observed[0] <= 1
    assert window.print_mode.currentData() == "compact"
