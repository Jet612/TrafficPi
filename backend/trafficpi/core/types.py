"""Plain data types shared by the vision pipeline."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Detection:
    """One detected object.

    Box coordinates are fractions (0-1) of the source frame, so they do not depend on the
    capture or inference resolution.
    """

    class_id: int
    label: str
    score: float
    x0: float
    y0: float
    x1: float
    y1: float

    @property
    def width(self) -> float:
        return self.x1 - self.x0

    @property
    def height(self) -> float:
        return self.y1 - self.y0

    @property
    def center(self) -> tuple[float, float]:
        return (self.x0 + self.x1) / 2, (self.y0 + self.y1) / 2

    @property
    def bottom_center(self) -> tuple[float, float]:
        """Where the object most likely touches the ground; the point used for road geometry."""
        return (self.x0 + self.x1) / 2, self.y1
