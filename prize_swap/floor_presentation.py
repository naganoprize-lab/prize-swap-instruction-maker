"""Pure screen projections. No display operation writes physical coordinates."""

import math
import re
import unicodedata
from .analysis_settings import machine_type
from .machine_matching import machine_key, display_identity
from PySide6.QtCore import QRectF
from .core import AppError
from .floor_status import ratio, days, COLOR_FILTERS

DEFAULTS = dict(
    mode="actual",
    color_filter="ratio",
    sort="number",
    descending=False,
    search="",
    filter="all",
    min_days=None,
    max_days=None,
    width=260,
    height=200,
    font=14,
    legend=True,
    edit=False,
)


def validate_presentation(value):
    if not isinstance(value, dict) or any(k not in DEFAULTS for k in value):
        raise AppError("売り場表示設定が不正です。")
    settings = DEFAULTS | value
    if settings["color_filter"] not in COLOR_FILTERS:
        raise AppError("色フィルターが不正です。")
    if (
        settings["mode"] not in ("actual", "readable", "list")
        or settings["sort"] not in ("number", "ratio", "days", "product")
        or settings["filter"] not in ("all", "poor", "high", "planned")
    ):
        raise AppError("売り場の表示方式・並べ替え・絞り込みが不正です。")
    for key in ("descending", "legend", "edit"):
        if type(settings[key]) is not bool:
            raise AppError("表示切替の値が不正です。")
    if not isinstance(settings["search"], str):
        raise AppError("検索文字が不正です。")
    for key in ("min_days", "max_days"):
        if settings[key] is not None and (
            type(settings[key]) is not int or not 0 <= settings[key] <= 100000
        ):
            raise AppError("投入日数の絞り込みが不正です。")
    if (
        settings["min_days"] is not None
        and settings["max_days"] is not None
        and settings["min_days"] > settings["max_days"]
    ):
        raise AppError("投入日数の下限は上限以下にしてください。")
    for key, lo, hi in (("width", 80, 2000), ("height", 60, 2000), ("font", 6, 72)):
        v = settings[key]
        if (
            isinstance(v, bool)
            or not isinstance(v, (int, float))
            or not math.isfinite(v)
            or not lo <= v <= hi
        ):
            raise AppError("表示カードのサイズが不正です。")
    return settings


def ordered_machines(machines, key="number", descending=False, reference=None):
    def natural(value):
        return tuple(
            (0, int(p)) if p.isdigit() else (1, p.casefold()) for p in re.split(r"(\d+)", value)
        )

    def value(m):
        if key == "ratio":
            return ratio(m)
        if key == "days":
            return days(m, reference)
        if key == "product":
            return m.product.casefold()
        return natural(display_identity(m))

    known = [m for m in machines if value(m) is not None]
    missing = [m for m in machines if value(m) is None]
    return sorted(known, key=value, reverse=descending) + missing


def visible_machines(machines, assignments, settings, reference):
    """Display filters never remove a machine or change the projection."""
    return list(machines)


def matching_machines(machines, assignments, settings, reference, analysis_settings=None):
    def normalized(value):
        return "".join(unicodedata.normalize("NFKC", value).split()).casefold()

    needle = normalized(settings["search"])
    result = []
    for m in machines:
        if needle and needle not in normalized(
            " ".join(
                (
                    machine_key(m),
                    m.product,
                    m.source_key,
                    machine_type(m, analysis_settings),
                    m.operation_method,
                    " ".join(
                        p.machine_methods.get(machine_key(m), "")
                        for p in assignments.get(machine_key(m), [])
                    ),
                )
            )
        ):
            continue
        value = ratio(m)
        kind = settings["filter"]
        if kind == "poor" and (value is None or value >= 50):
            continue
        if kind == "high" and (value is None or value < 200):
            continue
        if kind == "planned" and not assignments.get(machine_key(m)):
            continue
        age = days(m, reference)
        if settings["min_days"] is not None and (age is None or age < settings["min_days"]):
            continue
        if settings["max_days"] is not None and (age is None or age > settings["max_days"]):
            continue
        result.append(m)
    return result


def project_machines(machines, settings, reference, viewport_width=1200):
    if settings["mode"] == "actual":
        return {m.uid: QRectF(m.x, m.y, m.width, m.height) for m in machines}
    w, h = settings["width"], settings["height"]
    if settings["mode"] == "readable":
        scale = max([1, *[w / m.width for m in machines], *[h / m.height for m in machines]])
        return {
            m.uid: QRectF(
                m.x * scale, m.y * scale, max(w, m.width * scale), max(h, m.height * scale)
            )
            for m in machines
        }
    columns = max(1, int(viewport_width // (w + 16)))
    ordered = ordered_machines(machines, settings["sort"], settings["descending"], reference)
    return {
        m.uid: QRectF(i % columns * (w + 16), i // columns * (h + 16), w, h)
        for i, m in enumerate(ordered)
    }
