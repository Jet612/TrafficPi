"""What the trails endpoints return: a day by the hour, and the paths themselves (plan.md M4b).

Days and hours follow the configured time zone, as everywhere else in the dashboard: "9 am" is
what a person standing at the intersection would call it, and a day is 23 or 25 hours long when
the clocks change.
"""

from __future__ import annotations

import gzip
import json
import logging
import math
from collections import Counter
from collections.abc import Collection, Sequence
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from trafficpi.db.store import Store, TrailRow
from trafficpi.vision.trails import FULL_SCALE, decode_path_raw

log = logging.getLogger("trafficpi.api")

HOUR_MS = 3_600_000
MAX_SPAN_DAYS = 32  # one request may cover at most this long: the paths are the bulk of the answer
DEFAULT_LIMIT = 5_000
MAX_LIMIT = 20_000
MAX_MS = 10**14  # the year 5138: beyond any real time, yet a number the database takes
FIRST_DAY = date(2000, 1, 1)  # and days outside these cannot be turned into times everywhere
LAST_DAY = date(2100, 12, 31)


class BadTrailQuery(ValueError):
    """A request for trails that cannot be answered; the message says why, for the person."""


def local_day(day: date, tz: ZoneInfo) -> tuple[int, int]:
    """The start and the end of a local day in ms since the epoch (23 or 25 hours long when the
    clocks change)."""
    if not FIRST_DAY <= day <= LAST_DAY:
        raise BadTrailQuery(f"ask for a day from {FIRST_DAY} to {LAST_DAY}")
    start = datetime.combine(day, time.min, tzinfo=tz)
    end = datetime.combine(day + timedelta(days=1), time.min, tzinfo=tz)
    return int(start.timestamp() * 1000), int(end.timestamp() * 1000)


def _local_date(ms: int, tz: ZoneInfo) -> str:
    return datetime.fromtimestamp(ms / 1000, tz).date().isoformat()


def day_by_the_hour(store: Store, tz: ZoneInfo, day: date) -> dict[str, Any]:
    """How many trails began in each hour of a local day, and of what kind."""
    start_ms, end_ms = local_day(day, tz)
    hour_count = math.ceil((end_ms - start_ms) / HOUR_MS)
    kinds: list[Counter[str]] = [Counter() for _ in range(hour_count)]
    for started_at, label in store.trail_times(start_ms, end_ms):
        kinds[min((started_at - start_ms) // HOUR_MS, hour_count - 1)][label] += 1
    hours = []
    for index, by_kind in enumerate(kinds):
        t = start_ms + index * HOUR_MS
        hours.append(
            {
                "t": t,
                "hour": datetime.fromtimestamp(t / 1000, tz).hour,
                "count": sum(by_kind.values()),
                "by_kind": dict(by_kind),
            }
        )
    extent = store.trail_extent()
    return {
        "day": day.isoformat(),
        "timezone": str(tz),
        "total": sum(h["count"] for h in hours),
        "first_day": None if extent is None else _local_date(extent[0], tz),
        "last_day": None if extent is None else _local_date(extent[1], tz),
        "hours": hours,
    }


def check_range(from_ms: int, to_ms: int, limit: int) -> None:
    if from_ms < 0 or to_ms > MAX_MS:
        raise BadTrailQuery("that time is out of range")
    if to_ms <= from_ms:
        raise BadTrailQuery("the range must end after it begins")
    if to_ms - from_ms > MAX_SPAN_DAYS * 86_400_000:
        raise BadTrailQuery(f"ask for at most {MAX_SPAN_DAYS} days of trails at a time")
    if not 1 <= limit <= MAX_LIMIT:
        raise BadTrailQuery(f"the limit must be from 1 to {MAX_LIMIT}")


def paths(
    store: Store,
    from_ms: int,
    to_ms: int,
    *,
    kinds: Collection[str] | None,
    limit: int,
) -> dict[str, Any]:
    """The paths of the trails that began in a range, as whole numbers from 0 to ``scale``.

    If more match than ``limit``, an even sample of them is returned, spread over the whole range
    (not the first ones), and ``total`` says how many there were.
    """
    check_range(from_ms, to_ms, limit)
    total = store.trail_count(from_ms, to_ms, labels=kinds)
    every = max(1, math.ceil(total / limit)) if total else 1
    decoded = _decode_all(
        store.trails_between(from_ms, to_ms, labels=kinds, limit=limit, every=every)
    )
    return {
        "from_ms": from_ms,
        "to_ms": to_ms,
        "total": total,
        "returned": len(decoded),
        "scale": FULL_SCALE,
        "trails": decoded,
    }


def _decode_all(rows: Sequence[TrailRow]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    damaged = 0
    for row in rows:
        try:
            _, x, y = decode_path_raw(row.path)
        except ValueError:
            damaged += 1
            continue
        out.append(
            {
                "id": row.id,
                "started_at": row.started_at,
                "ended_at": row.ended_at,
                "label": row.class_label,
                "x": x.tolist(),
                "y": y.tolist(),
            }
        )
    if damaged:
        log.warning("left out %d stored trail(s) that could not be read", damaged)
    return out


def encode_json(payload: object, accepts_gzip: bool) -> tuple[bytes, dict[str, str]]:
    """The JSON bytes of an answer, compressed if the browser accepts it (and headers to match).

    Only this endpoint compresses: a blanket compression of the whole API would hold back the
    live video stream, which has to be sent as it is made.
    """
    body = json.dumps(payload, separators=(",", ":")).encode()
    if accepts_gzip and len(body) > 1_000:
        return gzip.compress(body, compresslevel=5), {
            "Content-Encoding": "gzip",
            "Vary": "Accept-Encoding",
        }
    return body, {"Vary": "Accept-Encoding"}
