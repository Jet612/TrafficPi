"""Detection logs: what the detector saw in each frame, as JSON Lines.

A log is the input for deterministic replays (tests and rule development without a camera or
an accelerator). Line 1 is a header; every following line is one frame::

    {"type": "header", "version": 1, "model": "yolov8s_h8l", "frame_size": [640, 360], ...}
    {"type": "frame", "i": 0, "t_ns": 123456789, "exp_us": 4000, "gain": 1.5,
     "dets": [[2, 0.83, 0.1, 0.2, 0.3, 0.4]]}

Each detection is ``[class_id, score, x0, y0, x1, y1]`` with the box as fractions (0-1) of the
frame. ``t_ns`` is the camera's sensor timestamp.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Any, Self

from trafficpi.core.coco import class_name
from trafficpi.core.types import Detection
from trafficpi.vision.sources import Frame

LOG_VERSION = 1


@dataclass(frozen=True, slots=True)
class LoggedFrame:
    index: int
    timestamp_ns: int
    detections: list[Detection]
    exposure_us: int | None = None
    analogue_gain: float | None = None


class DetectionLogWriter:
    """Appends one line per frame. Use as a context manager."""

    def __init__(self, path: Path, header: dict[str, Any]) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._file: IO[str] = self.path.open("w", encoding="utf-8")
        self._write({"type": "header", "version": LOG_VERSION, **header})

    def _write(self, record: dict[str, Any]) -> None:
        self._file.write(json.dumps(record, separators=(",", ":")) + "\n")

    def write_frame(self, frame: Frame, detections: list[Detection]) -> None:
        record: dict[str, Any] = {"type": "frame", "i": frame.index, "t_ns": frame.timestamp_ns}
        if frame.exposure_us is not None:
            record["exp_us"] = frame.exposure_us
        if frame.analogue_gain is not None:
            record["gain"] = round(frame.analogue_gain, 3)
        record["dets"] = [
            [
                d.class_id,
                round(d.score, 4),
                round(d.x0, 4),
                round(d.y0, 4),
                round(d.x1, 4),
                round(d.y1, 4),
            ]
            for d in detections
        ]
        self._write(record)

    def close(self) -> None:
        self._file.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def read_detection_log(path: Path) -> tuple[dict[str, Any], Iterator[LoggedFrame]]:
    """Read a log: the header, and a lazy iterator over its frames."""
    file = Path(path).open(encoding="utf-8")  # noqa: SIM115 - closed by the generator below
    header = json.loads(file.readline())
    if header.get("type") != "header":
        file.close()
        raise ValueError(f"{path} does not start with a header line")
    if header.get("version") != LOG_VERSION:
        file.close()
        raise ValueError(f"{path} has log version {header.get('version')}, expected {LOG_VERSION}")

    def frames() -> Iterator[LoggedFrame]:
        with file:
            for line in file:
                record = json.loads(line)
                if record.get("type") != "frame":
                    continue
                yield LoggedFrame(
                    index=record["i"],
                    timestamp_ns=record["t_ns"],
                    detections=[
                        Detection(int(c), class_name(int(c)), s, x0, y0, x1, y1)
                        for c, s, x0, y0, x1, y1 in record["dets"]
                    ],
                    exposure_us=record.get("exp_us"),
                    analogue_gain=record.get("gain"),
                )

    return header, frames()
