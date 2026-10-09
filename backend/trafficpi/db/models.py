"""Database tables.

Times are UTC: ``occurred_at`` is milliseconds since the epoch, ``bucket_start`` whole seconds
since the epoch aligned to a minute. Every table that belongs to a camera carries its id, so
several cameras can be added later without a schema change.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import (
    JSON,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    UniqueConstraint,
)
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


class HealthSample(Base):
    """How the machine was in one minute: temperature, load, frame rate, throttle flags and
    whether the camera worked (see ``vision/health.py``); kept for 30 days."""

    __tablename__ = "health_samples"

    camera_id: Mapped[str] = mapped_column(ForeignKey("cameras.id"), primary_key=True)
    minute_start: Mapped[int] = mapped_column(Integer, primary_key=True)  # s, on a minute
    temp_c_max: Mapped[float | None] = mapped_column(Float)
    cpu_percent: Mapped[float | None] = mapped_column(Float)
    fps_mean: Mapped[float | None] = mapped_column(Float)
    fps_min: Mapped[float | None] = mapped_column(Float)
    throttled: Mapped[int] = mapped_column(
        Integer
    )  # bits 0 to 3 of get_throttled seen in the minute
    samples: Mapped[int] = mapped_column(Integer)
    camera_ok_samples: Mapped[int] = mapped_column(Integer)


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


class MailRecipient(Base):
    """Someone who gets the daily email (plan.md D32). Removing one removes their deliveries too:
    an address that is taken off the list leaves no trace behind it."""

    __tablename__ = "mail_recipients"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    address: Mapped[str] = mapped_column(String, unique=True)  # lower case
    added_at: Mapped[int] = mapped_column(Integer)  # ms
    # The last thing tried with this address, a daily mail or a test: when, and how it went.
    last_at: Mapped[int | None] = mapped_column(Integer)  # ms
    last_kind: Mapped[str | None] = mapped_column(String)  # "daily" or "test"
    last_status: Mapped[str | None] = mapped_column(String)  # "sent" or "failed"
    last_detail: Mapped[str | None] = mapped_column(String)  # the reason, when it failed


class MailSend(Base):
    """One scheduled daily email: the stretch it covers and the mail as it was made, kept so that
    every recipient, and every retry, gets the same words. One row for each scheduled instant."""

    __tablename__ = "mail_sends"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    scheduled_at: Mapped[int] = mapped_column(Integer, unique=True)  # ms: the instant it was for
    period_start: Mapped[int] = mapped_column(Integer)  # ms
    period_end: Mapped[int] = mapped_column(Integer)  # ms
    created_at: Mapped[int] = mapped_column(Integer)  # ms
    status: Mapped[str] = mapped_column(String)  # "sending", "done" or "skipped"
    note: Mapped[str | None] = mapped_column(String)  # why it was skipped
    sender: Mapped[str] = mapped_column(String)
    subject: Mapped[str] = mapped_column(String)
    body_text: Mapped[str] = mapped_column(String)
    body_html: Mapped[str] = mapped_column(String)


class MailDelivery(Base):
    """One recipient's copy of one daily email, and how far it got."""

    __tablename__ = "mail_deliveries"
    __table_args__ = (
        UniqueConstraint("send_id", "recipient_id"),  # each recipient gets each mail once
        Index("ix_mail_deliveries_due", "status", "next_attempt_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    send_id: Mapped[int] = mapped_column(ForeignKey("mail_sends.id", ondelete="CASCADE"))
    recipient_id: Mapped[int] = mapped_column(ForeignKey("mail_recipients.id", ondelete="CASCADE"))
    # "pending", "sending" (claimed by a send in progress), "sent", "failed" or "cancelled"
    status: Mapped[str] = mapped_column(String)
    attempts: Mapped[int] = mapped_column(Integer)
    next_attempt_at: Mapped[int | None] = mapped_column(Integer)  # ms; when a claim was made, too
    last_error: Mapped[str | None] = mapped_column(String)
    provider_id: Mapped[str | None] = mapped_column(String)
    sent_at: Mapped[int | None] = mapped_column(Integer)  # ms
