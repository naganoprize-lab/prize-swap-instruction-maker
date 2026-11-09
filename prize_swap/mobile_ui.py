"""Isolated Android link / QR pages; no changes to instruction or print widgets."""

from copy import deepcopy
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace
from PySide6.QtCore import Qt, Signal, QObject, QRunnable, QThreadPool, QTimer, QDate
from PySide6.QtGui import QPainter, QImage, QPixmap
from PySide6.QtWidgets import (
    QWidget,
    QScrollArea,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QToolButton,
    QLineEdit,
    QComboBox,
    QFormLayout,
    QFileDialog,
    QMessageBox,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QCheckBox,
    QDateEdit,
)
from PySide6.QtPrintSupport import QPrinter, QPrintDialog, QPrintPreviewDialog
from .core import AppError
from .mobile_bridge import MobileService, export_data
from .mobile_package import read_package, save_package, create_package, now
from .mobile_qr import qr_png, render_labels, label
from .mobile_adb import Adb


class Signals(QObject):
    done = Signal(object)
    failed = Signal(str)


class Task(QRunnable):
    def __init__(self, work):
        super().__init__()
        self.work = work
        self.signals = Signals()

    def run(self):
        try:
            self.signals.done.emit(self.work())
        except Exception as e:
            self.signals.failed.emit(str(e))


class LinkPage(QScrollArea):
    def __init__(self, w):
        super().__init__()
        self.w = w
        self.service = MobileService(w)
        self.workers = set()
        self.pending = None
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.poll_ack)
        self.setWidgetResizable(True)
        self.setAcceptDrops(True)
        root = QWidget()
        self.setWidget(root)
        outer = QVBoxLayout(root)
        self.status = QLabel(
            "ZIPでオフライン交換できます。USBは認証済みのADB端末に対応します。"
        )
        self.status.setWordWrap(True)
        outer.addWidget(self.status)
        tabs = QTabWidget()
        outer.addWidget(tabs)
        link = QWidget()
        layout = QVBoxLayout(link)
        tabs.addTab(link, "Android連携")
        form = QFormLayout()
        layout.addLayout(form)
        self.kind = QComboBox()
        for t, k in [
            ("全体パッケージ", "full"),
            ("指示書", "instructions"),
            ("参照データ", "reference"),
        ]:
            self.kind.addItem(t, k)
        form.addRow("転送対象", self.kind)
        self.history = QCheckBox("日別・月別の参照フレームも含める（最大90日）")
        form.addRow(self.history)
        self.history_granularity = QComboBox()
        for label, key in [
            ("日別＋月別", "daily"),
            ("月別のみ", "monthly"),
            ("選択期間のみ", "range"),
        ]:
            self.history_granularity.addItem(label, key)
        form.addRow("履歴の粒度（大容量時は月別）", self.history_granularity)
        today = QDate.currentDate()
        self.start = QDateEdit(today.addDays(-6))
        self.end = QDateEdit(today)
        for edit in (self.start, self.end):
            edit.setCalendarPopup(True)
            edit.setDisplayFormat("yyyy/MM/dd")
        dates = QHBoxLayout()
        dates.addWidget(self.start)
        dates.addWidget(self.end)
        form.addRow("履歴の対象期間", dates)
        row = QHBoxLayout()
        layout.addLayout(row)
        self.button(row, "手動ZIP出力", self.export_zip)
        self.button(row, "手動ZIP取込", self.import_zip)
        notice = QLabel(
            "ZIPには売上等の業務データが含まれます。共有先を確認してください。参照データの管理元はWindowsです。"
        )
        notice.setWordWrap(True)
        layout.addWidget(notice)
        self.adb = QLineEdit(self.service.store.state["adb_path"])
        form.addRow("adb.exe", self.adb)
        self.button(row, "ADBを選択", self.select_adb)
        self.devices = QComboBox()
        form.addRow("接続端末（明示的に選択）", self.devices)
        usb = QHBoxLayout()
        layout.addLayout(usb)
        self.button(usb, "接続確認", self.detect)
        self.button(usb, "Androidへ送信", self.send)
        self.button(usb, "Androidから受信", self.receive)
        self.button(usb, "確認待ちを終了", self.cancel_wait)
        helptext = QLabel(
            "初回：公式platform-toolsを用意 → 端末の開発者オプション/USBデバッグを有効 → USB接続 → 端末上でPCを許可 → 接続確認して端末を選択。利用できない場合はZIPを手動で移動・取込してください。Android側で取込を確認するまで同期完了にはなりません。"
        )
        helptext.setWordWrap(True)
        layout.addWidget(helptext)
        self.log = QLabel()
        self.log.setWordWrap(True)
        layout.addWidget(self.log)
        layout.addStretch()
        qr = QWidget()
        ql = QVBoxLayout(qr)
        tabs.addTab(qr, "QRコード管理")
        qform = QFormLayout()
        ql.addLayout(qform)
        self.scope = QComboBox()
        self.scope.addItems(["全台", "レイアウト選択中", "機械番号・管理ナンバー指定"])
        qform.addRow("対象", self.scope)
        self.query = QLineEdit()
        qform.addRow("指定番号", self.query)
        self.width = QDoubleSpinBox()
        self.width.setRange(30, 180)
        self.width.setValue(65)
        self.height = QDoubleSpinBox()
        self.height.setRange(25, 100)
        self.height.setValue(35)
        qform.addRow("幅 mm", self.width)
        qform.addRow("高さ mm", self.height)
        actions = QHBoxLayout()
        ql.addLayout(actions)
        self.button(actions, "ラベルプレビュー", self.preview_qr)
        self.button(actions, "PDF保存", self.pdf_qr)
        self.button(actions, "A4印刷", self.print_qr)
        self.button(actions, "PNG保存", self.png_qr)
        self.example = QLabel()
        ql.addWidget(self.example)
        ql.addStretch()
        self.refresh_log()

    def button(self, row, text, callback):
        b = QPushButton(text)
        b.clicked.connect(lambda: self.safe(callback))
        row.addWidget(b)
        return b

    def safe(self, callback):
        try:
            callback()
        except Exception as e:
            self.error(str(e))

    def error(self, text):
        self.status.setText(text)
        QMessageBox.warning(self, "Android連携", text)

    def task(self, work, done):
        t = Task(work)
        self.workers.add(t)
        self.status.setText("処理中…")

        def finish(value):
            self.workers.discard(t)
            self.safe(lambda: done(value))

        def failed(message):
            self.workers.discard(t)
            self.error(message)

        t.signals.done.connect(finish)
        t.signals.failed.connect(failed)
        QThreadPool.globalInstance().start(t)

    def export_work(self):
        floor = self.w.floor
        docs = self.service.store.observe(deepcopy(self.w.products))
        kind = self.kind.currentData()
        state = deepcopy(self.service.store.state)
        day = floor.reference_day()
        snapshot = SimpleNamespace(
            physical_machines=deepcopy(floor.physical_machines),
            machines=deepcopy(floor.machines),
            analysis_settings=deepcopy(floor.analysis_settings),
            analysis_state=deepcopy(floor.analysis_state),
            number_references=deepcopy(floor.number_references),
            reference_day=lambda: day,
            temporal=floor.temporal,
            assignments=lambda assignments=deepcopy(floor.assignments()): assignments,
            performance_history=deepcopy(floor.performance_history),
        )
        ranges = []
        if self.history.isChecked():
            a = self.start.date().toPython()
            b = self.end.date().toPython()
            if a > b or (b - a).days > 89:
                raise AppError("履歴は開始日≦終了日、最大90日で指定してください。")
            current = a
            months = {}
            granularity = self.history_granularity.currentData()
            while current <= b:
                iso = current.isoformat()
                if granularity == "daily":
                    ranges.append((iso, iso, iso))
                months.setdefault(iso[:7], []).append(iso)
                current += timedelta(days=1)
            if granularity in ("daily", "monthly"):
                ranges.extend(
                    (v[0], v[-1], k + " 月別") for k, v in months.items() if len(v) > 1
                )
            if not ranges or (
                a < b
                and (a.isoformat(), b.isoformat()) not in [(x, y) for x, y, _ in ranges]
            ):
                ranges.append((a.isoformat(), b.isoformat(), "選択期間"))
        return lambda: create_package(
            export_data(snapshot, [] if kind == "reference" else docs, ranges),
            kind,
            "windows",
            state["device_id"],
            state["dataset_id"],
        )

    def export_zip(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "ZIP保存", "PrizeSwapPackage.zip", "ZIP (*.zip)"
        )
        if path:
            self.task(
                self.export_work(),
                lambda raw: (
                    save_package(path, raw),
                    self.status.setText("ZIP保存完了：" + path),
                ),
            )

    def import_zip(self):
        path, _ = QFileDialog.getOpenFileName(self, "ZIP取込", "", "ZIP (*.zip)")
        if path:
            self.load_path(path)

    def load_path(self, path):
        self.task(lambda: read_package(path), self.preview_import)

    def preview_import(self, package, receipt=None):
        try:
            plans = self.service.preview(package)
            if not plans:
                self.status.setText(
                    "新規の指示書はありません（同一パッケージ、参照のみ、または空の指示書）。"
                )
                if receipt:
                    receipt("imported")
                return
            dialog = QDialog(self)
            dialog.setWindowTitle("取込内容の確認")
            dialog.resize(760, 420)
            v = QVBoxLayout(dialog)
            v.addWidget(
                QLabel(
                    "Windowsのマシン・売上・配置は変更しません。指示書のみ取り込みます。"
                )
            )
            table = QTableWidget(len(plans), 3)
            table.setHorizontalHeaderLabels(["指示書", "状態", "処理"])
            v.addWidget(table)
            combos = {}
            names = {
                "new": "新規",
                "incoming": "受信側で更新",
                "local": "Windows側に新しい編集",
                "same": "同一",
                "conflict": "双方で編集（競合）",
            }
            for i, p in enumerate(plans):
                table.setItem(i, 0, QTableWidgetItem(p["title"]))
                table.setItem(i, 1, QTableWidgetItem(names[p["relation"]]))
                c = QComboBox()
                c.addItem("Windowsを保持", "local")
                c.addItem("受信内容を採用", "incoming")
                c.addItem("別の指示書として保存", "copy")
                if p["can_merge"]:
                    c.addItem("項目ごとの差分を確認して統合", "merge")
                c.setCurrentIndex(1 if p["relation"] in ("new", "incoming") else 0)
                table.setCellWidget(i, 2, c)
                combos[p["document_id"]] = c
            table.horizontalHeader().setStretchLastSection(True)
            buttons = QDialogButtonBox(
                QDialogButtonBox.StandardButton.Ok
                | QDialogButtonBox.StandardButton.Cancel
            )
            buttons.accepted.connect(dialog.accept)
            buttons.rejected.connect(dialog.reject)
            v.addWidget(buttons)
            if dialog.exec() != QDialog.DialogCode.Accepted:
                if receipt:
                    receipt("rejected")
                return
            decisions = {k: dict(action=c.currentData()) for k, c in combos.items()}
            for p in plans:
                if decisions[p["document_id"]]["action"] == "merge" and p["conflicts"]:
                    fields = {}
                    for conflict in p["conflicts"]:
                        answer = QMessageBox.question(
                            self,
                            "項目の競合",
                            conflict["path"]
                            + "\nWindows："
                            + str(conflict["local"])[:300]
                            + "\n受信："
                            + str(conflict["incoming"])[:300]
                            + "\n受信側を使用しますか？",
                            QMessageBox.StandardButton.Yes
                            | QMessageBox.StandardButton.No
                            | QMessageBox.StandardButton.Cancel,
                        )
                        if answer == QMessageBox.StandardButton.Cancel:
                            return
                        fields[conflict["path"]] = (
                            "incoming"
                            if answer == QMessageBox.StandardButton.Yes
                            else "local"
                        )
                    decisions[p["document_id"]]["fields"] = fields
            count = self.service.apply(package, decisions)
            self.status.setText(
                f"{count}件の指示書を反映しました。既存の入替指示書で編集・印刷できます。"
            )
            self.refresh_log()
            if receipt:
                receipt("imported")
        except Exception as exc:
            if receipt:
                receipt("error")
            self.error(str(exc))

    def select_adb(self):
        p, _ = QFileDialog.getOpenFileName(self, "公式ADB実行ファイルを選択")
        if p:
            self.adb.setText(p)

    def client(self):
        return Adb(self.adb.text())

    def detect(self):
        client = self.client()
        self.service.store.state["adb_path"] = self.adb.text()
        self.service.store.save()

        def done(devices):
            self.devices.clear()
            self.devices.addItem("端末を選択してください", "")
            for d in devices:
                self.devices.addItem(
                    d["serial"] + " / " + d["status"] + " / " + d["name"],
                    d["serial"] if d["status"] == "device" else "",
                )
            registered = self.service.store.state["selected_device"]
            self.devices.setCurrentIndex(max(0, self.devices.findData(registered)))
            self.status.setText(
                f"接続 {len(devices)}台。未認証は端末側の確認が必要です。"
            )

        self.task(client.devices, done)

    def target(self):
        serial = self.devices.currentData()
        if not serial:
            raise AppError("認証済みの転送先を選択してください。")
        self.service.store.state["selected_device"] = serial
        self.service.store.save()
        return serial

    def send(self):
        if self.pending:
            raise AppError("Android側で前回の取込を確認してください。")
        client = self.client()
        serial = self.target()
        export = self.export_work()

        def sent(pid):
            self.pending = (client, serial, pid, 0)
            self.status.setText("転送済み・Androidの取込確認待ちです。")
            self.timer.start(2500)

        self.task(lambda: client.send(serial, export()), sent)

    def cancel_wait(self):
        self.timer.stop()
        self.pending = None
        self.status.setText(
            "待機を終了しました。取込結果は未確認で、同期完了ではありません。Androidの履歴を確認するか再送してください。"
        )

    def poll_ack(self):
        if not self.pending:
            return
        self.timer.stop()
        client, serial, pid, count = self.pending

        def done(ack):
            if ack["status"] in ("pending", "copied_pending"):
                if count >= 119:
                    self.pending = None
                    self.status.setText(
                        "確認待ちのままタイムアウトしました。同期完了ではありません。Androidの連携履歴を確認し、必要なら再送してください。"
                    )
                    return
                self.pending = (client, serial, pid, count + 1)
                self.timer.start(2500)
                return
            self.pending = None
            success = ack["status"] in ("imported", "already_imported")
            self.status.setText(
                "Android取込完了" if success else "Android取込結果：" + ack["status"]
            )
            self.service.store.state["transfers"].append(
                dict(
                    timestamp=now(),
                    direction="send",
                    package_id=pid,
                    status=ack["status"],
                )
            )
            self.service.store.save()
            self.refresh_log()

        self.task(lambda: client.acknowledgement(serial, pid), done)

    def receive(self):
        client = self.client()
        serial = self.target()
        self.task(
            lambda: client.receive(serial),
            lambda p: self.preview_import(
                p,
                lambda status: self.task(
                    lambda: client.receipt(serial, p.manifest["package_id"], status),
                    lambda _: self.status.setText(
                        "受信結果をAndroidへ通知しました：" + status
                    ),
                ),
            ),
        )

    def refresh_log(self):
        self.log.setText(
            "転送履歴\n"
            + "\n".join(
                x.get("timestamp", "")
                + " "
                + x.get("direction", "")
                + " "
                + x.get("status", "")
                for x in self.service.store.state["transfers"][-12:]
            )
        )

    def qr_targets(self):
        from .machine_matching import normalize_number, normalize_reference, machine_key

        machines = self.w.floor.physical_machines
        if self.scope.currentIndex() == 1:
            machines = [
                m
                for m in machines
                if m.uid in self.w.floor.selection_state.selected_machine_keys
            ]
        elif self.scope.currentIndex() == 2:
            n = normalize_number(self.query.text(), allow_bare=True)
            k = normalize_reference(self.query.text())
            machines = [
                m
                for m in machines
                if n and machine_key(m) == n or k and m.source_key == k
            ]
            if len(machines) > 1:
                raise AppError(
                    "番号に複数候補があります。レイアウトで対象を選択してください。"
                )
        if not machines:
            raise AppError("QRの対象マシンがありません。")
        return machines

    def printer(self):
        return QPrinter(QPrinter.PrinterMode.HighResolution)

    def preview_qr(self):
        machines = self.qr_targets()
        printer = self.printer()
        d = QPrintPreviewDialog(printer, self)
        d.paintRequested.connect(
            lambda p: render_labels(
                p, machines, self.width.value(), self.height.value()
            )
        )
        d.exec()

    def pdf_qr(self):
        machines = self.qr_targets()
        path, _ = QFileDialog.getSaveFileName(
            self, "QR PDF保存", "machine-labels.pdf", "PDF (*.pdf)"
        )
        if path:
            p = self.printer()
            p.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
            p.setOutputFileName(path)
            render_labels(p, machines, self.width.value(), self.height.value())
            self.status.setText("QR PDFを保存しました。")

    def print_qr(self):
        machines = self.qr_targets()
        p = self.printer()
        if QPrintDialog(p, self).exec() == QDialog.DialogCode.Accepted:
            render_labels(p, machines, self.width.value(), self.height.value())

    def png_qr(self):
        machines = deepcopy(self.qr_targets())
        folder = QFileDialog.getExistingDirectory(self, "QR PNGの出力先")
        if not folder:
            return
        existing = sum((Path(folder) / (m.uid + ".png")).exists() for m in machines)
        if (
            existing
            and QMessageBox.question(
                self,
                "QR PNGの上書き",
                f"同じマシンのPNGが{existing}件あります。上書きしますか？",
            )
            != QMessageBox.StandardButton.Yes
        ):
            return
        width, height = self.width.value(), self.height.value()

        def work():
            for m in machines:
                qr_png(Path(folder) / (m.uid + ".png"), m, width, height)
            return len(machines)

        self.task(
            work, lambda count: self.status.setText(f"{count}枚のPNGを保存しました。")
        )

    def dragEnterEvent(self, e):
        if (
            e.mimeData().hasUrls()
            and len(e.mimeData().urls()) == 1
            and e.mimeData().urls()[0].toLocalFile().lower().endswith(".zip")
        ):
            e.acceptProposedAction()

    def dropEvent(self, e):
        self.load_path(e.mimeData().urls()[0].toLocalFile())
        e.acceptProposedAction()


def install(w):
    try:
        page = LinkPage(w)
    except (AppError, OSError, ValueError) as exc:
        page = QLabel(
            "Android連携データを開けません。既存のWindowsデータは変更しません。\n"
            + str(exc)
        )
        page.setWordWrap(True)
    w.mobile_page = page
    w.tabs.addTab(page, "Android連携")
    w.pages["mobile"] = page
    b = QToolButton()
    b.setText("Android連携")
    b.setToolTip("Android連携・QRコード")
    b.setCheckable(True)
    b.setProperty("nav", True)
    b.setProperty("iconName", "file")
    b.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
    b.clicked.connect(lambda: w.navigation.activate("mobile"))
    w.navigation.buttons["mobile"] = b
    w.navigation.layout().insertWidget(len(w.navigation.buttons) - 1, b)
    w.navigation.refresh_icons()
