"""Multi-object tracking: ByteTrack-style association over a constant-velocity Kalman filter.

Each frame the detections are matched to the existing tracks by overlap (IoU) in two passes:
confident detections first, then weak ones, which keeps hold of a vehicle that is briefly hard to
see. Unmatched confident detections start new tracks. A track must be matched a few times before
it counts as a real object, and one that goes unmatched for too long is dropped.

Everything is in fractions of the frame, and time comes from the camera timestamps, so a skipped
frame is a longer time step rather than a glitch.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum, auto

import numpy as np
from scipy.optimize import linear_sum_assignment

from trafficpi.core.coco import class_name
from trafficpi.core.geometry import Point
from trafficpi.core.types import Detection

Box = tuple[float, float, float, float]  # x0, y0, x1, y1
MIN_HEIGHT = 1e-3
MAX_STEP = 30.0  # longest Kalman time step, in frame periods


@dataclass(frozen=True, slots=True)
class TrackerConfig:
    high_score: float = 0.5  # detections at or above this may start tracks and take part in pass 1
    low_score: float = 0.1  # weaker detections may still extend an existing track (pass 2)
    match_iou: float = 0.2  # minimum overlap for a pass-1 match
    low_match_iou: float = 0.4  # minimum overlap for a pass-2 match
    nms_iou: float = 0.7  # detections overlapping a better one this much are duplicates
    confirm_hits: int = 3  # matches before a track counts as a real object
    max_lost_s: float = 1.0  # a confirmed track unmatched for this long is dropped
    nominal_fps: float = 30.0  # the Kalman filter's unit of time


# The score that starts a track, for the models that score lower than ``TrackerConfig`` expects.
# yolov6n rates a car about 0.1 below yolov8s. At 0.5 it counted 23 of 35 cars at night; at 0.4 it
# counted 28, as many as yolov8s, and below that it began to count one car twice (docs/accuracy.md).
START_SCORES = {"yolov6n": 0.4}


def tracker_config_for(model: str | None) -> TrackerConfig:
    """The tracker's settings for a model, by its name or the stem of its file (``yolov6n_h8l``)."""
    for name, score in START_SCORES.items():
        if model is not None and model.startswith(name):
            return TrackerConfig(high_score=score)
    return TrackerConfig()


@dataclass(frozen=True, slots=True)
class TrackUpdate:
    """A track as of the current frame."""

    id: int
    class_id: int  # the class the detector has voted for most strongly over the track's life
    label: str
    score: float  # of the latest matched detection
    box: Box  # filtered position, fractions of the frame
    velocity: tuple[float, float]  # fractions of the frame per second
    hits: int
    age_s: float
    confirmed: bool
    # Where it is on the road, in metres across and along the calibrated rectangle, and how fast
    # it is going, in metres a second over the last second: both filled in after the tracker, by
    # the speed meter, and only when the camera is calibrated. The speed is None when
    # it cannot be believed: too little seen of the vehicle, a jump of the track, or a part of the
    # picture the calibration does not reach.
    ground: tuple[float, float] | None = None
    speed_ms: float | None = None

    @property
    def ground_point(self) -> Point:
        """Bottom centre of the box: where the object most likely touches the road."""
        return (self.box[0] + self.box[2]) / 2, self.box[3]

    @property
    def center(self) -> Point:
        return (self.box[0] + self.box[2]) / 2, (self.box[1] + self.box[3]) / 2


@dataclass(frozen=True, slots=True)
class TrackerResult:
    tracks: list[TrackUpdate]  # tracks matched in this frame
    ended: list[int]  # ids of tracks dropped in this frame


class KalmanBoxFilter:
    """Constant-velocity Kalman filter over (centre x, centre y, aspect ratio, height).

    The noise scales with the box height, so near and far objects are filtered alike.
    """

    def __init__(self, std_position: float = 1 / 20, std_velocity: float = 1 / 160) -> None:
        self._wp = std_position
        self._wv = std_velocity
        self._h = np.eye(4, 8)

    def initiate(self, measurement: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        h = max(float(measurement[3]), MIN_HEIGHT)
        mean = np.concatenate([measurement, np.zeros(4)])
        std = np.array([2 * self._wp * h] * 2 + [1e-2] + [2 * self._wp * h])
        std_v = np.array([10 * self._wv * h] * 2 + [1e-5] + [10 * self._wv * h])
        return mean, np.diag(np.square(np.concatenate([std, std_v])))

    def predict(
        self, mean: np.ndarray, cov: np.ndarray, dt: float
    ) -> tuple[np.ndarray, np.ndarray]:
        h = max(float(mean[3]), MIN_HEIGHT)
        std = [self._wp * h, self._wp * h, 1e-2, self._wp * h]
        std_v = [self._wv * h, self._wv * h, 1e-5, self._wv * h]
        noise = np.diag(np.square(np.array(std + std_v))) * max(dt, 0.0)
        motion = np.eye(8)
        motion[:4, 4:] = np.eye(4) * dt
        return motion @ mean, motion @ cov @ motion.T + noise

    def project(self, mean: np.ndarray, cov: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        h = max(float(mean[3]), MIN_HEIGHT)
        noise = np.diag(np.square([self._wp * h, self._wp * h, 1e-1, self._wp * h]))
        return self._h @ mean, self._h @ cov @ self._h.T + noise

    def update(
        self, mean: np.ndarray, cov: np.ndarray, measurement: np.ndarray
    ) -> tuple[np.ndarray, np.ndarray]:
        projected_mean, projected_cov = self.project(mean, cov)
        gain = np.linalg.solve(projected_cov, self._h @ cov).T
        new_mean = mean + gain @ (measurement - projected_mean)
        new_cov = cov - gain @ projected_cov @ gain.T
        return new_mean, new_cov


def to_xyah(box: Box) -> np.ndarray:
    x0, y0, x1, y1 = box
    w, h = x1 - x0, max(y1 - y0, MIN_HEIGHT)
    return np.array([x0 + w / 2, y0 + h / 2, w / h, h])


def to_box(state: np.ndarray) -> Box:
    cx, cy, aspect, h = (float(v) for v in state[:4])
    h = max(h, 0.0)
    w = aspect * h
    return cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2


def iou_matrix(a: Sequence[Box], b: Sequence[Box]) -> np.ndarray:
    """Pairwise intersection-over-union of two lists of boxes, shape ``(len(a), len(b))``."""
    if not a or not b:
        return np.zeros((len(a), len(b)))
    boxes_a, boxes_b = np.asarray(a, dtype=float), np.asarray(b, dtype=float)
    x0 = np.maximum(boxes_a[:, None, 0], boxes_b[None, :, 0])
    y0 = np.maximum(boxes_a[:, None, 1], boxes_b[None, :, 1])
    x1 = np.minimum(boxes_a[:, None, 2], boxes_b[None, :, 2])
    y1 = np.minimum(boxes_a[:, None, 3], boxes_b[None, :, 3])
    inter = np.clip(x1 - x0, 0, None) * np.clip(y1 - y0, 0, None)
    area_a = (boxes_a[:, 2] - boxes_a[:, 0]) * (boxes_a[:, 3] - boxes_a[:, 1])
    area_b = (boxes_b[:, 2] - boxes_b[:, 0]) * (boxes_b[:, 3] - boxes_b[:, 1])
    union = area_a[:, None] + area_b[None, :] - inter
    return np.divide(inter, union, out=np.zeros_like(inter), where=union > 0)


def suppress_duplicates(detections: Sequence[Detection], iou: float) -> list[Detection]:
    """Class-agnostic non-maximum suppression, so one vehicle seen as car *and* truck is one."""
    kept: list[Detection] = []
    for det in sorted(detections, key=lambda d: d.score, reverse=True):
        box = (det.x0, det.y0, det.x1, det.y1)
        if all(iou_matrix([box], [(k.x0, k.y0, k.x1, k.y1)])[0, 0] <= iou for k in kept):
            kept.append(det)
    return kept


class _State(Enum):
    TENTATIVE = auto()  # not yet matched often enough to trust
    TRACKED = auto()  # confirmed and matched in the latest frame
    LOST = auto()  # confirmed but unmatched recently; may reappear


class _Track:
    def __init__(self, track_id: int, det: Detection, timestamp_ns: int, kf: KalmanBoxFilter):
        self.id = track_id
        self.mean, self.cov = kf.initiate(to_xyah((det.x0, det.y0, det.x1, det.y1)))
        self.hits = 1
        self.state = _State.TENTATIVE
        self.first_ns = self.last_match_ns = timestamp_ns
        self.score = det.score
        self.class_scores: dict[int, float] = {det.class_id: det.score}

    @property
    def box(self) -> Box:
        return to_box(self.mean)

    @property
    def class_id(self) -> int:
        return max(self.class_scores, key=lambda c: self.class_scores[c])

    def predict(self, kf: KalmanBoxFilter, dt: float) -> None:
        self.mean, self.cov = kf.predict(self.mean, self.cov, dt)

    def update(self, det: Detection, timestamp_ns: int, kf: KalmanBoxFilter) -> None:
        self.mean, self.cov = kf.update(
            self.mean, self.cov, to_xyah((det.x0, det.y0, det.x1, det.y1))
        )
        self.hits += 1
        self.last_match_ns = timestamp_ns
        self.score = det.score
        self.class_scores[det.class_id] = self.class_scores.get(det.class_id, 0.0) + det.score


def _associate(
    tracks: Sequence[_Track], detections: Sequence[Detection], min_iou: float
) -> tuple[list[tuple[int, int]], list[int], list[int]]:
    """Best one-to-one matching by overlap: (matches, unmatched track indices, unmatched dets)."""
    if not tracks or not detections:
        return [], list(range(len(tracks))), list(range(len(detections)))
    iou = iou_matrix([t.box for t in tracks], [(d.x0, d.y0, d.x1, d.y1) for d in detections])
    rows, cols = linear_sum_assignment(1.0 - iou)
    matches = [(int(r), int(c)) for r, c in zip(rows, cols, strict=True) if iou[r, c] >= min_iou]
    matched_tracks = {r for r, _ in matches}
    matched_dets = {c for _, c in matches}
    return (
        matches,
        [i for i in range(len(tracks)) if i not in matched_tracks],
        [j for j in range(len(detections)) if j not in matched_dets],
    )


class ByteTracker:
    """Follows objects from frame to frame and gives each a stable id."""

    def __init__(self, config: TrackerConfig | None = None) -> None:
        self.config = config or TrackerConfig()
        self._kf = KalmanBoxFilter()
        self._tracks: list[_Track] = []
        self._next_id = 1
        self._last_ns: int | None = None

    def update(self, timestamp_ns: int, detections: Sequence[Detection]) -> TrackerResult:
        cfg = self.config
        dt = 0.0
        if self._last_ns is not None:
            dt = min(max((timestamp_ns - self._last_ns) / 1e9 * cfg.nominal_fps, 0.0), MAX_STEP)
        self._last_ns = timestamp_ns
        for track in self._tracks:
            track.predict(self._kf, dt)

        detections = suppress_duplicates(
            [d for d in detections if d.score >= cfg.low_score], cfg.nms_iou
        )
        high = [d for d in detections if d.score >= cfg.high_score]
        low = [d for d in detections if d.score < cfg.high_score]

        # Pass 1: confident detections against every live track, lost ones included.
        matches, unmatched_tracks, unmatched_high = _associate(self._tracks, high, cfg.match_iou)
        matched: list[_Track] = []
        for ti, di in matches:
            track = self._tracks[ti]
            track.update(high[di], timestamp_ns, self._kf)
            matched.append(track)

        # Pass 2: weak detections against tracks that were being followed until just now.
        remaining = [self._tracks[i] for i in unmatched_tracks]
        recent = [t for t in remaining if t.state is not _State.LOST]
        matches2, _, _ = _associate(recent, low, cfg.low_match_iou)
        rescued = set()
        for ti, di in matches2:
            recent[ti].update(low[di], timestamp_ns, self._kf)
            matched.append(recent[ti])
            rescued.add(id(recent[ti]))
        unmatched = [t for t in remaining if id(t) not in rescued]

        for track in matched:
            if track.hits >= cfg.confirm_hits:
                track.state = _State.TRACKED
        ended: list[int] = []
        survivors: list[_Track] = list(matched)
        for track in unmatched:
            lost_for_s = (timestamp_ns - track.last_match_ns) / 1e9
            if track.state is _State.TENTATIVE or lost_for_s > cfg.max_lost_s:
                ended.append(track.id)
            else:
                track.state = _State.LOST
                survivors.append(track)

        # New tracks from confident detections nothing claimed.
        for di in unmatched_high:
            det = high[di]
            track = _Track(self._next_id, det, timestamp_ns, self._kf)
            self._next_id += 1
            if cfg.confirm_hits <= 1:
                track.state = _State.TRACKED
            survivors.append(track)
            matched.append(track)

        self._tracks = survivors
        return TrackerResult([self._output(t, timestamp_ns) for t in matched], ended)

    def _output(self, track: _Track, timestamp_ns: int) -> TrackUpdate:
        fps = self.config.nominal_fps
        return TrackUpdate(
            id=track.id,
            class_id=track.class_id,
            label=class_name(track.class_id),
            score=track.score,
            box=track.box,
            velocity=(float(track.mean[4]) * fps, float(track.mean[5]) * fps),
            hits=track.hits,
            age_s=(timestamp_ns - track.first_ns) / 1e9,
            confirmed=track.hits >= self.config.confirm_hits,
        )
