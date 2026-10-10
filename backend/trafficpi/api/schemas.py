"""Request and response shapes of the API; the dashboard's TypeScript types come from these."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field, SecretStr

from trafficpi.core.evidence_settings import MAX_CLIP_HOURS, MAX_MAX_MB, MIN_MAX_MB
from trafficpi.core.evidence_settings import MAX_KEEP_DAYS as MAX_EVIDENCE_DAYS
from trafficpi.core.profile import Point, Profile
from trafficpi.core.stop_hours import MAX_MARGIN_MIN, MIN_MARGIN_MIN
from trafficpi.core.stop_hours import MAX_RANGES as MAX_STOP_HOURS_RANGES
from trafficpi.core.trail_settings import MAX_KEEP_DAYS


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class AuthState(BaseModel):
    configured: bool  # whether an admin password has been set yet


class Me(BaseModel):
    username: str
    # Whether the request came through Cloudflare (plan.md D28), and whether this viewer may open
    # the continuous live stream: always on the LAN, and from outside only if the owner allowed it.
    remote: bool = False
    live_video: bool = True


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
    removed: int  # of the total, counted by lines that have been deleted since (not in `lines`)
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


StopOutcome = Literal["stopped", "rolling", "did_not_stop", "indeterminate"]
ReviewState = Literal["confirmed", "false_positive"]


class StopOut(BaseModel):
    """What one vehicle did at one stop sign (plan.md D23), and what a person made of it."""

    id: int
    t: int  # ms since the epoch: when it crossed the stop line
    sign: str  # the stop sign's id (the name is in the profile; a deleted sign's events remain)
    label: str | None  # the kind of vehicle
    outcome: StopOutcome
    hold_s: float  # the longest it was still in the approach zone
    min_speed: float  # the slowest it went there, in its own sizes a second; -1 if not measured
    observed_s: float  # how long it was watched in the zone before the line
    # With a calibrated camera (plan.md M6), in metres a second as the speed meter measured them
    # while the vehicle was in the approach zone: the slowest, the fastest, and the last one before
    # the line. None where the camera was not calibrated or the speed could not be believed.
    min_speed_ms: float | None
    max_speed_ms: float | None
    line_speed_ms: float | None
    review: ReviewState | None  # None: not looked at yet
    reviewed_at: int | None
    snapshot_url: str | None  # the picture of the moment it crossed the line; None: none is kept
    clip_url: str | None  # the clip around that moment; None: none is kept


class StopTotals(BaseModel):
    """How many stop events had one outcome, and how many of them a person has looked at."""

    outcome: StopOutcome
    total: int
    confirmed: int
    false_positive: int
    unreviewed: int


class StopSummary(BaseModel):
    from_ms: int
    to_ms: int
    outcomes: list[
        StopTotals
    ]  # all four, in the order stopped, rolling, did_not_stop, indeterminate


class OutcomeCounts(BaseModel):
    """How many passes at stop signs had each outcome; passes called false alarms are not in it."""

    stopped: int
    rolling: int
    did_not_stop: int
    indeterminate: int


class HourOutcomes(OutcomeCounts):
    hour: int  # local hour of the day, 0 to 23, over every day of the period


class SpeedEstimate(BaseModel):
    """The fastest speed in some time. Without a calibration it is an estimate from the vehicle's
    own size, good to about 40% (plan.md D41); with one (``calibrated``) it was measured in metres
    on the road, and a calibrated speed always beats an estimate, whatever their sizes (M6)."""

    speed_ms: float  # metres a second, to a tenth
    label: str  # the kind of vehicle it was
    at: int  # when it passed, ms since the epoch
    calibrated: bool  # measured on the calibrated road, and not estimated from the vehicle's size


class DayOutcomes(OutcomeCounts):
    date: str  # the local day, YYYY-MM-DD
    t: int  # when that local day began, ms since the epoch
    fastest: SpeedEstimate | None  # the fastest that day; None if no pass of it had a speed


class WeekdayOutcomes(OutcomeCounts):
    weekday: int  # 0 is Monday, 6 is Sunday


class KindOutcomes(OutcomeCounts):
    label: str  # the kind of vehicle


class SignOutcomes(OutcomeCounts):
    sign: str  # the stop sign's id (the name is in the profile)


class StopTiming(BaseModel):
    """What a typical pass looked like, as the middle value (None where there is none)."""

    stopped_hold_s: float | None  # how long a vehicle that stopped stood still
    rolling_min_speed: (
        float | None
    )  # how slowly one that rolled through went, in its sizes a second
    did_not_stop_min_speed: float | None  # and one that did not stop
    # The same two with a calibrated camera, in metres a second, over the passes it measured (None
    # where there are none): the slowest a vehicle that rolled through went, and one that did not
    # stop.
    rolling_min_speed_ms: float | None
    did_not_stop_min_speed_ms: float | None


class StopReviews(BaseModel):
    confirmed: int  # passes a person agreed with
    false_alarms: int  # passes a person called false alarms (left out of every count above)
    waiting: int  # passes that need a look (not a clean stop) and have not had one


class StopStats(BaseModel):
    """How vehicles did at the stop signs over some local days (plan.md D25)."""

    timezone: str
    units: Literal["mph", "kmh"]  # the owner's choice, for speeds shown in words
    from_ms: int  # the period begins: local midnight of its first day
    to_ms: int  # and ends: local midnight at the end of today
    days: int  # local days in it, today included
    all_time: bool  # asked for "all time": from the first pass (a year at most)
    sign: str | None  # only this stop sign, if asked
    totals: OutcomeCounts
    outside_hours: int  # of the passes in totals, those outside the hours judged now (plan.md D42)
    previous: OutcomeCounts | None  # the same number of days before it; None for all time
    by_hour: list[HourOutcomes]  # all 24
    by_day: list[DayOutcomes]  # every day of the period, oldest first, empty ones included
    fastest: SpeedEstimate | None  # the fastest of the period (the earlier day on a tie)
    by_weekday: list[WeekdayOutcomes]  # all 7, Monday first
    by_kind: list[KindOutcomes]  # kinds with passes, most passes first
    by_sign: list[SignOutcomes]  # stop signs with passes, most passes first
    timing: StopTiming
    review: StopReviews


class SpeedFigures(BaseModel):
    """What can be said of some speeds, in the units shown and to a tenth of one."""

    count: int  # how many vehicles had a speed
    mean: float
    median: float
    p85: float  # the speed that 85% of the vehicles did not exceed
    fastest: float
    slowest: float


class SpeedBin(BaseModel):
    low: float  # a bar of the histogram: from here (inclusive)
    high: float  # to here (exclusive), in the units shown
    count: int


class SpeedPeriod(BaseModel):
    """The speeds in one hour of the day or one day."""

    count: int
    mean: float | None
    p85: float | None


class SpeedHour(SpeedPeriod):
    hour: int  # local hour of the day, 0 to 23, over every day of the period


class SpeedDay(SpeedPeriod):
    date: str  # the local day, YYYY-MM-DD
    t: int  # when that local day began, ms since the epoch


class SpeedLine(BaseModel):
    line: str  # the counting line's id
    name: str  # its name now; empty if it has been deleted
    counted: int  # vehicles it counted in the period, measured or not
    figures: SpeedFigures | None  # None: none of them had a speed


class SpeedKind(BaseModel):
    label: str  # the kind of vehicle
    figures: SpeedFigures | None


class SpeedStats(BaseModel):
    """How fast the vehicles counted at the counting lines were going (plan.md M6). A speed is
    given only for a vehicle seen for long enough and in a part of the picture the calibration
    can be believed, so ``counted`` is more than ``figures.count`` and the difference is shown."""

    timezone: str
    units: Literal["mph", "kmh"]  # the owner's choice; every speed here is in them
    from_ms: int
    to_ms: int
    days: int
    all_time: bool
    line: str | None  # only this counting line, if asked
    counted: int  # vehicles counted in the period (at that line), with a speed or without
    calibration: str | None  # the name of the calibration in force; None: the camera has none
    # How many counts have a speed that was measured under an earlier calibration: they are left
    # out of every figure here, so that a corrected rectangle does not average with a wrong one.
    excluded: int
    figures: SpeedFigures | None  # None: nothing was measured in the period
    bins: list[SpeedBin]  # from zero up to the bar of the fastest, empty bars included
    by_hour: list[SpeedHour]  # all 24
    by_day: list[SpeedDay]  # every day of the period, oldest first, empty ones included
    by_line: list[SpeedLine]  # most vehicles counted first
    by_kind: list[SpeedKind]  # kinds with a speed, most first


class TrafficHour(BaseModel):
    hour: int  # local hour of the day, 0 to 23, over every day of the period
    total: int
    by_class: dict[str, int]


class TrafficDay(BaseModel):
    date: str  # the local day, YYYY-MM-DD
    t: int  # when that local day began, ms since the epoch
    total: int
    by_class: dict[str, int]


class TrafficWeekday(BaseModel):
    weekday: int  # 0 is Monday, 6 is Sunday
    total: int


class TrafficStats(BaseModel):
    """Vehicles counted over some local days (plan.md D25)."""

    timezone: str
    from_ms: int
    to_ms: int
    days: int
    all_time: bool
    total: int
    by_class: dict[str, int]
    removed: int  # of the total, counted by lines that have been deleted since (not in `lines`)
    lines: list[LineTotals]
    by_hour: list[TrafficHour]  # all 24
    by_day: list[TrafficDay]  # every day of the period, oldest first, empty ones included
    by_weekday: list[TrafficWeekday]  # all 7, Monday first


class ReviewRequest(BaseModel):
    """What a person made of a stop event; null takes the review back."""

    state: ReviewState | None


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


class CalibrationProbe(BaseModel):
    """A place in the picture to say what the calibration is at (a stop line, a counting line)."""

    id: str = Field(min_length=1, max_length=64)
    point: Point  # x and y as fractions of the picture, from 0 to 1


class CalibrationRequest(BaseModel):
    """A calibration that may not be saved yet, and places to ask about."""

    calibration: object  # the profile's calibration (corners, width_m, length_m, checks)
    probes: list[CalibrationProbe] = Field(default_factory=list, max_length=64)


class CalibrationCheckResult(BaseModel):
    """What the calibrated map finds for a distance the owner knows."""

    id: str
    entered_m: float  # the length that was typed
    measured_m: float | None  # the length the map finds; None if an end is beyond the horizon
    error_pct: float | None  # how far that is from the typed one, in %, negative when short
    expected_pct: float | None  # how far the map is expected to be out for it (1 sigma), in %
    agrees: bool | None  # within twice what is expected, and what a typed length may be out by


class CalibrationProbeResult(BaseModel):
    id: str
    metres_per_pixel_along: float | None  # one pixel of a 1280 pixel wide picture, along the road
    metres_per_pixel_across: float | None  # and across it; None beyond the horizon
    error_along_pct: float | None  # how far a distance driven along the road can be believed
    error_across_pct: float | None  # and one driven across it (1 sigma, in %); None: not at all
    believed: bool  # whether a vehicle driving along the road here is given a speed


class CalibrationGrid(BaseModel):
    """How far a distance can be believed over the picture, in %, row by row from the top."""

    columns: int
    rows: int
    along_pct: list[list[float | None]]  # None where the road does not reach
    across_pct: list[list[float | None]]


class CalibrationReport(BaseModel):
    id: str  # the name speeds are stored under
    limit_pct: float  # a speed is given only where the error in the direction of travel is below
    centre_error_pct: float | None  # along the road, at the middle of the rectangle
    checks: list[CalibrationCheckResult]
    probes: list[CalibrationProbeResult]
    grid: CalibrationGrid


class CalibrationAnswer(BaseModel):
    ok: bool
    problem: str | None  # why the calibration cannot be used, when it cannot
    report: CalibrationReport | None


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
    # The same for the picture and clip kept of a stop-sign pass that needs a look (plan.md D24).
    evidence_enabled: bool = Field(strict=True)
    evidence_keep_days: int = Field(strict=True, ge=1, le=MAX_EVIDENCE_DAYS)
    evidence_max_mb: int = Field(strict=True, ge=MIN_MAX_MB, le=MAX_MAX_MB)  # the oldest goes first
    # How long the clip (the picture stays as long as the days above say) of a roll, and of a pass
    # that did not stop, is kept; sooner than the days, to spare the disk. The counts stay for ever.
    evidence_rolling_clip_hours: int = Field(strict=True, ge=1, le=MAX_CLIP_HOURS)
    evidence_did_not_stop_clip_hours: int = Field(strict=True, ge=1, le=MAX_CLIP_HOURS)
    # Whether a picture and a clip are kept of a roll, and of a pass that did not stop. Off: the
    # pass is still judged and counted, only its evidence is not made (plan.md D33).
    evidence_keep_rolling: bool = Field(strict=True)
    evidence_keep_did_not_stop: bool = Field(strict=True)
    # May viewers who come through Cloudflare open the continuous live stream? Off: Cloudflare's
    # terms let it limit video on its CDN and do not say whether a Tunnel counts (plan.md D28).
    remote_live_video: bool = Field(strict=True)
    # Does the camera look for people at all? Off: no box, no track, no trail and no count for them
    # (plan.md D34). Vehicles are unaffected.
    detect_people: bool = Field(strict=True)


CLOCK_PATTERN = r"^([01]\d|2[0-3]):[0-5]\d$"  # HH:MM on a 24-hour clock


class StopHoursRange(BaseModel):
    """From ``start`` to just before ``end`` on the camera's clock; ``end`` before ``start`` runs
    through midnight."""

    start: str = Field(strict=True, pattern=CLOCK_PATTERN)
    end: str = Field(strict=True, pattern=CLOCK_PATTERN)


class StopHoursConfig(BaseModel):
    """When stop signs are judged (plan.md D42). A PUT replaces all of it, so every member is given:
    a client that does not know one cannot reset it by leaving it out. Checked in full again by
    ``StopHours``."""

    daylight: bool = Field(strict=True)  # limit 1: only from before sunrise to after sunset
    latitude: float | None = Field(strict=True, ge=-90, le=90)  # the camera's place; kept while
    longitude: float | None = Field(strict=True, ge=-180, le=180)  # the limit is off; or null
    before_sunrise_min: int = Field(strict=True, ge=MIN_MARGIN_MIN, le=MAX_MARGIN_MIN)
    after_sunset_min: int = Field(strict=True, ge=MIN_MARGIN_MIN, le=MAX_MARGIN_MIN)
    clock: bool = Field(strict=True)  # limit 2: only in these ranges of the day, on these days
    ranges: list[StopHoursRange] = Field(min_length=1, max_length=MAX_STOP_HOURS_RANGES)
    days: list[Annotated[int, Field(strict=True, ge=0, le=6)]] = Field(min_length=1, max_length=14)


class StopHoursDay(BaseModel):
    """The sun on one date of the camera's clock, at the place in the config."""

    date: str  # YYYY-MM-DD
    sunrise: int | None  # ms since the epoch; null on a day the sun does not rise (or does not set)
    sunset: int | None
    start: int | None  # the daylight window: sunrise less the margin ...
    end: int | None  # ... to sunset plus the margin
    polar: Literal["day", "night"] | None  # the sun stays up ("day") or down ("night") all day
    noon_offset_min: int  # minutes from 12:00 on the camera's clock to the sun's highest point


class StopHoursStatus(BaseModel):
    """What the hours in a config mean right now."""

    now: int  # ms since the epoch
    timezone: str  # the camera's, which "today" and the set hours go by
    limited: bool  # some pass is turned away at some time of day
    judging: bool  # whether a pass at ``now`` would be judged
    next_change: int | None  # ms: when ``judging`` next changes; null if it does not within days
    description: str  # the limits in words ("at all hours" when there are none)
    day: StopHoursDay | None  # null until a place is given


class StopHoursState(BaseModel):
    config: StopHoursConfig
    status: StopHoursStatus


class StopHoursSaved(StopHoursState):
    """What a save answers: the new state, and whether the vision service was told to read it. If
    it could not be reached, the hours are saved all the same and it reads them when it starts."""

    vision_notified: bool


class TrailHour(BaseModel):
    t: int  # ms since the epoch: when the hour began
    end: int  # and when it ended: an hour on, or sooner where the day ends
    hour: int  # on the local clock, 0 to 23 (twice, for the hour that repeats when clocks go back)
    count: int  # trails that began in it
    by_kind: dict[str, int]


class TrailDay(BaseModel):
    """A local day by the hour: how many vehicles' trails began when, to pick an hour to look at."""

    day: str  # YYYY-MM-DD, in the configured time zone
    timezone: str
    total: int
    first_day: str | None  # the days that have any trails run from this one ...
    last_day: str | None  # ... to this one; both None when there are none yet
    hours: list[TrailHour]  # 24 of them, or 23 or 25 across a clock change


class TrailPath(BaseModel):
    id: int
    started_at: int  # ms since the epoch
    ended_at: int
    label: str  # car, truck, ...
    x: list[int]  # the path, as whole numbers from 0 to `scale` across the picture
    y: list[int]  # ... and down it


class TrailPaths(BaseModel):
    from_ms: int
    to_ms: int
    total: int  # how many trails matched
    returned: int  # how many are in `trails`: an even sample if more than the limit matched
    scale: int  # divide x and y by this to get a fraction of the picture
    trails: list[TrailPath]


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


class AcceleratorStatus(BaseModel):
    """What the vision service knows about the accelerator chip (None where it does not know)."""

    architecture: str | None = None  # HAILO8L
    firmware: str | None = None  # 4.23.0
    model: str | None = None  # the compiled model in use, yolov8s_h8l
    inference_ms: float | None = None  # how long one picture takes on the chip, lately
    inference_p95_ms: float | None = None


class VisionStatus(BaseModel):
    status: Literal["ok", "camera-offline", "unreachable"]
    fps: float | None = None
    uptime_s: float | None = None
    camera_error: str | None = None
    profile_version: int | None = None
    detector: str | None = None
    accelerator: AcceleratorStatus = AcceleratorStatus()
    system: SystemMetrics = SystemMetrics()


class DataUsage(BaseModel):
    """What the data directory holds, in megabytes (a thousand to the gigabyte)."""

    database_mb: float
    evidence_mb: float
    backups_mb: float
    evidence_files: int
    backup_files: int


class BackupInfo(BaseModel):
    at_ms: int  # when the newest one was made
    size_mb: float
    count: int  # how many are kept in the usual folder


class MemoryTest(BaseModel):
    """What the installer's memory test found; a bad result means the board's other results
    cannot be trusted (plan.md R16)."""

    at_ms: int
    mb: int
    ok: bool


class HealthPoint(BaseModel):
    """The machine over one stretch of the history chart (a minute to four hours long)."""

    t: int  # seconds since the epoch, where the stretch begins
    minutes: int  # how many minutes of readings it rests on; 0: nothing was recorded then
    temp_c: float | None = None  # the hottest minute
    cpu_percent: float | None = None
    fps: float | None = None  # while the camera worked
    fps_min: float | None = None  # the slowest minute
    camera_up_percent: float | None = None
    throttled: int  # the Pi's flags (bits 0 to 3 of ``get_throttled``) seen at any time in it


class HealthEpisode(BaseModel):
    """Something that went on for a while and is worth a sentence."""

    kind: Literal["slowed", "under-voltage", "camera-offline", "no-data"]
    from_s: int
    to_s: int
    ongoing: bool  # it reaches the latest minute that was recorded (or, for no-data, now)
    peak_temp_c: float | None = None  # for "slowed": the hottest it got


class HealthSummary(BaseModel):
    minutes: int
    temp_c_max: float | None = None
    camera_up_percent: float | None = None
    slowed_minutes: int
    under_voltage_minutes: int


class SystemHistory(BaseModel):
    from_s: int
    to_s: int
    bucket_s: int  # how long one point is
    timezone: str  # for the clock times on the chart
    points: list[HealthPoint]
    episodes: list[HealthEpisode]  # newest first, at most 50
    summary: HealthSummary


class StatusResponse(BaseModel):
    vision: VisionStatus
    disk: DiskStatus
    api_uptime_s: float
    data: DataUsage
    last_backup: BackupInfo | None = None
    memory_test: MemoryTest | None = None
    events_total: int
    now_ms: int
    timezone: str


# --- the daily email (plan.md D32) --------------------------------------------------------------


class MailScheduleBody(BaseModel):
    """When the email goes and who it is from. A PUT gives every member: a client that does not
    know one cannot reset it by leaving it out. Checked in full by ``MailSchedule``."""

    enabled: bool = Field(strict=True)
    time: str  # HH:MM in the dashboard's time zone
    days: list[Annotated[int, Field(strict=True)]]  # Monday 0 to Sunday 6
    sender_name: str
    sender_address: str
    link_url: str  # empty for no link


class MailRecipientOut(BaseModel):
    id: int
    address: str
    added_at: int  # ms since the epoch
    last_at: int | None  # the last try with this address, a daily mail or a test
    last_kind: Literal["daily", "test"] | None
    last_status: Literal["sent", "failed", "retrying"] | None
    last_detail: str | None  # why it did not get through, in the provider's words


class MailSendOut(BaseModel):
    """One scheduled mail, for the list of what was sent."""

    id: int
    scheduled_at: int  # ms: the time it was for
    period_start: int
    period_end: int
    status: Literal["sending", "done", "skipped"]
    note: str | None  # why it was skipped
    subject: str
    sent: int
    failed: int
    pending: int
    cancelled: int


class MailState(BaseModel):
    schedule: MailScheduleBody
    recipients: list[MailRecipientOut]
    key_set: bool  # whether a provider key is stored: the key itself is never given out
    transport: Literal["resend", "file"]  # "file" writes mails to a folder instead (demo)
    ready: bool  # switched on, and nothing is missing
    problems: list[str]  # what is missing before it can send, in words
    next_at: int | None  # ms: the next scheduled time, when it is on
    timezone: str
    recent: list[MailSendOut]  # the latest scheduled mails, newest first
    max_recipients: int


class MailAddressBody(BaseModel):
    address: str = Field(max_length=300)


class MailKeyBody(BaseModel):
    """A secret never shows in a log line or a traceback: ``SecretStr`` prints as stars."""

    key: SecretStr


class MailTestResult(BaseModel):
    ok: bool
    detail: str  # the reason, when it did not get through


class MailPreview(BaseModel):
    subject: str
    text: str
    html: str
