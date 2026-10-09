"""Request and response shapes of the API; the dashboard's TypeScript types come from these."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from trafficpi.core.profile import Profile
from trafficpi.core.trail_settings import MAX_KEEP_DAYS


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
    version: int  # counts saves; 0 when none has been saved
    profile: Profile
    # Why the saved document could not be read, when it could not. The profile above is then an
    # empty one, and saving replaces the unreadable document.
    problem: str | None
    reference_at: int | None  # when the reference picture was taken, ms since the epoch; or None


class ProfileSaved(BaseModel):
    version: int
    vision_notified: bool  # whether the vision service was told to reload


class ProfileCheck(BaseModel):
    """The answer to "is this document a usable profile?", with the profile it comes to."""

    ok: bool
    problem: str | None  # what is wrong, when something is
    profile: Profile | None  # the document in canonical form, when it is usable


class ReferenceSaved(BaseModel):
    reference_at: int  # ms since the epoch


class Settings(BaseModel):
    """Everything the Setup page can change. A PUT replaces all of it, so every member is given."""

    timezone: str
    units: Literal["mph", "kmh"]
    # Strict, so that a stray `true` is not read as the number of days, nor "yes" as a switch.
    trails_enabled: bool = Field(strict=True)  # save the path each vehicle took (plan.md D21)
    trails_people: bool = Field(strict=True)  # and people's paths too (vehicles' only, if not)
    trails_keep_days: int = Field(strict=True, ge=1, le=MAX_KEEP_DAYS)  # older ones are deleted


class DiskStatus(BaseModel):
    free_gb: float
    total_gb: float
    used_percent: float


class SystemMetrics(BaseModel):
    """What the vision service measures about the machine, every few seconds."""

    cpu_cores: float | None = None  # CPU used by the vision service, in cores
    cpu_percent: float | None = None  # CPU used by the whole machine
    rss_mb: float | None = None  # memory held by the vision service
    soc_temp_c: float | None = None
    throttled_now: list[str] = []  # why the Pi is being slowed down right now, if it is
    throttled_since_boot: list[str] = []


class VisionStatus(BaseModel):
    status: Literal["ok", "camera-offline", "unreachable"]
    fps: float | None = None
    uptime_s: float | None = None
    camera_error: str | None = None
    profile_version: int | None = None
    detector: str | None = None
    system: SystemMetrics = SystemMetrics()


class StatusResponse(BaseModel):
    vision: VisionStatus
    disk: DiskStatus
    events_total: int
    now_ms: int
    timezone: str
