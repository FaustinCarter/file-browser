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


def _migrate():
    """Idempotent, in-place schema tweaks for existing deployments."""
    with engine.begin() as conn:
        # keep -> no_transfer rename (preserves existing annotations).
        has_keep = conn.execute(
            text(
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_name='annotations' AND column_name='keep'"
            )
        ).first()
        has_new = conn.execute(
            text(
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_name='annotations' AND column_name='no_transfer'"
            )
        ).first()
        if has_keep and not has_new:
            conn.execute(text("ALTER TABLE annotations RENAME COLUMN keep TO no_transfer"))


@asynccontextmanager
async def lifespan(app: FastAPI):
    _migrate()
    Base.metadata.create_all(bind=engine)
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

    @app.get("/")
    def index():
        return FileResponse(os.path.join(_STATIC_DIR, "index.html"))

    # SPA fallback for any non-API route.
    @app.get("/{full_path:path}")
    def spa(full_path: str):
        candidate = os.path.join(_STATIC_DIR, full_path)
        if os.path.isfile(candidate):
            return FileResponse(candidate)
        return FileResponse(os.path.join(_STATIC_DIR, "index.html"))
