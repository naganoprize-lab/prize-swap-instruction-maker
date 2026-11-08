from datetime import date
from PySide6.QtCore import QDate
from PySide6.QtWidgets import QApplication
from prize_swap.ui import Window
from prize_swap.floor_data import Machine
from prize_swap.core import load_session


def test_reason_panel_uses_saved_actual_values_rules_and_distinguishes_previous_result(tmp_path):
    w = Window(layout_storage=tmp_path / "floor.json")
    try:
        f = w.floor
        f.follow_today = False
        f.reference_date.setDate(QDate(2026, 11, 8))
        m = Machine(
            number="20088左",
            source_key="L_66",
            product="景品 270",
            ratio=39,
            sales=6000,
            dispensed=11,
            po_rate=0.2,
            installed_on="2026-10-27",
        )
        f.machines = [m]
        f.rebuild()
        w.navigation.activate("candidates")
        w.show()
        QApplication.processEvents()
        p = w.candidate_page
        p.refresh()
        p.show_reasons(m.uid)
        first = w.workflow.data["candidate_evaluations"][-1]
        assert (
            first["evaluation_result"] == "要確認"
            and first["actual_values"]["installation_days"] == 12
            and first["actual_values"]["average_ratio"] == 39
        )
        assert "50.0%未満" in first["explanation"] and "10日以上" in first["explanation"]
        assert (
            "売上推移：データ不足" in p.reason_panel.values.text()
            and "対象期間不明" in p.reason_panel.values.text()
        )
        n = len(w.workflow.data["candidate_evaluations"])
        w.workflow.capture_assessments(force=True)
        assert len(w.workflow.data["candidate_evaluations"]) == n
        f.analysis_settings["poor_ratio"] = 35
        p.refresh()
        p.show_reasons(m.uid)
        assert p.reason_panel.versions.count() == 2
        assert w.workflow.data["candidate_evaluations"][-1]["evaluation_result"] == "入替検討"
        p.reason_panel.versions.setCurrentIndex(1)
        assert (
            "要確認" in p.reason_panel.values.text()
            and "50.0%未満" in p.reason_panel.details.toPlainText()
        )
        w.autosave_instructions()
        assert (
            len(
                load_session(w.instruction_autosave_path)[2]["workflow_state"][
                    "candidate_evaluations"
                ]
            )
            == 2
        )
        assert not p.reason_panel.isWindow()
    finally:
        w.dirty = False
        w.instruction_save_timer.stop()
        w.floor.timer.stop()
        w.floor.day_timer.stop()
        w.preflight.timer.stop()
        w.workflow.reason_timer.stop()
        w.close()


def test_manual_registration_and_reason_inspection_do_not_change_shared_selection(tmp_path):
    w = Window(layout_storage=tmp_path / "floor.json")
    try:
        f = w.floor
        f.machines = [Machine(number="20088左"), Machine(source_key="R_168")]
        f.rebuild()
        f.selection_state.set(m.uid for m in f.machines)
        before = set(f.selection_state.selected_machine_keys)
        w.candidate_page.refresh()
        w.candidate_page.show_reasons(f.machines[1].uid)
        assert f.selection_state.selected_machine_keys == before
        assert w.candidate_page.reason_panel.records[0]["actual_values"]["sales_trend"] is None
        assert w.candidate_page.reason_panel.records[0]["evaluation_result"] == "判定保留"
    finally:
        w.dirty = False
        w.instruction_save_timer.stop()
        w.floor.timer.stop()
        w.floor.day_timer.stop()
        w.preflight.timer.stop()
        w.workflow.reason_timer.stop()
        w.close()
