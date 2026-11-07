"""Explainable assessments; no instruction assignment is changed by evaluation."""
from .analysis_settings import validate_settings
from .floor_status import ratio, days, effective_stat


def assess(machine, reference, settings=None, history=()):
    s=validate_settings(settings);value=ratio(machine);age=days(machine,reference)
    status,priority='判定保留',3
    reasons=[]
    if value is None or age is None or age<0:
        reasons.append('アベ比・設置日などの有効データが不足しています。')
    elif value<s['poor_ratio'] and age>=s['poor_days']:
        status,priority='要確認',0
        reasons.append(f'投入{age}日・アベ比{value:.1f}%のため要確認')
    elif s['poor_ratio']<=value<s['consider_ratio'] and age>=s['consider_days']:
        status,priority='入替検討',1
        reasons.append(f'投入{age}日・アベ比{value:.1f}%のため入替検討')
    elif value>=s['consider_ratio']:
        status,priority='好調',2;reasons.append(f'アベ比{value:.1f}%で好調')
    else:reasons.append('投入日数が判定条件に達していません。')
    return dict(machine_id=machine.number,uid=machine.uid,status=status,priority=priority,reasons=reasons,trend=None,source_record_ids=[])
