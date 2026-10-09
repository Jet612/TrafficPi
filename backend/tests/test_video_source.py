from pathlib import Path

import cv2
import numpy as np
import pytest

from tests.fakes import FakeDetector, FakeSource
from trafficpi.vision import pipeline
from trafficpi.vision.sources import EndOfStream, VideoFileSource

N_FRAMES, FPS, SIZE = 24, 30.0, (320, 180)


@pytest.fixture
def clip(tmp_path: Path) -> Path:
    """A small video: frame i is a flat grey of level 10 * i, with a red square in a corner."""
    path = tmp_path / "clip.mp4"
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter.fourcc(*"mp4v"), FPS, SIZE)
    for i in range(N_FRAMES):
        frame = np.full((SIZE[1], SIZE[0], 3), 10 * i, np.uint8)
        frame[:40, :40] = (0, 0, 255)  # red in BGR
        writer.write(frame)
    writer.release()
    return path


def test_reads_every_frame_with_deterministic_timestamps(clip: Path) -> None:
    with VideoFileSource(clip, size=(160, 90)) as source:
        assert source.fps == pytest.approx(FPS)
        frames = []
        while True:
            try:
                frames.append(source.read())
            except EndOfStream:
                break
    assert len(frames) == N_FRAMES
    assert [f.index for f in frames] == list(range(N_FRAMES))
    assert [f.timestamp_ns for f in frames[:3]] == [0, 33_333_333, 66_666_667]
    assert frames[0].image.shape == (90, 160, 3)
    # Lossy compression, but the brightness ramp survives.
    assert frames[20].image[60:, 100:].mean() > frames[2].image[60:, 100:].mean() + 100


def test_channel_order(clip: Path) -> None:
    with VideoFileSource(clip, size=SIZE, channel_order="rgb") as rgb:
        red = rgb.read().image[10, 10]
    with VideoFileSource(clip, size=SIZE, channel_order="bgr") as bgr:
        blue_green_red = bgr.read().image[10, 10]
    assert red[0] > 200 and red[2] < 60  # R, G, B
    assert blue_green_red[2] > 200 and blue_green_red[0] < 60  # B, G, R


def test_a_missing_file_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="cannot open"):
        VideoFileSource(tmp_path / "nope.mp4").start()


def test_the_pipeline_stops_cleanly_at_the_end_of_a_recording() -> None:
    report = pipeline.run(FakeSource(limit=12), FakeDetector(), seconds=30)
    assert report.frames == 12 and report.elapsed_s < 5
