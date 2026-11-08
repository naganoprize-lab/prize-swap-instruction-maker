from copy import deepcopy
from datetime import date
from PySide6.QtCore import Qt, QDate
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QComboBox,
    QLineEdit,
    QDoubleSpinBox,
)
from prize_swap.ui import Window
from prize_swap.floor_data import Machine, validate_state
from prize_swap.theme import manager, analysis_color, contrast_text
from prize_swap.floor_details import MachineDetailDialog
from prize_swap.po_evaluation import evaluate_po


def test_home_navigation_tiles_responsive_theme_and_saved_tile_order(tmp_path):
    w = Window(layout_storage=tmp_path / "auto.json")
    try:
        f = w.floor
        f.machines = [Machine(number="20088左", sales=17900, po_rate=0.2, unit_price=270, ratio=85)]
        f.rebuild()
        w.show()
        w.tabs.setCurrentWidget(w.dashboard_page)
        QApplication.processEvents()
        home = w.dashboard_page
        assert home.cards["total"].value.text() == "1"
        for key in (
            "floor",
            "analysis",
            "candidates",
            "instructions",
            "arrivals",
            "labels",
            "settings",
        ):
            home.tiles[key].click()
            QApplication.processEvents()
            assert w.tabs.currentWidget() is w.pages[key]
            w.tabs.setCurrentWidget(home)
        home.resize(1500, 700)
        QApplication.processEvents()
        assert home._tile_columns == 3
        home.resize(600, 700)
        QApplication.processEvents()
        assert home._tile_columns == 1
        before = [(m.x, m.y, m.width, m.height) for m in f.machines]
        manager().set_mode("dark", persist=False)
        assert analysis_color("#F3F4F6") == "#F3F4F6" and contrast_text("#F3F4F6") == "#111827"
        assert not home.grab().isNull()
        manager().set_mode("light", persist=False)
        assert [(m.x, m.y, m.width, m.height) for m in f.machines] == before
        d = w.analysis_page.distributions
        d.move_tile("sales_bin")
        assert validate_state(f.state())["analytics_ui"]["tile_order"][0] == "sales_bin"
    finally:
        manager().set_mode("light", persist=False)
        w.dirty = False
        w.floor.timer.stop()
        w.floor.day_timer.stop()
        w.analysis_page.timer.stop()
        w.candidate_page.timer.stop()
        w.close()


def test_detail_real_values_pt_manual_cost_definition_and_no_visible_score(tmp_path):
    w = Window(layout_storage=tmp_path / "auto.json")
    try:
        m = Machine(
            number="20088左",
            product="堅あげ 270",
            unit_price=270,
            po_rate=0.2,
            sales=17900,
            ratio=349,
            installed_on="2026-11-02",
        )
        w.floor.machines = [m]
        w.floor.follow_today = False
        w.floor.reference_date.setDate(QDate(2026, 11, 8))
        w.floor.rebuild()
        d = MachineDetailDialog(w.floor, m)
        d.show()
        QApplication.processEvents()
        assert "22.22%" in d.po_summary.text() and "-2.22pt" in d.po_summary.text()
        assert (
            "17900" not in d.radar.accessibleDescription()
            and "17,900円" in d.radar.accessibleDescription()
        )
        assert all(
            "スコア" not in d.score_table.horizontalHeaderItem(i).text()
            for i in range(d.score_table.columnCount())
        )
        assert not d.values["average"].isVisible()
        d.confirmed_cost.setValue(300)
        assert evaluate_po(d.updated, w.floor.analysis_settings)["target"] == 0.2
        assert "+0.00pt" in d.po_summary.text()
        d.reject()
    finally:
        w.dirty = False
        w.floor.timer.stop()
        w.floor.day_timer.stop()
        w.close()


def test_settings_dialog_saves_cost_fee_thresholds_and_recalculates(tmp_path, monkeypatch):
    from prize_swap.analysis_settings_ui import edit_analysis_settings
    from PySide6.QtWidgets import QFormLayout, QMessageBox

    w = Window(layout_storage=tmp_path / "auto.json")
    try:
        f = w.floor
        f.machines = [Machine(unit_price=270, po_rate=0.2)]
        f.rebuild()

        def edit(dialog):
            for form in dialog.findChildren(QFormLayout):
                for i in range(form.rowCount()):
                    label = form.itemAt(i, QFormLayout.ItemRole.LabelRole)
                    field = form.itemAt(i, QFormLayout.ItemRole.FieldRole)
                    if label is None or field is None:
                        continue
                    text = label.widget().text() if hasattr(label.widget(), "text") else ""
                    control = field.widget()
                    if text == "目標原価率 (%)":
                        control.setValue(54)
                    if text == "標準プレイ料金（円・仮設定）":
                        control.setValue(200)
                    if text == "売上の分位点 (%)・4個":
                        control.setText("10,30,70,90")
                    if text == "売上固定境界（円）・4個":
                        control.setText("100,200,300,400")
            box = dialog.findChild(QDialogButtonBox)
            box.button(QDialogButtonBox.StandardButton.Save).click()
            assert dialog.result() == QDialog.DialogCode.Accepted
            return dialog.result()

        monkeypatch.setattr(QDialog, "exec", edit)
        monkeypatch.setattr(
            QMessageBox, "warning", lambda *args: (_ for _ in ()).throw(AssertionError(args[2]))
        )
        edit_analysis_settings(f)
        assert (
            f.analysis_settings["cost_target"] == 0.54
            and f.analysis_settings["default_play_price"] == 200
        )
        assert f.analysis_settings["sales_percentiles"] == [0.1, 0.3, 0.7, 0.9]
        assert evaluate_po(f.machines[0], f.analysis_settings)["target"] == 0.4
        assert validate_state(f.state())["analysis_settings"] == f.analysis_settings
    finally:
        w.dirty = False
        w.floor.timer.stop()
        w.floor.day_timer.stop()
        w.close()
