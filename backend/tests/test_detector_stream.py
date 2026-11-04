"""Sending frames ahead of the results: what ``detect_stream`` promises, without the accelerator."""

import sys
from collections import deque
from concurrent.futures import Future
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest

from tests.fakes import FakeSource
from trafficpi.core.coco import COCO_NAMES
from trafficpi.vision import pipeline
from trafficpi.vision.detector import GROUPS_IN_FLIGHT, HailoDetector
from trafficpi.vision.sources import Frame

CAR = 2


class SlowHailo:
    """Reads a frame only when its group finishes, as the chip's DMA does, so a canvas that is
    written again while its group is on its way shows up as the wrong frame."""

    def __init__(self, hef_path: str, batch_size: int | None = None) -> None:
        self.batch_size = batch_size
        self.pending: deque[tuple[list[np.ndarray], Future[list[Any]]]] = deque()
        self.most_in_flight = 0

    def get_input_shape(self) -> tuple[int, int, int]:
        return 640, 640, 3

    def _output(self, canvas: np.ndarray) -> list[np.ndarray]:
        """A car whose score tells which frame was on the canvas (see ``frames``)."""
        per_class = [np.zeros((0, 5)) for _ in COCO_NAMES]
        score = canvas[320, 320, 0] / 255
        per_class[CAR] = np.array([[0.4, 0.4, 0.6, 0.6, score]])
        return per_class

    def _finish_oldest(self) -> None:
        canvases, future = self.pending.popleft()
        future.set_result([self._output(canvas) for canvas in canvases])

    def run_async(self, input_data: list[np.ndarray]) -> Future[list[Any]]:
        assert self.batch_size is not None and len(input_data) <= self.batch_size
        future: Future[list[Any]] = Future()
        self.pending.append((list(input_data), future))
        self.most_in_flight = max(self.most_in_flight, len(self.pending))
        if len(self.pending) > GROUPS_IN_FLIGHT:
            raise AssertionError("more groups in flight than there are canvases for")
        return future

    def run(self, input_data: Any) -> Any:
        if self.batch_size is None:
            return self._output(input_data)
        return [self._output(input_data[0])]


class FinishOnRead(SlowHailo):
    """Every group finishes the moment somebody waits for it, and not before."""

    def run_async(self, input_data: list[np.ndarray]) -> Future[list[Any]]:
        future = super().run_async(input_data)
        pending = self

        class Waited(Future):  # type: ignore[type-arg]
            def result(self, timeout: float | None = None) -> list[Any]:
                while not future.done():
                    pending._finish_oldest()
                return future.result()

        return Waited()


@pytest.fixture
def make_detector(monkeypatch: pytest.MonkeyPatch) -> Any:
    devices = SimpleNamespace(Hailo=FinishOnRead)
    monkeypatch.setitem(sys.modules, "picamera2", SimpleNamespace(devices=devices))
    monkeypatch.setitem(sys.modules, "picamera2.devices", devices)

    def make(batch_size: int | None) -> HailoDetector:
        return HailoDetector("model.hef", batch_size=batch_size, score_threshold=0.0)  # type: ignore[arg-type]

    return make


def frames(count: int) -> list[Frame]:
    """Frames whose centre pixel says which they are."""
    return [
        Frame(i, i * 33_000_000, np.full((360, 640, 3), (i * 7 + 10) % 256, np.uint8))
        for i in range(count)
    ]


def scores(found: list[tuple[Frame, Any]]) -> list[float]:
    return [round(detections[0].score, 4) for _, detections in found]


@pytest.mark.parametrize("count", [1, 3, 4, 5, 8, 9, 23])
def test_every_frame_comes_back_in_order_with_what_detect_finds_in_it(
    make_detector: Any, count: int
) -> None:
    one_by_one = make_detector(None)
    expected = [one_by_one.detect(f.image)[0].score for f in frames(count)]
    found = list(make_detector(4).detect_stream(frames(count)))
    assert [frame.index for frame, _ in found] == list(range(count))
    assert scores(found) == [round(s, 4) for s in expected]


def test_a_detector_with_no_batch_size_takes_one_frame_at_a_time(make_detector: Any) -> None:
    found = list(make_detector(None).detect_stream(frames(3)))
    assert [frame.index for frame, _ in found] == [0, 1, 2]


def test_two_groups_are_on_their_way_before_the_first_result_is_waited_for(
    make_detector: Any,
) -> None:
    detector = make_detector(4)
    stream = detector.detect_stream(frames(12))
    next(stream)
    assert detector._hailo.most_in_flight == GROUPS_IN_FLIGHT


def test_a_frame_of_another_size_gets_its_own_canvases(make_detector: Any) -> None:
    detector = make_detector(2)
    assert len(list(detector.detect_stream(frames(4)))) == 4
    wide = Frame(0, 0, np.full((180, 640, 3), 200, np.uint8))
    ((_, detections),) = detector.detect_stream([wide])
    assert detections[0].score == pytest.approx(200 / 255)
    assert (detector._group_canvases[0][0][:100] == 114).all()  # no old picture in the padding


def test_timings_describe_the_frame_just_returned(make_detector: Any) -> None:
    detector = make_detector(4)
    list(detector.detect_stream(frames(8)))
    assert detector.timings.preprocess_ms > 0 and detector.timings.postprocess_ms > 0


def test_a_closed_detector_refuses_more_frames(make_detector: Any) -> None:
    detector = make_detector(2)
    detector.close()
    with pytest.raises(RuntimeError, match="closed"):
        list(detector.detect_stream(frames(2)))


def test_run_ahead_reports_every_frame_of_a_recording_in_order(make_detector: Any) -> None:
    class Clip(FakeSource):
        def read(self) -> Frame:
            frame = super().read()
            frame.image = np.full((360, 640, 3), frame.index + 1, np.uint8)
            return frame

    seen: list[int] = []
    windows: list[pipeline.WindowReport] = []
    report = pipeline.run_ahead(
        Clip(limit=21),
        make_detector(4),
        seconds=30,
        on_frame=lambda frame, detections: seen.append(frame.index),
        on_report=windows.append,
        report_every=0.01,
    )
    assert seen == sorted(seen) and report.frames == len(seen) >= 18
    assert report.detections["car"] == report.frames
    assert pipeline.LATENCY not in report.stats.stages()  # there is no camera to be behind
    assert pipeline.INTERVAL not in report.stats.stages()
    assert report.skipped == 0
    assert report.stats.summary(pipeline.LOOP).count == report.frames
