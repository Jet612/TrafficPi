"""Database tables.

Times are UTC: ``occurred_at`` is milliseconds since the epoch, ``bucket_start`` whole seconds
since the epoch aligned to a minute. Every table that belongs to a camera carries its id, so
several cameras can be added later without a schema change.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import JSON, Float, ForeignKey, Index, Integer, LargeBinary, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Camera(Base):
    __tablename__ = "cameras"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String)
    created_at: Mapped[int] = mapped_column(Integer)  # ms


class Event(Base):
    """Something a rule noticed: a count now, stop-sign and speed events later."""

    __tablename__ = "events"
    __table_args__ = (
        Index("ix_events_camera_time", "camera_id", "occurred_at"),
        Index("ix_events_type_time", "type", "occurred_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    camera_id: Mapped[str] = mapped_column(ForeignKey("cameras.id"))
    occurred_at: Mapped[int] = mapped_column(Integer)  # ms
    type: Mapped[str] = mapped_column(String)  # "count" or "stop"
    track_id: Mapped[int | None] = mapped_column(Integer)
    class_label: Mapped[str | None] = mapped_column(String)
    line_id: Mapped[str | None] = mapped_column(String)
    direction: Mapped[int | None] = mapped_column(Integer)
    details: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    evidence_path: Mapped[str | None] = mapped_column(
        String
    )  # snapshot or clip, relative to the data dir
    # What a person made of a stop event: "confirmed", "false_positive", or None (not looked at).
    review: Mapped[str | None] = mapped_column(String)
    reviewed_at: Mapped[int | None] = mapped_column(Integer)  # ms


class MinuteCount(Base):
    """Counts per minute, so charts never have to scan individual events."""

    __tablename__ = "minute_counts"

    camera_id: Mapped[str] = mapped_column(ForeignKey("cameras.id"), primary_key=True)
    bucket_start: Mapped[int] = mapped_column(Integer, primary_key=True)  # s, minute-aligned
    line_id: Mapped[str] = mapped_column(String, primary_key=True)
    direction: Mapped[int] = mapped_column(Integer, primary_key=True)
    class_label: Mapped[str] = mapped_column(String, primary_key=True)
    count: Mapped[int] = mapped_column(Integer)


class Trail(Base):
    """The path one vehicle took, saved when its track ended (see ``vision/trails.py``)."""

    __tablename__ = "trails"
    __table_args__ = (Index("ix_trails_camera_time", "camera_id", "started_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    camera_id: Mapped[str] = mapped_column(ForeignKey("cameras.id"))
    started_at: Mapped[int] = mapped_column(Integer)  # ms
    ended_at: Mapped[int] = mapped_column(Integer)  # ms
    class_label: Mapped[str] = mapped_column(String)
    points: Mapped[int] = mapped_column(Integer)
    extent: Mapped[float] = mapped_column(Float)  # how far the path spreads, as a picture fraction
    path: Mapped[bytes] = mapped_column(LargeBinary)  # ``encode_path`` in vision/trails.py


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String, unique=True)
    password_hash: Mapped[str] = mapped_column(String)
    created_at: Mapped[int] = mapped_column(Integer)  # ms


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String, primary_key=True)
    value: Mapped[Any] = mapped_column(JSON, nullable=False)


class Profile(Base):
    """A camera's configuration document (counting lines now; zones and rules later)."""

    __tablename__ = "profiles"

    camera_id: Mapped[str] = mapped_column(ForeignKey("cameras.id"), primary_key=True)
    document: Mapped[dict[str, Any]] = mapped_column(JSON)
    version: Mapped[int] = mapped_column(Integer)  # incremented on every change
    updated_at: Mapped[int] = mapped_column(Integer)  # ms
