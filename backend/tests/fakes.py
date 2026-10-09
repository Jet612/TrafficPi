"""Stand-ins for the camera and the accelerator, so pipeline logic can be tested anywhere."""

import time
from collections.abc import Iterator, Sequence

import numpy as np

from trafficpi.core.types import Detection
from trafficpi.vision.detector import DetectTimings
from trafficpi.vision.sources import EndOfStream, Frame
from trafficpi.vision.trails import FinishedTrail, encode_path

PERIOD_NS = 33_333_333


class FakeSource:
    """30 fps camera that loses every 10th frame, like a loop that cannot keep up."""

    frame_size: tuple[int, int] = (4, 4)

    def __init__(self, limit: int | None = None) -> None:
        self.index = 0
        self.limit = limit  # end the stream after this many frames, like a recorded clip
        self.start_ns = time.monotonic_ns()

    def start(self) -> None: ...

    def stop(self) -> None: ...

    def read(self) -> Frame:
        if self.limit is not None and self.index >= self.limit:
            raise EndOfStream
        time.sleep(0.004)
        self.index += 1
        lost = self.index // 10  # one extra frame period gone per ten frames
        ts = (
            self.start_ns + (self.index + lost) * PERIOD_NS - 10 * PERIOD_NS
        )  # shifted into the past
        return Frame(self.index, ts, np.zeros((4, 4, 3), np.uint8))


class FakeDetector:
    def __init__(self) -> None:
        self.timings = DetectTimings()

    def detect(self, image: np.ndarray) -> list[Detection]:
        self.timings = DetectTimings(0.5, 12.0, 0.25)
        return [
            Detection(2, "car", 0.9, 0.1, 0.1, 0.2, 0.2),
            Detection(0, "person", 0.7, 0.5, 0.5, 0.6, 0.6),
        ]

    def close(self) -> None: ...


def write_crossing_log(path, *, frames: int = 100, fps: float = 30.0):  # type: ignore[no-untyped-def]
    """A detection log of one car driving down the frame, crossing y = 0.5 at about frame 50."""
    from trafficpi.core.coco import class_name
    from trafficpi.vision.recorder import DetectionLogWriter

    with DetectionLogWriter(path, {"model": "synthetic", "frame_size": [640, 360]}) as log:
        for i in range(frames):
            ground_y = 0.2 + 0.006 * i
            car = Detection(2, class_name(2), 0.9, 0.46, ground_y - 0.07, 0.54, ground_y)
            frame = Frame(i, round(i * 1e9 / fps), np.zeros((2, 2, 3), np.uint8))
            log.write_frame(frame, [car])
    return path


class ScriptedSource:
    """A source that plays frames at 30 fps from a list of per-frame detection lists."""

    def __init__(self, script: list[list[Detection]], on_read=None) -> None:  # type: ignore[no-untyped-def]
        self.script = script
        self.frame_size: tuple[int, int] = (4, 4)
        self.index = 0
        self.on_read = on_read  # called with the index of each frame just before it is returned

    def start(self) -> None: ...

    def stop(self) -> None: ...

    def read(self) -> Frame:
        if self.index >= len(self.script):
            raise EndOfStream
        if self.on_read is not None:
            self.on_read(self.index)
        frame = Frame(self.index, round(self.index * 1e9 / 30), np.zeros((4, 4, 3), np.uint8))
        self.index += 1
        return frame


class ScriptedDetector:
    """Returns the scripted detections for the frame it is asked about, in order."""

    def __init__(self, source: ScriptedSource) -> None:
        self.source = source
        self.timings = DetectTimings()

    def detect(self, image: np.ndarray) -> list[Detection]:
        return self.source.script[self.source.index - 1]

    def close(self) -> None: ...


def car_crossing(
    frames: int = 100, *, start_y: float = 0.2, x: float = 0.5, speed: float = 0.006
) -> list[list[Detection]]:
    """A car driving down the frame: its ground point reaches y = 0.5 at about frame 50."""
    from trafficpi.core.coco import class_name

    return [
        [
            Detection(
                2,
                class_name(2),
                0.9,
                x - 0.04,
                start_y + speed * i - 0.07,
                x + 0.04,
                start_y + speed * i,
            )
        ]
        for i in range(frames)
    ]


def finished_trail(
    at_ms: int,
    *,
    label: str = "car",
    seconds: float = 5.0,
    x0: float = 0.2,
    y: float = 0.6,
    track_id: int = 1,
) -> FinishedTrail:
    """A vehicle that drove left to right for ``seconds``, starting at ``at_ms``."""
    n = int(seconds * 5) + 1
    offsets = [round(i * 1000 / 5) for i in range(n)]
    xs = [x0 + 0.6 * i / (n - 1) for i in range(n)]
    return FinishedTrail(
        track_id=track_id,
        started_at=at_ms,
        ended_at=at_ms + offsets[-1],
        label=label,
        points=n,
        extent=0.6,
        path=encode_path(offsets, xs, [y] * n),
    )


# --- a vehicle at a stop sign ---------------------------------------------------------------------

# The stop sign of the tests: the road to the left of x = 0.5 is where a stop counts, and a vehicle
# driving from the left of the picture to its right goes through the sign.
STOP_SIGN = {
    "id": "elm",
    "name": "Elm Street",
    "approach": [[0.1, 0.5], [0.5, 0.5], [0.5, 0.95], [0.1, 0.95]],
    "a": [0.5, 0.9],
    "b": [0.5, 0.3],
}
# A drive is a list of (seconds, speed at the start, speed at the end) in picture widths a second.
FULL_STOP = [
    (1.5, 0.15, 0.15),
    (0.8, 0.15, 0.0),
    (2.0, 0.0, 0.0),
    (1.0, 0.0, 0.15),
    (2.0, 0.15, 0.15),
]
ROLLING_STOP = [
    (1.5, 0.15, 0.15),
    (1.0, 0.15, 0.04),
    (1.0, 0.04, 0.04),
    (1.0, 0.04, 0.15),
    (2.0, 0.15, 0.15),
]
STRAIGHT_THROUGH = [(5.0, 0.15, 0.15)]


def drive(segments: Sequence[tuple[float, float, float]], x0: float = 0.12) -> Iterator[float]:
    """Where a vehicle driving left to right is at each frame of a 30 fps camera."""
    x = x0
    yield x
    for seconds, v0, v1 in segments:
        frames = round(seconds * 30)
        for i in range(frames):
            x += (v0 + (v1 - v0) * (i + 0.5) / frames) / 30
            yield x


def car_driving(
    segments: Sequence[tuple[float, float, float]], *, x0: float = 0.12, y: float = 0.8
) -> list[list[Detection]]:
    """Per-frame detections of one car on such a drive, its ground point at (x, y)."""
    from trafficpi.core.coco import class_name

    return [
        [Detection(2, class_name(2), 0.9, x - 0.06, y - 0.14, x + 0.06, y)]
        for x in drive(segments, x0)
    ]
