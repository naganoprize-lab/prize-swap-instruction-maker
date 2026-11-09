"""Test the distribution viewport independently of the main window's minimum width."""

from PySide6.QtWidgets import QApplication
from test_distribution_workspace import window
from viewport_helpers import distribution_viewport


def test_resize_reflows_immediately_and_stays_stable_after_layout_events(window):
    window.navigation.activate("analysis")
    with distribution_viewport(window.analysis_page) as p:
        QApplication.processEvents()
        for width, columns in [(1600, 3), (1000, 2), (700, 1)] * 3:
            p.resize(width, 700)
            assert p.columns == columns
            QApplication.processEvents()
            assert p.columns == columns
