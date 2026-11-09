"""Exercise package schema and QR dependencies from a frozen Windows executable."""

from .mobile_package import read_package, save_package
from .mobile_qr import qr_png


def check(window, output):
    raw = window.mobile_page.service.export()
    package = read_package(raw)
    save_package(output / "Android連携.zip", raw)
    machines = window.floor.physical_machines
    if machines:
        qr_png(output / "マシンQR.png", machines[0])
    return dict(
        version=package.manifest["version"],
        machines=len(package.data["machines"]["items"]),
        documents=len(package.data["instructions"]["documents"]),
        qr=bool(machines),
    )
