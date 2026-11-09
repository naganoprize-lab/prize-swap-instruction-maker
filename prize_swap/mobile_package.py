"""Versioned offline package boundary. No extraction, mutation or matching in this module."""

from copy import deepcopy
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from tempfile import NamedTemporaryFile, TemporaryDirectory
from uuid import uuid4
import hashlib, json, math, os, re, stat, sys, zipfile, io, zlib
from .core import AppError

VERSION = 1
MAX_FILE = 32 * 1024 * 1024
MAX_TOTAL = 64 * 1024 * 1024
DATA_FILES = tuple(
    "data/" + n + ".json"
    for n in ("machines", "layout", "sales", "history", "settings", "instructions")
)
ALL_FILES = {"manifest.json", "metadata/checksums.json", *DATA_FILES}
ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")


def now():
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def uid():
    return uuid4().hex


def valid_id(value):
    return isinstance(value, str) and bool(ID_RE.fullmatch(value))


def encoded(value):
    return json.dumps(
        value, ensure_ascii=False, allow_nan=False, separators=(",", ":")
    ).encode("utf-8")


def sha(data):
    return hashlib.sha256(data).hexdigest()


def _pairs(pairs):
    out = {}
    for k, v in pairs:
        if k in out:
            raise AppError("JSONキーが重複しています：" + k)
        out[k] = v
    return out


def strict_json(raw):
    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_pairs,
            parse_constant=lambda x: (_ for _ in ()).throw(
                AppError("有限数以外は使用できません。")
            ),
        )

        def check(v, depth=0):
            if depth > 64:
                raise AppError("JSONが深すぎます。")
            if isinstance(v, float) and not math.isfinite(v):
                raise AppError("不正な数値です。")
            if isinstance(v, str):
                v.encode("utf-8")
            if isinstance(v, dict):
                for k, x in v.items():
                    check(k, depth + 1)
                    check(x, depth + 1)
            elif isinstance(v, list):
                for x in v:
                    check(x, depth + 1)

        check(value)
        return value
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise AppError("不正なUTF-8 JSONです：" + str(exc)) from exc


_SCHEMA = None


def validate_schema(name, value):
    global _SCHEMA
    from jsonschema import Draft202012Validator, FormatChecker

    if _SCHEMA is None:
        root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
        _SCHEMA = json.loads(
            (root / "shared/schemas/package-v1.schema.json").read_text(encoding="utf-8")
        )
    schema = {**_SCHEMA, "$ref": "#/$defs/" + name}
    errors = sorted(
        Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(value),
        key=lambda e: str(e.path),
    )
    if errors:
        raise AppError(name + "：" + errors[0].message)


def _timestamp(value):
    try:
        d = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if d.tzinfo is None:
            raise ValueError()
    except (TypeError, ValueError):
        raise AppError(
            "更新日時はタイムゾーン付きISO8601で指定してください。"
        ) from None


def products_from_entries(entries):
    from .core import load_session

    with TemporaryDirectory(prefix="prizeswap-validate-") as tmp:
        p = Path(tmp) / "session.json"
        p.write_bytes(
            encoded(
                dict(version=1, products=entries, selected_day="2000-01-01", source="")
            )
        )
        return load_session(p)[0]


def validate_document(document):
    validate_schema("document", document)
    _timestamp(document["updated_at"])
    if document["revision_id"] in document["ancestors"] or len(
        set(document["ancestors"])
    ) != len(document["ancestors"]):
        raise AppError("指示書の改訂履歴が不正です。")
    products = products_from_entries(document["body"]["entries"])
    for raw, product in zip(document["body"]["entries"], products):
        if [(a["assignment_id"], a["token"]) for a in raw["booth_assignments"]] != [
            (a["assignment_id"], a["token"]) for a in product.booth_assignments
        ]:
            raise AppError("ブース割当のID・番号が指示行と一致しません。")
    return document


def validate_documents(documents):
    docids = []
    entryids = []
    assignids = []
    for d in documents:
        validate_document(d)
        docids.append(d["document_id"])
        for e in d["body"]["entries"]:
            entryids.append(e["entry_id"])
            assignids.extend(a["assignment_id"] for a in e["booth_assignments"])
    for values in (docids, entryids, assignids):
        if len(values) != len(set(values)):
            raise AppError("指示書・指示行・ブース割当IDが重複しています。")


def validate_data(data, manifest):
    for name in ("machines", "layout", "sales", "history", "settings", "instructions"):
        validate_schema(name, data[name])
    mids = [m["machine_id"] for m in data["machines"]["items"]]
    if len(mids) != len(set(mids)):
        raise AppError("マシンIDが重複しています。")
    lids = [m["machine_id"] for m in data["layout"]["items"]]
    if len(lids) != len(set(lids)) or set(lids) - set(mids):
        raise AppError("レイアウトのマシン参照が不正です。")
    for rect in data["layout"]["items"]:
        if rect["width"] < 80 or rect["height"] < 60:
            raise AppError("カードサイズが不正です。")
    frameids = set()
    for frame in data["sales"]["frames"] + data["history"]["frames"]:
        if frame["frame_id"] in frameids:
            raise AppError("対象期間IDが重複しています。")
        frameids.add(frame["frame_id"])
        ids = [r.get("machine_id") for r in frame["records"]]
        if len(ids) != len(set(ids)) or any(i not in mids for i in ids):
            raise AppError("実績のマシン参照が不正です。")
        a, b = frame["period_start"], frame["period_end"]
        if bool(a) != bool(b) or a and (date.fromisoformat(a) > date.fromisoformat(b)):
            raise AppError("集計期間が不正です。")
        for r in frame["records"]:
            axes = r.get("radar", [])
            if axes and [a["key"] for a in axes] != [
                "ratio",
                "sales",
                "days",
                "cost_rate",
                "po_rate",
            ]:
                raise AppError("レーダーの軸構成が不正です。")
            if not isinstance(r.get("grades"), dict):
                raise AppError("共通評価がありません。")
            for g in r["grades"].values():
                if not isinstance(g, dict) or not re.fullmatch(
                    r"#[0-9A-Fa-f]{6}", str(g.get("color", ""))
                ):
                    raise AppError("評価色が不正です。")
    docs = data["instructions"]["documents"]
    if manifest.get("kind") == "reference" and docs:
        raise AppError("参照パッケージに指示書を含めることはできません。")
    validate_documents(docs)
    from .analysis_settings import validate_settings

    validate_settings(data["settings"]["analysis"])
    return data


@dataclass(frozen=True)
class Package:
    manifest: dict
    data: dict


def create_package(data, kind, source_app, device_id, dataset_id, package_id=None):
    manifest = dict(
        format="PrizeSwapPackage",
        version=VERSION,
        min_reader_version=VERSION,
        package_id=package_id or uid(),
        dataset_id=dataset_id,
        created_at=now(),
        source_app=source_app,
        source_device_id=device_id,
        kind=kind,
        data_updated_at=None,
        app_version="1.0",
        files=[],
    )
    stamps = [d["updated_at"] for d in data["instructions"]["documents"]]
    if kind != "instructions":
        stamps.extend(
            r.get("updated_at")
            for frame in data["sales"]["frames"] + data["history"]["frames"]
            for r in frame["records"]
        )
    known = []
    for stamp in stamps:
        if not stamp:
            continue
        try:
            _timestamp(stamp)
            known.append(datetime.fromisoformat(stamp.replace("Z", "+00:00")))
        except AppError:
            continue
    manifest["data_updated_at"] = max(known).isoformat() if known else None
    validate_data(data, manifest)
    contents = {f: encoded(data[Path(f).stem]) for f in DATA_FILES}
    for path, raw in contents.items():
        if len(raw) > MAX_FILE:
            raise AppError("データが大きすぎます。履歴の期間を短くしてください。")
        manifest["files"].append(dict(path=path, sha256=sha(raw), size=len(raw)))
    validate_schema("manifest", manifest)
    contents["manifest.json"] = encoded(manifest)
    contents["metadata/checksums.json"] = encoded(
        {f: sha(contents[f]) for f in DATA_FILES}
    )
    if sum(map(len, contents.values())) > MAX_TOTAL:
        raise AppError("パッケージは展開後64MiB以下にしてください。")
    out = io.BytesIO()
    with zipfile.ZipFile(
        out, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6
    ) as z:
        for path, raw in contents.items():
            compression = (
                zipfile.ZIP_STORED
                if len(raw) > max(1, len(zlib.compress(raw))) * 195
                else zipfile.ZIP_DEFLATED
            )
            z.writestr(path, raw, compress_type=compression)
    raw = out.getvalue()
    read_package(raw)
    return raw


def read_package(source):
    raw = (
        source
        if isinstance(source, bytes)
        else (
            Path(source).read_bytes()
            if Path(source).stat().st_size <= MAX_TOTAL
            else b""
        )
    )
    if not raw or len(raw) > MAX_TOTAL:
        raise AppError("ZIPは64MiB以下にしてください。")
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            infos = z.infolist()
            names = [x.filename for x in infos]
            if len(names) != len(ALL_FILES) or set(names) != ALL_FILES:
                raise AppError("パッケージのファイル構成が不正です。")
            if len(set(names)) != len(names):
                raise AppError("ZIPエントリが重複しています。")
            if sum(x.file_size for x in infos) > MAX_TOTAL:
                raise AppError("展開後のサイズ制限を超えています。")
            contents = {}
            for info in infos:
                if (
                    info.flag_bits & 1
                    or info.compress_type
                    not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED)
                    or stat.S_ISLNK(info.external_attr >> 16)
                ):
                    raise AppError("暗号化・リンク・未対応圧縮のZIPは使用できません。")
                if (
                    info.file_size > MAX_FILE
                    or info.file_size > max(1, info.compress_size) * 200
                ):
                    raise AppError("ZIPの展開サイズ・圧縮率が不正です。")
                with z.open(info) as f:
                    contents[info.filename] = f.read(MAX_FILE + 1)
                if len(contents[info.filename]) != info.file_size:
                    raise AppError("ZIPの実サイズが一致しません。")
        manifest = strict_json(contents["manifest.json"])
        validate_schema("manifest", manifest)
        _timestamp(manifest["created_at"])
        if manifest.get("data_updated_at") is not None:
            _timestamp(manifest["data_updated_at"])
        checks = strict_json(contents["metadata/checksums.json"])
        if not isinstance(checks, dict) or set(checks) != set(DATA_FILES):
            raise AppError("チェックサム一覧が不正です。")
        files = {f["path"]: f for f in manifest["files"]}
        if len(manifest["files"]) != 6 or set(files) != set(DATA_FILES):
            raise AppError("マニフェストのファイル一覧が不正です。")
        for path in DATA_FILES:
            if (
                sha(contents[path]) != checks[path]
                or checks[path] != files[path]["sha256"]
                or len(contents[path]) != files[path]["size"]
            ):
                raise AppError("チェックサムまたはサイズが一致しません：" + path)
        data = {Path(f).stem: strict_json(contents[f]) for f in DATA_FILES}
        validate_data(data, manifest)
        return Package(deepcopy(manifest), data)
    except (
        zipfile.BadZipFile,
        RuntimeError,
        OSError,
        KeyError,
        TypeError,
        OverflowError,
        RecursionError,
        ValueError,
    ) as exc:
        if isinstance(exc, AppError):
            raise
        raise AppError("パッケージを読み込めません：" + str(exc)) from exc


def save_package(path, raw):
    path = Path(path)
    if path.suffix.lower() != ".zip":
        raise AppError("保存先は.zipを指定してください。")
    read_package(raw)
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(dir=path.parent, suffix=".tmp", delete=False) as f:
        tmp = Path(f.name)
        f.write(raw)
        f.flush()
        os.fsync(f.fileno())
    try:
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)
