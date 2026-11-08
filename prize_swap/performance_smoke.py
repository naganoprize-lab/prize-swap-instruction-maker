"""Packaged feature verification using generated, non-customer 300-booth reports."""

from copy import deepcopy
from dataclasses import asdict
from datetime import date, timedelta
from pathlib import Path
from time import perf_counter
from openpyxl import Workbook
from PySide6.QtCore import QDate
from .floor_data import Machine, save_layout
from .floor_print import FloorPrintOptions, floor_printer
from .floor_status import filter_status
from .sales_analysis import HEADERS
from .replacement import assess, comparable_trend
from .method_analysis import totals
from .pdf_validation import require_searchable_pdf, normalized_pdf_text


def run_analysis_checks(floor, output):
    from .sales_analysis import empty_snapshot

    floor.analysis_state = empty_snapshot()
    floor.performance_history = []
    floor.machines = [
        Machine(
            number=f"{20000+i}右",
            product=f"CHECK{i:03} 日本語商品",
            kind="UFO9 third",
            operation_method=("橋渡し", "リング", "フック")[i % 3],
            x=i % 20 * 270,
            y=i // 20 * 200,
            width=260,
            height=190,
            average=1000,
            ratio=100,
            installed_on="2026-10-01",
        )
        for i in range(300)
    ]
    floor.reference_date.setDate(QDate(2026, 11, 7))
    floor.rebuild()
    for run in (0, 1):
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "景品運営分析表"
        for column, label in HEADERS.items():
            sheet.cell(1, column + 1, label)
        for i in range(300):
            average = (1000 + i * 10) * (1 if run == 0 else 0.75)
            sales = average * 3
            sheet.append(
                [
                    None,
                    f"{20000+i}-02",
                    "UFO9 third",
                    None,
                    "売上登録用",
                    sales,
                    3,
                    average,
                    sales * 0.4,
                    0.6,
                    0.05,
                    10,
                    sales / 10,
                    None,
                    500,
                    sales * 0.6,
                ]
            )
        sheet.append([None, "P/O率定義", "景品獲得数÷プレイ回数"])
        sheet.append([None, "期間(自)", f"2026/11/0{1 if run==0 else 4}"])
        sheet.append([None, "期間(至)", f"2026/11/0{3 if run==0 else 6}"])
        sheet.append([None, "検索日時: 2026/11/07 18:19:35"])
        path = output / f"分析確認{run}.xlsx"
        workbook.save(path)
        workbook.close()
        summary = floor.import_analysis_path(path)
        assert (
            summary["read"] == summary["matched"] == 300
            and not summary["unmatched"]
            and not summary["errors"]
        )
    floor.import_analysis_path(path)
    assert len(floor.performance_history) == 2
    a = assess(
        floor.machines[0], date(2026, 11, 7), floor.analysis_settings, floor.performance_history
    )
    assert abs(a["trend"] - 0.75) < 1e-8  # Same length, disjoint periods; total sales only.
    raw_trend, raw_sources = comparable_trend(floor.machines[0], floor.performance_history)
    assert abs(raw_trend - 0.75) < 1e-8 and len(raw_sources) == 2  # History is retained.
    before = [asdict(m) for m in floor.machines]
    ids = {k: id(n) for k, n in floor.nodes.items()}
    started = perf_counter()
    for query, count in [("20000右", 1), ("CHECK", 300), ("NO_MATCH", 0), ("", 300)]:
        floor.floor_search.setText(query)
        assert len(floor.visible_nodes()) == 300 and len(floor._match_uids) == count
        assert {k: id(n) for k, n in floor.nodes.items()} == ids
    duration = perf_counter() - started
    for mode in (
        "ratio",
        "planned",
        "age",
        "po_rate",
        "cost_rate",
        "sales_metric",
        "machine_type",
        "assessment",
    ):
        floor.set_color_filter(mode)
    assert [asdict(m) for m in floor.machines] == before
    assert len(totals(floor.machines, floor.analysis_settings)) == 3
    previews = {}
    for mode in ("po_rate", "cost_rate", "sales_metric", "machine_type", "assessment"):
        options = FloorPrintOptions(
            mode="list", color_filter=mode, analysis_settings=floor.analysis_settings
        )
        floor.set_print_options(options)
        path = output / f"分析フィルター_{mode}.pdf"
        plan = floor.paint_floor(floor_printer(options, path))
        text = normalized_pdf_text(
            "".join(p["text"] for p in require_searchable_pdf(path)["pages"])
        )
        for i in range(300):
            assert text.count(f"CHECK{i:03}") == 1
        previews[mode] = len(plan.pages)
    state = floor.state()
    save_layout(output / "分析保存.json", state)
    floor.restore(state)
    assert floor.state() == state
    return dict(
        machines=300,
        all_visible_during_search=True,
        node_identity_preserved=True,
        history_files=2,
        trend=a["trend"],
        average_history_preserved=True,
        method_groups=3,
        filter_pdf_pages=previews,
        search_seconds=duration,
        roundtrip=True,
    )
