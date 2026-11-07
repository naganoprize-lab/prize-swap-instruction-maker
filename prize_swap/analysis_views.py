"""Sortable analysis tables; activating a record locates its existing floor item."""

from PySide6.QtCore import Qt
from PySide6.QtGui import QPainter, QColor
from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QAbstractItemView,
    QLineEdit,
    QPushButton,
    QDialogButtonBox,
    QInputDialog,
    QMessageBox,
    QWidget,
    QComboBox,
)
from .machine_matching import machine_key,display_identity,normalize_number
from .floor_status import ratio_text, days_text, effective_stat, money, ratio, days


class ValueItem(QTableWidgetItem):
    def __init__(self, text, value=None):
        super().__init__(str(text))
        self.sort_value = value if value is not None else str(text).casefold()
        self.setToolTip(str(text))
        if type(self.sort_value) in (int,float) or str(text).endswith('%'):self.setTextAlignment(Qt.AlignmentFlag.AlignRight|Qt.AlignmentFlag.AlignVCenter)

    def __lt__(self, other):
        a, b = self.sort_value, getattr(other, "sort_value", other.text())
        if type(a) in (float, int) and type(b) in (float, int):
            return a < b
        return str(a) < str(b)


def make_table(headers, rows):
    t = QTableWidget(len(rows), len(headers))
    from .theme import style_table
    style_table(t)
    t.setHorizontalHeaderLabels(headers)
    t.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    t.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    t.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
    t.setAlternatingRowColors(True)
    for i, row in enumerate(rows):
        for j, v in enumerate(row):
            item = ValueItem(v[0], v[1]) if isinstance(v, tuple) else ValueItem(v)
            t.setItem(i, j, item)
    t.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
    t.horizontalHeader().setStretchLastSection(True)
    t.setSortingEnabled(True)
    t.resizeColumnsToContents()
    return t


def percent_text(v):
    return "—" if v is None else f"{v:.1%}"


def show_candidates(owner):
    from .replacement import assess

    dialog = QDialog(owner)
    dialog.setWindowTitle("入替候補（ルールによる参考判定）")
    dialog.resize(1250, 660)
    layout = QVBoxLayout(dialog)
    layout.addWidget(
        QLabel(
            "自動判定で入替を確定しません。商品変更・期間・データ鮮度を確認し、追加操作の確認後に指示書へ反映します。"
        )
    )
    search = QLineEdit()
    search.setPlaceholderText("番号・商品・判定理由を検索")
    layout.addWidget(search)
    rows = []
    for m in owner.machines:
        a = assess(
            m,
            owner.reference_day(),
            owner.analysis_settings,
            getattr(owner, "performance_history", []),
        )
        rows.append(
            [
                (f'{a["priority"]+1} {a["status"]}', a["priority"]),
                display_identity(m),
                m.product,
                (ratio_text(m), ratio(m)),
                (days_text(m, owner.reference_day()), days(m, owner.reference_day())),
                (money(effective_stat(m, "average")), effective_stat(m, "average")),
                (percent_text(effective_stat(m, "po_rate")), effective_stat(m, "po_rate")),
                (percent_text(effective_stat(m, "cost_rate")), effective_stat(m, "cost_rate")),
                (percent_text(a["trend"]), a["trend"]),
                " / ".join(a["reasons"]),
                (m.analysis or {}).get("loaded_at", "—"),
            ]
        )
    table = make_table(
        [
            "優先順位",
            "機器番号",
            "商品名",
            "アベ比",
            "投入日数",
            "平均売上",
            "PO率",
            "原価率",
            "売上前回比",
            "判定理由",
            "データ更新日時",
        ],
        rows,
    )
    table.setColumnWidth(9, 430)
    table.sortItems(0)
    layout.addWidget(table, 1)

    def filter_rows(text):
        for i in range(table.rowCount()):
            table.setRowHidden(
                i,
                bool(text)
                and not any(
                    text.casefold() in table.item(i, j).text().casefold()
                    for j in range(table.columnCount())
                ),
            )

    search.textChanged.connect(filter_rows)

    def locate(row, *_):
        number = table.item(row, 1).text()
        ids = [m.uid for m in owner.machines if machine_key(m) == number or m.source_key == number]
        owner.highlight_group(ids)
        if ids:
            owner.scene.clearSelection()
            node = owner.nodes[ids[0]]
            node.setSelected(True)
            owner.view.ensureVisible(node)

    table.cellClicked.connect(locate)
    row = QHBoxLayout()
    layout.addLayout(row)
    add = QPushButton("選択機器を入替指示書への追加候補にする")
    row.addWidget(add)

    def propose():
        numbers = list(
            dict.fromkeys(
                table.item(index.row(), 1).text() for index in table.selectionModel().selectedRows()
            )
        )
        propose_assignments(owner, numbers, dialog)

    add.clicked.connect(propose)
    close = QPushButton("閉じる")
    close.clicked.connect(dialog.accept)
    row.addWidget(close)
    dialog.exec()


def propose_assignments(owner, numbers, parent=None):
    numbers = [normalize_number(n,allow_bare=True) for n in numbers if normalize_number(n,allow_bare=True)]
    products = [p for p in owner.products() if p.day == owner.selected_day()]
    if not numbers or not products:
        owner.message.emit("機器と選択日の入替先商品を用意してください。")
        return False
    labels = [f"{i+1}: {p.name}" for i, p in enumerate(products)]
    label, ok = QInputDialog.getItem(
        parent or owner, "入替先商品の選択", "追加候補を割り当てる商品", labels, editable=False
    )
    if not ok:
        return False
    product = products[labels.index(label)]
    if (
        QMessageBox.question(
            parent or owner,
            "入替指示書へ反映の確認",
            f'「{product.name}」の投入予定へ {"、".join(numbers)} を追加します。既存の割当は維持します。確定しますか？',
        )
        != QMessageBox.StandardButton.Yes
    ):
        return False
    from .floor_details import DetailCommand
    from copy import deepcopy

    before = owner.state()
    old = deepcopy(product.machines)
    new = list(dict.fromkeys(old + numbers))
    product.machines = new
    owner.history.push(
        DetailCommand(owner, before, owner.state(), [(product, {"machines": (old, deepcopy(new))})])
    )
    owner.product_edited.emit()
    owner.sync()
    owner.schedule_save()
    return True


class MethodBars(QWidget):
    def __init__(self):
        super().__init__()
        self.records = []
        self.setMinimumHeight(180)

    def paintEvent(self, event):
        painter = QPainter(self)
        from .theme import tokens,analysis_color
        painter.fillRect(self.rect(), QColor(tokens()["card"]))
        records = self.records
        if not records:
            painter.drawText(10, 30, "集計対象がありません。")
            return
        values = [max(0, r["average_sales"] or 0) for r in records]
        largest = max(values, default=0) or 1
        height = max(20, (self.height() - 30) / max(1, len(records)))
        for i, (r, value) in enumerate(zip(records, values)):
            y = 20 + i * height
            painter.setPen(QColor(tokens()["text"]))
            painter.drawText(4, round(y + 14), r["method"])
            painter.fillRect(
                140,
                round(y),
                round(max(0, self.width() - 300) * value / largest),
                max(8, round(height - 5)),
                QColor(analysis_color("#94D8A0")),
            )
            painter.drawText(
                max(145, self.width() - 150), round(y + 14), money(r["average_sales"]) + "円"
            )
        painter.end()


def show_method_analysis(owner):
    from .method_analysis import totals
    from .analysis_settings import machine_type
    from .performance_validation import period_text
    from PySide6.QtWidgets import QScrollArea

    dialog = QDialog(owner)
    dialog.setWindowTitle("運営方法別の成績分析")
    dialog.resize(1200, 760)
    layout = QVBoxLayout(dialog)
    row = QHBoxLayout()
    layout.addLayout(row)
    kinds = QComboBox()
    kinds.addItem("全機種", "")
    for name in sorted(set(machine_type(m, owner.analysis_settings) for m in owner.machines)):
        kinds.addItem(name, name)
    methods = QComboBox()
    methods.addItem("全運営方法", "")
    for name in sorted(set(m.operation_method.strip() or "未設定" for m in owner.machines)):
        methods.addItem(name, name)
    row.addWidget(kinds)
    row.addWidget(methods)
    note = QLabel(
        "平均売上・アベ比は有効値の単純平均。PO率は売上加重を優先し、原価率は同じ有効対象の合計原価÷合計売上を優先します。欠損値は0で埋めません。異なる期間・期間不明の別ファイルは合算しません。"
    )
    note.setWordWrap(True)
    layout.addWidget(note)
    container = QVBoxLayout()
    layout.addLayout(container, 1)
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    bars = MethodBars()
    scroll.setWidget(bars)
    layout.addWidget(scroll, 1)
    table = None

    def refresh():
        nonlocal table
        records = totals(
            owner.machines, owner.analysis_settings, kinds.currentData(), methods.currentData()
        )
        rows = []
        for r in records:
            start, end, _ = r["period"]
            period = f"{start} ～ {end}" if start and end else "対象期間不明（比較不可）"
            coverage = " / ".join(f'{k}:{v}/{r["count"]}台' for k, v in r["coverage"].items())
            rows.append(
                [
                    r["method"],
                    (r["count"], r["count"]),
                    (money(r["average_sales"]), r["average_sales"]),
                    (money(r["sales"]), r["sales"]),
                    percent_text(r["po_rate"]),
                    percent_text(r["po_arithmetic"]),
                    r["po_mode"],
                    percent_text(r["cost_rate"]),
                    percent_text(r["cost_arithmetic"]),
                    r["cost_mode"],
                    "—" if r["ratio"] is None else f'{r["ratio"]:.1f}%',
                    money(r["gross_profit"]),
                    period,
                    f'{len(r["products"])}商品：' + "、".join(r["products"]),
                    coverage,
                ]
            )
        if table:
            container.removeWidget(table)
            table.deleteLater()
        table = make_table(
            [
                "運営方法",
                "ブース数",
                "平均売上",
                "総売上",
                "PO率（優先方式）",
                "PO率単純平均",
                "PO率計算方式",
                "原価率（優先方式）",
                "原価率単純平均",
                "原価率計算方式",
                "平均アベ比",
                "粗利益",
                "集計期間",
                "商品構成",
                "有効値件数",
            ],
            rows,
        )
        container.addWidget(table)
        # Each bar remains a separate period; its full label makes comparison caveats explicit.
        bars.records = [
            dict(r, method=r["method"] + " " + (r["period"][0] or "期間不明")) for r in records
        ]
        bars.setMinimumHeight(max(180, len(records) * 32))
        bars.update()

    kinds.currentIndexChanged.connect(refresh)
    methods.currentIndexChanged.connect(refresh)
    refresh()
    close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
    close.rejected.connect(dialog.reject)
    layout.addWidget(close)
    dialog.exec()
