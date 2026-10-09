from pathlib import Path

import numpy as np
import pytest

from trafficpi.core.coco import class_name
from trafficpi.core.types import Detection
from trafficpi.vision import cli
from trafficpi.vision.compare import compare_logs, format_comparison, mean_box_area
from trafficpi.vision.recorder import DetectionLogWriter
from trafficpi.vision.sources import Frame


def det(x: float, score: float, cls: int = 2) -> Detection:
    return Detection(cls, class_name(cls), score, x, 0.4, x + 0.1, 0.5)


def write(path: Path, model: str, frames: list[list[Detection]]) -> Path:
    with DetectionLogWriter(path, {"model": model}) as log:
        for i, dets in enumerate(frames):
            log.write_frame(Frame(i, i * 33_000_000, np.zeros((2, 2, 3), np.uint8)), dets)
    return path


def test_matching_detections_are_paired_and_score_changes_measured(tmp_path: Path) -> None:
    a = write(tmp_path / "a.jsonl", "model-a", [[det(0.1, 0.6), det(0.6, 0.5)]] * 10)
    b = write(tmp_path / "b.jsonl", "model-b", [[det(0.1, 0.8), det(0.6, 0.7), det(0.8, 0.4)]] * 10)
    c = compare_logs(a, b)
    assert c.frames_compared == 10 and c.matched == 20 and c.only_a == 0 and c.only_b == 10
    assert c.mean_score_delta == pytest.approx(0.2, abs=1e-3) and c.mean_iou == pytest.approx(1.0)
    assert c.a.detections["car"] == 20 and c.b.mean_score("car") == pytest.approx(0.633, abs=1e-3)


def test_scores_below_the_minimum_are_ignored(tmp_path: Path) -> None:
    a = write(tmp_path / "a.jsonl", "a", [[det(0.1, 0.9), det(0.5, 0.15)]] * 5)
    b = write(tmp_path / "b.jsonl", "b", [[det(0.1, 0.9)]] * 5)
    c = compare_logs(a, b, min_score=0.25)
    assert (c.matched, c.only_a, c.only_b) == (5, 0, 0)
    assert compare_logs(a, b, min_score=0.1).only_a == 5


def test_a_different_class_is_not_the_same_object(tmp_path: Path) -> None:
    a = write(tmp_path / "a.jsonl", "a", [[det(0.1, 0.9, cls=2)]] * 3)
    b = write(tmp_path / "b.jsonl", "b", [[det(0.1, 0.9, cls=7)]] * 3)
    c = compare_logs(a, b)
    assert (c.matched, c.only_a, c.only_b) == (0, 3, 3)


def test_only_frames_present_in_both_logs_are_compared(tmp_path: Path) -> None:
    a = write(tmp_path / "a.jsonl", "a", [[det(0.1, 0.9)]] * 8)
    b = write(tmp_path / "b.jsonl", "b", [[det(0.1, 0.9)]] * 5)
    assert compare_logs(a, b).frames_compared == 5


def test_the_compare_command_prints_a_table(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    a = write(tmp_path / "a.jsonl", "yolov8s_h8l", [[det(0.1, 0.6)]] * 4)
    b = write(tmp_path / "b.jsonl", "yolov6n_h8l", [[det(0.1, 0.7)]] * 4)
    assert cli.main(["compare", str(a), str(b)]) == 0
    text = capsys.readouterr().out
    assert "compared 4 frames" in text and "yolov8s_h8l" in text and "yolov6n_h8l" in text
    assert "score change a->b +0.1000" in text
    assert format_comparison(compare_logs(a, b)) in text


def test_mean_box_area(tmp_path: Path) -> None:
    log = write(tmp_path / "a.jsonl", "a", [[det(0.1, 0.9)]] * 3)  # boxes are 0.1 x 0.1
    assert mean_box_area(log) == pytest.approx(0.01)
