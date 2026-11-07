"""Missing-aware operational values; reference dates never alter sales data."""

import math
import re
import unicodedata

from .core import parse_date

COLORS = {
    "planned": "#A9D7FF",
    "high": "#FFE16A",
    "active": "#94D8A0",
    "low": "#FFFFFF",
    "poor": "#F4A6A6",
    "unknown": "#FFFFFF",
    "aged": "#C5A5ED",
}

COLOR_FILTERS = {"ratio": "アベ比", "planned": "投入予定地", "age": "設置日数"}
FILTER_FIELDS = {
    "ratio": ("number", "ratio", "product"),
    "planned": ("number", "ratio", "product"),
    "age": ("number", "days", "product"),
}


def filter_status(machine, mode, reference, planned=False, priority=False):
    if mode == "ratio":
        return machine_status(machine, planned, priority)
    if mode == "planned":
        colour = COLORS["planned"] if planned else COLORS["low"]
        label = "投入予定" if planned else "投入予定なし"
    elif mode == "age":
        age = days(machine, reference)
        colour = COLORS["aged"] if age is not None and age >= 10 else COLORS["low"]
        label = "設置日数 " + days_text(machine, reference)
        if planned:
            label += " / 投入予定"
    else:
        raise ValueError("色フィルターが不正です。")
    if priority:
        label += " / 優先入替"
    return colour, label


def filter_legend(mode):
    return {
        "ratio": "青：投入予定／黄枠：優先入替／黄：高稼働（200%以上）／緑：100〜200%未満／白：50〜100%未満・不明／赤：低稼働（50%未満）",
        "planned": "青：投入予定地／白：投入予定なし／黄枠：優先入替（アベ比による色分けは無効）",
        "age": "紫：設置10日以上／白：10日未満・不明・未来／黄枠：優先入替",
    }[mode]


def numeric(value):
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, str):
        value = (
            unicodedata.normalize("NFKC", value)
            .strip()
            .replace(",", "")
            .replace("¥", "")
            .replace("￥", "")
        )
        if value.endswith("%"):
            value = value[:-1]
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def percent(value, number_format="General"):
    number = numeric(value)
    if number is None:
        return None
    is_string = isinstance(value, str) and "%" in unicodedata.normalize("NFKC", value)
    fmt = re.sub(r'"[^"]*"|\\.', "", number_format)
    return number * 100 if "%" in fmt and not is_string else number


def effective_stat(machine, key):
    overlay = getattr(machine, "analysis", None)
    if overlay:
        return overlay.get("values", {}).get(key)
    return getattr(machine, key, None)


def reference_average(machine):
    value = getattr(machine, "reference_average", None)
    if value is not None:
        return value if value > 0 and math.isfinite(value) else None
    if machine.average is not None and machine.ratio is not None and machine.ratio > 0:
        value = machine.average * 100 / machine.ratio
        return value if value > 0 and math.isfinite(value) else None
    if (
        machine.ratio is None
        and not machine.ratio_from_excel
        and machine.average is not None
        and machine.average > 0
    ):
        return machine.average
    return None


def ratio(machine):
    if getattr(machine, "analysis", None):
        average = effective_stat(machine, "average")
        reference = reference_average(machine)
        if average is None or reference is None:
            return None
        value = average / reference * 100
        return value if math.isfinite(value) else None
    if machine.ratio is not None or machine.ratio_from_excel:
        return machine.ratio
    if machine.sales is None or machine.average is None or machine.average <= 0:
        return None
    value = machine.sales / machine.average * 100
    return value if math.isfinite(value) else None


def days(machine, reference):
    installed = parse_date(machine.installed_on)
    if installed is None:
        return None
    return (reference - installed).days


def days_text(machine, reference):
    value = days(machine, reference)
    return "—" if value is None else "投入予定" if value < 0 else f"{value}日"


def money(value):
    return (
        "—" if value is None else f"{value:,.0f}" if float(value).is_integer() else f"{value:,.2f}"
    )


def ratio_text(machine):
    value = ratio(machine)
    return "—" if value is None else f"{value:,.1f}%"


def machine_status(machine, planned=False, priority=False):
    value = ratio(machine)
    if value is None:
        base = "unknown"
    elif value >= 200:
        base = "high"
    elif value >= 100:
        base = "active"
    elif value >= 50:
        base = "low"
    else:
        base = "poor"
    label = {
        "unknown": "アベ比不明",
        "high": "高稼働（200%以上）",
        "active": "稼働100%以上",
        "low": "50%以上100%未満",
        "poor": "低稼働（50%未満）",
    }[base]
    if planned:
        label = "投入予定 / " + label
    if priority:
        label += " / 優先入替"
    return COLORS["planned" if planned else base], label


def overlapping(machines):
    """Sweep across x so ordinary nonoverlapping 298-card layouts stay cheap."""
    result = set()
    active = []
    for m in sorted(machines, key=lambda item: item.x):
        active = [a for a in active if a.x + a.width > m.x + 0.01]
        for a in active:
            if min(a.y + a.height, m.y + m.height) > max(a.y, m.y) + 0.01:
                result.update((a.uid, m.uid))
        active.append(m)
    return result
