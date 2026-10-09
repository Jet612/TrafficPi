"""Stop-sign monitoring: did a vehicle stop before the line? (plan.md M5)

A stop sign is two things drawn on the picture: an *approach zone*, the stretch of road where a
stop counts, and a *stop line* that ends it. A vehicle is followed while its ground point is in the
zone. When it crosses the line the right way, what it did in the zone is judged:

* ``stopped``: it was still for at least ``hold_s`` seconds, at any place in the zone (a vehicle
  that waited in a queue stopped as much as one at the line);
* ``rolling``: it slowed below ``roll_speed`` but never held still;
* ``did_not_stop``: it never went slower than that;
* ``indeterminate``: it was watched for too short a time to say (it was first seen at the line, or
  the tracker lost it and found it again), or the picture does not allow a speed to be measured.

Nothing here needs the speed of the camera or a calibration: distances are measured in the
vehicle's own size, which is also how far away it is, so "still" and "slow" mean the same near the
camera and far from it. The rule is pure logic over tracks, like the counting rule, so a recorded
pass can be replayed through it.
"""

from __future__ import annotations

import math
from collections import deque
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from typing import Literal

from trafficpi.core.geometry import Line, Polygon
from trafficpi.vision.rules.base import CountEvent, Event
from trafficpi.vision.rules.counting import CountingLine, CountingRule
from trafficpi.vision.tracker import TrackUpdate

Outcome = Literal["stopped", "rolling", "did_not_stop", "indeterminate"]

SAMPLE_SPACING_NS = 60_000_000  # points closer in time than this add nothing; this bounds the work
MAX_SAMPLES = 2_000  # about two minutes at that spacing; the oldest are dropped
# What the fastest speed trusts (a day's "fastest" is the extreme of many passes, so a glitch of the
# tracker, two close cars swapping numbers say, would become the answer if it were let through):
MAX_SIZE_CHANGE = 1.4  # the sizes at the two ends of a window may differ by this factor at most
STEADY = (
    0.4  # and, going faster than a size a second, each half of it covers this much of the other
)
MAX_SPEED = 12.0  # sizes a second: faster than this is no road vehicle, whatever the picture says


@dataclass(frozen=True, slots=True)
class StopEvent(Event):
    """A vehicle crossed a stop line, and this is what it did on the way."""

    sign_id: str
    outcome: Outcome
    hold_s: float  # the longest it was still in the approach zone
    min_speed: float  # the slowest it went there: its own sizes per second, over ``window_s``
    observed_s: float  # how long it was watched in the zone before the line
    # The fastest it went there, in the same units and over the same windows, among those that can
    # be trusted (``fastest``): how fast it arrived. -1 if there was no window to trust.
    max_speed: float = -1.0


@dataclass(frozen=True, slots=True)
class StopSign:
    """One stop line and the road before it, with what counts as a stop."""

    id: str
    approach: Polygon
    line: Line
    name: str = ""
    # Which way across the line goes through the stop sign: +1 from the left of the line's own
    # direction to its right (as on screen, y down), -1 the other way. Crossings the other way
    # (a vehicle that came out of the intersection) are not judged.
    direction: int = 1
    # Width over height of the picture: points are fractions of each, and a distance needs both
    # in the same unit.
    aspect: float = 16 / 9
    classes: frozenset[int] | None = None  # only these kinds must stop; None means all
    hold_s: float = 1.0  # how long a vehicle must be still to have stopped
    still_radius: float = 0.08  # and how little it may move meanwhile: this much of its own size
    roll_speed: float = 0.7  # slower than this (its own sizes a second) is a roll, not a pass
    min_observed_s: float = 0.8  # watched for less than this before the line: cannot say
    window_s: float = 0.5  # over what time a speed is measured
    max_gap_s: float = 0.4  # a break longer than this in the points splits them in two


@dataclass(frozen=True, slots=True)
class _Sample:
    t_ns: int
    x: float  # the ground point, in units of the picture's height
    y: float
    size: float  # the vehicle's size in the same unit: the geometric mean of its box's sides


def _sample(timestamp_ns: int, track: TrackUpdate, aspect: float) -> _Sample:
    x0, y0, x1, y1 = track.box
    gx, gy = track.ground_point
    size = math.sqrt(max((x1 - x0) * aspect, 1e-9) * max(y1 - y0, 1e-9))
    return _Sample(timestamp_ns, gx * aspect, gy, size)


def longest_still(samples: Sequence[_Sample], sign: StopSign) -> float:
    """The longest time (s) the vehicle stayed within ``still_radius`` of its own size.

    A run of points is still when the box around all of them is no bigger across than twice that
    radius. Runs do not cross a break in the points (``max_gap_s``).
    """
    n = len(samples)
    gap_ns = round(sign.max_gap_s * 1e9)
    best_ns = 0
    for i in range(n):
        lo_x = hi_x = samples[i].x
        lo_y = hi_y = samples[i].y
        j = i
        while j + 1 < n:
            nxt = samples[j + 1]
            if nxt.t_ns - samples[j].t_ns > gap_ns:
                break
            new_lo_x, new_hi_x = min(lo_x, nxt.x), max(hi_x, nxt.x)
            new_lo_y, new_hi_y = min(lo_y, nxt.y), max(hi_y, nxt.y)
            if (
                math.hypot(new_hi_x - new_lo_x, new_hi_y - new_lo_y)
                > 2 * sign.still_radius * nxt.size
            ):
                break
            lo_x, hi_x, lo_y, hi_y = new_lo_x, new_hi_x, new_lo_y, new_hi_y
            j += 1
        best_ns = max(best_ns, samples[j].t_ns - samples[i].t_ns)
        if j == n - 1:  # still to the end: no later start can last longer
            break
    return best_ns / 1e9


def _windows(samples: Sequence[_Sample], sign: StopSign) -> Iterator[tuple[int, int]]:
    """The first and last point of every stretch of ``window_s`` that has no break in it."""
    window_ns = round(sign.window_s * 1e9)
    gap_ns = round(sign.max_gap_s * 1e9)
    n = len(samples)
    for i in range(n):
        k = i
        broken = False
        while k + 1 < n and samples[k].t_ns - samples[i].t_ns < window_ns:
            if samples[k + 1].t_ns - samples[k].t_ns > gap_ns:
                broken = True
                break
            k += 1
        if broken or samples[k].t_ns - samples[i].t_ns < window_ns:
            continue
        yield i, k


def _speed(first: _Sample, last: _Sample) -> float:
    """How fast the vehicle went between two points, in its own sizes a second."""
    size = (first.size + last.size) / 2
    moved = math.hypot(last.x - first.x, last.y - first.y)
    return moved / ((last.t_ns - first.t_ns) / 1e9 * size)


def slowest(samples: Sequence[_Sample], sign: StopSign) -> float | None:
    """The slowest speed, in the vehicle's own sizes a second, over any ``window_s``.

    None if the points never cover a whole window without a break (``max_gap_s``) in it.
    """
    best: float | None = None
    for i, k in _windows(samples, sign):
        speed = _speed(samples[i], samples[k])
        best = speed if best is None else min(best, speed)
    return best


def fastest(samples: Sequence[_Sample], sign: StopSign) -> float | None:
    """The fastest speed, in the vehicle's own sizes a second, over any ``window_s`` that can be
    trusted; None if there is none.

    The windows are those of ``slowest``, less the ones a tracker's mistake could make look fast:
    those whose two ends are of very different size (the box was of another vehicle, or half
    hidden), those that go faster than a size a second with almost all of the way covered in one
    half of the window (a leap of the track, not a drive), and those faster than any road vehicle.
    """
    best: float | None = None
    for i, k in _windows(samples, sign):
        first, last = samples[i], samples[k]
        small, large = sorted((first.size, last.size))
        if large > MAX_SIZE_CHANGE * small:
            continue
        speed = _speed(first, last)
        if speed > MAX_SPEED:
            continue
        if speed >= 1.0:
            middle_ns = (first.t_ns + last.t_ns) / 2
            mid = min(samples[i : k + 1], key=lambda s: abs(s.t_ns - middle_ns))
            before = math.hypot(mid.x - first.x, mid.y - first.y)
            after = math.hypot(last.x - mid.x, last.y - mid.y)
            if min(before, after) < STEADY * max(before, after):
                continue
        best = speed if best is None else max(best, speed)
    return best


class StopSignRule:
    """Follows vehicles through each approach zone and judges them at the stop line."""

    def __init__(self, signs: Sequence[StopSign]) -> None:
        self.signs = list(signs)
        self._crossing = CountingRule(
            [CountingLine(id=sign.id, line=sign.line, classes=sign.classes) for sign in self.signs]
        )
        self._samples: dict[tuple[int, str], deque[_Sample]] = {}
        self._judged: set[tuple[int, str]] = set()  # each vehicle is judged once at each sign

    def update(self, timestamp_ns: int, tracks: Sequence[TrackUpdate]) -> list[Event]:
        for track in tracks:
            if not track.confirmed:
                continue
            for sign in self.signs:
                key = (track.id, sign.id)
                if key in self._judged:
                    continue
                if sign.classes is not None and track.class_id not in sign.classes:
                    continue
                if not sign.approach.contains(track.ground_point):
                    continue
                points = self._samples.setdefault(key, deque(maxlen=MAX_SAMPLES))
                if not points or timestamp_ns - points[-1].t_ns >= SAMPLE_SPACING_NS:
                    points.append(_sample(timestamp_ns, track, sign.aspect))

        by_id = {sign.id: sign for sign in self.signs}
        events: list[Event] = []
        for crossing in self._crossing.update(timestamp_ns, tracks):
            if not isinstance(crossing, CountEvent):
                continue
            sign = by_id[crossing.line_id]
            key = (crossing.track_id, sign.id)
            if crossing.direction != sign.direction or key in self._judged:
                continue
            self._judged.add(key)
            seen = list(self._samples.pop(key, ()))
            events.append(_judge(crossing, sign, seen))
        return events

    def forget(self, track_ids: Iterable[int]) -> None:
        gone = set(track_ids)
        self._crossing.forget(gone)
        for key in [k for k in self._samples if k[0] in gone]:
            del self._samples[key]
        self._judged = {key for key in self._judged if key[0] not in gone}


def _judge(crossing: CountEvent, sign: StopSign, seen: list[_Sample]) -> StopEvent:
    observed = (crossing.timestamp_ns - seen[0].t_ns) / 1e9 if seen else 0.0
    hold = longest_still(seen, sign) if seen else 0.0
    speed = slowest(seen, sign) if seen else None
    quickest = fastest(seen, sign) if seen else None
    if hold >= sign.hold_s:
        outcome: Outcome = "stopped"  # seen to stop: that needs no more watching than it took
    elif observed < sign.min_observed_s or speed is None:
        outcome = "indeterminate"
    elif speed < sign.roll_speed:
        outcome = "rolling"
    else:
        outcome = "did_not_stop"
    return StopEvent(
        timestamp_ns=crossing.timestamp_ns,
        track_id=crossing.track_id,
        class_id=crossing.class_id,
        label=crossing.label,
        sign_id=sign.id,
        outcome=outcome,
        hold_s=round(hold, 3),
        min_speed=round(speed, 3) if speed is not None else -1.0,
        observed_s=round(observed, 3),
        max_speed=round(quickest, 3) if quickest is not None else -1.0,
    )
