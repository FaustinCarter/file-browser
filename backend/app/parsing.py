"""Parsers for the raw text columns coming out of the Windows CSV export.

The CSV is produced by a Windows disk-usage tool (TreeSize-style). Numbers come
as human readable strings ("10 GB", "11.8 %") and we want to normalise them into
sortable numeric values while *also* keeping the original text so the UI can show
exactly what the source said.
"""
from __future__ import annotations

import re
from datetime import date

# Binary multipliers (these tools report KiB/MiB but label them KB/MB).
_SIZE_UNITS = {
    "B": 1,
    "BYTE": 1,
    "BYTES": 1,
    "KB": 1024,
    "KIB": 1024,
    "MB": 1024**2,
    "MIB": 1024**2,
    "GB": 1024**3,
    "GIB": 1024**3,
    "TB": 1024**4,
    "TIB": 1024**4,
    "PB": 1024**5,
    "PIB": 1024**5,
}

_SIZE_RE = re.compile(r"^\s*([\d.,]+)\s*([A-Za-z]+)?\s*$")


def parse_size(value: str | None) -> int | None:
    """Convert a size string like '10 GB' or '1,024 KB' into a byte count.

    Returns None when the value is empty/unparseable so callers can leave the
    numeric column NULL rather than guessing.
    """
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    m = _SIZE_RE.match(text)
    if not m:
        return None
    number = m.group(1).replace(",", "")
    unit = (m.group(2) or "B").upper()
    if unit not in _SIZE_UNITS:
        return None
    try:
        return int(round(float(number) * _SIZE_UNITS[unit]))
    except ValueError:
        return None


def parse_percent(value: str | None) -> float | None:
    """'11.8 %' -> 11.8"""
    if value is None:
        return None
    text = str(value).strip().rstrip("%").strip().replace(",", "")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def parse_int(value: str | None) -> int | None:
    if value is None:
        return None
    text = str(value).strip().replace(",", "")
    if not text:
        return None
    try:
        return int(float(text))
    except ValueError:
        return None


_DATE_FORMATS = ("%m/%d/%Y", "%m/%d/%y", "%Y-%m-%d")


def parse_date(value: str | None) -> date | None:
    """Parse the MM/DD/YYYY dates (with a couple of fallbacks)."""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    # Drop any trailing time component if present.
    text = text.split(" ")[0].split("T")[0]
    from datetime import datetime

    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def is_directory(full_path: str | None) -> bool:
    """Folders are exported with a trailing backslash (or forward slash)."""
    if not full_path:
        return False
    return full_path.rstrip().endswith("\\") or full_path.rstrip().endswith("/")


def normalize_path(full_path: str) -> str:
    """Normalise a Windows path for parent/child matching.

    We keep the original casing for display but use a normalised key (lowercased,
    forward slashes, single trailing slash for dirs stripped) for lookups.
    """
    p = full_path.strip().replace("\\", "/")
    return p.rstrip("/").lower()


def parent_key(full_path: str) -> str | None:
    """Return the normalised lookup key of the parent of ``full_path``.

    For 'C:/Foo/Bar/Baz.txt' -> 'c:/foo/bar'. For 'C:/Foo/' -> 'c:/foo'... wait
    parent of C:/Foo/ is C:/ . We strip the trailing slash then drop the last
    component.
    """
    norm = normalize_path(full_path)
    if "/" not in norm:
        return None
    parent = norm.rsplit("/", 1)[0]
    return parent or None
