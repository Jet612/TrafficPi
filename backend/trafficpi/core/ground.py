"""The road as a flat plane: the picture put in metres (plan.md M6, D44).

A camera sees a flat road through a perspective, so a distance in the picture means more metres the
further away it is. A *calibration* says where four points of the picture are on the road, as the
corners of a rectangle of known width and length; the map that follows (a homography, the standard
tool for a plane seen by a camera) puts any other point of the road in metres, and from metres and
the camera's clock a speed follows.

Points are ``(x, y)`` fractions of the frame (0-1) with y pointing *down*, like everything else;
the road's own axes are in metres, ``X`` across the rectangle (its width) and ``Y`` along it (its
length). Nothing here knows about cameras, vehicles or time.

The map is exact for four exact points on a perfectly flat road, and it is only as good as they
are: a corner a few pixels off, or a length that was measured badly, bends it, and it bends more
the further from the rectangle a point lies. :func:`estimate_accuracy` says by how much, for every
part of the picture, by shaking the corners and the lengths by what a careful person can get wrong
and looking at what happens to the metres. That is also how the vision service knows where a
speed can be believed (``GroundCalibration.believed``) and where it should say nothing.
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from functools import lru_cache

import numpy as np

Point = tuple[float, float]

# What the accuracy estimate assumes a careful person gets wrong (plan.md D44). The picture is taken
# to be 16:9 with 1280 pixels across, and a corner is placed to within 3 pixels; a length measured
# with a tape or read off a map is out by 2% (1 sigma, each of the two).
PICTURE_ASPECT = 16 / 9
PICTURE_WIDTH_PX = 1280
CORNER_SIGMA_PX = 3.0
LENGTH_SIGMA = 0.02
ACCURACY_TRIALS = 200
ACCURACY_COLUMNS = 32  # the picture is sampled on this grid, and read between its points
ACCURACY_ROWS = 18
ACCURACY_SEED = 20261009  # the same shaking every time, so the dashboard and the service agree
# The accuracy is judged on a stretch of road like the one a speed is measured over (a vehicle at
# 15 mph covers 6 m in a second): this long along the road, and a lane's width across it.
CHORD_ALONG_M = 6.0
CHORD_ACROSS_M = 3.0
# A speed is measured only where the calibration is believed to be right to this much (1 sigma, as
# a share of the distance): beyond it the answer is more the corners' than the road's.
MAX_TRUSTED_ERROR = 0.10
MIN_WIDTH_M, MAX_WIDTH_M = 0.5, 100.0
MIN_LENGTH_M, MAX_LENGTH_M = 1.0, 500.0
MIN_SIDE_PX = 25.0  # a side shorter than this (of a 1280 pixel wide picture) is a slip of the mouse
_HORIZON = 1e-6  # the third coordinate of a point on the road is positive, and 1 at the rectangle


class CalibrationError(ValueError):
    """The points cannot be a rectangle on a flat road seen by a camera. The message says why."""


# --- the homography ---------------------------------------------------------------------------


def _normalizing(points: np.ndarray) -> np.ndarray:
    """The similarity that puts the points' centre at the origin and their mean distance from it at
    the square root of two (Hartley): it keeps the fit well conditioned whatever the units."""
    centre = points.mean(axis=0)
    spread = float(np.linalg.norm(points - centre, axis=1).mean())
    if spread < 1e-12:
        raise CalibrationError("all the points are in the same place")
    scale = math.sqrt(2) / spread
    return np.array([[scale, 0.0, -scale * centre[0]], [0.0, scale, -scale * centre[1]], [0, 0, 1]])


def fit_homography(picture: Sequence[Point], ground: Sequence[Point]) -> np.ndarray:
    """The 3 x 3 matrix that takes picture points to ground points, from four or more pairs.

    With exactly four it is exact; with more it is the least-squares fit (the direct linear
    transform on normalized points). The result is scaled so that the third coordinate is 1 at the
    picture points' centre, and every one of the points is in front of the horizon.
    ``CalibrationError`` if the points cannot give such a map: three on a line, two in one place,
    or corners that are not in order round the rectangle.
    """
    src = np.asarray(picture, dtype=float)
    dst = np.asarray(ground, dtype=float)
    if src.ndim != 2 or src.shape[1] != 2 or src.shape != dst.shape or len(src) < 4:
        raise CalibrationError("a map needs at least four points in the picture and on the road")
    if not (np.isfinite(src).all() and np.isfinite(dst).all()):
        raise CalibrationError("a point is not a number")
    to_src, to_dst = _normalizing(src), _normalizing(dst)
    p = src @ to_src[:2, :2].T + to_src[:2, 2]
    q = dst @ to_dst[:2, :2].T + to_dst[:2, 2]
    rows = np.zeros((2 * len(p), 9))
    rows[0::2, 0:2] = -p
    rows[0::2, 2] = -1.0
    rows[0::2, 6:8] = q[:, :1] * p
    rows[0::2, 8] = q[:, 0]
    rows[1::2, 3:5] = -p
    rows[1::2, 5] = -1.0
    rows[1::2, 6:8] = q[:, 1:] * p
    rows[1::2, 8] = q[:, 1]
    _, singular, vt = np.linalg.svd(rows)
    # Four points in general position fix eight of the nine numbers; if the eighth singular value
    # is nothing, three of the points are on a line and the matrix is not determined.
    if singular[7] < 1e-6 * singular[0]:
        raise CalibrationError("three of the points are on one line, or two are in the same place")
    solution = vt[-1].reshape(3, 3)
    # Three points on a line in the picture, but not on the road (or two in one place): the nine
    # numbers fit, and the map is flat. Collinear points stay collinear under any real map.
    if np.linalg.cond(solution) > 1e9:
        raise CalibrationError("three of the points are on one line, or two are in the same place")
    matrix = np.linalg.inv(to_dst) @ solution @ to_src
    centre = src.mean(axis=0)
    w_centre = float(matrix[2] @ [centre[0], centre[1], 1.0])
    if abs(w_centre) < 1e-12:
        raise CalibrationError("the points cannot be on a road seen by a camera")
    matrix = matrix / w_centre
    if (src @ matrix[2, :2] + matrix[2, 2] <= _HORIZON).any():
        raise CalibrationError(
            "a corner is on or beyond the horizon, or the corners are in the wrong order: they "
            "must go round the rectangle one after the other"
        )
    return matrix


def _jacobians(matrix: np.ndarray, points: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """The 2 x 2 derivative of the map at each of the ``(n, 2)`` points, and which points are on
    the road at all. Rows are X and Y, columns the picture's x and y; where a point is on or beyond
    the horizon the derivative is the identity, so that it can be inverted and ignored."""
    x, y = points[:, 0], points[:, 1]
    a = matrix[0, 0] * x + matrix[0, 1] * y + matrix[0, 2]
    b = matrix[1, 0] * x + matrix[1, 1] * y + matrix[1, 2]
    w = matrix[2, 0] * x + matrix[2, 1] * y + matrix[2, 2]
    valid = w > _HORIZON
    safe = np.where(valid, w, 1.0)
    gx, gy = a / safe, b / safe
    jacobian = np.empty((len(points), 2, 2))
    jacobian[:, 0, 0] = (matrix[0, 0] - gx * matrix[2, 0]) / safe
    jacobian[:, 0, 1] = (matrix[0, 1] - gx * matrix[2, 1]) / safe
    jacobian[:, 1, 0] = (matrix[1, 0] - gy * matrix[2, 0]) / safe
    jacobian[:, 1, 1] = (matrix[1, 1] - gy * matrix[2, 1]) / safe
    jacobian[~valid] = np.eye(2)
    return jacobian, valid


@dataclass(frozen=True, slots=True, eq=False)
class GroundMap:
    """A map from the picture to the road plane, in metres."""

    matrix: np.ndarray  # 3 x 3, picture (x, y, 1) to (X, Y, W) with W positive on the road
    inverse: np.ndarray

    @classmethod
    def from_matrix(cls, matrix: np.ndarray) -> GroundMap:
        return cls(matrix, np.linalg.inv(matrix))

    def to_ground(self, point: Point) -> Point | None:
        """Where on the road a point of the picture is, in metres; None on or beyond the horizon,
        where the road is not."""
        m = self.matrix
        x, y = point
        w = m[2, 0] * x + m[2, 1] * y + m[2, 2]
        if w <= _HORIZON:
            return None
        return (
            float((m[0, 0] * x + m[0, 1] * y + m[0, 2]) / w),
            float((m[1, 0] * x + m[1, 1] * y + m[1, 2]) / w),
        )

    def to_picture(self, ground: Point) -> Point | None:
        """Where a point of the road is in the picture; None if it is behind the camera."""
        m = self.inverse
        x, y = ground
        w = m[2, 0] * x + m[2, 1] * y + m[2, 2]
        if abs(w) < 1e-12:
            return None
        u = float((m[0, 0] * x + m[0, 1] * y + m[0, 2]) / w)
        v = float((m[1, 0] * x + m[1, 1] * y + m[1, 2]) / w)
        in_front = self.matrix[2, 0] * u + self.matrix[2, 1] * v + self.matrix[2, 2] > _HORIZON
        return (u, v) if in_front else None

    def distance(self, a: Point, b: Point) -> float | None:
        """The distance in metres between two points of the road given as picture points."""
        ga, gb = self.to_ground(a), self.to_ground(b)
        if ga is None or gb is None:
            return None
        return math.hypot(gb[0] - ga[0], gb[1] - ga[1])

    def metres_per_pixel(self, point: Point, *, width_px: int = PICTURE_WIDTH_PX) -> Point | None:
        """How many metres a pixel stands for at a point, across the road and along it (the
        rectangle's own axes), in a 16:9 picture ``width_px`` wide; None beyond the horizon."""
        jacobian, valid = _jacobians(self.matrix, np.asarray([point], dtype=float))
        if not valid[0]:
            return None
        size = np.array([width_px, width_px / PICTURE_ASPECT])
        pixels_per_metre = np.linalg.inv(jacobian[0]) * size[:, None]
        across, along = np.linalg.norm(pixels_per_metre, axis=0)
        return float(1.0 / across), float(1.0 / along)


# --- a rectangle on the road --------------------------------------------------------------------


def _is_convex(corners: Sequence[Point]) -> bool:
    """Whether the polygon turns the same way at every corner (and is not flat)."""
    signs = []
    for i in range(len(corners)):
        (x0, y0), (x1, y1), (x2, y2) = corners[i - 1], corners[i], corners[(i + 1) % len(corners)]
        turn = (x1 - x0) * (y2 - y1) - (y1 - y0) * (x2 - x1)
        if abs(turn) < 1e-12:
            return False
        signs.append(turn > 0)
    return all(signs) or not any(signs)


def _rectangle_matrix(corners: Sequence[Point], width_m: float, length_m: float) -> np.ndarray:
    ground = [(0.0, 0.0), (width_m, 0.0), (width_m, length_m), (0.0, length_m)]
    return fit_homography(corners, ground)


def rectangle_map(corners: Sequence[Point], width_m: float, length_m: float) -> GroundMap:
    """The map that follows from four picture points being the corners of a rectangle on the road.

    The corners go round it, either way: the side from the first to the second is ``width_m`` long
    (across the road) and the side from the second to the third ``length_m`` (along it).
    """
    if len(corners) != 4:
        raise CalibrationError("a rectangle has four corners")
    if not (MIN_WIDTH_M <= width_m <= MAX_WIDTH_M):
        raise CalibrationError(
            f"the width must be from {MIN_WIDTH_M:g} to {MAX_WIDTH_M:g} m, got {width_m:g}"
        )
    if not (MIN_LENGTH_M <= length_m <= MAX_LENGTH_M):
        raise CalibrationError(
            f"the length must be from {MIN_LENGTH_M:g} to {MAX_LENGTH_M:g} m, got {length_m:g}"
        )
    if not _is_convex(corners):
        raise CalibrationError(
            "the corners must go round the rectangle one after the other, without crossing"
        )
    for i, (x0, y0) in enumerate(corners):
        x1, y1 = corners[(i + 1) % 4]
        side_px = math.hypot(
            (x1 - x0) * PICTURE_WIDTH_PX, (y1 - y0) * PICTURE_WIDTH_PX / PICTURE_ASPECT
        )
        if side_px < MIN_SIDE_PX:
            raise CalibrationError(
                f"two of the corners are only {side_px:.0f} pixels apart; the rectangle is too "
                "small in the picture to calibrate anything"
            )
    return GroundMap.from_matrix(_rectangle_matrix(corners, width_m, length_m))


# --- how far it can be believed -----------------------------------------------------------------


@dataclass(frozen=True, slots=True, eq=False)
class AccuracyMap:
    """How far a distance on the road can be believed at each part of the picture.

    Two grids laid over the picture hold, for the centre of each cell, the relative error (1
    sigma) that the calibration's own uncertainty puts on a distance travelled there: along the
    road (the rectangle's length) and across it (its width). ``inf`` where the road does not reach.
    """

    along: np.ndarray  # (rows, columns)
    across: np.ndarray

    def error_at(self, point: Point, heading: float = 0.0) -> float:
        """The relative error at a point of the picture, read between the grid's points, for travel
        ``heading`` radians from the road's own direction (0: along it, pi/2: across it)."""
        along = _read(self.along, point)
        across = _read(self.across, point)
        if math.isinf(along) or math.isinf(across):
            return math.inf
        return math.hypot(along * math.cos(heading), across * math.sin(heading))

    def best_error_at(self, point: Point) -> float:
        """The error for the better of the two directions: where a speed along the road can be
        believed at all."""
        return min(_read(self.along, point), _read(self.across, point))


def _read(grid: np.ndarray, point: Point) -> float:
    rows, columns = grid.shape
    fx = min(max(point[0] * columns - 0.5, 0.0), columns - 1.0)
    fy = min(max(point[1] * rows - 0.5, 0.0), rows - 1.0)
    x0, y0 = int(fx), int(fy)
    x1, y1 = min(x0 + 1, columns - 1), min(y0 + 1, rows - 1)
    tx, ty = fx - x0, fy - y0
    cells = (
        (float(grid[y0, x0]), (1 - tx) * (1 - ty)),
        (float(grid[y0, x1]), tx * (1 - ty)),
        (float(grid[y1, x0]), (1 - tx) * ty),
        (float(grid[y1, x1]), tx * ty),
    )
    if any(math.isinf(value) and weight > 0 for value, weight in cells):
        return math.inf
    return sum(value * weight for value, weight in cells if weight > 0)


def _shaken_maps(
    corners: Sequence[Point],
    width_m: float,
    length_m: float,
    *,
    corner_sigma_px: float,
    length_sigma: float,
    trials: int,
) -> Iterator[np.ndarray]:
    """The maps that follow from the rectangle with its corners and lengths moved at random by
    what a careful person gets wrong, ``trials`` times (fewer if some cannot be made). The same
    rectangle gives the same maps every time: the random numbers are seeded."""
    rng = np.random.default_rng(ACCURACY_SEED)
    sigma = np.array([1.0, PICTURE_ASPECT]) * corner_sigma_px / PICTURE_WIDTH_PX
    for _ in range(trials):
        moved = np.asarray(corners, dtype=float) + rng.normal(size=(4, 2)) * sigma
        width, length = (m * (1 + rng.normal() * length_sigma) for m in (width_m, length_m))
        try:
            yield _rectangle_matrix([(float(x), float(y)) for x, y in moved], width, length)
        except CalibrationError:
            continue


def estimate_accuracy(
    corners: Sequence[Point],
    width_m: float,
    length_m: float,
    *,
    corner_sigma_px: float = CORNER_SIGMA_PX,
    length_sigma: float = LENGTH_SIGMA,
    trials: int = ACCURACY_TRIALS,
) -> AccuracyMap:
    """How much of a distance on the road to believe, everywhere in the picture.

    The corners are moved by ``corner_sigma_px`` pixels (of a 1280 by 720 picture) and the two
    lengths by ``length_sigma`` of themselves, at random, ``trials`` times. Each time the map
    changes a little, and so does the length of a stretch of road at any point of the picture: 6 m
    along the road and 3 m across it, laid with its middle at the point (as the road is on the
    unshaken map). The root mean square of each change is the answer, for travel along the road
    and for travel across it. It is the same for a given rectangle every time (the random numbers
    are seeded), so that the dashboard and the vision service agree.
    """
    nominal = rectangle_map(corners, width_m, length_m)
    cells = np.array(
        [
            ((i + 0.5) / ACCURACY_COLUMNS, (j + 0.5) / ACCURACY_ROWS)
            for j in range(ACCURACY_ROWS)
            for i in range(ACCURACY_COLUMNS)
        ]
    )
    # The two ends of each stretch, as picture points (the road in the unshaken map).
    ends = np.full((4, len(cells), 2), np.nan)  # along: start, end; across: start, end
    for k, (u, v) in enumerate(cells):
        centre = nominal.to_ground((float(u), float(v)))
        if centre is None:
            continue
        for slot, (dx, dy) in enumerate(
            (
                (0.0, -CHORD_ALONG_M / 2),
                (0.0, CHORD_ALONG_M / 2),
                (-CHORD_ACROSS_M / 2, 0.0),
                (CHORD_ACROSS_M / 2, 0.0),
            )
        ):
            seen = nominal.to_picture((centre[0] + dx, centre[1] + dy))
            if seen is not None:
                ends[slot, k] = seen
    reached = ~np.isnan(ends).any(axis=(0, 2))
    lengths = np.array([CHORD_ALONG_M, CHORD_ACROSS_M])
    squares = np.zeros((2, len(cells)))
    used = 0
    for matrix in _shaken_maps(
        corners,
        width_m,
        length_m,
        corner_sigma_px=corner_sigma_px,
        length_sigma=length_sigma,
        trials=trials,
    ):
        for pair in range(2):
            a = _apply(matrix, ends[2 * pair])
            b = _apply(matrix, ends[2 * pair + 1])
            change = np.linalg.norm(b - a, axis=1) / lengths[pair] - 1.0
            squares[pair] += np.where(np.isfinite(change), change**2, np.inf)
        used += 1
    if used < trials // 2:
        raise CalibrationError("the corners are too close to a line for the map to be trusted")
    errors = np.where(reached, np.sqrt(squares / used), np.inf)
    shape = (ACCURACY_ROWS, ACCURACY_COLUMNS)
    return AccuracyMap(errors[0].reshape(shape), errors[1].reshape(shape))


def estimate_length_spread(
    corners: Sequence[Point],
    width_m: float,
    length_m: float,
    segments: Sequence[tuple[Point, Point]],
    *,
    corner_sigma_px: float = CORNER_SIGMA_PX,
    length_sigma: float = LENGTH_SIGMA,
    trials: int = ACCURACY_TRIALS,
) -> list[float | None]:
    """How much the length the map finds for each segment (two picture points) is expected to
    vary, as a share of itself (1 sigma), when the corners and lengths are moved as in
    :func:`estimate_accuracy`; None for a segment with an end beyond the horizon, and for one so
    near it that some of the shaken maps put an end of it there (its length cannot be trusted)."""
    nominal = rectangle_map(corners, width_m, length_m)
    measured = [nominal.distance(a, b) for a, b in segments]
    squares = [0.0] * len(segments)
    lost = [False] * len(segments)  # some shaken map has an end of this segment beyond the horizon
    used = 0
    for matrix in _shaken_maps(
        corners,
        width_m,
        length_m,
        corner_sigma_px=corner_sigma_px,
        length_sigma=length_sigma,
        trials=trials,
    ):
        shaken = GroundMap.from_matrix(matrix)
        for k, (a, b) in enumerate(segments):
            now, then = shaken.distance(a, b), measured[k]
            if now is None:
                lost[k] = True
            elif then:
                squares[k] += (now / then - 1.0) ** 2
        used += 1
    if used < trials // 2:
        raise CalibrationError("the corners are too close to a line for the map to be trusted")
    return [
        None if then is None or gone else math.sqrt(sq / used)
        for then, sq, gone in zip(measured, squares, lost, strict=True)
    ]


def _apply(matrix: np.ndarray, points: np.ndarray) -> np.ndarray:
    """The map applied to many ``(n, 2)`` picture points at once; NaN where a point is on or
    beyond the horizon, or was NaN already."""
    with np.errstate(invalid="ignore", divide="ignore"):
        w = points[:, 0] * matrix[2, 0] + points[:, 1] * matrix[2, 1] + matrix[2, 2]
        x = (points[:, 0] * matrix[0, 0] + points[:, 1] * matrix[0, 1] + matrix[0, 2]) / w
        y = (points[:, 0] * matrix[1, 0] + points[:, 1] * matrix[1, 1] + matrix[1, 2]) / w
    out = np.stack([x, y], axis=1)
    out[~(w > _HORIZON)] = np.nan
    return out


# --- everything a calibration is, in one place --------------------------------------------------


def calibration_id(corners: Sequence[Point], width_m: float, length_m: float) -> str:
    """A short name for a rectangle: eight hex digits of a hash of what makes the map."""
    text = ",".join(f"{c:.4f}" for corner in corners for c in corner)
    digest = hashlib.sha256(f"{text}|{width_m:.3f}|{length_m:.3f}".encode()).hexdigest()
    return digest[:8]


@dataclass(frozen=True, slots=True, eq=False)
class GroundCalibration:
    """A calibrated road: the map, where it can be believed, and a short name for it.

    ``id`` is the same for the same rectangle and different for another, so that a speed can be
    stored with the calibration it was measured under (the known distances that only verify it do
    not change it).
    """

    ground: GroundMap
    accuracy: AccuracyMap
    id: str

    def to_ground(self, point: Point) -> Point | None:
        """Where on the road a point of the picture is; None on or beyond the horizon."""
        return self.ground.to_ground(point)

    def believed(self, point: Point, velocity: Point) -> bool:
        """Whether a speed measured at a point of the picture, moving ``velocity`` (m/s, across
        and along the rectangle) on the road, can be believed: the calibration is good to
        ``MAX_TRUSTED_ERROR`` there, in the direction of travel."""
        speed = math.hypot(velocity[0], velocity[1])
        heading = math.atan2(abs(velocity[0]), abs(velocity[1])) if speed > 1e-9 else 0.0
        return self.accuracy.error_at(point, heading) <= MAX_TRUSTED_ERROR


def calibrate_rectangle(
    corners: Sequence[Point], width_m: float, length_m: float
) -> GroundCalibration:
    """The calibration for four picture points that are the corners of a rectangle on the road."""
    ground = rectangle_map(corners, width_m, length_m)
    accuracy = estimate_accuracy(corners, width_m, length_m)
    return GroundCalibration(ground, accuracy, calibration_id(corners, width_m, length_m))


@lru_cache(maxsize=8)
def calibration_for(
    corners: tuple[Point, ...], width_m: float, length_m: float
) -> GroundCalibration:
    """:func:`calibrate_rectangle`, kept: working out how far the map can be believed takes a tenth
    of a second, and the same rectangle is asked about again and again (every change of a
    setting reloads the profile, every keystroke in the editor asks for a report). A rectangle
    that cannot be calibrated raises each time and is not kept."""
    return calibrate_rectangle(corners, width_m, length_m)
