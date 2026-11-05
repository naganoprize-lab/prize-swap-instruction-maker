from pathlib import Path
from types import SimpleNamespace
import os
import subprocess
import sys

from pypdf import PdfWriter
import pytest

from prize_swap import fonts
from prize_swap.pdf_validation import require_searchable_pdf


def test_windows_offscreen_registers_ttc_by_file_path(tmp_path, monkeypatch):
    directory = tmp_path / "Windows" / "Fonts"
    directory.mkdir(parents=True)
    for name in ("arial.ttf", "meiryo.ttc", "YuGothR.ttc"):
        (directory / name).write_bytes(b"fixture")
    app = SimpleNamespace(platformName=lambda: "offscreen")
    registrations = []
    class Database:
        @staticmethod
        def families():
            return ["Arial", "Meiryo"] if registrations else []
        @staticmethod
        def addApplicationFont(path):
            registrations.append(Path(path))
            return len(registrations)
        @staticmethod
        def applicationFontFamilies(font_id):
            return ["Meiryo"]
    monkeypatch.setattr(fonts, "sys", SimpleNamespace(platform="win32"))
    monkeypatch.setattr(fonts, "QGuiApplication", SimpleNamespace(instance=lambda: app))
    monkeypatch.setattr(fonts, "QFontDatabase", Database)
    monkeypatch.setenv("WINDIR", str(directory.parent))
    monkeypatch.delenv("PRIZE_FONT_DIR", raising=False)
    report = fonts.initialize_fonts()
    assert report["families_before"] == 0 and report["families_after"] == 2
    assert {p.name for p in registrations} == {"arial.ttf", "meiryo.ttc", "YuGothR.ttc"}
    fonts.initialize_fonts()
    assert len(registrations) == 3  # No repeated registration per QApplication.


def test_print_fonts_have_real_latin_and_japanese_glyphs(qt_app):
    report = fonts.validate_print_fonts()
    assert len(report["selected_families"]) == 2
    assert report["families_after"] > 0


def test_fontless_backend_is_rejected_before_writing_pdf(tmp_path):
    # Qt minimal has no usable font engine, reproducing the unconfigured
    # Windows offscreen database. It must fail explicitly, never emit box glyphs.
    source = '''
from datetime import date
from pathlib import Path
from PySide6.QtWidgets import QApplication
from prize_swap.printing import save_pdf
from prize_swap.core import AppError,Product
app=QApplication([])
path=Path(__import__('sys').argv[1])
try:
    save_pdf(path,[Product(date(2026,11,5),'商品名')],date(2026,11,5))
except AppError as exc:
    assert 'フォント' in str(exc)
else:
    raise AssertionError('Fontless PDF was silently accepted')
assert not path.exists()
'''
    process = subprocess.run([sys.executable, "-c", source, str(tmp_path / "bad.pdf")],
                             env={**os.environ, "QT_QPA_PLATFORM": "minimal", "PRIZE_FONT_DIR": ""}, capture_output=True, text=True, timeout=30)
    assert process.returncode == 0, process.stdout + process.stderr


def test_blank_pdf_is_not_accepted_as_searchable(tmp_path):
    path = tmp_path / "blank.pdf"
    writer = PdfWriter()
    writer.add_blank_page(842, 595)
    writer.write(path)
    with pytest.raises(ValueError, match="フォント"):
        require_searchable_pdf(path)
