"""Speeds in metres a second: where each vehicle is on the road and how fast it moves.

Between the tracker and the rules sits a speed meter, once the camera is calibrated
(``core/ground``). For every track in every frame it puts the point where the vehicle meets the
road (the middle of the bottom of its box) on the road plane, in metres, and keeps the last second
or so of those positions. The speed is the slope of a straight line fitted through them by least
squares, against the camera's own timestamps: more steady than the difference of two positions,
because the box shakes a little from frame to frame and a fit averages the shaking out.

A speed is given only when it can be believed, and is otherwise None, which every user of it must
take to mean "not measured":

* too little of the vehicle has been seen (a vehicle that has only just appeared, or that the
  tracker lost for a moment, starts its second again);
* the points do not lie on a line (a track that jumped to another vehicle leaves one far off it);
* the calibration cannot be believed there, in the direction the vehicle is going (the part of the
  picture it does not reach, or too far from the rectangle it was made from);
* it is faster than any road vehicle.

Nothing here knows about cameras, accelerators or databases.
"""

from __future__ import annotations

import math
from collections import deque
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace

from trafficpi.core.ground import GroundCalibration
from trafficpi.vision.tracker import TrackUpdate


@dataclass(frozen=True, slots=True)
class MeterConfig:
    window_s: float = 1.0  # the speed is the one over the last this many seconds
    min_span_s: float = 0.6  # at least this much of it must have been seen
    min_points: int = 5
    max_gap_s: float = 0.4  # a break in the points longer than this starts the history again
    max_residual_m: float = 1.0  # no point may lie further than this from the fitted line
    max_speed_ms: float = (
        45.0  # about 100 mph: faster is no road vehicle, whatever the picture says
    )
    history_points: int = 120  # four seconds at 30 frames a second, and no more
    # A box that touches the left, right or bottom edge of the picture is cut off by it: the vehicle
    # goes on, the bottom of the box does not, and a line fitted through that is a vehicle slowing
    # down. Such a box is not measured. (As a fraction of the picture, about 13 pixels of 1280.)
    edge: float = 0.01


@dataclass(frozen=True, slots=True)
class _Seen:
    t: float  # seconds
    x: float  # metres across the rectangle
    y: float  # metres along it
    u: float  # and where in the picture
    v: float


def fit_velocity(points: Sequence[_Seen]) -> tuple[float, float, float] | None:
    """The velocity ``(across, along)`` in metres a second of the straight line through the points
    that is closest to them (least squares), and how far the farthest point is from that line; None
    if the points are all at the same time."""
    n = len(points)
    mean_t = sum(p.t for p in points) / n
    spread = sum((p.t - mean_t) ** 2 for p in points)
    if spread <= 0.0:
        return None
    mean_x = sum(p.x for p in points) / n
    mean_y = sum(p.y for p in points) / n
    vx = sum((p.t - mean_t) * (p.x - mean_x) for p in points) / spread
    vy = sum((p.t - mean_t) * (p.y - mean_y) for p in points) / spread
    farthest = max(
        math.hypot((p.x - mean_x) - vx * (p.t - mean_t), (p.y - mean_y) - vy * (p.t - mean_t))
        for p in points
    )
    return vx, vy, farthest


class SpeedMeter:
    """Puts every track on the road and gives it a speed, when one can be believed."""

    def __init__(self, calibration: GroundCalibration, config: MeterConfig | None = None) -> None:
        self.calibration = calibration
        self.config = config or MeterConfig()
        self._seen: dict[int, deque[_Seen]] = {}

    def update(self, timestamp_ns: int, tracks: Sequence[TrackUpdate]) -> list[TrackUpdate]:
        """The tracks of one frame with their positions on the road and their speeds."""
        t = timestamp_ns / 1e9
        return [self._measure(t, track) for track in tracks]

    def forget(self, track_ids: Iterable[int]) -> None:
        for track_id in track_ids:
            self._seen.pop(track_id, None)

    @property
    def following(self) -> int:
        """How many tracks have a history (for tests and the health report)."""
        return len(self._seen)

    def _measure(self, t: float, track: TrackUpdate) -> TrackUpdate:
        cfg = self.config
        u, v = track.ground_point
        on_road = self.calibration.to_ground((u, v))
        if on_road is None:  # above the horizon: nothing here is on the road, and nothing is added
            return track
        x0, _, x1, y1 = track.box
        if x0 <= cfg.edge or x1 >= 1 - cfg.edge or y1 >= 1 - cfg.edge:
            return replace(track, ground=on_road)  # cut off by the picture's edge: no speed
        history = self._seen.setdefault(track.id, deque(maxlen=cfg.history_points))
        if history and t - history[-1].t > cfg.max_gap_s:
            history.clear()  # a break: what came before is another stretch
        if not history or t > history[-1].t:  # the same instant twice adds nothing
            history.append(_Seen(t, on_road[0], on_road[1], u, v))
        return replace(track, ground=on_road, speed_ms=self._speed(history))

    def _speed(self, history: deque[_Seen]) -> float | None:
        cfg = self.config
        newest = history[-1].t
        window = [p for p in history if newest - p.t <= cfg.window_s]
        if len(window) < cfg.min_points or newest - window[0].t < cfg.min_span_s:
            return None
        fitted = fit_velocity(window)
        if fitted is None:
            return None
        vx, vy, farthest = fitted
        speed = math.hypot(vx, vy)
        if farthest > cfg.max_residual_m or speed > cfg.max_speed_ms:
            return None
        centre = (sum(p.u for p in window) / len(window), sum(p.v for p in window) / len(window))
        if not self.calibration.believed(centre, (vx, vy)):
            return None
        return speed
