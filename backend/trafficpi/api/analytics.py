"""The sums behind the Data page: stop-sign behaviour and traffic over local days.

Days, hours and weekdays follow the configured time zone, as in ``stats.py``: "7 days" is today and
the six days before it, each from local midnight, and "the 8 am hour" is the one a person at the
intersection would call that. Everything here is a plain function of rows, a zone and a period, so
that it can be tested without a database.
"""

from __future__ import annotations

import math
import statistics
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from trafficpi.api.stats import summarize
from trafficpi.core.speed import estimated_speed_ms, to_units
from trafficpi.db.store import STOP_OUTCOMES, CountRow, SpeedRow, StopStatRow

MAX_DAYS = 366  # the most a request may cover (days=0, "all time", stops here too)
# Every zone's offset from UTC is a multiple of 15 minutes, so every local hour, day and weekday
# starts on a quarter of an hour of UTC too: the local time of an instant is the local time of its
# quarter, which is worked out once and used for every event in it.
QUARTER_MS = 900_000


@dataclass(frozen=True, slots=True)
class Period:
    """A run of whole local days ending today, and the run of the same length before it."""

    days: int  # how many local days, today included
    first_day: date
    today: date
    start_ms: int  # local midnight at the start of the first day
    end_ms: int  # local midnight at the end of today
    previous_start_ms: int | None  # the same number of days before; None for "all time"
    all_time: bool


def window_period(
    start_ms: int, end_ms: int, tz: ZoneInfo, previous_start_ms: int | None = None
) -> Period:
    """Any stretch of time ``[start_ms, end_ms)`` as a ``Period``, for sums over something other
    than whole local days: the daily email's period runs from one scheduled time to the next.
    ``days`` counts the local dates it touches, which the per-day sums run over."""
    first = datetime.fromtimestamp(start_ms / 1000, tz).date()
    last = datetime.fromtimestamp(max(start_ms, end_ms - 1) / 1000, tz).date()
    return Period(
        days=(last - first).days + 1,
        first_day=first,
        today=last,
        start_ms=start_ms,
        end_ms=end_ms,
        previous_start_ms=previous_start_ms,
        all_time=False,
    )


def local_midnight_ms(day: date, tz: ZoneInfo) -> int:
    """The instant a local day begins, in ms since the epoch."""
    return int(datetime.combine(day, time.min, tzinfo=tz).timestamp()) * 1000


def period_for(now_ms: int, tz: ZoneInfo, days: int, first_event_ms: int | None) -> Period:
    """The ``days`` local days that end today. ``days=0`` means "all time": from the day of the
    first event (``first_event_ms``; today alone if there is none) but at most ``MAX_DAYS`` back."""
    today = datetime.fromtimestamp(now_ms / 1000, tz).date()
    if days == 0:
        if first_event_ms is None:
            count = 1
        else:
            first = datetime.fromtimestamp(first_event_ms / 1000, tz).date()
            count = max(1, (today - first).days + 1)
    else:
        count = days
    count = min(count, MAX_DAYS)
    first_day = today - timedelta(days=count - 1)
    return Period(
        days=count,
        first_day=first_day,
        today=today,
        start_ms=local_midnight_ms(first_day, tz),
        end_ms=local_midnight_ms(today + timedelta(days=1), tz),
        previous_start_ms=(
            None if days == 0 else local_midnight_ms(first_day - timedelta(days=count), tz)
        ),
        all_time=days == 0,
    )


class _LocalTimes:
    """The local hour, date and weekday (Monday is 0) of instants, one lookup per quarter hour."""

    def __init__(self, tz: ZoneInfo) -> None:
        self._tz = tz
        self._cache: dict[int, tuple[int, date, int]] = {}

    def of(self, epoch_ms: int) -> tuple[int, date, int]:
        quarter = epoch_ms // QUARTER_MS
        found = self._cache.get(quarter)
        if found is None:
            moment = datetime.fromtimestamp(quarter * QUARTER_MS / 1000, self._tz)
            found = (moment.hour, moment.date(), moment.weekday())
            self._cache[quarter] = found
        return found


def _days_of(period: Period) -> list[date]:
    return [period.first_day + timedelta(days=n) for n in range(period.days)]


def _outcomes(counter: Mapping[str, int]) -> dict[str, int]:
    return {name: counter.get(name, 0) for name in STOP_OUTCOMES}


def shares_of(counts: Mapping[str, int]) -> dict[str, int]:
    """Whole-number percentages of the passes judged, one for each outcome, that add up to 100.

    The Data page's own rule (``sharesOf`` in dashboard/src/lib/data.ts, which the cases in
    tests/data/share_cases.json were made with): each share is rounded down, and the points left
    over go to the largest remainders (the earlier outcome first on a tie), never to an outcome
    that did not happen. All 0 when nothing was judged. The daily email says these numbers, so
    that it and the Data page cannot disagree.
    """
    outcomes = list[str](STOP_OUTCOMES)  # plain strings: the result is keyed by them
    total = sum(counts.get(o, 0) for o in outcomes)
    shares = dict.fromkeys(outcomes, 0)
    if total == 0:
        return shares
    exact = {o: counts.get(o, 0) / total * 100 for o in outcomes}
    for outcome, value in exact.items():
        shares[outcome] = math.floor(value + 1e-9)
    left = 100 - sum(shares.values())
    for outcome in sorted(outcomes, key=lambda o: -(exact[o] - math.floor(exact[o] + 1e-9))):
        if left <= 0:
            break
        if counts.get(outcome, 0) > 0:
            shares[outcome] += 1
            left -= 1
    return shares
    exact = {o: counts.get(o, 0) / total * 100 for o in STOP_OUTCOMES}
    for outcome, value in exact.items():
        shares[outcome] = math.floor(value + 1e-9)
    left = 100 - sum(shares.values())
    for outcome in sorted(STOP_OUTCOMES, key=lambda o: -(exact[o] - math.floor(exact[o] + 1e-9))):
        if left <= 0:
            break
        if counts.get(outcome, 0) > 0:
            shares[outcome] += 1
            left -= 1
    return shares


# The fastest speed of some passes: the speed in metres a second, the kind of vehicle, when it
# passed, and whether the speed was measured on a calibrated road (and not estimated from the size).
Fastest = tuple[float, str, int, bool]


def _fastest(best: Fastest | None) -> dict[str, Any] | None:
    """A fastest speed as the API gives it: metres a second to a tenth, the kind of vehicle, when
    it passed (ms since the epoch) and whether it was measured; None if no pass had a speed."""
    if best is None:
        return None
    speed, label, at, calibrated = best
    return {"speed_ms": round(speed, 1), "label": label, "at": at, "calibrated": calibrated}


def _faster(candidate: Fastest, current: Fastest | None) -> bool:
    """Whether a pass is the new fastest: a measured speed beats any estimate (an estimate is good
    to about 40% and could be anything), then the faster wins, and on a tie the earlier stays."""
    return current is None or (candidate[3], candidate[0]) > (current[3], current[0])


def _median(values: Sequence[float], digits: int) -> float | None:
    return round(statistics.median(values), digits) if values else None


def _counted(rows: Iterable[StopStatRow]) -> Iterable[StopStatRow]:
    """The events that count: with an outcome the dashboard knows, and not called a false alarm."""
    return (r for r in rows if r.outcome in STOP_OUTCOMES and r.review != "false_positive")


def stop_stats(
    rows: Iterable[StopStatRow],
    previous_rows: Iterable[StopStatRow] | None,
    tz: ZoneInfo,
    period: Period,
    *,
    sign: str | None = None,
    judged: Callable[[int], bool] | None = None,
) -> dict[str, Any]:
    """How vehicles did at stop signs over a period, and over the run of days before it.

    Passes a person called false alarms are left out of every share and counted on their own.

    ``judged`` says whether a pass at an instant (ms) is inside the hours in which stop signs are
    judged now. Passes recorded before the hours were limited, or under other hours,
    stay in the sums; ``outside_hours`` counts the ones the hours turn away today. Without it, none.
    """
    local = _LocalTimes(tz)
    totals: Counter[str] = Counter()
    by_hour = [Counter[str]() for _ in range(24)]
    by_day: dict[date, Counter[str]] = {}
    by_weekday = [Counter[str]() for _ in range(7)]
    by_kind: dict[str, Counter[str]] = {}
    by_sign: dict[str, Counter[str]] = {}
    holds: list[float] = []
    roll_speeds: list[float] = []
    miss_speeds: list[float] = []
    # The fastest speed of each local day: the speed, the kind of vehicle, when it passed.
    fastest_by_day: dict[date, Fastest] = {}
    roll_speeds_ms: list[float] = []
    miss_speeds_ms: list[float] = []
    confirmed = false_alarms = waiting = outside_hours = 0

    for row in rows:
        if row.outcome not in STOP_OUTCOMES:
            continue  # an outcome that a newer version knew of
        if row.review == "false_positive":
            false_alarms += 1
            continue
        if judged is not None and not judged(row.occurred_at):
            outside_hours += 1
        if row.review == "confirmed":
            confirmed += 1
        elif row.outcome != "stopped":
            waiting += 1  # needs a look, and nobody has looked
        hour, day, weekday = local.of(row.occurred_at)
        totals[row.outcome] += 1
        by_hour[hour][row.outcome] += 1
        by_day.setdefault(day, Counter())[row.outcome] += 1
        by_weekday[weekday][row.outcome] += 1
        by_kind.setdefault(row.kind or "unknown", Counter())[row.outcome] += 1
        by_sign.setdefault(row.sign, Counter())[row.outcome] += 1
        # -1 is "not measured": passes from before it was kept, or too short. A speed measured on
        # a calibrated road is used when there is one; the estimate from the vehicle's size is not.
        if row.max_speed_ms >= 0:
            found: Fastest | None = (row.max_speed_ms, row.kind or "unknown", row.occurred_at, True)
        elif row.max_speed >= 0:
            speed = estimated_speed_ms(row.max_speed, row.kind)
            found = (speed, row.kind or "unknown", row.occurred_at, False)
        else:
            found = None
        if found is not None and _faster(found, fastest_by_day.get(day)):
            fastest_by_day[day] = found
        if row.outcome == "stopped":
            holds.append(row.hold_s)
        elif row.min_speed >= 0 and row.outcome == "rolling":
            roll_speeds.append(row.min_speed)
        elif row.min_speed >= 0 and row.outcome == "did_not_stop":
            miss_speeds.append(row.min_speed)
        if row.min_speed_ms >= 0 and row.outcome == "rolling":
            roll_speeds_ms.append(row.min_speed_ms)
        elif row.min_speed_ms >= 0 and row.outcome == "did_not_stop":
            miss_speeds_ms.append(row.min_speed_ms)

    fastest_overall: Fastest | None = None
    for day in _days_of(period):
        best = fastest_by_day.get(day)
        if best is not None and _faster(best, fastest_overall):
            fastest_overall = best

    def ranked(groups: Mapping[str, Counter[str]]) -> list[tuple[str, Counter[str]]]:
        return sorted(groups.items(), key=lambda item: (-item[1].total(), item[0]))

    return {
        "timezone": str(tz),
        "from_ms": period.start_ms,
        "to_ms": period.end_ms,
        "days": period.days,
        "all_time": period.all_time,
        "sign": sign,
        "totals": _outcomes(totals),
        "outside_hours": outside_hours,
        "previous": (
            None
            if previous_rows is None
            else _outcomes(Counter(r.outcome for r in _counted(previous_rows)))
        ),
        "by_hour": [{"hour": hour, **_outcomes(c)} for hour, c in enumerate(by_hour)],
        "by_day": [
            {
                "date": day.isoformat(),
                "t": local_midnight_ms(day, tz),
                **_outcomes(by_day.get(day, Counter())),
                "fastest": _fastest(fastest_by_day.get(day)),
            }
            for day in _days_of(period)
        ],
        "fastest": _fastest(fastest_overall),
        "by_weekday": [{"weekday": n, **_outcomes(c)} for n, c in enumerate(by_weekday)],
        "by_kind": [{"label": name, **_outcomes(c)} for name, c in ranked(by_kind)],
        "by_sign": [{"sign": name, **_outcomes(c)} for name, c in ranked(by_sign)],
        "timing": {
            "stopped_hold_s": _median(holds, 1),
            "rolling_min_speed": _median(roll_speeds, 2),
            "did_not_stop_min_speed": _median(miss_speeds, 2),
            "rolling_min_speed_ms": _median(roll_speeds_ms, 1),
            "did_not_stop_min_speed_ms": _median(miss_speeds_ms, 1),
        },
        "review": {"confirmed": confirmed, "false_alarms": false_alarms, "waiting": waiting},
    }


def traffic_stats(
    rows: Sequence[CountRow],
    tz: ZoneInfo,
    period: Period,
    line_names: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Vehicles counted over a period: in all, and by hour, day, weekday, kind and line."""
    local = _LocalTimes(tz)
    by_hour = [Counter[str]() for _ in range(24)]
    by_day: dict[date, Counter[str]] = {}
    by_weekday = [0] * 7
    for row in rows:
        hour, day, weekday = local.of(row.bucket_start * 1000)
        by_hour[hour][row.class_label] += row.count
        by_day.setdefault(day, Counter())[row.class_label] += row.count
        by_weekday[weekday] += row.count
    summary = summarize(rows, line_names)
    return {
        "timezone": str(tz),
        "from_ms": period.start_ms,
        "to_ms": period.end_ms,
        "days": period.days,
        "all_time": period.all_time,
        "total": summary["total"],
        "by_class": summary["by_class"],
        "removed": summary["removed"],
        "lines": summary["lines"],
        "by_hour": [
            {"hour": hour, "total": c.total(), "by_class": dict(c)}
            for hour, c in enumerate(by_hour)
        ],
        "by_day": [
            {
                "date": day.isoformat(),
                "t": local_midnight_ms(day, tz),
                "total": by_day.get(day, Counter()).total(),
                "by_class": dict(by_day.get(day, Counter())),
            }
            for day in _days_of(period)
        ],
        "by_weekday": [{"weekday": n, "total": total} for n, total in enumerate(by_weekday)],
    }


# --- the speeds of the vehicles counted ---------------------------------------------------------

SPEED_BIN = 5.0  # the histogram's bars are this wide, in miles or kilometres an hour, from zero
MAX_SPEED_BINS = 40  # so nothing is drawn beyond 200: a faster vehicle is in the last bar


def _percentile(sorted_values: Sequence[float], share: float) -> float:
    """The smallest value that at least ``share`` of the values are not above (nearest rank)."""
    return sorted_values[max(0, math.ceil(share * len(sorted_values)) - 1)]


def _speed_figures(speeds: Sequence[float], counted: int | None = None) -> dict[str, Any] | None:
    """The count, mean, median, 85th percentile, fastest and slowest of some speeds (already in
    the units shown, to a tenth), or None for none. The 85th percentile is the speed that 85% of
    the vehicles do not exceed, the figure that speed studies are summed up by."""
    if not speeds:
        return None
    ordered = sorted(speeds)
    figures: dict[str, Any] = {
        "count": len(ordered),
        "mean": round(statistics.fmean(ordered), 1),
        "median": round(statistics.median(ordered), 1),
        "p85": round(_percentile(ordered, 0.85), 1),
        "fastest": round(ordered[-1], 1),
        "slowest": round(ordered[0], 1),
    }
    if counted is not None:
        figures["counted"] = counted
    return figures


def speed_stats(
    rows: Sequence[SpeedRow],
    counted: Mapping[str, int],
    tz: ZoneInfo,
    period: Period,
    units: str,
    *,
    line_names: Mapping[str, str] | None = None,
    line: str | None = None,
    calibration: str | None = None,
    excluded: int = 0,
) -> dict[str, Any]:
    """How fast the vehicles that were counted were going, over a period of local days.

    ``rows`` are the counts that have a measured speed under the calibration in force
    (``calibration``; ``excluded`` more were measured under another and are not in the sums);
    ``counted`` is how many vehicles each counting line counted in the period, measured or not (a
    speed is given only when it can be believed, so a share of the vehicles has none, and the page
    says how many that is). Speeds are in ``units`` (miles or kilometres an hour), to a tenth.
    """
    local = _LocalTimes(tz)
    names = line_names or {}
    everything: list[float] = []
    by_hour: list[list[float]] = [[] for _ in range(24)]
    by_day: dict[date, list[float]] = {}
    by_line: dict[str, list[float]] = {}
    by_kind: dict[str, list[float]] = {}
    bins: Counter[int] = Counter()
    for row in rows:
        value = to_units(row.speed_ms, units)
        hour, day, _ = local.of(row.occurred_at)
        everything.append(value)
        by_hour[hour].append(value)
        by_day.setdefault(day, []).append(value)
        by_line.setdefault(row.line, []).append(value)
        by_kind.setdefault(row.kind or "unknown", []).append(value)
        bins[min(MAX_SPEED_BINS - 1, int(value // SPEED_BIN))] += 1
    last = max(bins, default=-1)

    def hour_of(values: list[float]) -> dict[str, Any]:
        figures = _speed_figures(values)
        return {
            "count": len(values),
            "mean": None if figures is None else figures["mean"],
            "p85": None if figures is None else figures["p85"],
        }

    def ranked(groups: Mapping[str, list[float]]) -> list[tuple[str, list[float]]]:
        return sorted(groups.items(), key=lambda item: (-len(item[1]), item[0]))

    lines_seen = {**dict.fromkeys(by_line, 0), **counted}
    return {
        "timezone": str(tz),
        "units": "mph" if units == "mph" else "kmh",
        "from_ms": period.start_ms,
        "to_ms": period.end_ms,
        "days": period.days,
        "all_time": period.all_time,
        "line": line,
        "counted": sum(counted.values()),
        "calibration": calibration,
        "excluded": excluded,
        "figures": _speed_figures(everything),
        "bins": [
            {"low": n * SPEED_BIN, "high": (n + 1) * SPEED_BIN, "count": bins.get(n, 0)}
            for n in range(last + 1)
        ],
        "by_hour": [{"hour": hour, **hour_of(values)} for hour, values in enumerate(by_hour)],
        "by_day": [
            {
                "date": day.isoformat(),
                "t": local_midnight_ms(day, tz),
                **hour_of(by_day.get(day, [])),
            }
            for day in _days_of(period)
        ],
        "by_line": [
            {
                "line": name,
                "name": names.get(name, ""),
                "counted": lines_seen.get(name, 0),
                "figures": _speed_figures(by_line.get(name, [])),
            }
            for name in sorted(lines_seen, key=lambda n: (-lines_seen[n], n))
        ],
        "by_kind": [
            {"label": label, "figures": _speed_figures(values)} for label, values in ranked(by_kind)
        ],
    }
