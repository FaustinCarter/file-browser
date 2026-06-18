#!/usr/bin/env bash
set -e

# Wait for Postgres to accept connections before starting the app.
python - <<'PY'
import os, time
import sqlalchemy as sa

url = os.environ.get("DATABASE_URL", "")
if url:
    engine = sa.create_engine(url)
    for attempt in range(60):
        try:
            with engine.connect() as c:
                c.execute(sa.text("SELECT 1"))
            print("Database is ready.")
            break
        except Exception as e:
            print(f"Waiting for database ({attempt + 1}/60): {e}")
            time.sleep(2)
    else:
        raise SystemExit("Database never became available")
PY

WORKERS="${WEB_CONCURRENCY:-4}"
exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers "$WORKERS"
