"""Request and response shapes of the API; the dashboard's TypeScript types come from these."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class AuthState(BaseModel):
    configured: bool  # whether an admin password has been set yet


class Me(BaseModel):
    username: str


class LineTotals(BaseModel):
    id: str
    name: str
    forward: int  # crossings from the left to the right of the line's own direction
    reverse: int
    by_class: dict[str, int]


class HourCount(BaseModel):
    t: int  # start of the hour, ms since the epoch
    hour: int  # local hour of day, 0-23
    count: int


class TodaySummary(BaseModel):
    date: str  # local date, YYYY-MM-DD
    timezone: str
    total: int
    by_class: dict[str, int]
    lines: list[LineTotals]
    hourly: list[HourCount]


class LineDirections(BaseModel):
    forward: int
    reverse: int


class SeriesPoint(BaseModel):
    t: int  # start of the bucket, ms since the epoch
    local: str  # the same instant in the configured time zone, ISO 8601
    total: int
    by_class: dict[str, int]
    by_line: dict[str, LineDirections]


class EventOut(BaseModel):
    id: int
    t: int  # ms since the epoch
    type: str
    track: int | None
    label: str | None
    line: str | None
    direction: int | None


class ProfileOut(BaseModel):
    version: int  # 0 when none has been saved
    document: dict[str, Any]


class ProfileSaved(BaseModel):
    version: int
    vision_notified: bool  # whether the vision service was told to reload


class Settings(BaseModel):
    timezone: str
    units: Literal["mph", "kmh"]


class DiskStatus(BaseModel):
    free_gb: float
    total_gb: float
    used_percent: float


class VisionStatus(BaseModel):
    status: Literal["ok", "camera-offline", "unreachable"]
    fps: float | None = None
    uptime_s: float | None = None
    camera_error: str | None = None
    profile_version: int | None = None
    detector: str | None = None
    system: dict[str, Any] = {}


class StatusResponse(BaseModel):
    vision: VisionStatus
    disk: DiskStatus
    events_total: int
    now_ms: int
    timezone: str
