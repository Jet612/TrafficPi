"""A made-up street, for trying everything without a camera or an accelerator.

``trafficpi-vision run --demo`` plays this instead of the real camera: cars, trucks, buses and
bicycles drive along a drawn road, and a stand-in detector reports exactly the boxes that were
drawn. Everything after that is the real thing (tracker, counting lines, database, live view), so
a counting line across the road counts these vehicles. It is for developing the dashboard on any
machine, for the browser tests in CI, and for screenshots of a street that is nobody's.
"""

from __future__ import annotations

import math
import random
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, tzinfo
from datetime import time as clock_time

import cv2
import numpy as np

from trafficpi.core.types import Detection
from trafficpi.vision.detector import DetectTimings
from trafficpi.vision.health import HealthRow
from trafficpi.vision.sources import Frame
from trafficpi.vision.trails import FinishedTrail, encode_path

FRAME_SIZE = (640, 360)
FPS = 30
PERIOD_NS = 1_000_000_000 // FPS

# The road runs left to right across the picture. Vehicles in the lane nearer the camera go to
# the right, the others to the left. "bottom" is where a vehicle meets the road (a fraction of
# the picture's height), which is the point the counting lines look at.
LANES = {"right": 0.80, "left": 0.65}
SPAWN_PER_FRAME = 0.02  # about one new vehicle every 1.7 s, over both lanes
MIN_GAP = 0.04  # space kept between vehicles in a lane, as a fraction of the width


@dataclass(frozen=True, slots=True)
class _Kind:
    name: str
    class_id: int
    weight: int  # how often it appears
    width: float  # fractions of the frame
    height: float
    speed: tuple[float, float]  # fractions of the frame's width per second
    colors: tuple[tuple[int, int, int], ...]  # RGB


KINDS = (
    _Kind(
        "car",
        2,
        78,
        0.11,
        0.10,
        (0.16, 0.24),
        ((59, 130, 196), (192, 57, 43), (208, 208, 208), (46, 139, 87), (60, 60, 70)),
    ),
    _Kind("truck", 7, 10, 0.17, 0.15, (0.11, 0.17), ((235, 235, 235), (230, 126, 34))),
    _Kind("bus", 5, 4, 0.24, 0.16, (0.10, 0.14), ((241, 196, 15),)),
    _Kind("bicycle", 1, 8, 0.04, 0.09, (0.05, 0.09), ((40, 40, 40),)),
)


@dataclass(slots=True)
class _Vehicle:
    kind: _Kind
    x: float  # centre, fraction of the frame width
    speed: float  # signed
    bottom: float
    color: tuple[int, int, int]
    top_speed: float

    @property
    def left(self) -> float:
        return self.x - self.kind.width / 2

    @property
    def right(self) -> float:
        return self.x + self.kind.width / 2


def _background() -> np.ndarray:
    width, height = FRAME_SIZE
    image = np.zeros((height, width, 3), np.uint8)
    horizon = int(height * 0.34)
    for row in range(horizon):  # sky, lighter near the horizon
        t = row / horizon
        image[row] = (int(150 + 60 * t), int(180 + 40 * t), int(205 + 25 * t))
    image[horizon:] = (96, 124, 92)  # grass
    top, bottom = int(height * 0.46), int(height * 0.86)
    image[top:bottom] = (68, 71, 77)  # asphalt
    image[int(height * 0.43) : top] = (170, 168, 160)  # kerbs
    image[bottom : int(height * 0.89)] = (170, 168, 160)
    middle = int(height * 0.665)  # between the two lanes
    for x in range(0, width, 56):  # dashed centre line
        cv2.rectangle(image, (x + 8, middle - 1), (x + 36, middle + 1), (232, 232, 224), -1)
    for x in range(40, width, 150):  # a few trees, to give the sky something to do
        cv2.circle(image, (x, horizon - 4), 22, (62, 100, 62), -1)
        cv2.rectangle(image, (x - 3, horizon - 4), (x + 3, horizon + 14), (70, 52, 38), -1)
    return image


class DemoStreet:
    """The frame source and the detector of the made-up street (give both to the service)."""

    def __init__(
        self,
        seed: int = 7,
        *,
        monotonic_ns: Callable[[], int] = time.monotonic_ns,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.frame_size = FRAME_SIZE
        self.timings = DetectTimings()
        self._random = random.Random(seed)
        self._monotonic_ns = monotonic_ns
        self._sleep = sleep
        self._background = _background()
        self._vehicles: list[_Vehicle] = []
        self._detections: list[Detection] = []
        self._index = 0
        self._start_ns = 0

    # --- the frame source ---------------------------------------------------------------------

    def start(self) -> None:
        self._start_ns = self._monotonic_ns()

    def stop(self) -> None: ...

    def read(self) -> Frame:
        due = self._start_ns + self._index * PERIOD_NS
        wait = due - self._monotonic_ns()
        if wait > 0:
            self._sleep(wait / 1e9)
        self._advance(1 / FPS)
        image = self._draw()
        self._detections = self._detect()
        frame = Frame(self._index, due, image)
        self._index += 1
        return frame

    # --- the detector ---------------------------------------------------------------------------

    def detect(self, image: np.ndarray) -> list[Detection]:
        """What the drawn frame contains. The image itself is not looked at."""
        return list(self._detections)

    def close(self) -> None: ...

    # --- the street -------------------------------------------------------------------------------

    def _advance(self, seconds: float) -> None:
        for lane, direction in (("right", 1), ("left", -1)):
            queue = sorted(
                (v for v in self._vehicles if v.bottom == LANES[lane]),
                key=lambda v: -direction * v.x,  # the one in front first
            )
            ahead: _Vehicle | None = None
            for vehicle in queue:
                speed = vehicle.top_speed
                if ahead is not None:
                    gap = (
                        (ahead.left - vehicle.right)
                        if direction > 0
                        else (vehicle.left - ahead.right)
                    )
                    if gap < MIN_GAP:
                        speed = min(speed, abs(ahead.speed))
                vehicle.speed = direction * speed
                vehicle.x += vehicle.speed * seconds
                ahead = vehicle
        self._vehicles = [v for v in self._vehicles if -0.2 < v.x < 1.2]
        if self._random.random() < SPAWN_PER_FRAME:
            self._spawn()

    def _spawn(self) -> None:
        lane = self._random.choice(("right", "left"))
        kind = self._random.choices(KINDS, weights=[k.weight for k in KINDS])[0]
        direction = 1 if lane == "right" else -1
        x = -kind.width / 2 if direction > 0 else 1 + kind.width / 2
        top_speed = self._random.uniform(*kind.speed)
        newcomer = _Vehicle(
            kind, x, direction * top_speed, LANES[lane], self._random.choice(kind.colors), top_speed
        )
        for other in self._vehicles:  # wait if the entrance is not clear
            if (
                other.bottom == newcomer.bottom
                and abs(other.x - newcomer.x) < (other.kind.width + kind.width) / 2 + MIN_GAP
            ):
                return
        self._vehicles.append(newcomer)

    def _draw(self) -> np.ndarray:
        width, height = FRAME_SIZE
        image = self._background.copy()
        for vehicle in sorted(self._vehicles, key=lambda v: v.bottom):
            x0, x1 = int(vehicle.left * width), int(vehicle.right * width)
            y1 = int(vehicle.bottom * height)
            y0 = y1 - int(vehicle.kind.height * height)
            if vehicle.kind.name == "bicycle":
                cv2.rectangle(image, (x0 + 1, y0), (x1 - 1, y1 - 7), vehicle.color, -1)  # rider
                for cx in (x0 + 2, x1 - 2):
                    cv2.circle(image, (cx, y1 - 4), 5, (20, 20, 20), 2)
                continue
            cv2.ellipse(
                image, ((x0 + x1) // 2, y1), ((x1 - x0) // 2 + 3, 4), 0, 0, 360, (45, 47, 52), -1
            )  # shadow
            cv2.rectangle(image, (x0, y0), (x1, y1 - 4), vehicle.color, -1, cv2.LINE_AA)
            glass_top, glass_bottom = y0 + 3, y0 + max(6, (y1 - y0) // 2)
            cv2.rectangle(image, (x0 + 4, glass_top), (x1 - 4, glass_bottom), (150, 180, 205), -1)
            for cx in (x0 + (x1 - x0) // 5, x1 - (x1 - x0) // 5):
                cv2.circle(image, (cx, y1 - 3), 5, (24, 24, 26), -1, cv2.LINE_AA)
        return image

    def _detect(self) -> list[Detection]:
        found = []
        for vehicle in self._vehicles:
            left, right = max(0.0, vehicle.left), min(1.0, vehicle.right)
            if right - left < 0.5 * vehicle.kind.width:  # mostly out of the picture: not seen yet
                continue
            wobble = self._random.uniform(-0.003, 0.003)

            def clip(value: float) -> float:
                return min(1.0, max(0.0, value))  # a detector never reports outside the picture

            found.append(
                Detection(
                    vehicle.kind.class_id,
                    vehicle.kind.name,
                    round(self._random.uniform(0.62, 0.96), 3),
                    clip(left + wobble),
                    clip(vehicle.bottom - vehicle.kind.height + wobble),
                    clip(right + wobble),
                    clip(vehicle.bottom + wobble),
                )
            )
        return found


# --- a made-up past -------------------------------------------------------------------------

# Vehicles an hour at each hour of a weekday, on the clock of the street's time zone: quiet at
# night, a rush hour in the morning and a bigger one in the evening. About 5,000 a day, which is
# enough to need an even sample when a whole day is drawn.
HOURLY_RATE = (
    20, 12, 8, 8, 15, 60, 180, 420, 480, 330, 260, 250,
    280, 270, 260, 300, 420, 500, 380, 220, 140, 90, 55, 30,
)  # fmt: skip
WEEKEND_FACTOR = 0.7
SAMPLE_MS = 200  # a point every 200 ms, as the recorder takes them


def demo_history(days: int, *, now_ms: int, tz: tzinfo, seed: int = 11) -> list[FinishedTrail]:
    """Made-up trails for each of the ``days`` days before today on ``tz``'s calendar, oldest first.

    They run along the same two lanes as the live street, at the same speeds, so that they lie on
    its road in the picture. For trying the Data page's Trails card, and the browser tests, without
    waiting for traffic; nothing in them is real.
    """
    rng = random.Random(seed)
    today = datetime.fromtimestamp(now_ms / 1000, tz).date()
    trails: list[FinishedTrail] = []
    for back in range(days, 0, -1):
        day = today - timedelta(days=back)
        busy = (WEEKEND_FACTOR if day.weekday() >= 5 else 1.0) * rng.uniform(0.9, 1.1)
        midnight = datetime.combine(day, clock_time.min, tzinfo=tz)
        for hour in range(24):
            hour_start = int((midnight + timedelta(hours=hour)).timestamp() * 1000)
            mean = HOURLY_RATE[hour] * busy
            count = max(0, round(rng.gauss(mean, math.sqrt(mean))))
            starts = sorted(hour_start + rng.randrange(3_600_000) for _ in range(count))
            trails.extend(
                _made_up_trail(rng, start, len(trails) + i) for i, start in enumerate(starts)
            )
    return trails


# Where the made-up health has something to tell, in minutes before the minute in progress.
HOT_AGO = range(300, 312)  # twelve minutes at the temperature where the board slows itself down
UNDER_VOLTAGE_AGO = 120  # a single minute of a weak supply
CAMERA_AWAY_AGO = range(593, 601)  # eight minutes without a camera
OFF_AGO = range(995, 1001)  # six minutes with no record at all: the Pi was off


def demo_health(hours: int, *, now_ms: int, seed: int = 5) -> list[HealthRow]:
    """Made-up minutes of machine health for the ``hours`` hours before the minute in progress,
    oldest first: a board at 60 to 70 degrees doing 30 frames a second, a quarter of an hour when
    it got hot enough to slow itself down, a minute of under-voltage, a camera that went away for
    a while and a hole where the Pi was off. For trying the System page and the browser tests;
    nothing in them was measured. What is told lies at fixed distances from ``now_ms`` (see the
    ``*_AGO`` ranges), so a short period has only some of it."""
    rng = random.Random(seed)
    end_s = now_ms // 60_000 * 60
    rows: list[HealthRow] = []
    for ago in range(hours * 60, 0, -1):
        if ago in OFF_AGO:
            continue
        start = end_s - ago * 60
        wave = math.sin(2 * math.pi * (start % 86_400) / 86_400)
        temp, cpu, fps = 65.0 + 4.0 * wave, 27.0 + 6.0 * wave, 30.0
        camera_ok, flags = 12, 0
        if ago in HOT_AGO:
            heat = math.sin(math.pi * (ago - HOT_AGO.start + 0.5) / len(HOT_AGO))
            temp, fps = 78.0 + 6.4 * heat, 30.0 - 6.0 * heat
            flags = 0b1000 if heat < 0.6 else 0b1100  # the soft limit, then throttled as well
        if ago == UNDER_VOLTAGE_AGO:
            flags = 0b0001
        if ago in CAMERA_AWAY_AGO:
            camera_ok, cpu = 0, 6.0
        rows.append(
            HealthRow(
                minute_start=start,
                temp_c_max=round(temp + rng.uniform(-0.6, 0.6), 1),
                cpu_percent=round(cpu + rng.uniform(-2.0, 2.0), 1),
                fps_mean=round(fps + rng.uniform(-0.3, 0.1), 1) if camera_ok else None,
                fps_min=round(fps - rng.uniform(0.5, 2.0), 1) if camera_ok else None,
                throttled=flags,
                samples=12,
                camera_ok_samples=camera_ok,
            )
        )
    return rows


def _made_up_trail(rng: random.Random, started_at: int, track_id: int) -> FinishedTrail:
    """One vehicle driving all the way across the picture in one of the two lanes."""
    lane = rng.choice(("right", "left"))
    direction = 1 if lane == "right" else -1
    kind = rng.choices(KINDS, weights=[k.weight for k in KINDS])[0]
    speed = rng.uniform(*kind.speed)
    # The tracker confirms a vehicle a few frames after it enters and loses it as it leaves.
    x_from, x_to = (0.03, 0.97) if direction > 0 else (0.97, 0.03)
    duration_ms = round(abs(x_to - x_from) / speed * 1000)
    points = max(3, duration_ms // SAMPLE_MS + 1)
    offsets = [round(i * duration_ms / (points - 1)) for i in range(points)]
    xs = [x_from + (x_to - x_from) * i / (points - 1) for i in range(points)]
    bottom = LANES[lane] + rng.uniform(-0.01, 0.01)  # drivers do not all take the same line
    wobble = 0.0
    ys: list[float] = []
    for _ in range(points):  # the detector's boxes jitter, so a ground point does too
        wobble = max(-0.006, min(0.006, wobble + rng.uniform(-0.002, 0.002)))
        ys.append(bottom + wobble)
    extent = math.hypot(max(xs) - min(xs), max(ys) - min(ys))
    return FinishedTrail(
        track_id=track_id,
        started_at=started_at,
        ended_at=started_at + duration_ms,
        label=kind.name,
        points=points,
        extent=round(extent, 4),
        path=encode_path(offsets, xs, ys),
    )
