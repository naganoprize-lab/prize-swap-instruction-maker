"""Synthetic interchange fixtures only; never reads business files."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from datetime import date
from prize_swap.core import Product
from prize_swap.floor_data import Machine
from prize_swap.mobile_bridge import reference_frame
from prize_swap.mobile_package import create_package, encoded
from prize_swap.mobile_sync import body_of, new_document
from prize_swap.analysis_settings import validate_settings


def main():
    root = Path(__file__).resolve().parents[1] / "shared/examples"
    settings = validate_settings()
    machines = [
        Machine(
            "20111右",
            "UFO",
            "サンプル商品 270",
            0,
            0,
            uid="fixture-machine",
            source_key="L_66",
            sales=6000,
            ratio=80,
            dispensed=11,
        ),
        Machine(
            "20088左",
            "UFO",
            "サンプル商品 020",
            200,
            0,
            uid="fixture-zero",
            source_key="R_168",
            sales=0,
            ratio=None,
        ),
    ]
    p = Product(
        date(2026, 11, 9),
        "",
        machines=["20111右"],
        entry_id="fixture-entry",
        included=True,
    )
    body = body_of([p], "互換性試験用・空欄指示書", "2026-11-09")
    body["entries"][0]["booth_assignments"][0].update(
        assignment_id="fixture-assignment",
        machine_key="fixture-machine",
        management_number="L_66",
    )
    for k in ("created_at", "updated_at"):
        body["entries"][0][k] = "2026-11-09T00:00:00Z"
    d = new_document(body, "fixture-windows", "fixture-document")
    d.update(revision_id="fixture-revision", updated_at="2026-11-09T00:00:00Z")
    data = dict(
        machines=dict(
            items=[
                dict(
                    machine_id=m.uid,
                    number=m.number,
                    management_number=m.source_key,
                    machine_type=m.kind,
                    product_name=m.product,
                    installed_on=None,
                    operating_method="",
                    windows_fields={},
                )
                for m in machines
            ],
            references=[dict(source_key="L_66", number="20111右")],
        ),
        layout=dict(
            coordinate_unit="windows-scene",
            items=[
                dict(
                    machine_id=m.uid,
                    x=m.x,
                    y=m.y,
                    width=m.width,
                    height=m.height,
                    font_size=m.font_size,
                )
                for m in machines
            ],
        ),
        sales=dict(frames=[reference_frame(machines, {}, settings, date(2026, 11, 9))]),
        history=dict(frames=[]),
        settings=dict(
            analysis=settings, methods=["橋渡し", "リング", "フック"], theme="system"
        ),
        instructions=dict(documents=[d]),
    )
    (root / "windows-fixture.zip").write_bytes(
        create_package(
            data,
            "full",
            "windows",
            "fixture-windows",
            "fixture-dataset",
            "fixture-package",
        )
    )
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    from prize_swap.mobile_qr import qr_png

    qr_png(root / "machine-qr.png", machines[0])


if __name__ == "__main__":
    main()
