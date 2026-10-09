import threading
from pathlib import Path

import pytest
from sqlalchemy.exc import IntegrityError

from trafficpi.core.clock import WallClock
from trafficpi.db import create_db_engine, upgrade_to_head
from trafficpi.db.store import Store
from trafficpi.vision.rules import CountEvent, StopEvent


def minute(n: int) -> int:
    """Start of the n-th minute after a fixed, minute-aligned instant, in ms."""
    return 1_760_000_040_000 + n * 60_000  # 1_760_000_040 s = 29_333_334 minutes exactly


def count(line: str = "main", direction: int = 1, label: str = "car", track: int = 1) -> CountEvent:
    return CountEvent(0, track, 2, label, line, direction)


@pytest.fixture
def store() -> Store:
    engine = create_db_engine(":memory:")
    upgrade_to_head(engine)
    s = Store(engine)
    s.ensure_camera("Front", now_ms=minute(0))
    return s


def test_recording_a_count_stores_an_event_and_a_minute_total(store: Store) -> None:
    event_id = store.record_count(count(), minute(0) + 5_000, details={"speed": None})
    (event,) = store.recent_events()
    assert event.id == event_id and event.type == "count"
    assert (event.line_id, event.direction, event.class_label, event.track_id) == (
        "main",
        1,
        "car",
        1,
    )
    assert event.occurred_at == minute(0) + 5_000 and event.details == {"speed": None}
    (row,) = store.minute_counts(0, 2**40)
    assert row.bucket_start == minute(0) // 1000 and row.count == 1


def test_a_stop_is_an_event_with_its_outcome_and_changes_no_count(store: Store) -> None:
    stop = StopEvent(0, 7, 2, "car", "elm", "rolling", hold_s=0.4, min_speed=0.31, observed_s=4.2)
    event_id = store.record_stop(stop, minute(0) + 3_000, evidence_path="evidence/a.jpg")
    (event,) = store.recent_events()
    assert event.id == event_id and event.type == "stop"
    assert (event.line_id, event.direction, event.class_label, event.track_id) == (
        "elm",
        None,
        "car",
        7,
    )
    assert event.details == {
        "outcome": "rolling",
        "hold_s": 0.4,
        "min_speed": 0.31,
        "observed_s": 4.2,
    }
    assert event.evidence_path == "evidence/a.jpg" and event.occurred_at == minute(0) + 3_000
    assert store.minute_counts(0, 2**40) == []
    store.record_count(count(), minute(0) + 4_000)
    assert [e.type for e in store.recent_events()] == ["count", "stop"]
    assert [e.type for e in store.recent_events(type="stop")] == ["stop"]


def test_counts_in_the_same_minute_add_up_and_others_stay_separate(store: Store) -> None:
    for i in range(3):
        store.record_count(count(track=i), minute(0) + i * 1000)
    store.record_count(count(label="truck"), minute(0) + 10_000)
    store.record_count(count(direction=-1), minute(0) + 11_000)
    store.record_count(count(), minute(1) + 1_000)
    rows = {
        (r.bucket_start, r.line_id, r.direction, r.class_label): r.count
        for r in store.minute_counts(0, 2**40)
    }
    b0, b1 = minute(0) // 1000, minute(1) // 1000
    assert rows == {
        (b0, "main", 1, "car"): 3,
        (b0, "main", 1, "truck"): 1,
        (b0, "main", -1, "car"): 1,
        (b1, "main", 1, "car"): 1,
    }


def test_minute_counts_range_is_half_open(store: Store) -> None:
    for n in range(4):
        store.record_count(count(), minute(n) + 1)
    got = store.minute_counts(minute(1) // 1000, minute(3) // 1000)
    assert [r.bucket_start for r in got] == [minute(1) // 1000, minute(2) // 1000]


def test_recent_events_are_newest_first_and_paged(store: Store) -> None:
    ids = [store.record_count(count(track=i), minute(0) + i) for i in range(10)]
    first = store.recent_events(limit=4)
    assert [e.id for e in first] == ids[::-1][:4]
    second = store.recent_events(limit=4, before_id=first[-1].id)
    assert [e.id for e in second] == ids[::-1][4:8]
    assert [e.id for e in store.recent_events(since_ms=minute(0) + 8)] == ids[::-1][:2]
    assert store.recent_events(type="stop") == []


def test_settings_round_trip_and_overwrite(store: Store) -> None:
    assert store.get_setting("units", "mph") == "mph"
    store.set_setting("units", "kmh")
    store.set_setting("thresholds", {"stop": [1, 2]})
    store.set_setting("units", "mph")
    assert store.get_setting("units") == "mph" and store.get_setting("thresholds") == {
        "stop": [1, 2]
    }


def test_the_profile_is_versioned(store: Store) -> None:
    assert store.get_profile() is None
    assert store.set_profile({"lines": []}, now_ms=1) == 1
    assert store.set_profile({"lines": [{"id": "a"}]}, now_ms=2) == 2
    assert store.get_profile() == ({"lines": [{"id": "a"}]}, 2)


def test_users(store: Store) -> None:
    assert store.user_count() == 0 and store.get_user("admin") is None
    uid = store.create_user("admin", "hash-1", now_ms=1)
    user = store.get_user("admin")
    assert user is not None and (user.id, user.password_hash) == (uid, "hash-1")
    assert store.set_password_hash("admin", "hash-2")
    updated = store.get_user("admin")
    assert updated is not None and updated.password_hash == "hash-2"
    assert not store.set_password_hash("nobody", "x")
    with pytest.raises(IntegrityError):
        store.create_user("admin", "other")


def test_ensure_camera_is_idempotent(store: Store) -> None:
    store.ensure_camera("Renamed")
    store.record_count(count(), minute(0))  # the foreign key to the camera still holds
    assert len(store.recent_events()) == 1


def test_the_vision_writer_and_the_api_reader_can_share_a_file(tmp_path: Path) -> None:
    """Two processes (here two engines and threads) use one WAL database at the same time."""
    path = tmp_path / "shared.db"
    writer_engine = create_db_engine(path)
    upgrade_to_head(writer_engine)
    writer, reader = Store(writer_engine), Store(create_db_engine(path))
    writer.ensure_camera()
    errors: list[BaseException] = []
    stop = threading.Event()

    def read_loop() -> None:
        try:
            while not stop.is_set():
                reader.recent_events(limit=10)
                reader.minute_counts(0, 2**40)
                reader.set_setting("seen", True)
        except BaseException as exc:
            errors.append(exc)

    thread = threading.Thread(target=read_loop)
    thread.start()
    try:
        for i in range(300):
            writer.record_count(count(track=i), minute(i // 20) + i)
    finally:
        stop.set()
        thread.join()
    assert errors == []
    assert len(writer.recent_events(limit=1000)) == 300


def test_wall_clock_maps_monotonic_to_utc_and_follows_clock_steps() -> None:
    wall, mono = [1_000_000_000_000_000_000], [5_000_000_000]  # ns
    clock = WallClock(refresh_s=60, wall_ns=lambda: wall[0], monotonic_ns=lambda: mono[0])
    assert clock.epoch_ms(mono[0] + 2_000_000_000) == (wall[0] + 2_000_000_000) // 1_000_000
    wall[0] += 3_000_000_000  # NTP steps the wall clock forward by 3 s...
    mono[0] += 61_000_000_000  # ...and a minute passes, so the offset is re-measured
    assert clock.epoch_ms(mono[0]) == wall[0] // 1_000_000
