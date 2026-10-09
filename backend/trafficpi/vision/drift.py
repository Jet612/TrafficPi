"""Has the camera's view moved since the reference picture was kept? (plan.md M7, D40)

The lines, zones and stop signs are drawn on the reference picture, so they are right only while
the camera sees what it saw then. A knock, a gust, a loosened screw or a hand that turns it, and
every count and every stop-sign judgement is quietly made against the wrong picture. This module
compares a live frame with the reference now and then and says how far the scenery has moved.

How: ORB features are matched between the two pictures, and a similarity transform (a shift, a
turn and a change of size) is fitted to the matches with RANSAC, which keeps the things that stay
put (the houses, the kerb, the poles) and drops the things that move (vehicles, leaves, shadows).
The move is reported as how far the drawn shapes would be off, in per cent of the picture's width,
because that is what matters to the counting. When too few features match (night, fog, a lorry in
front of the lens) the answer is "cannot tell", never a warning.
"""

from __future__ import annotations

import logging
import math
import threading
import time
from collections import deque
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import cv2
import numpy as np

if TYPE_CHECKING:
    from trafficpi.core.profile import Profile

log = logging.getLogger("trafficpi.vision")

WORK_WIDTH = 640  # both pictures are compared at this width
MIN_MATCHES = 12  # fewer matches than this and no transform is fitted
MIN_INLIERS = 30  # fewer matches that agree and the answer is "cannot tell"
MIN_INLIER_RATIO = 0.35  # a fit that most matches disagree with is not trusted either
RANSAC_THRESHOLD_PX = 3.0  # at WORK_WIDTH: how far a point may be from the fit and still agree
MATCH_RATIO = 0.75  # Lowe's ratio test: the best match must clearly beat the second best
FEATURES = 1500
# A fit is believed only if the reference, moved as the fit says, really lines up with the live
# picture: correlation of their edge strengths, which was at least 0.48 for every right answer in a
# sweep of 400 darkened, blurred, noisy and shifted real pictures, and 0.00 for the two nonsense
# answers that very dark frames gave (agreeing features on a shift of 44% of the width).
MIN_ALIGNMENT = 0.30
ALIGN_MARGIN_PX = 12  # the edge of the picture is left out of that comparison
MIN_COVERED = 0.25  # and so is any comparison of less than this share of the picture
MAX_TURN_DEG = 30.0  # a view turned or resized further than this is not a nudge, and not believed
SCALE_RANGE = (0.7, 1.4)

# Where the shapes may be, when there are none: the picture's corners and its middle.
_FALLBACK_POINTS = ((0.0, 0.0), (1.0, 0.0), (0.0, 1.0), (1.0, 1.0), (0.5, 0.5))


@dataclass(frozen=True, slots=True)
class DriftEstimate:
    """How far the live picture's scenery is from where the reference picture has it."""

    trusted: bool  # False: could not tell (``reason`` says why) and the numbers are zero
    reason: str = ""
    shift_pct: float = 0.0  # the largest move of any checked point, in % of the picture's width
    dx_pct: float = 0.0  # the middle of the picture moved right (+) by this % of the width
    dy_pct: float = 0.0  # ... and down (+) by this % of the width
    rotation_deg: float = 0.0  # turned clockwise on the screen (+) by this much
    scale: float = 1.0  # the scenery is this much bigger (>1) or smaller (<1) than it was
    inliers: int = 0
    matches: int = 0
    alignment: float = 0.0  # how well the two pictures line up once the move is undone, 0 to 1


def _untrusted(reason: str, matches: int = 0, inliers: int = 0) -> DriftEstimate:
    return DriftEstimate(trusted=False, reason=reason, matches=matches, inliers=inliers)


def _structure(gray: np.ndarray) -> np.ndarray:
    """Edge strength: what stays the same when the light changes."""
    smooth = cv2.GaussianBlur(gray.astype(np.float32), (0, 0), 1.5)
    return cv2.magnitude(cv2.Sobel(smooth, cv2.CV_32F, 1, 0), cv2.Sobel(smooth, cv2.CV_32F, 0, 1))


def _alignment(reference: np.ndarray, frame: np.ndarray, matrix: np.ndarray) -> float:
    """How well ``reference``, moved by ``matrix``, lines up with ``frame`` (0 to 1).

    It is the correlation of their edge strengths over the part of the picture that both cover; 0
    when too little is shared to say."""
    height, width = frame.shape[:2]
    moved = cv2.warpAffine(_structure(reference), matrix, (width, height), flags=cv2.INTER_LINEAR)
    covered = (
        cv2.warpAffine(
            np.ones((height, width), np.float32), matrix, (width, height), flags=cv2.INTER_NEAREST
        )
        > 0.5
    )
    covered[:ALIGN_MARGIN_PX] = covered[-ALIGN_MARGIN_PX:] = False
    covered[:, :ALIGN_MARGIN_PX] = covered[:, -ALIGN_MARGIN_PX:] = False
    if covered.sum() < MIN_COVERED * covered.size:
        return 0.0
    a, b = moved[covered], _structure(frame)[covered]
    a, b = a - a.mean(), b - b.mean()
    denominator = math.sqrt(float((a * a).sum()) * float((b * b).sum()))
    return max(0.0, float((a * b).sum()) / denominator) if denominator > 0 else 0.0


def _fit(
    reference: np.ndarray, frame: np.ndarray, points: Sequence[tuple[float, float]]
) -> DriftEstimate:
    """Match features between two grayscale pictures of the same size and fit the move."""
    detector = cv2.ORB.create(nfeatures=FEATURES, fastThreshold=10)
    key_a, desc_a = detector.detectAndCompute(reference, None)
    key_b, desc_b = detector.detectAndCompute(frame, None)
    if desc_a is None or desc_b is None or len(key_a) < MIN_MATCHES or len(key_b) < MIN_MATCHES:
        return _untrusted("too few features to compare: the view is too dark, plain or blurred")
    pairs = cv2.BFMatcher(cv2.NORM_HAMMING).knnMatch(desc_a, desc_b, k=2)
    good = [
        m for m, n in (p for p in pairs if len(p) == 2) if m.distance < MATCH_RATIO * n.distance
    ]
    if len(good) < MIN_MATCHES:
        return _untrusted("too few features match the reference picture", matches=len(good))
    pts_a = np.array([key_a[m.queryIdx].pt for m in good], dtype=np.float32)
    pts_b = np.array([key_b[m.trainIdx].pt for m in good], dtype=np.float32)
    matrix, inliers = cv2.estimateAffinePartial2D(
        pts_a,
        pts_b,
        method=cv2.RANSAC,
        ransacReprojThreshold=RANSAC_THRESHOLD_PX,
        maxIters=2000,
        confidence=0.99,
    )
    if matrix is None or inliers is None:
        return _untrusted("the pictures could not be fitted to each other", matches=len(good))
    n_inliers = int(inliers.sum())
    if n_inliers < MIN_INLIERS or n_inliers < MIN_INLIER_RATIO * len(good):
        return _untrusted(
            "too few features agree with each other to be sure",
            matches=len(good),
            inliers=n_inliers,
        )
    turn = math.degrees(math.atan2(matrix[1, 0], matrix[0, 0]))
    size = math.hypot(matrix[0, 0], matrix[1, 0])
    if abs(turn) > MAX_TURN_DEG or not SCALE_RANGE[0] <= size <= SCALE_RANGE[1]:
        return _untrusted(
            "the fit is not a believable move of the camera", matches=len(good), inliers=n_inliers
        )
    alignment = _alignment(reference, frame, matrix)
    if alignment < MIN_ALIGNMENT:
        return _untrusted(
            "the pictures do not line up well enough to be sure",
            matches=len(good),
            inliers=n_inliers,
        )
    height, width = frame.shape[:2]
    where = np.array(
        [(x * width, y * height) for x, y in (points or _FALLBACK_POINTS)], dtype=np.float32
    )
    moved = cv2.transform(where.reshape(-1, 1, 2), matrix).reshape(-1, 2) - where
    middle = np.array([[[width / 2, height / 2]]], dtype=np.float32)
    centre = cv2.transform(middle, matrix).reshape(2) - middle.reshape(2)
    return DriftEstimate(
        trusted=True,
        shift_pct=float(np.hypot(moved[:, 0], moved[:, 1]).max()) / width * 100,
        dx_pct=float(centre[0]) / width * 100,
        dy_pct=float(centre[1]) / width * 100,
        rotation_deg=turn,
        scale=size,
        inliers=n_inliers,
        matches=len(good),
        alignment=alignment,
    )


def _to_work_size(gray: np.ndarray) -> np.ndarray:
    height, width = gray.shape[:2]
    if width == WORK_WIDTH:
        return gray
    return cv2.resize(
        gray, (WORK_WIDTH, round(height * WORK_WIDTH / width)), interpolation=cv2.INTER_AREA
    )


def estimate_drift(
    reference: np.ndarray,
    frame: np.ndarray,
    points: Sequence[tuple[float, float]] = (),
) -> DriftEstimate:
    """How far has the scenery moved from ``reference`` to ``frame``?

    Both are grayscale (uint8) pictures of the same shape, though not necessarily the same size.
    ``points`` are places in the picture (fractions of its width and height) whose move is wanted,
    normally the corners of the drawn lines and zones; none means the picture's corners and middle.

    Plain pictures match best in the dark; a picture washed out by too much light matches better
    with its contrast evened out. So the evened-out pair is tried only if the plain pair gives no
    answer, and the better of the two is returned.
    """
    if reference.ndim != 2 or frame.ndim != 2:
        raise ValueError("pictures to compare must be grayscale")
    a, b = _to_work_size(reference), _to_work_size(frame)
    if abs(a.shape[0] / a.shape[1] - b.shape[0] / b.shape[1]) > 0.01:
        return _untrusted("the live picture and the reference picture have different shapes")
    plain = _fit(a, b, points)
    if plain.trusted:
        return plain
    equalise = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
    evened = _fit(equalise.apply(a), equalise.apply(b), points)
    if evened.trusted or evened.inliers > plain.inliers:
        return evened
    return plain


def shape_points(profile: Profile) -> list[tuple[float, float]]:
    """The corners of every line, zone and stop sign in ``profile``, as fractions of the picture."""
    points: list[tuple[float, float]] = []
    for line in profile.lines:
        points += [line.a, line.b]
    for zone in (*profile.detection_zones, *profile.exclusion_zones):
        points += list(zone.points)
    for sign in profile.stop_signs:
        points += [*sign.approach, sign.a, sign.b]
    return [(float(x), float(y)) for x, y in points]


# --- the monitor ----------------------------------------------------------------------------------

OK_MESSAGE = "The camera sees what it saw when the reference picture was kept."


@dataclass(frozen=True, slots=True)
class DriftConfig:
    first_check_after_s: float = 15.0  # the first look, after the service has started
    check_every_s: float = 60.0  # and then this often
    warn_shift_pct: float = 1.0  # the drawn shapes are off by at least this % of the width
    clear_shift_pct: float = 0.75  # and are back within this much (so it does not flicker)
    confirm_checks: int = 3  # sure checks in a row that must agree before a warning
    clear_checks: int = 2  # and before it is taken back


class DriftMonitor:
    """Looks at the live picture now and then, off the camera loop, and says if the view moved.

    The camera loop calls :meth:`offer` with every frame; it returns at once unless a check is
    due. A check runs on a thread of its own (OpenCV lets go of the interpreter lock while it
    works) and never raises. :meth:`status` is what the dashboard and ``/health`` show.

    The verdict is one of ``ok``, ``drifted``, ``unknown`` (not sure yet) and ``no_reference``.
    A look that cannot tell (night, fog) leaves the verdict as it was: a camera that moved in
    the day has not moved back at night, and one that was fine is not known to have moved.
    """

    def __init__(
        self,
        reference_path: Path,
        config: DriftConfig | None = None,
        *,
        publish: Callable[[dict[str, Any]], None] | None = None,
        estimator: Callable[..., DriftEstimate] = estimate_drift,
        clock: Callable[[], float] = time.monotonic,
        wall: Callable[[], float] = time.time,
        threaded: bool = True,
    ) -> None:
        self.config = config or DriftConfig()
        self._path = reference_path
        self._publish = publish
        self._estimator = estimator
        self._clock = clock
        self._wall = wall
        self._lock = threading.Lock()
        self._points: tuple[tuple[float, float], ...] = ()
        self._next_at = clock() + self.config.first_check_after_s
        self._reference: np.ndarray | None = None
        self._reference_key: tuple[int, int] | None = None  # (mtime_ns, size) of what is loaded
        self._recent: deque[DriftEstimate] = deque(
            maxlen=max(self.config.confirm_checks, self.config.clear_checks)
        )
        self._state = "unknown"
        self._message = "Waiting for the first check."
        self._sure: DriftEstimate | None = None  # the last look that could tell
        self._reason: str | None = None  # why the latest look could not tell, if it could not
        self._checked_at: int | None = None
        self._sure_at: int | None = None
        self._mailbox: np.ndarray | None = None
        self._wake = threading.Event()
        self._closed = False
        self._thread: threading.Thread | None = None
        if threaded:
            self._thread = threading.Thread(target=self._work, name="drift", daemon=True)
            self._thread.start()

    # --- the camera loop's side ---------------------------------------------------------------

    def set_points(self, points: Sequence[tuple[float, float]]) -> None:
        """The places whose move matters (the corners of the drawn shapes). None given means the
        corners and the middle of the picture."""
        with self._lock:
            self._points = tuple(points)

    def offer(self, image: np.ndarray) -> None:
        """Give the monitor the current frame (RGB). A copy is taken only when a check is due."""
        if self._closed or self._clock() < self._next_at:
            return
        with self._lock:
            if self._mailbox is not None:  # the last one has not been looked at yet
                return
            self._next_at = self._clock() + self.config.check_every_s
            self._mailbox = image.copy()
        self._wake.set()

    def close(self) -> None:
        self._closed = True
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout=10)

    # --- the checks -----------------------------------------------------------------------------

    def _work(self) -> None:
        while not self._closed:
            self._wake.wait()
            self._wake.clear()
            with self._lock:
                image, self._mailbox = self._mailbox, None
            if image is None or self._closed:
                continue
            try:
                self.check(image)
            except Exception:  # a check helps the owner look; it must never stop anything else
                log.exception("the camera-drift check failed")

    def check(self, image: np.ndarray) -> None:
        """Compare one RGB frame with the reference picture and update the verdict."""
        reference = self._load_reference()
        if reference is None:
            return
        with self._lock:
            points = self._points
        estimate = self._estimator(reference, cv2.cvtColor(image, cv2.COLOR_RGB2GRAY), points)
        self._note(estimate)

    def _load_reference(self) -> np.ndarray | None:
        """The reference picture as grayscale, read again when the file has changed."""
        try:
            stat = self._path.stat()
        except OSError:
            return self._reset("no_reference", "No reference picture has been kept yet.")
        key = (stat.st_mtime_ns, stat.st_size)
        if key != self._reference_key:
            picture = cv2.imread(str(self._path), cv2.IMREAD_GRAYSCALE)
            if picture is None:
                return self._reset("no_reference", "The reference picture cannot be read.")
            self._reference, self._reference_key = picture, key
            # A new picture is a new baseline: whatever was found against the old one is void.
            self._reset("unknown", "Checking against the new reference picture.")
        return self._reference

    def _reset(self, state: str, message: str) -> None:
        with self._lock:
            if state == "no_reference":
                self._reference, self._reference_key = None, None
            self._recent.clear()
            self._state, self._message = state, message
            self._sure = self._reason = self._checked_at = self._sure_at = None
        self._announce()

    def _note(self, estimate: DriftEstimate) -> None:
        cfg = self.config
        now_ms = round(self._wall() * 1000)
        with self._lock:
            self._checked_at = now_ms
            if not estimate.trusted:
                self._reason = estimate.reason
            else:
                self._reason, self._sure, self._sure_at = None, estimate, now_ms
                self._recent.append(estimate)
                recent = list(self._recent)
                if self._state == "drifted":
                    back = recent[-cfg.clear_checks :]
                    if len(back) >= cfg.clear_checks and all(
                        e.shift_pct < cfg.clear_shift_pct for e in back
                    ):
                        self._state, self._message = "ok", OK_MESSAGE
                else:
                    off = recent[-cfg.confirm_checks :]
                    if len(off) >= cfg.confirm_checks and all(
                        e.shift_pct >= cfg.warn_shift_pct for e in off
                    ):
                        self._state = "drifted"
                    elif estimate.shift_pct < cfg.warn_shift_pct:
                        self._state, self._message = "ok", OK_MESSAGE
                if self._state == "drifted":
                    self._message = _describe(estimate)
        self._announce()

    def _announce(self) -> None:
        if self._publish is not None:
            self._publish(self.status())

    # --- what the dashboard shows ---------------------------------------------------------------

    def status(self) -> dict[str, Any]:
        """The verdict and the numbers behind it (those of the last look that could tell)."""
        with self._lock:
            sure = self._sure
            return {
                "state": self._state,  # ok, drifted, unknown or no_reference
                "message": self._message,
                "shift_pct": None if sure is None else round(sure.shift_pct, 2),
                "dx_pct": None if sure is None else round(sure.dx_pct, 2),
                "dy_pct": None if sure is None else round(sure.dy_pct, 2),
                "rotation_deg": None if sure is None else round(sure.rotation_deg, 2),
                "scale": None if sure is None else round(sure.scale, 4),
                "alignment": None if sure is None else round(sure.alignment, 2),
                "checked_at": self._checked_at,  # the last look, whatever it found (ms)
                "sure_at": self._sure_at,  # the last look that could tell (ms)
                "unsure_because": self._reason,  # why the latest look could not tell
            }


def _describe(estimate: DriftEstimate) -> str:
    """One sentence for the owner: what moved, and by how much."""
    moves: list[str] = []
    if abs(estimate.dx_pct) >= 0.3:
        side = "right" if estimate.dx_pct > 0 else "left"
        moves.append(f"{abs(estimate.dx_pct):.1f}% of the width to the {side}")
    if abs(estimate.dy_pct) >= 0.3:
        moves.append(f"{abs(estimate.dy_pct):.1f}% {'down' if estimate.dy_pct > 0 else 'up'}")
    parts = ["the picture has moved " + " and ".join(moves)] if moves else []
    if abs(estimate.rotation_deg) >= 0.3:
        way = "clockwise" if estimate.rotation_deg > 0 else "anticlockwise"
        parts.append(f"turned {abs(estimate.rotation_deg):.1f} degrees {way}")
    if abs(estimate.scale - 1) >= 0.01:
        size = "bigger" if estimate.scale > 1 else "smaller"
        parts.append(f"the scenery looks {abs(estimate.scale - 1) * 100:.0f}% {size}")
    what = ", ".join(parts) if parts else "the view has changed"
    return (
        f"The camera's view has changed since the reference picture was kept: {what}. "
        f"The lines and zones are off by up to {estimate.shift_pct:.1f}% of the picture's width."
    )
