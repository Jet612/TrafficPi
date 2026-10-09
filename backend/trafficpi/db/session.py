"""Opening the database and bringing its schema up to date."""

from __future__ import annotations

import fcntl
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, event
from sqlalchemy.engine.interfaces import DBAPIConnection
from sqlalchemy.pool import ConnectionPoolEntry, StaticPool

MIGRATIONS = Path(__file__).parent / "migrations"


def create_db_engine(path: Path | str, *, echo: bool = False) -> Engine:
    """An engine for the SQLite database at ``path`` (``":memory:"`` for tests).

    Two processes use the same file: the vision service writes and the API reads and writes
    settings. WAL mode lets them do so concurrently, and the busy timeout makes a writer wait
    briefly for another instead of failing.
    """
    in_memory = str(path) == ":memory:"
    engine = create_engine(
        "sqlite://" if in_memory else f"sqlite:///{path}",
        echo=echo,
        connect_args={"check_same_thread": False},
        **({"poolclass": StaticPool} if in_memory else {}),
    )

    @event.listens_for(engine, "connect")
    def _configure(dbapi_connection: DBAPIConnection, _record: ConnectionPoolEntry) -> None:
        cursor = dbapi_connection.cursor()
        if not in_memory:
            cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    return engine


def _alembic_config(**attributes: Any) -> Config:
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS))
    config.attributes.update(attributes)
    return config


@contextmanager
def _migration_lock(engine: Engine) -> Iterator[None]:
    """Make processes take turns migrating one database file.

    Both services migrate at start-up, and systemd starts them together. On a fresh database the
    second one would otherwise fail with "database is locked" (SQLite does not wait for a write
    that follows a read in the same transaction). The lock file sits next to the database.
    """
    database = engine.url.database
    if not database or database == ":memory:":
        yield
        return
    with Path(f"{database}.migrate-lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)  # released when the file is closed
        yield


def upgrade_to_head(engine: Engine) -> None:
    """Apply any migrations the database has not seen yet."""
    with _migration_lock(engine), engine.begin() as connection:
        command.upgrade(_alembic_config(connection=connection), "head")
