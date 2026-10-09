import threading
from pathlib import Path

import pytest
from sqlalchemy.exc import IntegrityError

from trafficpi.core.clock import WallClock
from trafficpi.core.evidence_settings import EvidenceSettings
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


def stop(
    outcome: str = "rolling", sign: str = "elm", label: str = "car", track: int = 7
) -> StopEvent:
    return StopEvent(0, track, 2, label, sign, outcome, hold_s=0.4, min_speed=0.3, observed_s=4.0)  # type: ignore[arg-type]


def test_stops_are_found_by_time_outcome_review_and_sign_newest_first_and_in_pages(
    store: Store,
) -> None:
    store.record_stop(stop("stopped"), minute(0) + 1_000)
    store.record_stop(stop("rolling"), minute(0) + 2_000)
    store.record_stop(stop("did_not_stop", sign="oak"), minute(0) + 3_000)
    store.record_stop(stop("indeterminate"), minute(1))
    store.record_count(count(), minute(0) + 4_000)  # a count is not a stop
    everything = (minute(0), minute(2))
    found = store.stops_between(*everything)
    assert [(e.details or {})["outcome"] for e in found] == [
        "indeterminate",
        "did_not_stop",
        "rolling",
        "stopped",
    ]
    assert [e.type for e in found] == ["stop"] * 4
    # a range is closed at its start and open at its end
    assert len(store.stops_between(minute(0) + 2_000, minute(0) + 3_000)) == 1
    assert [(e.details or {})["outcome"] for e in store.stops_between(minute(0), minute(1))] == [
        "did_not_stop",
        "rolling",
        "stopped",
    ]
    needing_a_look = store.stops_between(*everything, outcomes=["rolling", "did_not_stop"])
    assert [(e.details or {})["outcome"] for e in needing_a_look] == ["did_not_stop", "rolling"]
    assert store.stops_between(*everything, outcomes=[]) == []
    assert [e.line_id for e in store.stops_between(*everything, sign="oak")] == ["oak"]
    # pages: the last id seen, then the ones before it
    first = store.stops_between(*everything, limit=2)
    second = store.stops_between(*everything, limit=2, before_id=first[-1].id)
    assert [e.id for e in first + second] == sorted([e.id for e in first + second], reverse=True)
    assert len(first) == len(second) == 2


def test_a_stop_is_reviewed_and_the_review_taken_back(store: Store) -> None:
    event_id = store.record_stop(stop(), minute(0) + 1_000)
    (before,) = store.stops_between(minute(0), minute(1))
    assert (before.review, before.reviewed_at) == (None, None)
    confirmed = store.review_stop(event_id, "confirmed", now_ms=minute(5))
    assert confirmed is not None and (confirmed.review, confirmed.reviewed_at) == (
        "confirmed",
        minute(5),
    )
    (after,) = store.stops_between(minute(0), minute(1))
    assert after.review == "confirmed"
    marked = store.review_stop(event_id, "false_positive", now_ms=minute(6))
    assert marked is not None and (marked.review, marked.reviewed_at) == (
        "false_positive",
        minute(6),
    )
    undone = store.review_stop(event_id, None)
    assert undone is not None and (undone.review, undone.reviewed_at) == (None, None)
    assert store.stops_between(minute(0), minute(1), review="unreviewed")


def test_reviews_are_filtered_and_only_known_ones_are_taken(store: Store) -> None:
    ids = [store.record_stop(stop(), minute(0) + i * 1_000) for i in range(3)]
    store.review_stop(ids[0], "confirmed")
    store.review_stop(ids[1], "false_positive")
    mine = (minute(0), minute(1))
    assert [e.id for e in store.stops_between(*mine, review="confirmed")] == [ids[0]]
    assert [e.id for e in store.stops_between(*mine, review="false_positive")] == [ids[1]]
    assert [e.id for e in store.stops_between(*mine, review="unreviewed")] == [ids[2]]
    assert len(store.stops_between(*mine)) == 3
    with pytest.raises(ValueError, match="confirmed"):
        store.review_stop(ids[2], "maybe")
    assert store.stops_between(*mine, review="unreviewed")[0].id == ids[2]  # and nothing changed


def test_only_a_stop_event_of_this_camera_can_be_reviewed(store: Store) -> None:
    counted = store.record_count(count(), minute(0))
    assert store.review_stop(counted, "confirmed") is None  # a count has nothing to review
    assert store.review_stop(9_999, "confirmed") is None
    other = Store(store._engine, camera_id="side")
    other.ensure_camera("Side", now_ms=minute(0))
    theirs = other.record_stop(stop(), minute(0))
    assert store.review_stop(theirs, "confirmed") is None
    assert store.stops_between(minute(0), minute(1)) == []  # and theirs are not ours to list


def test_evidence_is_noted_on_a_stop_and_taken_off_again(store: Store) -> None:
    event_id = store.record_stop(stop("rolling"), minute(0))
    assert store.attach_evidence(event_id, "evidence/d/stop-1.jpg", "evidence/d/stop-1.mp4")
    event = store.stop_event(event_id)
    assert event is not None and event.evidence_path == "evidence/d/stop-1.jpg"
    assert (event.details or {})["clip"] == "evidence/d/stop-1.mp4"
    assert (event.details or {})["outcome"] == "rolling"  # what was there is still there
    assert store.attach_evidence(event_id, "evidence/d/stop-1.jpg", None)  # the clip is gone
    event = store.stop_event(event_id)
    assert event is not None and "clip" not in (event.details or {})
    assert (event.details or {})["outcome"] == "rolling" and event.evidence_path is not None
    assert store.attach_evidence(event_id, None, None)
    event = store.stop_event(event_id)
    assert event is not None and event.evidence_path is None


def test_evidence_can_only_be_noted_on_a_stop_of_this_camera(store: Store) -> None:
    counted = store.record_count(count(), minute(0))
    assert store.attach_evidence(counted, "evidence/x.jpg", None) is False
    assert store.recent_events(type="count")[0].evidence_path is None
    assert store.attach_evidence(9_999, "evidence/x.jpg", None) is False
    other = Store(store._engine, camera_id="side")
    other.ensure_camera("Side", now_ms=minute(0))
    theirs = other.record_stop(stop(), minute(0))
    assert store.attach_evidence(theirs, "evidence/x.jpg", None) is False
    assert other.stop_event(theirs) is not None and other.stop_event(theirs).evidence_path is None  # type: ignore[union-attr]


def test_one_stop_is_found_by_its_id_only_if_it_is_a_stop_of_this_camera(store: Store) -> None:
    mine = store.record_stop(stop("did_not_stop"), minute(0))
    counted = store.record_count(count(), minute(0))
    other = Store(store._engine, camera_id="side")
    other.ensure_camera("Side", now_ms=minute(0))
    theirs = other.record_stop(stop(), minute(0))
    found = store.stop_event(mine)
    assert found is not None and found.id == mine and found.type == "stop"
    assert (found.details or {})["outcome"] == "did_not_stop"
    assert store.stop_event(counted) is None  # a count is not a stop
    assert store.stop_event(theirs) is None  # nor is another camera's
    assert store.stop_event(99_999) is None


def test_the_summary_counts_each_outcome_by_review_and_leaves_out_what_is_outside_the_range(
    store: Store,
) -> None:
    a = store.record_stop(stop("rolling"), minute(0) + 1_000)
    b = store.record_stop(stop("rolling"), minute(0) + 2_000)
    store.record_stop(stop("rolling"), minute(0) + 3_000)
    store.record_stop(stop("stopped"), minute(0) + 4_000)
    store.record_stop(stop("stopped"), minute(5))  # outside the range
    store.review_stop(a, "confirmed")
    store.review_stop(b, "false_positive")
    summary = store.stop_summary(minute(0), minute(1))
    assert list(summary) == ["stopped", "rolling", "did_not_stop", "indeterminate"]
    assert summary["rolling"] == {"total": 3, "confirmed": 1, "false_positive": 1, "unreviewed": 1}
    assert summary["stopped"] == {"total": 1, "confirmed": 0, "false_positive": 0, "unreviewed": 1}
    assert summary["did_not_stop"]["total"] == summary["indeterminate"]["total"] == 0


def test_only_stops_are_summed_even_when_a_count_carries_an_outcome_of_its_own(
    store: Store,
) -> None:
    store.record_count(count(), minute(0) + 1_000, details={"outcome": "rolling"})
    store.record_stop(stop("rolling"), minute(0) + 2_000)
    assert store.stop_summary(minute(0), minute(1))["rolling"]["total"] == 1
    assert len(store.stops_between(minute(0), minute(1))) == 1


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


def test_evidence_settings_start_as_the_defaults_and_round_trip(store: Store) -> None:
    assert store.get_evidence_settings() == EvidenceSettings(True, 30, 2048)
    store.set_evidence_settings(EvidenceSettings(enabled=False, keep_days=7, max_mb=500))
    assert store.get_evidence_settings() == EvidenceSettings(False, 7, 500)


def test_an_evidence_setting_that_is_stored_but_makes_no_sense_is_ignored(store: Store) -> None:
    for key, value in [
        ("evidence_keep_days", 0),
        ("evidence_max_mb", True),
        ("evidence_enabled", "yes"),
    ]:
        store.set_setting(key, value)
    # not a number of days, not a size, and not a plain "no": the defaults, and still on
    assert store.get_evidence_settings() == EvidenceSettings(True, 30, 2048)
    store.set_setting("evidence_keep_days", "30")
    store.set_setting("evidence_max_mb", 10**9)
    assert store.get_evidence_settings() == EvidenceSettings(True, 30, 2048)


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
