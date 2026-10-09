"""When the daily email goes, and which stretch of time each one covers (plan.md D32).

A schedule is a time of day in the camera's time zone on some days of the week. Each day it names
has one *instant* (a moment in ms since the epoch), worked out from the local date so that a day
the clocks change is right: 02:30 on a day when the clock skips it still has one instant, and 01:30
on a day when it happens twice has one too (the first). A mail is due once the instant has passed,
and covers the time since the previous instant, so that two mails never overlap and none leaves a
gap: 24 hours on a schedule of every day (23 or 25 across a clock change), the weekend too on the
Monday mail of a weekdays-only schedule.

Everything here is a plain function of a schedule, a zone and instants, so that it can be tested
without a clock or a database.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from trafficpi.mail.settings import MailSchedule, parse_time

DAY_MS = 24 * 3_600_000
# A mail is not sent for an instant that is further past than this: the Pi was off, and a report
# of yesterday's traffic arriving at midnight is worse than none (it says so on the System page).
MAX_LATE_MS = 6 * 3_600_000
# How far to look for the instant before or after one: a week for a schedule of one day a week,
# and a day over for the clocks.
LOOKBACK_DAYS = 9


def _local_ms(day: date, hour: int, minute: int, tz: ZoneInfo) -> int:
    """The instant of a wall-clock time on a local date. A time that does not exist that day (the
    clock skipped it) is read with the offset from before the skip; one that happens twice is read
    as its first time. Either way it is one instant, on that date."""
    return int(datetime(day.year, day.month, day.day, hour, minute, tzinfo=tz).timestamp()) * 1000


def instants(schedule: MailSchedule, tz: ZoneInfo, after_ms: int, until_ms: int) -> list[int]:
    """The scheduled instants ``t`` with ``after_ms < t <= until_ms``, oldest first."""
    if until_ms <= after_ms:
        return []
    hour, minute = parse_time(schedule.time)
    first = datetime.fromtimestamp(after_ms / 1000, tz).date() - timedelta(days=1)
    last = datetime.fromtimestamp(until_ms / 1000, tz).date() + timedelta(days=1)
    found: list[int] = []
    day = first
    while day <= last:
        if day.weekday() in schedule.days:
            moment = _local_ms(day, hour, minute, tz)
            if after_ms < moment <= until_ms:
                found.append(moment)
        day += timedelta(days=1)
    return found


def latest_at_or_before(
    schedule: MailSchedule, tz: ZoneInfo, now_ms: int, *, lookback_days: int = LOOKBACK_DAYS
) -> int | None:
    """The last scheduled instant that has come (an instant exactly now has come)."""
    found = instants(schedule, tz, now_ms - lookback_days * DAY_MS, now_ms)
    return found[-1] if found else None


def previous_before(
    schedule: MailSchedule, tz: ZoneInfo, instant_ms: int, *, lookback_days: int = LOOKBACK_DAYS
) -> int | None:
    """The scheduled instant before ``instant_ms`` (not that one)."""
    found = instants(schedule, tz, instant_ms - lookback_days * DAY_MS, instant_ms - 1)
    return found[-1] if found else None


def next_after(
    schedule: MailSchedule, tz: ZoneInfo, now_ms: int, *, lookahead_days: int = LOOKBACK_DAYS
) -> int | None:
    """The first scheduled instant after ``now_ms`` (not one exactly now)."""
    found = instants(schedule, tz, now_ms, now_ms + lookahead_days * DAY_MS)
    return found[0] if found else None


def period_for(
    schedule: MailSchedule, tz: ZoneInfo, instant_ms: int, *, lookback_days: int = LOOKBACK_DAYS
) -> tuple[int, int]:
    """The stretch a mail for ``instant_ms`` covers: from the instant before it, up to it. (A
    schedule always has an instant within a week, so the day before is a fallback that is not
    reached for one that is valid.)"""
    previous = previous_before(schedule, tz, instant_ms, lookback_days=lookback_days)
    return (instant_ms - DAY_MS if previous is None else previous), instant_ms


def comparison_start(start_ms: int, end_ms: int) -> int:
    """Where the stretch before ``[start_ms, end_ms)`` of the same length begins."""
    return start_ms - (end_ms - start_ms)
