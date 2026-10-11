"""What the System page can say about the past: points to chart, and episodes worth a sentence.

The vision service stores a row of health a minute (``vision/health.py``). A month of them is far
more than a chart can show, so the minutes are gathered into at most 240 points; the episodes
(the board slowing itself down, under-voltage, the camera away, the record having a hole) are
worked out from the minutes themselves, so that a short one is not lost in an average.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable, Sequence
from itertools import pairwise
from typing import Any

from trafficpi.vision.health import SLOWED_DOWN, UNDER_VOLTAGE, HealthRow

MINUTE_S = 60
MAX_POINTS = 240
BUCKET_SIZES_S = (60, 120, 300, 600, 900, 1200, 1800, 3600, 7200, 14400)
MERGE_GAP_S = 2 * MINUTE_S  # one good (or missing) minute between two bad ones is the same episode
NO_DATA_AFTER_S = 3 * MINUTE_S  # a hole in the record longer than this is told: a restart is not
MIN_SAMPLES_FOR_OUTAGE = 6  # readings in a minute, to say that its camera was away
MAX_EPISODES = 50


def bucket_seconds(hours: int) -> int:
    """How long one point is, for a period of this many hours: the shortest of a few round sizes
    that keeps the period to 240 points or fewer."""
    wanted = hours * 3600 / MAX_POINTS
    return next((size for size in BUCKET_SIZES_S if size >= wanted), BUCKET_SIZES_S[-1])


def _weighted(pairs: Iterable[tuple[float, int]]) -> float | None:
    total = weight = 0.0
    for value, share in pairs:
        total += value * share
        weight += share
    return round(total / weight, 1) if weight else None


def _percent(part: int, whole: int) -> float | None:
    return round(100 * part / whole, 1) if whole else None


def _point(t: int, rows: Sequence[HealthRow]) -> dict[str, Any]:
    """One point: the hottest minute, the load and frame rate weighted by the readings behind each
    minute (a short minute after a restart counts for less), the slowest frame rate, any flags."""
    temps = [r.temp_c_max for r in rows if r.temp_c_max is not None]
    slowest = [r.fps_min for r in rows if r.fps_min is not None]
    flags = 0
    for r in rows:
        flags |= r.throttled
    return {
        "t": t,
        "minutes": len(rows),
        "temp_c": max(temps) if temps else None,
        "cpu_percent": _weighted(
            (r.cpu_percent, r.samples) for r in rows if r.cpu_percent is not None
        ),
        "fps": _weighted((r.fps_mean, r.camera_ok_samples) for r in rows if r.fps_mean is not None),
        "fps_min": min(slowest) if slowest else None,
        "camera_up_percent": _percent(
            sum(r.camera_ok_samples for r in rows), sum(r.samples for r in rows)
        ),
        "throttled": flags,
    }


def _summary(rows: Sequence[HealthRow]) -> dict[str, Any]:
    temps = [r.temp_c_max for r in rows if r.temp_c_max is not None]
    return {
        "minutes": len(rows),
        "temp_c_max": max(temps) if temps else None,
        "camera_up_percent": _percent(
            sum(r.camera_ok_samples for r in rows), sum(r.samples for r in rows)
        ),
        "slowed_minutes": sum(1 for r in rows if r.throttled & SLOWED_DOWN),
        "under_voltage_minutes": sum(1 for r in rows if r.throttled & UNDER_VOLTAGE),
    }


def _runs(rows: Sequence[HealthRow], bad: Callable[[HealthRow], bool]) -> list[list[HealthRow]]:
    """The minutes that are bad, in runs; a run goes on over one good minute."""
    runs: list[list[HealthRow]] = []
    for row in rows:
        if not bad(row):
            continue
        if runs and row.minute_start - runs[-1][-1].minute_start <= MERGE_GAP_S:
            runs[-1].append(row)
        else:
            runs.append([row])
    return runs


def _episodes(rows: Sequence[HealthRow], finished_s: int) -> list[dict[str, Any]]:
    """Everything worth a sentence, newest first. ``finished_s`` is where the finished minutes end
    (the minute in progress has no row yet, so it is not a hole)."""
    found: list[dict[str, Any]] = []

    def add(kind: str, start: int, end: int, ongoing: bool, peak: float | None = None) -> None:
        found.append(
            {"kind": kind, "from_s": start, "to_s": end, "ongoing": ongoing, "peak_temp_c": peak}
        )

    classes: tuple[tuple[str, Callable[[HealthRow], bool], bool], ...] = (
        ("slowed", lambda r: bool(r.throttled & SLOWED_DOWN), True),
        ("under-voltage", lambda r: bool(r.throttled & UNDER_VOLTAGE), False),
        (
            "camera-offline",
            lambda r: r.camera_ok_samples == 0 and r.samples >= MIN_SAMPLES_FOR_OUTAGE,
            False,
        ),
    )
    for kind, bad, with_temperature in classes:
        for run in _runs(rows, bad):
            end = run[-1].minute_start + MINUTE_S
            temps = [r.temp_c_max for r in run if r.temp_c_max is not None]
            add(
                kind,
                run[0].minute_start,
                end,
                end >= finished_s - MERGE_GAP_S,
                max(temps) if with_temperature and temps else None,
            )
    for before, after in pairwise(rows):
        if after.minute_start - (before.minute_start + MINUTE_S) > NO_DATA_AFTER_S:
            add("no-data", before.minute_start + MINUTE_S, after.minute_start, False)
    if rows and finished_s - (rows[-1].minute_start + MINUTE_S) > NO_DATA_AFTER_S:
        add("no-data", rows[-1].minute_start + MINUTE_S, finished_s, True)
    found.sort(key=lambda e: (-e["from_s"], e["kind"]))
    return found[:MAX_EPISODES]


def window(hours: int, now_s: int) -> tuple[int, int, int]:
    """Where the past ``hours`` hours begin and end, and how long one point is: the period ends with
    the point that holds the last finished minute, and starts a whole number of points before
    that (a little more than ``hours`` if they do not divide evenly)."""
    bucket_s = bucket_seconds(hours)
    finished_s = now_s // MINUTE_S * MINUTE_S
    to_s = -(-finished_s // bucket_s) * bucket_s
    return to_s - math.ceil(hours * 3600 / bucket_s) * bucket_s, to_s, bucket_s


def build(rows: Sequence[HealthRow], *, hours: int, now_s: int) -> dict[str, Any]:
    """The past ``hours`` hours as points of equal length, episodes and a summary (rows outside
    the period are ignored). A point with no minutes in it says so (``minutes`` is 0): the
    service was not recording."""
    from_s, to_s, bucket_s = window(hours, now_s)
    finished_s = now_s // MINUTE_S * MINUTE_S
    inside = sorted(
        (r for r in rows if from_s <= r.minute_start < to_s), key=lambda r: r.minute_start
    )
    grouped: dict[int, list[HealthRow]] = {}
    for row in inside:
        grouped.setdefault((row.minute_start - from_s) // bucket_s, []).append(row)
    return {
        "from_s": from_s,
        "to_s": to_s,
        "bucket_s": bucket_s,
        "points": [
            _point(from_s + i * bucket_s, grouped.get(i, []))
            for i in range((to_s - from_s) // bucket_s)
        ],
        "episodes": _episodes(inside, finished_s),
        "summary": _summary(inside),
    }
