from copy import deepcopy
from datetime import date,timedelta
import pytest
from prize_swap.floor_data import Machine
from prize_swap.replacement import assess,episode,comparable_trend
from prize_swap.sales_analysis import empty_snapshot

DAY=date(2026,11,7)

@pytest.mark.parametrize('value,age,status',[(49.9,10,'要確認'),(49.9,9,'判定保留'),(50,7,'入替検討'),(99.9,7,'入替検討'),(100,0,'好調'),(None,20,'判定保留'),(20,None,'判定保留'),(20,-1,'判定保留')])
def test_rules_and_insufficient_data(value,age,status):
    m=Machine(ratio=value,installed_on=(DAY-timedelta(days=age)).isoformat() if age is not None else None,operating_days=90)
    a=assess(m,DAY)
    assert a['status']==status and a['trend'] is None
    assert any('未評価' in r for r in a['reasons'])


def test_financial_flags_do_not_force_replacement():
    m=Machine(ratio=200,installed_on=DAY.isoformat(),po_rate=.5,cost_rate=.1)
    a=assess(m,DAY)
    assert a['status']=='好調'
    assert any('PO率' in r for r in a['reasons']) and any('原価率' in r for r in a['reasons'])


def test_comparison_requires_confirmed_period_same_episode_nonoverlap():
    m=Machine(number='20088左',product='現在商品',installed_on='2026-10-01',ratio=40)
    context=episode(m)
    m.analysis=dict(values={'average':50},product_context=context,period_start='2026-11-04',period_end='2026-11-06',fingerprint='current')
    prior=empty_snapshot();prior.update(fingerprint='prior',period_start='2026-11-01',period_end='2026-11-03',period_source='excel')
    prior['records']={m.number:dict(values={'average':100},product_context=deepcopy(context))}
    assert comparable_trend(m,[prior])[0]==.5
    assert 'prior' in comparable_trend(m,[prior])[1]
    prior['period_end']='2026-11-04';assert comparable_trend(m,[prior])[0] is None
    prior['period_end']='2026-11-03';prior['records'][m.number]['product_context']['product']='変更前商品'
    assert comparable_trend(m,[prior])[0] is None
    m.product='新しい商品'
    assert assess(m,DAY)['status']=='判定保留'


def test_candidate_assignment_requires_confirmation_and_undo(tmp_path,monkeypatch):
    from prize_swap.floor import FloorWidget
    from prize_swap.core import Product
    from prize_swap.analysis_views import propose_assignments
    from PySide6.QtWidgets import QInputDialog,QMessageBox
    product=Product(DAY,'入替先',machines=['20000右'])
    w=FloorWidget(lambda:[product],lambda:DAY,tmp_path/'auto.json')
    try:
        w.machines=[Machine(number='20088左')];w.rebuild()
        monkeypatch.setattr(QInputDialog,'getItem',lambda *a,**k:('1: 入替先',True))
        monkeypatch.setattr(QMessageBox,'question',lambda *a:QMessageBox.StandardButton.No)
        assert not propose_assignments(w,['20088左']) and product.machines==['20000右']
        monkeypatch.setattr(QMessageBox,'question',lambda *a:QMessageBox.StandardButton.Yes)
        assert propose_assignments(w,['20088左']) and product.machines==['20000右','20088左']
        w.history.undo();assert product.machines==['20000右']
        w.history.redo();assert product.machines==['20000右','20088左']
    finally:w.timer.stop();w.day_timer.stop();w.close()


def test_method_analysis_weighting_missing_values_and_periods():
    from prize_swap.method_analysis import totals
    a=Machine(number='20000右',operation_method='リング',sales=100,average=50,po_rate=.1,cost_rate=.2,cost=20,gross_profit=80,ratio=50)
    b=Machine(number='20001右',operation_method='リング',sales=300,average=100,po_rate=.2,cost_rate=.6,cost=180,gross_profit=120,ratio=150)
    missing=Machine(number='20002右',operation_method='フック')
    unset=Machine(number='20003右')
    result=totals([a,b,missing,unset]);ring=next(r for r in result if r['method']=='リング')
    assert ring['count']==2 and ring['average_sales']==75 and ring['sales']==400 and ring['gross_profit']==200
    assert ring['po_rate']==pytest.approx(.175) and ring['po_arithmetic']==pytest.approx(.15)
    assert ring['cost_rate']==pytest.approx(.5) and ring['cost_arithmetic']==pytest.approx(.4) and ring['ratio']==100
    assert next(r for r in result if r['method']=='フック')['sales'] is None
    assert any(r['method']=='未設定' for r in result)
    # Even with the same method, separate periods never become one total.
    a.analysis={'period_start':'2026-11-01','period_end':'2026-11-03','values':{}}
    b.analysis={'period_start':'2026-11-04','period_end':'2026-11-06','values':{}}
    assert len(totals([a,b]))==2


def test_method_analysis_type_filter_and_fallback_arithmetic():
    from prize_swap.method_analysis import totals
    machines=[Machine(kind='機種A',operation_method='自由方法',po_rate=.1,cost_rate=.3),Machine(kind='機種B',operation_method='自由方法',po_rate=.3,cost_rate=.5)]
    result=totals(machines,type_filter='機種A')
    assert len(result)==1 and result[0]['count']==1 and result[0]['po_rate']==.1
    assert '単純平均' in result[0]['po_mode']
    assert totals(machines,method_filter='橋渡し')==[]
