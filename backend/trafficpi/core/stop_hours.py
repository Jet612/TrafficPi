"""When stop signs are judged (plan.md D42).

The hours in which stop signs are judged can be limited. Outside them a vehicle is not judged at
all: no row in the database, no picture, no clip, nothing on the Data page or in the email. The
vehicles are still counted at the counting lines and their trails still saved: only the stop-sign
stats stop.

There are two limits, each switched on by itself, and both must hold for a pass to be judged:

* **In daylight**: from some minutes before sunrise to some minutes after sunset, where the camera
  is. It follows the seasons by itself. It needs the camera's latitude and longitude (``sun.py``).
* **In set hours**: one or more ranges of the day on the camera's clock, on chosen days of the
  week. A range may run through midnight; it belongs to the day it starts on.

With neither on, every pass is judged, as it always was.

The settings live in the database's settings table, like the others, so that the dashboard can
change them and the vision service picks the change up when it is told to reload. What is stored
is read leniently (``read_stop_hours``): a value that cannot be used is ignored, and a limit that
cannot work is left off, so that damage judges more passes and never fewer.
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, tzinfo
from typing import Any, Literal

from trafficpi.core.sun import MS_PER_DAY, solar_day_label, sun_day

DAYLIGHT_KEY = "stop_hours_daylight"
LATITUDE_KEY = "stop_hours_latitude"
LONGITUDE_KEY = "stop_hours_longitude"
BEFORE_SUNRISE_KEY = "stop_hours_before_sunrise_min"
AFTER_SUNSET_KEY = "stop_hours_after_sunset_min"
CLOCK_KEY = "stop_hours_clock"
RANGES_KEY = "stop_hours_ranges"
DAYS_KEY = "stop_hours_days"

DEFAULT_MARGIN_MIN = 30  # 30 minutes before sunrise to 30 minutes after sunset
MIN_MARGIN_MIN = -120  # a negative margin starts later (or stops sooner) than the sun
MAX_MARGIN_MIN = 180
MAX_RANGES = 6
ALL_DAYS = frozenset(range(7))  # Monday is 0, as in the daily email and Python's ``weekday()``
LOOK_AHEAD_DAYS = 9  # how far ``next_change_ms`` looks: a week of closed days, and a little more

_MS_PER_MINUTE = 60_000
_CLOCK = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


def parse_clock(text: str) -> int:
    """Minutes after midnight of a ``HH:MM`` time of day (24-hour clock); else it is refused."""
    found = _CLOCK.match(text) if isinstance(text, str) else None
    if found is None:
        raise ValueError("A time of day must be written HH:MM, like 07:00.")
    return int(found.group(1)) * 60 + int(found.group(2))


def format_clock(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


@dataclass(frozen=True, slots=True)
class TimeRange:
    """From ``start`` to just before ``end``, in minutes after midnight on the camera's clock.

    ``end`` before ``start`` is a range through midnight (22:00 to 06:00); it belongs to the day it
    starts on. The same time twice is refused: it could mean no time or all of it."""

    start: int
    end: int

    def __post_init__(self) -> None:
        for minutes in (self.start, self.end):
            if not 0 <= minutes < 24 * 60:
                raise ValueError("A time of day must be between 00:00 and 23:59.")
        if self.start == self.end:
            raise ValueError("A range of hours cannot start and end at the same time.")

    @property
    def through_midnight(self) -> bool:
        return self.end < self.start


DEFAULT_RANGES = (TimeRange(7 * 60, 19 * 60),)


@dataclass(frozen=True, slots=True)
class StopHours:
    daylight: bool = False  # limit 1: judge only from before sunrise to after sunset
    latitude: float | None = None  # the camera's place, north positive; kept while the limit is off
    longitude: float | None = None  # and east positive
    before_sunrise_min: int = DEFAULT_MARGIN_MIN  # start this long before sunrise (< 0: after it)
    after_sunset_min: int = DEFAULT_MARGIN_MIN  # and stop this long after sunset (< 0: before it)
    clock: bool = False  # limit 2: judge only in these ranges of the day ...
    ranges: tuple[TimeRange, ...] = DEFAULT_RANGES
    days: frozenset[int] = ALL_DAYS  # ... on these days of the week (Monday is 0)

    def __post_init__(self) -> None:
        if (self.latitude is None) != (self.longitude is None):
            raise ValueError("Enter both latitude and longitude, or neither.")
        if self.latitude is not None and self.longitude is not None:
            if not -90.0 <= self.latitude <= 90.0:
                raise ValueError("The latitude must be between -90 and 90 degrees.")
            if not -180.0 <= self.longitude <= 180.0:
                raise ValueError("The longitude must be between -180 and 180 degrees.")
        if self.daylight and self.latitude is None:
            raise ValueError("Judging only in daylight needs the camera's latitude and longitude.")
        for margin in (self.before_sunrise_min, self.after_sunset_min):
            if not MIN_MARGIN_MIN <= margin <= MAX_MARGIN_MIN:
                raise ValueError(
                    f"A margin must be from {MIN_MARGIN_MIN} to {MAX_MARGIN_MIN} minutes."
                )
        if not self.ranges:
            raise ValueError("Give at least one range of hours.")
        if len(self.ranges) > MAX_RANGES:
            raise ValueError(f"Give at most {MAX_RANGES} ranges of hours.")
        if not self.days:
            raise ValueError("Pick at least one day of the week.")
        if not self.days <= ALL_DAYS:
            raise ValueError("The days must be days of the week: 0 (Monday) to 6 (Sunday).")

    @property
    def limited(self) -> bool:
        """Whether any pass is turned away at any time of day."""
        return self.daylight or self.clock


# --- judging ---------------------------------------------------------------------------------


def daylight_window(hours: StopHours, label: int) -> tuple[int, int] | None:
    """When a pass is in daylight on solar day ``label``: from sunrise less the margin to sunset
    plus the margin, as a half-open span of ms. None if the sun does not rise and set that day
    (the midnight sun and the polar night are handled apart), or if the margins eat the day.

    It describes the window whether or not the limit is switched on, so that the page can show it
    before the switch is turned."""
    if hours.latitude is None or hours.longitude is None:
        return None
    day = sun_day(label, hours.latitude, hours.longitude)
    if day.sunrise_ms is None or day.sunset_ms is None:
        return None
    start = day.sunrise_ms - hours.before_sunrise_min * _MS_PER_MINUTE
    end = day.sunset_ms + hours.after_sunset_min * _MS_PER_MINUTE
    return (start, end) if start < end else None


def _in_daylight(hours: StopHours, at_ms: int) -> bool:
    if hours.latitude is None or hours.longitude is None:
        return True
    label = solar_day_label(at_ms, hours.longitude)
    # A window reaches at most three hours past the solar day it belongs to, so the days either
    # side of this one are the only others that can hold the instant.
    for other in (label - 1, label, label + 1):
        window = daylight_window(hours, other)
        if window is not None and window[0] <= at_ms < window[1]:
            return True
    return sun_day(label, hours.latitude, hours.longitude).polar == "day"  # the midnight sun


def _in_hours(hours: StopHours, at_ms: int, tz: tzinfo) -> bool:
    local = datetime.fromtimestamp(at_ms / 1000, tz)
    second = local.hour * 3600 + local.minute * 60 + local.second
    today = local.weekday()
    yesterday = (today - 1) % 7
    for span in hours.ranges:
        start, end = span.start * 60, span.end * 60
        if not span.through_midnight:
            if today in hours.days and start <= second < end:
                return True
        elif (today in hours.days and second >= start) or (
            yesterday in hours.days and second < end
        ):
            return True
    return False


def judging_at(hours: StopHours, at_ms: int, tz: tzinfo) -> bool:
    """Whether a stop-sign pass at this instant is judged. ``tz`` is the camera's time zone: it
    matters for the set hours (the sun does not care what the clock says)."""
    if hours.daylight and not _in_daylight(hours, at_ms):
        return False
    return not hours.clock or _in_hours(hours, at_ms, tz)


def _local_instant(day: date, minutes: int, tz: tzinfo) -> int:
    moment = datetime.combine(day, time(minutes // 60, minutes % 60), tzinfo=tz)
    return int(moment.timestamp() * 1000)


def next_change_ms(
    hours: StopHours, at_ms: int, tz: tzinfo, look_ahead_days: int = LOOK_AHEAD_DAYS
) -> int | None:
    """The first instant after ``at_ms`` at which the answer of ``judging_at`` is the other one,
    or None if it does not change within ``look_ahead_days`` (no limit is on; the polar night)."""
    if not hours.limited:
        return None
    horizon = at_ms + look_ahead_days * MS_PER_DAY
    edges: set[int] = set()
    if hours.daylight and hours.latitude is not None and hours.longitude is not None:
        first = solar_day_label(at_ms, hours.longitude) - 1
        last = solar_day_label(horizon, hours.longitude) + 1
        for label in range(first, last + 1):
            window = daylight_window(hours, label)
            if window is not None:
                edges.update(window)
            elif sun_day(label, hours.latitude, hours.longitude).polar == "day":
                for boundary in (label, label + 1):  # the midnight sun begins and ends at midnight
                    edges.add(boundary * MS_PER_DAY - round(hours.longitude * 240_000))
    if hours.clock:
        today = datetime.fromtimestamp(at_ms / 1000, tz).date()
        for offset in range(-1, look_ahead_days + 2):
            day = today + timedelta(days=offset)
            for span in hours.ranges:
                end_day = day + timedelta(days=1) if span.through_midnight else day
                edges.add(_local_instant(day, span.start, tz))
                edges.add(_local_instant(end_day, span.end, tz))
    now = judging_at(hours, at_ms, tz)
    for edge in sorted(edges):
        if at_ms < edge <= horizon and judging_at(hours, edge, tz) is not now:
            return edge
    return None


# --- the sun today, for the page -------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DaylightDay:
    """The sun on one date of the camera's clock, and the window around it."""

    date: str  # YYYY-MM-DD, by the camera's clock
    sunrise_ms: int | None
    sunset_ms: int | None
    start_ms: int | None  # the window: sunrise less the margin ...
    end_ms: int | None  # ... to sunset plus the margin; None when there is no sunrise and sunset
    polar: Literal["day", "night"] | None  # the sun stays up ("day") or down ("night") all day
    # How far from 12:00 on the camera's clock the sun is highest here, in minutes (positive:
    # later). Within an hour or two for a place in the zone's own country; many hours means the
    # longitude has the wrong sign or the zone is not the camera's.
    noon_offset_min: int


def daylight_day(hours: StopHours, at_ms: int, tz: tzinfo) -> DaylightDay | None:
    """The sun on the date that ``at_ms`` is on, by the camera's clock; None without a place."""
    if hours.latitude is None or hours.longitude is None:
        return None
    today = datetime.fromtimestamp(at_ms / 1000, tz).date()
    base = solar_day_label(_local_instant(today, 12 * 60, tz), hours.longitude)
    days = [sun_day(label, hours.latitude, hours.longitude) for label in (base, base - 1, base + 1)]
    # The solar day whose noon is on today's date by the clock (the first, if none or both are).
    day = next(
        (d for d in days if datetime.fromtimestamp(d.solar_noon_ms / 1000, tz).date() == today),
        days[0],
    )
    noon = datetime.fromtimestamp(day.solar_noon_ms / 1000, tz)
    minutes = noon.hour * 60 + noon.minute + noon.second / 60 - 12 * 60
    window = daylight_window(hours, day.label)
    return DaylightDay(
        date=today.isoformat(),
        sunrise_ms=day.sunrise_ms,
        sunset_ms=day.sunset_ms,
        start_ms=window[0] if window else None,
        end_ms=window[1] if window else None,
        polar=day.polar,
        noon_offset_min=round(minutes),
    )


# --- in words --------------------------------------------------------------------------------

_WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def days_text(days: frozenset[int]) -> str:
    """ "every day", "weekdays", "weekends", or the days by name: "Mon, Wed, Fri" (as the dashboard
    tells them)."""
    if days == ALL_DAYS:
        return "every day"
    if days == frozenset(range(5)):
        return "weekdays"
    if days == frozenset({5, 6}):
        return "weekends"
    return ", ".join(_WEEKDAYS[d] for d in sorted(days))


def _minutes(count: int) -> str:
    return f"{count} minute" if count == 1 else f"{count} minutes"


def describe_hours(hours: StopHours) -> str:
    """The limits that are on, in words, for the journal and the email: "from 30 minutes before
    sunrise to 30 minutes after sunset, and from 09:00 to 20:00 on weekdays"."""
    parts: list[str] = []
    if hours.daylight:
        before, after = hours.before_sunrise_min, hours.after_sunset_min
        start = (
            "sunrise"
            if before == 0
            else f"{_minutes(before)} before sunrise"
            if before > 0
            else f"{_minutes(-before)} after sunrise"
        )
        end = (
            "sunset"
            if after == 0
            else f"{_minutes(after)} after sunset"
            if after > 0
            else f"{_minutes(-after)} before sunset"
        )
        parts.append(f"from {start} to {end}")
    if hours.clock:
        spans = " and ".join(
            f"from {format_clock(r.start)} to {format_clock(r.end)}" for r in hours.ranges
        )
        days = days_text(hours.days)
        parts.append(f"{spans} {days if days == 'every day' else 'on ' + days}")
    return ", and ".join(parts) if parts else "at all hours"


# --- what is stored --------------------------------------------------------------------------


def _read_ranges(stored: Any) -> tuple[TimeRange, ...] | None:
    if not isinstance(stored, list) or not 1 <= len(stored) <= MAX_RANGES:
        return None
    ranges: list[TimeRange] = []
    for item in stored:
        if not isinstance(item, dict):
            return None
        try:
            ranges.append(TimeRange(parse_clock(item.get("start")), parse_clock(item.get("end"))))  # type: ignore[arg-type]
        except ValueError:
            return None
    return tuple(ranges)


def _read_days(stored: Any) -> frozenset[int]:
    usable = (
        isinstance(stored, list)
        and len(stored) > 0
        and all(isinstance(d, int) and not isinstance(d, bool) and 0 <= d <= 6 for d in stored)
    )
    return frozenset(stored) if usable else ALL_DAYS


def read_stop_hours(get: Callable[[str, Any], Any]) -> StopHours:
    """The stored settings (``get(key, default)``, like ``Store.get_setting``), whatever state they
    are in. A value that cannot be used is ignored; a limit that is on but cannot work (no usable
    place, no usable range) is left off; and only a plain ``true`` switches a limit on. A damaged
    value therefore judges more passes, never fewer."""

    def number(key: str, low: float, high: float) -> float | None:
        value = get(key, None)
        usable = (
            isinstance(value, int | float)
            and not isinstance(value, bool)
            and math.isfinite(value)
            and low <= value <= high
        )
        return float(value) if usable else None

    def margin(key: str) -> int:
        value = get(key, DEFAULT_MARGIN_MIN)
        usable = (
            isinstance(value, int)
            and not isinstance(value, bool)
            and MIN_MARGIN_MIN <= value <= MAX_MARGIN_MIN
        )
        return value if usable else DEFAULT_MARGIN_MIN

    latitude, longitude = number(LATITUDE_KEY, -90, 90), number(LONGITUDE_KEY, -180, 180)
    if latitude is None or longitude is None:
        latitude = longitude = None
    ranges = _read_ranges(get(RANGES_KEY, None))
    return StopHours(
        daylight=get(DAYLIGHT_KEY, False) is True and latitude is not None,
        latitude=latitude,
        longitude=longitude,
        before_sunrise_min=margin(BEFORE_SUNRISE_KEY),
        after_sunset_min=margin(AFTER_SUNSET_KEY),
        clock=get(CLOCK_KEY, False) is True and ranges is not None,
        ranges=ranges or DEFAULT_RANGES,
        days=_read_days(get(DAYS_KEY, None)),
    )


def ranges_as_stored(ranges: tuple[TimeRange, ...]) -> list[dict[str, str]]:
    return [{"start": format_clock(r.start), "end": format_clock(r.end)} for r in ranges]
