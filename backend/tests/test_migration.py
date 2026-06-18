"""Regression test for the keep -> no_transfer in-place migration."""
import threading

from sqlalchemy import text

from app.database import engine
from app.main import _init_schema


def _make_old_schema():
    """Replace annotations with the pre-rename schema (a 'keep' column + data)."""
    with engine.begin() as c:
        c.execute(text("DROP TABLE IF EXISTS annotations CASCADE"))
        c.execute(
            text(
                "CREATE TABLE annotations ("
                "node_id bigint primary key, dataset_id bigint not null, "
                "processed boolean, keep boolean, target_location text, "
                "jira_ticket text, comment text, user_name text, "
                "updated_at timestamp default now())"
            )
        )
        c.execute(
            text("INSERT INTO annotations (node_id, dataset_id, keep) VALUES (1, 1, true)")
        )


def _assert_migrated():
    with engine.connect() as c:
        cols = [
            r[0]
            for r in c.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name='annotations'"
                )
            )
        ]
        val = c.execute(
            text("SELECT no_transfer FROM annotations WHERE node_id=1")
        ).scalar()
    assert "no_transfer" in cols and "keep" not in cols
    assert val is True  # data preserved


def test_keep_renamed_and_data_preserved():
    _make_old_schema()
    _init_schema()
    _assert_migrated()


def test_migration_is_concurrency_safe():
    """Multiple workers starting at once must not race on the rename."""
    _make_old_schema()
    errors: list[str] = []

    def run():
        try:
            _init_schema()
        except Exception as e:  # noqa: BLE001
            errors.append(repr(e))

    threads = [threading.Thread(target=run) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    _assert_migrated()
