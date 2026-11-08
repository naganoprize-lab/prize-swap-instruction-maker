from dataclasses import asdict
import pytest
from PySide6.QtCore import Qt,QPoint,QPointF,QEvent
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import QApplication,QPushButton
from PySide6.QtTest import QTest
from prize_swap.ui import Window
from prize_swap.floor_data import Machine
from prize_swap.theme import manager

@pytest.mark.parametrize('theme',['light','dark'])
def test_300_candidates_hover_wheel_focus_space_and_refresh_keep_checks_stable(tmp_path,theme):
    w=Window(layout_storage=tmp_path/'layout.json')
    try:
        f=w.floor;f.machines=[Machine(number=f'{20000+i}左',product=f'景品{i} 270',ratio=40,installed_on='2026-10-01') for i in range(300)]
        f.rebuild();w.navigation.activate('candidates');w.show();manager().set_mode(theme,persist=False);QApplication.processEvents()
        p=w.candidate_page;t=p.table;assert t.rowCount()==300
        before=[asdict(m) for m in f.machines]
        first=t.item(0,0);uid=first.data(Qt.ItemDataRole.UserRole)[0]
        QTest.mouseClick(t.viewport(),Qt.MouseButton.LeftButton,pos=t.visualItemRect(first).center())
        assert f.selection_state.selected_machine_keys=={uid}
        t.verticalScrollBar().setValue(t.verticalScrollBar().maximum()//2);scroll=t.verticalScrollBar().value();keys=set(p.selected_uids())
        for point in [QPoint(15,20),QPoint(100,t.viewport().height()-3),QPoint(250,60)]:QTest.mouseMove(t.viewport(),point)
        QApplication.processEvents()
        assert t.verticalScrollBar().value()==scroll and set(p.selected_uids())==keys
        event=QWheelEvent(QPointF(100,100),QPointF(t.viewport().mapToGlobal(QPoint(100,100))),QPoint(),QPoint(0,-120),Qt.MouseButton.NoButton,Qt.KeyboardModifier.NoModifier,Qt.ScrollPhase.ScrollUpdate,False)
        QApplication.sendEvent(t.viewport(),event)
        assert t.verticalScrollBar().value()>scroll and set(p.selected_uids())==keys
        scroll=t.verticalScrollBar().value();p.refresh();assert t.verticalScrollBar().value()==scroll
        t.setCurrentCell(25,3);t.selectRow(25);assert set(p.selected_uids())==keys
        focus=t.item(25,0).data(Qt.ItemDataRole.UserRole)[0]
        QTest.keyClick(t,Qt.Key.Key_Space);assert focus in p.selected_uids()
        selected=set(p.selected_uids());QTest.keyClick(t,Qt.Key.Key_Down);assert set(p.selected_uids())==selected
        p.select_all_candidates();assert len(p.selected_uids())==300
        next(b for b in p.findChildren(QPushButton) if b.text()=='選択解除').click();assert not p.selected_uids()
        QApplication.sendEvent(t,QEvent(QEvent.Type.WindowDeactivate));QTest.mouseMove(t.viewport(),QPoint(50,t.viewport().height()-2))
        assert not p.selected_uids() and [asdict(m) for m in f.machines]==before
    finally:
        manager().set_mode('light',persist=False);w.dirty=False
        w.instruction_save_timer.stop();w.floor.timer.stop();w.floor.day_timer.stop();w.candidate_page.timer.stop();w.close()
