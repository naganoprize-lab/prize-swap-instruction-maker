"""Shared financial and age bands for cards, distributions and printed legends."""

SALES_LABELS = ("低売上", "やや低売上", "中程度", "高売上", "特に高売上")
SALES_COLORS = ("#F4A6A6", "#F8C88A", "#F3F4F6", "#94D8A0", "#FFE16A")
UNKNOWN = "#AEB7C2"
PO_LABELS = ("適正より低い", "やや低い", "適正範囲", "やや高い", "適正より高い")
PO_COLORS = ("#8FC5F4", "#C5E2FA", "#94D8A0", "#F7C2C2", "#F4A6A6")
COST_LABELS = ("低すぎる", "目標より低い", "適正範囲", "目標より高い", "高すぎる")
COST_COLORS = ("#8FC5F4", "#C5E2FA", "#94D8A0", "#F8C88A", "#F4A6A6")
AGE_LABELS = ("0〜6日", "7〜9日", "10〜13日", "14〜20日", "21日以上")
AGE_COLORS = ("#F3F4F6", "#F3F4F6", "#C5A5ED", "#C5A5ED", "#C5A5ED")


def cost_band(value, s):
    if value is None:
        return UNKNOWN, "不明"
    i = (
        0
        if value < s["cost_low"]
        else (
            1
            if value < s["cost_near"]
            else 2 if value <= s["cost_high"] else 3 if value <= s["cost_very_high"] else 4
        )
    )
    return COST_COLORS[i], COST_LABELS[i]


def age_band(value):
    if value is None:
        return UNKNOWN, "不明"
    if value < 0:
        return "#F3F4F6", "投入予定"
    i = 0 if value < 7 else 1 if value < 10 else 2 if value < 14 else 3 if value < 21 else 4
    return AGE_COLORS[i], AGE_LABELS[i]


def po_band(relative, s):
    t, o = s["po_tolerance"], s["po_outer_tolerance"]
    i = (
        0
        if relative < -o - 1e-12
        else (
            1
            if relative < -t - 1e-12
            else 2 if relative <= t + 1e-12 else 3 if relative <= o + 1e-12 else 4
        )
    )
    return PO_COLORS[i], PO_LABELS[i]
