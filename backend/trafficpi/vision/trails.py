"""Trails: the path each vehicle took, saved when its track ends (plan.md D21).

A trail is the ground point of a confirmed track (the middle of the bottom edge of its box, where
the vehicle meets the road: the point zones and counting lines judge by), sampled about five times
a second, in the same 0 to 1 fractions of the frame as zones and lines. It is one small row, not
frames and not video: about 100 to 300 bytes for a vehicle that crosses the picture.

What is kept and what is not:

* only vehicles, unless people are switched on (``trail_classes``);
* only tracks that moved: the points must spread over at least ``min_extent`` of the picture. A
  parked car's detections flicker, so its path *length* grows with time, but its *spread* stays
  tiny, which is why the spread is what is judged;
* nothing from an exclusion zone or outside the detection zones, because the zone filter runs
  before the tracker, so a vehicle that is ignored never becomes a track.

This module knows nothing about databases, cameras or the accelerator.
"""

from __future__ import annotations

import math
import struct
import zlib
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

import numpy as np

from trafficpi.vision.tracker import TrackUpdate

FORMAT_VERSION = 1
SAMPLE_INTERVAL_MS = 200  # about five points a second
# A point is taken at the first frame that is at least this much of the interval after the last
# one. Without the allowance a 30 fps camera (a frame every 33 ms) would wait for the seventh frame,
# 231 ms on, and give four and a third points a second instead of five.
_INTERVAL_ALLOWANCE = 0.9
MIN_EXTENT = 0.05  # a trail must spread over at least this much of the picture (its diagonal)
MIN_POINTS = 3
MAX_POINTS = 1500  # five minutes at five a second; a longer track is saved in pieces
VEHICLES = ("car", "truck", "bus", "motorcycle", "bicycle")
_FULL_SCALE = 65535  # a coordinate is stored as a 16-bit fraction of the picture


def trail_classes(*, people: bool = False) -> frozenset[str]:
    """The kinds of object whose trails are saved: vehicles, and people only if asked."""
    return frozenset((*VEHICLES, "person") if people else VEHICLES)


# --- the stored form ------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TrailPoints:
    """A decoded path: when each point was (ms after the trail began) and where (0 to 1)."""

    offsets_ms: np.ndarray
    x: np.ndarray
    y: np.ndarray

    def __len__(self) -> int:
        return len(self.offsets_ms)


def _quantize(values: Sequence[float]) -> np.ndarray:
    return np.rint(np.clip(np.asarray(values, dtype=np.float64), 0.0, 1.0) * _FULL_SCALE).astype(
        np.int64
    )


def encode_path(offsets_ms: Sequence[int], xs: Sequence[float], ys: Sequence[float]) -> bytes:
    """Pack a path: times in ms and the two coordinates as 16-bit fractions, each as the change
    from the point before, then compressed (the changes are small, so it shrinks well)."""
    if not len(offsets_ms) == len(xs) == len(ys):
        raise ValueError("a path needs as many times as x values and y values")
    columns = (np.asarray(offsets_ms, dtype=np.int64), _quantize(xs), _quantize(ys))
    body = b"".join(np.diff(column, prepend=0).astype("<i4").tobytes() for column in columns)
    return zlib.compress(struct.pack("<BI", FORMAT_VERSION, len(offsets_ms)) + body, 6)


def decode_path(blob: bytes) -> TrailPoints:
    """The inverse of :func:`encode_path`; ``ValueError`` for anything that is not a path."""
    try:
        raw = zlib.decompress(blob)
        version, n = struct.unpack_from("<BI", raw)
    except (zlib.error, struct.error) as exc:
        raise ValueError(f"not a stored path: {exc}") from exc
    if version != FORMAT_VERSION:
        raise ValueError(
            f"a path in format {version}; this TrafficPi reads format {FORMAT_VERSION}"
        )
    if len(raw) != 5 + 12 * n:
        raise ValueError("a stored path of the wrong length")
    columns = [
        np.cumsum(np.frombuffer(raw, dtype="<i4", count=n, offset=5 + 4 * n * i).astype(np.int64))
        for i in range(3)
    ]
    return TrailPoints(columns[0], columns[1] / _FULL_SCALE, columns[2] / _FULL_SCALE)


# --- the recorder ---------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TrailConfig:
    classes: frozenset[str] = trail_classes()
    sample_interval_ms: int = SAMPLE_INTERVAL_MS
    min_extent: float = MIN_EXTENT
    min_points: int = MIN_POINTS
    max_points: int = MAX_POINTS


@dataclass(frozen=True, slots=True)
class FinishedTrail:
    """One vehicle's path, ready to be stored."""

    track_id: int
    started_at: int  # ms since the epoch, UTC
    ended_at: int
    label: str
    points: int
    extent: float  # how far the path spreads, as a fraction of the picture's diagonal
    path: bytes  # see encode_path


class _Open:
    """A track being followed."""

    __slots__ = (
        "label",
        "last_at",
        "last_x",
        "last_y",
        "offsets",
        "started_at",
        "track_id",
        "xs",
        "ys",
    )

    def __init__(self, track_id: int, at_ms: int, label: str) -> None:
        self.track_id = track_id
        self.started_at = at_ms
        self.last_at = at_ms
        self.last_x = 0.0
        self.last_y = 0.0
        self.label = label
        self.offsets: list[int] = []
        self.xs: list[float] = []
        self.ys: list[float] = []


class TrailRecorder:
    """Follows confirmed tracks and hands back each finished path that is worth keeping."""

    def __init__(self, config: TrailConfig | None = None) -> None:
        self.config = config or TrailConfig()
        self._open: dict[int, _Open] = {}

    def update(
        self, at_ms: int, tracks: Sequence[TrackUpdate], ended: Iterable[int]
    ) -> list[FinishedTrail]:
        """One frame: the tracks matched in it and the ids of those that ended. ``at_ms`` is the
        frame's time (ms since the epoch)."""
        cfg = self.config
        finished: list[FinishedTrail] = []
        for track in tracks:
            if not track.confirmed or track.label not in cfg.classes:
                self._open.pop(track.id, None)  # a label that turned into something unwanted
                continue
            x, y = track.ground_point
            current = self._open.get(track.id)
            if current is None:
                current = self._open[track.id] = _Open(track.id, at_ms, track.label)
            current.label = track.label
            current.last_at = max(at_ms, current.last_at)
            current.last_x, current.last_y = x, y
            since_last = (
                at_ms - (current.started_at + current.offsets[-1]) if current.offsets else None
            )
            if since_last is None or since_last >= cfg.sample_interval_ms * _INTERVAL_ALLOWANCE:
                self._sample(current, at_ms)
                if len(current.offsets) >= cfg.max_points:  # a very long track is saved in pieces
                    del self._open[track.id]
                    finished.extend(self._finish(current))
        for track_id in ended:
            current = self._open.pop(track_id, None)
            if current is not None:
                finished.extend(self._finish(current))
        return finished

    def finish_all(self) -> list[FinishedTrail]:
        """The service is stopping: every path in progress is finished as it stands."""
        finished = [t for current in self._open.values() for t in self._finish(current)]
        self._open.clear()
        return finished

    @property
    def following(self) -> int:
        """How many tracks are being followed (for tests and the health report)."""
        return len(self._open)

    def _sample(self, current: _Open, at_ms: int) -> None:
        previous = current.offsets[-1] if current.offsets else 0
        current.offsets.append(max(at_ms - current.started_at, previous))  # never backwards
        current.xs.append(current.last_x)
        current.ys.append(current.last_y)

    def _finish(self, current: _Open) -> list[FinishedTrail]:
        cfg = self.config
        # The last position is always part of the path, even if it fell between two samples.
        end_offset = max(
            current.last_at - current.started_at, current.offsets[-1] if current.offsets else 0
        )
        if current.offsets and (
            current.offsets[-1] != end_offset
            or (current.xs[-1], current.ys[-1]) != (current.last_x, current.last_y)
        ):
            current.offsets.append(end_offset)
            current.xs.append(current.last_x)
            current.ys.append(current.last_y)
        if len(current.offsets) < cfg.min_points:
            return []
        extent = math.hypot(max(current.xs) - min(current.xs), max(current.ys) - min(current.ys))
        if extent < cfg.min_extent:
            return []  # it did not go anywhere: a parked car, a flickering detection
        return [
            FinishedTrail(
                track_id=current.track_id,
                started_at=current.started_at,
                ended_at=current.started_at + end_offset,
                label=current.label,
                points=len(current.offsets),
                extent=round(extent, 4),
                path=encode_path(current.offsets, current.xs, current.ys),
            )
        ]
