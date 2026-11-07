from copy import deepcopy
from dataclasses import asdict
from datetime import date
import hashlib
import pytest
from openpyxl import Workbook
from prize_swap.floor_data import Machine,import_layout,save_layout
from prize_swap.machine_matching import normalize_number,normalize_reference,resolve_matches,prepare_references,display_identity,value_source
from prize_swap.floor_status import ratio,effective_stat,filter_status
from prize_swap.sales_analysis import apply_snapshot,load_analysis,import_summary
from prize_swap.floor import FloorWidget
from test_sales_analysis import report_file,data_row

DAY=date(2026,11,7)

@pytest.mark.parametrize('raw,expected',[('20111-01','20111左'),('20111-02','20111右'),(' ２０１１１－０２　','20111右'),('２０１１１ 右','20111右'),('20111左','20111左'),('20111',''),('20111-03','')])
def test_strict_normalization(raw,expected):assert (normalize_number(raw) or '')==expected


def snapshot(tmp_path):return load_analysis(report_file(tmp_path/'分析.xlsx',[data_row('20111-02',average=6000,sales=18000)]))


def test_management_reference_match_and_independent_identifiers(tmp_path):
    m=Machine(source_key=' Ｌ_６６ ',product='既存商品',average=4250,ratio=85)
    prepare_references([m],[dict(source_key='L_66',number='２０１１１－０２')]);apply_snapshot([m],snapshot(tmp_path))
    assert m.number=='' and m.resolved_number=='20111右' and m.match_method=='reference'
    assert display_identity(m)=='20111右' and ratio(m)==120 and m.product=='既存商品'
    assert value_source(m,'sales')=='景品運営分析表'
    assert import_summary([m],snapshot(tmp_path))['matched']==1


def test_direct_priority_and_unmatched_layout_metric_fallback(tmp_path):
    m=Machine(number='20111右',source_key='R_66',average=5000,ratio=100)
    prepare_references([m],[dict(source_key='R_66',number='20111右')]);apply_snapshot([m],snapshot(tmp_path))
    assert m.match_method=='direct' and ratio(m)==120
    m=Machine(source_key='L_99',ratio=85,sales=1000,product='レイアウト商品')
    apply_snapshot([m],snapshot(tmp_path))
    assert m.match_method=='layout' and m.analysis is None and ratio(m)==85 and effective_stat(m,'sales')==1000
    assert filter_status(m,'ratio',DAY)[0]=='#FFFFFF' and value_source(m,'sales')=='レイアウト由来'
    assert display_identity(m)=='L_99'
    m.ratio=None;m.sales=None
    assert ratio(m) is None and filter_status(m,'ratio',DAY)[0]=='#B8BEC6'


@pytest.mark.parametrize('rows',[
    [('L_66','20111右'),('L_66','20088左')],
    [('L_66','20111右'),('R_66','20111右')],
    [('L_66','20111')],
    [('L_66','20111-03')],
])
def test_ambiguous_catalog_never_attaches_other_performance(tmp_path,rows):
    m=Machine(source_key='L_66',ratio=85)
    prepare_references([m],[dict(source_key=k,number=n) for k,n in rows]);apply_snapshot([m],snapshot(tmp_path))
    assert m.match_method=='review' and m.analysis is None and ratio(m)==85
    assert import_summary([m],snapshot(tmp_path))['matched']==0


def test_direct_reference_conflict_and_duplicate_frames_require_review(tmp_path):
    a=Machine(number='20111右',source_key='L_66',ratio=50)
    prepare_references([a],[dict(source_key='L_66',number='20088左')]);apply_snapshot([a],snapshot(tmp_path))
    assert a.match_method=='review' and a.analysis is None
    a,b=Machine(source_key='L_66'),Machine(source_key='L_66')
    prepare_references([a,b],[dict(source_key='L_66',number='20111右')]);apply_snapshot([a,b],snapshot(tmp_path))
    assert all(m.match_method=='review' for m in (a,b))
    a,b=Machine(number='20111右'),Machine(source_key='L_66')
    prepare_references([a,b],[dict(source_key='L_66',number='20111右')]);apply_snapshot([a,b],snapshot(tmp_path))
    assert all(m.analysis is None for m in (a,b))


def test_matched_missing_fields_stay_missing_and_unknown_baseline_not_invented(tmp_path):
    m=Machine(number='20111右',po_rate=.1,ratio=85,average=4250)
    s=snapshot(tmp_path);s['records']['20111右']['values']['po_rate']=None
    apply_snapshot([m],s);assert effective_stat(m,'po_rate') is None and ratio(m)==120
    m=Machine(number='20111右');apply_snapshot([m],s);assert ratio(m) is None


def test_manual_resolution_roundtrip_preserves_layout_and_undo(tmp_path):
    w=FloorWidget(lambda:[],lambda:DAY,tmp_path/'auto.json');other=None
    try:
        w.machines=[Machine(source_key='L_66',ratio=85,x=123,y=456)];w.rebuild();w.import_analysis_path(report_file(tmp_path/'分析.xlsx',[data_row('20111-02')]))
        uid=w.machines[0].uid;node=w.nodes[uid];before=deepcopy(w.state())
        w.apply_matching([dict(source_key='L_66',number='20111右')],{uid:'20111右'})
        assert w.nodes[uid] is node and w.machines[0].match_method=='manual'
        assert w.machines[0].x==123 and w.machines[0].y==456 and w.machines[0].source_key=='L_66'
        path=tmp_path/'saved.json';save_layout(path,w.state());other=FloorWidget(lambda:[],lambda:DAY,path)
        assert other.machines[0].manual_number=='20111右' and other.machines[0].match_method=='manual'
        w.history.undo();assert w.machines[0].manual_number is None and ratio(w.machines[0])==85
        w.history.redo();assert w.machines[0].manual_number=='20111右'
    finally:
        for item in (w,other):
            if item:item.timer.stop();item.day_timer.stop();item.close()


def test_reference_excel_headers_unicode_and_source_unchanged(tmp_path):
    p=tmp_path/'レイアウト.xlsx';book=Workbook();s=book.active;s.title='投入中レイアウト';s['B3']=' Ｌ_６６ ';s['C3']='現在商品'
    s['B4']='アベ比';s['C4']=.85;s['C4'].number_format='0%'
    refs=book.create_sheet('管理ナンバー参考表');refs.append(['機器番号','メモ','管理ナンバー']);refs.append(['２０１１１－０２','使用しない','L_66']);book.save(p);book.close()
    digest=hashlib.sha256(p.read_bytes()).hexdigest();machines,warnings,rows=import_layout(p,with_references=True)
    assert len(machines)==1 and rows==[dict(source_key='L_66',number='２０１１１－０２')]
    apply_snapshot(machines,snapshot(tmp_path));assert machines[0].match_method=='reference' and ratio(machines[0])==85 # Stored layout ratio is identified as layout-derived; no new baseline is invented.
    assert hashlib.sha256(p.read_bytes()).hexdigest()==digest


@pytest.mark.parametrize('direct,method',[('２０１１１－０２','direct'),('20111','review'),('20088左','review')])
def test_explicit_layout_number_not_product_name_controls_matching(tmp_path,direct,method):
    p=tmp_path/'layout.xlsx';book=Workbook();s=book.active;s.title='投入中レイアウト'
    s['B3']='L_66';s['C3']='20111右' # A product name that resembles a number must not create another card.
    s['B4']='機械番号';s['C4']=direct
    refs=book.create_sheet('管理番号参考値');refs.append(['L_66','20111右']);book.save(p);book.close()
    machines,_=import_layout(p);assert len(machines)==1
    apply_snapshot(machines,snapshot(tmp_path));assert machines[0].match_method==method
    if method=='review':assert machines[0].analysis is None
    assert machines[0].product=='20111右'


def test_manual_match_shared_by_details_dashboard_methods_candidates_and_json(tmp_path):
    from prize_swap.floor_details import MachineDetailDialog
    from prize_swap.method_analysis import totals
    from prize_swap.replacement import assess
    from prize_swap.core import Product
    product=Product(DAY,'入替予定',machines=['20111右'])
    w=FloorWidget(lambda:[product],lambda:DAY,tmp_path/'auto.json')
    try:
        w.machines=[Machine(source_key='L_66',product='現商品',operation_method='リング',ratio=85,average=4250,installed_on='2026-10-01')];w.rebuild();w.apply_matching([dict(source_key='L_66',number='20111右')],{})
        w.import_analysis_path(report_file(tmp_path/'分析.xlsx',[data_row('20111-02',average=6000,sales=18000)]))
        m=w.machines[0];detail=MachineDetailDialog(w,m)
        assert detail.values['sales'].text()=='18,000' and detail.values['ratio'].text()=='120.0%'
        assert detail.values['match_method'].text()=='管理ナンバーから一致' and len(detail.products)==1;detail.close()
        assert w.dashboard.metrics['planned']==1 and w.dashboard.groups['100〜200%未満']['count']==1
        assert totals(w.machines)[0]['sales']==18000
        assert assess(m,DAY)['machine_id']=='20111右'
        assert w.state()['number_references']==[dict(source_key='L_66',number='20111右')]
    finally:w.timer.stop();w.day_timer.stop();w.close()


def test_analysis_refresh_retains_nodes_selection_zoom_and_metric_provenance(tmp_path):
    from prize_swap.machine_matching import metric_info
    w=FloorWidget(lambda:[],lambda:DAY,tmp_path/'auto.json')
    try:
        w.machines=[Machine(number='20111右',source_key='R_66',ratio=85,average=4250,x=123,y=456,layout_loaded_at='2026-11-01T10:00:00+09:00')];w.rebuild()
        uid=w.machines[0].uid;node=w.nodes[uid];node.setSelected(True);w.set_zoom(125)
        w.import_analysis_path(report_file(tmp_path/'分析.xlsx',[data_row('20111-02',average=6000,sales=18000)]))
        assert w.nodes[uid] is node and node.isSelected() and w._zoom==125
        info=metric_info(w.machines[0],'ratio');assert info['value']==120 and info['source'].startswith('景品運営分析表')
        w.import_analysis_path(report_file(tmp_path/'別機械.xlsx',[data_row('20088-01')]))
        info=metric_info(w.machines[0],'ratio')
        assert info['value']==85 and info['source']=='レイアウト由来' and info['updated_at']=='2026-11-01T10:00:00+09:00'
        assert w.nodes[uid] is node and node.isSelected() and w._zoom==125
    finally:w.timer.stop();w.day_timer.stop();w.close()
