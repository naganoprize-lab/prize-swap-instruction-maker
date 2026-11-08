"""Exercise the actual dock, candidate pointer events and public work-set output paths."""

import json
import os
import subprocess
import sys


def test_native_workflow_widgets_and_public_cli_at_150_percent(tmp_path):
    output = tmp_path / "workflow-150"
    result = subprocess.run(
        [sys.executable, "main.py", "--workflow-visual-test", str(output)],
        env=os.environ | {"QT_SCALE_FACTOR": "1.5"},
        capture_output=True,
        text=True,
        timeout=150,
    )
    report = json.loads((output / "report.json").read_text(encoding="utf-8"))
    assert result.returncode == 0 and report["ok"], report.get("error", result.stderr)
    assert report["candidate_count"] == 300 and not report["physical_printer_tested"]
    for key in (
        "cost_priority",
        "cost_rate_recalculation",
        "raw_po_preserved",
        "radar_actual_values",
        "reusable_dock",
        "dock_left_right_float",
        "selection_preserved",
        "shortcut_undo_redo",
        "candidate_hover_stable",
        "candidate_wheel_stable",
        "numerical_reason_panel",
        "excel_reviewed_mapping",
        "preflight_error_blocks",
        "preflight_warning_approval",
        "blank_product_printable",
        "work_set_pdf",
        "direct_print_renderer_matches_pdf",
        "home_live_counts",
        "profile_audit_evidence_roundtrip",
        "positions_unchanged",
    ):
        assert report[key], key
    assert (output / "work-set.pdf").stat().st_size > 1000
    assert (output / "detail-dark.png").stat().st_size > 1000
