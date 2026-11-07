"""One conservative resolver for layout identities and performance provenance."""
from collections import defaultdict
from dataclasses import dataclass
import re
import unicodedata
from .core import AppError

MATCH_LABELS = {'direct':'機械番号で直接一致','reference':'管理ナンバーから一致','manual':'管理ナンバーから一致（手動確認済み）','layout':'レイアウト情報のみ','review':'照合要確認'}


def clean(value):
    return re.sub(r'\s+', '', unicodedata.normalize('NFKC',str(value or ''))).replace('－','-').replace('−','-').replace('‐','-')


def normalize_number(value, allow_bare=False):
    text=clean(value)
    m=re.fullmatch(r'(\d+)-(01|02)',text)
    if m:return m[1]+('左' if m[2]=='01' else '右')
    if re.fullmatch(r'\d+[左右]',text):return text
    if allow_bare and re.fullmatch(r'\d+',text):return text
    return None


def normalize_reference(value):
    text=clean(value).upper()
    return text if re.fullmatch(r'[LR]_\d+',text) else None


def machine_key(machine):
    resolved=getattr(machine,'resolved_number','')
    if resolved:return resolved
    if getattr(machine,'number_origin','direct')!='direct':return ''
    return normalize_number(machine.number,allow_bare=True) or ''


def display_identity(machine):
    return machine_key(machine) or normalize_reference(machine.source_key) or machine.source_key or '番号未設定'


def validate_references(rows):
    if not isinstance(rows,list) or len(rows)>10000:raise AppError('管理ナンバー参考表は10000件以下の一覧で指定してください。')
    for r in rows:
        if not isinstance(r,dict) or set(r)!={'source_key','number'} or any(not isinstance(v,str) or len(v)>500 for v in r.values()):raise AppError('管理ナンバー参考表の形式が不正です。')
    return rows


def reference_index(rows):
    validate_references(rows)
    by_key=defaultdict(set);by_number=defaultdict(set);problems=defaultdict(list)
    for row in rows:
        key=normalize_reference(row['source_key']);number=normalize_number(row['number'])
        if not key:continue
        if not number:
            problems[key].append('参考表の機械番号が不正、または左右が不明です。');continue
        by_key[key].add(number);by_number[number].add(key)
    for key,numbers in by_key.items():
        if len(numbers)>1:problems[key].append('同じ管理ナンバーに複数の機械番号があります。')
    for number,keys in by_number.items():
        if len(keys)>1:
            for key in keys:problems[key].append(f'機械番号 {number} が複数の管理ナンバーに対応しています。')
    mapping={key:next(iter(numbers)) for key,numbers in by_key.items() if len(numbers)==1 and not problems.get(key)}
    return mapping,{key:' '.join(dict.fromkeys(values)) for key,values in problems.items()}


def read_reference_rows(workbook):
    rows=[]
    for name in workbook.sheetnames:
        if '管理' not in name or not ('番号' in name or 'ナンバー' in name):continue
        sheet=workbook[name]
        if sheet.max_row>10001:raise AppError('管理ナンバー参考表が10000行を超えています。')
        values=list(sheet.values);key_col,number_col,start=0,1,0
        for i,row in enumerate(values[:10]):
            labels=[clean(v) for v in row]
            a=next((j for j,v in enumerate(labels) if v in ('管理番号','管理ナンバー','参照ID')),None)
            b=next((j for j,v in enumerate(labels) if v in ('機械番号','機器番号','マシン番号')),None)
            if a is not None and b is not None:key_col,number_col,start=a,b,i+1;break
        for row in values[start:]:
            key=row[key_col] if key_col<len(row) else None;number=row[number_col] if number_col<len(row) else None
            if key is None and number is None:continue
            rows.append(dict(source_key=str(key or '').strip(),number=str(number or '').strip()))
    return validate_references(rows)


def prepare_references(machines,rows):
    mapping,problems=reference_index(rows)
    for m in machines:
        key=normalize_reference(m.source_key)
        m.reference_number=mapping.get(key,'');m.reference_warning=problems.get(key,'')
    return problems


@dataclass(frozen=True)
class Match:
    number:str=''
    method:str='layout'
    message:str=''
    matched:bool=False


def resolve_matches(machines,records):
    results={};claims=defaultdict(list)
    keys=defaultdict(list)
    for m in machines:
        if normalize_reference(m.source_key):keys[normalize_reference(m.source_key)].append(m.uid)
    for m in machines:
        manual=getattr(m,'manual_number',None)
        direct=normalize_number(m.number) if getattr(m,'number_origin','direct')=='direct' else None
        ref=getattr(m,'reference_number','')
        problem=getattr(m,'reference_warning','')
        method='direct' if direct else 'reference';number=direct or ref or ''
        if manual is not None:
            number=normalize_number(manual) if manual else '';method='manual'
            if manual and not number:problem='手動対応の機械番号が不正、または左右が不明です。'
            else:problem=''
        else:
            if m.number and getattr(m,'number_origin','direct')=='direct' and not direct:problem='機械番号が不正、または左右が不明です。'
            if direct and ref and direct!=ref:problem='直接の機械番号と管理ナンバー参考表の対応が異なります。'
            if len(keys.get(normalize_reference(m.source_key),[]))>1:problem='同じ管理ナンバーのマシン枠が複数あります。'
        if problem:
            results[m.uid]=Match('', 'review',problem);continue
        if number:claims[number].append(m.uid)
        matched=bool(number and number in records)
        results[m.uid]=Match(number,method if matched else 'layout',
            MATCH_LABELS[method] if matched else ('対応する機械番号の最新実績がありません。' if number else '対応する機械番号がありません。'),matched)
    for number,uids in claims.items():
        if len(uids)>1:
            for uid in uids:results[uid]=Match('','review',f'機械番号 {number} が複数のマシン枠へ割り当てられています。')
    return results


def value_source(machine,key):
    if key=='ratio':
        from .floor_status import effective_stat,reference_average
        if machine.analysis and effective_stat(machine,'average') is not None and reference_average(machine) is not None:
            return '景品運営分析表（既存基準から算出）'
        return 'レイアウト由来' if machine.ratio is not None or machine.sales is not None and machine.average is not None and machine.average>0 else 'データなし'
    if machine.analysis:
        from .floor_status import effective_stat
        return '景品運営分析表' if effective_stat(machine,key) is not None else '景品運営分析表：未取得'
    return 'レイアウト由来' if getattr(machine,key,None) is not None else 'データなし'
