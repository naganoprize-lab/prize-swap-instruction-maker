from copy import deepcopy
from datetime import date
import io, json, zipfile
import pytest
from prize_swap.core import Product, AppError
from prize_swap.mobile_package import (
    create_package,
    read_package,
    products_from_entries,
    encoded,
)
from prize_swap.mobile_sync import (
    body_of,
    new_document,
    revise,
    three_way,
    SyncStore,
    relation,
)
from prize_swap.analysis_settings import validate_settings


def sample():
    p = Product(date(2026, 11, 9), "", machines=["20088左"], included=True)
    d = new_document(body_of([p], "空欄指示", "2026-11-09"), "windows-test")
    return dict(
        machines=dict(items=[], references=[]),
        layout=dict(coordinate_unit="windows-scene", items=[]),
        sales=dict(frames=[]),
        history=dict(frames=[]),
        settings=dict(
            analysis=validate_settings(),
            methods=["橋渡し", "リング", "フック"],
            theme="system",
        ),
        instructions=dict(documents=[d]),
    )


def package(data=None):
    return create_package(
        data or sample(), "full", "windows", "pc-test", "dataset-test"
    )


def repack(raw, edit):
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        files = {n: z.read(n) for n in z.namelist()}
    edit(files)
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        for k, v in files.items():
            z.writestr(k, v)
    return out.getvalue()


def test_roundtrip_preserves_blank_and_assignment():
    s = sample()
    got = read_package(package(s))
    assert got.data == s
    p = products_from_entries(
        got.data["instructions"]["documents"][0]["body"]["entries"]
    )[0]
    assert p.name == "" and p.units == "" and p.included
    assert (
        p.booth_assignments[0]["assignment_id"]
        == s["instructions"]["documents"][0]["body"]["entries"][0]["booth_assignments"][
            0
        ]["assignment_id"]
    )


@pytest.mark.parametrize(
    "edit",
    [
        lambda f: f.update({"../outside": b"x"}),
        lambda f: f.update({"data/settings.json": b"{}"}),
        lambda f: f.update({"manifest.json": b'{"version":1,"version":1}'}),
        lambda f: f.update(
            {
                "manifest.json": f["manifest.json"].replace(
                    b'"version":1', b'"version":2'
                )
            }
        ),
    ],
)
def test_reject_unsafe_and_corrupt(edit):
    with pytest.raises(AppError):
        read_package(repack(package(), edit))


def test_conflict_requires_choice_and_merges_independent_fields(tmp_path):
    store = SyncStore(tmp_path / "sync.json")
    d = sample()["instructions"]["documents"][0]
    store.remember(d)
    left = deepcopy(d["body"])
    left["entries"][0]["name"] = "Windows"
    right = deepcopy(d["body"])
    right["entries"][0]["notes"] = "Android"
    l = revise(d, left, "pc")
    r = revise(d, right, "tablet")
    store.remember(l)
    assert relation(l, r) == "conflict"
    with pytest.raises(AppError):
        store.resolve([r], {})
    merged = store.resolve([r], {r["document_id"]: dict(action="merge")})[0]
    assert (
        merged["body"]["entries"][0]["name"] == "Windows"
        and merged["body"]["entries"][0]["notes"] == "Android"
    )
    assert r["revision_id"] in merged["ancestors"]


def test_delete_vs_edit_not_silently_merged():
    b = dict(entries=[dict(entry_id="e", name="old")])
    l = dict(entries=[])
    r = dict(entries=[dict(entry_id="e", name="new")])
    _, conflicts = three_way(b, l, r)
    assert conflicts
    merged, c = three_way(b, l, r, {"/entries/e": "incoming"})
    assert not c and merged == r


def test_export_reference_uses_existing_services_and_import_undo(tmp_path, qt_app):
    from prize_swap.ui import Window
    from prize_swap.floor_data import Machine
    from prize_swap.mobile_bridge import MobileService
    from prize_swap.instructions import prepare_entry, booths
    from prize_swap.mobile_sync import revise

    w = Window(tmp_path / "floor.json")
    try:
        m = Machine(
            "20111右",
            "UFO",
            "堅あげ 270",
            0,
            0,
            source_key="L_66",
            sales=6000,
            dispensed=11,
            ratio=80,
        )
        w.floor.machines = [m]
        w.floor.sync()
        p = Product(date(2026, 11, 9), "", machines=["20111右"], included=True)
        prepare_entry(p)
        w.products = [p]
        w.update_dates()
        w.refresh()
        service = w.mobile_page.service
        raw = service.export()
        pkg = read_package(raw)
        r = pkg.data["sales"]["frames"][0]["records"][0]
        assert r["sales"] == 6000 and r["machine_id"] == m.uid and r["ratio"] == 80
        assert r["grades"]["sales_metric"]["color"] != "#AEB7C2"
        d = pkg.data["instructions"]["documents"][0]
        body = deepcopy(d["body"])
        body["entries"][0]["notes"] = "Androidで追記"
        incoming = revise(d, body, "tablet")
        data = deepcopy(pkg.data)
        data["instructions"]["documents"] = [incoming]
        edited = read_package(
            create_package(
                data, "instructions", "android", "tablet", pkg.manifest["dataset_id"]
            )
        )
        assert service.preview(edited)[0]["relation"] == "incoming"
        service.apply(edited, {})
        assert w.products[0].name == "" and w.products[0].notes == "Androidで追記"
        assert booths(w.products, [m], [])[0].machine.uid == m.uid
        w.instruction_history.undo()
        assert w.products[0].notes == ""
        w.instruction_history.redo()
        assert w.products[0].notes == "Androidで追記"
        assert list((tmp_path / "mobile-backups").glob("*.json"))
    finally:
        w.dirty = False
        w.close()


def test_qr_labels_and_decode_fixture(tmp_path, qt_app):
    from prize_swap.floor_data import Machine
    from prize_swap.mobile_qr import qr_uri, parse_qr, qr_png, render_labels
    from PySide6.QtPrintSupport import QPrinter
    from pypdf import PdfReader

    m = Machine("20088左", "UFO", "", 0, 0, source_key="L_66", uid="fixture-machine")
    assert parse_qr(qr_uri(m.uid)) == m.uid
    for bad in [
        "https://example.com",
        qr_uri(m.uid) + "?x=1",
        qr_uri(m.uid) + "/../a",
        qr_uri(m.uid) + "#x",
    ]:
        with pytest.raises(AppError):
            parse_qr(bad)
    out = tmp_path / "qr.pdf"
    p = QPrinter(QPrinter.PrinterMode.HighResolution)
    p.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
    p.setOutputFileName(str(out))
    render_labels(p, [m] * 30)
    assert len(PdfReader(out).pages) == 2
    qr_png(tmp_path / "qr.png", m)
    assert (tmp_path / "qr.png").stat().st_size > 1000


def test_manual_cost_and_identifiers_are_not_mutated_by_export(tmp_path, qt_app):
    from prize_swap.ui import Window
    from prize_swap.floor_data import Machine
    from dataclasses import asdict

    w = Window(tmp_path / "floor.json")
    try:
        m = Machine(
            "L_66", "UFO", "商品 020", 0, 0, source_key="L_66", sales=0, prize_cost=80
        )
        w.floor.machines = [m]
        w.floor.sync()
        before = deepcopy(asdict(m))
        raw = w.mobile_page.service.export("reference")
        pkg = read_package(raw)
        assert before == asdict(m)
        assert pkg.data["machines"]["items"][0]["number"] == ""
        assert pkg.data["machines"]["items"][0]["management_number"] == "L_66"
        assert pkg.data["sales"]["frames"][0]["records"][0]["sales"] == 0
        assert pkg.data["instructions"]["documents"] == []
    finally:
        w.dirty = False
        w.close()


def test_bad_assignment_and_method_data_rejected_before_import():
    for change in ("assignment", "method", "negative_plan"):
        data = sample()
        e = data["instructions"]["documents"][0]["body"]["entries"][0]
        if change == "assignment":
            e["booth_assignments"][0]["token"] = "different"
        elif change == "method":
            e["machine_methods"] = {"not-a-machine": False}
        else:
            e["planned_booths"] = {"橋渡し": -1}
        with pytest.raises(AppError):
            package(data)


def test_history_export_uses_same_period_rollup(tmp_path, qt_app):
    from test_daily_reference import workbook
    from prize_swap.floor_data import Machine
    from prize_swap.ui import Window

    w = Window(tmp_path / "floor.json")
    try:
        folder = tmp_path / "daily"
        folder.mkdir()
        workbook(folder / "a.xlsx", "2026/11/06", sales=100)
        workbook(folder / "b.xlsx", "2026/11/07", sales=200)
        w.floor.machines = [
            Machine("20111右", "UFO", "景品 100", 50, 70, source_key="R_66")
        ]
        w.floor.sync()
        w.floor.temporal.settings["folder"] = str(folder)
        w.floor.temporal.library.scan(folder)
        raw = w.mobile_page.service.export(
            "full", [("2026-11-06", "2026-11-07", "選択期間")]
        )
        frame = read_package(raw).data["history"]["frames"][0]
        assert frame["records"][0]["sales"] == 300
        assert frame["records"][0]["machine_id"] == w.floor.machines[0].uid
        assert (frame["period_start"], frame["period_end"]) == (
            "2026-11-06",
            "2026-11-07",
        )
        assert (w.floor.machines[0].x, w.floor.machines[0].y) == (50, 70)
    finally:
        w.dirty = False
        w.close()


def test_reference_metric_types_and_radar_bounds_are_validated():
    package_data = read_package("shared/examples/windows-fixture.zip").data
    for field in ("sales", "score", "axes"):
        data = deepcopy(package_data)
        r = data["sales"]["frames"][0]["records"][0]
        if field == "sales":
            r["sales"] = "6,000円"
        elif field == "score":
            r["radar"][0]["score"] = 101
        else:
            r["radar"][0]["key"] = "cost_rate"
        with pytest.raises(AppError):
            package(data)


def test_updating_one_document_keeps_equal_order_print_numbers(tmp_path, qt_app):
    from prize_swap.ui import Window
    from prize_swap.instructions import booths

    w = Window(tmp_path / "floor.json")
    try:
        a = Product(date(2026, 11, 9), "A", entry_id="entry-a", order=1, included=True)
        b = Product(date(2026, 11, 9), "B", entry_id="entry-b", order=1, included=True)
        da = new_document(body_of([a], "A", "2026-11-09"), "pc", "doc-a")
        db = new_document(body_of([b], "B", "2026-11-09"), "pc", "doc-b")
        w.products = [a, b]
        w.update_dates()
        w.refresh()
        service = w.mobile_page.service
        service.store.remember(da)
        service.store.remember(db)
        service.store.save()
        changed = deepcopy(da["body"])
        changed["entries"][0]["notes"] = "Android edit"
        incoming = revise(da, changed, "tablet")
        data = sample()
        data["instructions"]["documents"] = [incoming, db]
        pkg = read_package(
            create_package(
                data,
                "instructions",
                "android",
                "tablet",
                service.store.state["dataset_id"],
            )
        )
        before = [(x.product.entry_id, x.number) for x in booths(w.products)]
        service.apply(pkg, {})
        assert [(x.product.entry_id, x.number) for x in booths(w.products)] == before
        assert w.products[0].notes == "Android edit"
    finally:
        w.dirty = False
        w.close()
