"""Import a Windows file-server CSV export into the database.

The importer is tolerant about header naming (the Windows tools vary slightly)
and builds the materialized-path tree with **bounded memory** so multi-GB
exports (millions of rows) don't blow up the worker:

* the upload is streamed to a temp file (the caller does this) and parsed from
  disk, never decoded whole into RAM;
* it runs in two passes — folders first, then files — so only the (relatively
  small) set of folders is held in memory for parent resolution; files are
  streamed and inserted in fixed-size batches and never all held at once.
"""
from __future__ import annotations

import csv
import io
import os
import re

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from . import parsing
from .models import Dataset, Node

# Rows per INSERT/COPY batch — keeps peak memory flat regardless of file size.
# Tunable via env; COPY makes huge batches unnecessary (diminishing returns).
_BATCH = max(1, int(os.environ.get("IMPORT_BATCH_SIZE", "50000")))
# Use Postgres COPY for the bulk load (much faster than INSERT). Off -> ORM bulk.
_USE_COPY = os.environ.get("IMPORT_USE_COPY", "1") != "0"
# CSV fields can be long; lift the default 128 KB field-size cap.
csv.field_size_limit(64 * 1024 * 1024)


def _norm(h: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (h or "").lower())


_EXACT = {
    "name": "name",
    "full_path": "fullpath",
    "size": "size",
    "allocated": "allocated",
    "files": "files",
    "folders": "folders",
    "last_modified": "lastmodified",
    "last_accessed": "lastaccessed",
    "owner": "owner",
    "file_type": "type",
}


def resolve_headers(fieldnames: list[str]) -> dict[str, str | None]:
    """Map our logical column names to the actual CSV header strings."""
    norm_to_orig: dict[str, str] = {}
    for h in fieldnames:
        norm_to_orig.setdefault(_norm(h), h)

    resolved: dict[str, str | None] = {}
    for target, key in _EXACT.items():
        resolved[target] = norm_to_orig.get(key)

    def find_contains(*needles: str) -> str | None:
        for nh, orig in norm_to_orig.items():
            if all(n in nh for n in needles):
                return orig
        return None

    resolved["pct_parent"] = find_contains("parent")
    resolved["dir_level"] = find_contains("dir", "level") or find_contains("level")
    return resolved


def _resolve_indices(header: list[str]) -> dict[str, int | None]:
    name_map = resolve_headers(header)
    return {
        target: (header.index(orig) if orig in header else None)
        for target, orig in name_map.items()
    }


def _cell(row: list[str], i: int | None):
    if i is None or i < 0 or i >= len(row):
        return None
    return row[i]


def _parse_row(row: list[str], idx: dict[str, int | None]) -> dict:
    full_path = (_cell(row, idx["full_path"]) or "").strip()
    is_dir = parsing.is_directory(full_path)
    name_val = (_cell(row, idx["name"]) or "").strip()
    if not name_val:
        name_val = parsing.normalize_path(full_path).rsplit("/", 1)[-1]
    return {
        "name": name_val,
        "full_path": full_path,
        "path_key": parsing.normalize_path(full_path),
        "is_dir": is_dir,
        "size_raw": _s(_cell(row, idx["size"])),
        "size_bytes": parsing.parse_size(_cell(row, idx["size"])),
        "allocated_raw": _s(_cell(row, idx["allocated"])),
        "allocated_bytes": parsing.parse_size(_cell(row, idx["allocated"])),
        "files_count": parsing.parse_int(_cell(row, idx["files"])),
        "folders_count": parsing.parse_int(_cell(row, idx["folders"])),
        "pct_parent_raw": _s(_cell(row, idx["pct_parent"])),
        "pct_parent": parsing.parse_percent(_cell(row, idx["pct_parent"])),
        "last_modified": parsing.parse_date(_cell(row, idx["last_modified"])),
        "last_accessed": parsing.parse_date(_cell(row, idx["last_accessed"])),
        "owner": _s(_cell(row, idx["owner"])),
        "file_type": _normalize_type(_cell(row, idx["file_type"]), is_dir),
        "dir_level": parsing.parse_int(_cell(row, idx["dir_level"])),
    }


# ---- Public entry points ----

def import_csv(db: Session, *, name: str, filename: str, content: bytes | str) -> Dataset:
    """Import from an in-memory string/bytes (used by tests and small inputs)."""
    if isinstance(content, bytes):
        content = content.decode("utf-8-sig", errors="replace")

    def opener():
        return io.StringIO(content)

    return _run(db, name=name, filename=filename, opener=opener)


def import_csv_path(db: Session, *, name: str, filename: str, path: str) -> Dataset:
    """Import by streaming from a file on disk (used for large uploads)."""

    def opener():
        # utf-8-sig strips a BOM if the Windows tool added one.
        return open(path, encoding="utf-8-sig", errors="replace", newline="")

    return _run(db, name=name, filename=filename, opener=opener)


def _run(db: Session, *, name: str, filename: str, opener) -> Dataset:
    # ---- header ----
    with opener() as fh:
        reader = csv.reader(fh)
        try:
            header = next(reader)
        except StopIteration:
            raise ValueError("CSV has no header row")
    idx = _resolve_indices(header)
    if idx.get("full_path") is None:
        raise ValueError(
            f"Could not find a 'Full Path' column in the CSV. Headers seen: {header}"
        )

    dataset = Dataset(name=name, filename=filename, row_count=0)
    db.add(dataset)
    db.flush()  # assigns dataset.id

    max_id = db.execute(select(func.coalesce(func.max(Node.id), 0))).scalar_one()
    next_id = int(max_id) + 1

    # ---- pass 1: folders (held in memory for parent resolution) ----
    folders: list[dict] = []
    with opener() as fh:
        reader = csv.reader(fh)
        next(reader, None)  # skip header
        fp_i = idx["full_path"]
        for row in reader:
            fp = (_cell(row, fp_i) or "").strip()
            if fp and parsing.is_directory(fp):
                folders.append(_parse_row(row, idx))

    # Ancestor-first so a folder's parent is processed before it.
    folders.sort(key=lambda d: (d["path_key"].count("/"), d["path_key"]))

    # folder path_key -> (id, mat_path, depth)
    folder_map: dict[str, tuple[int, str, int]] = {}
    batch: list[dict] = []
    for data in folders:
        nid = next_id
        next_id += 1
        parent = _find_parent(parsing.parent_key(data["full_path"]), folder_map)
        if parent is not None:
            pid, pmat, pdepth = parent
            mat_path, depth, parent_id = f"{pmat}{nid}/", pdepth + 1, pid
        else:
            mat_path, depth, parent_id = f"/{nid}/", 0, None
        folder_map[data["path_key"]] = (nid, mat_path, depth)
        batch.append(_row_mapping(data, nid, dataset.id, parent_id, mat_path, depth))
        if len(batch) >= _BATCH:
            _flush(db, batch)
    _flush(db, batch)
    folder_count = len(folders)
    del folders  # free before streaming files

    # ---- pass 2: files (streamed, never all held) ----
    file_count = 0
    with opener() as fh:
        reader = csv.reader(fh)
        next(reader, None)  # skip header
        fp_i = idx["full_path"]
        for row in reader:
            fp = (_cell(row, fp_i) or "").strip()
            if not fp or parsing.is_directory(fp):
                continue
            data = _parse_row(row, idx)
            nid = next_id
            next_id += 1
            parent = _find_parent(parsing.parent_key(fp), folder_map)
            if parent is not None:
                pid, pmat, pdepth = parent
                mat_path, depth, parent_id = f"{pmat}{nid}/", pdepth + 1, pid
            else:
                mat_path, depth, parent_id = f"/{nid}/", 0, None
            batch.append(_row_mapping(data, nid, dataset.id, parent_id, mat_path, depth))
            file_count += 1
            if len(batch) >= _BATCH:
                _flush(db, batch)
    _flush(db, batch)

    total = folder_count + file_count
    if total == 0:
        db.rollback()
        raise ValueError("CSV contained no usable rows")

    dataset.row_count = total
    if db.bind.dialect.name == "postgresql":
        db.execute(
            text("SELECT setval(pg_get_serial_sequence('nodes','id'), :v)"),
            {"v": next_id},
        )
    db.commit()
    return dataset


def _row_mapping(data, nid, dataset_id, parent_id, mat_path, depth) -> dict:
    d = dict(data)
    d.update(
        id=nid,
        dataset_id=dataset_id,
        parent_id=parent_id,
        mat_path=mat_path,
        depth=depth,
    )
    return d


def _flush(db: Session, batch: list[dict]) -> None:
    if not batch:
        return
    if _USE_COPY and db.bind.dialect.name == "postgresql":
        _copy_flush(db, batch)
    else:
        db.bulk_insert_mappings(Node, batch)
    batch.clear()


# Column order used by the COPY fast path.
_COPY_COLUMNS = [
    "id", "dataset_id", "parent_id", "mat_path", "depth", "name", "full_path",
    "path_key", "is_dir", "size_raw", "size_bytes", "allocated_raw",
    "allocated_bytes", "files_count", "folders_count", "pct_parent_raw",
    "pct_parent", "last_modified", "last_accessed", "owner", "file_type",
    "dir_level",
]


def _copy_value(d: dict, key: str):
    v = d.get(key)
    if v is None:
        return None
    if key == "is_dir":
        return "t" if v else "f"
    if key in ("last_modified", "last_accessed"):
        return v.isoformat()
    return v


def _copy_flush(db: Session, batch: list[dict]) -> None:
    """Load a batch via COPY ... FROM STDIN (CSV). None -> NULL; bool -> t/f."""
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    for d in batch:
        writer.writerow([_copy_value(d, c) for c in _COPY_COLUMNS])
    buf.seek(0)
    sql = (
        f"COPY nodes ({', '.join(_COPY_COLUMNS)}) "
        "FROM STDIN WITH (FORMAT csv, NULL '')"
    )
    # Run on the session's own DBAPI connection so it shares the transaction.
    raw = db.connection().connection
    cur = raw.cursor()
    try:
        cur.copy_expert(sql, buf)
    finally:
        cur.close()


def _find_parent(parent_key, folder_map):
    """Nearest ancestor folder for ``parent_key`` (walk the path upward)."""
    key = parent_key
    while key:
        found = folder_map.get(key)
        if found is not None:
            return found
        if "/" not in key:
            break
        key = key.rsplit("/", 1)[0]
    return None


def _s(v) -> str | None:
    if v is None:
        return None
    v = str(v).strip()
    return v or None


def _normalize_type(raw, is_dir: bool) -> str | None:
    if is_dir:
        return "Folder"
    s = _s(raw)
    return s or "File"
