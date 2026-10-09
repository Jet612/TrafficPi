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
