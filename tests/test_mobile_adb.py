from pathlib import Path
import json
import pytest
from prize_swap.mobile_adb import Adb, ROOT
from prize_swap.core import AppError
from prize_swap.mobile_package import read_package


class FakeAdb(Adb):
    def __init__(self, devices):
        self.output = devices
        self.calls = []

    def run(self, *args, timeout=30):
        self.calls.append(args)
        if args == ("devices", "-l"):
            return self.output
        if "cat" in args:
            raise AppError("not ready")
        return ""


def test_never_uses_unauthorized_or_implicit_device():
    c = FakeAdb(
        "List of devices attached\na unauthorized\nb device model:tablet\nc device model:phone\n"
    )
    for serial in ("a", "", "a; reboot", "missing"):
        with pytest.raises(AppError):
            c.authorized(serial)
    c.authorized("b")
    assert c.devices()[0]["status"] == "unauthorized"


def test_send_requires_android_ack_and_uses_atomic_fixed_location():
    c = FakeAdb("List of devices attached\nserial device model:tablet\n")
    raw = Path("shared/examples/windows-fixture.zip").read_bytes()
    pid = c.send("serial", raw)
    assert pid == "fixture-package"
    pushes = [a for a in c.calls if "push" in a]
    assert pushes[0][-1] == ROOT + "/inbox/fixture-package.zip.part"
    assert any("mv" in a and a[-1].endswith(".zip") for a in c.calls)
    assert c.acknowledgement("serial", pid)["status"] == "copied_pending"
    assert all("root" not in a and "run-as" not in a for a in c.calls)
