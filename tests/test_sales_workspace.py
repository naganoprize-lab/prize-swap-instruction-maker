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
    assert p.chart_stack.currentWidget() is p.radar and len(p.radar.axes)==5
