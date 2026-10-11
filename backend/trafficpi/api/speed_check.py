"""Checking the camera's speeds against vehicles whose speed was known.

A calibration is only as good as the vehicles it has been checked against. The owner (or anyone)
drives past the camera at a speed they know, from a GPS app or a speedometer on cruise control, and
writes down when the vehicle crossed a counting line and how fast it was going: one line of a file
for each pass, ``2026-10-11 14:03:20, 25``. This module matches those passes to the counts the
camera made and says how far its speeds were from the truth: the mean error (the bias), the mean
and the largest error without regard to sign, in percent and in miles or kilometres an hour. Those
numbers are the calibration's error band, and are what ``docs/speed-calibration.md`` publishes once
there are enough.

Everything here is a plain function of rows, so it is tested without a database or a camera.
"""

from __future__ import annotations

import math
import re
import statistics
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from zoneinfo import ZoneInfo

from trafficpi.core.speed import MPS_TO_KMH, MPS_TO_MPH, to_units
from trafficpi.db.store import SpeedRow

DEFAULT_TOLERANCE_S = 8.0  # a pass and its count are this near in time at most
MAX_READINGS = 500


class ReadingsError(ValueError):
    """The file is not a list of passes. The message says which line, and what is wrong with it."""


@dataclass(frozen=True, slots=True)
class Reading:
    """A vehicle that went past at a known speed."""

    at_ms: int  # when it crossed the counting line, ms since the epoch
    speed_ms: float  # how fast it was really going, metres a second
    line: str | None  # the counting line it crossed (an id or a name); None: any
    note: str


@dataclass(frozen=True, slots=True)
class Match:
    reading: Reading
    event: SpeedRow | None  # the count the camera made of it; None if it made none with a speed
    ambiguous: bool  # another count with a speed was as near in time: it may be another vehicle

    @property
    def error(self) -> float | None:
        """The camera's speed over the true one, less one: +0.1 is 10% too fast."""
        if self.event is None or self.reading.speed_ms <= 0:
            return None
        return self.event.speed_ms / self.reading.speed_ms - 1.0


_SPEED = re.compile(r"^\s*([0-9]+(?:\.[0-9]+)?)\s*(mph|km/h|kmh|kph|m/s)?\s*$", re.IGNORECASE)
_TO_MS = {"mph": 1 / MPS_TO_MPH, "kmh": 1 / MPS_TO_KMH, "m/s": 1.0}


def _speed_ms(text: str, units: str) -> float | None:
    found = _SPEED.match(text)
    if found is None:
        return None
    unit = (found.group(2) or units).lower()
    key = "kmh" if unit in ("km/h", "kph") else unit
    return float(found.group(1)) * _TO_MS[key]


def _parse_time(text: str, day: date, tz: ZoneInfo) -> int | None:
    """A moment in ms, or None if the text is not one: a clock only (on ``day``), or a date and a
    clock, on the camera's clock unless it carries an offset of its own (``+00:00`` or ``Z``)."""
    cleaned = text.strip().replace("T", " ")
    if ":" not in cleaned:  # a date with no time is no moment, whatever midnight might match
        return None
    try:
        if len(cleaned) <= 8:  # a clock only: the day is the one given
            clock = datetime.strptime(cleaned, "%H:%M:%S" if cleaned.count(":") == 2 else "%H:%M")
            moment = datetime.combine(day, clock.time(), tzinfo=tz)
        else:
            moment = datetime.fromisoformat(cleaned)
            if moment.tzinfo is None:
                moment = moment.replace(tzinfo=tz)
    except ValueError:
        return None
    return int(moment.timestamp() * 1000)


def parse_readings(text: str, tz: ZoneInfo, units: str, day: date) -> list[Reading]:
    """The passes in a file: one per line, ``time, speed[, line[, note]]``.

    ``time`` is a clock (``14:03:20``, on ``day``) or a date and clock (``2026-10-11 14:03:20``) on
    the camera's clock; ``speed`` is a number in ``units`` unless it says otherwise (``40 km/h``,
    ``11 m/s``, ``25 mph``). Blank lines, lines beginning with ``#`` and a header line
    (``time, speed``) are skipped.
    """
    readings: list[Reading] = []
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        cells = [cell.strip() for cell in line.split(",")]
        if cells[0].lower() in ("time", "when") and len(readings) == 0:
            continue
        if len(cells) < 2:
            raise ReadingsError(f"line {number}: expected a time and a speed, like 14:03:20, 25")
        at = _parse_time(cells[0], day, tz)
        if at is None:
            raise ReadingsError(
                f"line {number}: {cells[0]!r} is not a time (14:03:20 or 2026-10-11 14:03:20)"
            )
        speed = _speed_ms(cells[1], units)
        if speed is None or speed <= 0:
            raise ReadingsError(
                f"line {number}: {cells[1]!r} is not a speed (25, 25 mph, 40 km/h, 11 m/s)"
            )
        line_name = (cells[2] or None) if len(cells) > 2 else None
        readings.append(Reading(at, speed, line_name, ",".join(cells[3:])))
        if len(readings) > MAX_READINGS:
            raise ReadingsError(f"more than {MAX_READINGS} passes: split the file")
    if not readings:
        raise ReadingsError("the file has no passes in it")
    return readings


def match_readings(
    readings: Sequence[Reading],
    events: Sequence[SpeedRow],
    *,
    tolerance_s: float = DEFAULT_TOLERANCE_S,
    line_ids: Mapping[str, str] | None = None,
) -> list[Match]:
    """Each pass with the count the camera made of it: the nearest in time, within ``tolerance_s``,
    on the line it names (a line's id, or its name through ``line_ids``, which maps names to ids),
    and not already taken by an earlier pass. ``ambiguous`` marks a pass with another count as
    near, since traffic that is not the test vehicle would be mistaken for it."""
    names = {name.lower(): ident for name, ident in (line_ids or {}).items()}
    tolerance_ms = tolerance_s * 1000
    taken: set[int] = set()
    matches: list[Match] = []
    for reading in sorted(readings, key=lambda r: r.at_ms):
        wanted = None if reading.line is None else names.get(reading.line.lower(), reading.line)
        near = [
            (abs(e.occurred_at - reading.at_ms), i)
            for i, e in enumerate(events)
            if i not in taken
            and abs(e.occurred_at - reading.at_ms) <= tolerance_ms
            and (wanted is None or e.line == wanted)
        ]
        if not near:
            matches.append(Match(reading, None, False))
            continue
        near.sort()
        taken.add(near[0][1])
        matches.append(Match(reading, events[near[0][1]], len(near) > 1))
    return matches


def summarize(
    matches: Sequence[Match], *, exclude_ambiguous: bool = False
) -> dict[str, float | int]:
    """How far the camera's speeds were from the truth, over the passes that were matched: the mean
    error (negative: too slow), the mean and the largest error without regard to sign, and the root
    mean square, as shares of the true speed; and the mean and the largest error without regard to
    sign in metres a second."""
    used = [m for m in matches if m.error is not None and not (exclude_ambiguous and m.ambiguous)]
    errors = [m.error for m in used if m.error is not None]
    summary: dict[str, float | int] = {
        "passes": len(matches),
        "matched": len(used),
        "unmatched": sum(1 for m in matches if m.event is None),
        "ambiguous": sum(1 for m in matches if m.ambiguous),
    }
    if not errors:
        return summary
    metres = [abs(m.event.speed_ms - m.reading.speed_ms) for m in used if m.event is not None]
    summary |= {
        "mean_error": statistics.fmean(errors),
        "mean_abs_error": statistics.fmean(abs(e) for e in errors),
        "max_abs_error": max(abs(e) for e in errors),
        "rms_error": math.sqrt(statistics.fmean(e * e for e in errors)),
        "mean_abs_error_ms": statistics.fmean(metres),
        "max_abs_error_ms": max(metres),
    }
    return summary


def _clock(ms: int, tz: ZoneInfo) -> str:
    return datetime.fromtimestamp(ms / 1000, tz).strftime("%Y-%m-%d %H:%M:%S")


def report(
    matches: Sequence[Match], tz: ZoneInfo, units: str, *, exclude_ambiguous: bool = False
) -> str:
    """A table of the passes and the summary, in plain text that can be pasted into a document."""
    unit = "mph" if units == "mph" else "km/h"
    lines = [
        f"{'time':19}  {'line':12}  {'true':>8}  {'camera':>8}  {'error':>7}  note",
    ]
    for m in matches:
        true = f"{to_units(m.reading.speed_ms, units):.1f}"
        if m.event is None:
            lines.append(
                f"{_clock(m.reading.at_ms, tz):19}  {m.reading.line or '':12}  {true:>8}  "
                f"{'none':>8}  {'':>7}  no count with a speed within the tolerance"
            )
            continue
        error = m.error
        shown = f"{100 * error:+.1f}%" if error is not None else ""
        note = "another count was as near: may be another vehicle" if m.ambiguous else ""
        camera = f"{to_units(m.event.speed_ms, units):8.1f}"
        lines.append(
            f"{_clock(m.reading.at_ms, tz):19}  {m.event.line:12}  {true:>8}  {camera}  "
            f"{shown:>7}  {note or m.reading.note}".rstrip()
        )
    s = summarize(matches, exclude_ambiguous=exclude_ambiguous)
    lines.append("")
    lines.append(
        f"{s['matched']} of {s['passes']} passes matched a count with a speed"
        + (f" ({s['unmatched']} had none)" if s["unmatched"] else "")
        + (f"; {s['ambiguous']} could be another vehicle" if s["ambiguous"] else "")
        + (" and were left out" if exclude_ambiguous and s["ambiguous"] else "")
        + "."
    )
    if "mean_error" in s:
        lines += [
            f"mean error (bias):      {100 * s['mean_error']:+.1f}%  (minus: too slow)",
            f"mean absolute error:    {100 * s['mean_abs_error']:.1f}%  "
            f"({to_units(float(s['mean_abs_error_ms']), units):.1f} {unit})",
            f"largest absolute error: {100 * s['max_abs_error']:.1f}%  "
            f"({to_units(float(s['max_abs_error_ms']), units):.1f} {unit})",
            f"root mean square:       {100 * s['rms_error']:.1f}%",
        ]
        if s["matched"] < 10:
            lines.append(
                f"Only {s['matched']} passes: too few for a band you can rely on (ten or more at "
                "several speeds, in both directions, are better)."
            )
    return "\n".join(lines)
