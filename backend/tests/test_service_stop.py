import logging

import pytest

from tests.fakes import (
    FULL_STOP,
    ROLLING_STOP,
    STOP_SIGN,
    STRAIGHT_THROUGH,
    ScriptedDetector,
    ScriptedSource,
    car_driving,
)
from trafficpi.core.types import Detection
from trafficpi.db import create_db_engine, upgrade_to_head
from trafficpi.db.store import Store
from trafficpi.vision.service import VisionService
from trafficpi.vision.shutdown import StopRequest

LINE = {
    "id": "main",
    "name": "Main",
    "a": [0.7, 0.9],
    "b": [0.7, 0.3],
}  # a counting line, past the sign


@pytest.fixture
def store() -> Store:
    engine = create_db_engine(":memory:")
    upgrade_to_head(engine)
    s = Store(engine)
    s.ensure_camera()
    return s


def run(script: list[list[Detection]], store: Store, **kw) -> VisionService:  # type: ignore[no-untyped-def]
    source = kw.pop("source", None) or ScriptedSource(script)
    source.frame_size = (640, 360)  # a 16:9 picture, which the sign's distances need
    service = VisionService(source, ScriptedDetector(source), store, **kw)
    service.run(StopRequest())
    return service


def stops(store: Store) -> list[tuple[str, str, str | None]]:
    """(the sign, the outcome, the kind) of every stop event stored, oldest first."""
    rows = store.recent_events(type="stop", limit=100)
    return [(r.line_id or "", (r.details or {})["outcome"], r.class_label) for r in reversed(rows)]


@pytest.mark.parametrize(
    ("drive", "outcome"),
    [
        (FULL_STOP, "stopped"),
        (ROLLING_STOP, "rolling"),
        (STRAIGHT_THROUGH, "did_not_stop"),
    ],
)
def test_what_a_car_does_at_a_stop_sign_is_stored_as_an_event(
    store: Store, drive: list[tuple[float, float, float]], outcome: str
) -> None:
    store.set_profile({"stop_signs": [STOP_SIGN]})
    service = run(car_driving(drive), store, config=None)
    assert stops(store) == [("elm", outcome, "car")]
    (row,) = store.recent_events(type="stop")
    assert row.type == "stop" and row.direction is None and row.track_id is not None
    assert set(row.details or {}) == {"outcome", "hold_s", "min_speed", "observed_s", "max_speed"}
    assert service.events_recorded == 1
    assert store.minute_counts(0, 2**40) == []  # a stop is not a count


def test_the_numbers_behind_the_outcome_are_kept(store: Store) -> None:
    store.set_profile({"stop_signs": [STOP_SIGN]})
    run(car_driving(FULL_STOP), store)
    (row,) = store.recent_events(type="stop")
    details = row.details or {}
    assert details["hold_s"] >= 1.5 and details["min_speed"] < 0.1 and details["observed_s"] > 3
    assert details["max_speed"] > 1.0  # it arrived at about 1.5 sizes a second, then stopped


def test_stop_signs_and_counting_lines_work_side_by_side(store: Store) -> None:
    store.set_profile({"lines": [LINE], "stop_signs": [STOP_SIGN]})
    service = run(car_driving([*FULL_STOP, (1.0, 0.15, 0.15)]), store)
    assert stops(store) == [("elm", "stopped", "car")]
    (count,) = store.minute_counts(0, 2**40)
    assert (count.line_id, count.direction, count.count) == ("main", 1, 1)
    assert service.events_recorded == 2


def test_a_car_going_the_other_way_is_not_judged(store: Store) -> None:
    store.set_profile({"stop_signs": [STOP_SIGN]})
    script = list(reversed(car_driving(FULL_STOP, x0=0.12)))  # from the right of the picture
    run(script, store)
    assert stops(store) == []


def test_no_stop_sign_no_stop_events(store: Store) -> None:
    run(car_driving(FULL_STOP), store)
    assert stops(store) == []


def test_a_stop_sign_added_while_running_takes_effect_when_the_service_is_told(
    store: Store,
) -> None:
    holder: list[VisionService] = []

    def at_frame(i: int) -> None:
        if i == 10:  # the owner draws the stop sign, and the API tells the service
            store.set_profile({"stop_signs": [STOP_SIGN]})
            holder[0].request_reload()

    source = ScriptedSource(car_driving(FULL_STOP), on_read=at_frame)
    source.frame_size = (640, 360)
    service = VisionService(source, ScriptedDetector(source), store)
    holder.append(service)
    service.run(StopRequest())
    # it was followed from the frame where the sign appeared (0.4 s into a 6 s drive): that is
    # still long before the car got to the stop
    assert stops(store) == [("elm", "stopped", "car")]


def test_a_database_that_fails_for_a_while_loses_no_stop(
    store: Store, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    store.set_profile({"stop_signs": [STOP_SIGN]})
    real, failures = store.record_stop, []

    def flaky(event, at_ms, **kw):  # type: ignore[no-untyped-def]
        if not failures:
            failures.append(1)
            raise OSError("database is locked")
        return real(event, at_ms, **kw)

    monkeypatch.setattr(store, "record_stop", flaky)
    script = car_driving(FULL_STOP) + [[] for _ in range(30)]  # empty frames: the retry happens
    with caplog.at_level(logging.ERROR, logger="trafficpi.vision"):
        service = run(script, store)
    # the next event is what triggers the retry; with none, it is kept waiting for the next one
    assert failures == [1]
    assert any("could not store an event" in r.getMessage() for r in caplog.records)
    assert len(service._pending) == 1


def test_a_pass_is_logged_in_words(store: Store, caplog: pytest.LogCaptureFixture) -> None:
    store.set_profile({"stop_signs": [STOP_SIGN]})
    with caplog.at_level(logging.INFO, logger="trafficpi.vision"):
        run(car_driving(ROLLING_STOP), store)
    messages = [r.getMessage() for r in caplog.records]
    assert any(m.startswith("car at elm: rolling (still ") for m in messages)
    assert any("1 stop sign(s)" in m for m in messages)


def test_the_picture_that_is_wider_changes_what_a_distance_means(store: Store) -> None:
    """The sign measures in the picture's own proportions, which the source reports."""
    store.set_profile({"stop_signs": [STOP_SIGN]})
    source = ScriptedSource(car_driving(FULL_STOP))
    service = VisionService(source, ScriptedDetector(source), store)
    source.frame_size = (1280, 720)
    assert service._aspect() == pytest.approx(16 / 9)
    source.frame_size = (0, 0)
    assert service._aspect() == pytest.approx(16 / 9)
    source.frame_size = (1000, 1000)
    assert service._aspect() == 1.0
