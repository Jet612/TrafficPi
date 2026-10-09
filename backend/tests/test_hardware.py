"""Tests that need the real camera and accelerator. Run with ``uv run pytest --hardware``."""

import time

import numpy as np
import pytest

from trafficpi.vision import pipeline
from trafficpi.vision.detector import HailoDetector, find_hef
from trafficpi.vision.sources import PiCameraSource

pytestmark = pytest.mark.hardware


def test_detector_runs_on_a_blank_frame() -> None:
    with HailoDetector(find_hef()) as detector:
        assert detector.input_size == (640, 640)
        assert detector.detect(np.zeros((360, 640, 3), np.uint8)) == []
        assert detector.timings.infer_ms > 0


def test_camera_frames_and_clock() -> None:
    with PiCameraSource() as camera:
        frames = [camera.read() for _ in range(10)]
    assert frames[0].image.shape == (360, 640, 3) and frames[0].image.dtype == np.uint8
    stamps = [f.timestamp_ns for f in frames]
    assert stamps == sorted(stamps) and len(set(stamps)) == len(stamps)
    # Sensor timestamps are on the monotonic clock: a recent frame is a few frame periods old.
    age_ms = (time.monotonic_ns() - frames[-1].timestamp_ns) / 1e6
    assert 0 < age_ms < 1000, age_ms
    assert frames[-1].exposure_us is not None and frames[-1].lens_position is not None


def test_end_to_end_run_keeps_up_with_the_camera() -> None:
    with HailoDetector(find_hef()) as detector, PiCameraSource() as camera:
        report = pipeline.run(camera, detector, seconds=4, report_every=2)
    assert report.fps > 20
    assert report.stats.summary(pipeline.LATENCY).p95 < 250
