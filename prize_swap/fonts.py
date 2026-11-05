"""Explicit font bootstrap for Qt's Windows offscreen FreeType database.

Unlike the native windows platform plugin, Qt 6.8's offscreen plugin does not
scan the Windows font registry. Its directory scan also omits .ttc collections.
Register file paths (not in-memory bytes) so the PDF engine can embed the faces.
"""
from pathlib import Path
import os
import sys

from PySide6.QtGui import QFont, QFontDatabase, QGuiApplication, QRawFont

from .core import AppError

LATIN_FAMILIES = ("Arial", "Liberation Sans", "DejaVu Sans")
JAPANESE_FAMILIES = ("Yu Gothic", "Yu Gothic UI", "Meiryo", "MS Gothic", "Noto Sans CJK JP", "Noto Sans JP", "IPAGothic")
WINDOWS_FONT_FILES = ("arial.ttf", "arialbd.ttf", "meiryo.ttc", "meiryob.ttc", "YuGothR.ttc", "YuGothB.ttc", "msgothic.ttc")


def initialize_fonts():
    app = QGuiApplication.instance()
    if app is None:
        raise AppError("フォント初期化にはQApplicationが必要です。")
    if hasattr(app, "_prize_font_report"):
        return app._prize_font_report
    before = QFontDatabase.families()
    registrations = []
    paths = []
    if sys.platform == "win32" and app.platformName() == "offscreen":
        directory = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
        paths.extend(directory / name for name in WINDOWS_FONT_FILES)
    if os.environ.get("PRIZE_FONT_DIR"):
        directory = Path(os.environ["PRIZE_FONT_DIR"])
        paths.extend(p for p in sorted(directory.iterdir()) if p.suffix.lower() in (".ttf", ".ttc", ".otf"))
    for path in paths:
        if path.is_file():
            font_id = QFontDatabase.addApplicationFont(str(path))
            registrations.append(dict(file=path.name, registered=font_id >= 0,
                families=QFontDatabase.applicationFontFamilies(font_id) if font_id >= 0 else []))
    after = QFontDatabase.families()
    report = dict(platform=sys.platform, qt_platform=app.platformName(),
                  families_before=len(before), families_after=len(after), registrations=registrations,
                  available_candidates=[f for f in (*LATIN_FAMILIES, *JAPANESE_FAMILIES) if f in after])
    app._prize_font_report = report
    return report


def print_font(size=11, bold=True):
    initialize_fonts()
    families = QFontDatabase.families()
    latin = next((f for f in LATIN_FAMILIES if f in families), None)
    japanese = next((f for f in JAPANESE_FAMILIES if f in families), None)
    if latin is None or japanese is None:
        raise AppError("印刷用の英数字・日本語フォントが見つかりません。WindowsのArialとメイリオ／游ゴシック、またはNoto Sans CJK JP等を利用可能にしてください。")
    # Explicit fallback also works with the offscreen FreeType database, which
    # has no Windows registry fallback configuration. Prefer Latin for digits.
    font = QFont()
    font.setFamilies([latin, japanese])
    font.setPixelSize(size)  # Logical print coordinates are points.
    font.setWeight(QFont.Weight.DemiBold if bold else QFont.Weight.Normal)
    return font


def validate_print_fonts():
    report = dict(initialize_fonts())
    font = print_font()
    japanese = font.families()[1]
    for family, sample in ((font.families()[0], "ABC0123456789"), (japanese, "商品名入替指示書橋渡し左右①⑩")):
        candidate = QFont(family)
        candidate.setPixelSize(11)
        raw = QRawFont.fromFont(candidate)
        if not raw.isValid() or not all(raw.supportsCharacter(ord(c)) for c in sample):
            raise AppError(f"印刷用フォント「{family}」に必要な文字がありません。")
    report["selected_families"] = font.families()
    return report
