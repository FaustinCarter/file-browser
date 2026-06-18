# File Browser & Migration Tagger

A fast, centrally-deployed tool for **categorizing, exploring, and tagging**
millions of files ahead of a file-server migration. You feed it the CSV exports
produced by a Windows disk-usage tool (TreeSize-style, one row per filesystem
object) and get an interactive tree explorer, a filterable grid, recursive
type/size analytics, and editable migration metadata (No-Transfer/Processed flags,
target location, JIRA ticket, comments) — all shared by your whole team.

---

## Highlights

- **Handles scale.** Data lives in PostgreSQL with a materialized-path tree, so
  descendant counts and subtree queries stay fast at millions of rows.
- **Interactive tree explorer** with lazy loading, file-type filtering, and a
  last-accessed date range. Every folder shows its size and a **file counter
  that respects the active filters**.
- **Grid / bulk-edit view** — flat, paginated, sortable, filterable table with
  inline editing and multi-row bulk actions.
- **Inherited + override tagging.** Mark a folder `No Transfer`/`Processed` and
  every file inside inherits it automatically (no mass row rewrites); override
  any individual file or subfolder. A separate "bulk stamp" writes concrete
  values (e.g. one JIRA ticket onto hundreds of filtered files at once).
- **Tri-state folder flags + hide filters.** A folder's No-Transfer/Processed
  checkbox rolls up its files (checked = all, **indeterminate = mixed/irregular**,
  empty = none). Filter either boolean in the tree or grid to hide rows you've
  already handled; fully-marked folders drop out, partly-marked ones stay with a
  ⚠ marker.
- **Original CSV columns are always read-only**; all migration metadata is
  editable.
- **Multi-CSV.** Upload as many exports as you like; each becomes an independent
  dataset you can switch between. Add the first (and every other) CSV right from
  the UI.
- **One-command Docker deploy** (`docker compose up`) for shared team access.

---

## Quick start (Docker — recommended)

```bash
git clone <this repo> && cd file-browser
docker compose up --build
```

Then open <http://localhost:8000>. On first visit you'll be asked for a display
name (used to stamp the `user name` column on your edits — see
[Identity](#identity)). Click **+ Upload CSV** and select an export to begin.

A ready-made test dataset lives at `sample_data/fake_fileserver.csv`
(1,212 rows, 131 folders) — upload it to explore immediately.

### Configuration

| Variable            | Default        | Purpose                                  |
| ------------------- | -------------- | ---------------------------------------- |
| `POSTGRES_PASSWORD` | `filebrowser`  | Database password                        |
| `APP_PORT`          | `8000`         | Host port the web app is published on    |
| `WEB_CONCURRENCY`   | `4`            | uvicorn worker processes                 |
| `DATABASE_URL`      | (compose-set)  | Override to point at an external Postgres |

---

## Offline / air-gapped deployment (build on Mac, run on RHEL)

The images target **`linux/amd64`** (set in `docker-compose.yml`), so they run on
a standard x86-64 RHEL host even when built on an Apple-silicon Mac. The app is
fully self-contained at runtime — the frontend is bundled into the image (system
fonts, same-origin API; **no CDN/web fonts**) and the backend makes no outbound
calls — so the only things the offline host needs are the two Docker images.

**On the build machine (online, e.g. a Mac with Docker Desktop):**

```bash
./scripts/build-offline.sh      # -> file-browser-offline.tar
```

This pulls `postgres:16` for amd64, builds `file-browser-web:latest` for amd64
(Node/Python/pip/apt resources are all baked into the image), and `docker save`s
both into one tarball. Copy `file-browser-offline.tar` and `docker-compose.yml`
(plus an optional `.env`) to the RHEL host.

**On the RHEL host (offline):**

```bash
docker load -i file-browser-offline.tar
docker compose up -d
```

`pull_policy: never` on both services guarantees Compose uses the loaded images
and never contacts a registry. The first run creates the schema automatically;
data lives in the `pgdata` volume.

> Building amd64 on Apple silicon uses Docker Desktop's buildx + QEMU emulation
> (the frontend stage builds natively via `$BUILDPLATFORM` for speed). If you
> build on an x86-64 machine, no emulation is involved.

---

## Local development (without Docker)

**Backend** (needs a reachable PostgreSQL):

```bash
cd backend
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
export DATABASE_URL="postgresql+psycopg2://user:pass@localhost:5432/filebrowser"
uvicorn app.main:app --reload
```

**Frontend** (proxies `/api` to the backend on :8000):

```bash
cd frontend
npm install
npm run dev      # http://localhost:5173
```

**Generate fresh test data:**

```bash
python backend/scripts/generate_fake_data.py \
    --out sample_data/fake_fileserver.csv --min-rows 1200
```

---

## CSV format

The importer expects the Windows-export columns below (header matching is
case/spacing tolerant). Folders are identified by a trailing backslash in
`Full Path`.

`Name`, `Full Path`, `Size`, `Allocated`, `Files`, `Folders`,
`% of Parent (Allocated)`, `Last Modified`, `Last Accessed`, `Owner`, `Type`,
`Dir Level (Relative)`.

Human-readable sizes (`10 GB`, `1,024 KB`) and percentages (`11.8 %`) are parsed
into sortable numeric values while the original strings are kept for display.

---

## What it answers

- *How many PPTX (or any type) files are under each of these folders?* —
  select a folder (tree) or use `POST /api/nodes/type-counts`; combine with the
  type filter.
- *How many files/folders are nested under row X?* — the detail panel's
  "Nested (total)" line / `GET /api/nodes/{id}/counts`.
- *All file types in a folder with counts, sortable & filterable* — the detail
  panel's "File types here" table / `GET /api/nodes/{id}/type-breakdown`.
- *Mark files/folders No-Transfer/Processed (recursive)* — checkboxes in the
  detail or grid view; folders cascade to all their files. **With a tree filter
  active** (file type and/or last-accessed), checking a folder applies only to
  the *matching* files in its subtree — other types are left untouched and the
  folder stays indeterminate until every file is marked.
- *Hide what you've handled* — filter the tree or grid by No-Transfer/Processed
  to drop marked rows (and fully-marked folders) from view.
- *Target location, JIRA ticket, comment, user* — editable columns; bulk-edit by
  selection (grid) or by filtered subtree (detail panel's "Bulk stamp").

---

## Identity

Access is open (pick-a-name): on first load each user enters a display name,
stored in their browser and automatically attached to the `user name` column on
their edits. There is no login wall — put the container behind your own reverse
proxy / SSO if you need access control.

---

## Architecture

```
frontend/   React + TypeScript (Vite) — tree explorer, grid, detail/edit panels
backend/    FastAPI + SQLAlchemy
  app/parsing.py     size/percent/date parsers
  app/csv_import.py  tolerant importer, builds the materialized-path tree
  app/models.py      Node (read-only CSV cols) + Annotation (editable, nullable)
  app/services.py    inheritance resolution + subtree aggregate queries
  app/routers/       datasets, tree, nodes
Dockerfile           multi-stage: builds the SPA, bundles it into the API image
docker-compose.yml   web + postgres
```

**Tree storage.** Each node carries a materialized path of ids
(`/1/5/23/`). Descendant queries are a single indexed `LIKE '/1/5/23/%'`;
ancestor lookups just split the string. No `ltree` extension required.

**Inheritance.** Annotations are stored sparsely (1 row per *edited* node, every
field nullable = "inherit"). Effective values are resolved by walking the
ancestor chain nearest-first, so marking a huge folder is a single insert and
never rewrites its children.

---

## Tests

```bash
cd backend
. ../.venv/bin/activate
DATABASE_URL=postgresql+psycopg2://... pytest        # API + parsing units
python tests/ui_smoke.py                              # Playwright frontend smoke
```
