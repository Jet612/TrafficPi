import os
import signal
import threading
import time

import pytest

from tests.fakes import FakeDetector, FakeSource
from trafficpi.vision import pipeline, shutdown
from trafficpi.vision.shutdown import StopRequest


def test_a_request_is_remembered() -> None:
    stop = StopRequest()
    assert not stop.requested
    stop.request()
    assert stop.requested


def test_signals_set_the_flag_and_extra_ones_are_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    exits: list[int] = []
    monkeypatch.setattr(os, "_exit", exits.append)
    stop = StopRequest()
    stop._handle(signal.SIGINT, None)
    stop._handle(signal.SIGINT, None)  # e.g. forwarded a second time by `uv run`
    assert stop.requested and exits == []


def test_a_later_signal_forces_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    exits: list[int] = []
    monkeypatch.setattr(os, "_exit", exits.append)
    clock = [100.0]
    monkeypatch.setattr(shutdown.time, "monotonic", lambda: clock[0])
    stop = StopRequest()
    stop._handle(signal.SIGTERM, None)
    clock[0] += shutdown.FORCE_AFTER_S + 0.1
    stop._handle(signal.SIGTERM, None)
    assert exits == [128 + signal.SIGTERM]


def test_the_pipeline_stops_when_asked() -> None:
    stop = StopRequest()
    seen: list[int] = []

    def on_frame(frame, detections) -> None:  # type: ignore[no-untyped-def]
        seen.append(frame.index)
        if len(seen) == 5:
            stop.request()

    report = pipeline.run(FakeSource(), FakeDetector(), seconds=30, on_frame=on_frame, stop=stop)
    assert report.frames == 5 and report.elapsed_s < 5


def test_waiting_ends_early_when_a_stop_arrives() -> None:
    stop = StopRequest()
    assert stop.wait(0.01) is False
    threading.Timer(0.05, stop.request).start()
    started = time.monotonic()
    assert stop.wait(30) is True and time.monotonic() - started < 5
