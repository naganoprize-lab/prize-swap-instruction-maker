from datetime import date
from dataclasses import asdict
import pytest
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QDate
from prize_swap.floor import FloorWidget
from prize_swap.floor_data import Machine
from prize_swap.floor_print import save_floor_pdf
from prize_swap.pdf_validation import require_searchable_pdf

DAY = date(2026, 11, 9)


def test_light_mode_reuses_300_items_preserves_shared_selection_data_and_saved_settings(tmp_path):
    f = FloorWidget(lambda: [], lambda: DAY, tmp_path / "floor.json")
    try:
        f.reference_date.setDate(QDate(DAY))
        f.machines = [
            Machine(
                source_key=f"R_{i}",
                sales=i * 100,
                ratio=i,
                product=f"商品{i} 270",
                x=(i % 20) * 240,
                y=(i // 20) * 170,
            )
            for i in range(300)
        ]
        f.rebuild()
        items = f.nodes.copy()
        positions = [asdict(m) for m in f.machines]
        records = f.analytics.records()
        f.selection_state.set({m.uid for m in f.machines[:20]})
        for mode in ("light", "normal", "light"):
            f.render_mode.setCurrentIndex(f.render_mode.findData(mode))
            f.set_color_filter("sales_metric")
            f.rebuild()
            f.view.grab()
            assert all(f.nodes[k] is v for k, v in items.items())
            assert len(f.selection_state.selected_machine_keys) == 20
            assert len(f.visible_nodes()) == 300 and [asdict(m) for m in f.machines] == positions
            assert f.analytics.records() is records
        f.autosave()
        g = FloorWidget(lambda: [], lambda: DAY, f.storage)
        try:
            assert (
                g.presentation["render_mode"] == "light" and g.render_mode.currentData() == "light"
            )
            assert len(g.machines) == 300
        finally:
            g.timer.stop()
            g.day_timer.stop()
            g.close()
    finally:
        f.timer.stop()
        f.day_timer.stop()
        f.close()


def test_light_mode_does_not_change_vector_pdf_or_financial_calculations(tmp_path):
    f = FloorWidget(lambda: [], lambda: DAY, tmp_path / "floor.json")
    try:
        f.machines = [
            Machine(number="20088左", product="印刷は通常品質 270", sales=16100, ratio=85)
        ]
        f.rebuild()
        texts = []
        for mode in ("normal", "light"):
            f.render_mode.setCurrentIndex(f.render_mode.findData(mode))
            path = tmp_path / (mode + ".pdf")
            save_floor_pdf(path, f.machines, {}, f.print_options, DAY)
            texts.append([p["text"] for p in require_searchable_pdf(path)["pages"]])
        from prize_swap.pdf_validation import normalized_pdf_text

        assert texts[0] == texts[1] and "印刷" in normalized_pdf_text("".join(texts[0]))
    finally:
        f.timer.stop()
        f.day_timer.stop()
        f.close()
