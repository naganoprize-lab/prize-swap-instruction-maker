"""Independent PDF text and embedded-font inspection; used by tests and EXE smoke."""
from pathlib import Path

from pypdf import PdfReader


def inspect_pdf(path):
    reader = PdfReader(Path(path))
    pages = []
    fonts = {}
    for page in reader.pages:
        pages.append(dict(width=float(page.mediabox.width), height=float(page.mediabox.height), text=page.extract_text() or ""))
        resources = page.get("/Resources", {})
        resources = resources.get_object() if hasattr(resources, "get_object") else resources
        font_resources = resources.get("/Font", {})
        font_resources = font_resources.get_object() if hasattr(font_resources, "get_object") else font_resources
        for font_ref in font_resources.values():
            font = font_ref.get_object()
            name = str(font.get("/BaseFont", "unknown"))
            faces = font.get("/DescendantFonts", [font_ref])
            embedded = True
            for face_ref in faces:
                face = face_ref.get_object()
                descriptor = face.get("/FontDescriptor")
                descriptor = descriptor.get_object() if descriptor else {}
                streams = [descriptor[key].get_object().get_data() for key in ("/FontFile", "/FontFile2", "/FontFile3") if key in descriptor]
                embedded &= any(streams)
            cmap = font.get("/ToUnicode")
            fonts[name] = dict(embedded=bool(embedded), unicode_map=bool(cmap and cmap.get_object().get_data()))
    return dict(pages=pages, fonts=fonts)


def require_searchable_pdf(path):
    report = inspect_pdf(path)
    if not report["fonts"] or not all(f["embedded"] and f["unicode_map"] for f in report["fonts"].values()):
        raise ValueError("PDFにUnicode対応の埋込フォントがありません。")
    if not report["pages"] or not all(p["text"].strip() for p in report["pages"]):
        raise ValueError("PDFから文字を抽出できません。画像やパスだけの出力は許可されません。")
    return report
