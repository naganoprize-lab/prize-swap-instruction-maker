"""Authorized devices only; fixed transfer paths, argv execution and explicit ACK."""

from pathlib import Path
import json, re, subprocess, tempfile
from .core import AppError
from .mobile_package import read_package, valid_id

PACKAGE = "jp.naganoprize.prizeswap"
ROOT = "/sdcard/Android/data/" + PACKAGE + "/files/transfer"


class Adb:
    def __init__(self, path):
        self.path = str(Path(path).expanduser())
        if not Path(self.path).is_file():
            raise AppError("公式platform-toolsのadb実行ファイルを選択してください。")

    def run(self, *args, timeout=30):
        result = subprocess.run(
            [self.path, *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if result.returncode:
            raise AppError((result.stderr or result.stdout or "ADBエラー")[-2000:])
        return result.stdout

    def devices(self):
        rows = []
        for line in self.run("devices", "-l").splitlines()[1:]:
            parts = line.split()
            if len(parts) >= 2 and re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", parts[0]):
                rows.append(
                    dict(serial=parts[0], status=parts[1], name=" ".join(parts[2:]))
                )
        return rows

    def authorized(self, serial):
        if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", serial or ""):
            raise AppError("転送先を明示的に選択してください。")
        if not any(
            d["serial"] == serial and d["status"] == "device" for d in self.devices()
        ):
            raise AppError(
                "端末が未接続・未認証です。Android側でUSBデバッグの認証を確認してください。"
            )

    def send(self, serial, raw):
        package = read_package(raw)
        pid = package.manifest["package_id"]
        self.authorized(serial)
        with tempfile.TemporaryDirectory(prefix="prizeswap-adb-") as tmp:
            file = Path(tmp) / (pid + ".zip")
            file.write_bytes(raw)
            self.run(
                "-s",
                serial,
                "shell",
                "mkdir",
                "-p",
                ROOT + "/inbox",
                ROOT + "/acks",
                ROOT + "/receipts",
                ROOT + "/outbox",
            )
            self.run(
                "-s",
                serial,
                "push",
                str(file),
                ROOT + "/inbox/" + pid + ".zip.part",
                timeout=120,
            )
            self.run(
                "-s",
                serial,
                "shell",
                "mv",
                ROOT + "/inbox/" + pid + ".zip.part",
                ROOT + "/inbox/" + pid + ".zip",
            )
            self.run(
                "-s",
                serial,
                "shell",
                "am",
                "start",
                "-n",
                PACKAGE + "/.MainActivity",
                "--es",
                "transfer_request",
                pid,
            )
        return pid  # copied_pending, never success before Android ACK

    def acknowledgement(self, serial, pid):
        if not valid_id(pid):
            raise AppError("転送ID不正")
        try:
            out = self.run(
                "-s", serial, "shell", "cat", ROOT + "/acks/" + pid + ".json", timeout=5
            )
        except AppError:
            return dict(status="copied_pending")
        value = json.loads(out)
        if value.get("package_id") != pid or value.get("status") not in (
            "imported",
            "already_imported",
            "rejected",
            "error",
            "pending",
        ):
            raise AppError("Androidの応答が不正です。")
        return value

    def receive(self, serial):
        self.authorized(serial)
        latest = json.loads(
            self.run("-s", serial, "shell", "cat", ROOT + "/outbox/latest.json")
        )
        pid = latest.get("package_id")
        if not valid_id(pid):
            raise AppError("受信パッケージIDが不正です。")
        with tempfile.TemporaryDirectory(prefix="prizeswap-adb-") as tmp:
            file = Path(tmp) / "received.zip"
            self.run(
                "-s",
                serial,
                "pull",
                ROOT + "/outbox/" + pid + ".zip",
                str(file),
                timeout=120,
            )
            package = read_package(file)
            if package.manifest["package_id"] != pid:
                raise AppError("受信ファイルIDが一致しません。")
            return package

    def receipt(self, serial, pid, status):
        if not valid_id(pid) or status not in ("imported", "rejected", "error"):
            raise AppError("応答形式が不正です。")
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "receipt.json"
            f.write_text(
                json.dumps(dict(package_id=pid, status=status)), encoding="utf-8"
            )
            self.run("-s", serial, "push", str(f), ROOT + "/receipts/" + pid + ".json")
