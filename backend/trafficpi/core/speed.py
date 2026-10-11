"""A speed in a vehicle's own sizes a second, put in real units: a rough estimate.

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
pages say so. A real figure needs a calibration: a speed measured on a calibrated
road (``core/ground``) is in metres a second already, needs no size, and is shown to a whole mile
or kilometre an hour, without the "about", and marked as measured.
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


# What a speed on a page or in the mail is worth, in a sentence, for each kind of speed. The
# dashboard says the same words (lib/speed.ts); tests/data/speed_cases.json holds them for both, so
# that they cannot drift. When a calibration has been checked against vehicles of known speed,
# MEASURED_CAVEAT is where its error band goes.
ESTIMATE_CAVEAT = (
    "A speed is an estimate: how fast a vehicle crossed the picture, compared with its own size. "
    "It is good to about 40% and is not a measurement."
)
MEASURED_CAVEAT = (
    "A speed is measured on the road as it was calibrated on the dashboard's Setup page. The "
    "calibration has not yet been checked against a vehicle of known speed, so treat the figure "
    "as unproven."
)


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


def measured_speed(value: float) -> int:
    """A speed measured on a calibrated road, rounded to a whole mile or kilometre an hour (halves
    go up, as in the dashboard's ``Math.round``)."""
    return math.floor(value + 0.5)


def speed_phrase(speed_ms: float, units: str, measured: bool = False) -> str:
    """ "about 30 mph" for an estimate, "30 mph" for a speed measured on a calibrated road, "about
    50 km/h", or "almost still"."""
    value = to_units(speed_ms, units)
    unit = "mph" if units == "mph" else "km/h"
    if value < 1:
        return "almost still"
    if measured:
        return f"{measured_speed(value)} {unit}"
    return f"about {rough_speed(value)} {unit}"


def pass_speeds_phrase(min_speed_ms: float, line_speed_ms: float, units: str) -> str | None:
    """The speeds a calibrated camera measured for one stop pass, in a few words for a caption:
    "slowest 4 mph, at the line 7 mph". Each is left out if it was not measured (it is negative
    then); None if neither was."""
    parts = []
    if min_speed_ms >= 0:
        parts.append(f"slowest {speed_phrase(min_speed_ms, units, measured=True)}")
    if line_speed_ms >= 0:
        parts.append(f"at the line {speed_phrase(line_speed_ms, units, measured=True)}")
    return ", ".join(parts) or None
