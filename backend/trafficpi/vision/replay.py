"""Running recorded detections through the tracker and rules, with no camera or accelerator."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from trafficpi.core.ground import GroundCalibration
from trafficpi.vision.engine import CountTotals, EngineResult, TrafficEngine
from trafficpi.vision.recorder import read_detection_log
from trafficpi.vision.rules import (
    CountEvent,
    CountingLine,
    CountingRule,
    Rule,
    StopEvent,
    StopSign,
    StopSignRule,
)
from trafficpi.vision.speedmeter import SpeedMeter
from trafficpi.vision.tracker import ByteTracker, TrackerConfig, tracker_config_for
from trafficpi.vision.zones import DetectionFilter


@dataclass(slots=True)
class ReplayResult:
    header: dict[str, Any]
    timestamps_ns: list[int]  # one per logged frame
    results: list[EngineResult]  # one per logged frame
    events: list[CountEvent] = field(default_factory=list)
    stops: list[StopEvent] = field(default_factory=list)
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
    stop_signs: Sequence[StopSign] = (),
    calibration: GroundCalibration | None = None,
) -> ReplayResult:
    """Feed a detection log (see ``trafficpi.vision.recorder``) through the engine.

    Stop signs measure distances by the picture's shape, which the log's header says (when it
    does not, they keep the aspect they were given). With a ``calibration`` the events carry the
    speeds the speed meter measures, which is how a calibration is tried on a recorded clip.
    """
    header, frames = read_detection_log(path)
    rules: list[Rule] = [CountingRule(lines)] if lines else []
    if stop_signs:
        width, height = header.get("frame_size") or (0, 0)
        aspect = width / height if width > 0 and height > 0 else None
        rules.append(StopSignRule([replace(s, aspect=aspect) if aspect else s for s in stop_signs]))
    meter = SpeedMeter(calibration) if calibration is not None else None
    config = tracker_config or tracker_config_for(header.get("model"))
    engine = TrafficEngine(ByteTracker(config), rules, detection_filter, meter)
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
            elif isinstance(event, StopEvent):
                out.stops.append(event)
    return out
