"""Reversible history and explicitly marked manual report periods."""

from copy import deepcopy
from PySide6.QtCore import QDate
from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QDialogButtonBox,
    QDateEdit,
    QFormLayout,
    QMessageBox,
)
from .analysis_views import make_table
from .performance_history import archive, identity, set_period, update_period
from .performance_validation import period_text


def manual_period(owner):
    if not owner.analysis_state["file_name"]:
        owner.message.emit("先に売上分析Excelを読み込んでください。")
        return
    dialog = QDialog(owner)
    dialog.setWindowTitle("集計対象期間を手動設定")
    form = QFormLayout(dialog)
    if owner.analysis_state.get("period_source") == "excel":
        form.addRow(QLabel("Excelに期間があります。変更後は「手動設定」と記録します。"))
    start, end = QDateEdit(), QDateEdit()
    for control, key in ((start, "period_start"), (end, "period_end")):
        control.setCalendarPopup(True)
        control.setDisplayFormat("yyyy/MM/dd")
        control.setDate(
            QDate.fromString(owner.analysis_state.get(key) or "", "yyyy-MM-dd")
            if owner.analysis_state.get(key)
            else QDate.currentDate()
        )
    form.addRow("開始日", start)
    form.addRow("終了日", end)
    buttons = QDialogButtonBox(
        QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
    )
    form.addRow(buttons)
    buttons.rejected.connect(dialog.reject)

    def save():
        try:
            updated = set_period(
                owner.analysis_state, start.date().toPython(), end.date().toPython()
            )
            before = owner.state()
            owner.performance_history = archive(owner.performance_history, owner.analysis_state)
            owner.performance_history = update_period(owner.performance_history, updated)
            owner.analysis_state = updated
            owner.record(before, "実績期間を手動設定")
            owner.update_info()
            dialog.accept()
        except Exception as exc:
            QMessageBox.warning(dialog, "集計期間", str(exc))

    buttons.accepted.connect(save)
    dialog.exec()


def show_history(owner):
    dialog = QDialog(owner)
    dialog.setWindowTitle("売上履歴（削除・復元）")
    dialog.resize(1100, 650)
    layout = QVBoxLayout(dialog)
    note = QLabel(
        "同一ファイルはSHA-256で識別し、二重登録しません。削除は復元可能です。期間が重なる実績を合算しません。最新画面の実績は削除後も維持します。履歴の選択だけで古い実績に切り替えません。"
    )
    note.setWordWrap(True)
    layout.addWidget(note)
    container = QVBoxLayout()
    layout.addLayout(container, 1)
    table = None

    def refresh():
        nonlocal table
        if table:
            container.removeWidget(table)
            table.deleteLater()
        rows = []
        for e in owner.performance_history:
            s = e["snapshot"]
            rows.append(
                [
                    "削除済み" if e["deleted"] else "有効",
                    s["file_name"],
                    period_text(s),
                    s.get("extracted_at") or "不明",
                    s["loaded_at"],
                    len(s["records"]),
                    e["id"],
                    s.get("filename_timestamp") or "不明",
                ]
            )
        table = make_table(
            [
                "状態",
                "元ファイル",
                "対象期間",
                "抽出日時",
                "初回読込日時",
                "実績数",
                "ファイル識別情報",
                "ファイル名の日時（参考）",
            ],
            rows,
        )
        container.addWidget(table)

    def change(deleted):
        keys = {table.item(i.row(), 6).text() for i in table.selectionModel().selectedRows()}
        if not keys:
            return
        before = owner.state()
        for e in owner.performance_history:
            if e["id"] in keys:
                e["deleted"] = deleted
        owner.record(before, "売上履歴の削除" if deleted else "売上履歴の復元")
        owner.dashboard.refresh()
        refresh()

    refresh()
    row = QHBoxLayout()
    layout.addLayout(row)
    for text, callback in [
        ("選択履歴を削除", lambda: change(True)),
        ("選択履歴を復元", lambda: change(False)),
        ("対象期間を手動設定", lambda: manual_period(owner)),
        ("閉じる", dialog.accept),
    ]:
        b = QPushButton(text)
        b.clicked.connect(callback)
        row.addWidget(b)
    dialog.exec()
