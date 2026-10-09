from pathlib import Path

import numpy as np
import pytest

from trafficpi.core.types import Detection
from trafficpi.vision.recorder import DetectionLogWriter, read_detection_log
from trafficpi.vision.sources import Frame


def frame(index: int, ts: int, **kw: float | None) -> Frame:
    return Frame(index=index, timestamp_ns=ts, image=np.zeros((2, 2, 3), np.uint8), **kw)  # type: ignore[arg-type]


def test_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "log" / "detections.jsonl"
    car = Detection(2, "car", 0.8312345, 0.1, 0.2, 0.3, 0.4)
    with DetectionLogWriter(path, {"model": "yolov8s_h8l", "frame_size": [640, 360]}) as log:
        log.write_frame(frame(0, 1_000, exposure_us=4000, analogue_gain=1.5), [car])
        log.write_frame(frame(1, 34_000), [])
    header, frames = read_detection_log(path)
    assert header["model"] == "yolov8s_h8l" and header["version"] == 1
    first, second = list(frames)
    assert (first.index, first.timestamp_ns, first.exposure_us, first.analogue_gain) == (
        0,
        1_000,
        4000,
        1.5,
    )
    assert first.detections == [
        Detection(2, "car", 0.8312, 0.1, 0.2, 0.3, 0.4)
    ]  # scores rounded to 4 places
    assert (second.index, second.timestamp_ns, second.detections) == (1, 34_000, [])
    assert second.exposure_us is None and second.analogue_gain is None


def test_rejects_a_file_without_a_header(tmp_path: Path) -> None:
    path = tmp_path / "bad.jsonl"
    path.write_text('{"type":"frame","i":0,"t_ns":1,"dets":[]}\n')
    with pytest.raises(ValueError, match="header"):
        read_detection_log(path)


def test_rejects_an_unknown_version(tmp_path: Path) -> None:
    path = tmp_path / "old.jsonl"
    path.write_text('{"type":"header","version":99}\n')
    with pytest.raises(ValueError, match="version"):
        read_detection_log(path)
