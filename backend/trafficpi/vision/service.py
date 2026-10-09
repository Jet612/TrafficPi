"""The vision service: camera, detector, tracker and rules, writing what they find to the database.

The loop in :class:`VisionService` does only the vision work. Everything a viewer needs (boxes,
status, the latest frame) is published to a :class:`LiveState` that the web server thread reads;
encoding preview images and talking to browsers never happens on the camera loop.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np

from trafficpi.core.clock import WallClock
from trafficpi.db.store import Store
from trafficpi.vision.detector import Detector
from trafficpi.vision.engine import TrafficEngine
from trafficpi.vision.lines import parse_counting_lines
from trafficpi.vision.metrics import SystemSample, SystemSampler, decode_throttled
from trafficpi.vision.rules import CountEvent, CountingLine, CountingRule, Rule
from trafficpi.vision.shutdown import StopRequest
from trafficpi.vision.sources import EndOfStream, Frame, FrameSource
from trafficpi.vision.tracker import ByteTracker, TrackerConfig, TrackUpdate

log = logging.getLogger("trafficpi.vision")

MAX_PENDING_WRITES = 1000


@dataclass(frozen=True, slots=True)
class ServiceConfig:
    metrics_every_s: float = 5.0  # how often CPU, memory and temperature are sampled
    recent_events: int = 30  # how many count events the live view remembers
    fps_smoothing: float = 0.1  # weight of the newest frame in the frame-rate average


class LiveState:
    """What the vision service currently sees, shared with the web server thread.

    The loop calls the ``set_*`` and ``publish_*`` methods; the server calls :meth:`snapshot`,
    :meth:`latest_frame` and :meth:`wait_for_frame`. All of it is guarded by one lock held only
    for brief copies.
    """

    def __init__(self, recent_events: int = 30) -> None:
        self._condition = threading.Condition()
        self._started = time.time()
        self._frame_index = -1
        self._image: np.ndarray | None = None
        self._frame_seq = 0
        self._updated_ms = 0
        self._fps = 0.0
        self._tracks: list[dict[str, Any]] = []
        self._events: deque[dict[str, Any]] = deque(maxlen=recent_events)
        self._lines: list[dict[str, Any]] = []
        self._system: dict[str, Any] = {}
        self._camera_ok = False
        self._camera_error: str | None = None
        self._profile_version = 0
        self._detector: str = ""

    # --- written by the vision loop ---------------------------------------------------------

    def publish_frame(
        self,
        frame: Frame,
        tracks: Sequence[TrackUpdate],
        updated_ms: int,
        fps: float,
    ) -> None:
        snapshot = [_track_json(t) for t in tracks]
        with self._condition:
            self._frame_index = frame.index
            self._image = frame.image
            self._frame_seq += 1
            self._updated_ms = updated_ms
            self._fps = fps
            self._tracks = snapshot
            self._camera_ok, self._camera_error = True, None
            self._condition.notify_all()

    def add_event(self, event: CountEvent, occurred_at_ms: int) -> None:
        with self._condition:
            self._events.appendleft(
                {
                    "t": occurred_at_ms,
                    "line": event.line_id,
                    "direction": event.direction,
                    "class": event.label,
                    "track": event.track_id,
                }
            )

    def set_lines(self, lines: Sequence[CountingLine], profile_version: int) -> None:
        with self._condition:
            self._lines = [
                {"id": c.id, "name": c.name, "a": list(c.line.a), "b": list(c.line.b)}
                for c in lines
            ]
            self._profile_version = profile_version

    def set_system(self, sample: SystemSample) -> None:
        now, since = (
            decode_throttled(sample.throttled) if sample.throttled is not None else ([], [])
        )
        with self._condition:
            self._system = {
                "cpu_cores": round(sample.process_cores, 2),
                "cpu_percent": round(sample.total_percent, 1),
                "rss_mb": round(sample.rss_mb, 1),
                "soc_temp_c": None if sample.soc_temp_c is None else round(sample.soc_temp_c, 1),
                "throttled_now": now,
                "throttled_since_boot": since,
            }

    def set_camera_error(self, message: str) -> None:
        with self._condition:
            self._camera_ok, self._camera_error = False, message
            self._condition.notify_all()

    def set_detector(self, name: str) -> None:
        with self._condition:
            self._detector = name

    # --- read by the web server -------------------------------------------------------------

    def snapshot(self, now_ms: int | None = None) -> dict[str, Any]:
        with self._condition:
            return {
                "uptime_s": round(time.time() - self._started, 1),
                "updated_ms": self._updated_ms,
                "frame": self._frame_index,
                "fps": round(self._fps, 1),
                "camera": {"ok": self._camera_ok, "error": self._camera_error},
                "detector": self._detector,
                "profile_version": self._profile_version,
                "lines": list(self._lines),
                "tracks": list(self._tracks),
                "events": list(self._events),
                "system": dict(self._system),
            }

    def latest_frame(self) -> tuple[int, np.ndarray | None]:
        """The sequence number and image of the newest frame (an RGB array, not to be modified)."""
        with self._condition:
            return self._frame_seq, self._image

    def wait_for_frame(self, after_seq: int, timeout: float = 2.0) -> tuple[int, np.ndarray | None]:
        """Block until a frame newer than ``after_seq`` exists, or ``timeout`` passes."""
        with self._condition:
            self._condition.wait_for(lambda: self._frame_seq > after_seq, timeout)
            return self._frame_seq, self._image


def _track_json(track: TrackUpdate) -> dict[str, Any]:
    return {
        "id": track.id,
        "label": track.label,
        "score": round(track.score, 3),
        "box": [round(v, 4) for v in track.box],
        "velocity": [round(v, 4) for v in track.velocity],
        "confirmed": track.confirmed,
    }


class VisionService:
    """Runs the camera loop and keeps the database and the live view up to date."""

    def __init__(
        self,
        source: FrameSource,
        detector: Detector,
        store: Store,
        *,
        live: LiveState | None = None,
        clock: WallClock | None = None,
        timestamp_ms: Callable[[Frame], int] | None = None,
        tracker_config: TrackerConfig | None = None,
        config: ServiceConfig | None = None,
    ) -> None:
        self.source = source
        self.detector = detector
        self.store = store
        self.config = config or ServiceConfig()
        self.live = live or LiveState(self.config.recent_events)
        self._clock = clock or WallClock()
        # Sensor timestamps are accurate wall time for a camera; a replay overrides this.
        self._timestamp_ms = timestamp_ms or (
            lambda frame: self._clock.epoch_ms(frame.timestamp_ns)
        )
        self._engine = TrafficEngine(ByteTracker(tracker_config))
        self._tracker_config = tracker_config
        self._reload = threading.Event()
        self._pending: deque[tuple[CountEvent, int]] = deque(maxlen=MAX_PENDING_WRITES)
        self.profile_version = 0
        self.frames = 0
        self.events_recorded = 0

    # --- configuration ----------------------------------------------------------------------

    def load_profile(self) -> int:
        """(Re)load the counting lines from the database; returns the profile version."""
        profile = self.store.get_profile()
        lines: list[CountingLine] = []
        version = 0
        if profile is not None:
            document, version = profile
            try:
                lines = parse_counting_lines(document)
            except ValueError as exc:
                log.error("ignoring invalid profile v%s: %s", version, exc)
        rules: list[Rule] = [CountingRule(lines)] if lines else []
        self._engine.rules = rules
        self.profile_version = version
        self.live.set_lines(lines, version)
        log.info("profile v%s loaded: %d counting line(s)", version, len(lines))
        return version

    def request_reload(self) -> None:
        """Ask the loop to reload the profile before its next frame (safe from any thread)."""
        self._reload.set()

    # --- the loop ---------------------------------------------------------------------------

    def run(self, stop: StopRequest, *, max_frames: int | None = None) -> None:
        """Process frames until ``stop`` is requested, the source ends or ``max_frames`` is hit."""
        self.load_profile()
        sampler = SystemSampler()
        next_metrics = time.monotonic()
        fps = 0.0
        last = time.perf_counter()
        while not stop.requested and (max_frames is None or self.frames < max_frames):
            if self._reload.is_set():
                self._reload.clear()
                self.load_profile()
            try:
                frame = self.source.read()
            except EndOfStream:
                break
            detections = self.detector.detect(frame.image)
            result = self._engine.update(frame.timestamp_ns, detections)

            now_ms = self._timestamp_ms(frame)
            for event in result.events:
                if isinstance(event, CountEvent):
                    self._record(event, now_ms)

            now = time.perf_counter()
            instant = 1 / max(now - last, 1e-6)
            last = now
            fps = instant if fps == 0.0 else fps + self.config.fps_smoothing * (instant - fps)
            self.live.publish_frame(frame, result.tracks, now_ms, fps)
            self.frames += 1

            if time.monotonic() >= next_metrics:
                self.live.set_system(sampler.sample())
                next_metrics = time.monotonic() + self.config.metrics_every_s

    def _record(self, event: CountEvent, occurred_at_ms: int) -> None:
        """Store a count; if the database is unavailable, keep it for the next attempt."""
        self._pending.append((event, occurred_at_ms))
        self.live.add_event(event, occurred_at_ms)
        while self._pending:
            queued, at_ms = self._pending[0]
            try:
                self.store.record_count(queued, at_ms)
            except Exception:
                log.exception(
                    "could not store a count; will retry (%d waiting)", len(self._pending)
                )
                return
            self._pending.popleft()
            self.events_recorded += 1
