"""Large-scale performance regression test.

Builds a synthetic dataset meeting the scale target this app is meant to
handle (>= 1,000,000 file rows, >= 500,000 folder rows, >= 500 distinct file
types -- matching a real ~2M-row TreeSize export) and asserts that every kind
of SELECT the UI issues -- tree navigation, the flat grid, type breakdowns,
counts/stats, node detail -- returns in under 3 seconds, across a matrix of
filter combinations (type, date range, no_transfer/processed tri-state, jira,
assignee, substring search, sort/pagination).

This is expensive (CSV generation + import take a few minutes), so it's
opt-in: set RUN_PERF_TESTS=1 to run it. It is skipped by default so the
regular `pytest` run stays fast.

    RUN_PERF_TESTS=1 DATABASE_URL=postgresql+psycopg2://... pytest tests/test_performance.py -s

It intentionally does NOT use the `client`/`loaded` fixtures from conftest.py:
those rely on the autouse `fresh_db` fixture, which drops/recreates every
table before *each* test function -- fine for cheap functional tests, fatal
here since re-importing 2.2M rows per test would make the suite take forever.
Instead this file has exactly one test function that imports the dataset once
and runs the whole query matrix against it.
"""
from __future__ import annotations

import os
import sys
import time

import pytest

RUN = os.environ.get("RUN_PERF_TESTS", "").lower() in ("1", "true", "yes", "on")

pytestmark = pytest.mark.skipif(
    not RUN, reason="opt-in: set RUN_PERF_TESTS=1 to run the large-scale performance suite"
)

BUDGET_SECONDS = 3.0


@pytest.fixture(autouse=True)
def fresh_db():
    """Override conftest.py's autouse `fresh_db` for this module.

    That fixture drops/recreates every table before *each* test function --
    fine for cheap functional tests, but it's function-scoped while our
    dataset setup (`perf_client`) is module-scoped, so without this override
    it would drop the just-imported 2.2M-row dataset immediately before the
    test body runs (higher-scoped fixtures are set up first, so `perf_client`
    finishes importing, then this fires and wipes it out from under the test).
    """
    yield

# Scale floor required by this test (see task spec): >=1M files, >=500k
# folders, >=500 file types. The tree shape (40 depts x 125 projects x 110
# leaf folders, 2-4 files per leaf) comfortably clears all three and matches
# the "~2M rows, ~500k folders" scale the app is meant to handle in practice.
GEN_ARGS = dict(l1=40, l2_per_l1=125, l3_per_l2=110, files_min=2, files_max=4, min_types=550)


@pytest.fixture(scope="module")
def perf_client(tmp_path_factory):
    """Import a large synthetic dataset once for the whole module."""
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
    import generate_large_fake_data as gen

    from app.database import Base, engine
    from app.main import app

    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)

    csv_path = str(tmp_path_factory.mktemp("perf") / "big.csv")
    t0 = time.time()
    stats = gen.generate(csv_path, **GEN_ARGS)
    print(f"\n[perf] generated {stats['total_rows']:,} rows "
          f"({stats['folders']:,} folders, {stats['files']:,} files, "
          f"{stats['file_types']} file types) in {time.time() - t0:.1f}s")
    assert stats["files"] >= 1_000_000
    assert stats["folders"] >= 500_000
    assert stats["file_types"] >= 500

    from fastapi.testclient import TestClient

    with TestClient(app) as client:
        t0 = time.time()
        resp = client.post(
            "/api/datasets",
            data={"name": "perf"},
            files={"file": ("big.csv", open(csv_path, "rb"), "text/csv")},
        )
        assert resp.status_code == 200, resp.text
        print(f"[perf] import took {time.time() - t0:.1f}s")
        dataset = resp.json()

        # Seed a representative mix of annotations so the flag/jira/assignee
        # filters below have real (non-trivial) matches on both sides.
        root_id = client.get(
            "/api/tree/children", params={"dataset_id": dataset["id"]}
        ).json()["children"][0]["id"]
        dept_id = client.get(
            "/api/tree/children", params={"dataset_id": dataset["id"], "parent_id": root_id}
        ).json()["children"][0]["id"]
        client.post(
            f"/api/nodes/{dept_id}/folder-flag",
            json={"field": "no_transfer", "value": True},
        )
        project_id = client.get(
            "/api/tree/children", params={"dataset_id": dataset["id"], "parent_id": dept_id}
        ).json()["children"][1]["id"]
        client.post(
            f"/api/nodes/{project_id}/folder-flag",
            json={"field": "processed", "value": True},
        )
        client.post(
            "/api/nodes/bulk-annotation",
            json={
                "node_id": project_id, "files_only": True,
                "values": {"jira_ticket": "MIG-100"},
            },
        )
        leaf_id = client.get(
            "/api/tree/children", params={"dataset_id": dataset["id"], "parent_id": project_id}
        ).json()["children"][0]["id"]
        client.post(
            "/api/nodes/bulk-annotation",
            json={"node_id": leaf_id, "files_only": True, "values": {"assignee": "alice"}},
        )

        yield client, dataset, {
            "root_id": root_id, "dept_id": dept_id, "project_id": project_id, "leaf_id": leaf_id,
        }


def test_all_select_queries_under_budget(perf_client):
    client, dataset, ids = perf_client
    dsid = dataset["id"]

    ft_resp = client.get(f"/api/datasets/{dsid}/file-types")
    assert ft_resp.status_code == 200, ft_resp.text
    file_types = [r["file_type"] for r in ft_resp.json()][:3]
    a_type = file_types[0]

    results: list[tuple[str, float, int]] = []

    def check(label, method, path, **kwargs):
        t0 = time.time()
        resp = method(path, **kwargs)
        elapsed = time.time() - t0
        assert resp.status_code == 200, f"{label}: {resp.status_code} {resp.text[:300]}"
        results.append((label, elapsed, resp.status_code))
        return resp

    g = client.get
    p = client.post

    # ---- Tree navigation, every level, no filter ----
    check("tree/root", g, "/api/tree/children", params={"dataset_id": dsid})
    check("tree/dept", g, "/api/tree/children", params={"dataset_id": dsid, "parent_id": ids["dept_id"]})
    check("tree/project", g, "/api/tree/children", params={"dataset_id": dsid, "parent_id": ids["project_id"]})
    check("tree/leaf", g, "/api/tree/children", params={"dataset_id": dsid, "parent_id": ids["leaf_id"]})

    # ---- Tree navigation, every filter individually, at root (most expensive
    # level: root's rollup spans the whole dataset) ----
    check("tree/root+type", g, "/api/tree/children", params={"dataset_id": dsid, "types": [a_type]})
    check("tree/root+accessed_after", g, "/api/tree/children",
          params={"dataset_id": dsid, "accessed_after": "2023-01-01"})
    check("tree/root+accessed_before", g, "/api/tree/children",
          params={"dataset_id": dsid, "accessed_before": "2023-01-01"})
    check("tree/root+no_transfer=yes", g, "/api/tree/children", params={"dataset_id": dsid, "no_transfer": "yes"})
    check("tree/root+no_transfer=no", g, "/api/tree/children", params={"dataset_id": dsid, "no_transfer": "no"})
    check("tree/root+processed=yes", g, "/api/tree/children", params={"dataset_id": dsid, "processed": "yes"})
    check("tree/root+processed=no", g, "/api/tree/children", params={"dataset_id": dsid, "processed": "no"})
    check("tree/root+jira=value", g, "/api/tree/children", params={"dataset_id": dsid, "jira": "MIG-100"})
    check("tree/root+jira=none", g, "/api/tree/children", params={"dataset_id": dsid, "jira": "__none__"})
    check("tree/root+assignee=value", g, "/api/tree/children", params={"dataset_id": dsid, "assignee": "alice"})
    check("tree/root+assignee=none", g, "/api/tree/children", params={"dataset_id": dsid, "assignee": "__none__"})

    # ---- Tree navigation, combined filters ----
    check("tree/root+type+date+flag", g, "/api/tree/children", params={
        "dataset_id": dsid, "types": [a_type], "accessed_after": "2020-01-01",
        "accessed_before": "2024-01-01", "no_transfer": "no",
    })
    check("tree/dept+type+processed", g, "/api/tree/children", params={
        "dataset_id": dsid, "parent_id": ids["dept_id"], "types": [a_type], "processed": "no",
    })

    # ---- Flat grid (search), default + every filter individually ----
    check("search/default", g, "/api/nodes/search", params={"dataset_id": dsid})
    check("search/type", g, "/api/nodes/search", params={"dataset_id": dsid, "types": [a_type]})
    check("search/owner", g, "/api/nodes/search", params={"dataset_id": dsid, "owner": "CORP\\jsmith"})
    check("search/is_dir=true", g, "/api/nodes/search", params={"dataset_id": dsid, "is_dir": True})
    check("search/is_dir=false", g, "/api/nodes/search", params={"dataset_id": dsid, "is_dir": False})
    check("search/q realistic", g, "/api/nodes/search", params={"dataset_id": dsid, "q": "Apollo_00017"})
    check("search/q common word", g, "/api/nodes/search", params={"dataset_id": dsid, "q": "Analytics"})
    check("search/no_transfer=yes", g, "/api/nodes/search", params={"dataset_id": dsid, "no_transfer": "yes"})
    check("search/no_transfer=no", g, "/api/nodes/search", params={"dataset_id": dsid, "no_transfer": "no"})
    check("search/processed=yes", g, "/api/nodes/search", params={"dataset_id": dsid, "processed": "yes"})
    check("search/processed=no", g, "/api/nodes/search", params={"dataset_id": dsid, "processed": "no"})
    check("search/jira=value", g, "/api/nodes/search", params={"dataset_id": dsid, "jira": "MIG-100"})
    check("search/jira=none", g, "/api/nodes/search", params={"dataset_id": dsid, "jira": "__none__"})
    check("search/assignee=value", g, "/api/nodes/search", params={"dataset_id": dsid, "assignee": "alice"})
    check("search/assignee=none", g, "/api/nodes/search", params={"dataset_id": dsid, "assignee": "__none__"})
    check("search/accessed range", g, "/api/nodes/search",
          params={"dataset_id": dsid, "accessed_after": "2020-01-01", "accessed_before": "2024-01-01"})
    check("search/under_node_id", g, "/api/nodes/search", params={"dataset_id": dsid, "under_node_id": ids["dept_id"]})

    # ---- Flat grid: combined filters (the realistic "power user" query) ----
    check("search/type+flag+date", g, "/api/nodes/search", params={
        "dataset_id": dsid, "types": [a_type], "no_transfer": "no",
        "accessed_after": "2020-01-01", "accessed_before": "2024-01-01",
    })
    check("search/q+type+assignee", g, "/api/nodes/search", params={
        "dataset_id": dsid, "q": "file_00", "types": [a_type], "assignee": "__none__",
    })

    # ---- Flat grid: every sort column, both directions ----
    for sort_col in ("name", "full_path", "size", "last_modified", "last_accessed", "file_type", "owner", "dir_level"):
        for direction in ("asc", "desc"):
            check(f"search/sort={sort_col}:{direction}", g, "/api/nodes/search",
                  params={"dataset_id": dsid, "sort": sort_col, "direction": direction})

    # ---- Flat grid: pagination (a late page still has to skip past matches) ----
    check("search/page=50", g, "/api/nodes/search", params={"dataset_id": dsid, "page": 50, "page_size": 100})
    check("search/large page_size", g, "/api/nodes/search", params={"dataset_id": dsid, "page_size": 1000})

    # ---- Type breakdown ----
    check("type-breakdown/root", g, f"/api/nodes/{ids['root_id']}/type-breakdown")
    check("type-breakdown/root+filter", g, f"/api/nodes/{ids['root_id']}/type-breakdown",
          params={"no_transfer": "no"})
    check("type-breakdown/dept", g, f"/api/nodes/{ids['dept_id']}/type-breakdown")

    # ---- Counts / stats / node detail ----
    check("counts/root", g, f"/api/nodes/{ids['root_id']}/counts")
    check("stats/root", g, f"/api/nodes/{ids['root_id']}/stats")
    check("stats/root+type", g, f"/api/nodes/{ids['root_id']}/stats", params={"types": [a_type]})
    # The scoped-write "Apply to N" preview: full filter set on the root folder.
    check("stats/root+full-filters", g, f"/api/nodes/{ids['root_id']}/stats", params={
        "types": file_types, "accessed_after": "2020-01-01", "no_transfer": "no",
        "jira": "__none__", "assignee": "__none__",
    })
    check("node/get leaf-folder", g, f"/api/nodes/{ids['leaf_id']}")
    check("node/get dept-folder", g, f"/api/nodes/{ids['dept_id']}")

    # ---- Tree navigation, all filter axes at once ----
    check("tree/root+type+flag+jira+assignee", g, "/api/tree/children", params={
        "dataset_id": dsid, "types": file_types, "no_transfer": "no",
        "jira": "__none__", "assignee": "__none__",
    })

    # ---- Type-counts (bulk folder query) ----
    check("type-counts/bulk", p, "/api/nodes/type-counts",
          json={"node_ids": [ids["root_id"], ids["dept_id"], ids["project_id"], ids["leaf_id"]], "types": [a_type]})

    # ---- Filter-dropdown support endpoints ----
    check("file-types", g, f"/api/datasets/{dsid}/file-types")
    check("distinct/jira_ticket", g, f"/api/datasets/{dsid}/distinct/jira_ticket")
    check("distinct/assignee", g, f"/api/datasets/{dsid}/distinct/assignee")

    # ---- Report + assert ----
    width = max(len(r[0]) for r in results)
    over = [r for r in results if r[1] > BUDGET_SECONDS]
    print(f"\n[perf] {len(results)} queries, budget={BUDGET_SECONDS}s, "
          f"{len(over)} over budget, worst={max(r[1] for r in results):.3f}s")
    for label, elapsed, _ in sorted(results, key=lambda r: -r[1]):
        flag = " OVER BUDGET" if elapsed > BUDGET_SECONDS else ""
        print(f"  {elapsed:7.3f}s  {label:<{width}}{flag}")

    assert not over, (
        f"{len(over)}/{len(results)} queries exceeded the {BUDGET_SECONDS}s budget: "
        + ", ".join(f"{label}={elapsed:.2f}s" for label, elapsed, _ in over)
    )


# Bulk writes get a looser budget than reads: a write is a deliberate,
# occasional action (reads happen on every click), and it pays for the
# annotation upsert + the scoped effective-column refresh.
WRITE_BUDGET_SECONDS = 10.0
# One deliberate exception: a substring that scatters ~100k matches across the
# entire tree (no bounding folder) is the accepted worst case -- measured
# ~13-15s on the reference environment and signed off as acceptable. The 2x
# budget pins it against *regression* without failing on accepted behavior.
SCATTERED_100K_BUDGET_SECONDS = 2 * WRITE_BUDGET_SECONDS


def test_write_scenarios_under_budget(perf_client):
    """Bulk-write latency for the documented user workflows, including the
    filter-scoped paths (flag/JIRA/assignee filters on folder-scoped writes).

    Runs after the read test (module fixture is shared), so it may mutate the
    dataset freely.
    """
    client, dataset, ids = perf_client
    dsid = dataset["id"]

    file_types = [r["file_type"] for r in client.get(
        f"/api/datasets/{dsid}/file-types").json()][:10]

    # A second, so-far-untouched department for the eff-filter folder flag.
    depts = client.get(
        "/api/tree/children", params={"dataset_id": dsid, "parent_id": ids["root_id"]},
    ).json()["children"]
    dept2_id = next(d["id"] for d in depts if d["is_dir"] and d["id"] != ids["dept_id"])

    results: list[tuple[str, float, int, float]] = []

    def check(label, method, path, *, budget=WRITE_BUDGET_SECONDS, **kwargs):
        t0 = time.time()
        resp = method(path, **kwargs)
        elapsed = time.time() - t0
        assert resp.status_code == 200, f"{label}: {resp.status_code} {resp.text[:300]}"
        results.append((label, elapsed, resp.status_code, budget))
        return resp

    p = client.post

    # 1. Grid: common path substring scatters matches across the whole tree,
    #    select-all -> set flag (the accepted pathological worst case).
    r = check(
        "grid/q-scattered ~100k select-all set flag", p, "/api/nodes/bulk-by-filter",
        budget=SCATTERED_100K_BUDGET_SECONDS,
        json={"dataset_id": dsid, "q": "2021_", "files_only": True,
              "values": {"no_transfer": True}},
    )
    assert r.json()["updated"] > 50_000  # sanity: this really is the big case

    # 2. Grid: many file types simultaneously, select-all -> set assignee.
    r = check(
        "grid/10-types select-all set assignee", p, "/api/nodes/bulk-by-filter",
        json={"dataset_id": dsid, "types": file_types, "files_only": True,
              "values": {"assignee": "bob"}},
    )
    assert r.json()["updated"] > 10_000

    # 3. Tree: folder + type filter + jira=__none__ -> bulk JIRA stamp (the
    #    new combined effective-value scoping path).
    check(
        "tree/dept type+jira-none bulk JIRA stamp", p, "/api/nodes/bulk-annotation",
        json={"node_id": ids["dept_id"], "files_only": True,
              "types": file_types[:5], "jira": "__none__",
              "values": {"jira_ticket": "MIG-900"}},
    )

    # 4. Tree: folder-flag scoped by an effective-value filter over a full
    #    (~40k-file) department subtree.
    r = check(
        "tree/dept2 folder-flag scoped by no_transfer=no", p,
        f"/api/nodes/{dept2_id}/folder-flag",
        json={"field": "no_transfer", "value": True, "no_transfer": "no"},
    )
    rep = r.json()
    assert rep["own"]["no_transfer"] is None  # scoped path
    assert rep["no_transfer_marked"] == rep["total_files"]

    # 5. Grid: scoped CLEAR over the multi-type set -- exercises the slower
    #    COALESCE-with-parent refresh path (clearing can't use the direct-
    #    literal fast path).
    check(
        "grid/10-types select-all CLEAR assignee", p, "/api/nodes/bulk-by-filter",
        json={"dataset_id": dsid, "types": file_types, "files_only": True,
              "values": {"assignee": None}},
    )

    # 6. Tree: scoped clear via folder-flag with a JIRA filter.
    check(
        "tree/dept folder-flag CLEAR scoped by jira", p,
        f"/api/nodes/{ids['dept_id']}/folder-flag",
        json={"field": "no_transfer", "value": None, "jira": "MIG-900"},
    )

    # ---- Report + assert ----
    width = max(len(r[0]) for r in results)
    over = [r for r in results if r[1] > r[3]]
    print(f"\n[perf] {len(results)} write scenarios, budget={WRITE_BUDGET_SECONDS}s "
          f"({SCATTERED_100K_BUDGET_SECONDS}s for the scattered-100k case), "
          f"{len(over)} over budget")
    for label, elapsed, _, budget in sorted(results, key=lambda r: -r[1]):
        flag = " OVER BUDGET" if elapsed > budget else ""
        print(f"  {elapsed:7.3f}s  (budget {budget:4.0f}s)  {label:<{width}}{flag}")

    assert not over, (
        f"{len(over)}/{len(results)} write scenarios exceeded budget: "
        + ", ".join(f"{label}={elapsed:.2f}s>{budget:.0f}s" for label, elapsed, _, budget in over)
    )
