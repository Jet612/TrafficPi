from pathlib import Path

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import inspect, text

from trafficpi.db import Base, create_db_engine, upgrade_to_head
from trafficpi.db.session import _alembic_config


def test_migrating_creates_every_table(tmp_path: Path) -> None:
    engine = create_db_engine(tmp_path / "t.db")
    upgrade_to_head(engine)
    tables = set(inspect(engine).get_table_names())
    assert {
        "cameras",
        "events",
        "minute_counts",
        "users",
        "settings",
        "profiles",
        "trails",
        "mail_recipients",
        "mail_sends",
        "mail_deliveries",
    } <= tables
    assert "alembic_version" in tables


def test_the_migration_matches_the_models(tmp_path: Path) -> None:
    """If a model changes without a migration, this fails."""
    engine = create_db_engine(tmp_path / "t.db")
    upgrade_to_head(engine)
    with engine.connect() as connection:
        diff = compare_metadata(MigrationContext.configure(connection), Base.metadata)
    assert diff == []


def test_a_database_from_before_reviews_keeps_its_events_and_gains_the_columns(
    tmp_path: Path,
) -> None:
    """The real database has months of events by the time a migration is added: none may be lost."""
    engine = create_db_engine(tmp_path / "t.db")
    with engine.begin() as connection:
        command.upgrade(_alembic_config(connection=connection), "0002")
        connection.execute(
            text("INSERT INTO cameras (id, name, created_at) VALUES ('default', 'Front', 1)")
        )
        connection.execute(
            text(
                "INSERT INTO events (camera_id, occurred_at, type, class_label, line_id, direction)"
                " VALUES ('default', 5, 'count', 'car', 'main', 1)"
            )
        )
    assert "review" not in {c["name"] for c in inspect(engine).get_columns("events")}
    upgrade_to_head(engine)
    columns = {c["name"] for c in inspect(engine).get_columns("events")}
    assert {"review", "reviewed_at"} <= columns
    with engine.connect() as connection:
        row = connection.execute(
            text("SELECT occurred_at, class_label, review, reviewed_at FROM events")
        ).one()
    assert tuple(row) == (5, "car", None, None)


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


def test_a_database_from_before_the_email_keeps_what_it_has_and_gains_its_tables(
    tmp_path: Path,
) -> None:
    """The real database has days of events, trails and health by the time this migration runs."""
    engine = create_db_engine(tmp_path / "t.db")
    with engine.begin() as connection:
        command.upgrade(_alembic_config(connection=connection), "0004")
        connection.execute(
            text("INSERT INTO cameras (id, name, created_at) VALUES ('default', 'Front', 1)")
        )
        connection.execute(
            text(
                "INSERT INTO events (camera_id, occurred_at, type, class_label, line_id, direction)"
                " VALUES ('default', 5, 'count', 'car', 'main', 1)"
            )
        )
        connection.execute(text("INSERT INTO settings (key, value) VALUES ('units', '\"kmh\"')"))
    assert "mail_sends" not in set(inspect(engine).get_table_names())
    upgrade_to_head(engine)
    assert {"mail_recipients", "mail_sends", "mail_deliveries"} <= set(
        inspect(engine).get_table_names()
    )
    with engine.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM events")).scalar_one() == 1
        assert (
            connection.execute(text("SELECT value FROM settings")).scalar_one() == '"kmh"'
        )  # JSON text


def test_the_email_migration_can_be_undone_and_done_again(tmp_path: Path) -> None:
    engine = create_db_engine(tmp_path / "t.db")
    upgrade_to_head(engine)
    with engine.begin() as connection:
        command.downgrade(_alembic_config(connection=connection), "0004")
    assert "mail_sends" not in set(inspect(engine).get_table_names())
    upgrade_to_head(engine)
    assert "mail_sends" in set(inspect(engine).get_table_names())
