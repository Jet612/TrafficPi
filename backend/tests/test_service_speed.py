"""The vision service with a calibrated camera: speeds on counts and stops, and what is shown live
(plan.md M6)."""

import json
from typing import Any

import pytest

from tests.camera_sim import PinholeCamera
from tests.fakes import ScriptedDetector, ScriptedSource
from trafficpi.core.coco import class_name
from trafficpi.core.ground import calibration_id
from trafficpi.core.types import Detection
from trafficpi.db import create_db_engine, upgrade_to_head
from trafficpi.db.store import Store
from trafficpi.vision.service import VisionService
from trafficpi.vision.shutdown import StopRequest

CAMERA = PinholeCamera()
ROAD = (-4.0, 4.0, 8.0, 30.0)


def picture(x: float, y: float) -> list[float]:
    seen = CAMERA.project(x, y)
    assert seen is not None
    return [seen[0], seen[1]]


def profile(**changes: Any) -> dict[str, Any]:
    x, y, width, length = ROAD
    return {
        "lines": [
            {"id": "across", "name": "Across", "a": picture(-4.0, 18.0), "b": picture(4.0, 18.0)}
        ],
        "stop_signs": [
            {
                "id": "elm",
                "name": "Elm",
                "approach": [
                    picture(-4.0, 8.0),
                    picture(4.0, 8.0),
                    picture(4.0, 24.0),
                    picture(-4.0, 24.0),
                ],
                "a": picture(-4.0, 24.0),
                "b": picture(4.0, 24.0),
                "direction": -1,
            }
        ],
        "calibration": {
            "corners": [
                picture(x, y),
                picture(x + width, y),
                picture(x + width, y + length),
                picture(x, y + length),
            ],
            "width_m": width,
            "length_m": length,
        },
        **changes,
    }


def drive(
    speed_ms: float, seconds: float, y0: float = -4.0, x: float = 1.75
) -> list[list[Detection]]:
    """Per-frame detections of a car driving up the road at a steady speed."""
    frames = []
    for i in range(round(seconds * 30)):
        box = CAMERA.box_of(x, y0 + speed_ms * i / 30, 0.0)
        ok = box is not None and box[0] > 0 and box[2] < 1 and box[1] > 0 and box[3] < 1
        frames.append([Detection(2, class_name(2), 0.9, *box)] if ok and box else [])  # type: ignore[misc]
    return frames


@pytest.fixture
def store() -> Store:
    engine = create_db_engine(":memory:")
    upgrade_to_head(engine)
    s = Store(engine)
    s.ensure_camera()
    return s


def run(script: list[list[Detection]], store: Store) -> VisionService:
    source = ScriptedSource(script)
    service = VisionService(source, ScriptedDetector(source), store)
    service.run(StopRequest())
    return service


# --- stored with the calibration they were measured under -----------------------------------------


def test_a_count_made_by_a_calibrated_camera_keeps_the_speed_and_the_calibration(
    store: Store,
) -> None:
    store.set_profile(profile())
    service = run(drive(8.0, 8.0), store)
    counts = [e for e in store.recent_events() if e.type == "count"]
    (count,) = counts
    assert count.details is not None
    assert count.details["speed_ms"] == pytest.approx(8.0, rel=0.07)
    stored = service.live.snapshot()["calibration"]
    assert stored is not None
    _, _, width, length = ROAD
    assert count.details["cal"] == calibration_id(
        [tuple(c) for c in stored["corners"]], width, length
    )


def test_a_stop_pass_keeps_the_speeds_it_was_measured_at(store: Store) -> None:
    store.set_profile(profile())
    run(drive(9.0, 8.0), store)
    (stop,) = [e for e in store.recent_events() if e.type == "stop"]
    assert stop.details is not None
    assert stop.details["outcome"] == "did_not_stop"
    for key in ("min_speed_ms", "max_speed_ms", "line_speed_ms"):
        assert stop.details[key] == pytest.approx(9.0, rel=0.08), key
    (row,) = store.stop_rows(0, 2**42)
    assert row.max_speed_ms == pytest.approx(9.0, rel=0.08)


def test_without_a_calibration_everything_is_stored_as_it_always_was(store: Store) -> None:
    uncalibrated = profile(calibration=None)
    store.set_profile(uncalibrated)
    service = run(drive(8.0, 8.0), store)
    events = store.recent_events()
    assert {e.type for e in events} == {"count", "stop"}
    for event in events:
        assert "speed_ms" not in (event.details or {}) and "cal" not in (event.details or {})
        assert "min_speed_ms" not in (event.details or {})
    assert service.live.snapshot()["calibration"] is None


def test_the_outcome_of_a_pass_is_the_same_with_and_without_a_calibration(store: Store) -> None:
    store.set_profile(profile())
    run(drive(9.0, 8.0), store)
    calibrated = [e.details["outcome"] for e in store.recent_events() if e.type == "stop"]  # type: ignore[index]
    other = Store(create_db_engine(":memory:"))
    upgrade_to_head(other._engine)
    other.ensure_camera()
    other.set_profile(profile(calibration=None))
    run(drive(9.0, 8.0), other)
    plain = [e.details["outcome"] for e in other.recent_events() if e.type == "stop"]  # type: ignore[index]
    assert calibrated == plain != []


# --- shown live -----------------------------------------------------------------------------------


def test_the_live_state_carries_the_calibration_and_each_tracks_speed(store: Store) -> None:
    store.set_profile(profile())
    source = ScriptedSource(drive(8.0, 5.0))
    service = VisionService(source, ScriptedDetector(source), store)
    service.run(StopRequest(), max_frames=110)
    state = service.live.snapshot()
    json.dumps(state)  # the web server sends it as JSON
    assert state["calibration"]["width_m"] == 8.0 and state["calibration"]["length_m"] == 30.0
    assert len(state["calibration"]["corners"]) == 4
    (track,) = state["tracks"]
    assert track["speed_ms"] == pytest.approx(8.0, rel=0.05)


def test_a_track_that_has_no_believable_speed_says_nothing_of_one(store: Store) -> None:
    store.set_profile(profile())
    source = ScriptedSource(drive(8.0, 5.0))
    service = VisionService(source, ScriptedDetector(source), store)
    service.run(StopRequest(), max_frames=8)  # a quarter of a second: too soon
    (track,) = service.live.snapshot()["tracks"]
    assert "speed_ms" not in track


# --- the meter follows the profile ----------------------------------------------------------------


def test_the_meter_is_kept_while_the_calibration_does_not_change_and_changes_with_it(
    store: Store,
) -> None:
    store.set_profile(profile())
    source = ScriptedSource([[]] * 5)
    service = VisionService(source, ScriptedDetector(source), store)
    service.load_profile()
    meter = service._engine.speed_meter
    assert meter is not None
    service.load_profile()  # saved again, nothing changed in the calibration
    assert service._engine.speed_meter is meter
    store.set_profile(profile(lines=[]))  # other things changed
    service.load_profile()
    assert service._engine.speed_meter is meter
    changed = profile()
    changed["calibration"]["length_m"] = 31.0
    store.set_profile(changed)
    service.load_profile()
    assert service._engine.speed_meter is not meter and service._engine.speed_meter is not None
    store.set_profile(profile(calibration=None))
    service.load_profile()
    assert service._engine.speed_meter is None


def test_a_profile_that_cannot_be_read_leaves_the_meter_as_it_was(store: Store) -> None:
    store.set_profile(profile())
    source = ScriptedSource([[]] * 5)
    service = VisionService(source, ScriptedDetector(source), store)
    service.load_profile()
    meter = service._engine.speed_meter
    store.set_profile({"calibration": {"corners": [[0, 0]], "width_m": 1, "length_m": 1}})
    service.load_profile()
    assert service._engine.speed_meter is meter
