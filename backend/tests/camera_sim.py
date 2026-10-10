"""A pretend camera over a flat road, for tests that need the truth to compare with.

The world is in metres: ``X`` across the road (to the right), ``Y`` along it, ``Z`` up, the road at
``Z = 0``. The camera looks at it through an ideal pinhole, so a point of the road lands on the
picture exactly where a real camera would put it (no lens distortion, no rolling shutter). A vehicle
is a box on the road; the box a detector would draw round it is the box that its eight corners
fill, and the "ground point" the system works with is the middle of that box's bottom edge, just as
in the real thing, so the error that comes from that choice is in the numbers too.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

Point = tuple[float, float]


@dataclass(frozen=True, slots=True)
class PinholeCamera:
    x: float = -7.0  # where it stands, on the road's plane
    y: float = -22.0
    height: float = 7.0  # above the road
    pitch_deg: float = 21.0  # how far it looks down
    yaw_deg: float = 12.0  # how far it looks to the right of the road's direction
    hfov_deg: float = 66.0  # a Camera Module 3 (the standard lens)
    width_px: int = 1280
    height_px: int = 720

    def _axes(self) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        yaw, pitch = math.radians(self.yaw_deg), math.radians(self.pitch_deg)
        flat = np.array([math.sin(yaw), math.cos(yaw), 0.0])
        right = np.array([math.cos(yaw), -math.sin(yaw), 0.0])
        forward = flat * math.cos(pitch) + np.array([0.0, 0.0, -math.sin(pitch)])
        down = np.cross(forward, right)
        return right, down, forward

    @property
    def focal_px(self) -> float:
        return self.width_px / 2 / math.tan(math.radians(self.hfov_deg) / 2)

    def project(self, x: float, y: float, z: float = 0.0) -> Point | None:
        """Where a point of the world is in the picture (fractions of the frame); None if the
        camera is not looking towards it."""
        right, down, forward = self._axes()
        offset = np.array([x - self.x, y - self.y, z - self.height])
        depth = float(offset @ forward)
        if depth <= 1e-6:
            return None
        u = self.width_px / 2 + self.focal_px * float(offset @ right) / depth
        v = self.height_px / 2 + self.focal_px * float(offset @ down) / depth
        return u / self.width_px, v / self.height_px

    def ground(self, u: float, v: float) -> Point | None:
        """Where on the road a point of the picture is; None above the horizon."""
        right, down, forward = self._axes()
        ray = (
            right * ((u * self.width_px - self.width_px / 2) / self.focal_px)
            + down * ((v * self.height_px - self.height_px / 2) / self.focal_px)
            + forward
        )
        if ray[2] >= -1e-9:
            return None
        t = -self.height / ray[2]
        return self.x + t * ray[0], self.y + t * ray[1]

    def box_of(
        self,
        x: float,
        y: float,
        heading: float,
        *,
        length: float = 4.5,
        width: float = 1.8,
        height: float = 1.5,
    ) -> tuple[float, float, float, float] | None:
        """The box a detector would draw round a vehicle of this size whose middle is at ``(x, y)``
        on the road, driving towards ``heading`` (radians from the direction of the road, positive
        to the right); None if any of it is out of sight."""
        along = np.array([math.sin(heading), math.cos(heading)])
        across = np.array([math.cos(heading), -math.sin(heading)])
        points = []
        for a in (-length / 2, length / 2):
            for c in (-width / 2, width / 2):
                for z in (0.0, height):
                    ground = np.array([x, y]) + a * along + c * across
                    seen = self.project(float(ground[0]), float(ground[1]), z)
                    if seen is None:
                        return None
                    points.append(seen)
        xs, ys = zip(*points, strict=True)
        return min(xs), min(ys), max(xs), max(ys)
