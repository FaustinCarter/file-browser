#!/usr/bin/env bash
set -e

# Wait for the Postgres *server* and make sure the application database exists.
#
# The official postgres image only creates POSTGRES_DB / configures auth on the
# FIRST init of an empty data directory. If that init was interrupted (common on
# a slow air-gapped first boot) the volume is left non-empty but missing the app
# database, and every later start "skips initialization" -> "database ... does
# not exist". So we provision the database ourselves here (idempotent), which
# also covers pointing at an external Postgres where only the role was created.
python - <<'PY'
import os, time
import sqlalchemy as sa
from sqlalchemy.exc import OperationalError

url = os.environ.get("DATABASE_URL", "")
if url:
    u = sa.engine.make_url(url)
    if not u.get_backend_name().startswith("postgresql"):
        raise SystemExit(0)

    target_db = u.database
    # Connect to the always-present 'postgres' maintenance database.
    maint = sa.create_engine(u.set(database="postgres"), isolation_level="AUTOCOMMIT")

    for attempt in range(60):
        try:
            with maint.connect() as c:
                c.execute(sa.text("SELECT 1"))
            break
        except OperationalError as e:
            print(f"Waiting for database server ({attempt + 1}/60): {e}")
            time.sleep(2)
    else:
        raise SystemExit("Database server never became available")

    if target_db and target_db != "postgres":
        with maint.connect() as c:
            exists = c.execute(
                sa.text("SELECT 1 FROM pg_database WHERE datname = :n"),
                {"n": target_db},
            ).first()
            if not exists:
                print(f"Creating missing database {target_db!r}")
                c.execute(sa.text(f'CREATE DATABASE "{target_db}"'))
    print("Database is ready.")
PY

WORKERS="${WEB_CONCURRENCY:-4}"
exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers "$WORKERS"
