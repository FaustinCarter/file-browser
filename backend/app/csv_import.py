"""Import a Windows file-server CSV export into the database.

The importer is tolerant about header naming (the Windows tools vary slightly)
and builds the materialized-path tree in a single pass by assigning ids in
ancestor-first order.
"""
from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from . import parsing
from .models import Dataset, Node


def _norm(h: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (h or "").lower())


# target -> matcher. First matcher that returns True wins; we try exact-ish
# matches before fuzzy "contains" ones by checking equality first.
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

    # Fuzzy ones.
    def find_contains(*needles: str, avoid: str | None = None) -> str | None:
        for nh, orig in norm_to_orig.items():
            if avoid and avoid in nh:
                continue
            if all(n in nh for n in needles):
                return orig
        return None

    resolved["pct_parent"] = find_contains("parent")
    resolved["dir_level"] = find_contains("dir", "level") or find_contains("level")
    return resolved


@dataclass
class _Rec:
    data: dict
    path_key: str
    parent_key: str | None
    # filled in during id assignment
    id: int = 0
    parent_id: int | None = None
    mat_path: str = ""
    depth: int = 0


def import_csv(db: Session, *, name: str, filename: str, content: bytes | str) -> Dataset:
    if isinstance(content, bytes):
        # utf-8-sig strips a BOM if the Windows tool added one.
        text_content = content.decode("utf-8-sig", errors="replace")
    else:
        text_content = content

    reader = csv.DictReader(io.StringIO(text_content))
    if not reader.fieldnames:
        raise ValueError("CSV has no header row")
    cols = resolve_headers(list(reader.fieldnames))
    if not cols.get("full_path"):
        raise ValueError(
            "Could not find a 'Full Path' column in the CSV. "
            f"Headers seen: {reader.fieldnames}"
        )

    def g(row: dict, target: str):
        src = cols.get(target)
        return row.get(src) if src else None

    records: list[_Rec] = []
    by_key: dict[str, _Rec] = {}

    for row in reader:
        full_path = (g(row, "full_path") or "").strip()
        if not full_path:
            continue
        is_dir = parsing.is_directory(full_path)
        name_val = (g(row, "name") or "").strip()
        if not name_val:
            # derive from path
            name_val = parsing.normalize_path(full_path).rsplit("/", 1)[-1]
        pkey = parsing.normalize_path(full_path)
        rec = _Rec(
            data={
                "name": name_val,
                "full_path": full_path,
                "path_key": pkey,
                "is_dir": is_dir,
                "size_raw": _s(g(row, "size")),
                "size_bytes": parsing.parse_size(g(row, "size")),
                "allocated_raw": _s(g(row, "allocated")),
                "allocated_bytes": parsing.parse_size(g(row, "allocated")),
                "files_count": parsing.parse_int(g(row, "files")),
                "folders_count": parsing.parse_int(g(row, "folders")),
                "pct_parent_raw": _s(g(row, "pct_parent")),
                "pct_parent": parsing.parse_percent(g(row, "pct_parent")),
                "last_modified": parsing.parse_date(g(row, "last_modified")),
                "last_accessed": parsing.parse_date(g(row, "last_accessed")),
                "owner": _s(g(row, "owner")),
                "file_type": _normalize_type(g(row, "file_type"), is_dir),
                "dir_level": parsing.parse_int(g(row, "dir_level")),
            },
            path_key=pkey,
            parent_key=parsing.parent_key(full_path),
        )
        records.append(rec)
        by_key.setdefault(pkey, rec)

    if not records:
        raise ValueError("CSV contained no usable rows")

    # Ancestor-first ordering: shorter paths first guarantees a node's parent is
    # processed before it.
    records.sort(key=lambda r: (r.path_key.count("/"), r.path_key))

    # Create the dataset row to get an id.
    dataset = Dataset(name=name, filename=filename, row_count=len(records))
    db.add(dataset)
    db.flush()  # assigns dataset.id

    # Reserve a contiguous id block so we can compute mat_path in memory.
    max_id = db.execute(select(func.coalesce(func.max(Node.id), 0))).scalar_one()
    next_id = int(max_id) + 1

    for rec in records:
        rec.id = next_id
        next_id += 1

    # Resolve parents (walk up the path until we find a known ancestor).
    for rec in records:
        parent = _find_parent(rec, by_key)
        if parent is not None:
            rec.parent_id = parent.id
            rec.mat_path = f"{parent.mat_path}{rec.id}/"
            rec.depth = parent.depth + 1
        else:
            rec.parent_id = None
            rec.mat_path = f"/{rec.id}/"
            rec.depth = 0

    mappings = []
    for rec in records:
        d = dict(rec.data)
        d.update(
            id=rec.id,
            dataset_id=dataset.id,
            parent_id=rec.parent_id,
            mat_path=rec.mat_path,
            depth=rec.depth,
        )
        mappings.append(d)

    db.bulk_insert_mappings(Node, mappings)

    # Keep the sequence ahead of our manually assigned ids (Postgres only).
    if db.bind.dialect.name == "postgresql":
        db.execute(
            text("SELECT setval(pg_get_serial_sequence('nodes','id'), :v)"),
            {"v": next_id},
        )

    db.commit()
    return dataset


def _find_parent(rec: _Rec, by_key: dict[str, _Rec]) -> _Rec | None:
    key = rec.parent_key
    while key:
        parent = by_key.get(key)
        if parent is not None and parent is not rec:
            return parent
        # walk up another level
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
