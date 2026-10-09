import json
import threading
from collections import deque

import pytest

from tests.fakes import ScriptedDetector, ScriptedSource, car_crossing
from trafficpi.core.clock import WallClock
from trafficpi.core.types import Detection
from trafficpi.db import create_db_engine, upgrade_to_head
from trafficpi.db.store import Store
from trafficpi.vision.service import LiveState, VisionService
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
