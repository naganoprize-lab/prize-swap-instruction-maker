from datetime import date
import pytest
from PySide6.QtCore import QPointF, Qt
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest
from prize_swap.analytics_charts import CHARTS,ChartCanvas,histogram,scatter,chart_constraints,chart_icon
from prize_swap.analytics_data import machine_record
from prize_swap.floor_data import Machine
from prize_swap.analysis_settings import validate_settings
from prize_swap.theme import manager


def data():
    return [dict(label=f'機種{i}',series=f'方法{i%2}',period=('2026-11-01','2026-11-03','confirmed'),period_text='2026-11-01 ～ 2026-11-03',value=v,count=2,uids=[f'id{i}',f'id{i+5}']) for i,v in enumerate([10,20,30])]


@pytest.mark.parametrize('kind',['horizontal','vertical','pie','donut','line','area','stacked','scatter','histogram'])
@pytest.mark.parametrize('mode',['light','dark'])
def test_real_chart_pixels_hover_and_exact_source_uids(kind,mode):
    theme=manager();old=theme.mode;theme.set_mode(mode,persist=False)
    c=ChartCanvas();ds=data()
    if kind in ('line','area'):
        for i,r in enumerate(ds):r['series']='20088右';r['label']=f'2026/11/0{i+1}'
    if kind=='scatter':
        for i,r in enumerate(ds):r.update(x=i*30,y=r['value'],x_metric='ratio')
    try:
        c.set_data(kind,ds,'実描画テスト','dispensed');c.resize(760,400);c.show();QApplication.processEvents()
        image=c.grab().toImage();assert not image.isNull() and len(c.hits)>=3
        assert len({image.pixelColor(x,y).name() for x in range(0,image.width(),6) for y in range(0,image.height(),6)})>6
        seen=[];c.activated.connect(seen.append)
        shape,r=c.hits[0]
        point=next(QPointF(x,y) for x in range(int(shape.boundingRect().left()),int(shape.boundingRect().right())+1,2) for y in range(int(shape.boundingRect().top()),int(shape.boundingRect().bottom())+1,2) if shape.contains(QPointF(x,y)))
        assert r['label'] in c.tooltip_at(point)
        QTest.mouseClick(c,Qt.MouseButton.LeftButton,pos=point.toPoint());assert seen and set(seen[0])==set(r['uids'])
        assert not chart_icon(kind).isNull()
    finally:c.close();theme.set_mode(old,persist=False)


def test_histogram_includes_upper_boundary_zero_and_excludes_missing():
    rs=[dict(uid=str(i),ratio=v) for i,v in enumerate([0,25,50,75,100,None])]
    ds=histogram(rs,'ratio',bins=4)
    assert sum(d['value'] for d in ds)==5 and ds[-1]['count']==2 and ds[0]['uids']==['0']
    assert {u for d in ds for u in d['uids']}==set('01234')
    assert scatter([dict(uid='a',number='A',machine_type='UFO',ratio=0,po_rate=0),dict(uid='b',ratio=None,po_rate=0)],'ratio','po_rate')[0]['x']==0


def test_invalid_chart_choices_have_specific_explanations():
    for kind in ('pie','donut'):assert chart_constraints(kind,[],data(),'po_rate','mean',[])
    for kind in ('line','area'):assert chart_constraints(kind,[],[],'average','mean',[])
    assert chart_constraints('radar',[{},{}],[],'ratio','mean',[])
    assert not chart_constraints('pie',[],data(),'machine_type','count',[])
    assert not chart_constraints('radar',[{}],[],'average','mean',[])
