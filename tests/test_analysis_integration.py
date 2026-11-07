from copy import deepcopy
from dataclasses import asdict
from datetime import date
import hashlib
import pytest
from openpyxl import load_workbook
from prize_swap.sales_analysis import load_analysis, apply_snapshot, validate_snapshot
from prize_swap.performance_validation import reconcile, period_text
from prize_swap.floor_data import Machine, validate_state
from prize_swap.floor_status import effective_stat, ratio
from test_sales_analysis import report_file, data_row


def test_metadata_reordered_headers_and_fraction_units(tmp_path):
    p = report_file(tmp_path / '分析_202611070619.xlsx', [data_row(),
        [None, '期間(自)', '2026/11/01'], [None, '期間(至)', '2026/11/06'],
        [None, '検索日時: 2026/11/07 18:19:35']])
    w = load_workbook(p)
    s = w.active
    for row in s:
        a,b = row[1].value,row[7].value
        row[1].value,row[7].value=b,a
    w.save(p);w.close()
    before = hashlib.sha256(p.read_bytes()).hexdigest()
    v = load_analysis(p)
    r = v['records']['20088左']
    assert r['machine_name'] == '機器'
    assert r['values']['po_rate'] == .08 and r['values']['cost_rate'] == .3333
    assert v['period_start'] == '2026-11-01' and v['period_end'] == '2026-11-06'
    assert v['extracted_at'] == '2026-11-07T18:19:35+09:00'
    assert v['filename_timestamp'] == '2026-11-07T06:19:00'
    assert hashlib.sha256(p.read_bytes()).hexdigest() == before
    assert '2026/11/01' in period_text(v)


def test_unknown_period_no_inference_and_missing_no_silent_fallback(tmp_path):
    row = data_row();row[10] = None
    v=load_analysis(report_file(tmp_path/'analysis_202611070619.xlsx',[row]))
    assert v['period_start'] is None and v['extracted_at'] is None
    m=Machine(number='20088左', po_rate=.2, average=2000, ratio=100)
    n=Machine(number='29999右', sales=200, average=100, ratio=200)
    apply_snapshot([m,n],v)
    assert effective_stat(m,'po_rate') is None
    assert effective_stat(n,'sales') is None and ratio(n) is None
    assert n.sales == 200


def test_issues_duplicates_invalid_id_range_consistency_and_reconciliation(tmp_path):
    row=data_row();row[10]=1.5;row[5]=123
    bad=data_row('20088-03')
    v=load_analysis(report_file(tmp_path/'x.xlsx',[row,deepcopy(row),bad]))
    issues=reconcile([Machine(number='29999右')],v,v)
    assert {'duplicate','range','consistency','number_format','unmatched','absent','reimport'} <= {i['kind'] for i in issues}
    assert v['unmatched_rows'][0]['number'] == '20088-03'
    assert any(i['severity']=='エラー' for i in issues)


def test_tolerance_and_legacy_snapshot_migration(tmp_path):
    row=data_row();row[5]=30010
    p=report_file(tmp_path/'x.xlsx',[row])
    assert any(i['kind']=='consistency' for i in load_analysis(p,dict(absolute=0,relative=0))['issues'])
    assert not any(i['kind']=='consistency' for i in load_analysis(p,dict(absolute=11,relative=0))['issues'])
    v=load_analysis(p);old=deepcopy(v);old['version']=1
    old['records']['20088左']['values']['po_rate']=8
    old['records']['20088左']['values']['cost_rate']=33.33
    migrated=validate_snapshot(old)
    assert migrated['records']['20088左']['values']['po_rate']==.08
    assert old['records']['20088左']['values']['po_rate']==8
