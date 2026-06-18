"""Application configuration, sourced from environment variables."""
from __future__ import annotations

import os


class Settings:
    # Default points at the docker-compose 'db' service. Override with
    # DATABASE_URL for local dev / tests.
    database_url: str = os.environ.get(
        "DATABASE_URL",
        "postgresql+psycopg2://filebrowser:filebrowser@db:5432/filebrowser",
    )
    # How many descendants we will physically rewrite before refusing a
    # "write to every descendant" style operation. Inheritance is the default
    # so we never need this, but it guards bulk ops.
    max_bulk_rows: int = int(os.environ.get("MAX_BULK_ROWS", "5000000"))


settings = Settings()
