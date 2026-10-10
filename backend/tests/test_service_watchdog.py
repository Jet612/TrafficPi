"""The vision service tells the watchdog whether its loop is moving (plan.md D27)."""

import threading
import time

import pytest

from tests.fakes import ScriptedDetector, ScriptedSource, car_crossing
from trafficpi.db import create_db_engine, upgrade_to_head
from trafficpi.db.store import Store
from trafficpi.vision.service import CameraUnavailable, ServiceConfig, VisionService
from trafficpi.vision.shutdown import StopRequest

STALL = 0.3  # how long without a move counts as stuck, in these tests


@pytest.fixture
def store() -> Store:
    engine = create_db_engine(":memory:")
    upgrade_to_head(engine)
    s = Store(engine)
    s.ensure_camera()
    return s


class StickySource(ScriptedSource):
    """A camera that hangs where it is told to: in start, or in the read of one frame."""

    def __init__(self, script, *, hang_in_start=False, hang_at_frame=None, hang_in_stop=False):  # type: ignore[no-untyped-def]
        super().__init__(script)
        self.release = threading.Event()
        self.hang_in_start = hang_in_start
        self.hang_at_frame = hang_at_frame
        self.hang_in_stop = hang_in_stop
        self.stops = 0  # how many times stop() was called
        self.hanging = threading.Event()  # set while it is stuck

    def start(self) -> None:
        if self.hang_in_start:
            self._hang()

    def read(self):  # type: ignore[no-untyped-def]
        if self.index == self.hang_at_frame:
            self._hang()
            self.hang_at_frame = None
        return super().read()

    def _hang(self) -> None:
        self.hanging.set()
        self.release.wait(30)
        self.hanging.clear()

    def stop(self) -> None:
        self.stops += 1
        if self.hang_in_stop:
            self._hang()


class StickyDetector(ScriptedDetector):
    def __init__(self, source, hang_at_frame: int) -> None:  # type: ignore[no-untyped-def]
        super().__init__(source)
        self.release = threading.Event()
        self.hanging = threading.Event()
        self.hang_at_frame = hang_at_frame

    def detect(self, image):  # type: ignore[no-untyped-def]
        if self.source.index == self.hang_at_frame:
            self.hanging.set()
            self.release.wait(30)
            self.hanging.clear()
        return super().detect(image)


def run_in_thread(service: VisionService, stop: StopRequest) -> threading.Thread:
    thread = threading.Thread(target=service.run, args=(stop,), daemon=True)
    thread.start()
    return thread


def wait_until(condition, timeout: float = 5.0) -> bool:  # type: ignore[no-untyped-def]
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.01)
    return False


def test_a_service_that_has_not_started_yet_is_alive(store: Store) -> None:
    source = ScriptedSource(car_crossing(5))
    assert VisionService(source, ScriptedDetector(source), store).alive()


def test_every_frame_is_a_move(store: Store) -> None:
    source = ScriptedSource(car_crossing(40))
    service = VisionService(
        source, ScriptedDetector(source), store, config=ServiceConfig(stall_after_s=STALL)
    )
    service.run(StopRequest())
    assert service.frames == 40 and service.alive()


def test_a_read_that_never_returns_stops_the_beat_and_a_return_starts_it_again(
    store: Store,
) -> None:
    source = StickySource(car_crossing(60), hang_at_frame=10)
    service = VisionService(
        source, ScriptedDetector(source), store, config=ServiceConfig(stall_after_s=STALL)
    )
    stop = StopRequest()
    thread = run_in_thread(service, stop)
    assert source.hanging.wait(5)
    assert wait_until(lambda: not service.alive(), timeout=3)  # stuck for longer than STALL
    source.release.set()  # the camera answers after all
    thread.join(10)
    assert service.frames == 60 and service.alive()


def test_a_camera_that_hangs_while_starting_stops_the_beat(store: Store) -> None:
    source = StickySource(car_crossing(20), hang_in_start=True)
    service = VisionService(
        source, ScriptedDetector(source), store, config=ServiceConfig(stall_after_s=STALL)
    )
    thread = run_in_thread(service, StopRequest())
    assert source.hanging.wait(5)
    assert wait_until(lambda: not service.alive(), timeout=3)
    source.release.set()
    thread.join(10)


def test_a_detector_that_hangs_stops_the_beat(store: Store) -> None:
    source = ScriptedSource(car_crossing(40))
    detector = StickyDetector(source, hang_at_frame=8)
    service = VisionService(source, detector, store, config=ServiceConfig(stall_after_s=STALL))
    thread = run_in_thread(service, StopRequest())
    assert detector.hanging.wait(5)
    assert wait_until(lambda: not service.alive(), timeout=3)
    detector.release.set()
    thread.join(10)
    assert service.frames == 40


def test_waiting_for_a_camera_that_is_broken_is_not_a_hang(store: Store) -> None:
    """A camera that will not start is the service's own business (it retries, and gives up after
    five minutes so that systemd restarts it); the watchdog must not pre-empt that."""

    class BrokenSource(ScriptedSource):
        def start(self) -> None:
            raise RuntimeError("camera busy")

        def stop(self) -> None: ...

    source = BrokenSource(car_crossing(5))
    config = ServiceConfig(stall_after_s=STALL, camera_retry_s=0.05, camera_giveup_s=60)
    service = VisionService(source, ScriptedDetector(source), store, config=config)
    stop = StopRequest()
    thread = run_in_thread(service, stop)
    deadline = time.monotonic() + 1.5  # five times the stall limit
    while time.monotonic() < deadline:
        assert service.alive()
        time.sleep(0.02)
    stop.request()
    thread.join(5)


def test_the_status_line_says_what_the_service_is_doing(store: Store) -> None:
    source = ScriptedSource(car_crossing(30))
    service = VisionService(source, ScriptedDetector(source), store)
    service.live.set_detector("yolov8s_h8l")
    assert "camera" in service.status_line() and "waiting" in service.status_line()
    service.run(StopRequest())
    line = service.status_line()
    assert line.startswith("counting at ") and "fps" in line and "yolov8s_h8l" in line
    service.live.set_camera_error("cannot start the camera: busy")
    assert service.status_line() == "camera offline: cannot start the camera: busy"


def test_a_camera_that_never_stops_is_given_up_on_after_the_limit_and_what_is_in_hand_is_saved(
    store: Store,
) -> None:
    """On 2026-10-09 Picamera2's stop() hung for good and the watchdog restarted the service a
    minute later. The service now waits for a stop only as long as it is told to, says so with
    CameraUnavailable (the way out that makes the process restart), does not try to stop the camera
    a second time, and has saved the trail of the car that was being followed first."""
    source = StickySource(car_crossing(60), hang_in_stop=True)
    config = ServiceConfig(camera_stop_timeout_s=0.3, stall_after_s=30.0)
    service = VisionService(source, ScriptedDetector(source), store, config=config)
    started = time.monotonic()
    try:
        with pytest.raises(CameraUnavailable, match=r"did not stop within 0\.3 s"):
            service.run(StopRequest())
        assert time.monotonic() - started < 3.0, "it did not wait for the camera for ever"
        assert source.stops == 1, "a stop that hung is not tried again"
        assert store.trail_count() == 1, "what was in hand was saved before the exit"
    finally:
        source.release.set()  # the stuck thread is let go


def test_a_camera_that_stops_slowly_but_within_the_limit_is_waited_for(store: Store) -> None:
    source = StickySource(car_crossing(20), hang_in_stop=True)
    threading.Timer(0.1, source.release.set).start()  # it comes back after a tenth of a second
    config = ServiceConfig(camera_stop_timeout_s=5.0)
    service = VisionService(source, ScriptedDetector(source), store, config=config)
    service.run(StopRequest())  # no exception
    assert service.frames == 20 and source.stops == 1
