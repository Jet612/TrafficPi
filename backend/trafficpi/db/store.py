"""Everything the services read and write, in one place."""

from __future__ import annotations

from collections.abc import Collection, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from sqlalchemy import CursorResult, Engine, delete, func, select
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.orm import Session, sessionmaker

from trafficpi.core.clock import WallClock
from trafficpi.core.trail_settings import (
    DEFAULT_KEEP_DAYS,
    ENABLED_KEY,
    KEEP_DAYS_KEY,
    MAX_KEEP_DAYS,
    PEOPLE_KEY,
    TrailSettings,
)
from trafficpi.db.models import Camera, Event, MinuteCount, Profile, Setting, Trail, User
from trafficpi.db.session import create_db_engine, upgrade_to_head
from trafficpi.vision.rules import CountEvent
from trafficpi.vision.trails import FinishedTrail

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
class TrailRow:
    id: int
    started_at: int  # ms since the epoch, UTC
    ended_at: int
    class_label: str
    points: int
    extent: float
    path: bytes  # decode with ``trafficpi.vision.trails.decode_path``


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

    # --- trails ----------------------------------------------------------------------------

    def add_trails(self, trails: Sequence[FinishedTrail]) -> None:
        """Store finished trails, all in one transaction."""
        if not trails:
            return
        with self._session() as s:
            s.add_all(
                Trail(
                    camera_id=self.camera_id,
                    started_at=t.started_at,
                    ended_at=t.ended_at,
                    class_label=t.label,
                    points=t.points,
                    extent=t.extent,
                    path=t.path,
                )
                for t in trails
            )

    def trails_between(
        self,
        start_ms: int,
        end_ms: int,
        *,
        labels: Collection[str] | None = None,
        limit: int = 20_000,
        every: int = 1,
    ) -> list[TrailRow]:
        """Trails that began in ``[start_ms, end_ms)``, oldest first, at most ``limit`` of them.

        ``every`` thins them out: 3 keeps every third one of those that match (the 3rd, the 6th,
        and so on), spread over the whole range, which is fairer than keeping the first ``limit``.
        """
        wanted = [
            Trail.camera_id == self.camera_id,
            Trail.started_at >= start_ms,
            Trail.started_at < end_ms,
        ]
        if labels is not None:
            wanted.append(Trail.class_label.in_(list(labels)))
        if every > 1:
            # Numbered by id only, so that the paths are read for the chosen ones alone.
            numbered = (
                select(
                    Trail.id.label("id"),
                    func.row_number().over(order_by=(Trail.started_at, Trail.id)).label("n"),
                )
                .where(*wanted)
                .subquery()
            )
            query = (
                select(Trail)
                .join(numbered, numbered.c.id == Trail.id)
                .where(numbered.c.n % every == 0)
                .order_by(numbered.c.n)
            )
        else:
            query = select(Trail).where(*wanted).order_by(Trail.started_at, Trail.id)
        with self._session() as s:
            rows = s.execute(query.limit(limit)).scalars()
            return [
                TrailRow(r.id, r.started_at, r.ended_at, r.class_label, r.points, r.extent, r.path)
                for r in rows
            ]

    def trail_count(
        self,
        start_ms: int | None = None,
        end_ms: int | None = None,
        *,
        labels: Collection[str] | None = None,
    ) -> int:
        """How many trails began in ``[start_ms, end_ms)`` (open ended when a bound is None)."""
        query = select(func.count(Trail.id)).where(Trail.camera_id == self.camera_id)
        if start_ms is not None:
            query = query.where(Trail.started_at >= start_ms)
        if end_ms is not None:
            query = query.where(Trail.started_at < end_ms)
        if labels is not None:
            query = query.where(Trail.class_label.in_(list(labels)))
        with self._session() as s:
            return s.execute(query).scalar_one()

    def trail_times(self, start_ms: int, end_ms: int) -> list[tuple[int, str]]:
        """When each trail in ``[start_ms, end_ms)`` began, and of what kind (no paths: cheap)."""
        with self._session() as s:
            rows = s.execute(
                select(Trail.started_at, Trail.class_label).where(
                    Trail.camera_id == self.camera_id,
                    Trail.started_at >= start_ms,
                    Trail.started_at < end_ms,
                )
            ).all()
            return [(started, label) for started, label in rows]

    def trail_extent(self) -> tuple[int, int] | None:
        """When the first and the last stored trail began; None if there are none."""
        with self._session() as s:
            first, last = s.execute(
                select(func.min(Trail.started_at), func.max(Trail.started_at)).where(
                    Trail.camera_id == self.camera_id
                )
            ).one()
            return None if first is None else (first, last)

    def delete_trails_before(self, cutoff_ms: int, *, limit: int = 2_000) -> int:
        """Delete up to ``limit`` trails that ended before ``cutoff_ms``; returns how many went.

        A limit keeps each transaction short, so that the camera loop, which writes counts to the
        same file, is never made to wait for a big clean-up: call it again while it returns
        ``limit``.
        """
        # A trail cannot end before it began, so those that ended before the cutoff began before
        # it too: saying so lets the index on the start find them without reading the whole table.
        oldest = (
            select(Trail.id)
            .where(
                Trail.camera_id == self.camera_id,
                Trail.started_at < cutoff_ms,
                Trail.ended_at < cutoff_ms,
            )
            .order_by(Trail.started_at)
            .limit(limit)
        )
        with self._session() as s:
            result = cast("CursorResult[Any]", s.execute(delete(Trail).where(Trail.id.in_(oldest))))
            return result.rowcount

    def get_trail_settings(self) -> TrailSettings:
        """Which trails are saved and for how long; what is stored but unusable is ignored."""
        keep = self.get_setting(KEEP_DAYS_KEY, DEFAULT_KEEP_DAYS)
        usable = isinstance(keep, int) and not isinstance(keep, bool) and 1 <= keep <= MAX_KEEP_DAYS
        return TrailSettings(
            enabled=self.get_setting(ENABLED_KEY, True) is not False,
            people=self.get_setting(PEOPLE_KEY, False) is True,
            keep_days=keep if usable else DEFAULT_KEEP_DAYS,
        )

    def set_trail_settings(self, settings: TrailSettings) -> None:
        self.set_setting(ENABLED_KEY, settings.enabled)
        self.set_setting(PEOPLE_KEY, settings.people)
        self.set_setting(KEEP_DAYS_KEY, settings.keep_days)

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
