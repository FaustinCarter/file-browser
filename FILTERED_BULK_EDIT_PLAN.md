# Plan: Filter-scoped bulk edits for every flag and annotation

> **Status: implemented.** All gaps G1–G7 are closed; functional tests cover
> §4.1 and the perf suite gained the §4.2 write scenarios (see
> `backend/tests/test_performance.py::test_write_scenarios_under_budget`).
> This document remains as the design record.

## 1. Goal

Any filter the frontend can express — file type(s), last-accessed range,
No-Transfer/Processed tri-state, JIRA ticket (incl. "No ticket"), Assignee
(incl. "Unassigned") — must be usable to **scope a bulk write** of any editable
value (No-Transfer, Processed, Assignee, JIRA ticket, Target location,
Comment), in both views:

- **Tree view:** filter (e.g. by file types, or by JIRA ticket, or both),
  select a folder, and apply/clear a flag or annotation to exactly the
  matching files in that subtree.
- **Table view:** filter (e.g. by several file types at once, or by a flag),
  select-all matching, and apply/clear a flag or annotation to every match.

Driving examples from the request:

1. Select a large group of files by file type and add a JIRA ticket to every
   file of those types **in a subtree** (tree view).
2. Filter by JIRA ticket, then add or remove files from that ticket **further
   narrowed by a type filter** (either view).

Performance constraint: no regression to the existing budgets — all reads
< 3 s, bulk writes ≲ 10 s, on the ≥ 2M-row reference dataset
(`backend/tests/test_performance.py`).

## 2. Audit of current functionality

### 2.1 Backend endpoints

| Endpoint | Purpose | Scoping filters accepted today |
|---|---|---|
| `GET /api/nodes/search` | grid page | `q`, `types`, `owner`, `is_dir`, `accessed_*`, `no_transfer`, `processed`, `jira`, `assignee`, `under_node_id` — **complete** |
| `POST /api/nodes/bulk-by-filter` | grid "select all matching" write | same full set as `search` — **complete** |
| `GET /api/tree/children` | tree level + rollups | full filter set — **complete** |
| `POST /api/nodes/{id}/folder-flag` | folder flag set/clear (scoped or whole-subtree) | `types`, `accessed_after`, `accessed_before` only — **gap** |
| `POST /api/nodes/bulk-annotation` | folder-scoped bulk stamp | `types`, `accessed_after`, `accessed_before` only — **gap** |
| `GET /api/nodes/{id}/stats` | "will touch N files" preview count | `types`, `accessed_after`, `accessed_before` only — **gap** |

Internally, `folder-flag` and `bulk-annotation` scope through
`services.bulk_set_under` / `services.clear_field_under`, whose WHERE is built
by `_apply_file_filters` (types + dates only). The full-filter predicate
builder already exists — `services.ViewFilter` covers all seven filter axes
against the indexed materialized `<field>_eff` columns — it just isn't wired
into these two write paths (or into `folder_stats`).

### 2.2 Tree view (FilterBar + TreeView + DetailPanel)

- **FilterBar** exposes the full filter set: multi-type, accessed range, both
  flags, assignee, JIRA. ✔
- **TreeView** passes all filters to `/tree/children`; visible rows, folder
  file-counts and sizes all respect every filter. ✔
- **DetailPanel** is where the gap lives. Its `filterActive` check
  (`DetailPanel.tsx:41`) considers **only `types`/`accessed_*`**:
  - `setFolderFlag` forwards only `types`/`accessed_*` to `folder-flag`.
  - `applyScoped` (the "filter active: matching files only" edit) forwards
    only `types`/`accessed_*` to `bulk-annotation`.
  - `BulkStamp`'s "Respect active filters" forwards only `types`/`accessed_*`.
  - The "Apply to N" preview (`api.stats`) counts with only `types`/`accessed_*`.
  - `ScopedFolderEdit` offers Target location / JIRA / Comment but **omits
    Assignee** (the unscoped editor has it).

  **Consequence — a live correctness hazard, not just a missing feature:** with
  *only* a flag/JIRA/assignee filter active (say `jira=MIG-100`), the tree
  shows a filtered subset, but `filterActive` is false, so checking a folder's
  No-Transfer box silently runs the **unscoped** path: it wipes every
  descendant override and stamps the folder — including files the user had
  filtered out of view.

### 2.3 Table view (GridView)

- **Filters exposed:** `q`, kind (`is_dir`), both flags, assignee, JIRA. ✔ for
  those — but **no file-type filter** and **no accessed-date range**, even
  though `/search` and `/bulk-by-filter` already accept both. The request's
  scenario "filter by multiple file types simultaneously and mark them all"
  is therefore impossible in table view today purely for lack of UI.
- **Bulk bar values:** No-Transfer ✓/✗, Processed ✓/✗, Assignee, JIRA, Target
  location — **Comment missing** (it is editable per-row and via tree bulk
  stamp, so "every possible annotation" requires adding it).
- "Select all N matching" → `bulk-by-filter` correctly reuses the exact
  filter params, `files_only` semantics prevent folder-override bloat. ✔
- `api.ts`'s `bulkByFilter` payload type omits `types`/`accessed_*`/`owner`/
  `under_node_id` (backend accepts them; the TS type just never grew).

### 2.4 Gap summary

| # | Gap | Layer |
|---|---|---|
| G1 | `folder-flag`, `bulk-annotation`, `stats` don't accept flag/JIRA/assignee filters | backend |
| G2 | `bulk_set_under` / `clear_field_under` / `folder_stats` build WHERE from types+dates only | backend |
| G3 | DetailPanel treats flag/JIRA/assignee filters as "no filter" → unscoped writes while the view is filtered | frontend (hazard) |
| G4 | Scoped folder editor omits Assignee | frontend |
| G5 | Grid has no file-type or accessed-date filter UI | frontend |
| G6 | Grid bulk bar omits Comment | frontend |
| G7 | `api.ts` types lag the backend contract | frontend |

## 3. Design

### 3.1 Semantics: one uniform rule

> **In tree view, if *any* filter is active, a folder edit is scoped: it
> writes own-values onto exactly the descendant *files* matching *all* active
> filters (AND), and leaves the folder's own value untouched.** With no filter
> active, the whole-subtree behavior is unchanged (clear descendants, set
> folder's own inheritable value).

This generalizes the existing types/dates rule (invariant **I13** in
`RECURSIVE_SQL_PLAN.md`) to all filter axes rather than inventing a second
rule. Notes:

- Effective-value filters match the **effective** (own-or-inherited) value via
  the materialized `<field>_eff` columns — identical semantics to how the
  same filters behave in `search`/`tree/children`. The `__none__` sentinel
  (Unassigned / No ticket) means `<field>_eff IS NULL`.
- Filters are evaluated against the **pre-write** state in the same statement/
  transaction, so "filter `no_transfer=no`, set `no_transfer=true`" marks
  exactly the currently-unmarked matching files — the natural reading.
- Writing a value makes rows fall out of a now-mismatched filter on the next
  refetch; that is expected and already how the grid behaves.
- Table view needs **no semantic change** — `bulk-by-filter` already applies
  values to exactly the filtered matches; we only add missing filter UI.

### 3.2 Backend changes (G1, G2)

1. **Schemas** (`app/schemas.py`): add `no_transfer: str | None`,
   `processed: str | None`, `jira: str | None`, `assignee: str | None` to
   `FolderFlagUpdate` and `BulkAnnotationUpdate` (same tri-state / sentinel
   contract as the search endpoint).
2. **Services** (`app/services.py`): replace the `types`/`accessed_*`
   parameter plumbing of `bulk_set_under`, `clear_field_under`, and
   `folder_stats` with an optional `view_filter: ViewFilter` (or add the four
   new kwargs and build one internally — prefer passing a `ViewFilter` so
   there is exactly **one** filter definition shared by reads and writes, the
   same no-drift argument that motivated `ViewFilter` in the first place).
   The WHERE becomes: `dataset_id = :ds AND mat_path LIKE :prefix ||'%'
   AND is_dir = false AND <view_filter.build(Node)>`.
   - `mat_path` prefix here is a **Python-constant** string, so the planner's
     `LIKE 'const%'` → index-range rewrite applies; the eff-filter predicates
     hit the existing `(dataset_id, <field>_eff)` indexes. No new indexes.
3. **Routers** (`app/routers/nodes.py`):
   - `folder_flag`: `scoped = bool(types or accessed_* or no_transfer or
     processed or jira or assignee)`; pass the `ViewFilter` through. The
     effective-column refresh calls are **unchanged**: scoped path keeps
     `refresh_effective_for_subtree(..., fields=(field,))` (no `own_values` —
     the folder's own annotation is untouched, preserving the fix that
     prevents stomping its indeterminate state); unscoped path unchanged.
   - `bulk_annotation`: same pass-through; `own_touched` logic unchanged.
   - `stats`: accept the four new query params for the preview count.
4. **No schema migration, no new indexes, no new dependencies** → nothing to
   change in `Dockerfile`, `docker-compose.yml`, or `scripts/build-offline.sh`
   (the air-gap bundle is rebuilt from the same images; the frontend is baked
   into the web image by the existing build).

### 3.3 Frontend changes (G3–G7)

1. **`api.ts`**: extend `folderFlag`, `bulkAnnotation`, and `stats` payload
   types with the four filter fields; extend `bulkByFilter`'s type with the
   already-supported `types` / `accessed_after` / `accessed_before`.
2. **DetailPanel**:
   - `filterActive` = any of `types`, `accessed_*`, `no_transfer`,
     `processed`, `jira`, `assignee`.
   - `setFolderFlag`, `applyScoped`, `BulkStamp`, and the `api.stats` preview
     forward **all** active filters.
   - Add **Assignee** to `ScopedFolderEdit` (G4).
   - Copy tweak: the "filter active: matching files only" banner should list
     which filters are active (e.g. "3 types, JIRA = MIG-100") so the blast
     radius is explicit before an apply.
3. **GridView**:
   - Add a multi-select **file-type filter** (reuse FilterBar's searchable
     dropdown; extract it into a shared component rather than duplicating)
     and **accessed after/before** date inputs; include them in
     `filterParams()` so paging, select-all, and `bulk-by-filter` all agree
     (G5).
   - Add **Comment** to the bulk bar (G6).

### 3.4 Explicitly out of scope

- `owner` / `under_node_id` filter UI in the grid (backend-ready; nobody asked).
- The unused `POST /api/nodes/type-counts` endpoint stays as-is.
- Optimization note (pre-existing, not a regression): clearing a value via
  `bulk-by-filter` upserts annotation rows even where none existed (a NULL row
  ≡ no row). Harmless semantically; if write volume ever matters, teach
  `_apply_annotation_values` to skip row creation when *all* provided values
  are clearing. Not required for this feature.

## 4. Test plan

### 4.1 Functional (extend `backend/tests/test_api.py`)

| Test | Asserts |
|---|---|
| folder-flag with `jira` filter | only files with that effective JIRA get the flag; others and the folder's own value untouched (folder stays indeterminate) |
| folder-flag with `no_transfer=no` filter setting `no_transfer=true` | only previously-unmarked files stamped; result = all effectively marked |
| bulk-annotation with `assignee=__none__` + `types` | JIRA stamped only onto unassigned files of those types (AND-composition) |
| bulk-annotation with flag filter, then **clear** (`value: null`) with same filter | scoped clear touches only matches; `clear_field_under` still never creates rows |
| scoped write with *only* an eff-filter active (no types/dates) | takes the **scoped** path — regression test for the G3 hazard |
| `stats` preview with full filter set | count equals the row count the subsequent scoped write reports |
| equivalence | after each scenario, materialized eff columns == `resolve_effective` for every touched subtree (reuse the existing checker) |
| grid `bulk-by-filter` with `types` + flag filter + values incl. `comment` | applies to exactly the search-matching set (already-supported path, now UI-reachable — pin it) |

### 4.2 Performance (extend `backend/tests/test_performance.py`)

Add an opt-in **write-scenario section** to the existing `RUN_PERF_TESTS=1`
suite, run against the same ≥ 2.2M-row dataset, with a **10 s budget per
write** (separate from the 3 s read budget):

1. Grid: substring `q` matching ~100k scattered files → select-all → set flag.
2. Grid: 10 file types simultaneously → select-all → set assignee.
3. Tree: department folder (n ≈ 40k files) + type filter + `jira=__none__` →
   bulk JIRA stamp (the new combined-filter path).
4. Tree: folder-flag with `no_transfer=no` filter (eff-filter scoped write).
5. A scoped **clear** with a filter (exercises the slower COALESCE-with-parent
   refresh path).

And to the **read** section (3 s budget): `stats` with the full filter set on
the root folder; `tree/children` at root with types + flag + jira combined
(already partially covered — add the jira/assignee axes).

### 4.3 Non-regression gate

Before merge, all three must pass at reference scale:

1. Full functional suite (`pytest`) — unchanged tests unchanged.
2. Full read suite: all 66 existing queries still < 3 s (the new predicates
   touch only write paths and `stats`; `search`/`children`/rollups are not
   modified, so any movement here is noise — but verify).
3. New write scenarios < 10 s each; additionally `EXPLAIN (ANALYZE)` the new
   `bulk_set_under` WHERE on the big dataset to confirm the plan is
   mat_path-range + eff-index, not a seq scan per write.

## 5. Sequencing

1. Backend: `ViewFilter` plumbing into `bulk_set_under` / `clear_field_under`
   / `folder_stats` + schema/router pass-through (G1, G2). Functional tests.
2. Frontend: `api.ts` types, DetailPanel scoping fix incl. Assignee (G3, G4).
3. Frontend: grid type/date filters + Comment in bulk bar (G5, G6, G7).
4. Perf-test extension; run the full gate at 2.2M rows; record numbers in the
   PR description.
5. Docs: README "What it answers" bullets for the two new workflows; update
   invariant I13's wording in `RECURSIVE_SQL_PLAN.md` (types/dates → any
   filter); note the air-gap bundle needs no changes beyond rebuilding the
   web image via the existing `scripts/build-offline.sh`.
