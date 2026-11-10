import json
import os
from pathlib import Path
import subprocess
import sys
import pytest


@pytest.mark.parametrize("scale", ["1", "1.25", "1.5"])
def test_fullhd_layout_in_isolated_qt_process(tmp_path, scale):
    root = Path(__file__).resolve().parents[1]
    config = tmp_path / "screen.json"
    config.write_text(
        json.dumps(
            {
                "synchronousWindowSystemEvents": True,
                "windowFrameMargins": True,
                "screens": [
                    {
                        "name": "FullHD",
                        "x": 0,
                        "y": 0,
                        "width": 1920,
                        "height": 1080,
                        "logicalDpi": 96,
                        "logicalBaseDpi": 96,
                    }
                ],
            }
        )
    )
    output = tmp_path / "probe"
    result = subprocess.run(
        [sys.executable, str(root / "scripts/verify_fullhd_layout.py"), str(output)],
        cwd=tmp_path,
        env=os.environ
            # Qt splits platform arguments at colons, including Windows C:.
            # The child runs in tmp_path, so a relative filename is portable.
            | {"QT_QPA_PLATFORM": f"offscreen:configfile={config.name}", "QT_SCALE_FACTOR": scale},
        capture_output=True,
        text=True,
        timeout=90,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads((output / "report.json").read_text(encoding="utf-8"))
    assert report["ok"] and set(report["checks"]) == {
        theme + ":" + key
        for theme in ("light", "dark")
        for key in (
            "instructions",
            "floor",
            "labels",
            "dashboard",
            "settings",
            "analysis",
            "candidates",
            "arrivals",
            "mobile",
        )
    }
