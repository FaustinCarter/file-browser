# Plan: Resolve effective annotation values with recursive SQL

> **Status update:** Strategy A below (per-request recursive CTE) is
> implemented and remains the correctness reference (`services.effective_cte`).
> At real scale (~2M rows) it turned out too slow to run on every *read* as
> planned — every tree/grid request paid a full-dataset walk regardless of
> whether a flag/jira/assignee filter was active, since the folder rollups
> need it unconditionally for their marked counts. Strategy B (materialized
> `<field>_eff` columns, §4) has since been adopted for exactly the fields
> that are filtered on, refreshed at write time for the affected subtree only
> (not the whole dataset, which the "risk" callout under Strategy B below
> assumed — see `services.refresh_effective_for_*` and the README's
> "Performance at millions of rows" section). Reads now filter/rollup on
> those columns directly; §5.1's CTE is used only by the refresh and by
> `resolve_effective`'s equivalence test. The rest of this document is kept
> as the historical design record and invariant list, which still holds.

## 1. Why

Effective-value resolution (own value, else nearest-ancestor folder value) is
currently expressed as a **boolean SQL expression built in Python** from the set
of folder overrides for a field (`services.effective_equals_clause` and
friends). The generated clause is a materialized-path region `OR`
(`mat_path LIKE '/1/5/23/%' AND NOT LIKE any(child-prefixes) ...`).

This has been patched twice for scale:

- Own (file) values collapsed into a subquery `IN` so flagging 145k files
  doesn't emit 145k clauses (commit `96653f0`).
- Folder-prefix regions collapsed into a single `mat_path LIKE ANY(ARRAY[...])`
  bind parameter so the SQL text stays small with many folder overrides
  (`_like_any`, commit `3ac8ae3`).

The SQL is now small, but **execution is still O(overrides) and index-hostile**:
`LIKE ANY(array)` cannot use the `text_pattern_ops` prefix index the way a single
`LIKE 'const%'` can, so a dataset with tens of thousands of *folder* overrides
still evaluates slowly. Today we dodge this by only ever creating *file*
overrides on the mass path (`files_only`), but that is a workaround, not a fix:
any legitimate large set of folder overrides (many single-folder toggles over
time, or an explicit "Folders" bulk) reintroduces the slowdown.

A recursive CTE walks the tree **once** by `parent_id`, propagating each node's
inherited value to its children, and produces an effective value for **every**
node in `O(nodes)` — independent of how many overrides exist. Benchmarked at
~0.29s over the full dataset. This document is the plan to adopt it without
regressing any existing behavior.

## 2. How effective values are consumed today (the constraint that shapes everything)

There are **three** distinct consumers. Any replacement must serve all three.

| # | Consumer | Code | Shape needed |
|---|----------|------|--------------|
| C1 | Grid/tree **filtering** over the whole dataset | `grid_filter_conds` → `build_filters` → `effective_equals_clause` / `effective_isnull_clause` / `effective_true_clause`; used by `search`, `bulk-by-filter`, tree `children`, `type_breakdown` | A `WHERE` predicate over `nodes` |
| C2 | Folder **tri-state rollups** | `folder_metrics` runs, **per folder**, one aggregate over descendant files: `count(*) FILTER (nt_clause)`, `... FILTER (proc_clause)` | An expression usable **inside an aggregate `FILTER`** over descendant files |
| C3 | Per-node **display** (own vs inherited, which ancestor supplied the value, audit) | `resolve_effective` → `build_node_outs` | Per-node effective + **source node id** + own values, for the ~page of visible nodes only |

**Key scoping decision:** C3 (`resolve_effective`) is **not** the scalability
problem. It resolves values only for the handful-to-page of nodes actually being
rendered, using a cheap ancestor-id split of `mat_path`. It also computes the
*source* (which ancestor supplied each value) that the UI needs to distinguish
inherited from own. **Leave `resolve_effective` and the entire write path
untouched.** The recursive rewrite targets **only C1 and C2**, which today
re-derive a full-dataset region clause on every request.

## 3. Goals / non-goals

**Goals**
- Effective-value **filtering** and **folder rollups** run in time that depends
  on dataset size, not on override count.
- No behavioral change: every invariant in §6 holds identically before/after.
- No data migration required to ship (see §4 — per-request CTE needs no schema
  change).

**Non-goals (explicitly out of scope for this change)**
- Changing write semantics (folder-flag, bulk, single-edit, clear) — untouched.
- Changing the per-node display/source-attribution path (`resolve_effective`).
- Changing the read-only CSV columns or the `Annotation`/`Node` split.
- Introducing `ltree` or any Postgres extension (stay portable; recursion uses
  only standard SQL + existing `parent_id` index).

## 4. Chosen approach: per-request recursive CTE (Strategy A)

### Strategy A — Per-request recursive CTE (RECOMMENDED)

Each request that needs effective values includes a `WITH RECURSIVE eff AS (...)`
that resolves all inheritable fields for the dataset in one tree-walk, and the
query **joins**/filters `nodes` against it.

- **Pro:** No schema change, no stored state, **nothing to keep in sync** — so
  the class of bugs where a materialized value drifts from the truth *cannot
  occur*. This is the single biggest regression-risk reducer.
- **Pro:** Always correct by construction; the CTE is the definition of
  inheritance.
- **Con:** The CTE is recomputed per request. Acceptable (~0.29s full-dataset),
  and mitigated by scoping (§5.4) and by the rollup refactor (§5.3) that shares
  one CTE evaluation across all folders on a page.

### Strategy B — Materialized effective columns (REJECTED for now)

Add `no_transfer_eff`, `processed_eff`, `jira_ticket_eff`, `assignee_eff`, …
columns (or a sidecar table), populate at import via the CTE, and **recompute the
affected subtree on every annotation edit**.

- **Pro:** Reads become trivial indexed lookups; rollups become
  `count(*) FILTER (WHERE no_transfer_eff)`.
- **Con:** Every write path (folder-flag clear-then-set, file bulk, single edit,
  `clear_field_under`) must correctly recompute the affected subtree, in the
  right order, inside the same transaction. **This is exactly the kind of
  maintenance coupling that causes feature regressions** — the thing this task
  is meant to avoid. Also needs a migration + backfill.

Ship Strategy A. Revisit B only if per-request CTE latency becomes the
bottleneck at real scale; A's helpers are structured so B could later back them
without touching consumers.

## 5. Design detail (Strategy A)

### 5.1 The recursive CTE

One walk resolves **all** inheritable fields, matching the "resolve everything at
once" nature of `resolve_effective`. Sketch (PostgreSQL):

```sql
WITH RECURSIVE eff AS (
    -- Seed: dataset roots (parent_id IS NULL). Their effective value is their
    -- own value (no ancestor above them).
    SELECT
        n.id,
        n.mat_path,
        NULLIF(a.no_transfer::text, '')::boolean       AS no_transfer_eff, -- bools: NULLIF is a no-op guard
        a.processed                                     AS processed_eff,
        NULLIF(a.target_location, '')                   AS target_location_eff,
        NULLIF(a.jira_ticket, '')                       AS jira_ticket_eff,
        NULLIF(a.comment, '')                           AS comment_eff,
        NULLIF(a.assignee, '')                          AS assignee_eff
    FROM nodes n
    LEFT JOIN annotations a ON a.node_id = n.id
    WHERE n.dataset_id = :ds AND n.parent_id IS NULL

    UNION ALL

    -- Step: a child inherits the parent's effective value unless it sets its own.
    SELECT
        c.id,
        c.mat_path,
        COALESCE(ca.no_transfer, e.no_transfer_eff),
        COALESCE(ca.processed,   e.processed_eff),
        COALESCE(NULLIF(ca.target_location, ''), e.target_location_eff),
        COALESCE(NULLIF(ca.jira_ticket, ''),     e.jira_ticket_eff),
        COALESCE(NULLIF(ca.comment, ''),         e.comment_eff),
        COALESCE(NULLIF(ca.assignee, ''),        e.assignee_eff)
    FROM eff e
    JOIN nodes c ON c.parent_id = e.id AND c.dataset_id = :ds
    LEFT JOIN annotations ca ON ca.node_id = c.id
)
SELECT id, no_transfer_eff, processed_eff, jira_ticket_eff, assignee_eff FROM eff
```

Critical details (each maps to an invariant in §6):

- **`COALESCE(own, inherited)`** encodes "own wins, else nearest ancestor" — the
  recursion carries the *nearest* ancestor value down, so the first non-null
  ancestor naturally wins (I1).
- **`NULLIF(text, '')`** normalizes empty-string text to NULL so `''` counts as
  "no value" exactly like `resolve_effective` does (`v != ""`) (I2).
- **Files are leaves** — a file has no children, so its own value never
  propagates. No special-casing needed; the walk enforces it (I3).
- **Per-dataset seed and step** (`dataset_id = :ds` in both anchor and recursive
  term) so inheritance never crosses datasets (I20).
- Recursion is by **`parent_id`**, backed by `ix_nodes_parent`; consistent with
  `mat_path` because `mat_path` is exactly the `parent_id` chain (I19).

### 5.2 New service helpers (replacing the region-clause machinery)

Delete/replace: `_like_any`, `_direct_children`, `_folder_overrides`,
`_own_ids`, `_folder_region_clause`, `effective_equals_clause`,
`effective_isnull_clause`, `effective_true_clause`.

Introduce:

```python
def effective_cte(dataset_id: int):
    """Return the recursive CTE (columns: id + <field>_eff for each field)."""

def effective_is(cte, field: str, value) -> ColumnElement:      # eff col == value
def effective_is_true(cte, field: str) -> ColumnElement:         # bool flag true
def effective_is_null(cte, field: str) -> ColumnElement:         # no effective value
```

C1 (filtering) becomes membership against the CTE. Two equivalent renderings; pick
per call site:

- **Semi-join** for top-level `WHERE`:
  `Node.id.in_(select(cte.c.id).where(effective_is_true(cte, "no_transfer")))`
- **Join** when the query already joins the CTE (rollups, §5.3): reference
  `cte.c.no_transfer_eff` directly.

Hide-filter ("no") stays `not_(...)` semantics but NULL-safe:
`Node.id.notin_(select(cte.c.id).where(cte.c.no_transfer_eff.is_(True)))`.
The subquery yields only non-null ids, so `NOT IN` is safe, and "not marked"
correctly includes both effective-false **and** effective-null (I8).

`build_filters` / `grid_filter_conds` keep their **signatures and return shape**
(`view_filter`, `nt_clause`, `proc_clause`, `filter_active`) so their consumers
(`search`, `bulk-by-filter`, `type_breakdown`, tree `children`) don't change —
only the internals swap region clauses for CTE membership. `nt_clause`/`proc_clause`
returned for rollups become predicates over the joined CTE (see §5.3).

### 5.3 Folder rollups — fix the per-folder query (C2)

Today `build_node_outs` loops and calls `folder_metrics` **once per directory**
(up to ~1000 aggregate queries for a full grid page — already an N+1). Embedding a
recursive CTE inside a per-folder aggregate would recompute the walk N times. So
this refactor **must** batch:

- Add `folder_metrics_bulk(db, folder_nodes, *, view_filter_spec, dataset_id)`
  that computes, in **one** statement, for all page folders at once:
  `total_files`, `filtered_file_count`, `filtered_total_size`,
  `no_transfer_marked`, `processed_marked`.

```sql
WITH RECURSIVE eff AS (...)          -- referenced once, materialized once by PG
SELECT f.id AS folder_id,
       count(*) FILTER (WHERE d.is_dir = false)                              AS total_files,
       count(*) FILTER (WHERE d.is_dir = false AND <view_filter over d,e>)   AS filtered_file_count,
       coalesce(sum(d.size_bytes) FILTER (WHERE d.is_dir = false AND <view_filter>), 0) AS filtered_total_size,
       count(*) FILTER (WHERE d.is_dir = false AND e.no_transfer_eff)        AS no_transfer_marked,
       count(*) FILTER (WHERE d.is_dir = false AND e.processed_eff)          AS processed_marked
FROM nodes f
JOIN nodes d ON d.dataset_id = f.dataset_id
            AND d.mat_path LIKE f.mat_path || '%'
            AND d.id <> f.id
JOIN eff  e ON e.id = d.id
WHERE f.id = ANY(:folder_ids)
GROUP BY f.id
```

- `build_node_outs` changes from "loop calling `folder_metrics`" to "collect the
  directory ids, call `folder_metrics_bulk` once, map results back by id." Files
  in the page get no rollup (unchanged).
- The tri-state thresholds stay in the frontend `folderFlagState`
  (empty/none/some/all) and are unchanged; the backend still returns
  `total_files` + `*_marked` computed over **descendant files, excluding self**,
  using **effective** values (I5, I6, I7).
- `view_filter` must be renderable against the descendant alias `d` + CTE alias
  `e`. Change `build_filters` to return the view filter as a **spec/callable**
  `view_filter(node_alias, eff_alias) -> predicate` (or a small dataclass the
  helpers can bind to an alias), rather than a clause pre-bound to the bare
  `Node` mapper. Single-node/serializer call sites bind it to `Node` + the CTE;
  the bulk rollup binds it to `d` + `e`. This keeps **one** definition of the
  filter for search, rollups, and breakdown (no drift — the bug class fixed when
  `grid_filter_conds` was first shared).

### 5.4 Optional scoping (perf, not correctness)

When a query is already restricted to a subtree (`under_node_id`) or the rollup
only concerns a page of folders, the CTE can be seeded at the subtree root(s)
**carrying the root's inherited value** (resolved once via the cheap
ancestor-split for that single path) and walk only that subtree. This is an
optimization to add if profiling shows the full-dataset walk dominates; it does
**not** change results. Ship without it first; measure.

## 6. Invariants — MUST hold identically after the change

These are the behavioral contracts accumulated across the project. The rewrite
must preserve every one. Each has a test in §8.

**Inheritance semantics**
- **I1** Effective value = own value if set, else the nearest ancestor folder
  with a value, else NULL. Nearer ancestor beats farther; self beats all.
- **I2** For **text** fields, empty string `''` is treated as "no value" (same as
  NULL) for inheritance and for the UNASSIGNED filter. Booleans have no empty
  case.
- **I3** Only folders are ancestors; a **file's** own override affects **only
  itself** and never propagates.
- **I4** Setting a folder's own value affects all descendants without a nearer
  override; clearing to NULL makes them inherit again. (Write path; unchanged,
  but effective reads must reflect it.)

**Folder rollups / tri-state**
- **I5** Folder tri-state derives from **descendant files only**: states are
  `empty` (0 files), `none` (0 marked), `some` (0<marked<total), `all`
  (marked==total). Thresholds live in `folderFlagState` and don't change.
- **I6** Rollups count descendant **files**, excluding the folder itself and
  excluding sub**folders**.
- **I7** The "marked" count uses the **effective** value (a file under a marked
  folder counts even with no own value).

**Filtering**
- **I8** Flag filters are tri-state: absent = any; `"yes"` = effectively marked;
  `"no"` = **not** effectively marked (effective false **or** null). `"no"` must
  remain NULL-safe.
- **I9** `jira`/`assignee` value filter: a concrete value matches
  `effective == value`; the `__none__` (UNASSIGNED) sentinel matches
  `effective IS NULL` (no own **and** none inherited).
- **I10** `types` / `last_accessed` filters match the node's **own raw CSV
  columns**, never inherited.
- **I11** All active filters compose with `AND`.
- **I12** The info-bar **type-breakdown** and a folder's **filtered_file_count /
  filtered_total_size** respect the active filters (prior bug fix) and stay
  consistent with the grid/rollup — one shared filter definition.

**Scoped folder edits (write behavior — must not regress)**
- **I13** Folder flag **with** a type/last-accessed filter touches only matching
  descendant **files** (`files_only`) and leaves the folder's own value untouched
  (folder shows indeterminate). **Without** a filter it clears all descendant
  overrides for the field, then sets the folder's own value.
- **I17** `clear_field_under` only nulls fields on **existing** annotation rows
  (never creates a row to store NULL).

**Scale**
- **I14** Grid "select all → toggle flag" still applies to **files only**
  (`files_only`) — no write amplification into per-folder overrides. (Recursion
  makes folder overrides cheap to *read*, but we keep files-only to avoid mass
  writes.)
- **I15** Reads after flagging very large sets (e.g. 145k) must stay bounded in
  SQL size **and** execution time. The CTE is `O(nodes)`, independent of override
  count — this is the property being bought.

**Audit / read-only**
- **I16** Every annotation write stamps `updated_by` (from `X-Actor`) and
  `updated_at` (server `now()`). Reads expose the node's **own** audit fields
  (NULL until first touched), never an inherited audit value.
- **I18** Original CSV columns on `Node` are never mutated by any endpoint.

**Structure / isolation**
- **I19** `mat_path` is `/id/…/` with trailing slash; descendant = `LIKE
  'matpath%'`; ancestors via split. Recursion by `parent_id` must agree with
  this.
- **I20** All effective-value computation is scoped by `dataset_id`; inheritance
  never crosses datasets.

**Display**
- **I21** `NodeOut.inherited_fields`, `own`, and `effective` still distinguish an
  inherited value from an own one, with the correct source node. (Served by the
  untouched `resolve_effective`; the CTE must agree with it — see the equivalence
  test I-EQ.)

## 7. Rollout plan (low-regression sequencing)

1. **Add** `effective_cte` + `effective_is/_is_true/_is_null` helpers alongside
   the existing region-clause functions. No consumer changes yet.
2. **Golden equivalence test (I-EQ):** for randomly generated trees (mixed
   folders/files, multiple roots) and random overrides (own + folder, including
   empty-string text and deep nesting), assert the CTE's per-node effective
   values equal `resolve_effective`'s for **every** node and **every** field.
   This single test guards I1–I3, I7, I9, I21 at once. Keep it permanently.
3. **Switch C1** (`build_filters`/`grid_filter_conds` internals) to the CTE
   helpers. Run the full existing suite (search/tree/bulk/type-breakdown tests)
   unchanged — they encode I8–I12.
4. **Refactor C2**: add `folder_metrics_bulk`, convert `build_node_outs` to the
   batched call, thread `view_filter` as an alias-bindable spec. Assert rollup
   counts (I5–I7, I12) and confirm the grid page issues **one** rollup query, not
   N.
5. **Optional gate:** put the engine behind `EFFECTIVE_ENGINE=recursive|region`
   (default `recursive`) for one release so a bad surprise can be reverted by env
   without a redeploy, and so a test can run the suite under both and diff. Remove
   the old `region` path (and dead helpers `_like_any`, `_folder_region_clause`,
   `_direct_children`, `_folder_overrides`, `_own_ids`) once `recursive` has
   soaked.
6. **Benchmark** the two scale scenarios from prior work and record numbers in
   the PR: (a) 145k file overrides, (b) 10k folder overrides. Both filter +
   rollup should be sub-second and flat in override count.

No schema migration, no `_init_schema` change, no new index required
(`ix_nodes_parent` and the annotation flag indexes already exist). If the
recursive step benefits from it, consider adding `ix_nodes_parent_dataset` on
`(dataset_id, parent_id)` as an idempotent `CREATE INDEX IF NOT EXISTS` in
`_init_schema` — measure first.

## 8. Test checklist (invariant → test)

| Invariant | Test |
|-----------|------|
| I1, I2, I3, I7, I9, I21 | **I-EQ** property test: CTE effective == `resolve_effective` for random trees/overrides (incl. empty-string text, deep nesting, file-own-override-doesn't-propagate) |
| I5, I6, I7 | Folder with mixed marked/unmarked descendant files → `total_files` and `*_marked` yield empty/none/some/all correctly; subfolders and self excluded |
| I8 | `no_transfer=yes` returns only marked; `=no` returns effective-false **and** effective-null (NULL-safe) |
| I9 | `jira=<value>` matches inheritors; `jira=__none__` matches nodes with no effective jira |
| I10, I11 | type + last_accessed + flag filters compose (AND) and match own raw columns |
| I12 | type-breakdown and `filtered_file_count` equal the grid count under the same filters |
| I13, I17 | Existing folder-flag scoped/unscoped write tests still pass (write path untouched) |
| I14 | `bulk-by-filter` grid select-all still marks files, not folders (existing test) |
| I15 | Effective clause/query **bounded**: existing "many file overrides" + "many folder overrides" tests, now asserting flat execution (no `LIKE ANY` growth) at 10k folders |
| I16 | Audit stamped on write; effective read never surfaces an inherited audit value |
| I20 | Two datasets with identical paths don't leak inheritance across each other |

Also assert (regression on the N+1 that motivated the refactor): rendering a grid
page of K folders issues exactly **one** rollup query.

## 9. Risks & mitigations

- **CTE recomputed per request.** Mitigate via §5.3 (share one evaluation across
  a page's rollups) and §5.4 (optional subtree scoping). Measured baseline
  ~0.29s full-dataset; acceptable.
- **`view_filter` re-plumbing** (alias-bindable spec) touches search, rollups,
  and breakdown. Mitigate by keeping `build_filters`' return keys stable and
  covering with the existing shared-filter tests before/after.
- **NULL/empty-string mismatch** between SQL `COALESCE` and Python `!= ""`.
  Mitigate with `NULLIF(text,'')` in the CTE and the I-EQ equivalence test as the
  gate.
- **`NOT IN` NULL trap** on hide-filters. Mitigate: the subquery selects only
  non-null ids; covered by the I8 test.
- **Per-folder N+1 regression** if batching is skipped. Mitigate: the
  "one rollup query per page" assertion in §8.

## 10. Decisions to confirm before implementing

1. **Ship Strategy A** (per-request CTE, no migration) rather than B (materialized
   columns)? Recommended: yes.
2. Keep the temporary **`EFFECTIVE_ENGINE` gate** for one release, or cut straight
   over behind the equivalence test? (Gate = safer rollback; cut-over = less code.)
3. Add the optional **`(dataset_id, parent_id)` index** now or only if profiling
   demands it?
