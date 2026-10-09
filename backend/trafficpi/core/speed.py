"""A speed in a vehicle's own sizes a second, put in real units: a rough estimate (plan.md D41).

The stop rule (``vision/rules/stop_sign.py``) measures how fast a vehicle moves in its own *sizes*
a second, a size being the geometric mean of the width and the height of its box in the picture.
That takes the perspective out (a vehicle twice as far looks half as big and moves half as far in
the picture) and needs no calibration. To put it in metres a second a size has to stand for a
length. It is **not** the vehicle's length: a car 4.5 m long and 1.5 m high, seen from the side,
has a size of sqrt(4.5 x 1.5) = 2.6 m, and seen end-on (1.8 m wide) about 1.6 m. The sizes below
are for a view in between, mostly from the side, as the street here is seen. Counting a car's
size as its length, as the Data page first did, overstated every speed about 1.8 times.

An estimate made this way is good to about 40%: the view angle, the box (which is never exact)
and the kind of car all matter. It is shown as "about", rounded to what it can claim, and the
pages say so. A real figure needs a calibration (plan.md M6).
"""

from __future__ import annotations

import math
from collections.abc import Mapping

# What a size stands for, in metres, by the class label of the detector.
SIZE_M: Mapping[str, float] = {
    "car": 2.5,
    "truck": 3.5,  # a pickup or a van: 5.5 m by 2.3 m, side-on, 3.6 m
    "bus": 5.5,  # 11 m by 3 m, side-on, 5.7 m
    "motorcycle": 1.7,  # with its rider: 2.1 m by 1.4 m
    "bicycle": 1.6,  # with its rider: 1.7 m by 1.5 m
}
DEFAULT_SIZE_M = SIZE_M["car"]  # any other kind, and a vehicle with no label
MPS_TO_MPH = 2.2369362920544
MPS_TO_KMH = 3.6


def size_metres(label: str | None) -> float:
    """What a size stands for, in metres, for a vehicle of this kind (a car if unknown)."""
    return SIZE_M.get(label or "", DEFAULT_SIZE_M)


def estimated_speed_ms(sizes_per_second: float, label: str | None) -> float:
    """An estimate of a vehicle's speed in metres a second, from its speed in its own sizes."""
    return sizes_per_second * size_metres(label)


def to_units(speed_ms: float, units: str) -> float:
    """Metres a second as miles an hour (``"mph"``) or kilometres an hour (anything else)."""
    return speed_ms * (MPS_TO_MPH if units == "mph" else MPS_TO_KMH)


def rough_speed(value: float) -> int:
    """A speed rounded to what an estimate can claim: a whole number below 10, then the nearest 5
    (halves go up, as in the dashboard's ``Math.round``)."""
    if value < 9.5:
        return math.floor(value + 0.5)
    return 5 * math.floor(value / 5 + 0.5)


def speed_phrase(speed_ms: float, units: str) -> str:
    """ "about 30 mph", "about 50 km/h", or "almost still"."""
    value = to_units(speed_ms, units)
    if value < 1:
        return "almost still"
    return f"about {rough_speed(value)} {'mph' if units == 'mph' else 'km/h'}"
