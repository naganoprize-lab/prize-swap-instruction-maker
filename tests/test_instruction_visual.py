import json, os, subprocess, sys


def test_native_editable_instructions_map_and_pdf_at_150_dpi(tmp_path):
    output = tmp_path / "instructions-150"
    process = subprocess.run(
        [sys.executable, "-m", "prize_swap.instruction_visual_check", str(output)],
        env=os.environ | {"QT_SCALE_FACTOR": "1.5"},
        capture_output=True,
        text=True,
        timeout=180,
    )
    report = json.loads((output / "report.json").read_text())
    assert process.returncode == 0 and report["ok"], report.get("error", process.stderr)
    assert (
        report["machines"] == 300 and report["numbered_booths"] == 31 and report["direct_pdf_equal"]
    )
    assert (
        report["blank_product_printable"]
        and report["above_20"]
        and report["save_restore"]
        and report["unresolved_manual_resolution"]
        and report["mixed_paper_preview"]
    )
    assert not report["physical_printer_tested"]
