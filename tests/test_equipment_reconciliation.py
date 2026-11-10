"""Identity regression tests use synthetic workbooks, never customer paths/data."""

from dataclasses import asdict
from copy import deepcopy
import pytest
from openpyxl import load_workbook
from prize_swap.core import AppError
from prize_swap.floor_data import Machine
from prize_swap.machine_matching import normalize_number, prepare_references, resolve_matches
from prize_swap.sales_analysis import load_analysis, apply_snapshot, import_summary
from prize_swap.reconciliation_report import reconciliation_rows, problem_category
from test_sales_analysis import report_file, data_row


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("20088-01", "20088左"),
        ("20088-02", "20088右"),
        ("20102-02", "20102右"),
        ("20112-02", "20112右"),
        ("20170-01", "20170左"),
        ("20170-02", "20170右"),
        (" ２０１１２－０２　", "20112右"),
        ("20170‑1", "20170左"),
        ("20170–02", "20170右"),
        ("20170—2", "20170右"),
        ("２０１７０右", "20170右"),
        ("20170", ""),
        ("20170-03", ""),
        ("L_66", ""),
        ("20170-001", ""),
    ],
)
def test_equipment_number_normalization(raw, expected):
    assert (normalize_number(raw) or "") == expected


def test_equipment_header_wins_over_blank_or_different_management_column(tmp_path):
    rows = [data_row("20088-01"), data_row("20088-02")]
    rows[1][0] = "20170-01"  # Management column must never become the sales key.
    path = report_file(tmp_path / "report.xlsm", rows)
    book = load_workbook(path)
    sheet = book.active
    # Reorder every column; reading must use headers, not positions.
    original = list(sheet.values)
    book.remove(sheet)
    sheet = book.create_sheet("景品運営分析表")
    for row in original:
        sheet.append(list(reversed(row)))
    book.save(path)
    book.close()
    snapshot = load_analysis(path)
    assert set(snapshot["records"]) == {"20088左", "20088右"}
    machines = [Machine(source_key="L_66"), Machine(source_key="R_66")]
    prepare_references(
        machines,
        [dict(source_key="L_66", number="20088左"), dict(source_key="R_66", number="20088右")],
    )
    uids = [m.uid for m in machines]
    apply_snapshot(machines, snapshot)
    assert [m.resolved_number for m in machines] == ["20088左", "20088右"]
    assert [m.uid for m in machines] == uids
    assert import_summary(machines, snapshot)["matched"] == 2
    assert all(r[3] == "照合済み" for r in reconciliation_rows(machines, snapshot))
    # A missing reference must not delete Excel records or attach them elsewhere.
    unknown = Machine(source_key="L_99")
    assert not resolve_matches([unknown], snapshot["records"])[unknown.uid].matched
    assert len(snapshot["records"]) == 2


def test_duplicates_and_unknown_side_are_explained_without_forced_matching(tmp_path):
    path = report_file(
        tmp_path / "report.xlsx",
        [
            data_row("20088-01"),
            data_row("20088-1", sales=50),
            data_row("20088-02"),
            data_row("20170"),
            data_row("invalid"),
        ],
    )
    snapshot = load_analysis(path)
    assert set(snapshot["records"]) == {"20088右"}
    assert snapshot["duplicate_rows"] == 1
    assert len(snapshot["unmatched_rows"]) == 2
    m = Machine(number="20088左")
    before = deepcopy(asdict(m))
    rows = reconciliation_rows([m], snapshot)
    assert all(r[3] != "照合済み" for r in rows)
    assert any("左右区分が不明" in r[4] for r in rows)
    assert asdict(m) == before
    assert problem_category("同じ管理ナンバーに複数の機械番号があります。").startswith("管理番号")


def test_duplicate_equipment_headers_rejected_before_application(tmp_path):
    path = report_file(tmp_path / "report.xlsx", [data_row()])
    book = load_workbook(path)
    book.active["A1"] = "機器番号"
    book.save(path)
    book.close()
    with pytest.raises(AppError, match="ヘッダー.*重複"):
        load_analysis(path)


def test_normalized_export_preserves_android_uid_and_qr(tmp_path):
    from prize_swap.ui import Window
    from prize_swap.mobile_bridge import export_data
    from prize_swap.mobile_package import create_package, read_package
    from prize_swap.mobile_qr import qr_uri

    window = Window(layout_storage=tmp_path / "floor.json")
    try:
        floor = window.floor
        floor.machines = [
            Machine(number="２００８８–１", source_key="L_66"),
            Machine(source_key="R_66"),
            Machine(source_key="L_182"),
        ]
        before = {m.uid: qr_uri(m.uid) for m in floor.machines}
        prepare_references(
            floor.machines,
            [dict(source_key="R_66", number="20088-02"), dict(source_key="L_182", number="20201")],
        )
        snapshot = load_analysis(
            report_file(
                tmp_path / "sales.xlsx",
                [data_row("20088-01"), data_row("20088-02"), data_row("20201-01")],
            )
        )
        apply_snapshot(floor.machines, snapshot)
        data = export_data(floor, [])
        got = read_package(create_package(data, "full", "windows", "test-pc", "test-dataset"))
        assert {m["number"] for m in got.data["machines"]["items"]} == {
            "20088左",
            "20088右",
            "20201左",
        }
        assert {m["machine_id"] for m in got.data["machines"]["items"]} == set(before)
        assert {m.uid: qr_uri(m.uid) for m in floor.machines} == before
    finally:
        window.dirty = False
        window.floor.timer.stop()
        window.instruction_save_timer.stop()
        window.close()


def test_daily_machine_keys_do_not_mix_dates_or_reimport_twice(tmp_path):
    from datetime import date
    from prize_swap.daily_reference import ReferenceLibrary
    from test_daily_reference import workbook

    folder = tmp_path / "source"
    folder.mkdir()
    workbook(folder / "day1.xlsx", day="2026/11/06", sales=100)
    workbook(folder / "day2.xlsx", day="2026/11/07", sales=200)
    library = ReferenceLibrary(tmp_path / "library.sqlite")
    library.scan(folder)
    library.scan(folder)
    selection, metadata = library.selection("2026-11-06", "2026-11-07", folder)
    assert len(selection) == 2
    assert sorted(d["snapshot"]["records"]["20111右"]["values"]["sales"] for d in selection) == [
        100,
        200,
    ]


def test_short_suffix_layout_annotations_do_not_create_machine_cards(tmp_path):
    from openpyxl import Workbook
    from prize_swap.floor_data import import_layout

    book = Workbook()
    sheet = book.active
    sheet.title = "投入中レイアウト"
    sheet["B3"] = "L_66"
    sheet["C3"] = "景品"
    sheet["J12"] = "11-1"
    sheet["K12"] = "設置日/設定2"
    sheet["J20"] = "11-2"
    sheet["K20"] = "設置日/設定2"
    sheet["B30"] = "20088-1"
    sheet["C30"] = "実マシン"
    sheet["B31"] = "売上/アベレージ"
    sheet["C31"] = 100
    refs = book.create_sheet("管理番号参考値")
    refs.append(["L_66", "20111左"])
    path = tmp_path / "layout.xlsx"
    book.save(path)
    book.close()
    machines, _ = import_layout(path)
    assert [m.number for m in machines] == ["20111左", "20088左"]


def test_bare_reference_is_valid_single_booth_only_when_sales_key_is_unique(tmp_path):
    m = Machine(source_key="L_182")
    refs = [dict(source_key="L_182", number="20201")]
    prepare_references([m], refs)
    assert not m.reference_warning and m.reference_number == "20201"
    snapshot = load_analysis(report_file(tmp_path / "single.xlsx", [data_row("20201-01")]))
    apply_snapshot([m], snapshot)
    assert m.resolved_number == "20201左" and m.match_method == "reference"
    assert m.reference_number == "20201" and m.analysis["values"]["sales"] == 30000
    both = load_analysis(
        report_file(tmp_path / "both.xlsx", [data_row("20201-01"), data_row("20201-02")])
    )
    apply_snapshot([m], both)
    assert m.analysis is None and m.match_method == "review"
    assert "複数候補" in m.match_message
    other = Machine(number="20201左")
    apply_snapshot([m, other], snapshot)
    assert all(x.analysis is None and x.match_method == "review" for x in (m, other))
    invalid = Machine(source_key="R_1")
    prepare_references([invalid], [dict(source_key="R_1", number="20201-03")])
    assert invalid.reference_warning


def test_single_booth_does_not_ignore_a_rejected_opposite_side(tmp_path):
    m = Machine(source_key="L_1")
    prepare_references([m], [dict(source_key="L_1", number="20088")])
    snapshot = load_analysis(
        report_file(
            tmp_path / "sales.xlsx",
            [data_row("20088-01"), data_row("20088-02"), data_row("20088-02", sales=50)],
        )
    )
    assert set(snapshot["records"]) == {"20088左"}
    apply_snapshot([m], snapshot)
    assert m.analysis is None and m.match_method == "review"


def test_existing_direct_number_keeps_priority_over_import_provenance(tmp_path):
    m = Machine(number="20088右", direct_number_raw="20088-01")
    snapshot = load_analysis(
        report_file(tmp_path / "sales.xlsx", [data_row("20088-01"), data_row("20088-02", sales=50)])
    )
    apply_snapshot([m], snapshot)
    assert m.resolved_number == "20088右" and m.analysis["values"]["sales"] == 50
