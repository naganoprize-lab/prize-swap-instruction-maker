import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication


@pytest.fixture(scope="session", autouse=True)
def qt_app():
    app = QApplication.instance() or QApplication([])
    from prize_swap.fonts import initialize_fonts
    initialize_fonts()
    yield app


@pytest.fixture(autouse=True)
def dispose_closed_test_windows(qt_app):
    """Release closed Qt windows so global theme repaint work stays bounded."""
    yield
    from PySide6.QtCore import QCoreApplication,QEvent
    import gc
    for widget in QApplication.topLevelWidgets():
        if not widget.isVisible():widget.deleteLater()
    QCoreApplication.sendPostedEvents(None,QEvent.Type.DeferredDelete)
    gc.collect()
