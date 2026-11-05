from datetime import date
import re
import unicodedata

import pytest
from PySide6.QtCore import QDate, QSize, Qt, QTimer
from PySide6.QtGui import QFont
from PySide6.QtPdf import QPdfDocument
from PySide6.QtPrintSupport import QPrinter, QPrintPreviewDialog, QPrintPreviewWidget
from PySide6.QtWidgets import QApplication, QDialog

from prize_swap.core import AppError, Product, load_session, machine_numbers, save_session
from prize_swap.labels_data import LabelEntry, LabelOptions, LabelStore, expanded_labels, label_machines
from prize_swap.labels_printing import PADDING, fitted_text, label_printer, plan_labels, render_labels, save_labels_pdf
from prize_swap.pdf_validation import require_searchable_pdf
from prize_swap.printing import configured_printer, save_pdf
from prize_swap.ui import Window

DAY = date(2026, 11, 5)


def compact(text):
    return "".join(unicodedata.normalize("NFKC", text).split())


@pytest.mark.parametrize("raw,expected", [
    ("20085右", ["20085右"]),
    (" 20098右, 20083左 ", ["20098右", "20083左"]),
    ("20112左，20099左、20090右", ["20112左", "20099左", "20090右"]),
    ("1右\n2左;3右；4左,1右", ["1右", "2左", "3右", "4左"]),
    (["1右,2左", "2左", "3右"], ["1右", "2左", "3右"]),
    ("1右,1右、1右\n1右;1右", ["1右"]),
    ("", []), ([], []), (None, []),
])
def test_automatic_machine_counts_and_separators(raw, expected):
    assert label_machines(raw) == expected
    entry = LabelEntry(Product(DAY, "商品A", machines=raw))
    assert entry.machines == expected
    assert entry.copies == max(1, len(expected))


def test_manual_copies_reset_and_grouped_source_order():
    a = LabelEntry(Product(DAY, "商品A", machines=["1右"], order=1))
    b = LabelEntry(Product(DAY, "商品B", machines=["2右", "3左"], order=2))
    c = LabelEntry(Product(DAY, "商品C", machines=["4左", "5右", "6右"], order=3))
    assert expanded_labels([c, a, b]) == ["商品A", "商品B", "商品B", "商品C", "商品C", "商品C"]
    b.manual_copies = 4
    b.product.machines = ["2右"]
    assert b.copies == 4
    b.manual_copies = None
    assert b.copies == 1
    a.product.included = False
    c.included_override = False
    assert expanded_labels([a,b,c]) == ["商品B"]


@pytest.mark.parametrize("count,expected_pages", [(7,[7]), (23,[7,7,7,2])])
def test_pdf_portrait_names_only_and_all_text_inside_frames(tmp_path, count, expected_pages):
    entries = [LabelEntry(Product(DAY, f"LABEL{i:03d} 商品名ABCぬいぐるみ " + "テスト商品名 " * (i%4), 12345, 999,
                maker="NOT_PRINTED_MAKER", machines=["20085右"], order=i)) for i in range(count)]
    path = tmp_path / "入荷ラベル.pdf"
    plan = save_labels_pdf(path, entries)
    assert [len(page) for page in plan.pages] == expected_pages
    independent = require_searchable_pdf(path)
    doc = QPdfDocument()
    assert doc.load(str(path)) == QPdfDocument.Error.None_
    assert doc.pageCount() == len(expected_pages)
    extracted = []
    printer = label_printer()
    margins = printer.pageLayout().marginsPoints()
    for i in range(doc.pageCount()):
        size = doc.pagePointSize(i)
        assert abs(size.width()-595.28) < 1 and abs(size.height()-841.89) < 1
        selection = doc.getAllText(i)
        extracted.append(compact(selection.text()))
        assert "NOT_PRINTED_MAKER" not in selection.text()
        assert "20085" not in selection.text()
        assert "12345" not in selection.text() and "999" not in selection.text()
        assert "印刷日" not in compact(selection.text()) and "入替指示書" not in compact(selection.text())
        # Verify the actual PDF glyph geometry, not only our layout estimates.
        frames = [cell.rect.translated(margins.left(), margins.top()).adjusted(PADDING-2,PADDING-2,-PADDING+2,-PADDING+2) for cell in plan.pages[i]]
        bounds = selection.bounds()
        assert bounds
        for polygon in bounds:
            rect = polygon.boundingRect()
            if rect.width() > 0 and rect.height() > 0:
                assert any(frame.contains(rect) for frame in frames), (i, rect)
        image = doc.render(i, QSize(595,842))
        assert not image.isNull()
    qt_text = "".join(extracted)
    pdf_text = compact("".join(page["text"] for page in independent["pages"]))
    for i in range(count):
        assert qt_text.count(f"LABEL{i:03d}") == 1
        assert pdf_text.count(f"LABEL{i:03d}") == 1
    doc.close()


def test_font_fit_maximal_wrapping_extreme_warning_and_manual_error(tmp_path):
    options = LabelOptions()
    entry = LabelEntry(Product(DAY, "アニメ「鬼滅の刃」ABC フィギュアぬいぐるみ数量限定 " * 3))
    plan = plan_labels([entry], 561, 808, options)
    cell = plan.pages[0][0]
    assert cell.block.layout.lineCount() > 1
    assert cell.block.layout.font().weight() == QFont.Weight.Bold
    # The next integer point size must no longer fit the same frame.
    too_large = LabelOptions(mode="manual",font_size=cell.font_size+1)
    with pytest.raises(AppError,match="収まり"):
        fitted_text(entry.name, cell.rect.width()-2*PADDING,cell.rect.height()-2*PADDING,too_large,plan.device)
    small = LabelEntry(Product(DAY,"商品名"*150))
    warning_plan = plan_labels([small],561,808)
    assert warning_plan.warnings
    existing = tmp_path / "元.pdf"
    existing.write_bytes(b"old-pdf")
    with pytest.raises(AppError,match="長すぎ"):
        save_labels_pdf(existing,[LabelEntry(Product(DAY,"商品名"*3000))])
    assert existing.read_bytes() == b"old-pdf"


@pytest.mark.parametrize("per_page",[4,10])
def test_layout_settings_and_empty_selection(per_page):
    options = LabelOptions(per_page=per_page, mode="manual",font_size=20,weight="black",alignment="left",border=2.5)
    entries = [LabelEntry(Product(DAY,"商品ABC")) for _ in range(11)]
    plan = plan_labels(entries,561,808,options)
    cell = plan.pages[0][0]
    assert cell.block.layout.font().weight() == QFont.Weight.Black
    assert cell.block.layout.textOption().alignment() == Qt.AlignmentFlag.AlignLeft
    assert len(plan.pages[0]) == per_page
    with pytest.raises(AppError): plan_labels([],561,808)
    with pytest.raises(AppError): LabelOptions(per_page=3).validate()


def test_range_reverse_copies_and_compatibility_with_instructions(tmp_path):
    entries = [LabelEntry(Product(DAY,f"MARK{i:03d}",order=i)) for i in range(15)]
    printer = label_printer(tmp_path/"範囲.pdf")
    printer.setFromTo(2,3)
    printer.setPageOrder(QPrinter.PageOrder.LastPageFirst)
    printer.setCopyCount(2)
    printer.setCollateCopies(True)
    render_labels(printer,entries)
    doc = QPdfDocument()
    assert doc.load(str(tmp_path/"範囲.pdf")) == QPdfDocument.Error.None_
    assert "MARK014" in doc.getAllText(0).text()
    assert "MARK007" in doc.getAllText(1).text()
    assert doc.pageCount() == (2 if printer.supportsMultipleCopies() else 4)
    doc.close()
    assert configured_printer().pageLayout().orientation() != label_printer().pageLayout().orientation()
    save_pdf(tmp_path/"指示書.xlsx.pdf",[entries[0].product],DAY)
    doc2 = QPdfDocument()
    assert doc2.load(str(tmp_path/"指示書.xlsx.pdf")) == QPdfDocument.Error.None_
    assert doc2.pagePointSize(0).width() > doc2.pagePointSize(0).height()
    doc2.close()


@pytest.fixture
def window(tmp_path):
    w=Window(layout_storage=tmp_path/"floor.json")
    w.products=[Product(DAY,"商品A",machines=["1右"],order=1),Product(DAY,"商品B",machines=["2右","3左"],order=2),Product(date(2026,11,6),"別日商品",order=3)]
    w.day.setDate(QDate(2026,11,5));w.refresh();w.tabs.setCurrentWidget(w.labels);w.show();QApplication.processEvents()
    yield w
    w.dirty=False;w.close()


def test_live_updates_overrides_dates_and_add_remove(window):
    labels=window.labels
    assert [e.copies for e in labels.model.entries] == [1,2]
    assert labels.model.setData(labels.model.index(0,1),"ラベル専用表示")
    assert window.products[0].name == "商品A"
    assert window.model.setData(window.model.index(0,3),"元商品変更")
    assert labels.model.entries[0].name == "ラベル専用表示"
    labels.table.selectRow(0);labels.reset_names()
    assert labels.model.entries[0].name == "元商品変更"
    assert window.model.setData(window.model.index(0,6)," 1右;1右，4左、5右\n6左 ")
    assert labels.model.entries[0].copies == 4
    assert labels.model.setData(labels.model.index(0,5),5)
    assert window.model.setData(window.model.index(0,6),"1右")
    assert labels.model.entries[0].copies == 5
    labels.table.selectRow(0);labels.reset_copies()
    assert labels.model.entries[0].copies == 1
    assert window.model.setData(window.model.index(0,0),Qt.CheckState.Unchecked,Qt.ItemDataRole.CheckStateRole)
    assert not labels.model.entries[0].included
    assert not labels.model.setData(labels.model.index(0,0),Qt.CheckState.Checked,Qt.ItemDataRole.CheckStateRole)
    window.model.setData(window.model.index(0,0),Qt.CheckState.Checked,Qt.ItemDataRole.CheckStateRole)
    assert labels.model.entries[0].included
    labels.day.setDate(QDate(2026,11,6))
    assert window.selected_day()==date(2026,11,6) and len(labels.model.entries)==1
    window.day.setDate(QDate(2026,11,5))
    assert len(labels.model.entries)==2
    window.products.append(Product(DAY,"追加商品",order=4));window.refresh()
    assert len(labels.model.entries)==3
    del window.products[0];window.refresh()
    assert len(labels.model.entries)==2
    assert all(e.product.name!="元商品変更" for e in labels.store.entries.values())


def test_unset_selection_manual_reset_and_common_order(window):
    labels=window.labels
    window.model.setData(window.model.index(0,6),"")
    assert labels.model.data(labels.model.index(0,2))=="未設定"
    assert "未設定" in labels.model.data(labels.model.index(0,4),Qt.ItemDataRole.ToolTipRole)
    labels.select_all(False)
    assert expanded_labels(labels.model.entries)==[]
    assert window.products[0].included
    labels.select_all(True)
    labels.model.setData(labels.model.index(0,5),3)
    labels.model.setData(labels.model.index(0,5),0)
    assert labels.model.entries[0].manual_copies is None
    labels.table.selectRow(1);labels.move(-1)
    assert labels.model.entries[0].name=="商品B"
    assert window.proxy.index(0,3).data()=="商品B"
    assert expanded_labels(labels.model.entries)==["商品B","商品B","商品A"]


def test_session_persists_label_settings_and_legacy_json(window,tmp_path,monkeypatch):
    from PySide6.QtWidgets import QFileDialog
    labels=window.labels
    labels.model.setData(labels.model.index(0,1),"独立名")
    labels.model.setData(labels.model.index(0,5),4)
    labels.per_page.setValue(4)
    labels.weight.setCurrentIndex(1)
    path=tmp_path/"作業.json"
    monkeypatch.setattr(window,"save_path",lambda *a:str(path))
    window.save_json()
    products,selected,data=load_session(path)
    restored=LabelStore.restore(data["label_printing"],products)
    assert restored.options.per_page==4 and restored.options.weight=="black"
    assert restored.for_day(products,DAY)[0].name=="独立名"
    assert restored.for_day(products,DAY)[0].copies==4
    monkeypatch.setattr(QFileDialog,"getOpenFileName",lambda *a:(str(path),""))
    window.open_json()
    assert labels.model.entries[0].name=="独立名"
    assert labels.per_page.value()==4
    assert LabelStore.restore(None,products).for_day(products,DAY)[0].name==products[0].name


def test_preview_pdf_and_direct_print_flow(window,tmp_path,monkeypatch):
    import prize_swap.labels_ui as ui
    path=tmp_path/"画面ラベル.pdf"
    monkeypatch.setattr(window.labels,"save_path",lambda:str(path))
    window.export_pdf()  # top-level actions route to the active labels tab
    assert require_searchable_pdf(path)["pages"]
    observed=[]
    def close():
        dialog=window.labels.findChild(QPrintPreviewDialog)
        if dialog and dialog.isVisible():
            observed.append(dialog.findChild(QPrintPreviewWidget).pageCount());dialog.accept()
        else: QTimer.singleShot(20,close)
    QTimer.singleShot(100,close)
    window.preview()
    assert observed==[1]
    target=tmp_path/"直接印刷経路.pdf"
    real_factory=ui.label_printer
    monkeypatch.setattr(ui,"label_printer",lambda:real_factory(target))
    dialogs=[]
    class AcceptedDialog:
        def __init__(self,printer,parent): self.printer=printer;dialogs.append(self)
        def setMinMax(self,first,last): self.range=(first,last)
        def setOption(self,*a): pass
        def exec(self): self.printer.setCopyCount(2);return QDialog.DialogCode.Accepted
    monkeypatch.setattr(ui,"QPrintDialog",AcceptedDialog)
    window.print_direct()
    assert dialogs[0].range==(1,1)
    assert len(require_searchable_pdf(target)["pages"]) in (1,2)
    target.unlink()
    monkeypatch.setattr(AcceptedDialog,"exec",lambda self:QDialog.DialogCode.Rejected)
    window.print_direct()
    assert not target.exists()


@pytest.mark.parametrize("width,height",[(1100,700),(1920,1080)])
def test_label_ui_layout_is_usable(window,width,height):
    window.resize(width,height);QApplication.processEvents()
    assert window.labels.table.width() > width*.60
    assert window.labels.table.height() > height*.30


def test_unchanged_label_edit_keeps_following_source_name(window):
    labels=window.labels
    labels.model.setData(labels.model.index(0,1),"商品A")
    assert labels.model.entries[0].name_override is None
    window.model.setData(window.model.index(0,3),"指示書側の新商品名")
    assert labels.model.entries[0].name=="指示書側の新商品名"


def test_pdf_keeps_repeated_labels_consecutive_across_page_boundary(tmp_path):
    a=LabelEntry(Product(DAY,"商品A",machines=["1右"],order=1))
    b=LabelEntry(Product(DAY,"商品B",machines=["2右","3右"],order=2))
    c=LabelEntry(Product(DAY,"商品C",machines=[f"{i}左" for i in range(4,10)],order=3))
    path=tmp_path/"連続ラベル.pdf"
    plan=save_labels_pdf(path,[c,b,a])
    assert [len(p) for p in plan.pages]==[7,2]
    doc=QPdfDocument()
    assert doc.load(str(path))==QPdfDocument.Error.None_
    text=compact("".join(doc.getAllText(i).text() for i in range(doc.pageCount())))
    expected=["商品A","商品B","商品B"]+["商品C"]*6
    assert re.findall("商品[ABC]",text)==expected
    independent=require_searchable_pdf(path)
    other=compact("".join(p["text"] for p in independent["pages"]))
    assert re.findall("商品[ABC]",other)==expected
    doc.close()
