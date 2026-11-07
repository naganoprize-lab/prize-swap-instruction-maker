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
    "unknown": "#B8BEC6",
    "aged": "#C5A5ED",
}

COLOR_FILTERS = {"ratio": "アベ比", "planned": "投入予定地", "age": "設置日数", "po_rate": "PO率", "cost_rate": "原価率", "sales_metric": "売上", "machine_type": "機器名・機種", "assessment": "入替候補"}
FILTER_FIELDS = {
    "ratio": ("number", "ratio", "product"),
    "planned": ("number", "ratio", "product"),
    "age": ("number", "days", "product"),
    **{k: ("number", k, "product") for k in ("po_rate", "cost_rate", "sales_metric", "machine_type", "assessment")},
}


def filter_status(machine, mode, reference, planned=False, priority=False, settings=None):
    from .analysis_settings import validate_settings, machine_type, type_style
    bounds = (settings or {}).get("_sales_bounds")
    settings = validate_settings({k:v for k,v in (settings or {}).items() if k != "_sales_bounds"})
    if bounds is not None:
        settings["_sales_bounds"] = bounds
    if mode == "ratio":
        colour, label = machine_status(machine, False, priority)
        return colour, label + (" / 投入予定" if planned else "")
    if mode == "planned":
        colour = COLORS["planned"] if planned else COLORS["low"]
        label = "投入予定" if planned else "投入予定なし"
    elif mode == "age":
        age = days(machine, reference)
        colour = COLORS["unknown"] if age is None else COLORS["aged"] if age >= 10 else COLORS["low"]
        label = "設置日数 " + days_text(machine, reference)
        if planned:
            label += " / 投入予定"
    elif mode in ("po_rate", "cost_rate", "sales_metric", "machine_type", "assessment"):
        value = effective_stat(machine, mode)
        colour = COLORS["unknown"]
        label = metric_text(machine, mode, reference, settings)
        if mode == "po_rate" and value is not None:
            colour = "#8FC5F4" if value < settings["po_low"] else "#94D8A0" if value <= settings["po_high"] else "#F4A6A6"
        elif mode == "cost_rate" and value is not None:
            colour = "#8FC5F4" if value < settings["cost_low"] else "#C5E2FA" if value < settings["cost_near"] else "#94D8A0" if value <= settings["cost_high"] else "#F4A6A6"
            if value < settings["cost_low"]:
                label += " / 原価率要確認"
        elif mode == "sales_metric":
            value = effective_stat(machine, "average")
            bounds = settings.get("_sales_bounds")
            if bounds is None:
                from .analysis_settings import sales_boundaries
                bounds = sales_boundaries([machine], settings) if settings["sales_mode"] != "quantile" else None
            if value is not None and bounds is not None:
                colour = "#F4A6A6" if value < bounds[0] else "#F7D5A0" if value < bounds[1] else "#94D8A0" if value < bounds[2] else "#FFE16A"
            elif bounds is None:
                label += " / 比較基準未設定"
        elif mode == "machine_type":
            name = machine_type(machine, settings)
            colour = type_style(name, settings)["color"] if name and name != "クレーン" else COLORS["unknown"]
        elif mode == "assessment":
            from .replacement import assess
            result = assess(machine, reference, {k:v for k,v in settings.items() if k != "_sales_bounds"})
            label = result["status"] + " / " + result["reasons"][0]
            colour = {"要確認": "#F4A6A6", "入替検討": "#FFE16A", "好調": "#94D8A0", "判定保留": COLORS["unknown"]}[result["status"]]
        if planned:
            label += " / 投入予定"
    else:
        raise ValueError("色フィルターが不正です。")
    if priority:
        label += " / 優先入替"
    return colour, label


def filter_legend(mode, settings=None):
    from .analysis_settings import validate_settings, type_style
    s=validate_settings(settings)
    return {
        "ratio": "黄：200%以上／緑：100〜200%未満／白：50〜100%未満／赤：低稼働（50%未満）／灰：不明／投入予定は青マーカー",
        "planned": "青：投入予定地／白：投入予定なし／黄枠：優先入替",
        "age": "紫：設置10日以上／白：10日未満・未来／灰：設置日不明",
        "po_rate": f"青：{s['po_low']:.1%}未満／緑：{s['po_low']:.1%}〜{s['po_high']:.1%}／赤：超過／基準 {s['po_reference']:.1%}／灰：不明",
        "cost_rate": f"青：{s['cost_low']:.0%}未満（要確認）／薄青：{s['cost_near']:.0%}未満／緑：{s['cost_high']:.0%}以下／赤：超過／目標 {s['cost_target']:.0%}／灰：不明",
        "sales_metric": "平均売上で比較（アベ比と独立）／赤・薄橙・緑・黄／比較方式：" + {"benchmark":"設定基準額への比率", "fixed":"設定固定金額", "quantile":"最新有効実績の四分位"}[s['sales_mode']] + "／灰：値・基準不明",
        "machine_type": "機種ごとの固定色（略称と枠線でも識別）／灰：不明 " + "、".join(f"{k} {v['color']}" for k,v in s['types'].items()),
        "assessment": "赤：要確認／黄：入替検討／緑：好調／灰：判定保留（確定は手動）",
    }[mode]


def metric_text(machine, key, reference, settings=None):
    from .analysis_settings import machine_type, type_style
    if key in ('po_rate','cost_rate'):
        value=effective_stat(machine,key)
        return ('PO率 ' if key=='po_rate' else '原価率 ') + ('—' if value is None else f'{value*100:.1f}%')
    if key=='sales_metric':
        return f"平均売上 {money(effective_stat(machine,'average'))}円\n総売上 {money(effective_stat(machine,'sales'))}円"
    if key=='machine_type':
        name=machine_type(machine,settings)
        return type_style(name,settings)['short'] if name and name!='クレーン' else '機種 —'
    if key=='assessment':
        from .replacement import assess
        return assess(machine,reference,settings)['status']
    raise ValueError(key)


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
