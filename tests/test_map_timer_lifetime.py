"""A queued fit must not survive its map and block the next Qt event loop."""

import os
import subprocess
import sys


def test_transfer_window_teardown_followed_by_theme_repaint_does_not_deadlock():
    # This sequence exercises normal instructions, their map, Undo/Redo, teardown
    # and the next 300-card window. In-process timeouts cannot recover a Qt/GIL wait.
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "tests/test_selection_transfer.py::test_single_bulk_transfer_drafts_identity_unknown_save_restore_and_undo",
            "tests/test_themes.py::test_system_manual_priority_immediate_switch_and_persistence",
            "tests/test_themes.py::test_300_cards_theme_changes_preserve_geometry_zoom_search_and_selection",
        ],
        env=os.environ | {"QT_QPA_PLATFORM": "offscreen"},
        capture_output=True,
        text=True,
        timeout=45,
    )
    assert result.returncode == 0, result.stdout + result.stderr
