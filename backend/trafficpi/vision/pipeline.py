"""The capture-and-detect loop, with the measurements (rate, latency, load) it is judged by."""

from __future__ import annotations

import time
from collections import Counter, deque
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field

from trafficpi.core.types import Detection
from trafficpi.vision.detector import Detector, StreamDetector
from trafficpi.vision.metrics import StageStats, SystemSample, SystemSampler
from trafficpi.vision.shutdown import StopRequest
from trafficpi.vision.sources import EndOfStream, Frame, FrameSource

# Stage names, in pipeline order.
CAPTURE = "capture wait"  # waiting for the camera; idle time, not work
PREPROCESS = "preprocess"
INFER = "infer"
POSTPROCESS = "postprocess"
LOOP = "loop"  # everything from asking for a frame to having detections
LATENCY = "latency"  # sensor timestamp to detections ready
INTERVAL = "frame interval"  # time between consecutive frames, from sensor timestamps

OnFrame = Callable[[Frame, list[Detection]], None]
OnReport = Callable[["WindowReport"], None]
# A frame, what was found in it, the time spent waiting for it and the whole time it took, in ms.
Produced = Iterator[tuple[Frame, list[Detection], float, float]]


@dataclass(slots=True)
class WindowReport:
    """Statistics for the last reporting window."""

    elapsed_s: float  # length of this window
    run_elapsed_s: float  # time since the run started
    frames: int
    skipped: int
    stage_means_ms: dict[str, float]
    detections: Counter[str]
    system: SystemSample

    @property
    def fps(self) -> float:
        return self.frames / self.elapsed_s if self.elapsed_s > 0 else 0.0


@dataclass(slots=True)
class RunReport:
    """Statistics for a whole run."""

    elapsed_s: float = 0.0
    frames: int = 0
    skipped: int = 0
    stats: StageStats = field(default_factory=StageStats)
    detections: Counter[str] = field(default_factory=Counter)
    samples: list[SystemSample] = field(default_factory=list)

    @property
    def fps(self) -> float:
        return self.frames / self.elapsed_s if self.elapsed_s > 0 else 0.0


def _one_at_a_time(source: FrameSource, detector: Detector) -> Produced:
    while True:
        t0 = time.perf_counter()
        try:
            frame = source.read()
        except EndOfStream:
            return
        t1 = time.perf_counter()
        detections = detector.detect(frame.image)
        t2 = time.perf_counter()
        yield frame, detections, (t1 - t0) * 1000, (t2 - t0) * 1000


def _working_ahead(source: FrameSource, detector: StreamDetector) -> Produced:
    waits: deque[float] = deque()

    def frames() -> Iterator[Frame]:
        while True:
            t0 = time.perf_counter()
            try:
                frame = source.read()
            except EndOfStream:
                return
            waits.append((time.perf_counter() - t0) * 1000)
            yield frame

    last = time.perf_counter()
    for frame, detections in detector.detect_stream(frames()):
        now = time.perf_counter()
        yield frame, detections, waits.popleft(), (now - last) * 1000
        last = time.perf_counter()


def run(
    source: FrameSource,
    detector: Detector,
    *,
    seconds: float,
    frame_rate: float = 30.0,
    on_frame: OnFrame | None = None,
    on_report: OnReport | None = None,
    report_every: float = 5.0,
    stop: StopRequest | None = None,
) -> RunReport:
    """Read frames and detect objects for ``seconds``, or until ``stop`` is requested.

    ``frame_rate`` is only used to estimate how many frames the camera produced that this loop
    never saw (``skipped``): the camera hands out the freshest frame, so a slow loop skips some.
    """
    return _measure(
        _one_at_a_time(source, detector),
        detector,
        seconds=seconds,
        frame_rate=frame_rate,
        on_frame=on_frame,
        on_report=on_report,
        report_every=report_every,
        stop=stop,
    )


def run_ahead(
    source: FrameSource,
    detector: StreamDetector,
    *,
    seconds: float,
    on_frame: OnFrame | None = None,
    on_report: OnReport | None = None,
    report_every: float = 5.0,
    stop: StopRequest | None = None,
) -> RunReport:
    """The same over a recording: the next frames are read and sent while the last are worked on.

    There is no camera to keep up with, so latency, frame intervals and skips are not measured,
    and the times of the stages overlap: they no longer add up to the frame's share of the run.
    """
    return _measure(
        _working_ahead(source, detector),
        detector,
        seconds=seconds,
        frame_rate=None,
        on_frame=on_frame,
        on_report=on_report,
        report_every=report_every,
        stop=stop,
    )


def _measure(
    produced: Produced,
    detector: Detector,
    *,
    seconds: float,
    frame_rate: float | None,  # of the camera; None for a recording, which has no live figures
    on_frame: OnFrame | None,
    on_report: OnReport | None,
    report_every: float,
    stop: StopRequest | None,
) -> RunReport:
    report = RunReport()
    window = StageStats()
    window_frames = window_skipped = 0
    window_detections: Counter[str] = Counter()
    sampler = SystemSampler()
    live = frame_rate is not None
    period_ms = 1000 / frame_rate if frame_rate else 0.0
    last_timestamp_ns: int | None = None

    start = window_start = time.perf_counter()
    try:
        while time.perf_counter() - start < seconds and not (stop and stop.requested):
            try:
                frame, detections, capture_ms, loop_ms = next(produced)
            except StopIteration:
                break
            latency_ms = (time.monotonic_ns() - frame.timestamp_ns) / 1e6

            timings = detector.timings
            measured = {
                CAPTURE: capture_ms,
                PREPROCESS: timings.preprocess_ms,
                INFER: timings.infer_ms,
                POSTPROCESS: timings.postprocess_ms,
                LOOP: loop_ms,
            }
            if live:
                measured[LATENCY] = latency_ms
            skipped = 0
            if live and last_timestamp_ns is not None:
                interval_ms = (frame.timestamp_ns - last_timestamp_ns) / 1e6
                measured[INTERVAL] = interval_ms
                skipped = max(0, round(interval_ms / period_ms) - 1)
            last_timestamp_ns = frame.timestamp_ns

            for stage, ms in measured.items():
                report.stats.add(stage, ms)
                window.add(stage, ms)
            labels = Counter(d.label for d in detections)
            report.frames += 1
            report.skipped += skipped
            report.detections.update(labels)
            window_frames += 1
            window_skipped += skipped
            window_detections.update(labels)

            if on_frame is not None:
                on_frame(frame, detections)

            now = time.perf_counter()
            if now - window_start >= report_every:
                sample = sampler.sample()
                report.samples.append(sample)
                if on_report is not None:
                    on_report(
                        WindowReport(
                            elapsed_s=now - window_start,
                            run_elapsed_s=now - start,
                            frames=window_frames,
                            skipped=window_skipped,
                            stage_means_ms={s: window.summary(s).mean for s in window.stages()},
                            detections=window_detections,
                            system=sample,
                        )
                    )
                window.clear()
                window_frames = window_skipped = 0
                window_detections = Counter()
                window_start = now
    except KeyboardInterrupt:
        pass
    report.elapsed_s = time.perf_counter() - start
    return report
