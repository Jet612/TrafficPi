"""Plane geometry for lines and zones.

Points are ``(x, y)`` fractions of the frame (0-1) in screen orientation: x grows to the right and
y grows *downwards*. "Left" and "right" below are as seen by someone walking along the line from
its start to its end, looking at the screen; with y pointing down that makes the right-hand side
the one where the cross product is positive.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

Point = tuple[float, float]


def cross(o: Point, a: Point, b: Point) -> float:
    """Cross product of ``a - o`` and ``b - o``; positive when ``b`` is right of ray ``o -> a``."""
    return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])


@dataclass(frozen=True, slots=True)
class Line:
    """A directed line segment from ``a`` to ``b``."""

    a: Point
    b: Point

    @property
    def length(self) -> float:
        return math.hypot(self.b[0] - self.a[0], self.b[1] - self.a[1])

    def signed_distance(self, p: Point) -> float:
        """Perpendicular distance from the infinite line through the segment.

        Positive on the right-hand side, negative on the left. A degenerate (zero-length) line
        has no sides and returns 0.
        """
        length = self.length
        if length == 0:
            return 0.0
        return cross(self.a, self.b, p) / length

    def side(self, p: Point, margin: float = 0.0) -> int:
        """+1 right, -1 left, 0 within ``margin`` of the line."""
        d = self.signed_distance(p)
        if d > margin:
            return 1
        if d < -margin:
            return -1
        return 0

    def intersect(self, p0: Point, p1: Point, tolerance: float = 0.0) -> tuple[float, float] | None:
        """Where the step ``p0 -> p1`` meets this segment, if it does.

        Returns ``(t, u)``: ``t`` is the position along this segment and ``u`` along the step, both
        from 0 to 1. ``tolerance`` lets ``t`` run slightly past either end of the segment, as a
        fraction of its length. Parallel and non-touching steps return None.
        """
        rx, ry = self.b[0] - self.a[0], self.b[1] - self.a[1]
        sx, sy = p1[0] - p0[0], p1[1] - p0[1]
        denominator = rx * sy - ry * sx
        if denominator == 0:
            return None
        qx, qy = p0[0] - self.a[0], p0[1] - self.a[1]
        t = (qx * sy - qy * sx) / denominator
        u = (qx * ry - qy * rx) / denominator
        if -tolerance <= t <= 1 + tolerance and 0 <= u <= 1:
            return t, u
        return None

    def crossing(self, p0: Point, p1: Point) -> int:
        """Direction in which the step ``p0 -> p1`` crosses the segment, or 0 if it does not.

        +1 for left to right, -1 for right to left. A step that starts or ends exactly on the
        line is not a crossing; callers that track movement over many frames should remember the
        last side they saw instead (see ``CountingLine`` in ``trafficpi.vision.rules``).
        """
        s0, s1 = self.side(p0), self.side(p1)
        if s0 * s1 >= 0 or self.intersect(p0, p1) is None:
            return 0
        return s1


@dataclass(frozen=True, slots=True)
class Polygon:
    """A simple polygon given by its corners in order."""

    points: tuple[Point, ...]

    def __post_init__(self) -> None:
        if len(self.points) < 3:
            raise ValueError("a polygon needs at least three points")

    def contains(self, p: Point) -> bool:
        """Whether ``p`` is inside or on the boundary."""
        x, y = p
        inside = False
        n = len(self.points)
        for i in range(n):
            a, b = self.points[i], self.points[(i + 1) % n]
            if _on_segment(a, b, p):
                return True
            if (a[1] > y) != (b[1] > y):
                x_at_y = a[0] + (y - a[1]) * (b[0] - a[0]) / (b[1] - a[1])
                if x < x_at_y:
                    inside = not inside
        return inside

    def bounds(self) -> tuple[float, float, float, float]:
        xs = [p[0] for p in self.points]
        ys = [p[1] for p in self.points]
        return min(xs), min(ys), max(xs), max(ys)

    def area(self) -> float:
        """Enclosed area as a fraction of the frame (the shoelace formula); 0 for a flat polygon."""
        n = len(self.points)
        twice = 0.0
        for i in range(n):
            (x0, y0), (x1, y1) = self.points[i], self.points[(i + 1) % n]
            twice += x0 * y1 - x1 * y0
        return abs(twice) / 2

    def is_simple(self) -> bool:
        """Whether the outline avoids itself: no two edges cross or touch, and no edge doubles back.

        Neighbouring edges meet at their shared corner and nowhere else. A bow-tie, a corner
        dragged across the opposite side, or a point that retraces the edge before it are all
        refused; a point-in-polygon test is not meaningful for them.
        """
        pts, n = self.points, len(self.points)
        for i in range(n):
            a, b = pts[i], pts[(i + 1) % n]
            if a == b:
                return False
            for j in range(i + 1, n):
                c, d = pts[j], pts[(j + 1) % n]
                if j == i + 1:  # the edge after this one: shares b == c
                    if _doubles_back(a, b, d):
                        return False
                elif i == 0 and j == n - 1:  # the edge before the first one: shares a == d
                    if _doubles_back(b, a, c):
                        return False
                elif _segments_touch(a, b, c, d):
                    return False
        return True


def _doubles_back(a: Point, corner: Point, c: Point, eps: float = 1e-12) -> bool:
    """Whether the edges ``a -> corner`` and ``corner -> c`` run along each other, overlapping."""
    if abs(cross(a, corner, c)) > eps:
        return False
    # Collinear: they overlap exactly when both lead away from the corner in the same direction.
    return (a[0] - corner[0]) * (c[0] - corner[0]) + (a[1] - corner[1]) * (c[1] - corner[1]) > 0


def _segments_touch(a: Point, b: Point, c: Point, d: Point) -> bool:
    """Whether the segments ``a-b`` and ``c-d`` have any point in common."""
    d1, d2 = cross(a, b, c), cross(a, b, d)
    d3, d4 = cross(c, d, a), cross(c, d, b)
    if d1 * d2 < 0 and d3 * d4 < 0:  # each segment straddles the other's line
        return True
    return (
        _on_segment(a, b, c) or _on_segment(a, b, d) or _on_segment(c, d, a) or _on_segment(c, d, b)
    )


def _on_segment(a: Point, b: Point, p: Point, eps: float = 1e-12) -> bool:
    if abs(cross(a, b, p)) > eps:
        return False
    return (
        min(a[0], b[0]) - eps <= p[0] <= max(a[0], b[0]) + eps
        and min(a[1], b[1]) - eps <= p[1] <= max(a[1], b[1]) + eps
    )
