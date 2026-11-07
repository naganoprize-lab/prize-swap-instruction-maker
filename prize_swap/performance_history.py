"""Deduplicated report history. Deletion is reversible and never sums periods."""

from copy import deepcopy
import hashlib
import json
from .core import AppError, parse_date
from .sales_analysis import validate_snapshot


def identity(snapshot):
    return (
        snapshot.get("fingerprint")
        or hashlib.sha256(
            json.dumps(
                dict(file_name=snapshot["file_name"], records=snapshot["records"]),
                sort_keys=True,
                ensure_ascii=False,
            ).encode()
        ).hexdigest()
    )


def validate_history(entries=None):
    if entries is None:
        return []
    if not isinstance(entries, list) or len(entries) > 500:
        raise AppError("売上履歴は500ファイルまでです。")
    result = []
    ids = set()
    count = 0
    for entry in entries:
        if (
            not isinstance(entry, dict)
            or not isinstance(entry.get("id"), str)
            or type(entry.get("deleted", False)) is not bool
        ):
            raise AppError("売上履歴の形式が不正です。")
        snapshot = validate_snapshot(entry.get("snapshot"))
        key = identity(snapshot)
        if entry["id"] != key or key in ids:
            raise AppError("売上履歴の識別情報が重複または不正です。")
        count += len(snapshot["records"])
        if count > 1000000:
            raise AppError("売上履歴の総実績数が100万件を超えています。")
        ids.add(key)
        result.append(dict(id=key, deleted=entry.get("deleted", False), snapshot=snapshot))
    return result


def archive(entries, snapshot):
    result = deepcopy(entries)
    if not snapshot.get("file_name"):
        return result
    key = identity(snapshot)
    if not any(entry["id"] == key for entry in result):
        result.append(dict(id=key, deleted=False, snapshot=deepcopy(snapshot)))
    return validate_history(result)


def set_period(snapshot, start, end):
    start, end = parse_date(start), parse_date(end)
    if start is None or end is None or start > end:
        raise AppError("集計期間は有効な開始日≦終了日で指定してください。")
    result = deepcopy(snapshot)
    result.update(
        period_start=start.isoformat(), period_end=end.isoformat(), period_source="manual"
    )
    return result


def update_period(entries, snapshot):
    key = identity(snapshot)
    result = deepcopy(entries)
    for entry in result:
        if entry["id"] == key:
            entry["snapshot"] = deepcopy(snapshot)
    return validate_history(result)
