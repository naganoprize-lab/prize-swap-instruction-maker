"""Run the actual EXE smoke entry before packaging, with all its export assertions."""

import json
import os
import subprocess
import sys


def test_packaged_smoke_entry_keeps_average_history_but_pauses_its_candidate_evaluation(tmp_path):
    output = tmp_path / "smoke"
    result = subprocess.run(
        [sys.executable, "main.py", "--smoke-test", str(output)],
        env=os.environ | {"QT_QPA_PLATFORM": "offscreen"},
        capture_output=True,
        text=True,
        timeout=150,
    )
    report = json.loads((output / "report.json").read_text(encoding="utf-8"))
    assert result.returncode == 0 and report["ok"], report.get("error", result.stderr)
    assert not report["physical_printer_tested"]
    analysis = report["integrated_analysis"]
    assert analysis["machines"] == 300 and analysis["history_files"] == 2
    assert analysis["trend"] is None and analysis["average_history_preserved"]
    assert analysis["roundtrip"] and analysis["all_visible_during_search"]
    assert set(analysis["filter_pdf_pages"]) == {
        "po_rate",
        "cost_rate",
        "sales_metric",
        "machine_type",
        "assessment",
    }
    assert report["products"] == 45 and report["label_count"] == 47
    assert report["floor_print"]["direct_pdf_equal"]
