"""The camera-drift check inside the vision service (plan.md M7, D40)."""

from __future__ import annotations

import json
import threading
from pathlib import Path

import cv2
import numpy as np
import pytest

from tests.fakes import STOP_SIGN, ScriptedDetector, ScriptedSource, car_crossing
from trafficpi.core.profile import parse_profile
from trafficpi.db import create_db_engine, upgrade_to_head
from trafficpi.db.store import Store
from trafficpi.vision.drift import DriftConfig, DriftEstimate, DriftMonitor, shape_points
from trafficpi.vision.service import LiveState, VisionService
from trafficpi.vision.shutdown import StopRequest

PROFILE = {
    "lines": [{"id": "main", "name": "Main", "a": [0.2, 0.5], "b": [0.8, 0.5]}],
    "stop_signs": [STOP_SIGN],
}
EAGER = DriftConfig(first_check_after_s=0, check_every_s=0.0)  # a look at every frame offered


def sure(shift: float) -> DriftEstimate:
    return DriftEstimate(trusted=True, shift_pct=shift, inliers=80, matches=100)


@pytest.fixture
def store() -> Store:
    engine = create_db_engine(":memory:")
    upgrade_to_head(engine)
    s = Store(engine)
    s.ensure_camera()
    s.set_profile(PROFILE)
    return s


@pytest.fixture
def reference(tmp_path: Path) -> Path:
    path = tmp_path / "reference.jpg"
    cv2.imwrite(str(path), np.full((360, 640), 120, np.uint8))
    return path


def run_with(
    store: Store,
    monitor: DriftMonitor | None,
    live: LiveState,
    *,
    wait_for: threading.Event | None = None,
) -> VisionService:
    """Run a car across the picture. At frame 20 the loop waits (up to 5 s) for a check to have
    been made; if none was, the test sees that in ``wait_for``, and the run is not held up."""

    def on_read(i: int) -> None:
        if i == 20 and wait_for is not None:
            wait_for.wait(5)

    source = ScriptedSource(car_crossing(), on_read=on_read)
    service = VisionService(source, ScriptedDetector(source), store, live=live, drift=monitor)
    service.run(StopRequest())
    return service


def test_the_service_looks_at_the_view_and_publishes_what_it_finds(
    store: Store, reference: Path
) -> None:
    live, done = LiveState(), threading.Event()

    def estimator(*_: object) -> DriftEstimate:
        done.set()
        return sure(0.2)

    monitor = DriftMonitor(reference, EAGER, publish=live.set_drift, estimator=estimator)
    run_with(store, monitor, live, wait_for=done)
    assert done.is_set(), "no check was made while the service ran"
    drift = live.snapshot()["drift"]
    assert drift["state"] == "ok" and drift["shift_pct"] == 0.2
    json.dumps(live.snapshot())  # the web server sends all of it as JSON


def test_the_corners_of_the_drawn_shapes_are_what_it_checks(store: Store, reference: Path) -> None:
    live, done, seen = LiveState(), threading.Event(), []

    def estimator(ref: np.ndarray, frame: np.ndarray, points: object) -> DriftEstimate:
        seen.append(points)
        done.set()
        return sure(0.0)

    monitor = DriftMonitor(reference, EAGER, publish=live.set_drift, estimator=estimator)
    run_with(store, monitor, live, wait_for=done)
    assert done.is_set(), "no check was made while the service ran"
    stored = store.get_profile()
    assert stored is not None
    expected = tuple(shape_points(parse_profile(stored[0])))
    assert seen[0] == expected
    assert (0.2, 0.5) in expected and len(
        expected
    ) > 4  # the line's end, and the stop sign's corners


def test_a_view_that_has_drifted_changes_nothing_that_is_counted(
    store: Store, reference: Path
) -> None:
    """The warning is for people to read. Counting goes on exactly as without it."""
    source = ScriptedSource(car_crossing())
    baseline = VisionService(source, ScriptedDetector(source), store)
    baseline.run(StopRequest())
    counted_without = baseline.events_recorded

    live, done = LiveState(), threading.Event()

    def estimator(*_: object) -> DriftEstimate:
        done.set()
        return sure(9.0)  # the view is far off, at every look

    monitor = DriftMonitor(
        reference,
        DriftConfig(first_check_after_s=0, check_every_s=0.0, confirm_checks=1),
        publish=live.set_drift,
        estimator=estimator,
    )
    service = run_with(store, monitor, live, wait_for=done)
    assert done.is_set(), "no check was made while the service ran"
    assert live.snapshot()["drift"]["state"] == "drifted"
    assert counted_without == 1 and service.events_recorded == counted_without


def test_the_monitor_is_stopped_when_the_service_is(store: Store, reference: Path) -> None:
    live = LiveState()
    monitor = DriftMonitor(reference, EAGER, publish=live.set_drift, estimator=lambda *a: sure(0))
    run_with(store, monitor, live)
    assert not any(t.name == "drift" and t.is_alive() for t in threading.enumerate())


def test_without_a_data_directory_nothing_is_checked(store: Store) -> None:
    live = LiveState()
    source = ScriptedSource(car_crossing(30))
    service = VisionService(source, ScriptedDetector(source), store, live=live)
    service.run(StopRequest())
    assert live.snapshot()["drift"] == {}
    assert not any(t.name == "drift" for t in threading.enumerate())


def test_with_a_data_directory_the_service_makes_its_own_monitor(
    store: Store, tmp_path: Path
) -> None:
    live = LiveState()
    source = ScriptedSource(car_crossing(30))
    service = VisionService(source, ScriptedDetector(source), store, live=live, data_dir=tmp_path)
    service.run(StopRequest())
    drift = live.snapshot()["drift"]
    assert drift["state"] == "unknown"  # the first look comes after a while; the run was short
    assert "first check" in drift["message"]
    assert not any(t.name == "drift" and t.is_alive() for t in threading.enumerate())


def test_a_service_that_cannot_open_its_camera_still_closes_the_monitor(
    store: Store, reference: Path
) -> None:
    live = LiveState()
    monitor = DriftMonitor(reference, EAGER, publish=live.set_drift, estimator=lambda *a: sure(0))

    class NoCamera(ScriptedSource):
        def start(self) -> None:
            raise RuntimeError("no camera")

    source = NoCamera(car_crossing(5))
    service = VisionService(source, ScriptedDetector(source), store, live=live, drift=monitor)
    stop = StopRequest()
    stop.request()  # so that the retry loop gives up at once
    service.run(stop)
    assert not any(t.name == "drift" and t.is_alive() for t in threading.enumerate())
