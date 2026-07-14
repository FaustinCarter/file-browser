"""FastAPI application entrypoint."""
from __future__ import annotations

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import services
from .database import Base, engine
from .routers import datasets, nodes, tree
from sqlalchemy import text


# Arbitrary constant key for the schema-setup advisory lock.
_SCHEMA_LOCK_KEY = 728171


def _init_schema():
    """Run in-place migrations + create tables, serialized across workers.

    A transaction-level advisory lock guarantees only one worker performs schema
    changes at a time; the others block, then re-check and skip. Without this,
    concurrent uvicorn workers race on the keep -> no_transfer rename (the first
    succeeds, the rest see "column keep does not exist").
    """
    is_pg = engine.dialect.name == "postgresql"
    with engine.begin() as conn:
        if is_pg:
            conn.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": _SCHEMA_LOCK_KEY})

            def col(name: str) -> bool:
                return bool(
                    conn.execute(
                        text(
                            "SELECT 1 FROM information_schema.columns "
                            "WHERE table_name='annotations' AND column_name=:c"
                        ),
                        {"c": name},
                    ).first()
                )

            table_exists = col("node_id")
            if table_exists:
                # keep -> no_transfer
                if col("keep") and not col("no_transfer"):
                    conn.execute(
                        text("ALTER TABLE annotations RENAME COLUMN keep TO no_transfer")
                    )
                # user_name (auto-stamped editor) -> updated_by audit field
                if col("user_name") and not col("updated_by"):
                    conn.execute(
                        text("ALTER TABLE annotations RENAME COLUMN user_name TO updated_by")
                    )
                # new editable assignee column
                if not col("assignee"):
                    conn.execute(text("ALTER TABLE annotations ADD COLUMN assignee text"))
                # indexes that speed effective-value filtering (created for
                # existing tables; create_all handles fresh ones).
                for name, cols in (
                    ("ix_annotations_assignee", "dataset_id, assignee"),
                    ("ix_annotations_no_transfer", "dataset_id, no_transfer"),
                    ("ix_annotations_processed", "dataset_id, processed"),
                ):
                    conn.execute(
                        text(
                            f"CREATE INDEX IF NOT EXISTS {name} ON annotations ({cols})"
                        )
                    )

                # Materialized effective-value columns on nodes (see models.py):
                # a one-time addition on an existing table needs both the new
                # columns/indexes AND a backfill, since ADD COLUMN leaves them
                # NULL for every existing row -- without the backfill, an
                # upgraded dataset's filters/rollups would look "empty" until
                # its next annotation edit.
                needs_eff_backfill = not bool(
                    conn.execute(
                        text(
                            "SELECT 1 FROM information_schema.columns "
                            "WHERE table_name='nodes' AND column_name='no_transfer_eff'"
                        )
                    ).first()
                )
                for name, ddl_type in (
                    ("no_transfer_eff", "boolean"),
                    ("processed_eff", "boolean"),
                    ("jira_ticket_eff", "text"),
                    ("assignee_eff", "text"),
                ):
                    conn.execute(
                        text(f"ALTER TABLE nodes ADD COLUMN IF NOT EXISTS {name} {ddl_type}")
                    )
                for name, cols in (
                    ("ix_nodes_no_transfer_eff", "dataset_id, no_transfer_eff"),
                    ("ix_nodes_processed_eff", "dataset_id, processed_eff"),
                    ("ix_nodes_jira_eff", "dataset_id, jira_ticket_eff"),
                    ("ix_nodes_assignee_eff", "dataset_id, assignee_eff"),
                    ("ix_nodes_dirlevel", "dataset_id, dir_level"),
                ):
                    conn.execute(
                        text(f"CREATE INDEX IF NOT EXISTS {name} ON nodes ({cols})")
                    )
        # create_all is a no-op for existing tables; runs inside the same lock.
        Base.metadata.create_all(bind=conn)

        if is_pg and table_exists and needs_eff_backfill:
            ds_ids = [r[0] for r in conn.execute(text("SELECT id FROM datasets")).all()]
            for dsid in ds_ids:
                services.refresh_effective_columns(conn, dsid)

    _maybe_build_trgm_index(is_pg)


def _maybe_build_trgm_index(is_pg: bool):
    """Optional trigram index for fast 'path contains' search (opt-in).

    Kept in its own transaction (so a failure — e.g. a managed Postgres where the
    role can't CREATE EXTENSION — degrades to a sequential scan instead of
    aborting schema setup) and serialized with the schema advisory lock.
    """
    if not is_pg or os.environ.get("ENABLE_TRGM_INDEX", "").lower() not in (
        "1", "true", "yes", "on",
    ):
        return
    try:
        with engine.begin() as conn:
            conn.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": _SCHEMA_LOCK_KEY})
            conn.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
            conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_nodes_fullpath_trgm "
                    "ON nodes USING gin (full_path gin_trgm_ops)"
                )
            )
    except Exception as e:  # noqa: BLE001
        print(f"trigram index unavailable, skipping ({e})")


@asynccontextmanager
async def lifespan(app: FastAPI):
    _init_schema()
    yield


app = FastAPI(title="File Browser & Migration Tagger", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(datasets.router)
app.include_router(tree.router)
app.include_router(nodes.router)


@app.get("/api/health")
def health():
    return {"status": "ok"}


# ---- Serve the built frontend (production) ----
# In the docker image the Vite build is copied to /app/static.
_STATIC_DIR = os.environ.get("STATIC_DIR", "/app/static")
if os.path.isdir(_STATIC_DIR):
    app.mount(
        "/assets",
        StaticFiles(directory=os.path.join(_STATIC_DIR, "assets")),
        name="assets",
    )

    def _index() -> FileResponse:
        # The shell points at content-hashed assets, so it must always be
        # revalidated — otherwise a rebuild's new UI is masked by a cached
        # index.html still referencing the old bundle.
        return FileResponse(
            os.path.join(_STATIC_DIR, "index.html"),
            headers={"Cache-Control": "no-cache, must-revalidate"},
        )

    @app.get("/")
    def index():
        return _index()

    # SPA fallback for any non-API route.
    @app.get("/{full_path:path}")
    def spa(full_path: str):
        candidate = os.path.join(_STATIC_DIR, full_path)
        if os.path.isfile(candidate) and os.path.abspath(candidate).startswith(
            os.path.abspath(_STATIC_DIR)
        ):
            return FileResponse(candidate)
        return _index()
