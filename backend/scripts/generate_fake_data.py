"""Generate a realistic fake file-server CSV for testing.

Produces a nested folder tree (>= 50 folders, >= 1000 total rows) matching the
exact column layout of the Windows export. Sizes roll up from children, folder
file/folder counts are real, and "% of Parent (Allocated)" is computed.

Usage:
    python generate_fake_data.py --out ../../sample_data/fake_fileserver.csv \
        --min-rows 1200
"""
from __future__ import annotations

import argparse
import csv
import random
from datetime import date, timedelta

random.seed(42)

OWNERS = [
    "CORP\\jsmith", "CORP\\mwilson", "CORP\\achen", "CORP\\rgarcia",
    "CORP\\tnguyen", "CORP\\kpatel", "CORP\\dmiller", "CORP\\lokafor",
    "CORP\\svc_backup", "CORP\\administrator",
]

# (extension, Type label, typical size range in bytes)
FILE_KINDS = [
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
    ("tif", "TIF File", (500_000, 200_000_000)),
]

DEPARTMENTS = [
    "Finance", "Engineering", "Marketing", "HumanResources", "Legal",
    "Operations", "Sales", "IT", "Research", "Facilities",
]
PROJECT_WORDS = [
    "Apollo", "Titan", "Falcon", "Nimbus", "Quantum", "Atlas", "Vertex",
    "Horizon", "Pioneer", "Catalyst", "Beacon", "Summit", "Orbit", "Pulse",
]
SUBFOLDERS = [
    "Archive", "Drafts", "Final", "Reports", "Backups", "Shared", "Old",
    "2021", "2022", "2023", "2024", "Working", "Templates", "Exports",
]


def fmt_size(b: int) -> str:
    units = [("PB", 1024**5), ("TB", 1024**4), ("GB", 1024**3),
             ("MB", 1024**2), ("KB", 1024), ("B", 1)]
    for label, mult in units:
        if b >= mult:
            val = b / mult
            return f"{val:.1f} {label}" if label != "B" else f"{int(b)} B"
    return "0 B"


def rand_date(start_year=2015, end_year=2025) -> date:
    start = date(start_year, 1, 1)
    end = date(end_year, 12, 31)
    return start + timedelta(days=random.randint(0, (end - start).days))


class Counter:
    def __init__(self):
        self.rows = []


def make_file(name_base, path_prefix, level, rows):
    ext, type_label, (lo, hi) = random.choice(FILE_KINDS)
    size = random.randint(lo, hi)
    name = f"{name_base}.{ext}"
    full = f"{path_prefix}{name}"
    modified = rand_date()
    # accessed >= modified
    accessed = modified + timedelta(days=random.randint(0, 800))
    if accessed > date(2025, 12, 31):
        accessed = date(2025, 12, 31)
    rows.append({
        "Name": name,
        "Full Path": full,
        "Size": fmt_size(size),
        "Allocated": fmt_size(((size // 4096) + 1) * 4096),
        "Files": 1,
        "Folders": 0,
        "% of Parent (Allocated)": "",  # filled by caller
        "Last Modified": modified.strftime("%m/%d/%Y"),
        "Last Accessed": accessed.strftime("%m/%d/%Y"),
        "Owner": random.choice(OWNERS),
        "Type": type_label,
        "Dir Level (Relative)": level,
        "_size": size,
        "_idx": len(rows),
    })
    return rows[-1]


def make_folder(name, path_prefix, level, rows):
    full = f"{path_prefix}{name}\\"
    row = {
        "Name": name,
        "Full Path": full,
        "Size": "",
        "Allocated": "",
        "Files": 0,
        "Folders": 0,
        "% of Parent (Allocated)": "",
        "Last Modified": rand_date().strftime("%m/%d/%Y"),
        "Last Accessed": rand_date().strftime("%m/%d/%Y"),
        "Owner": random.choice(OWNERS),
        "Type": "File Folder",
        "Dir Level (Relative)": level,
        "_size": 0,
        "_idx": len(rows),
        "_full": full,
    }
    rows.append(row)
    return row


def build(min_rows: int):
    rows = []
    root_path = "D:\\FileServer\\"
    root = {
        "Name": "FileServer", "Full Path": root_path, "Size": "", "Allocated": "",
        "Files": 0, "Folders": 0, "% of Parent (Allocated)": "",
        "Last Modified": rand_date().strftime("%m/%d/%Y"),
        "Last Accessed": rand_date().strftime("%m/%d/%Y"),
        "Owner": "CORP\\administrator", "Type": "File Folder",
        "Dir Level (Relative)": 0, "_size": 0, "_idx": 0, "_full": root_path,
    }
    rows.append(root)

    folders = [root]  # track folder rows to roll up later, in creation order

    def add_files(folder_row, n, level):
        for _ in range(n):
            base = random.choice(PROJECT_WORDS) + "_" + random.choice([
                "summary", "report", "v1", "v2", "final", "draft", "notes",
                "data", "export", "budget", "plan", "spec", "review", "Q1",
                "Q2", "Q3", "Q4", "minutes", "proposal",
            ]) + f"_{random.randint(1, 999)}"
            make_file(base, folder_row["_full"], level, rows)

    # Level 1: departments
    for dept in DEPARTMENTS:
        d = make_folder(dept, root_path, 1, rows)
        folders.append(d)
        add_files(d, random.randint(2, 6), 2)
        # Level 2: projects
        nproj = random.randint(2, 5)
        for _ in range(nproj):
            proj = random.choice(PROJECT_WORDS) + str(random.randint(100, 999))
            p = make_folder(proj, d["_full"], 2, rows)
            folders.append(p)
            add_files(p, random.randint(3, 10), 3)
            # Level 3: subfolders
            for _ in range(random.randint(1, 4)):
                sub = random.choice(SUBFOLDERS)
                s = make_folder(sub, p["_full"], 3, rows)
                folders.append(s)
                add_files(s, random.randint(2, 12), 4)

    # Top up with extra files spread across existing folders until min_rows.
    while len(rows) < min_rows:
        target = random.choice(folders)
        lvl = int(target["Dir Level (Relative)"]) + 1
        add_files(target, random.randint(3, 15), lvl)

    _rollup(rows)
    return rows


def _rollup(rows):
    """Compute folder sizes, file/folder counts, and % of parent."""
    by_path = {r["Full Path"]: r for r in rows}

    def parent_path(full):
        p = full.rstrip("\\")
        if "\\" not in p:
            return None
        return p.rsplit("\\", 1)[0] + "\\"

    # roll sizes upward: process deepest first. Use the normalised component
    # depth so a file and its containing folder never tie (a file path and its
    # folder path otherwise have the same number of backslashes).
    def depth_key(r):
        return r["Full Path"].rstrip("\\").count("\\")

    ordered = sorted(rows, key=depth_key, reverse=True)
    for r in ordered:
        pp = parent_path(r["Full Path"])
        if pp and pp in by_path:
            parent = by_path[pp]
            parent["_size"] += r["_size"]
            if r["Type"] == "File Folder" or r["Full Path"].endswith("\\"):
                parent["Folders"] += 1
            else:
                parent["Files"] += 1

    # For folders, Files/Folders are recursive totals (TreeSize style). Count by
    # path prefix so the numbers match exactly what a recursive query returns.
    file_rows = [r for r in rows if not r["Full Path"].endswith("\\")]
    folder_rows = [r for r in rows if r["Full Path"].endswith("\\")]
    for r in rows:
        if r["Full Path"].endswith("\\"):
            prefix = r["Full Path"]
            r["Files"] = sum(1 for f in file_rows if f["Full Path"].startswith(prefix))
            r["Folders"] = sum(
                1 for d in folder_rows
                if d["Full Path"].startswith(prefix) and d["Full Path"] != prefix
            )

    # Format sizes and % of parent.
    for r in rows:
        if r["Full Path"].endswith("\\"):
            r["Size"] = fmt_size(r["_size"])
            r["Allocated"] = fmt_size(r["_size"])
        pp = parent_path(r["Full Path"])
        if pp and pp in by_path:
            parent_size = by_path[pp]["_size"] or 1
            pct = 100.0 * r["_size"] / parent_size
            r["% of Parent (Allocated)"] = f"{pct:.1f} %"
        else:
            r["% of Parent (Allocated)"] = "100.0 %"


HEADERS = [
    "Name", "Full Path", "Size", "Allocated", "Files", "Folders",
    "% of Parent (Allocated)", "Last Modified", "Last Accessed", "Owner",
    "Type", "Dir Level (Relative)",
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--min-rows", type=int, default=1200)
    args = ap.parse_args()

    rows = build(args.min_rows)
    folders = sum(1 for r in rows if r["Full Path"].endswith("\\"))
    with open(args.out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=HEADERS, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)
    print(f"Wrote {len(rows)} rows ({folders} folders) to {args.out}")


if __name__ == "__main__":
    main()
