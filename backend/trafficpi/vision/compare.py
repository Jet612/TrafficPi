"""Comparing two detection logs of the same footage, e.g. two models or two colour orders."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from scipy.optimize import linear_sum_assignment

from trafficpi.core.types import Detection
from trafficpi.vision.recorder import read_detection_log
from trafficpi.vision.tracker import iou_matrix


@dataclass(slots=True)
class LogSummary:
    label: str
    frames: int = 0
    detections: Counter[str] = field(default_factory=Counter)
    score_sum: defaultdict[str, float] = field(default_factory=lambda: defaultdict(float))

    def mean_score(self, label: str) -> float:
        n = self.detections[label]
        return self.score_sum[label] / n if n else 0.0

    @property
    def total(self) -> int:
        return sum(self.detections.values())


@dataclass(slots=True)
class Comparison:
    a: LogSummary
    b: LogSummary
    frames_compared: int = 0
    matched: int = 0  # the same object seen by both: overlapping boxes of the same class
    only_a: int = 0
    only_b: int = 0
    score_delta_sum: float = 0.0  # sum of (score in b) - (score in a) over matched pairs
    iou_sum: float = 0.0

    @property
    def mean_score_delta(self) -> float:
        return self.score_delta_sum / self.matched if self.matched else 0.0

    @property
    def mean_iou(self) -> float:
        return self.iou_sum / self.matched if self.matched else 0.0


def _match(
    a: list[Detection], b: list[Detection], min_iou: float
) -> list[tuple[Detection, Detection, float]]:
    if not a or not b:
        return []
    iou = iou_matrix([(d.x0, d.y0, d.x1, d.y1) for d in a], [(d.x0, d.y0, d.x1, d.y1) for d in b])
    for i, da in enumerate(a):  # only the same class can be the same object
        for j, db in enumerate(b):
            if da.class_id != db.class_id:
                iou[i, j] = 0.0
    rows, cols = linear_sum_assignment(1.0 - iou)
    return [
        (a[r], b[c], float(iou[r, c]))
        for r, c in zip(rows, cols, strict=True)
        if iou[r, c] >= min_iou
    ]


def compare_logs(
    path_a: Path, path_b: Path, *, min_score: float = 0.25, min_iou: float = 0.5
) -> Comparison:
    """Match the detections of two logs frame by frame (by frame index) and summarise."""
    header_a, frames_a = read_detection_log(path_a)
    header_b, frames_b = read_detection_log(path_b)
    out = Comparison(
        LogSummary(str(header_a.get("model", path_a.stem))),
        LogSummary(str(header_b.get("model", path_b.stem))),
    )
    by_index_b = {f.index: f for f in frames_b}
    for frame_a in frames_a:
        frame_b = by_index_b.get(frame_a.index)
        if frame_b is None:
            continue
        dets_a = [d for d in frame_a.detections if d.score >= min_score]
        dets_b = [d for d in frame_b.detections if d.score >= min_score]
        out.frames_compared += 1
        for summary, dets in ((out.a, dets_a), (out.b, dets_b)):
            summary.frames += 1
            for d in dets:
                summary.detections[d.label] += 1
                summary.score_sum[d.label] += d.score
        pairs = _match(dets_a, dets_b, min_iou)
        out.matched += len(pairs)
        out.only_a += len(dets_a) - len(pairs)
        out.only_b += len(dets_b) - len(pairs)
        out.score_delta_sum += sum(db.score - da.score for da, db, _ in pairs)
        out.iou_sum += sum(iou for _, _, iou in pairs)
    return out


def format_comparison(c: Comparison) -> str:
    labels = sorted(set(c.a.detections) | set(c.b.detections))
    lines = [
        f"compared {c.frames_compared} frames",
        f"{'':14}{c.a.label[:22]:>24}{c.b.label[:22]:>24}",
        f"{'detections':14}{c.a.total:>24}{c.b.total:>24}",
    ]
    for label in labels:
        lines.append(
            f"  {label:12}{c.a.detections[label]:>14} (mean {c.a.mean_score(label):.3f})"
            f"{c.b.detections[label]:>14} (mean {c.b.mean_score(label):.3f})"
        )
    lines += [
        f"matched objects (same class, boxes overlap): {c.matched}"
        f" (mean IoU {c.mean_iou:.3f}; mean score change a->b {c.mean_score_delta:+.4f})",
        f"only in a: {c.only_a}    only in b: {c.only_b}",
    ]
    return "\n".join(lines)


def mean_box_area(path: Path, *, min_score: float = 0.25) -> float:
    """Mean area of the boxes in a log, as a fraction of the frame (a rough size measure)."""
    _, frames = read_detection_log(path)
    areas = [
        (d.x1 - d.x0) * (d.y1 - d.y0) for f in frames for d in f.detections if d.score >= min_score
    ]
    return float(np.mean(areas)) if areas else 0.0
