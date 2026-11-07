"""Atomic analysis exports; blanks stay blank and source workbooks are protected."""

import csv
import os
from pathlib import Path
import tempfile
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment
from .core import AppError
from .analytics_data import FIELDS, NUMERIC


def safe_csv(value):
    if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


def atomic_export(path, writer, protected=(), overwrite=False):
    path = Path(path).resolve()
    if any(path == Path(source).resolve() for source in protected if source):
        raise AppError("読込元Excelには保存できません。別の保存先を指定してください。")
    if path.exists() and not overwrite:
        raise AppError("同名ファイルが存在します。上書きを確認してから保存してください。")
    fd, temp = tempfile.mkstemp(dir=path.parent, prefix="." + path.stem + "-", suffix=path.suffix)
    os.close(fd)
    try:
        writer(Path(temp))
        os.replace(temp, path)
    finally:
        if Path(temp).exists():
            Path(temp).unlink()
    return path


def write_csv(path, headers, rows, protected=(), overwrite=False):
    def save(temp):
        with temp.open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow([safe_csv(value) for value in headers])
            for row in rows:
                writer.writerow([safe_csv(value) for value in row])

    return atomic_export(path, save, protected, overwrite)


def export_tables(page):
    metric = page.metric_choice.currentData()
    operation = page.operation.currentData()
    unit = (
        "件"
        if operation == "count"
        else "比率" if operation == "share" else NUMERIC.get(metric, ("", ""))[1]
    )
    headers = [
        "区分",
        "第2軸",
        "対象期間",
        "集計値（" + unit + "）",
        "対象台数",
        "有効件数",
        "計算方式",
    ]
    results = [
        [
            r["label"],
            r["series"],
            r["period_text"],
            r["value"],
            r["count"],
            r["valid_count"],
            r["mode"],
        ]
        for r in page.results
    ]
    keys = list(FIELDS)
    details_headers = [
        FIELDS[k] + ("（内部比率 0.05=5%）" if k in ("po_rate", "cost_rate") else "") for k in keys
    ] + ["更新日時", "抽出日時", "実績ファイル"]
    details = [
        [r.get(k) for k in keys] + [r["loaded_at"], r["extracted_at"], r["source_file"]]
        for r in page.records
    ]
    for key in NUMERIC:
        details_headers.extend(
            [FIELDS[key] + "・取得元", FIELDS[key] + "・更新日時", FIELDS[key] + "・対象期間"]
        )
    for row, r in zip(details, page.records):
        for key in NUMERIC:
            from .analytics_data import period_label

            row.extend(
                [
                    r["sources"][key]["source"],
                    r["sources"][key]["updated_at"],
                    period_label(r["metric_periods"][key]),
                ]
            )
    ids = {r["uid"]: r["number"] for r in page.owner.analytics.records()}
    kind = page.chart_type.currentData()
    value_key = (
        page.y_metric.currentData()
        if kind == "scatter"
        else (
            "dispensed"
            if kind == "histogram" or operation == "count"
            else "po_rate" if operation == "share" else metric
        )
    )

    def value_header(label, key):
        return (
            label + "（内部比率 0.05=5%）"
            if key in ("po_rate", "cost_rate")
            else label + "（" + NUMERIC.get(key, ("", ""))[1] + "）"
        )

    graph_headers = [
        "区分",
        "系列",
        value_header("値", value_key),
        value_header("横軸：" + page.x_metric.currentText(), page.x_metric.currentData()),
        value_header("縦軸：" + page.y_metric.currentText(), page.y_metric.currentData()),
        "対象期間",
        "対象マシン",
        "取得元",
        "ファイル名",
        "抽出日時",
        "読込日時",
    ]
    graph = [
        [
            r.get("label", ""),
            r.get("series", ""),
            r.get("value"),
            r.get("x"),
            r.get("y"),
            r.get("period_text", ""),
            "、".join(ids.get(uid, uid) for uid in r.get("uids", [])),
            r.get("source_kind"),
            r.get("source_file"),
            r.get("extracted_at"),
            r.get("loaded_at"),
        ]
        for r in page.chart_data
    ]
    output = {
        "集計結果": (headers, results),
        "詳細": (details_headers, details),
        "グラフデータ": (graph_headers, graph),
    }
    if page.chart_type.currentData() == "radar":
        output["グラフデータ"] = (
            ["項目", "実測値", "スコア", "目標・意味", "説明", "注意"],
            [
                [a["label"], a["actual"], a["score"], a["goal"], a["note"], a["warning"]]
                for a in page.radar.axes
            ],
        )
    if page.chart_type.currentData() == "cross":
        columns = sorted({r["series"] for r in page.results})
        keys = list(dict.fromkeys((r["period"], r["label"]) for r in page.results))
        mapping = {(r["period"], r["label"], r["series"]): r["value"] for r in page.results}
        output["クロス集計"] = (
            ["区分", "期間開始", "期間終了"] + columns,
            [
                [label, period[0], period[1]] + [mapping.get((period, label, c)) for c in columns]
                for period, label in keys
            ],
        )
    return output


def write_workbook(path, tables, metadata=(), protected=(), overwrite=False):
    def save(temp):
        book = Workbook()
        book.remove(book.active)
        for name, (headers, rows) in tables.items():
            sheet = book.create_sheet(name)
            sheet.append(headers)
            for row in rows:
                sheet.append(row)
                for cell in sheet[sheet.max_row]:
                    if isinstance(cell.value, str):
                        cell.data_type = "s"
                    elif isinstance(cell.value, (float, int)):
                        header = str(headers[cell.column - 1])
                        cell.number_format = (
                            "0.00%"
                            if "内部比率" in header or "集計値（比率）" in header
                            else '0.0"%"' if header == "アベ比" else "#,##0.00"
                        )
            for cell in sheet[1]:
                if isinstance(cell.value, str):
                    cell.data_type = "s"
                cell.font = Font(name="Yu Gothic", bold=True, color="1F2937")
                cell.fill = PatternFill("solid", fgColor="EDF1F5")
                cell.alignment = Alignment(wrap_text=True)
            sheet.freeze_panes = "A2"
            sheet.auto_filter.ref = sheet.dimensions
            for column in sheet.iter_cols(min_row=1, max_row=1):
                sheet.column_dimensions[column[0].column_letter].width = (
                    24 if "商品" not in str(column[0].value) else 55
                )
        meta = book.create_sheet("設定・取得元")
        meta.append(["項目", "値"])
        for key, value in metadata:
            meta.append([str(key), str(value)])
            meta.cell(meta.max_row, 1).data_type = "s"
            meta.cell(meta.max_row, 2).data_type = "s"
        meta.column_dimensions["A"].width = 25
        meta.column_dimensions["B"].width = 90
        try:
            book.save(temp)
        finally:
            book.close()

    return atomic_export(path, save, protected, overwrite)
