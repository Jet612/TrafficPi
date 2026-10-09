"""Render tracks and counts onto a recorded clip, to check them by eye."""

from __future__ import annotations

import bisect
from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

from trafficpi.core.geometry import Point
from trafficpi.vision.engine import CountTotals
from trafficpi.vision.replay import replay_log
from trafficpi.vision.rules import CountEvent, CountingLine
from trafficpi.vision.tracker import TrackUpdate

COLORS = {
    0: (0, 165, 255),
    1: (255, 128, 0),
    2: (0, 255, 0),
    3: (255, 0, 255),
    5: (0, 255, 255),
    7: (255, 255, 0),
}
LINE_COLOR = (0, 215, 255)
FONT = cv2.FONT_HERSHEY_SIMPLEX
BANNER_S = 2.0  # how long a count event stays on screen


@dataclass(slots=True)
class AnnotateReport:
    frames_written: int = 0
    stills: list[Path] = field(default_factory=list)
    events: list[CountEvent] = field(default_factory=list)


def _px(point: Point, size: tuple[int, int]) -> tuple[int, int]:
    return round(point[0] * size[0]), round(point[1] * size[1])


def draw(
    image: np.ndarray,
    *,
    lines: Sequence[CountingLine],
    tracks: Sequence[TrackUpdate],
    trails: dict[int, deque[Point]],
    banners: Sequence[str],
    totals: CountTotals,
) -> None:
    """Draw on a BGR image in place."""
    height, width = image.shape[:2]
    size = (width, height)
    for cfg in lines:
        a, b = _px(cfg.line.a, size), _px(cfg.line.b, size)
        cv2.line(image, a, b, LINE_COLOR, 2)
        # An arrow towards the +1 side: the right-hand side of a -> b.
        dx, dy = b[0] - a[0], b[1] - a[1]
        length = max((dx * dx + dy * dy) ** 0.5, 1.0)
        mid = ((a[0] + b[0]) // 2, (a[1] + b[1]) // 2)
        tip = (round(mid[0] - dy / length * 30), round(mid[1] + dx / length * 30))
        cv2.arrowedLine(image, mid, tip, LINE_COLOR, 2, tipLength=0.4)
        label_at = (a[0], max(14, a[1] - 8))
        cv2.putText(image, f"{cfg.id} (+1 ->)", label_at, FONT, 0.5, LINE_COLOR, 1)
    for track in tracks:
        color = COLORS.get(track.class_id, (200, 200, 200))
        if not track.confirmed:
            color = (128, 128, 128)
        x0, y0, x1, y1 = track.box
        cv2.rectangle(image, _px((x0, y0), size), _px((x1, y1), size), color, 2)
        label_at = (round(x0 * width), max(14, round(y0 * height) - 5))
        cv2.putText(image, f"#{track.id} {track.label}", label_at, FONT, 0.5, color, 1)
        points = [_px(p, size) for p in trails.get(track.id, ())]
        if len(points) > 1:
            cv2.polylines(image, [np.array(points, np.int32)], False, color, 2)
    summary = ", ".join(
        f"{cfg.id}: +{totals.total(cfg.id, 1)} / -{totals.total(cfg.id, -1)}" for cfg in lines
    )
    for i, text in enumerate([summary, *banners]):
        if text:
            cv2.putText(image, text, (10, 22 + 22 * i), FONT, 0.6, (255, 255, 255), 3)
            cv2.putText(image, text, (10, 22 + 22 * i), FONT, 0.6, (0, 0, 0), 1)


def annotate_clip(
    clip: Path,
    log: Path,
    lines: Sequence[CountingLine],
    *,
    video_out: Path | None = None,
    stills_dir: Path | None = None,
    trail_s: float = 2.0,
) -> AnnotateReport:
    """Draw the tracks, trails, lines and running counts of ``log`` onto ``clip``.

    Clip frames are matched to log frames by time since the first frame of each (the recorder
    starts both together, so they agree to within a frame or two). With ``stills_dir`` a JPEG is
    saved at every count event.
    """
    replayed = replay_log(log, lines)
    stamps, results = replayed.timestamps_ns, replayed.results
    report = AnnotateReport()
    if not stamps:
        return report
    relative = [(t - stamps[0]) / 1e9 for t in stamps]

    capture = cv2.VideoCapture(str(clip))
    if not capture.isOpened():
        raise RuntimeError(f"cannot open video {clip}")
    fps = capture.get(cv2.CAP_PROP_FPS) or 30.0
    size = (int(capture.get(cv2.CAP_PROP_FRAME_WIDTH)), int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT)))
    writer = None
    if video_out is not None:
        video_out.parent.mkdir(parents=True, exist_ok=True)
        writer = cv2.VideoWriter(str(video_out), cv2.VideoWriter.fourcc(*"mp4v"), fps, size)
    if stills_dir is not None:
        stills_dir.mkdir(parents=True, exist_ok=True)

    trails: dict[int, deque[Point]] = {}
    totals = CountTotals()
    banners: deque[tuple[float, str]] = deque()
    current = -1
    index = 0
    try:
        while True:
            ok, image = capture.read()
            if not ok:
                break
            now = index / fps
            target = max(bisect.bisect_right(relative, now) - 1, 0)
            new_event_frame = False
            while current < target:  # advance through log frames, updating trails and counts
                current += 1
                result = results[current]
                for track in result.tracks:
                    trail = trails.setdefault(track.id, deque(maxlen=max(2, round(trail_s * fps))))
                    trail.append(track.ground_point)
                for ended in result.ended:
                    trails.pop(ended, None)
                for event in result.events:
                    if isinstance(event, CountEvent):
                        totals.add(event)
                        report.events.append(event)
                        text = (
                            f"{event.line_id} {event.direction:+d} {event.label} #{event.track_id}"
                        )
                        banners.append((relative[current], text))
                        new_event_frame = True
            while banners and now - banners[0][0] > BANNER_S:
                banners.popleft()
            shown = [text for _, text in banners]
            tracks = results[current].tracks
            draw(image, lines=lines, tracks=tracks, trails=trails, banners=shown, totals=totals)
            if writer is not None:
                writer.write(image)
                report.frames_written += 1
            if stills_dir is not None and new_event_frame:
                path = stills_dir / f"event-{len(report.events):03d}-t{now:07.2f}s.jpg"
                cv2.imwrite(str(path), image, [cv2.IMWRITE_JPEG_QUALITY, 90])
                report.stills.append(path)
            index += 1
    finally:
        capture.release()
        if writer is not None:
            writer.release()
    return report
