"""The owner can switch off looking for people altogether."""

import logging

import pytest

from tests.fakes import ScriptedDetector, ScriptedSource, car_crossing
from trafficpi.core.trail_settings import TrailSettings
from trafficpi.core.types import Detection
from trafficpi.db import create_db_engine, upgrade_to_head
from trafficpi.db.store import Store
from trafficpi.vision.service import VisionService
from trafficpi.vision.shutdown import StopRequest


@pytest.fixture
def store() -> Store:
    engine = create_db_engine(":memory:")
    upgrade_to_head(engine)
    s = Store(engine)
    s.ensure_camera()
    s.set_trail_settings(TrailSettings(people=True))  # people's paths are saved when they exist
    return s


def walker(frames: int = 100) -> list[list[Detection]]:
    """A person walking across the picture."""
    return [
        [Detection(0, "person", 0.9, 0.18 + 0.006 * i, 0.4, 0.22 + 0.006 * i, 0.5)]
        for i in range(frames)
    ]


def car_and_walker(frames: int = 100) -> list[list[Detection]]:
    return [
        cars + people for cars, people in zip(car_crossing(frames), walker(frames), strict=True)
    ]


def run(
    script: list[list[Detection]], store: Store, on_read=None
) -> tuple[VisionService, list[set[str]]]:  # type: ignore[no-untyped-def]
    """Play a script; also returns which kinds were being tracked as each frame went by."""
    seen: list[set[str]] = []
    holder: list[VisionService] = []

    def watch(index: int) -> None:
        if holder:
            seen.append({t["label"] for t in holder[0].live.snapshot()["tracks"]})
        if on_read is not None:
            on_read(index, holder[0] if holder else None)

    source = ScriptedSource(script, on_read=watch)
    service = VisionService(source, ScriptedDetector(source), store)
    holder.append(service)
    service.run(StopRequest())
    return service, seen


def labels_saved(store: Store) -> set[str]:
    return {row.class_label for row in store.trails_between(0, 2**50)}


def test_by_default_people_are_looked_for_tracked_and_saved(store: Store) -> None:
    """The control: without the switch, a walker is tracked and gets a trail."""
    service, seen = run(walker(), store)
    assert "person" in set().union(*seen)
    assert labels_saved(store) == {"person"}
    assert service.trails_recorded == 1


def test_with_people_switched_off_a_walker_is_never_tracked_or_saved(store: Store) -> None:
    store.set_setting("detect_people", False)
    service, seen = run(walker(), store)
    assert set().union(*seen) == set()
    assert labels_saved(store) == set()
    assert service.trails_recorded == 0
    assert service.frames == 100  # the loop still ran: the walker was only left out


def test_vehicles_in_the_same_picture_are_still_followed(store: Store) -> None:
    store.set_setting("detect_people", False)
    _, seen = run(car_and_walker(), store)
    assert set().union(*seen) == {"car"}
    assert labels_saved(store) == {"car"}


def test_the_switch_is_read_again_on_a_reload_so_no_restart_is_needed(store: Store) -> None:
    store.set_setting("detect_people", False)

    def turn_on_half_way(index: int, service: VisionService | None) -> None:
        if index == 50 and service is not None:
            store.set_setting("detect_people", True)
            service.request_reload()

    _, seen = run(walker(), store, on_read=turn_on_half_way)
    assert set().union(*seen[:50]) == set()  # not a person while it was off
    assert "person" in set().union(*seen[50:])  # and one once it was on again


def test_switching_off_in_the_middle_ends_the_tracking_of_people_at_once(store: Store) -> None:
    def turn_off_half_way(index: int, service: VisionService | None) -> None:
        if index == 50 and service is not None:
            store.set_setting("detect_people", False)
            service.request_reload()

    _, seen = run(walker(), store, on_read=turn_off_half_way)
    assert "person" in set().union(*seen[:50])
    # The tracker keeps a track a few frames after its object was last seen; then it is gone.
    assert set().union(*seen[70:]) == set()


def test_the_log_says_when_people_stop_being_looked_for(
    store: Store, caplog: pytest.LogCaptureFixture
) -> None:
    store.set_setting("detect_people", False)
    with caplog.at_level(logging.INFO, logger="trafficpi.vision"):
        run(walker(5), store)
    assert "people are not looked for" in caplog.text


@pytest.mark.parametrize("odd", ["no", 0, ""])
def test_a_value_that_is_not_a_switch_leaves_people_looked_for(store: Store, odd: object) -> None:
    """Only a plain `false` turns it off: a damaged or odd value must not silently blind the camera
    to anything."""
    store.set_setting("detect_people", odd)
    _, seen = run(walker(), store)
    assert "person" in set().union(*seen)
