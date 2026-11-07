"""Import actions and transparent match/error results for numbered analysis data."""

from copy import deepcopy
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QLabel,
    QVBoxLayout,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QMessageBox,
    QAbstractItemView,
    QLineEdit,
)
from .sales_analysis import load_analysis, import_summary
from .floor_status import money
from .performance_validation import period_text, reconcile


class SalesAnalysisMixin:
    def import_analysis_path(self, path):
        snapshot = load_analysis(
            path,
            dict(
                absolute=self.analysis_settings["tolerance_absolute"],
                relative=self.analysis_settings["tolerance_relative"],
            ),
        )  # Parse before changing any current state.
        before = self.state()
        from .replacement import episode
        from .performance_history import archive, identity

        history = archive(self.performance_history, self.analysis_state)
        existing = next((e["snapshot"] for e in history if e["id"] == identity(snapshot)), None)
        if existing and existing.get("period_source") == "manual":
            snapshot.update(
                {k: existing[k] for k in ("period_start", "period_end", "period_source")}
            )
        from .machine_matching import resolve_matches

        matches = resolve_matches(self.machines, snapshot["records"])
        by_number = {matches[m.uid].number: m for m in self.machines if matches[m.uid].matched}
        for number, record in snapshot["records"].items():
            if existing:
                record["product_context"] = deepcopy(
                    existing["records"].get(number, {}).get("product_context")
                )
            elif number in by_number:
                record["product_context"] = episode(by_number[number])
        snapshot["issues"] = reconcile(self.machines, snapshot, self.analysis_state)
        from .analysis_settings import normalize_type, type_style

        history = archive(history, snapshot)
        settings = deepcopy(self.analysis_settings)
        for record in snapshot["records"].values():
            name = normalize_type(record.get("machine_name", ""))
            if name and name not in settings["types"]:
                canonical_name = next(
                    (k for k in settings["types"] if k.casefold() == name.casefold()),
                    name,
                )
                if canonical_name != name:
                    settings["aliases"][name] = canonical_name
                settings["types"].setdefault(canonical_name, type_style(canonical_name, settings))
        from .analysis_settings import validate_settings

        self.analysis_settings = validate_settings(settings)
        self.print_options.analysis_settings = deepcopy(self.analysis_settings)
        self.performance_history = history
        self.analysis_state = snapshot
        self.sync()
        self.record(before, "売上分析Excel読込")
        self.schedule_save()
        return import_summary(self.machines, snapshot)

    def import_analysis_dialog(self, *_):
        path, _ = QFileDialog.getOpenFileName(
            self, "売上分析Excelを開く", "", "Excel (*.xlsx *.xlsm)"
        )
        if not path:
            return
        try:
            self.import_analysis_path(path)
            self.show_analysis_report()
        except Exception as exc:
            QMessageBox.warning(self, "売上分析Excel読込エラー", str(exc))

    def analysis_summary_text(self):
        data = import_summary(self.machines, self.analysis_state)
        return (
            f"{data['file_name']} ／ 実績{data['read']}件・照合更新{data['matched']}件・未照合{data['unmatched_count']}件・数値エラー{data['numeric_errors']}件（読込エラー計{data['read_errors']}件）"
            f" ／ エラー{data['errors']}・警告{data['warnings']} ／ {period_text(self.analysis_state)} ／ 抽出 {self.analysis_state.get('extracted_at') or '不明'} ／ 最終読込（JST）{data['loaded_at'].replace('T',' ')}"
        )

    def update_analysis_note(self):
        if not hasattr(self, "analysis_note"):
            return
        loaded = bool(self.analysis_state["file_name"])
        self.analysis_note.setText(
            f"売上実績：{self.analysis_state['file_name']} ／ {period_text(self.analysis_state)}"
            if loaded
            else "売上分析Excel：未読込"
        )
        self.analysis_note.setVisible(loaded and not self._fullscreen_host)
        self.analysis_note.setToolTip(
            self.analysis_summary_text() + "\n" + self.analysis_state["source"]
        )

    def print_data_timestamp(self):
        if self.analysis_state["file_name"]:
            return f"{self.analysis_state['loaded_at']}（売上分析 {self.analysis_state['file_name']} を優先、最新欠損は不明）"
        return self.loaded_at

    def show_analysis_report(self, *_):
        dialog = QDialog(self)
        dialog.setWindowTitle("売上分析Excelの読込結果")
        dialog.resize(1000, 650)
        layout = QVBoxLayout(dialog)
        summary = QLabel(self.analysis_summary_text())
        summary.setWordWrap(True)
        layout.addWidget(summary)
        note = QLabel(
            "機器番号が一致した実績だけを表示へ適用します。商品名・設置日・配置・投入予定・運営方法は変更しません。\nアベ比はH列の平均売上÷既存基準アベレージ×100です。基準不明は —、G列の稼働日数は設置日数に使用しません。"
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        search = QLineEdit()
        search.setPlaceholderText("異常の番号・種類・内容・対応方法を検索")
        layout.addWidget(search)
        info = import_summary(self.machines, self.analysis_state)
        tabs = QTabWidget()
        layout.addWidget(tabs, 1)

        def table(headers, rows):
            widget = QTableWidget(len(rows), len(headers))
            from .theme import style_table

            style_table(widget)
            widget.setHorizontalHeaderLabels(headers)
            widget.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
            for i, row in enumerate(rows):
                for j, value in enumerate(row):
                    from .analysis_views import ValueItem

                    item = ValueItem(value)
                    widget.setItem(i, j, item)
            widget.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
            widget.setSortingEnabled(True)

            def filter_rows(text):
                for i in range(widget.rowCount()):
                    widget.setRowHidden(
                        i,
                        bool(text)
                        and not any(
                            text.casefold() in widget.item(i, j).text().casefold()
                            for j in range(widget.columnCount())
                        ),
                    )

            search.textChanged.connect(filter_rows)
            return widget

        rows = []
        for number in info["unmatched"]:
            r = self.analysis_state["records"][number]
            rows.append(
                (
                    r["raw_number"],
                    number,
                    r["reference_name"],
                    money(r["values"]["sales"]),
                    money(r["values"]["average"]),
                )
            )
        rows.extend(
            (r["number"], "正規化不能", "—", "—", "—")
            for r in self.analysis_state.get("unmatched_rows", [])
        )
        tabs.addTab(
            table(["Excel機器番号", "照合用番号", "商品名（参考）", "売上金額", "平均売上"], rows),
            f"未照合（{len(rows)}件）",
        )
        errors = [(r["row"], r["number"], r["message"]) for r in self.analysis_state["errors"]]
        tabs.addTab(
            table(["Excel行", "番号", "読込エラー"], errors), f"読込エラー（{len(errors)}件）"
        )
        layout.addWidget(
            QLabel(
                f"付随情報などの除外：{info['ignored']}行 ／ 重複：{info['duplicates']}行（同一値は一度だけ、異なる値の重複は照合せず除外）"
            )
        )
        issues = [
            (i["number"], i["severity"], i["kind"], i["message"], i["remedy"])
            for i in info["issues"]
        ]
        tabs.addTab(
            table(["機器番号", "重要度", "種類", "内容", "対応方法"], issues),
            f"異常・情報（{len(issues)}件）",
        )
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        dialog.exec()
