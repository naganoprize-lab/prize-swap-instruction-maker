"""Versioned, reproducible rule evidence; no inferred history or extra selection state."""

from copy import deepcopy
from hashlib import sha256
import json
from uuid import uuid4
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget, QVBoxLayout, QLabel, QComboBox, QPlainTextEdit
from .analytics_data import formatted


def evidence(machine, record, reference, settings):
    a = record["assessment_detail"]
    rules = {
        k: deepcopy(v)
        for k, v in settings.items()
        if k.startswith(("poor_", "consider_", "long_", "decline_", "stale_", "po_", "cost_"))
        or k == "default_play_price"
    }
    ratio = record["ratio"]
    days = record["days"]
    status = a["status"]
    if status == "要確認":
        explanation = f"アベ比{ratio:.1f}%は{rules['poor_ratio']:.1f}%未満、投入{days}日は{rules['poor_days']}日以上のため要確認。"
    elif status == "入替検討":
        explanation = f"アベ比{ratio:.1f}%は{rules['poor_ratio']:.1f}%以上・{rules['consider_ratio']:.1f}%未満、投入{days}日は{rules['consider_days']}日以上のため入替検討。"
    elif status == "好調":
        explanation = f"アベ比{ratio:.1f}%は{rules['consider_ratio']:.1f}%以上のため好調。"
    else:
        explanation = "判定保留：" + a["reasons"][0]
    source = machine.analysis or {}
    data = dict(
        machine_key=machine.uid,
        machine_number=record["number"],
        management_number=machine.source_key,
        product_name=machine.product,
        data_period_start=source.get("period_start"),
        data_period_end=source.get("period_end"),
        reference_date=reference.isoformat(),
        actual_values=dict(
            average_ratio=ratio,
            installation_days=days,
            sales_amount=record["sales"],
            po_rate=record["po_rate"],
            cost_rate=record["cost_rate"],
            sales_trend=a["trend"],
            target_po_rate=record["target_po_rate"],
        ),
        rule_id="replacement-rule-v1",
        rule=rules,
        evaluation_result=status,
        priority=a["priority"],
        explanation=explanation,
        reasons=deepcopy(a["reasons"]),
        source_record_ids=deepcopy(a["source_record_ids"]),
    )
    signature = sha256(
        json.dumps(data, ensure_ascii=False, sort_keys=True, default=str).encode()
    ).hexdigest()
    return data | dict(
        evaluation_id=uuid4().hex, evaluation_date=a["evaluated_at"], signature=signature
    )


class CandidateReasonPanel(QWidget):
    def __init__(self, owner):
        super().__init__()
        self.owner = owner
        self.uid = None
        self.setMinimumWidth(300)
        outer = QVBoxLayout(self)
        self.title = QLabel("判定理由と使用した数値")
        self.title.setObjectName("section")
        outer.addWidget(self.title)
        self.versions = QComboBox()
        outer.addWidget(self.versions)
        self.versions.currentIndexChanged.connect(self.render)
        self.values = QLabel()
        self.values.setTextFormat(Qt.TextFormat.PlainText)
        self.values.setWordWrap(True)
        outer.addWidget(self.values)
        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        outer.addWidget(self.details, 1)
        close = __import__("PySide6.QtWidgets", fromlist=["QPushButton"]).QPushButton(
            "理由パネルを閉じる"
        )
        close.clicked.connect(self.hide)
        outer.addWidget(close)

    def show_uid(self, uid):
        self.uid = uid
        w = self.owner.host_window
        w.workflow.capture_assessments(force=True)
        self.records = [
            r for r in reversed(w.workflow.data["candidate_evaluations"]) if r["machine_key"] == uid
        ]
        self.versions.blockSignals(True)
        self.versions.clear()
        for i, r in enumerate(self.records):
            self.versions.addItem(
                ("現在：" if i == 0 else "以前：")
                + r["evaluation_date"].replace("T", " ")
                + " "
                + r["evaluation_result"],
                r["evaluation_id"],
            )
        self.versions.blockSignals(False)
        self.render()
        self.show()

    def render(self, *_):
        if not getattr(self, "records", None):
            self.values.setText("判定データがありません。")
            return
        r = self.records[max(0, self.versions.currentIndex())]
        v = r["actual_values"]
        period = (
            f"{r['data_period_start']} ～ {r['data_period_end']}"
            if r["data_period_start"] and r["data_period_end"]
            else "対象期間不明"
        )
        self.values.setText(
            f"{r['machine_number']} ／ 管理 {r['management_number'] or '—'}\n{r['product_name']}\n{r['evaluation_result']}\nアベ比：{formatted('ratio',v['average_ratio'])}\n投入日数：{formatted('days',v['installation_days'])}{'日' if v['installation_days'] is not None and v['installation_days']>=0 else ''}\n売上金額：{formatted('sales',v['sales_amount'])}{'円' if v['sales_amount'] is not None else ''}\nPO率：{formatted('po_rate',v['po_rate'])} ／ 適正PO率：{formatted('po_rate',v['target_po_rate'])}\n原価率：{formatted('cost_rate',v['cost_rate'])}\n売上推移："
            + (f"前回比{v['sales_trend']:.1%}" if v["sales_trend"] is not None else "データ不足")
            + f"\n期間：{period}\n基準日：{r['reference_date']}\n判定日時：{r['evaluation_date']}"
        )
        self.details.setPlainText(
            r["explanation"]
            + "\n\n"
            + "\n".join(r["reasons"])
            + "\n\n判定時のルール\n"
            + json.dumps(r["rule"], ensure_ascii=False, indent=2)
        )
