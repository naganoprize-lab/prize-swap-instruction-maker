from datetime import date
from copy import deepcopy
import pytest
from PySide6.QtCore import QDate
from PySide6.QtWidgets import QApplication,QDialog
from prize_swap.ui import Window
from prize_swap.floor_data import Machine

DAY=date(2026,11,7)


@pytest.fixture
def window(tmp_path):
    w=Window(layout_storage=tmp_path/'auto.json')
    w.floor.machines=[Machine(number=f'{20000+i}右',product=f'商品{i}',operation_method='リング' if i%2 else '橋渡し',kind='UFO9',ratio=i,average=1000+i,sales=3000+i,x=i%20*240,y=i//20*170) for i in range(300)]
    w.floor.rebuild();w.show();QApplication.processEvents()
    yield w
    w.dirty=False;w.floor.timer.stop();w.floor.day_timer.stop();w.analysis_page.timer.stop();w.close()


def test_analysis_is_embedded_and_keeps_floor_state_and_selection(window,monkeypatch):
    w=window;f=w.floor
    w.tabs.setCurrentWidget(f);f.set_zoom(150);f.floor_search.setText('商品29')
    f.nodes[f.machines[29].uid].setSelected(True)
    before=deepcopy(f.state());ids={k:id(v) for k,v in f.nodes.items()}
    monkeypatch.setattr(QDialog,'exec',lambda *_:pytest.fail('analysis must not open a dialog'))
    w.navigation.buttons['analysis'].click();QApplication.processEvents()
    assert w.tabs.currentWidget() is w.analysis_page and w.navigation.buttons['analysis'].isChecked()
    assert w.analysis_page.detail_table.rowCount()==300 and w.analysis_page.legacy.kinds
    w.analysis_page.search.setText('商品299');w.analysis_page.refresh()
    w.navigation.buttons['floor'].click();w.navigation.buttons['analysis'].click();QApplication.processEvents()
    assert w.analysis_page.search.text()=='商品299' and w.analysis_page.detail_table.rowCount()==1
    assert f.state()['machines']==before['machines'] and f._zoom==150 and f.floor_search.text()=='商品29'
    assert {k:id(v) for k,v in f.nodes.items()}==ids and f.nodes[f.machines[29].uid].isSelected()


def test_detail_to_embedded_analysis_scopes(window):
    w=window;m=w.floor.machines[0]
    w.floor.analytics_requested.emit(m.uid,'machine');QApplication.processEvents()
    assert w.tabs.currentWidget() is w.analysis_page and [r['uid'] for r in w.analysis_page.records]==[m.uid]
    w.floor.analytics_requested.emit(m.uid,'method');assert len(w.analysis_page.records)==150
    w.floor.analytics_requested.emit(m.uid,'machine_type');assert len(w.analysis_page.records)==300
    w.analysis_page.clear_focus();assert w.analysis_page.detail_table.rowCount()==300


def test_graph_choices_constraints_expansion_image_and_cross(window,tmp_path):
    from prize_swap.analytics_charts import CHARTS
    w=window;p=w.analysis_page;w.show_sales_analysis();QApplication.processEvents()
    assert p.chart_type.count()==11
    assert not p.chart_type.model().item(p.chart_type.findData('line')).isEnabled()
    assert not p.chart_type.model().item(p.chart_type.findData('pie')).isEnabled()
    p.operation.setCurrentIndex(p.operation.findData('count'));p.refresh()
    assert p.chart_type.model().item(p.chart_type.findData('pie')).isEnabled()
    for kind in ('horizontal','vertical','pie','donut','stacked','histogram','scatter','cross'):
        p.chart_type.setCurrentIndex(p.chart_type.findData(kind));p.refresh();QApplication.processEvents()
        assert p.save_image(tmp_path/f'{kind}.png') and (tmp_path/f'{kind}.png').stat().st_size>300
    p.expand_button.click();assert not p.data_tabs.isVisible();p.expand_button.click();assert p.data_tabs.isVisible()
    p.focus_machine(w.floor.machines[0].uid);p.chart_type.setCurrentIndex(p.chart_type.findData('radar'));p.refresh()
    assert p.chart_stack.currentWidget() is p.radar_scroll and len(p.radar.axes)==5


def test_all_fields_compound_conditions_and_saved_state(window):
    from prize_swap.analytics_data import FIELDS
    w=window;p=w.analysis_page
    assert {p.metric_choice.itemData(i) for i in range(p.metric_choice.count())}==set(FIELDS)
    p.filter_field.setCurrentIndex(p.filter_field.findData('ratio'));p.filter_operator.setCurrentIndex(p.filter_operator.findData('range'))
    p.filter_value.setText('50');p.filter_upper.setText('99.9');p.add_condition()
    assert len(p.records)==50
    p.filter_field.setCurrentIndex(p.filter_field.findData('method'));p.filter_operator.setCurrentIndex(p.filter_operator.findData('eq'));p.filter_value.setText('リング');p.add_condition()
    assert len(p.records)==25 and len(p.conditions)==2
    saved=w.floor.state();assert saved['analytics_ui']['conditions']==p.conditions
    p.clear_conditions();assert len(p.records)==300
    w.floor.restore(saved);p.refresh();assert len(p.records)==25 and len(p.conditions)==2
    p.remove_condition(0);assert len(p.records)==150
    p.clear_conditions();p.filter_field.setCurrentIndex(p.filter_field.findData('po_rate'));p.filter_value.setText('5');p.add_condition()
    assert p.conditions[-1]['value']==.05 and p.records==[] # missing is not zero


def test_csv_excel_missing_zero_provenance_and_formula_safety(window,tmp_path):
    import csv
    from openpyxl import load_workbook
    from prize_swap.analytics_data import FIELDS
    p=window.analysis_page;m=window.floor.machines[0]
    m.product='=HYPERLINK("bad")';m.po_rate=0;m.cost_rate=.6;m.sales=0;m.average=None
    window.floor.sync();p.focus_machine(m.uid)
    path=tmp_path/'分析_日本語.xlsx';assert p.save_excel(path)
    b=load_workbook(path,data_only=False);s=b['詳細'];headers=[c.value for c in s[1]]
    assert s.cell(2,headers.index('売上金額')+1).value==0
    assert s.cell(2,headers.index('平均売上')+1).value is None
    cell=s.cell(2,headers.index('商品名')+1);assert cell.value==m.product and cell.data_type=='s'
    po=s.cell(2,headers.index('PO率（内部比率 0.05=5%）')+1);assert po.value==0 and po.number_format=='0.00%'
    assert s.cell(2,headers.index('売上金額・取得元')+1).value=='レイアウト由来'
    assert '景品種別' not in headers and set(b.sheetnames)>={'詳細','集計結果','グラフデータ','設定・取得元'};b.close()
    csvpath=tmp_path/'分析.csv';assert p.save_csv(csvpath,table='詳細')
    with csvpath.open(encoding='utf-8-sig',newline='') as stream:rows=list(csv.reader(stream))
    assert rows[1][rows[0].index('商品名')].startswith("'=HYPERLINK")
    before=path.read_bytes();assert not p.save_excel(path) and path.read_bytes()==before
    window.source=str(path);assert not p.save_excel(path,overwrite=True) and path.read_bytes()==before


@pytest.mark.parametrize('kind',['horizontal','vertical','pie','donut','scatter','histogram','stacked','cross'])
def test_graph_click_links_exact_ids_keeps_all_booths_and_clear_restores_search(window,kind):
    from PySide6.QtCore import Qt,QPointF
    from PySide6.QtTest import QTest
    w=window;f=w.floor;p=w.analysis_page
    if kind=='scatter':
        for m in f.machines:m.po_rate=.05
        f.sync()
    w.tabs.setCurrentWidget(f);f.set_zoom(125);f.floor_search.setText('商品29');QApplication.processEvents()
    before=[(m.uid,m.x,m.y,m.width,m.height) for m in f.machines];nodes={k:id(n) for k,n in f.nodes.items()}
    opacity={k:n.opacity() for k,n in f.nodes.items()};settings=deepcopy(f.presentation)
    w.show_sales_analysis();p.operation.setCurrentIndex(p.operation.findData('count'));p.chart_type.setCurrentIndex(p.chart_type.findData(kind));p.refresh();QApplication.processEvents()
    if kind=='cross':
        item=next(p.cross_table.item(i,j) for i in range(p.cross_table.rowCount()) for j in range(1,p.cross_table.columnCount()) if p.cross_table.item(i,j).data(Qt.ItemDataRole.UserRole))
        expected=set(item.data(Qt.ItemDataRole.UserRole));p.cross_clicked(item.row(),item.column())
    else:
        p.canvas.grab();shape,data=p.canvas.hits[0]
        point=next(QPointF(x,y) for x in range(int(shape.boundingRect().left()),int(shape.boundingRect().right())+1,2) for y in range(int(shape.boundingRect().top()),int(shape.boundingRect().bottom())+1,2) if shape.contains(QPointF(x,y)))
        expected=set(data['uids']);QTest.mouseClick(p.canvas,Qt.MouseButton.LeftButton,pos=point.toPoint())
    QApplication.processEvents()
    assert w.tabs.currentWidget() is f and f.analysis_highlight_uids==expected
    assert len(f.visible_nodes())==300 and f._zoom==125 and f.presentation==settings
    assert all(n.opacity()==(1 if uid in expected else .35) for uid,n in f.nodes.items())
    assert [(m.uid,m.x,m.y,m.width,m.height) for m in f.machines]==before and {k:id(n) for k,n in f.nodes.items()}==nodes
    f.analysis_clear.click();QApplication.processEvents()
    assert f.analysis_highlight_uids is None and {k:n.opacity() for k,n in f.nodes.items()}==opacity
    assert f.floor_search.text()=='商品29' and f._zoom==125


def test_analysis_highlight_in_fullscreen_empty_link_and_detail_return(window):
    w=window;f=w.floor;p=w.analysis_page;uid=f.machines[20].uid
    w.highlight_analysis_machines([uid]);f.toggle_fullscreen();QApplication.processEvents()
    assert len(f.visible_nodes())==300 and f._fullscreen_host.buttons['clear_analysis'].isVisible()
    f._fullscreen_host.buttons['clear_analysis'].click();assert f.analysis_highlight_uids is None
    f.analytics_requested.emit(uid,'machine');QApplication.processEvents()
    assert f._fullscreen_host is None and w.tabs.currentWidget() is p and len(p.records)==1
    p.chart_type.setCurrentIndex(p.chart_type.findData('radar'));p.refresh();p.radar.activated.emit([uid]);QApplication.processEvents()
    assert w.tabs.currentWidget() is f and f.nodes[uid].isSelected()
    previous=f.analysis_highlight_uids;w.highlight_analysis_machines([]);assert f.analysis_highlight_uids==previous
