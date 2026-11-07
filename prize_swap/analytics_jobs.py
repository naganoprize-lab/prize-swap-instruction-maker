"""Read-only history work off the GUI thread; results are versioned on return."""

from PySide6.QtCore import QObject, Signal, QRunnable
from .analytics_data import history_series


class JobSignals(QObject):
    finished = Signal(object)


class HistoryJob(QRunnable):
    def __init__(self, key, source, records, metric):
        super().__init__()
        self.key = key
        self.source = source
        self.records = records
        self.metric = metric
        self.signals = JobSignals()

    def run(self):
        try:
            data, omitted = history_series(self.source, self.records, self.metric)
            self.signals.finished.emit((self.key, data, omitted, ""))
        except Exception as exc:
            self.signals.finished.emit((self.key, [], 0, str(exc)))
