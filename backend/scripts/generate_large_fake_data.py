"""Generate a large, representative fake file-server CSV for performance testing.

Unlike ``generate_fake_data.py`` (which holds every row in memory and computes
exact recursive rollups with an O(rows * folders) pass -- fine at ~1k rows,
impossible at millions), this script streams rows straight to disk and
accumulates folder rollups bottom-up in a single O(rows) pass using per-level
index arrays. It intentionally produces the same *column layout* the importer
expects but does not guarantee byte-exact recursive Size/Files/Folders totals
-- those columns are read-only display fields the app never recomputes from,
so approximate values are sufficient (see ``app/services.py`` docstrings: all
live counts/sums are derived from the tree at query time, never from the CSV's
own rollup columns).

Usage:
    python generate_large_fake_data.py --out /tmp/big.csv \
        --l1 40 --l2-per-l1 125 --l3-per-l2 110 --files-min 2 --files-max 4
"""
from __future__ import annotations

import argparse
import csv
import random
from datetime import date, timedelta

random.seed(1337)

OWNERS = [
    "CORP\\jsmith", "CORP\\mwilson", "CORP\\achen", "CORP\\rgarcia",
    "CORP\\tnguyen", "CORP\\kpatel", "CORP\\dmiller", "CORP\\lokafor",
    "CORP\\svc_backup", "CORP\\administrator", "CORP\\pwhite", "CORP\\hyoon",
]

# A realistic core of file kinds...
_BASE_KINDS = [
    ("pptx", "PPTX File", (200_000, 60_000_000)),
    ("docx", "DOCX File", (20_000, 5_000_000)),
    ("xlsx", "XLSX File", (15_000, 25_000_000)),
    ("pdf", "PDF File", (50_000, 40_000_000)),
    ("txt", "Text File", (200, 500_000)),
    ("csv", "CSV File", (1_000, 80_000_000)),
    ("jpg", "JPG File", (80_000, 8_000_000)),
    ("png", "PNG File", (20_000, 6_000_000)),
    ("zip", "ZIP File", (100_000, 800_000_000)),
    ("mp4", "MP4 File", (2_000_000, 4_000_000_000)),
    ("dwg", "DWG File", (300_000, 120_000_000)),
    ("psd", "PSD File", (5_000_000, 900_000_000)),
    ("vsdx", "VSDX File", (40_000, 3_000_000)),
    ("msg", "MSG File", (10_000, 2_000_000)),
    ("log", "Log File", (1_000, 50_000_000)),
    ("bak", "BAK File", (1_000_000, 5_000_000_000)),
    ("iso", "Disc Image File", (100_000_000, 8_000_000_000)),
    ("dat", "DAT File", (1_000, 200_000_000)),
    ("xml", "XML File", (500, 10_000_000)),
    ("json", "JSON File", (200, 5_000_000)),
    ("pst", "Outlook Data File", (50_000_000, 20_000_000_000)),
    ("accdb", "Access Database", (1_000_000, 2_000_000_000)),
    ("sql", "SQL File", (1_000, 100_000_000)),
    ("py", "Python File", (200, 200_000)),
]


def _build_file_kinds(min_types: int) -> list[tuple[str, str, tuple[int, int]]]:
    """Real kinds + synthetic padding so we always have >= ``min_types`` types."""
    kinds = list(_BASE_KINDS)
    n = 0
    while len(kinds) < min_types:
        ext = f"syn{n:04d}"
        label = f"SYN{n:04d} File"
        lo = random.choice([200, 1_000, 50_000, 1_000_000])
        hi = lo * random.choice([50, 500, 5_000])
        kinds.append((ext, label, (lo, hi)))
        n += 1
    return kinds


DEPT_WORDS = [
    "Finance", "Engineering", "Marketing", "HumanResources", "Legal",
    "Operations", "Sales", "IT", "Research", "Facilities", "Compliance",
    "Support", "Product", "Design", "Analytics", "Procurement", "Security",
    "Logistics", "Training", "Strategy",
]
PROJECT_WORDS = [
    "Apollo", "Titan", "Falcon", "Nimbus", "Quantum", "Atlas", "Vertex",
    "Horizon", "Pioneer", "Catalyst", "Beacon", "Summit", "Orbit", "Pulse",
    "Zenith", "Comet", "Drift", "Ember", "Forge", "Glacier",
]
SUBFOLDERS = [
    "Archive", "Drafts", "Final", "Reports", "Backups", "Shared", "Old",
    "2021", "2022", "2023", "2024", "2025", "Working", "Templates", "Exports",
    "Scans", "Misc",
]


def fmt_size(b: int) -> str:
    units = [("PB", 1024**5), ("TB", 1024**4), ("GB", 1024**3),
             ("MB", 1024**2), ("KB", 1024), ("B", 1)]
    for label, mult in units:
        if b >= mult:
            val = b / mult
            return f"{val:.1f} {label}" if label != "B" else f"{int(b)} B"
    return "0 B"


_EPOCH = date(2015, 1, 1)
_SPAN = (date(2025, 12, 31) - _EPOCH).days


def rand_date() -> date:
    return _EPOCH + timedelta(days=random.randint(0, _SPAN))


HEADERS = [
    "Name", "Full Path", "Size", "Allocated", "Files", "Folders",
    "% of Parent (Allocated)", "Last Modified", "Last Accessed", "Owner",
    "Type", "Dir Level (Relative)",
]


def _name(words: list[str], idx: int) -> str:
    return f"{random.choice(words)}_{idx:05d}"


def generate(
    out_path: str, *, l1: int, l2_per_l1: int, l3_per_l2: int,
    files_min: int, files_max: int, min_types: int,
) -> dict:
    kinds = _build_file_kinds(min_types)
    writer_file = open(out_path, "w", newline="", encoding="utf-8")
    w = csv.writer(writer_file)
    w.writerow(HEADERS)

    counts = {"folders": 0, "files": 0}

    def write_row(name, full_path, size, is_dir, level, files_n, folders_n, ftype, own_size_for_fmt=None):
        modified = rand_date()
        accessed = modified + timedelta(days=random.randint(0, 800))
        if accessed > date(2025, 12, 31):
            accessed = date(2025, 12, 31)
        size_str = "" if is_dir and own_size_for_fmt is None else fmt_size(size)
        w.writerow([
            name, full_path, size_str, size_str, files_n, folders_n,
            "100.0 %", modified.strftime("%m/%d/%Y"), accessed.strftime("%m/%d/%Y"),
            random.choice(OWNERS), ftype, level,
        ])

    root_path = "D:\\FileServer\\"

    # ---- Level 1: departments ----
    l1_names = [_name(DEPT_WORDS, i) for i in range(l1)]
    l1_paths = [f"{root_path}{name}\\" for name in l1_names]

    # ---- Level 2 & 3 & files: streamed, minimal memory ----
    total_size = 0
    for i1, p1 in enumerate(l1_paths):
        l1_size = 0
        for i2 in range(l2_per_l1):
            name2 = _name(PROJECT_WORDS, i2)
            p2 = f"{p1}{name2}\\"
            l2_size = 0
            for i3 in range(l3_per_l2):
                name3 = f"{random.choice(SUBFOLDERS)}_{i3:04d}"
                p3 = f"{p2}{name3}\\"
                nfiles = random.randint(files_min, files_max)
                leaf_size = 0
                for fi in range(nfiles):
                    ext, type_label, (lo, hi) = random.choice(kinds)
                    size = random.randint(lo, hi)
                    fname = f"file_{fi:03d}_{random.randint(1000, 9999)}.{ext}"
                    fpath = f"{p3}{fname}"
                    write_row(fname, fpath, size, False, 4, 1, 0, type_label)
                    leaf_size += size
                    counts["files"] += 1
                write_row(name3, p3, leaf_size, True, 3, nfiles, 0, "File Folder")
                counts["folders"] += 1
                l2_size += leaf_size
            write_row(name2, p2, l2_size, True, 2, 0, l3_per_l2, "File Folder")
            counts["folders"] += 1
            l1_size += l2_size
        write_row(l1_names[i1], p1, l1_size, True, 1, 0, l2_per_l1, "File Folder")
        counts["folders"] += 1
        total_size += l1_size

    write_row("FileServer", root_path, total_size, True, 0, 0, l1, "File Folder")
    counts["folders"] += 1

    writer_file.close()
    return {
        "folders": counts["folders"],
        "files": counts["files"],
        "total_rows": counts["folders"] + counts["files"],
        "file_types": len(kinds),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--l1", type=int, default=40, help="department folders")
    ap.add_argument("--l2-per-l1", type=int, default=125, help="project folders per department")
    ap.add_argument("--l3-per-l2", type=int, default=110, help="leaf folders per project")
    ap.add_argument("--files-min", type=int, default=2)
    ap.add_argument("--files-max", type=int, default=4)
    ap.add_argument("--min-types", type=int, default=550)
    args = ap.parse_args()

    stats = generate(
        args.out, l1=args.l1, l2_per_l1=args.l2_per_l1, l3_per_l2=args.l3_per_l2,
        files_min=args.files_min, files_max=args.files_max, min_types=args.min_types,
    )
    print(
        f"Wrote {stats['total_rows']:,} rows "
        f"({stats['folders']:,} folders, {stats['files']:,} files, "
        f"{stats['file_types']} file types) to {args.out}"
    )


if __name__ == "__main__":
    main()
