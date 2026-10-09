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
from trafficpi.core.profile import Profile, ProfileError, empty_profile, parse_profile
from trafficpi.core.trail_settings import DEFAULT_KEEP_DAYS
from trafficpi.db.store import Store
from trafficpi.vision.detector import Detector
from trafficpi.vision.engine import EngineResult, TrafficEngine
from trafficpi.vision.metrics import SystemSample, SystemSampler, decode_throttled
from trafficpi.vision.profile import DEFAULT_ASPECT, compile_profile
from trafficpi.vision.rules import CountEvent, CountingRule, Rule, StopEvent, StopSignRule
from trafficpi.vision.shutdown import StopRequest
from trafficpi.vision.sources import EndOfStream, Frame, FrameSource
from trafficpi.vision.tracker import ByteTracker, TrackerConfig, TrackUpdate
from trafficpi.vision.trails import FinishedTrail, TrailConfig, TrailRecorder, trail_classes

log = logging.getLogger("trafficpi.vision")

MAX_PENDING_WRITES = 1000
MAX_PENDING_TRAILS = 2000
TRAIL_RETRY_S = 5.0  # after a failed write, wait this long before trying again
TRAIL_CLEANUP_CHUNK = 2000  # trails deleted in one transaction
TRAIL_CLEANUP_CHUNKS_PER_PASS = 5  # so that a big backlog never holds up the camera loop


class CameraUnavailable(RuntimeError):
    """The camera could not be brought back within ``ServiceConfig.camera_giveup_s``."""


@dataclass(frozen=True, slots=True)
class ServiceConfig:
    metrics_every_s: float = 5.0  # how often CPU, memory and temperature are sampled
    recent_events: int = 30  # how many count events the live view remembers
    fps_smoothing: float = 0.1  # weight of the newest frame in the frame-rate average
    camera_retry_s: float = 5.0  # pause before trying again to start a camera that failed
    # After this long without a working camera the service stops with CameraUnavailable, so that
    # a supervisor (systemd) restarts the whole process. Starting afresh also resets libcamera,
    # and bounds what Picamera2 leaks (a file descriptor) on every failed attempt.
    camera_giveup_s: float = 300.0
    trail_cleanup_every_s: float = (
        6 * 3600.0
    )  # how often trails older than the owner wants are deleted


class LiveState:
    """What the vision service currently sees, shared with the web server thread.

    The loop calls the ``set_*`` and ``publish_*`` methods; the server calls :meth:`snapshot`,
    :meth:`latest_frame` and :meth:`wait_for_frame`. All of it is guarded by one lock held only
    for brief copies.
    """

    def __init__(self, recent_events: int = 30, stale_after_s: float = 5.0) -> None:
        self._condition = threading.Condition()
        self._stale_after_s = stale_after_s
        self._last_frame_at = 0.0  # time.monotonic() when the newest frame arrived
        self._started = time.time()
        self._frame_index = -1
        self._image: np.ndarray | None = None
        self._frame_seq = 0
        self._updated_ms = 0
        self._fps = 0.0
        self._tracks: list[dict[str, Any]] = []
        self._events: deque[dict[str, Any]] = deque(maxlen=recent_events)
        self._lines: list[dict[str, Any]] = []
        self._zones: list[dict[str, Any]] = []
        self._profile_error: str | None = None
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
            self._last_frame_at = time.monotonic()
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

    def set_profile(self, profile: Profile, version: int) -> None:
        """Publish the lines and zones now in use, and clear any earlier complaint about them."""
        zones = (
            ("detection", profile.detection_zones),
            ("exclusion", profile.exclusion_zones),
        )
        with self._condition:
            self._lines = [
                {"id": c.id, "name": c.name, "a": list(c.a), "b": list(c.b)} for c in profile.lines
            ]
            self._zones = [
                {"id": z.id, "name": z.name, "kind": kind, "points": [list(p) for p in z.points]}
                for kind, group in zones
                for z in group
            ]
            self._profile_version = version
            self._profile_error = None

    def set_profile_error(self, message: str) -> None:
        """Say that the saved profile cannot be used (whatever was in use stays in use)."""
        with self._condition:
            self._profile_error = message

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

    def snapshot(self) -> dict[str, Any]:
        with self._condition:
            camera_ok, camera_error, fps = self._camera_ok, self._camera_error, self._fps
            silent_s = time.monotonic() - self._last_frame_at
            if camera_ok and silent_s > self._stale_after_s:
                # The loop is stuck waiting on the camera, so it cannot say so itself.
                camera_ok, camera_error = False, f"no frames for {silent_s:.0f} s"
            if not camera_ok:
                fps = 0.0
            return {
                "uptime_s": round(time.time() - self._started, 1),
                "updated_ms": self._updated_ms,
                "frame": self._frame_index,
                "fps": round(fps, 1),
                "camera": {"ok": camera_ok, "error": camera_error},
                "detector": self._detector,
                "profile_version": self._profile_version,
                "profile_error": self._profile_error,
                "lines": list(self._lines),
                "zones": list(self._zones),
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
        perf_counter: Callable[[], float] = time.perf_counter,
    ) -> None:
        self.source = source
        self.detector = detector
        self.store = store
        self.config = config or ServiceConfig()
        self.live = live or LiveState(self.config.recent_events)
        self._clock = clock or WallClock()
        self._perf_counter = perf_counter
        # Sensor timestamps are accurate wall time for a camera; a replay overrides this.
        self._timestamp_ms = timestamp_ms or (
            lambda frame: self._clock.epoch_ms(frame.timestamp_ns)
        )
        self._engine = TrafficEngine(ByteTracker(tracker_config))
        self._tracker_config = tracker_config
        self._reload = threading.Event()
        self._pending: deque[tuple[CountEvent | StopEvent, int]] = deque(maxlen=MAX_PENDING_WRITES)
        self.profile_version = 0
        self._active_profile: Profile | None = None  # what the engine is running on right now
        self.frames = 0
        self.events_recorded = 0
        self._camera_failures = 0  # in a row, since a frame last arrived
        self._camera_down_since: float | None = None  # time.monotonic() of the first of them
        self._trails: TrailRecorder | None = None  # None while trails are switched off
        self._pending_trails: deque[FinishedTrail] = deque(maxlen=MAX_PENDING_TRAILS)
        self._trail_retry_at = 0.0  # time.monotonic(): do not try to store trails before this
        self._next_trail_cleanup = 0.0  # time.monotonic(): delete old trails when this has passed
        self._keep_days = DEFAULT_KEEP_DAYS
        self.trails_recorded = 0

    # --- configuration ----------------------------------------------------------------------

    def load_profile(self) -> int:
        """(Re)load the camera profile from the database; returns the version in use.

        A saved profile that cannot be used is reported (in the log and the live state) and the
        one already running stays, so that a bad edit never stops the counting. At start-up there
        is nothing to keep: nothing is counted until the profile is fixed.
        """
        stored = self.store.get_profile()
        profile, version = empty_profile(), 0
        if stored is not None:
            document, saved_version = stored
            try:
                profile, version = parse_profile(document), saved_version
            except ProfileError as exc:
                problem = f"the saved profile (version {saved_version}) cannot be used: {exc}"
                log.error("%s", problem)
                if self._active_profile is not None:
                    self.live.set_profile_error(problem)
                    return self.profile_version
                profile = empty_profile()
                self._apply(profile, 0)
                self.live.set_profile_error(problem)
                return 0
        self._apply(profile, version)
        return version

    def _aspect(self) -> float:
        """The picture's width over its height: a stop sign measures distances in both."""
        try:
            width, height = self.source.frame_size
        except Exception:  # a source that cannot say yet: the usual shape
            return DEFAULT_ASPECT
        return width / height if width > 0 and height > 0 else DEFAULT_ASPECT

    def _apply(self, profile: Profile, version: int) -> None:
        compiled = compile_profile(profile, self._aspect())
        lines = compiled.counting_lines
        rules: list[Rule] = [CountingRule(lines)] if lines else []
        if compiled.stop_signs:
            rules.append(StopSignRule(compiled.stop_signs))
        self._engine.rules = rules
        self._engine.detection_filter = compiled.detection_filter
        self._active_profile = profile
        self.profile_version = version
        self.live.set_profile(profile, version)
        log.info(
            "profile v%s loaded: %d counting line(s), %d stop sign(s), %d detection zone(s), "
            "%d exclusion zone(s)",
            version,
            len(lines),
            len(compiled.stop_signs),
            len(profile.detection_zones),
            len(profile.exclusion_zones),
        )

    def request_reload(self) -> None:
        """Ask the loop to reload the profile before its next frame (safe from any thread)."""
        self._reload.set()

    # --- the loop ---------------------------------------------------------------------------

    def run(self, stop: StopRequest, *, max_frames: int | None = None) -> None:
        """Process frames until ``stop`` is requested, the source ends or ``max_frames`` is hit.

        The service starts and stops the frame source itself, so pass one that is not started. A
        camera that cannot be started, fails or stops delivering is reported through the live
        state and started again every ``camera_retry_s`` seconds; the loop never gives up.
        """
        self.load_profile()
        self._load_trail_settings()
        if not self._open_source(stop):
            return
        try:
            self._loop(stop, max_frames)
        finally:
            self._close_source()
            self._finish_trails()

    def _loop(self, stop: StopRequest, max_frames: int | None) -> None:
        sampler = SystemSampler()
        next_metrics = time.monotonic()
        interval = 0.0  # smoothed time between frames; the frame rate is its inverse
        last = self._perf_counter()
        while not stop.requested and (max_frames is None or self.frames < max_frames):
            if self._reload.is_set():
                self._reload.clear()
                self.load_profile()
                self._load_trail_settings()
            try:
                frame = self.source.read()
            except EndOfStream:
                break
            except Exception as exc:
                self._report_camera_failure(f"camera failed: {exc}", exc)
                self._close_source()
                if stop.wait(self.config.camera_retry_s) or not self._open_source(stop):
                    break
                continue
            if self._camera_failures:
                log.info("the camera is delivering frames again")
                self._camera_failures = 0
                self._camera_down_since = None
            detections = self.detector.detect(frame.image)
            result = self._engine.update(frame.timestamp_ns, detections)

            now_ms = self._timestamp_ms(frame)
            for event in result.events:
                if isinstance(event, CountEvent | StopEvent):
                    self._record(event, now_ms)
            self._follow_trails(result, now_ms)

            now = self._perf_counter()
            gap = now - last
            last = now
            # Average the gaps and invert, rather than averaging 1/gap: with jittery arrival
            # times the second reads high (a steady 30 fps camera showed 33).
            interval = (
                gap if interval == 0.0 else interval + self.config.fps_smoothing * (gap - interval)
            )
            self.live.publish_frame(frame, result.tracks, now_ms, 1 / max(interval, 1e-6))
            self.frames += 1

            if time.monotonic() >= next_metrics:
                self.live.set_system(sampler.sample())
                next_metrics = time.monotonic() + self.config.metrics_every_s
            if time.monotonic() >= self._next_trail_cleanup:
                self._clean_up_trails()

    # --- the frame source's life cycle ------------------------------------------------------

    def _open_source(self, stop: StopRequest) -> bool:
        """Start the source, trying again until it works. False if a stop came first."""
        while not stop.requested:
            try:
                self.source.start()
            except Exception as exc:
                self._report_camera_failure(f"cannot start the camera: {exc}", exc)
                self._close_source()  # release whatever was half open
                self._give_up_if_hopeless(exc)
                if stop.wait(self.config.camera_retry_s):
                    return False
                continue
            return True
        return False

    def _close_source(self) -> None:
        try:
            self.source.stop()
        except Exception:
            log.exception("could not stop the camera cleanly")

    def _give_up_if_hopeless(self, exc: BaseException) -> None:
        down_s = time.monotonic() - (self._camera_down_since or time.monotonic())
        if down_s > self.config.camera_giveup_s:
            raise CameraUnavailable(
                f"no camera for {down_s:.0f} s ({exc}); stopping so the service can be restarted"
            ) from exc

    def _report_camera_failure(self, message: str, exc: BaseException) -> None:
        if self._camera_down_since is None:
            self._camera_down_since = time.monotonic()
        self._camera_failures += 1
        if self._camera_failures == 1:  # a traceback once per outage is enough
            log.error("%s", message, exc_info=exc)
        else:
            log.error("%s (failure %d in a row)", message, self._camera_failures)
        self.live.set_camera_error(message)

    def _record(self, event: CountEvent | StopEvent, occurred_at_ms: int) -> None:
        """Store a count or a stop; if the database is unavailable, keep it for the next attempt."""
        self._pending.append((event, occurred_at_ms))
        if isinstance(event, CountEvent):
            self.live.add_event(event, occurred_at_ms)
        while self._pending:
            queued, at_ms = self._pending[0]
            try:
                if isinstance(queued, CountEvent):
                    self.store.record_count(queued, at_ms)
                else:
                    self.store.record_stop(queued, at_ms)
            except Exception:
                log.exception(
                    "could not store an event; will retry (%d waiting)", len(self._pending)
                )
                return
            self._pending.popleft()
            self.events_recorded += 1
            if isinstance(queued, StopEvent):
                log.info(
                    "%s at %s: %s (still %.1f s, slowest %.2f, watched %.1f s)",
                    queued.label,
                    queued.sign_id,
                    queued.outcome,
                    queued.hold_s,
                    queued.min_speed,
                    queued.observed_s,
                )

    # --- trails (plan.md D21) ---------------------------------------------------------------

    def _load_trail_settings(self) -> None:
        """Read which trails are saved and for how long (the owner may have changed it)."""
        try:
            settings = self.store.get_trail_settings()
        except Exception:
            log.exception("could not read the trail settings; keeping the ones in use")
            return
        self._keep_days = settings.keep_days
        if not settings.enabled:
            self._trails = None  # paths in progress are dropped: switched off means no more
            return
        config = TrailConfig(classes=trail_classes(people=settings.people))
        if self._trails is None:
            self._trails = TrailRecorder(config)
        else:
            self._trails.config = config

    def _follow_trails(self, result: EngineResult, now_ms: int) -> None:
        if self._trails is None:
            return
        self._pending_trails.extend(self._trails.update(now_ms, result.tracks, result.ended))
        self._write_trails()

    def _write_trails(self) -> None:
        """Store finished trails; if the database is unavailable, keep them for another try.

        A failed write is not tried again for a few seconds (the loop runs thirty times a second,
        and each failure logs), and the queue is bounded, so a database that stays away costs the
        oldest trails and nothing else.
        """
        if not self._pending_trails or time.monotonic() < self._trail_retry_at:
            return
        batch = list(self._pending_trails)
        try:
            self.store.add_trails(batch)
        except Exception:
            self._trail_retry_at = time.monotonic() + TRAIL_RETRY_S
            log.exception("could not store %d trail(s); will retry", len(batch))
            return
        for _ in batch:
            self._pending_trails.popleft()
        self.trails_recorded += len(batch)

    def _finish_trails(self) -> None:
        """The service is stopping: the paths in progress are finished and stored as they stand."""
        if self._trails is None:
            return
        try:
            self._pending_trails.extend(self._trails.finish_all())
            self._trail_retry_at = 0.0
            self._write_trails()
        except Exception:
            log.exception("could not store the trails in progress")

    def _clean_up_trails(self) -> None:
        """Delete trails older than the owner wants kept, a few chunks at a time."""
        self._next_trail_cleanup = time.monotonic() + self.config.trail_cleanup_every_s
        cutoff = self._clock.now_ms() - self._keep_days * 86_400_000
        removed = 0
        try:
            for _ in range(TRAIL_CLEANUP_CHUNKS_PER_PASS):
                gone = self.store.delete_trails_before(cutoff, limit=TRAIL_CLEANUP_CHUNK)
                removed += gone
                if gone < TRAIL_CLEANUP_CHUNK:
                    break
            else:  # more remain: carry on soon, instead of waiting for the next scheduled pass
                self._next_trail_cleanup = time.monotonic() + 30.0
        except Exception:
            log.exception("could not delete old trails")
        if removed:
            log.info("deleted %d trail(s) older than %d days", removed, self._keep_days)
