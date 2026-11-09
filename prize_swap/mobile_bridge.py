"""Adapters to existing Windows services; reference data is never imported from Android."""

from copy import deepcopy
from dataclasses import asdict
from datetime import date, timedelta
from pathlib import Path
from .mobile_package import (
    create_package,
    products_from_entries,
    now,
    validate_documents,
)
from .mobile_sync import SyncStore
from .core import AppError, save_session
from .machine_matching import machine_key, normalize_number, normalize_reference

MODES = (
    "ratio",
    "sales_metric",
    "po_rate",
    "cost_rate",
    "age",
    "machine_type",
    "assessment",
    "planned",
)


def reference_frame(
    machines,
    snapshot,
    settings,
    day,
    frame_id="current",
    label="現在",
    coverage=None,
    assignments=None,
    performance_history=None,
):
    from .analysis_settings import sales_boundaries
    from .analytics_data import machine_record
    from .floor_status import filter_status
    from .machine_matching import machine_key
    from .radar import evaluate, score_record

    bounds = sales_boundaries(machines, settings, snapshot)
    effective = deepcopy(settings)
    effective["_sales_bounds"] = bounds
    assignments = assignments or {}
    peers = (
        []
        if getattr(bounds, "snapshot", None)
        else [score_record(m, day, settings, assignments) for m in machines]
    )
    records = []
    for m in machines:
        r = machine_record(
            m, day, settings, assignments, performance_history, sales_bounds=bounds
        )
        r["machine_id"] = m.uid
        r["grades"] = {
            k: dict(
                zip(
                    ("color", "label"),
                    filter_status(
                        m,
                        k,
                        day,
                        bool(assignments.get(machine_key(m))),
                        settings=effective,
                    ),
                )
            )
            for k in MODES
        }
        r["radar"] = evaluate(m, day, settings, peers, assignments, bounds)
        # File names aid provenance; machine.analysis/source paths are not transferred.
        for key in ("source_file",):
            r[key] = (r.get(key) or "").replace("\\", "/").rsplit("/", 1)[-1]
        records.append(r)
    return dict(
        frame_id=frame_id,
        label=label,
        period_start=snapshot.get("period_start"),
        period_end=snapshot.get("period_end"),
        reference_date=day.isoformat(),
        coverage=coverage or {},
        reference_count=len(snapshot.get("records", {})),
        records=records,
    )


def export_data(floor, documents, history_ranges=()):
    machines = deepcopy(floor.physical_machines)
    settings = deepcopy(floor.analysis_settings)
    # Keep only portable identity/context. Never transfer local source file paths.
    masters = [
        dict(
            machine_id=m.uid,
            number=machine_key(m) or normalize_number(m.number, allow_bare=True) or "",
            management_number=normalize_reference(m.source_key)
            or normalize_reference(m.number)
            or "",
            machine_type=m.kind,
            product_name=m.product,
            installed_on=m.installed_on,
            operating_method=m.operation_method,
            windows_fields={},
        )
        for m in machines
    ]
    layout = [
        dict(
            machine_id=m.uid,
            x=m.x,
            y=m.y,
            width=m.width,
            height=m.height,
            font_size=m.font_size,
        )
        for m in machines
    ]
    day = floor.reference_day()
    snapshot = deepcopy(floor.analysis_state)
    frames = [
        reference_frame(
            deepcopy(floor.machines),
            snapshot,
            settings,
            day,
            assignments=deepcopy(floor.assignments()),
            performance_history=deepcopy(floor.performance_history),
        )
    ]
    history = []
    if history_ranges:
        from .temporal_data import build_frame, attach_comparison

        lib = floor.temporal.library
        for start, end, label in history_ranges:
            docs, coverage = lib.selection(
                start, end, floor.temporal.settings["folder"]
            )
            overrides = {
                d["id"]: lib.preference("overrides:" + d["id"], {}) for d in docs
            }
            frame = build_frame(machines, docs, start, end, coverage, overrides)
            attach_comparison(
                frame, machines, lib, floor.temporal.settings["folder"], settings
            )
            history.append(
                reference_frame(
                    frame.machines,
                    frame.snapshot,
                    settings,
                    date.fromisoformat(end),
                    "period:" + start + ":" + end,
                    label,
                    coverage,
                )
            )
    return dict(
        machines=dict(items=masters, references=deepcopy(floor.number_references)),
        layout=dict(coordinate_unit="windows-scene", items=layout),
        sales=dict(frames=frames),
        history=dict(frames=history),
        settings=dict(
            analysis=settings, methods=["橋渡し", "リング", "フック"], theme="system"
        ),
        instructions=dict(documents=documents),
    )


class MobileService:
    def __init__(self, window):
        self.w = window
        self.store = SyncStore(window.floor.storage.with_suffix(".mobile.json"))

    def export(self, kind="full", ranges=()):
        docs = self.store.observe(self.w.products)
        data = export_data(self.w.floor, [] if kind == "reference" else docs, ranges)
        return create_package(
            data,
            kind,
            "windows",
            self.store.state["device_id"],
            self.store.state["dataset_id"],
        )

    def preview(self, package):
        if package.manifest["dataset_id"] != self.store.state["dataset_id"]:
            raise AppError(
                "別の売り場データセットです。このWindowsデータには取り込めません。"
            )
        if package.manifest["package_id"] in self.store.state["packages"]:
            return []
        self.store.observe(self.w.products)
        return self.store.plan(package.data["instructions"]["documents"])

    def apply(self, package, decisions):
        w = self.w
        self.preview(package)
        if package.manifest["package_id"] in self.store.state["packages"]:
            return 0
        docs = self.store.resolve(package.data["instructions"]["documents"], decisions)
        proposed = {
            **self.store.state["documents"],
            **{d["document_id"]: d for d in docs},
        }
        validate_documents(list(proposed.values()))
        from collections import deque

        affected = {d["document_id"] for d in docs}
        owned = {
            e["entry_id"]: d["document_id"]
            for d in self.store.state["documents"].values()
            for e in d["body"]["entries"]
        }
        incoming = {
            d["document_id"]: deque(
                sorted(
                    products_from_entries(d["body"]["entries"]), key=lambda p: p.order
                )
                if not d["deleted"]
                else []
            )
            for d in docs
        }
        products = []
        # Keep each document's physical slots, so unchanged equal-order rows do not swap numbers.
        for current in w.products:
            key = owned.get(current.entry_id)
            if key not in affected:
                products.append(deepcopy(current))
            elif incoming[key]:
                products.append(incoming[key].popleft())
        for d in docs:
            products.extend(incoming[d["document_id"]])
        ids = [p.entry_id for p in products]
        if len(ids) != len(set(ids)):
            raise AppError("別の指示書と指示行IDが重複しています。")
        products_from_entries(
            [asdict(p) | dict(day=p.day.isoformat()) for p in products]
        )
        # Concrete recovery file before touching current products or metadata.
        backup = (
            self.store.path.parent
            / "mobile-backups"
            / ("before-" + now().replace(":", "-") + ".json")
        )
        backup.parent.mkdir(parents=True, exist_ok=True)
        save_session(
            backup, w.products, w.selected_day(), w.source, label_state=w.labels.state()
        )
        from .instruction_editor import InstructionCommand, all_state

        before = all_state(w)
        old = deepcopy(self.store.state)
        for d in docs:
            self.store.remember(d)
        self.store.state["packages"] = (
            self.store.state["packages"] + [package.manifest["package_id"]]
        )[-1000:]
        self.store.state["transfers"] = (
            self.store.state["transfers"]
            + [
                dict(
                    timestamp=now(),
                    direction="import",
                    package_id=package.manifest["package_id"],
                    status="imported",
                )
            ]
        )[-200:]
        after = deepcopy(self.store.state)
        self.store.state = old
        service = self

        class ImportCommand(InstructionCommand):
            def apply(self, state):
                target = after if state is self.after else old
                service.store.save(target)
                try:
                    super().apply(state)
                except Exception:
                    service.store.save(old if target is after else after)
                    raise

        w.instruction_history.push(
            ImportCommand(w, before, (products, before[1]), "Android指示書の取込")
        )
        return len(docs)
