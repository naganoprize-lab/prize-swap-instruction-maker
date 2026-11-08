import json, os, subprocess, sys


def test_rendered_full_population_and_modes_and_plan_context(tmp_path):
    output = tmp_path / "filters"
    process = subprocess.run(
        [sys.executable, "-m", "prize_swap.filter_visual_check", str(output)],
        capture_output=True,
        text=True,
        env=os.environ | {"QT_SCALE_FACTOR": "1.5"},
        timeout=180,
    )
    report = json.loads((output / "report.json").read_text(encoding="utf-8"))
    assert process.returncode == 0 and report["ok"], report.get("error", process.stderr)
    assert report["sales_example"]["rank"] == 31 and report["matched_booths"] == 6
    assert (
        report["selection_preserved"] and report["normal_and_light"] and report["opaque_neighbors"]
    )
    assert len(list(output.glob("floor-*.png"))) == 4
