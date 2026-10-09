"""A made-up street, for trying everything without a camera or an accelerator.

``trafficpi-vision run --demo`` plays this instead of the real camera: cars, trucks, buses and
bicycles drive along a drawn road, and a stand-in detector reports exactly the boxes that were
drawn. Everything after that is the real thing (tracker, counting lines, database, live view), so
a counting line across the road counts these vehicles. It is for developing the dashboard on any
machine, for the browser tests in CI, and for screenshots of a street that is nobody's.
"""

from __future__ import annotations

import random
import time
from collections.abc import Callable
from dataclasses import dataclass

import cv2
import numpy as np

from trafficpi.core.types import Detection
from trafficpi.vision.detector import DetectTimings
from trafficpi.vision.sources import Frame

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
