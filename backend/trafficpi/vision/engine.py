"""Detections in, tracks and events out: the hardware-free heart of the vision service."""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field

from trafficpi.core.types import Detection
from trafficpi.vision.rules import CountEvent, Event, Rule
from trafficpi.vision.tracker import ByteTracker, TrackUpdate
from trafficpi.vision.zones import DetectionFilter


@dataclass(frozen=True, slots=True)
class EngineResult:
    tracks: list[TrackUpdate]  # tracks matched in this frame
    events: list[Event]
    ended: list[int]  # ids of tracks dropped in this frame


class TrafficEngine:
    """Runs the tracker and the rules over a stream of per-frame detections.

    Nothing here knows about cameras or accelerators, so the same code runs live and when
    replaying recorded detections.
    """

    def __init__(
        self,
        tracker: ByteTracker | None = None,
        rules: Sequence[Rule] = (),
        detection_filter: DetectionFilter | None = None,
    ) -> None:
        self.tracker = tracker or ByteTracker()
        self.rules = list(rules)
        self.detection_filter = detection_filter  # None: look at every detection

    def update(self, timestamp_ns: int, detections: Sequence[Detection]) -> EngineResult:
        if self.detection_filter is not None:
            detections = self.detection_filter.apply(detections)
        tracked = self.tracker.update(timestamp_ns, detections)
        events: list[Event] = []
        for rule in self.rules:
            events.extend(rule.update(timestamp_ns, tracked.tracks))
        for rule in self.rules:
            rule.forget(tracked.ended)
        return EngineResult(tracked.tracks, events, tracked.ended)


@dataclass(slots=True)
class CountTotals:
    """Running totals of count events, by line, direction and class."""

    counts: Counter[tuple[str, int, str]] = field(default_factory=Counter)

    def add(self, event: Event) -> None:
        if isinstance(event, CountEvent):
            self.counts[(event.line_id, event.direction, event.label)] += 1

    def total(self, line_id: str | None = None, direction: int | None = None) -> int:
        return sum(
            n
            for (line, d, _), n in self.counts.items()
            if (line_id is None or line == line_id) and (direction is None or d == direction)
        )
