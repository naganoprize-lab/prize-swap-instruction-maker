"""Exercise rendered graphs and real input, not just window construction."""

import json
import os
import subprocess
import sys


def test_real_300booth_analytics_and_detail_at_150_dpi(tmp_path):
    output = tmp_path / "analytics-150"
    process = subprocess.run(
        [sys.executable, "-m", "prize_swap.analytics_visual_check", str(output)],
        env=os.environ | {"QT_SCALE_FACTOR": "1.5"},
        capture_output=True,
        text=True,
        timeout=120,
    )
    report = json.loads((output / "report.json").read_text(encoding="utf-8"))
    assert process.returncode == 0 and report["ok"], report.get("error", process.stderr)
    assert report["machines"] == 300 and report["history_points"] == 600
    assert report["shared_selection_analysis"] and report["home_tiles"] == 9
    assert report["candidate_instruction_bulk"]
    assert len(report["charts"]) == 23 and all(r["ok"] for r in report["charts"].values())
    assert (
        report["pdf_theme_equal"]
        and report["detail_navigation"]
        and report["save_restore"]
        and report["physical_coordinates_unchanged"]
    )
    assert (output / "分析.xlsx").stat().st_size > 1000
