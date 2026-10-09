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

from trafficpi.core import evidence_settings as ev
from trafficpi.core.clock import WallClock
from trafficpi.core.evidence_settings import EvidenceSettings
from trafficpi.core.paths import ensure_private_dir
from trafficpi.core.trail_settings import (
    DEFAULT_KEEP_DAYS,
    ENABLED_KEY,
    KEEP_DAYS_KEY,
    MAX_KEEP_DAYS,
    PEOPLE_KEY,
    TrailSettings,
)
from trafficpi.db.models import (
    Camera,
    Event,
    HealthSample,
    MinuteCount,
    Profile,
    Setting,
    Trail,
    User,
)
from trafficpi.db.session import create_db_engine, upgrade_to_head
from trafficpi.vision.health import HealthRow
from trafficpi.vision.rules import CountEvent, StopEvent
from trafficpi.vision.trails import FinishedTrail

DEFAULT_CAMERA = "default"
STOP_OUTCOMES = ("stopped", "rolling", "did_not_stop", "indeterminate")
REVIEW_STATES = ("confirmed", "false_positive")  # and None: not looked at yet


def _event_row(r: Event) -> EventRow:
    return EventRow(
        r.id,
        r.occurred_at,
        r.type,
        r.track_id,
        r.class_label,
        r.line_id,
        r.direction,
        r.details,
        r.evidence_path,
        r.review,
        r.reviewed_at,
    )


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
    review: str | None = None  # stop events: "confirmed", "false_positive", or None
    reviewed_at: int | None = None


@dataclass(frozen=True, slots=True)
class StopStatRow:
    """What the sums over stop events need of one of them (the Data page, plan.md D25)."""

    occurred_at: int  # ms since the epoch, UTC
    kind: str  # the vehicle's class label; "" if the event has none
    sign: str  # the stop sign's id; "" if the event has none
    outcome: str
    review: str | None  # "confirmed", "false_positive", or None
    hold_s: float
    min_speed: float  # in the vehicle's own sizes a second; -1 if it was not measured
    observed_s: float


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

    def record_stop(
        self, event: StopEvent, occurred_at_ms: int, *, evidence_path: str | None = None
    ) -> int:
        """Store what a vehicle did at a stop sign (plan.md D23); returns the event id.

        The sign's id is kept where a count keeps its line's, and the outcome and the numbers
        behind it are the event's details. A stop is not a count, so no totals change.
        """
        with self._session() as s:
            row = Event(
                camera_id=self.camera_id,
                occurred_at=occurred_at_ms,
                type="stop",
                track_id=event.track_id,
                class_label=event.label,
                line_id=event.sign_id,
                direction=None,
                details={
                    "outcome": event.outcome,
                    "hold_s": event.hold_s,
                    "min_speed": event.min_speed,
                    "observed_s": event.observed_s,
                },
                evidence_path=evidence_path,
            )
            s.add(row)
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
            return [_event_row(r) for r in rows]

    # --- stops: what vehicles did at stop signs, and what a person made of it -------------------

    def stops_between(
        self,
        start_ms: int,
        end_ms: int,
        *,
        outcomes: Collection[str] | None = None,
        review: str | None = None,
        sign: str | None = None,
        limit: int = 50,
        before_id: int | None = None,
    ) -> list[EventRow]:
        """Stop events in ``[start_ms, end_ms)``, newest first.

        ``review`` is "unreviewed", "confirmed" or "false_positive" (None: whichever). Pass the
        last id seen as ``before_id`` to get the next page.
        """
        query = select(Event).where(
            Event.camera_id == self.camera_id,
            Event.type == "stop",
            Event.occurred_at >= start_ms,
            Event.occurred_at < end_ms,
        )
        if outcomes is not None:
            query = query.where(Event.details["outcome"].as_string().in_(list(outcomes)))
        if review == "unreviewed":
            query = query.where(Event.review.is_(None))
        elif review is not None:
            query = query.where(Event.review == review)
        if sign is not None:
            query = query.where(Event.line_id == sign)
        if before_id is not None:
            query = query.where(Event.id < before_id)
        with self._session() as s:
            rows = s.execute(query.order_by(Event.id.desc()).limit(limit)).scalars()
            return [_event_row(r) for r in rows]

    def stop_summary(self, start_ms: int, end_ms: int) -> dict[str, dict[str, int]]:
        """For each outcome, how many stop events in the range, and how many are confirmed, marked
        as false positives, or not looked at yet. Every outcome is there, with zeros if need be."""
        outcome = Event.details["outcome"].as_string()
        with self._session() as s:
            rows = s.execute(
                select(outcome, Event.review, func.count(Event.id))
                .where(
                    Event.camera_id == self.camera_id,
                    Event.type == "stop",
                    Event.occurred_at >= start_ms,
                    Event.occurred_at < end_ms,
                )
                .group_by(outcome, Event.review)
            ).all()
        totals = {
            name: {"total": 0, "confirmed": 0, "false_positive": 0, "unreviewed": 0}
            for name in STOP_OUTCOMES
        }
        for name, review, count in rows:
            if name not in totals:
                continue  # an outcome that a newer version knew of
            totals[name]["total"] += count
            totals[name][review or "unreviewed"] += count
        return totals

    def stop_rows(
        self, start_ms: int, end_ms: int, *, sign: str | None = None
    ) -> list[StopStatRow]:
        """Every stop event in ``[start_ms, end_ms)`` (of one stop sign, if given), as the few
        fields that sums over them need; oldest first. Events without a recognised outcome are
        left to the caller to ignore."""
        details = Event.details
        query = (
            select(
                Event.occurred_at,
                Event.class_label,
                Event.line_id,
                details["outcome"].as_string(),
                Event.review,
                details["hold_s"].as_float(),
                details["min_speed"].as_float(),
                details["observed_s"].as_float(),
            )
            .where(
                Event.camera_id == self.camera_id,
                Event.type == "stop",
                Event.occurred_at >= start_ms,
                Event.occurred_at < end_ms,
            )
            .order_by(Event.occurred_at, Event.id)
        )
        if sign is not None:
            query = query.where(Event.line_id == sign)
        with self._session() as s:
            return [
                StopStatRow(
                    occurred_at=at,
                    kind=kind or "",
                    sign=line or "",
                    outcome=outcome or "",
                    review=review,
                    hold_s=0.0 if hold is None else float(hold),
                    min_speed=-1.0 if speed is None else float(speed),
                    observed_s=0.0 if watched is None else float(watched),
                )
                for at, kind, line, outcome, review, hold, speed, watched in s.execute(query)
            ]

    def first_stop_ms(self, *, sign: str | None = None) -> int | None:
        """When the earliest stop event happened (of one stop sign, if given); None if none has."""
        query = select(func.min(Event.occurred_at)).where(
            Event.camera_id == self.camera_id, Event.type == "stop"
        )
        if sign is not None:
            query = query.where(Event.line_id == sign)
        with self._session() as s:
            return s.execute(query).scalar_one_or_none()

    def first_count_s(self) -> int | None:
        """The earliest minute with a count, in seconds since the epoch; None if there is none."""
        with self._session() as s:
            return s.execute(
                select(func.min(MinuteCount.bucket_start)).where(
                    MinuteCount.camera_id == self.camera_id
                )
            ).scalar_one_or_none()

    def stop_event(self, event_id: int) -> EventRow | None:
        """One stop event, or None if there is no such one (a count has no evidence to show)."""
        with self._session() as s:
            row = s.execute(
                select(Event).where(
                    Event.id == event_id, Event.camera_id == self.camera_id, Event.type == "stop"
                )
            ).scalar_one_or_none()
            return None if row is None else _event_row(row)

    def stop_outcomes(self, event_ids: Collection[int]) -> dict[int, str]:
        """The outcome of each of these stop events (those that exist and have one)."""
        if not event_ids:
            return {}
        with self._session() as s:
            rows = s.execute(
                select(Event.id, Event.details["outcome"].as_string()).where(
                    Event.camera_id == self.camera_id,
                    Event.type == "stop",
                    Event.id.in_(list(event_ids)),
                )
            ).all()
        return {event_id: name for event_id, name in rows if name is not None}

    def attach_evidence(self, event_id: int, snapshot: str | None, clip: str | None) -> bool:
        """Record where the picture and the clip of a stop event were written (paths relative to
        the data directory; None for one that was not made). False if there is no such event."""
        with self._session() as s:
            row = s.execute(
                select(Event).where(
                    Event.id == event_id, Event.camera_id == self.camera_id, Event.type == "stop"
                )
            ).scalar_one_or_none()
            if row is None:
                return False
            details = dict(row.details or {})
            if clip is None:
                details.pop("clip", None)
            else:
                details["clip"] = clip
            row.evidence_path = snapshot
            row.details = details  # a new dict: a change inside the old one would not be saved
            return True

    def review_stop(
        self, event_id: int, state: str | None, *, now_ms: int | None = None
    ) -> EventRow | None:
        """Record what a person made of a stop event (None takes it back to not looked at).

        Returns the event as it is now, or None if there is no such stop event.
        """
        if state is not None and state not in REVIEW_STATES:
            raise ValueError(f"a review is one of {', '.join(REVIEW_STATES)}, or nothing")
        with self._session() as s:
            row = s.execute(
                select(Event).where(
                    Event.id == event_id,
                    Event.camera_id == self.camera_id,
                    Event.type == "stop",
                )
            ).scalar_one_or_none()
            if row is None:
                return None
            row.review = state
            row.reviewed_at = None if state is None else (now_ms or WallClock().now_ms())
            s.flush()
            return _event_row(row)

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

    # --- machine health, a row a minute (plan.md M8 step 4) -------------------------------------

    def add_health(self, rows: Sequence[HealthRow]) -> None:
        """Store minutes of health; a minute that is already there is replaced."""
        if not rows:
            return
        values = [
            {
                "camera_id": self.camera_id,
                "minute_start": r.minute_start,
                "temp_c_max": r.temp_c_max,
                "cpu_percent": r.cpu_percent,
                "fps_mean": r.fps_mean,
                "fps_min": r.fps_min,
                "throttled": r.throttled,
                "samples": r.samples,
                "camera_ok_samples": r.camera_ok_samples,
            }
            for r in rows
        ]
        statement = insert(HealthSample).values(values)
        replaced = {
            c.name: statement.excluded[c.name]
            for c in HealthSample.__table__.columns
            if not c.primary_key
        }
        with self._session() as s:
            s.execute(
                statement.on_conflict_do_update(
                    index_elements=["camera_id", "minute_start"], set_=replaced
                )
            )

    def health_count(self) -> int:
        with self._session() as s:
            return s.execute(
                select(func.count())
                .select_from(HealthSample)
                .where(HealthSample.camera_id == self.camera_id)
            ).scalar_one()

    def health_between(self, from_s: int, to_s: int) -> list[HealthRow]:
        """The minutes that began from ``from_s`` up to ``to_s`` (not included), oldest first."""
        with self._session() as s:
            found = s.execute(
                select(HealthSample)
                .where(
                    HealthSample.camera_id == self.camera_id,
                    HealthSample.minute_start >= from_s,
                    HealthSample.minute_start < to_s,
                )
                .order_by(HealthSample.minute_start)
            ).scalars()
            return [
                HealthRow(
                    minute_start=h.minute_start,
                    temp_c_max=h.temp_c_max,
                    cpu_percent=h.cpu_percent,
                    fps_mean=h.fps_mean,
                    fps_min=h.fps_min,
                    throttled=h.throttled,
                    samples=h.samples,
                    camera_ok_samples=h.camera_ok_samples,
                )
                for h in found
            ]

    def delete_health_before(self, cutoff_s: int, *, limit: int = 2_000) -> int:
        """Delete up to ``limit`` minutes older than ``cutoff_s``; returns how many went."""
        oldest = (
            select(HealthSample.minute_start)
            .where(HealthSample.camera_id == self.camera_id, HealthSample.minute_start < cutoff_s)
            .order_by(HealthSample.minute_start)
            .limit(limit)
        )
        with self._session() as s:
            result = cast(
                "CursorResult[Any]",
                s.execute(
                    delete(HealthSample).where(
                        HealthSample.camera_id == self.camera_id,
                        HealthSample.minute_start.in_(oldest),
                    )
                ),
            )
            return result.rowcount

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

    def get_evidence_settings(self) -> EvidenceSettings:
        """Whether evidence is kept, how long and how much; unusable stored values are ignored."""

        def whole(key: str, default: int, low: int, high: int) -> int:
            value = self.get_setting(key, default)
            usable = isinstance(value, int) and not isinstance(value, bool) and low <= value <= high
            return value if usable else default

        return EvidenceSettings(
            enabled=self.get_setting(ev.ENABLED_KEY, True) is not False,
            keep_days=whole(ev.KEEP_DAYS_KEY, ev.DEFAULT_KEEP_DAYS, 1, ev.MAX_KEEP_DAYS),
            max_mb=whole(ev.MAX_MB_KEY, ev.DEFAULT_MAX_MB, ev.MIN_MAX_MB, ev.MAX_MAX_MB),
            rolling_clip_hours=whole(
                ev.ROLLING_CLIP_HOURS_KEY, ev.DEFAULT_ROLLING_CLIP_HOURS, 1, ev.MAX_CLIP_HOURS
            ),
            did_not_stop_clip_hours=whole(
                ev.DID_NOT_STOP_CLIP_HOURS_KEY,
                ev.DEFAULT_DID_NOT_STOP_CLIP_HOURS,
                1,
                ev.MAX_CLIP_HOURS,
            ),
            # only a plain "no" turns one off, so that a damaged value never costs the owner the
            # evidence they asked for
            keep_rolling=self.get_setting(ev.KEEP_ROLLING_KEY, True) is not False,
            keep_did_not_stop=self.get_setting(ev.KEEP_DID_NOT_STOP_KEY, True) is not False,
        )

    def set_evidence_settings(self, settings: EvidenceSettings) -> None:
        self.set_setting(ev.ENABLED_KEY, settings.enabled)
        self.set_setting(ev.KEEP_DAYS_KEY, settings.keep_days)
        self.set_setting(ev.MAX_MB_KEY, settings.max_mb)
        self.set_setting(ev.ROLLING_CLIP_HOURS_KEY, settings.rolling_clip_hours)
        self.set_setting(ev.DID_NOT_STOP_CLIP_HOURS_KEY, settings.did_not_stop_clip_hours)
        self.set_setting(ev.KEEP_ROLLING_KEY, settings.keep_rolling)
        self.set_setting(ev.KEEP_DID_NOT_STOP_KEY, settings.keep_did_not_stop)

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

    def usernames(self) -> list[str]:
        """Who can log in, in alphabetical order (never their password hashes)."""
        with self._session() as s:
            return list(s.execute(select(User.username).order_by(User.username)).scalars())


def open_store(data_dir: Path) -> Store:
    """The store for the database in ``data_dir``, created and migrated as needed.

    Both services call this at start-up; migrations take turns (see ``upgrade_to_head``).
    """
    ensure_private_dir(data_dir)
    engine = create_db_engine(data_dir / "trafficpi.db")
    upgrade_to_head(engine)
    store = Store(engine)
    store.ensure_camera()
    return store
