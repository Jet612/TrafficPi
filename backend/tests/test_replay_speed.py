"""A calibration tried on a recorded clip, with no camera."""

import json
from pathlib import Path

import cv2
import numpy as np
import pytest

from tests.camera_sim import PinholeCamera
from trafficpi.core.coco import class_name
from trafficpi.core.types import Detection
from trafficpi.vision import cli
from trafficpi.vision.annotate import annotate_clip
from trafficpi.vision.profile import parse_compiled_profile
from trafficpi.vision.recorder import DetectionLogWriter
from trafficpi.vision.replay import replay_log
from trafficpi.vision.sources import Frame

CAMERA = PinholeCamera()
SPEED = 8.0  # metres a second


def picture(x: float, y: float) -> list[float]:
    seen = CAMERA.project(x, y)
    assert seen is not None
    return [seen[0], seen[1]]


PROFILE = {
    "lines": [{"id": "across", "name": "Across", "a": picture(-4, 18), "b": picture(4, 18)}],
    "calibration": {
        "corners": [picture(-4, 4), picture(4, 4), picture(4, 34), picture(-4, 34)],
        "width_m": 8.0,
        "length_m": 30.0,
    },
}


def write_drive_log(path: Path) -> Path:
    """A detection log of a car driving up the pretend road at a steady 8 m/s."""
    with DetectionLogWriter(path, {"model": "synthetic", "frame_size": [640, 360]}) as log:
        for i in range(240):
            box = CAMERA.box_of(1.75, -4.0 + SPEED * i / 30, 0.0)
            ok = box is not None and box[0] > 0 and box[2] < 1 and box[1] > 0 and box[3] < 1
            found = [Detection(2, class_name(2), 0.9, *box)] if ok and box else []  # type: ignore[misc]
            log.write_frame(Frame(i, round(i * 1e9 / 30), np.zeros((2, 2, 3), np.uint8)), found)
    return path


def test_replaying_with_a_calibration_gives_the_events_their_speeds(tmp_path: Path) -> None:
    profile = parse_compiled_profile(PROFILE)
    assert profile.calibration is not None
    replayed = replay_log(
        write_drive_log(tmp_path / "d.jsonl"),
        profile.counting_lines,
        calibration=profile.calibration,
    )
    (event,) = replayed.events
    assert event.speed_ms == pytest.approx(SPEED, rel=0.05)
    assert event.calibration == profile.calibration.id
    assert any(t.speed_ms is not None for r in replayed.results for t in r.tracks)


def test_replaying_without_one_gives_no_speeds(tmp_path: Path) -> None:
    profile = parse_compiled_profile(PROFILE)
    replayed = replay_log(write_drive_log(tmp_path / "d.jsonl"), profile.counting_lines)
    (event,) = replayed.events
    assert event.speed_ms is None and event.calibration is None


def test_the_replay_command_uses_the_profiles_calibration_and_prints_and_writes_the_speed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    log = write_drive_log(tmp_path / "d.jsonl")
    profile = tmp_path / "profile.json"
    profile.write_text(json.dumps(PROFILE))
    out = tmp_path / "events.json"
    assert cli.main(["replay", str(log), "--lines", str(profile), "--events-json", str(out)]) == 0
    text = capsys.readouterr().out
    assert " m/s" in text
    (record,) = json.loads(out.read_text())
    assert record["speed_ms"] == pytest.approx(SPEED, rel=0.05)


def test_the_replay_command_without_a_calibration_writes_a_null_speed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    log = write_drive_log(tmp_path / "d.jsonl")
    profile = tmp_path / "profile.json"
    profile.write_text(json.dumps({"lines": PROFILE["lines"]}))
    out = tmp_path / "events.json"
    assert cli.main(["replay", str(log), "--lines", str(profile), "--events-json", str(out)]) == 0
    assert " m/s" not in capsys.readouterr().out
    (record,) = json.loads(out.read_text())
    assert record["speed_ms"] is None


def test_annotating_a_clip_shows_each_vehicles_speed(tmp_path: Path) -> None:
    log = write_drive_log(tmp_path / "detections.jsonl")
    clip = tmp_path / "clip.mp4"
    writer = cv2.VideoWriter(str(clip), cv2.VideoWriter.fourcc(*"mp4v"), 30.0, (640, 360))
    for _ in range(240):
        writer.write(np.zeros((360, 640, 3), np.uint8))
    writer.release()
    profile = parse_compiled_profile(PROFILE)
    report = annotate_clip(
        clip,
        log,
        profile.counting_lines,
        video_out=tmp_path / "out.mp4",
        stills_dir=tmp_path / "stills",
        calibration=profile.calibration,
    )
    (event,) = report.events
    assert event.speed_ms == pytest.approx(SPEED, rel=0.05)
    assert report.frames_written == 240
