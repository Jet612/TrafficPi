"""Turning stored minute counts into what the dashboard shows.

Days and hours follow the configured time zone, so "today" and "9 am" mean what a person
standing at the intersection would expect.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from datetime import datetime, timedelta
from typing import Any, Literal
from zoneinfo import ZoneInfo

from trafficpi.db.store import CountRow, Store

Bucket = Literal["minute", "hour", "day"]
DIRECTION_NAMES = {1: "forward", -1: "reverse"}  # relative to the counting line's own direction


def day_bounds(now_ms: int, tz: ZoneInfo) -> tuple[int, int]:
    """Start and end of the local day containing ``now_ms``, in whole seconds since the epoch."""
    now = datetime.fromtimestamp(now_ms / 1000, tz)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end = (start + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return int(start.timestamp()), int(end.timestamp())


def _empty_line() -> dict[str, Any]:
    return {"forward": 0, "reverse": 0, "by_class": {}}


def summarize(
    rows: Iterable[CountRow], line_names: Mapping[str, str] | None = None
) -> dict[str, Any]:
    """Totals overall, by class and by line, from minute rows.

    ``line_names`` says which lines exist: every one of them is listed, with zeros if nothing
    crossed it yet. What a line that has since been deleted counted stays in the totals, and is
    given together as ``removed`` rather than as a row for each (nobody remembers their ids).
    Without ``line_names`` every line that has counts is listed.
    """
    configured = line_names is not None
    names = line_names or {}
    total = 0
    removed = 0
    by_class: Counter[str] = Counter()
    lines: dict[str, dict[str, Any]] = {line_id: _empty_line() for line_id in names}
    for row in rows:
        total += row.count
        by_class[row.class_label] += row.count
        if configured and row.line_id not in lines:
            removed += row.count
            continue
        line = lines.setdefault(row.line_id, _empty_line())
        line[DIRECTION_NAMES.get(row.direction, "forward")] += row.count
        line["by_class"][row.class_label] = line["by_class"].get(row.class_label, 0) + row.count
    return {
        "total": total,
        "by_class": dict(by_class.most_common()),
        "removed": removed,
        "lines": [
            {"id": line_id, "name": names.get(line_id, ""), **data}
            for line_id, data in sorted(lines.items())
        ],
    }


def _bucket_start(epoch_s: int, tz: ZoneInfo, bucket: Bucket) -> datetime:
    moment = datetime.fromtimestamp(epoch_s, tz)
    if bucket == "minute":
        return moment.replace(second=0, microsecond=0)
    if bucket == "hour":
        return moment.replace(minute=0, second=0, microsecond=0)
    return moment.replace(hour=0, minute=0, second=0, microsecond=0)


def series(
    rows: Iterable[CountRow],
    tz: ZoneInfo,
    start_s: int,
    end_s: int,
    bucket: Bucket,
) -> list[dict[str, Any]]:
    """Counts per minute, hour or local day between two instants, empty buckets included."""
    cells: dict[int, dict[str, Any]] = {}
    for row in rows:
        key = int(_bucket_start(row.bucket_start, tz, bucket).timestamp())
        cell = cells.setdefault(key, {"total": 0, "by_class": Counter(), "by_line": {}})
        cell["total"] += row.count
        cell["by_class"][row.class_label] += row.count
        line = cell["by_line"].setdefault(row.line_id, {"forward": 0, "reverse": 0})
        line[DIRECTION_NAMES.get(row.direction, "forward")] += row.count

    out: list[dict[str, Any]] = []
    cursor = _bucket_start(start_s, tz, bucket)
    step = {"minute": timedelta(minutes=1), "hour": timedelta(hours=1), "day": timedelta(days=1)}[
        bucket
    ]
    while int(cursor.timestamp()) < end_s:
        key = int(cursor.timestamp())
        cell = cells.get(key, {"total": 0, "by_class": Counter(), "by_line": {}})
        out.append(
            {
                "t": key * 1000,
                "local": cursor.isoformat(),
                "total": cell["total"],
                "by_class": dict(cell["by_class"]),
                "by_line": cell["by_line"],
            }
        )
        cursor = _next(cursor, step, bucket)
    return out


def _next(cursor: datetime, step: timedelta, bucket: Bucket) -> datetime:
    """The next bucket start; days advance by calendar day so 23- and 25-hour days work."""
    if bucket == "day":
        return (cursor + step).replace(hour=0, minute=0, second=0, microsecond=0)
    # Step in UTC so a repeated local hour (clocks going back) appears twice, as it happened.
    return (cursor.astimezone(ZoneInfo("UTC")) + step).astimezone(cursor.tzinfo)


def today_summary(
    store: Store,
    tz: ZoneInfo,
    now_ms: int,
    line_names: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    start_s, end_s = day_bounds(now_ms, tz)
    rows = store.minute_counts(start_s, end_s)
    hourly = series(rows, tz, start_s, end_s, "hour")
    return {
        "date": datetime.fromtimestamp(start_s, tz).date().isoformat(),
        "timezone": str(tz),
        **summarize(rows, line_names),
        "hourly": [
            {"t": h["t"], "hour": datetime.fromisoformat(h["local"]).hour, "count": h["total"]}
            for h in hourly
        ],
    }
