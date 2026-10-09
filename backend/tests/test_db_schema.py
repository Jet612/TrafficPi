from pathlib import Path

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import inspect, text

from trafficpi.db import Base, create_db_engine, upgrade_to_head


def test_migrating_creates_every_table(tmp_path: Path) -> None:
    engine = create_db_engine(tmp_path / "t.db")
    upgrade_to_head(engine)
    tables = set(inspect(engine).get_table_names())
    assert {"cameras", "events", "minute_counts", "users", "settings", "profiles"} <= tables
    assert "alembic_version" in tables


def test_the_migration_matches_the_models(tmp_path: Path) -> None:
    """If a model changes without a migration, this fails."""
    engine = create_db_engine(tmp_path / "t.db")
    upgrade_to_head(engine)
    with engine.connect() as connection:
        diff = compare_metadata(MigrationContext.configure(connection), Base.metadata)
    assert diff == []


def test_upgrading_twice_is_harmless(tmp_path: Path) -> None:
    engine = create_db_engine(tmp_path / "t.db")
    upgrade_to_head(engine)
    upgrade_to_head(engine)
    with engine.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM alembic_version")).scalar() == 1


def test_the_file_database_uses_wal_and_waits_for_locks(tmp_path: Path) -> None:
    engine = create_db_engine(tmp_path / "t.db")
    with engine.connect() as connection:
        assert connection.execute(text("PRAGMA journal_mode")).scalar() == "wal"
        assert connection.execute(text("PRAGMA busy_timeout")).scalar() == 5000
        assert connection.execute(text("PRAGMA foreign_keys")).scalar() == 1


def test_an_in_memory_database_shares_one_connection() -> None:
    engine = create_db_engine(":memory:")
    upgrade_to_head(engine)
    assert "events" in inspect(engine).get_table_names()


MIGRATE_AT = """
import sys, time
from trafficpi.db import create_db_engine, upgrade_to_head

path, start_at = sys.argv[1], float(sys.argv[2])
while time.time() < start_at:
    pass
upgrade_to_head(create_db_engine(path))
"""


def test_processes_starting_together_can_all_migrate_a_fresh_database(tmp_path: Path) -> None:
    """Both services migrate at start-up, and systemd starts them at the same moment."""
    import subprocess
    import sys
    import time

    database = tmp_path / "trafficpi.db"
    start_at = time.time() + 3.0  # leave time for every interpreter to import its modules
    workers = [
        subprocess.Popen(
            [sys.executable, "-c", MIGRATE_AT, str(database), str(start_at)],
            stderr=subprocess.PIPE,
            text=True,
        )
        for _ in range(3)
    ]
    outcomes = [(w.wait(timeout=60), w.stderr.read() if w.stderr else "") for w in workers]
    assert [code for code, _ in outcomes] == [0, 0, 0], outcomes
    engine = create_db_engine(database)
    with engine.connect() as connection:
        assert connection.exec_driver_sql("SELECT version_num FROM alembic_version").all()


def test_open_store_creates_the_directory_and_a_usable_store(tmp_path: Path) -> None:
    from trafficpi.db.store import open_store

    data_dir = tmp_path / "nested" / "data"
    store = open_store(data_dir)
    assert (data_dir / "trafficpi.db").exists()
    assert store.event_count() == 0 and store.user_count() == 0
    store.set_setting("units", "kmh")
    again = open_store(data_dir)  # opening an existing database migrates nothing and keeps data
    assert again.get_setting("units") == "kmh"
