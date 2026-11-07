"""Versioned, explicit thresholds. Sales comparison never invents a benchmark."""
from copy import deepcopy
import colorsys
import hashlib
import math
import re
import unicodedata
from .core import AppError

DEFAULTS = dict(po_low=.03, po_high=.07, po_reference=.05,
    cost_low=.40, cost_near=.50, cost_high=.70, cost_target=.60,
    sales_mode="benchmark", sales_benchmark=None, sales_low=1000., sales_middle=3000., sales_high=6000.,
    aliases={}, types={}, tolerance_absolute=2., tolerance_relative=.02,
    poor_ratio=50., consider_ratio=100., poor_days=10, consider_days=7, stale_days=7,
    decline_ratio=.8, long_days=30, dashboard_visible=True, fullscreen_dashboard=False)


def validate_settings(value=None):
    if value is not None and (not isinstance(value, dict) or set(value) - set(DEFAULTS)):
        raise AppError("分析設定の形式が不正です。")
    s=deepcopy(DEFAULTS) | deepcopy(value or {})
    for key, default in DEFAULTS.items():
        v=s[key]
        if key in ('aliases','types','sales_mode','dashboard_visible','fullscreen_dashboard'):
            continue
        if key=='sales_benchmark' and v is None:
            continue
        if type(v) not in (float,int) or not math.isfinite(v) or v<0:
            raise AppError(f"分析設定 {key} は0以上の有限数で指定してください。")
    for key in ('poor_days','consider_days','stale_days','long_days'):
        if type(s[key]) is not int or s[key]>100000:
            raise AppError("判定日数は0〜100000の整数で指定してください。")
    if not 0 <= s['po_low'] < s['po_high'] <= 1 or not 0<=s['po_reference']<=1:
        raise AppError("PO率の下限・上限は0〜100%で下限＜上限にしてください。")
    if not 0 <= s['cost_low'] < s['cost_near'] <= s['cost_target'] <= s['cost_high'] <= 1:
        raise AppError("原価率は低すぎる境界＜低め境界≦目標≦高い境界にしてください。")
    if s['sales_mode'] not in ('benchmark','fixed','quantile') or not s['sales_low']<s['sales_middle']<s['sales_high']:
        raise AppError("売上比較方式または固定金額の境界が不正です。")
    if s['sales_benchmark'] is not None and s['sales_benchmark']<=0:
        raise AppError("売上比較用の基準平均売上は正の金額を指定してください（空欄は未設定）。")
    if s['tolerance_relative']>1 or not 0<s['poor_ratio']<s['consider_ratio'] or not 0<s['decline_ratio']<1:
        raise AppError("許容差・入替判定閾値が不正です。")
    for key in ('dashboard_visible','fullscreen_dashboard'):
        if type(s[key]) is not bool:
            raise AppError("ダッシュボード表示設定が不正です。")
    if not isinstance(s['aliases'],dict) or len(s['aliases'])>1000 or any(not isinstance(k,str) or not isinstance(v,str) or not k.strip() or not v.strip() for k,v in s['aliases'].items()):
        raise AppError("機種の表記対応が不正です。")
    if not isinstance(s['types'],dict) or len(s['types'])>1000:
        raise AppError("機種色設定が不正です。")
    for name, item in s['types'].items():
        if not isinstance(name,str) or not isinstance(item,dict) or set(item)!={'color','short'} or not isinstance(item['short'],str) or not isinstance(item['color'],str) or not re.fullmatch('#[0-9a-fA-F]{6}', item['color']):
            raise AppError("機種色は #RRGGBB、短縮名は文字列で指定してください。")
    return s


def normalize_type(value):
    return ' '.join(unicodedata.normalize('NFKC',str(value or '')).split())


def machine_type(machine, settings=None):
    name=normalize_type((machine.analysis or {}).get('machine_name') or machine.kind)
    return (settings or {}).get('aliases',{}).get(name,name)


def type_style(name, settings=None):
    # Stable across sessions and new imports, independent of sort/order or Python hash seed.
    hue=int(hashlib.sha256(name.casefold().encode()).hexdigest()[:8],16)/0xffffffff
    rgb=colorsys.hsv_to_rgb(hue,.28,.97)
    default=dict(color='#'+''.join(f'{round(c*255):02X}' for c in rgb),short=name.replace('UFOキャッチャー','UFO'))
    return (settings or {}).get('types',{}).get(name,default)


def sales_boundaries(machines, settings):
    from .floor_status import effective_stat
    if settings['sales_mode']=='quantile':
        values=sorted(v for m in machines if (v:=effective_stat(m,'average')) is not None and v>=0)
        if not values:
            return None
        return [values[round((len(values)-1)*p)] for p in (.25,.5,.75)]
    if settings['sales_mode']=='fixed':
        return [settings[k] for k in ('sales_low','sales_middle','sales_high')]
    v=settings['sales_benchmark']
    return [v*.5,v,v*2] if v is not None else None
