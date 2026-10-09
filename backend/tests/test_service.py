import json
import threading
import time
from collections import deque

import pytest

from tests.fakes import ScriptedDetector, ScriptedSource, car_crossing
from trafficpi.core.clock import WallClock
from trafficpi.core.types import Detection
from trafficpi.db import create_db_engine, upgrade_to_head
from trafficpi.db.store import Store
from trafficpi.vision.service import CameraUnavailable, LiveState, ServiceConfig, VisionService
from trafficpi.vision.shutdown import StopRequest

LINE_PROFILE = {"lines": [{"id": "main", "name": "Main", "a": [0.2, 0.5], "b": [0.8, 0.5]}]}


@pytest.fixture
def store() -> Store:
    engine = create_db_engine(":memory:")
    upgrade_to_head(engine)
    s = Store(engine)
    s.ensure_camera()
    return s


def service_for(script, store: Store, **kw) -> VisionService:  # type: ignore[no-untyped-def]
    source = kw.pop("source", None) or ScriptedSource(script)
    return VisionService(source, ScriptedDetector(source), store, **kw)


def test_a_crossing_is_stored_and_shown(store: Store) -> None:
    store.set_profile(LINE_PROFILE)
    service = service_for(car_crossing(), store)
    service.run(StopRequest())
    assert service.frames == 100 and service.events_recorded == 1 and service.profile_version == 1
    (event,) = store.recent_events()
    assert (event.line_id, event.direction, event.class_label) == ("main", 1, "car")
    (row,) = store.minute_counts(0, 2**40)
    assert row.count == 1
    state = service.live.snapshot()
    assert state["frame"] == 99 and state["camera"] == {"ok": True, "error": None}
    assert state["lines"] == [{"id": "main", "name": "Main", "a": [0.2, 0.5], "b": [0.8, 0.5]}]
    assert [e["direction"] for e in state["events"]] == [1] and state["events"][0]["class"] == "car"
    json.dumps(state)  # the web server sends this as JSON


def test_without_a_profile_vehicles_are_tracked_but_nothing_is_counted(store: Store) -> None:
    service = service_for(car_crossing(), store)
    service.run(StopRequest())
    assert service.events_recorded == 0 and store.recent_events() == []
    assert service.live.snapshot()["tracks"] and service.profile_version == 0


def test_the_live_view_lists_the_current_tracks(store: Store) -> None:
    service = service_for(car_crossing(40), store)
    service.run(StopRequest())
    (track,) = service.live.snapshot()["tracks"]
    assert track["label"] == "car" and track["confirmed"] and len(track["box"]) == 4
    assert track["velocity"][1] > 0  # moving down the frame


def test_a_reloaded_profile_takes_effect_without_restarting(store: Store) -> None:
    holder: list[VisionService] = []

    def at_frame(i: int) -> None:
        if i == 20:  # the owner saves a profile while the service is running
            store.set_profile(LINE_PROFILE)
            holder[0].request_reload()

    source = ScriptedSource(car_crossing(), on_read=at_frame)
    service = service_for(None, store, source=source)
    holder.append(service)
    service.run(StopRequest())
    assert service.profile_version == 1 and service.events_recorded == 1


def test_an_invalid_profile_is_ignored_not_fatal(store: Store) -> None:
    store.set_profile({"lines": [{"id": "bad", "a": [2, 2], "b": [0, 0]}]})
    service = service_for(car_crossing(30), store)
    service.run(StopRequest())
    assert service.frames == 30 and service.events_recorded == 0


def two_cars_in_turn(frames: int = 160) -> list[list[Detection]]:
    """One car crosses the line at about frame 50, another (other lane) at about frame 110."""
    script: list[list[Detection]] = [[] for _ in range(frames)]
    for i, detections in enumerate(car_crossing(100)):
        script[i] += detections
    for i, detections in enumerate(car_crossing(100, x=0.7)):
        script[60 + i] += detections
    return script


def test_a_database_failure_neither_stops_the_loop_nor_loses_the_count(
    store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    store.set_profile(LINE_PROFILE)
    real = store.record_count
    calls = {"n": 0}

    def flaky(*args, **kwargs):  # type: ignore[no-untyped-def]
        calls["n"] += 1
        if calls["n"] == 1:  # the first write, for the first car, fails
            raise RuntimeError("database is locked")
        return real(*args, **kwargs)

    monkeypatch.setattr(store, "record_count", flaky)
    service = service_for(two_cars_in_turn(), store)
    service.run(StopRequest())
    assert service.frames == 160  # the loop kept going
    assert service.events_recorded == 2  # the second event flushed the first one as well
    events = store.recent_events()
    assert [e.track_id for e in reversed(events)] == [1, 2]  # in the order they happened
    assert service._pending == deque()


def test_stop_requests_and_frame_limits_end_the_loop(store: Store) -> None:
    stop = StopRequest()
    source = ScriptedSource(car_crossing(), on_read=lambda i: stop.request() if i == 10 else None)
    service = service_for(None, store, source=source)
    service.run(stop)
    assert service.frames == 11 or service.frames == 10
    limited = service_for(car_crossing(), store)
    limited.run(StopRequest(), max_frames=7)
    assert limited.frames == 7


def test_replays_can_stamp_events_with_the_current_time(store: Store) -> None:
    store.set_profile(LINE_PROFILE)
    service = service_for(car_crossing(), store, timestamp_ms=lambda frame: 1_700_000_000_000)
    service.run(StopRequest())
    (event,) = store.recent_events()
    assert event.occurred_at == 1_700_000_000_000


def test_events_are_stamped_from_the_sensor_clock_by_default(store: Store) -> None:
    store.set_profile(LINE_PROFILE)
    clock = WallClock(wall_ns=lambda: 2_000_000_000_000_000_000, monotonic_ns=lambda: 0)
    service = service_for(car_crossing(), store, clock=clock)
    service.run(StopRequest())
    (event,) = store.recent_events()
    assert (
        2_000_000_000_000 + 1_500 < event.occurred_at < 2_000_000_000_000 + 2_200
    )  # about 1.7 s in


def test_live_state_waiting_for_frames() -> None:
    live = LiveState()
    assert live.latest_frame() == (0, None)
    assert live.wait_for_frame(0, timeout=0.05) == (0, None)  # nothing arrives: times out
    source = ScriptedSource(car_crossing(3))
    frame = source.read()
    got: list[int] = []
    waiter = threading.Thread(target=lambda: got.append(live.wait_for_frame(0, timeout=2)[0]))
    waiter.start()
    live.publish_frame(frame, [], 1, 30.0)
    waiter.join(timeout=3)
    assert got == [1]
    live.set_camera_error("camera lost")
    assert live.snapshot()["camera"] == {"ok": False, "error": "camera lost"}


# --- a camera that misbehaves ----------------------------------------------------------------

FAST_RETRY = ServiceConfig(camera_retry_s=0.01)


class FlakySource(ScriptedSource):
    """A scripted source that fails to start ``start_failures`` times and to read at some frames."""

    def __init__(self, script, *, start_failures=0, fail_reads_at=(), on_start=None):  # type: ignore[no-untyped-def]
        super().__init__(script)
        self.starts = self.stops = 0
        self.start_failures = start_failures
        self.fail_reads_at = set(fail_reads_at)
        self.on_start = on_start

    def start(self) -> None:
        self.starts += 1
        if self.on_start is not None:
            self.on_start()
        if self.starts <= self.start_failures:
            raise RuntimeError("camera busy")

    def stop(self) -> None:
        self.stops += 1

    def read(self):  # type: ignore[no-untyped-def]
        if self.index in self.fail_reads_at:
            self.fail_reads_at.discard(self.index)
            raise RuntimeError("camera unplugged")
        return super().read()


def test_the_service_starts_and_stops_its_source(store: Store) -> None:
    source = FlakySource(car_crossing(10))
    service_for(None, store, source=source).run(StopRequest())
    assert (source.starts, source.stops) == (1, 1)


def test_a_camera_that_will_not_start_is_reported_and_retried(store: Store) -> None:
    seen: list[dict[str, object]] = []
    holder: list[VisionService] = []
    source = FlakySource(
        car_crossing(10),
        start_failures=3,
        on_start=lambda: holder and seen.append(holder[0].live.snapshot()["camera"]),
    )
    service = service_for(None, store, source=source, config=FAST_RETRY)
    holder.append(service)
    service.run(StopRequest())
    assert source.starts == 4 and service.frames == 10
    assert seen[1:] == [{"ok": False, "error": "cannot start the camera: camera busy"}] * 3
    assert service.live.snapshot()["camera"] == {"ok": True, "error": None}


def test_a_camera_that_fails_while_running_is_restarted_and_counting_goes_on(
    store: Store,
) -> None:
    store.set_profile(LINE_PROFILE)
    source = FlakySource(car_crossing(), fail_reads_at={30})
    service = service_for(None, store, source=source, config=FAST_RETRY)
    service.run(StopRequest())
    assert source.starts == 2 and source.stops == 2  # restarted once; stopped for good at the end
    assert service.frames == 100 and service.events_recorded == 1


def test_the_outage_shows_in_the_live_state_until_frames_return(store: Store) -> None:
    holder: list[VisionService] = []
    during: list[object] = []
    source = FlakySource(
        car_crossing(60),
        fail_reads_at={20},
        on_start=lambda: holder and during.append(holder[0].live.snapshot()["camera"]),
    )
    service = service_for(None, store, source=source, config=FAST_RETRY)
    holder.append(service)
    service.run(StopRequest())
    # The first entry is the normal start; the second is the restart, made during the outage.
    assert during[1:] == [{"ok": False, "error": "camera failed: camera unplugged"}]
    assert service.live.snapshot()["camera"]["ok"]


def test_a_stop_request_cuts_short_the_wait_for_a_broken_camera(store: Store) -> None:
    source = FlakySource(car_crossing(10), start_failures=10**9)
    service = service_for(None, store, source=source, config=ServiceConfig(camera_retry_s=60))
    stop = StopRequest()
    threading.Timer(0.2, stop.request).start()
    started = time.monotonic()
    service.run(stop)
    assert time.monotonic() - started < 5 and service.frames == 0
    assert service.live.snapshot()["camera"]["error"] == "cannot start the camera: camera busy"


def test_a_camera_that_stops_delivering_is_reported_by_the_live_state_itself() -> None:
    """The loop is stuck waiting for the camera then, so it cannot say anything itself."""
    live = LiveState(stale_after_s=0.05)
    live.publish_frame(ScriptedSource(car_crossing(3)).read(), [], 1, 30.0)
    assert live.snapshot()["camera"]["ok"] and live.snapshot()["fps"] == 30.0
    time.sleep(0.15)
    state = live.snapshot()
    assert state["camera"]["ok"] is False and "no frames for" in state["camera"]["error"]
    assert state["fps"] == 0.0


def test_the_service_gives_up_on_a_camera_that_never_comes_back(store: Store) -> None:
    """So that systemd restarts the whole process, which starts libcamera afresh."""
    source = FlakySource(car_crossing(10), start_failures=10**9)
    config = ServiceConfig(camera_retry_s=0.01, camera_giveup_s=0.15)
    service = service_for(None, store, source=source, config=config)
    started = time.monotonic()
    with pytest.raises(CameraUnavailable, match=r"no camera for .*\(camera busy\)"):
        service.run(StopRequest())
    assert 0.1 < time.monotonic() - started < 5
    assert source.stops == source.starts  # every failed attempt was cleaned up, and once more


def test_the_give_up_clock_restarts_whenever_frames_flow_again(store: Store) -> None:
    # Two outages of about 0.2 s each, with working frames between them. Each is shorter than
    # the 0.3 s limit, though together they are longer.
    class Outage(FlakySource):
        def __init__(self) -> None:
            super().__init__(car_crossing(60), fail_reads_at={10, 30})
            self.down_until = 0.0

        def start(self) -> None:
            if self.down_until and time.monotonic() < self.down_until:
                raise RuntimeError("still down")
            super().start()

        def read(self):  # type: ignore[no-untyped-def]
            if self.index in self.fail_reads_at:
                self.down_until = time.monotonic() + 0.2
            return super().read()

    config = ServiceConfig(camera_retry_s=0.02, camera_giveup_s=0.3)
    service = service_for(None, store, source=Outage(), config=config)
    service.run(StopRequest())
    assert service.frames == 60


def test_the_frame_rate_is_not_inflated_by_uneven_arrival_times(store: Store) -> None:
    """Frames arriving 20 and 46 ms apart, in turn, are 30 a second; 1/gap averaged would say 36."""
    gaps = iter(0.020 if i % 2 == 0 else 0.046 for i in range(1000))
    clock = [0.0]

    def perf_counter() -> float:
        clock[0] += next(gaps)
        return clock[0]

    source = ScriptedSource(car_crossing(300))
    service = VisionService(source, ScriptedDetector(source), store, perf_counter=perf_counter)
    service.run(StopRequest())
    assert service.live.snapshot()["fps"] == pytest.approx(30.3, abs=1.5)
