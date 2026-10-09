"""Running recorded detections through the tracker and rules, with no camera or accelerator."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from trafficpi.vision.engine import CountTotals, EngineResult, TrafficEngine
from trafficpi.vision.recorder import read_detection_log
from trafficpi.vision.rules import CountEvent, CountingLine, CountingRule
from trafficpi.vision.tracker import ByteTracker, TrackerConfig
from trafficpi.vision.zones import DetectionFilter


@dataclass(slots=True)
class ReplayResult:
    header: dict[str, Any]
    timestamps_ns: list[int]  # one per logged frame
    results: list[EngineResult]  # one per logged frame
    events: list[CountEvent] = field(default_factory=list)
    totals: CountTotals = field(default_factory=CountTotals)
    confirmed_tracks: set[int] = field(default_factory=set)

    @property
    def duration_s(self) -> float:
        if len(self.timestamps_ns) < 2:
            return 0.0
        return (self.timestamps_ns[-1] - self.timestamps_ns[0]) / 1e9


def replay_log(
    path: Path,
    lines: Sequence[CountingLine] = (),
    tracker_config: TrackerConfig | None = None,
    detection_filter: DetectionFilter | None = None,
) -> ReplayResult:
    """Feed a detection log (see ``trafficpi.vision.recorder``) through the engine."""
    header, frames = read_detection_log(path)
    engine = TrafficEngine(
        ByteTracker(tracker_config), [CountingRule(lines)] if lines else [], detection_filter
    )
    out = ReplayResult(header, [], [])
    for frame in frames:
        result = engine.update(frame.timestamp_ns, frame.detections)
        out.timestamps_ns.append(frame.timestamp_ns)
        out.results.append(result)
        out.confirmed_tracks.update(t.id for t in result.tracks if t.confirmed)
        for event in result.events:
            if isinstance(event, CountEvent):
                out.events.append(event)
                out.totals.add(event)
    return out
