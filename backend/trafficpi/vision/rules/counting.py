"""Counting vehicles as they cross a line."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Literal

from trafficpi.core.geometry import Line, Point
from trafficpi.vision.rules.base import CountEvent, Event
from trafficpi.vision.tracker import TrackUpdate


@dataclass(frozen=True, slots=True)
class CountingLine:
    """A line to count crossings of.

    Direction is relative to the line's own direction ``a -> b``: crossing from its left to its
    right (as seen on screen, y down) is ``+1``, the other way ``-1``.
    """

    id: str
    line: Line
    name: str = ""
    # Detection jitter would make an object sitting on the line flip sides, so a point only counts
    # as being on a side when it is further from the line than the larger of these two margins:
    margin: float = 0.004  # a fixed distance, as a fraction of the frame
    # A fraction of the object's height (jitter grows with size). In a simulation of a car sitting
    # on the line with realistic detection noise, 0.1 gave false counts for 10% of runs and 0.15
    # or more gave none in 200 runs, while normal, fast and slow crossings were always counted.
    margin_per_height: float = 0.2
    # How many consecutive frames the new side must be seen before the move counts.
    persistence: int = 2
    # Slack at the ends of the segment, as a fraction of its length.
    end_tolerance: float = 0.02
    point: Literal["ground", "center"] = "ground"  # which point of the box is tested
    classes: frozenset[int] | None = None  # only these classes count; None means all


@dataclass(slots=True)
class _Memory:
    side: int = 0  # +1 or -1: the side the object is on; 0: not known yet
    anchor: Point = (0.0, 0.0)  # the last point seen clearly on that side
    pending_side: int = 0  # a different side seen recently, not yet persistent
    pending_frames: int = 0
    counted: set[int] = field(default_factory=set)  # directions already counted


class CountingRule:
    """Counts each tracked object at most once per line and direction.

    The side an object is on is remembered with hysteresis (a margin) and debouncing (the other
    side has to persist for a few frames), so jitter near the line does not count. A crossing only
    counts when the move passes through the segment itself, not around its end, and when the
    object is a confirmed track.
    """

    def __init__(self, lines: Sequence[CountingLine]) -> None:
        self.lines = list(lines)
        self._memory: dict[tuple[int, str], _Memory] = {}

    def update(self, timestamp_ns: int, tracks: Sequence[TrackUpdate]) -> list[Event]:
        events: list[Event] = []
        for track in tracks:
            for cfg in self.lines:
                direction = self._observe(track, cfg)
                if direction:
                    events.append(
                        CountEvent(
                            timestamp_ns=timestamp_ns,
                            track_id=track.id,
                            class_id=track.class_id,
                            label=track.label,
                            line_id=cfg.id,
                            direction=direction,
                        )
                    )
        return events

    def _observe(self, track: TrackUpdate, cfg: CountingLine) -> int:
        """Update what we know about one track and one line; return a counted direction or 0."""
        point = track.ground_point if cfg.point == "ground" else track.center
        height = track.box[3] - track.box[1]
        margin = max(cfg.margin, cfg.margin_per_height * height)
        memory = self._memory.setdefault((track.id, cfg.id), _Memory())

        distance = cfg.line.signed_distance(point)
        if abs(distance) <= margin:  # in the band around the line: undecided
            memory.pending_side, memory.pending_frames = 0, 0
            return 0
        side = 1 if distance > 0 else -1

        if memory.side == 0 or side == memory.side:  # first sighting, or still on the same side
            memory.side, memory.anchor = side, point
            memory.pending_side, memory.pending_frames = 0, 0
            return 0

        # Clearly on the other side. Wait until that has persisted for a few frames.
        if memory.pending_side == side:
            memory.pending_frames += 1
        else:
            memory.pending_side, memory.pending_frames = side, 1
        if memory.pending_frames < cfg.persistence:
            return 0

        anchor = memory.anchor
        memory.side, memory.anchor = side, point
        memory.pending_side, memory.pending_frames = 0, 0

        # Did the move from the old side to the new one go through the segment?
        if cfg.line.intersect(anchor, point, cfg.end_tolerance) is None:
            return 0
        if not track.confirmed or (cfg.classes is not None and track.class_id not in cfg.classes):
            return 0
        if side in memory.counted:
            return 0
        memory.counted.add(side)
        return side

    def forget(self, track_ids: Iterable[int]) -> None:
        gone = set(track_ids)
        for key in [k for k in self._memory if k[0] in gone]:
            del self._memory[key]
