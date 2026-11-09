"""Transactional, versioned reference-file cache. Excel sources are read-only."""

from contextlib import contextmanager
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime, timezone, date, timedelta
from pathlib import Path
import hashlib, json, sqlite3
from .core import AppError
from .reference_dates import date_metadata, text, business_date

SCHEMA = 2


def stamp():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def digest(value):
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, ensure_ascii=False, default=str, separators=(",", ":")
        ).encode()
    ).hexdigest()


def file_hash(path):
    h = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def read_document(path, fingerprint=None):
    from openpyxl import load_workbook
    from .sales_analysis import load_analysis, empty_snapshot
    from .floor_data import import_layout
    from .machine_matching import read_reference_rows

    path = Path(path)
    book = load_workbook(path, data_only=True, read_only=True)
    try:
        meta = date_metadata(book, path)
        refs = read_reference_rows(book)
        layout_name = next((s for s in book.sheetnames if text(s) == "投入中レイアウト"), None)
        created = book.properties.created.isoformat() if book.properties.created else None
    finally:
        book.close()
    warnings = meta["warnings"][:]
    layouts = []
    profiles = None

    def auto_profile(kind):
        nonlocal profiles
        from .excel_profiles import detect_workbook

        if profiles is None:
            profiles = detect_workbook(path)["candidates"]
        choices = [p["profile"] for p in profiles if p["profile"]["file_type"] == kind]
        if len(choices) > 1:
            warnings.append(kind + "：候補シートが複数あり、自動採用しません。")
            return None
        return choices[0] if choices else None

    def staged(profile):
        from types import SimpleNamespace
        from .excel_staging import prepare_import

        prepared = prepare_import(
            path, profile, SimpleNamespace(number_references=refs, machines=[])
        )
        if prepared.errors:
            raise AppError(" / ".join(prepared.errors))
        warnings.extend(prepared.warnings)
        return prepared.data

    if not refs:
        profile = auto_profile("references")
        if profile:
            refs = staged(profile)
    try:
        snapshot = load_analysis(path)
    except AppError as exc:
        profile = auto_profile("sales")
        if profile:
            snapshot = staged(profile)
        else:
            snapshot = empty_snapshot()
            warnings.append("売上分析表：" + str(exc))
    if layout_name:
        machines, notes, refs = import_layout(path, True, sheet_name=layout_name)
        layouts = [asdict(m) for m in machines]
        warnings.extend(notes)
    else:
        profile = auto_profile("layout")
        if profile:
            machines, notes, stage_refs = staged(profile)
            layouts = [asdict(m) for m in machines]
            warnings.extend(notes)
            refs = refs or stage_refs
    if not layouts and not snapshot["records"]:
        raise AppError("対応する売上またはレイアウトデータがありません。")
    clean_layout = [
        {
            k: v
            for k, v in m.items()
            if k not in ("uid", "layout_loaded_at", "manual_updates", "analysis")
        }
        for m in layouts
    ]
    sales_hash = digest({n: r["values"].get("sales") for n, r in snapshot["records"].items()})
    payload_hash = digest(
        dict(
            records={n: r["values"] for n, r in snapshot["records"].items()},
            layout=clean_layout,
            refs=refs,
        )
    )
    return dict(
        id=fingerprint or file_hash(path),
        path=str(path.resolve()),
        name=path.name,
        metadata=meta,
        snapshot=snapshot,
        layouts=layouts,
        references=refs,
        warnings=warnings,
        content_hash=payload_hash,
        sales_hash=sales_hash,
        imported_at=stamp(),
        created_at=created,
        duplicate_approved=False,
    )


def document_summary(doc):
    """Metadata-only index: date switches never parse every historical payload."""
    return {
        k: deepcopy(v) for k, v in doc.items() if k not in ("snapshot", "layouts", "references")
    } | dict(record_count=len(doc["snapshot"]["records"]), layout_count=len(doc["layouts"]))


class ReferenceLibrary:
    def __init__(self, path):
        self.path = Path(path)
        with self.connect() as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version > SCHEMA:
                raise AppError("日別参照キャッシュの形式が新しいため、このアプリでは開けません。")
            db.executescript("""
            CREATE TABLE IF NOT EXISTS files(path TEXT PRIMARY KEY,size INTEGER,mtime INTEGER,hash TEXT,doc_id TEXT,error TEXT,checked_at TEXT,present INTEGER DEFAULT 1);
            CREATE TABLE IF NOT EXISTS documents(id TEXT PRIMARY KEY,path TEXT,start TEXT,end TEXT,kind TEXT,sales_hash TEXT,content_hash TEXT,payload TEXT);
            CREATE INDEX IF NOT EXISTS document_dates ON documents(start,end);
            CREATE TABLE IF NOT EXISTS preferences(key TEXT PRIMARY KEY,value TEXT);

            """)
            columns = {r["name"] for r in db.execute("PRAGMA table_info(documents)")}
            if "summary" not in columns:
                db.execute("ALTER TABLE documents ADD COLUMN summary TEXT")
                for row in db.execute("SELECT id,payload FROM documents").fetchall():
                    db.execute(
                        "UPDATE documents SET summary=? WHERE id=?",
                        (
                            json.dumps(
                                document_summary(json.loads(row["payload"])), ensure_ascii=False
                            ),
                            row["id"],
                        ),
                    )
            db.execute("PRAGMA user_version=2")

    @contextmanager
    def connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(str(self.path), timeout=30)
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA foreign_keys=ON")
            with db:
                yield db
        finally:
            db.close()

    def scan(self, folder, cancel=None, progress=None):
        folder = Path(folder)
        if not folder.is_dir():
            return dict(
                files=0,
                parsed=0,
                cached=0,
                failed=0,
                cancelled=False,
                warnings=["参照フォルダーが見つかりません。保存済みキャッシュは保持しています。"],
                finished_at=stamp(),
            )
        paths = sorted(
            p
            for p in folder.iterdir()
            if p.is_file()
            and p.suffix.lower() in (".xlsx", ".xlsm")
            and not p.name.startswith("~$")
        )
        result = dict(
            files=len(paths),
            parsed=0,
            cached=0,
            failed=0,
            cancelled=False,
            warnings=[],
            finished_at=None,
        )
        for i, path in enumerate(paths):
            if cancel and cancel.is_set():
                result["cancelled"] = True
                break
            key = str(path.resolve())
            try:
                before = path.stat()
                with self.connect() as db:
                    old = db.execute("SELECT * FROM files WHERE path=?", (key,)).fetchone()
                if (
                    old
                    and not old["error"]
                    and old["size"] == before.st_size
                    and old["mtime"] == before.st_mtime_ns
                ):
                    result["cached"] += 1
                else:
                    hashed = file_hash(path)
                    if old and old["hash"] == hashed and old["doc_id"]:
                        with self.connect() as db:
                            db.execute(
                                "UPDATE files SET size=?,mtime=?,error=NULL,checked_at=?,present=1 WHERE path=?",
                                (before.st_size, before.st_mtime_ns, stamp(), key),
                            )
                        result["cached"] += 1
                    else:
                        doc = read_document(path, hashed)
                        after = path.stat()
                        if (before.st_size, before.st_mtime_ns) != (
                            after.st_size,
                            after.st_mtime_ns,
                        ):
                            raise AppError("読込中にファイルが変更されました。再読込してください。")
                        if cancel and cancel.is_set():
                            result["cancelled"] = True
                            break
                        # Path is part of document identity: identical file copies remain visible.
                        doc["id"] = digest([key, hashed])
                        meta = doc["metadata"]
                        with self.connect() as db:
                            existing = db.execute(
                                "SELECT id FROM documents WHERE id=?", (doc["id"],)
                            ).fetchone()
                            if not existing:
                                db.execute(
                                    "INSERT INTO documents(id,path,start,end,kind,sales_hash,content_hash,payload,summary) VALUES(?,?,?,?,?,?,?,?,?)",
                                    (
                                        doc["id"],
                                        key,
                                        meta["start"],
                                        meta["end"],
                                        meta["data_type"],
                                        doc["sales_hash"],
                                        doc["content_hash"],
                                        json.dumps(doc, ensure_ascii=False),
                                        json.dumps(document_summary(doc), ensure_ascii=False),
                                    ),
                                )
                            db.execute(
                                "INSERT INTO files(path,size,mtime,hash,doc_id,error,checked_at,present) VALUES(?,?,?,?,?,NULL,?,1) ON CONFLICT(path) DO UPDATE SET size=excluded.size,mtime=excluded.mtime,hash=excluded.hash,doc_id=excluded.doc_id,error=NULL,checked_at=excluded.checked_at,present=1",
                                (key, after.st_size, after.st_mtime_ns, hashed, doc["id"], stamp()),
                            )
                        result["parsed"] += 1
            except Exception as exc:
                result["failed"] += 1
                result["warnings"].append(path.name + "：" + str(exc))
                # A failed revision does not replace the last successful document.
                with self.connect() as db:
                    db.execute(
                        "INSERT INTO files(path,error,checked_at) VALUES(?,?,?) ON CONFLICT(path) DO UPDATE SET error=excluded.error,checked_at=excluded.checked_at",
                        (key, str(exc), stamp()),
                    )
            if progress:
                progress(i + 1, len(paths), path.name)
        if not result["cancelled"]:
            present = {str(p.resolve()) for p in paths}
            with self.connect() as db:
                for r in db.execute("SELECT path FROM files").fetchall():
                    if Path(r["path"]).parent == folder.resolve() and r["path"] not in present:
                        db.execute("UPDATE files SET present=0 WHERE path=?", (r["path"],))
        result["finished_at"] = stamp()
        self.set_preference("last_scan", result)
        return result

    def preference(self, key, default=None):
        with self.connect() as db:
            r = db.execute("SELECT value FROM preferences WHERE key=?", (key,)).fetchone()
        return json.loads(r["value"]) if r else default

    def set_preference(self, key, value):
        with self.connect() as db:
            db.execute(
                "INSERT INTO preferences VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, json.dumps(value, ensure_ascii=False)),
            )

    def documents(self, folder=None, full=True):
        with self.connect() as db:
            column = "payload" if full else "summary"
            rows = db.execute(
                f"SELECT d.{column} AS data,f.path,f.error,f.present,f.checked_at FROM files f JOIN documents d ON d.id=f.doc_id ORDER BY f.path"
            ).fetchall()
        docs = []
        for r in rows:
            if folder and Path(r["path"]).parent != Path(folder).resolve():
                continue
            d = json.loads(r["data"])
            d.update(read_error=r["error"], present=bool(r["present"]))
            if r["error"]:
                d["warnings"].append(
                    "更新版の読込に失敗したため、前回キャッシュを表示：" + r["error"]
                )
            if not r["present"]:
                d["warnings"].append("元のファイルは現在見つかりません。前回キャッシュです。")
            docs.append(d)
        self.annotate(docs)
        return docs

    def document(self, doc_id):
        with self.connect() as db:
            row = db.execute("SELECT payload FROM documents WHERE id=?", (doc_id,)).fetchone()
        if not row:
            raise AppError("日別参照データが見つかりません。再読込してください。")
        return json.loads(row["payload"])

    def annotate(self, docs):
        for d in docs:
            d["warnings"] = list(dict.fromkeys(d["warnings"]))
            d["conflicts"] = []
            d["duplicate_content"] = []
            m = d["metadata"]
            d["eligible"] = bool(m["start"] and m["end"])
        for i, a in enumerate(docs):
            for b in docs[i + 1 :]:
                ma, mb = a["metadata"], b["metadata"]
                if ma["start"] and (ma["start"], ma["end"]) == (mb["start"], mb["end"]):
                    a["conflicts"].append(b["id"])
                    b["conflicts"].append(a["id"])
                elif (
                    a.get("record_count", len(a.get("snapshot", {}).get("records", {})))
                    and b.get("record_count", len(b.get("snapshot", {}).get("records", {})))
                    and a["sales_hash"] == b["sales_hash"]
                ):
                    a["duplicate_content"].append(b["id"])
                    b["duplicate_content"].append(a["id"])
        for d in docs:
            if d["duplicate_content"]:
                d["warnings"].append(
                    "異なる日付候補の全台売上が同一です。複製の可能性を確認してください。"
                )
            if d["conflicts"]:
                d["warnings"].append("同一対象日のファイルが重複しています。合算しません。")

    def confirm(self, doc_id, start, end, kind, independent=False, preferred=True):
        a, b = business_date(start), business_date(end)
        if not a or not b or a > b or kind not in ("daily", "period") or kind == "daily" and a != b:
            raise AppError("確定する日付・データ区分が不正です。")
        with self.connect() as db:
            row = db.execute("SELECT payload FROM documents WHERE id=?", (doc_id,)).fetchone()
            if not row:
                raise AppError("確認対象のファイルがありません。")
            d = json.loads(row["payload"])
            d["metadata"].update(start=a, end=b, data_type=kind, source="manual")
            d["metadata"]["warnings"] = []
            d["warnings"] = [
                w
                for w in d["warnings"]
                if not any(
                    k in w for k in ("日付未確定", "開始日が", "日付・期間", "開始日・終了日")
                )
            ]
            d["duplicate_approved"] = bool(independent)
            d["confirmed_at"] = stamp()
            db.execute(
                "UPDATE documents SET start=?,end=?,kind=?,payload=?,summary=? WHERE id=?",
                (
                    a,
                    b,
                    kind,
                    json.dumps(d, ensure_ascii=False),
                    json.dumps(document_summary(d), ensure_ascii=False),
                    doc_id,
                ),
            )
        if preferred:
            self.set_preference("period:" + a + ":" + b, doc_id)

    def usable(self, folder=None, full=True):
        docs = [d for d in self.documents(folder, full=full) if d["eligible"]]
        groups = {}
        for d in docs:
            groups.setdefault((d["metadata"]["start"], d["metadata"]["end"]), []).append(d)
        with self.connect() as db:
            preferences = {
                r["key"]: json.loads(r["value"])
                for r in db.execute("SELECT key,value FROM preferences WHERE key LIKE 'period:%'")
            }
        chosen = []
        warnings = []
        for (a, b), items in groups.items():
            preferred = preferences.get("period:" + a + ":" + b)
            pick = next((d for d in items if d["id"] == preferred), None)
            if pick is None and len({d["content_hash"] for d in items}) == 1:
                pick = items[0]
            if pick is None:
                warnings.append(
                    a + "〜" + b + "：同期間の異なるファイルがあり、採用ファイルの確認が必要です。"
                )
                continue
            if pick["duplicate_content"] and not pick.get("duplicate_approved"):
                # The earlier canonical day remains usable; later cloned days require confirmation.
                peers = [x for x in docs if x["id"] in pick["duplicate_content"]]
                if any((x["metadata"]["start"], x["path"]) < (a, pick["path"]) for x in peers):
                    warnings.append(pick["name"] + "：別日データとして未確認（複製疑い）。")
                    continue
            chosen.append(pick)
        return sorted(chosen, key=lambda d: (d["metadata"]["start"], d["path"])), warnings

    def selection(self, start, end, folder=None, usable_docs=None):
        a, b = date.fromisoformat(start), date.fromisoformat(end)
        if a > b or (b - a).days > 3660:
            raise AppError("期間は開始日≦終了日、最大10年で指定してください。")
        docs, warnings = self.usable(folder, full=False) if usable_docs is None else usable_docs
        daily = [
            d
            for d in docs
            if d["metadata"]["data_type"] == "daily" and start <= d["metadata"]["start"] <= end
        ]
        occupied = {d["metadata"]["start"] for d in daily}
        selected = daily[:]
        periods = [
            d
            for d in docs
            if d["metadata"]["data_type"] == "period"
            and d["metadata"]["start"] <= end
            and d["metadata"]["end"] >= start
        ]
        # A cumulative report is never prorated, nor added over any chosen daily record.
        for d in sorted(
            periods, key=lambda x: (x["metadata"]["start"], x["metadata"]["end"], x["path"])
        ):
            m = d["metadata"]
            ds = {
                (date.fromisoformat(m["start"]) + timedelta(days=i)).isoformat()
                for i in range(
                    (date.fromisoformat(m["end"]) - date.fromisoformat(m["start"])).days + 1
                )
            }
            if m["start"] < start or m["end"] > end:
                warnings.append(d["name"] + "：期間累計の一部は利用しません。")
                continue
            if ds & occupied:
                warnings.append(d["name"] + "：既存日・期間と重なる累計を加算しません。")
                continue
            overlapping = [
                x
                for x in periods
                if x["id"] != d["id"]
                and max(x["metadata"]["start"], m["start"]) <= min(x["metadata"]["end"], m["end"])
                and not (
                    set(
                        (date.fromisoformat(x["metadata"]["start"]) + timedelta(days=i)).isoformat()
                        for i in range(
                            (
                                date.fromisoformat(x["metadata"]["end"])
                                - date.fromisoformat(x["metadata"]["start"])
                            ).days
                            + 1
                        )
                    )
                    & occupied
                )
            ]
            if overlapping:
                warnings.append(d["name"] + "：重なる期間累計の採用は未確定です。")
                continue
            selected.append(d)
            occupied |= ds
        selected = [
            self.document(d["id"])
            | {
                k: d[k]
                for k in (
                    "warnings",
                    "read_error",
                    "present",
                    "eligible",
                    "conflicts",
                    "duplicate_content",
                )
            }
            for d in selected
        ]
        return selected, dict(
            expected=(b - a).days + 1,
            acquired=len(occupied),
            missing=(b - a).days + 1 - len(occupied),
            partial=len(occupied) != (b - a).days + 1,
            warnings=warnings,
            dates=sorted(occupied),
        )
