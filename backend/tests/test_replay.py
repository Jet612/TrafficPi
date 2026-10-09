import json
from pathlib import Path

import cv2
import numpy as np
import pytest

from tests.fakes import write_crossing_log
from trafficpi.vision import cli
from trafficpi.vision.annotate import annotate_clip
from trafficpi.vision.lines import parse_counting_lines
from trafficpi.vision.replay import replay_log

LINES = parse_counting_lines(
    {"lines": [{"id": "main", "name": "Main", "a": [0.2, 0.5], "b": [0.8, 0.5]}]}
)


def test_replaying_a_log_counts_the_crossing(tmp_path: Path) -> None:
    log = write_crossing_log(tmp_path / "detections.jsonl")
    replayed = replay_log(log, LINES)
    assert len(replayed.results) == 100 and replayed.duration_s == pytest.approx(99 / 30)
    assert replayed.confirmed_tracks == {1}
    (event,) = replayed.events
    assert (event.line_id, event.direction, event.label) == ("main", 1, "car")
    assert replayed.totals.total("main", 1) == 1


def test_replaying_without_lines_only_tracks(tmp_path: Path) -> None:
    replayed = replay_log(write_crossing_log(tmp_path / "d.jsonl"))
    assert replayed.events == [] and replayed.confirmed_tracks == {1}


def test_the_replay_command_prints_counts_and_writes_events(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    log = write_crossing_log(tmp_path / "d.jsonl")
    lines = tmp_path / "lines.json"
    lines.write_text(
        json.dumps({"lines": [{"id": "main", "name": "Main", "a": [0.2, 0.5], "b": [0.8, 0.5]}]})
    )
    out = tmp_path / "events.json"
    assert cli.main(["replay", str(log), "--lines", str(lines), "--events-json", str(out)]) == 0
    text = capsys.readouterr().out
    assert "100 frames" in text and "1 confirmed tracks" in text
    assert "+1:    1   car 1" in text and "-1:    0" in text
    (record,) = json.loads(out.read_text())
    assert record["line"] == "main" and record["direction"] == 1 and 1.5 < record["t_s"] < 2.2


def test_annotating_a_clip_writes_a_video_and_a_still_per_event(tmp_path: Path) -> None:
    folder = tmp_path / "rec"
    folder.mkdir()
    write_crossing_log(folder / "detections.jsonl", frames=100)
    writer = cv2.VideoWriter(
        str(folder / "clip.mp4"), cv2.VideoWriter.fourcc(*"mp4v"), 30.0, (320, 180)
    )
    for _ in range(100):
        writer.write(np.zeros((180, 320, 3), np.uint8))
    writer.release()

    report = annotate_clip(
        folder / "clip.mp4", folder / "detections.jsonl", LINES,
        video_out=folder / "annotated.mp4", stills_dir=folder / "stills",
    )  # fmt: skip
    assert report.frames_written == 100 and len(report.events) == 1
    (still,) = report.stills
    image = cv2.imread(str(still))
    assert image is not None and image.shape == (180, 320, 3)
    assert image.any()  # something was drawn on the black frame
    assert (folder / "annotated.mp4").stat().st_size > 0


def test_the_annotate_command_uses_the_default_file_names(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    folder = tmp_path / "rec"
    folder.mkdir()
    write_crossing_log(folder / "detections.jsonl")
    writer = cv2.VideoWriter(
        str(folder / "clip.mp4"), cv2.VideoWriter.fourcc(*"mp4v"), 30.0, (160, 90)
    )
    for _ in range(100):
        writer.write(np.zeros((90, 160, 3), np.uint8))
    writer.release()
    lines = tmp_path / "lines.json"
    lines.write_text(json.dumps({"lines": [{"id": "main", "a": [0.2, 0.5], "b": [0.8, 0.5]}]}))
    assert cli.main(["annotate", str(folder), "--lines", str(lines)]) == 0
    assert "100 frames written, 1 count events" in capsys.readouterr().out
    assert (folder / "annotated.mp4").exists() and len(
        list((folder / "event-stills").glob("*.jpg"))
    ) == 1
