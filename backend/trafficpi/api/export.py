"""CSV exports of what was counted and of what vehicles did at stop signs (plan.md D62).

One row for each event, as the database holds it, so that the data can be looked at in a
spreadsheet or another program. Times are given twice, in UTC and in the camera's time zone; speeds
are in metres a second, as they are stored. A speed is marked "calibrated" only when it was
measured under the calibration saved now (D49), and a pass a person called a false alarm is in the
file, marked.

The rows are produced a page at a time, so a year of them is never in memory at once, and every cell
that a spreadsheet could take for a formula is made plain text.
"""

from __future__ import annotations

import csv
import io
from collections.abc import Callable, Iterator, Mapping
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from trafficpi.db.store import EventRow, Store

PAGE = 2_000

COUNT_HEADER = ["time_utc", "time_local", "line", "direction", "kind", "speed_ms", "calibrated"]
PASS_HEADER = [
    "time_utc",
    "time_local",
    "stop_sign",
    "kind",
    "outcome",
    "still_s",
    "watched_s",
    "slowest_ms",
    "fastest_ms",
    "at_line_ms",
    "speeds_calibrated",
    "review",
]

OUTCOME_WORDS = {
    "stopped": "stopped",
    "rolling": "rolled through",
    "did_not_stop": "did not stop",
    "indeterminate": "unclear",
}
REVIEW_WORDS = {"confirmed": "confirmed", "false_positive": "false alarm"}
FORMULA_STARTS = ("=", "+", "-", "@", "\t", "\r")


def safe(value: object) -> str:
    """A cell as text that a spreadsheet will not run: one that begins like a formula gets an
    apostrophe in front of it (names are typed by a person, and a downloaded file is opened by
    one)."""
    text = "" if value is None else str(value)
    return "'" + text if text.startswith(FORMULA_STARTS) else text


def _times(at_ms: int, tz: ZoneInfo) -> tuple[str, str]:
    moment = datetime.fromtimestamp(at_ms / 1000, UTC)
    return (
        moment.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        moment.astimezone(tz).isoformat(timespec="milliseconds"),
    )


def _metres_a_second(value: object) -> str:
    """A stored speed (negative or missing when it was not measured) as a cell."""
    if isinstance(value, bool) or not isinstance(value, int | float) or value < 0:
        return ""
    return f"{value:.2f}"


def _seconds(value: object) -> str:
    """A stored length of time in seconds, to a tenth; empty if what is stored is not a number."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return ""
    return f"{value:.1f}"


def _text(rows: list[list[str]]) -> str:
    out = io.StringIO()
    csv.writer(out, lineterminator="\r\n").writerows(rows)
    return out.getvalue()


def _paged(
    store: Store, type: str, start_ms: int, end_ms: int, rows_of: Callable[[EventRow], list[str]]
) -> Iterator[str]:
    after = 0
    while True:
        page = store.events_after(type, start_ms, end_ms, after_id=after, limit=PAGE)
        if not page:
            return
        yield _text([rows_of(event) for event in page])
        after = page[-1].id
        if len(page) < PAGE:
            return


def count_rows(
    store: Store,
    start_ms: int,
    end_ms: int,
    tz: ZoneInfo,
    line_names: Mapping[str, str],
    calibration: str | None,
) -> Iterator[str]:
    """The count events of a range as CSV text: the header, then a page of rows at a time."""
    yield _text([COUNT_HEADER])

    def row(event: EventRow) -> list[str]:
        details = event.details or {}
        speed = _metres_a_second(details.get("speed_ms"))
        utc, local = _times(event.occurred_at, tz)
        line = event.line_id or ""
        return [
            utc,
            local,
            safe(line_names.get(line, line)),
            "forward" if event.direction == 1 else "reverse" if event.direction == -1 else "",
            safe(event.class_label),
            speed,
            "yes"
            if speed and calibration is not None and details.get("cal") == calibration
            else "",
        ]

    yield from _paged(store, "count", start_ms, end_ms, row)


def pass_rows(
    store: Store,
    start_ms: int,
    end_ms: int,
    tz: ZoneInfo,
    sign_names: Mapping[str, str],
    calibration: str | None,
) -> Iterator[str]:
    """The passes at stop signs of a range as CSV text: the header, then pages of rows."""
    yield _text([PASS_HEADER])

    def row(event: EventRow) -> list[str]:
        details = event.details or {}
        speeds = [
            _metres_a_second(details.get(key))
            for key in ("min_speed_ms", "max_speed_ms", "line_speed_ms")
        ]
        measured_now = any(speeds) and calibration is not None and details.get("cal") == calibration
        utc, local = _times(event.occurred_at, tz)
        sign = event.line_id or ""
        return [
            utc,
            local,
            safe(sign_names.get(sign, sign)),
            safe(event.class_label),
            OUTCOME_WORDS.get(str(details.get("outcome")), "unclear"),
            _seconds(details.get("hold_s")),
            _seconds(details.get("observed_s")),
            *speeds,
            "yes" if measured_now else "",
            REVIEW_WORDS.get(event.review or "", ""),
        ]

    yield from _paged(store, "stop", start_ms, end_ms, row)
