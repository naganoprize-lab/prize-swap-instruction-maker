"""Isolate a responsive component from its owning layout during geometry checks."""

from contextlib import contextmanager


@contextmanager
def distribution_viewport(page):
    viewport = page.distributions
    index = page.workspace_tabs.indexOf(viewport)
    title = page.workspace_tabs.tabText(index)
    page.workspace_tabs.removeTab(index)
    viewport.setParent(None)
    viewport.show()
    try:
        yield viewport
    finally:
        viewport.hide()
        page.workspace_tabs.insertTab(index, viewport, title)
        page.workspace_tabs.setCurrentWidget(viewport)
