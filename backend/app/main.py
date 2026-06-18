"""FastAPI application entrypoint."""
from __future__ import annotations

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

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
                    conn.execute(
                        text(
                            "CREATE INDEX IF NOT EXISTS ix_annotations_assignee "
                            "ON annotations (dataset_id, assignee)"
                        )
                    )
        # create_all is a no-op for existing tables; runs inside the same lock.
        Base.metadata.create_all(bind=conn)


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
