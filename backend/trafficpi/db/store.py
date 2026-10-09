"""Everything the services read and write, in one place."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, func, select
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.orm import Session, sessionmaker

from trafficpi.core.clock import WallClock
from trafficpi.db.models import Camera, Event, MinuteCount, Profile, Setting, User
from trafficpi.db.session import create_db_engine, upgrade_to_head
from trafficpi.vision.rules import CountEvent

DEFAULT_CAMERA = "default"


@dataclass(frozen=True, slots=True)
class EventRow:
    id: int
    occurred_at: int  # ms since the epoch, UTC
    type: str
    track_id: int | None
    class_label: str | None
    line_id: str | None
    direction: int | None
    details: dict[str, Any] | None
    evidence_path: str | None


@dataclass(frozen=True, slots=True)
class CountRow:
    bucket_start: int  # s since the epoch, UTC, aligned to a minute
    line_id: str
    direction: int
    class_label: str
    count: int


@dataclass(frozen=True, slots=True)
class UserRow:
    id: int
    username: str
    password_hash: str


class Store:
    """Reads and writes for one camera. Each call is its own short transaction."""

    def __init__(self, engine: Engine, camera_id: str = DEFAULT_CAMERA) -> None:
        self.camera_id = camera_id
        self._engine = engine
        self._sessions = sessionmaker(engine, expire_on_commit=False)

    def close(self) -> None:
        """Close the database connections (the last one folds the WAL file into the database)."""
        self._engine.dispose()

    @contextmanager
    def _session(self) -> Iterator[Session]:
        with self._sessions() as session, session.begin():
            yield session

    # --- cameras ---------------------------------------------------------------------------

    def ensure_camera(self, name: str = "Camera", *, now_ms: int | None = None) -> None:
        """Create this store's camera row if it is not there yet."""
        now = WallClock().now_ms() if now_ms is None else now_ms
        with self._session() as s:
            s.execute(
                insert(Camera)
                .values(id=self.camera_id, name=name, created_at=now)
                .on_conflict_do_nothing()
            )

    # --- events and counts -----------------------------------------------------------------

    def record_count(
        self,
        event: CountEvent,
        occurred_at_ms: int,
        *,
        details: dict[str, Any] | None = None,
        evidence_path: str | None = None,
    ) -> int:
        """Store a count event and add it to its minute's totals; returns the event id."""
        bucket = occurred_at_ms // 60_000 * 60
        with self._session() as s:
            row = Event(
                camera_id=self.camera_id,
                occurred_at=occurred_at_ms,
                type="count",
                track_id=event.track_id,
                class_label=event.label,
                line_id=event.line_id,
                direction=event.direction,
                details=details,
                evidence_path=evidence_path,
            )
            s.add(row)
            s.execute(
                insert(MinuteCount)
                .values(
                    camera_id=self.camera_id,
                    bucket_start=bucket,
                    line_id=event.line_id,
                    direction=event.direction,
                    class_label=event.label,
                    count=1,
                )
                .on_conflict_do_update(
                    index_elements=[
                        "camera_id",
                        "bucket_start",
                        "line_id",
                        "direction",
                        "class_label",
                    ],
                    set_={"count": MinuteCount.count + 1},
                )
            )
            s.flush()
            return row.id

    def minute_counts(self, start_s: int, end_s: int) -> list[CountRow]:
        """Per-minute totals for minutes starting in ``[start_s, end_s)``, oldest first."""
        with self._session() as s:
            rows = s.execute(
                select(MinuteCount)
                .where(
                    MinuteCount.camera_id == self.camera_id,
                    MinuteCount.bucket_start >= start_s,
                    MinuteCount.bucket_start < end_s,
                )
                .order_by(MinuteCount.bucket_start, MinuteCount.line_id, MinuteCount.direction)
            ).scalars()
            return [
                CountRow(r.bucket_start, r.line_id, r.direction, r.class_label, r.count)
                for r in rows
            ]

    def recent_events(
        self,
        *,
        limit: int = 50,
        before_id: int | None = None,
        type: str | None = None,
        since_ms: int | None = None,
    ) -> list[EventRow]:
        """Newest first. Pass the last id seen as ``before_id`` to get the next page."""
        query = select(Event).where(Event.camera_id == self.camera_id)
        if before_id is not None:
            query = query.where(Event.id < before_id)
        if type is not None:
            query = query.where(Event.type == type)
        if since_ms is not None:
            query = query.where(Event.occurred_at >= since_ms)
        with self._session() as s:
            rows = s.execute(query.order_by(Event.id.desc()).limit(limit)).scalars()
            return [
                EventRow(
                    r.id,
                    r.occurred_at,
                    r.type,
                    r.track_id,
                    r.class_label,
                    r.line_id,
                    r.direction,
                    r.details,
                    r.evidence_path,
                )
                for r in rows
            ]

    def event_count(self) -> int:
        with self._session() as s:
            return s.execute(
                select(func.count(Event.id)).where(Event.camera_id == self.camera_id)
            ).scalar_one()

    # --- settings and profile --------------------------------------------------------------

    def get_setting(self, key: str, default: Any = None) -> Any:
        with self._session() as s:
            row = s.get(Setting, key)
            return default if row is None else row.value

    def set_setting(self, key: str, value: Any) -> None:
        with self._session() as s:
            s.execute(
                insert(Setting)
                .values(key=key, value=value)
                .on_conflict_do_update(index_elements=["key"], set_={"value": value})
            )

    def get_profile(self) -> tuple[dict[str, Any], int] | None:
        """The camera's configuration document and its version, or None if none was saved."""
        with self._session() as s:
            row = s.get(Profile, self.camera_id)
            return None if row is None else (row.document, row.version)

    def set_profile(self, document: dict[str, Any], *, now_ms: int | None = None) -> int:
        """Save the configuration document; returns the new version."""
        now = WallClock().now_ms() if now_ms is None else now_ms
        with self._session() as s:
            row = s.get(Profile, self.camera_id)
            if row is None:
                s.add(
                    Profile(camera_id=self.camera_id, document=document, version=1, updated_at=now)
                )
                return 1
            row.document, row.version, row.updated_at = document, row.version + 1, now
            return row.version

    # --- users -----------------------------------------------------------------------------

    def create_user(self, username: str, password_hash: str, *, now_ms: int | None = None) -> int:
        now = WallClock().now_ms() if now_ms is None else now_ms
        with self._session() as s:
            user = User(username=username, password_hash=password_hash, created_at=now)
            s.add(user)
            s.flush()
            return user.id

    def get_user(self, username: str) -> UserRow | None:
        with self._session() as s:
            user = s.execute(select(User).where(User.username == username)).scalar_one_or_none()
            return None if user is None else UserRow(user.id, user.username, user.password_hash)

    def set_password_hash(self, username: str, password_hash: str) -> bool:
        with self._session() as s:
            user = s.execute(select(User).where(User.username == username)).scalar_one_or_none()
            if user is None:
                return False
            user.password_hash = password_hash
            return True

    def user_count(self) -> int:
        with self._session() as s:
            return len(s.execute(select(User.id)).all())


def open_store(data_dir: Path) -> Store:
    """The store for the database in ``data_dir``, created and migrated as needed.

    Both services call this at start-up; migrations take turns (see ``upgrade_to_head``).
    """
    data_dir.mkdir(parents=True, exist_ok=True)
    engine = create_db_engine(data_dir / "trafficpi.db")
    upgrade_to_head(engine)
    store = Store(engine)
    store.ensure_camera()
    return store
