"""Read-only schedule over existing products; never invents an arrival date."""

from PySide6.QtWidgets import QWidget, QVBoxLayout, QLabel, QPushButton
from PySide6.QtCore import QDate, Qt
from .analysis_views import make_table


class ArrivalsPage(QWidget):
    def __init__(self, window):
        super().__init__()
        self.host = window
        layout = QVBoxLayout(self)
        note = QLabel(
            "既存の商品データを表示します。日付は登録日／稼働予定日です。入荷日が未登録の商品に入荷日を推測しません。"
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        self.table = make_table([], [])
        layout.addWidget(self.table, 1)
        button = QPushButton("選択日の入替指示書を開く")
        button.clicked.connect(self.open_instructions)
        layout.addWidget(button)

    def showEvent(self, event):
        super().showEvent(event)
        self.refresh()

    def refresh(self):
        from .analytics_page import fill_table

        self.products = list(self.host.floor.products())
        fill_table(
            self.table,
            ["登録日／稼働予定日", "商品名", "メーカー", "入数", "CS数", "割当マシン"],
            [
                [
                    p.day.isoformat(),
                    p.name,
                    p.maker,
                    str(p.units),
                    str(p.cases),
                    "、".join(p.machines),
                ]
                for p in self.products
            ],
        )
        for i, p in enumerate(self.products):
            self.table.item(i, 0).setData(Qt.ItemDataRole.UserRole, p.day.isoformat())
        self.table.setSortingEnabled(True)
        self.table.setColumnWidth(1, 380)

    def open_instructions(self):
        row = self.table.currentRow()
        if row >= 0:
            self.host.day.setDate(
                QDate.fromString(
                    self.table.item(row, 0).data(Qt.ItemDataRole.UserRole), "yyyy-MM-dd"
                )
            )
        self.host.tabs.setCurrentWidget(self.host.instruction_page)
