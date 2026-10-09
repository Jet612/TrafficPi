import json
from pathlib import Path

import pytest

from tests.fakes import (
    STOP_SIGN,
    ScriptedDetector,
    ScriptedSource,
    car_crossing,
    write_crossing_log,
)
from trafficpi.core.coco import class_name
from trafficpi.core.geometry import Polygon
from trafficpi.core.types import Detection
from trafficpi.db import create_db_engine, upgrade_to_head
from trafficpi.db.store import Store
from trafficpi.vision import cli
from trafficpi.vision.engine import TrafficEngine
from trafficpi.vision.profile import parse_compiled_profile
from trafficpi.vision.replay import replay_log
from trafficpi.vision.rules import CountEvent, CountingRule
from trafficpi.vision.service import VisionService
from trafficpi.vision.shutdown import StopRequest
from trafficpi.vision.zones import DetectionFilter

LINE = {"id": "main", "name": "Main", "a": [0.2, 0.5], "b": [0.8, 0.5]}
FRAME_NS = 33_333_333


def box(x: float, ground_y: float, *, w: float = 0.08, h: float = 0.07) -> Detection:
    return Detection(2, class_name(2), 0.9, x - w / 2, ground_y - h, x + w / 2, ground_y)


def square(x0: float, y0: float, x1: float, y1: float) -> Polygon:
    return Polygon(((x0, y0), (x1, y0), (x1, y1), (x0, y1)))


def zone(zone_id: str, x0: float, y0: float, x1: float, y1: float) -> dict[str, object]:
    return {"id": zone_id, "points": [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]}


# --- the filter ---------------------------------------------------------------------------------


def test_without_zones_everything_is_accepted() -> None:
    assert DetectionFilter().accepts(box(0.5, 0.5)) and DetectionFilter().accepts(box(0.01, 0.01))


def test_a_detection_zone_keeps_only_what_is_inside_it() -> None:
    road = DetectionFilter(include=(square(0.0, 0.4, 1.0, 0.8),))
    assert road.accepts(box(0.5, 0.6))
    assert not road.accepts(box(0.5, 0.9))  # on the pavement below
    assert not road.accepts(box(0.5, 0.3))  # in the sky above


def test_detection_zones_add_up() -> None:
    two_streets = DetectionFilter(include=(square(0.0, 0.4, 0.4, 0.8), square(0.6, 0.4, 1.0, 0.8)))
    assert two_streets.accepts(box(0.2, 0.6)) and two_streets.accepts(box(0.8, 0.6))
    assert not two_streets.accepts(box(0.5, 0.6))


def test_an_exclusion_zone_drops_what_is_inside_it() -> None:
    parking = DetectionFilter(exclude=(square(0.0, 0.5, 0.3, 0.9),))
    assert not parking.accepts(box(0.15, 0.7))
    assert parking.accepts(box(0.5, 0.7))


def test_exclusion_wins_over_detection() -> None:
    both = DetectionFilter(
        include=(square(0.0, 0.0, 1.0, 1.0),), exclude=(square(0.4, 0.4, 0.6, 0.6),)
    )
    assert both.accepts(box(0.2, 0.5)) and not both.accepts(box(0.5, 0.5))


def test_the_point_that_counts_is_where_the_object_meets_the_road() -> None:
    zone_ = DetectionFilter(include=(square(0.0, 0.5, 1.0, 1.0),))
    tall = box(0.5, 0.45, h=0.3)  # most of the box is in the zone... but its bottom is not
    assert tall.center[1] > 0.3 and not zone_.accepts(tall)
    assert zone_.accepts(box(0.5, 0.55, h=0.3))  # the bottom is inside, the top is not
    assert zone_.accepts(box(0.5, 0.5))  # the boundary counts as inside


def test_apply_keeps_order_and_leaves_the_input_alone() -> None:
    parking = DetectionFilter(exclude=(square(0.0, 0.0, 0.3, 1.0),))
    cars = [box(0.1, 0.5), box(0.5, 0.5), box(0.2, 0.2), box(0.9, 0.9)]
    assert parking.apply(cars) == [cars[1], cars[3]] and len(cars) == 4


# --- compiling a profile ------------------------------------------------------------------------


def test_a_profile_compiles_to_counting_lines_and_a_filter() -> None:
    compiled = parse_compiled_profile(
        {
            "lines": [
                LINE
                | {"classes": ["car", "bus"], "point": "center", "margin": 0.01, "persistence": 3},
                LINE | {"id": "plain"},
            ],
            "detection_zones": [zone("road", 0, 0.4, 1, 0.9)],
            "exclusion_zones": [zone("parking", 0, 0.6, 0.2, 0.9)],
        }
    )
    tuned, plain = compiled.counting_lines
    assert (tuned.id, tuned.name, tuned.point) == ("main", "Main", "center")
    assert tuned.classes == frozenset({2, 5}) and tuned.margin == 0.01 and tuned.persistence == 3
    assert plain.classes is None and plain.point == "ground" and plain.persistence == 2  # defaults
    assert compiled.detection_filter is not None
    assert len(compiled.detection_filter.include) == len(compiled.detection_filter.exclude) == 1


def test_a_stop_sign_compiles_to_the_rules_sign_with_the_pictures_shape() -> None:
    document = {
        "stop_signs": [
            STOP_SIGN
            | {"classes": ["car", "bus"], "hold_s": 2, "roll_speed": 1.2, "direction": -1},
            STOP_SIGN | {"id": "plain"},
        ]
    }
    tuned, plain = parse_compiled_profile(document, aspect=4 / 3).stop_signs
    assert (tuned.id, tuned.name, tuned.direction, tuned.aspect) == ("elm", "Elm Street", -1, 4 / 3)
    assert tuned.classes == frozenset({2, 5}) and tuned.hold_s == 2 and tuned.roll_speed == 1.2
    assert tuned.line.a == (0.5, 0.9) and tuned.line.b == (0.5, 0.3)
    assert tuned.approach.contains((0.3, 0.7)) and not tuned.approach.contains((0.7, 0.7))
    assert plain.classes == frozenset({2, 7, 5, 3})  # cars, trucks, buses and motorcycles
    assert (plain.hold_s, plain.still_radius, plain.roll_speed, plain.min_observed_s) == (
        1.0,
        0.08,
        0.7,
        0.8,
    )
    assert plain.direction == 1 and plain.aspect == 4 / 3
    assert parse_compiled_profile(document).stop_signs[0].aspect == 16 / 9  # shape not given


def test_a_profile_without_zones_has_no_filter() -> None:
    assert parse_compiled_profile({"lines": [LINE]}).detection_filter is None
    assert parse_compiled_profile({}).counting_lines == []


# --- in the engine -------------------------------------------------------------------------------


def count(engine: TrafficEngine, frames: list[list[Detection]]) -> tuple[int, int]:
    """The number of count events, and of frames that had a track."""
    events = tracked = 0
    for i, detections in enumerate(frames):
        result = engine.update(i * FRAME_NS, detections)
        events += sum(isinstance(e, CountEvent) for e in result.events)
        tracked += bool(result.tracks)
    return events, tracked


def engine_for(**profile: object) -> TrafficEngine:
    compiled = parse_compiled_profile({"lines": [LINE], **profile})
    return TrafficEngine(
        rules=[CountingRule(compiled.counting_lines)], detection_filter=compiled.detection_filter
    )


DRIVE = [[box(0.5, 0.2 + 0.006 * i)] for i in range(100)]  # crosses y = 0.5 around frame 50


def test_the_car_is_counted_with_no_zones() -> None:
    assert count(engine_for(), DRIVE)[0] == 1


def test_a_detection_zone_around_the_road_changes_nothing() -> None:
    assert count(engine_for(detection_zones=[zone("road", 0.3, 0.1, 0.7, 0.9)]), DRIVE)[0] == 1


def test_a_detection_zone_that_misses_the_road_counts_nothing() -> None:
    events, tracked = count(engine_for(detection_zones=[zone("far", 0.7, 0.1, 0.9, 0.9)]), DRIVE)
    assert (events, tracked) == (0, 0)


def test_an_exclusion_zone_over_the_road_stops_the_counting_and_the_tracking() -> None:
    events, tracked = count(engine_for(exclusion_zones=[zone("all", 0.3, 0.1, 0.7, 0.9)]), DRIVE)
    assert (events, tracked) == (0, 0)


def test_an_exclusion_zone_elsewhere_changes_nothing() -> None:
    assert count(engine_for(exclusion_zones=[zone("lot", 0.7, 0.1, 0.95, 0.9)]), DRIVE)[0] == 1


def confirmed_tracks(engine: TrafficEngine, frames: list[list[Detection]]) -> set[int]:
    seen: set[int] = set()
    for i, detections in enumerate(frames):
        seen |= {t.id for t in engine.update(i * FRAME_NS, detections).tracks if t.confirmed}
    return seen


def test_a_parked_car_in_an_exclusion_zone_is_ignored_while_traffic_is_counted() -> None:
    frames = [[*cars, box(0.12, 0.7)] for cars in DRIVE]  # the same drive past a parked car
    lot = zone("lot", 0.0, 0.55, 0.25, 0.85)
    assert len(confirmed_tracks(engine_for(), frames)) == 2  # the driver and the parked car
    assert len(confirmed_tracks(engine_for(exclusion_zones=[lot]), frames)) == 1
    assert count(engine_for(exclusion_zones=[lot]), frames)[0] == 1  # the driver still counts


def test_zones_can_be_swapped_while_the_engine_runs() -> None:
    engine = engine_for()
    for i, detections in enumerate(DRIVE[:30]):
        engine.update(i * FRAME_NS, detections)
    assert engine.update(30 * FRAME_NS, DRIVE[30]).tracks  # tracked
    engine.detection_filter = DetectionFilter(exclude=(square(0.3, 0.1, 0.7, 0.9),))
    assert engine.update(31 * FRAME_NS, DRIVE[31]).tracks == []  # now ignored
    engine.detection_filter = None
    assert engine.update(32 * FRAME_NS, DRIVE[32]).tracks  # and noticed again


# --- replay and the command line ------------------------------------------------------------------


def test_replay_honours_the_filter(tmp_path: Path) -> None:
    log = write_crossing_log(tmp_path / "d.jsonl")
    compiled = parse_compiled_profile(
        {"lines": [LINE], "exclusion_zones": [zone("x", 0.3, 0.1, 0.7, 0.9)]}
    )
    assert replay_log(log, compiled.counting_lines).events  # counted without the filter
    ignored = replay_log(log, compiled.counting_lines, detection_filter=compiled.detection_filter)
    assert ignored.events == [] and ignored.confirmed_tracks == set()


def test_the_replay_command_reads_zones_from_a_profile(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    log = write_crossing_log(tmp_path / "d.jsonl")
    path = tmp_path / "profile.json"
    path.write_text(
        json.dumps({"lines": [LINE], "exclusion_zones": [zone("x", 0.3, 0.1, 0.7, 0.9)]})
    )
    assert cli.main(["replay", str(log), "--profile", str(path)]) == 0
    assert "0 confirmed tracks" in capsys.readouterr().out
    # the older spelling still works
    path.write_text(json.dumps({"lines": [LINE]}))
    assert cli.main(["replay", str(log), "--lines", str(path)]) == 0
    assert "1 confirmed tracks" in capsys.readouterr().out


def test_a_bad_profile_file_is_a_message_not_a_traceback(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    log = write_crossing_log(tmp_path / "d.jsonl")
    path = tmp_path / "profile.json"
    path.write_text(json.dumps({"lines": [LINE | {"a": [3, 3]}]}))
    assert cli.main(["replay", str(log), "--profile", str(path)]) == 1
    assert r"lines[0].a: must be inside the picture" in capsys.readouterr().err


# --- in the service ----------------------------------------------------------------------------


@pytest.fixture
def store() -> Store:
    engine = create_db_engine(":memory:")
    upgrade_to_head(engine)
    s = Store(engine)
    s.ensure_camera()
    return s


def run_service(store: Store, frames: int = 100, **kw):  # type: ignore[no-untyped-def]
    source = kw.pop("source", None) or ScriptedSource(car_crossing(frames))
    service = VisionService(source, ScriptedDetector(source), store, **kw)
    service.run(StopRequest())
    return service


def test_the_service_obeys_the_zones_and_shows_them_to_viewers(store: Store) -> None:
    store.set_profile(
        {
            "lines": [LINE],
            "detection_zones": [zone("road", 0.3, 0.1, 0.7, 0.9)],
            "exclusion_zones": [zone("lot", 0.0, 0.5, 0.2, 0.9) | {"name": "Parking"}],
        }
    )
    service = run_service(store)
    assert service.events_recorded == 1  # the car drives through the detection zone
    state = service.live.snapshot()
    kinds = [(z["id"], z["kind"], z["name"]) for z in state["zones"]]
    assert kinds == [("road", "detection", ""), ("lot", "exclusion", "Parking")]
    assert state["zones"][0]["points"][0] == [0.3, 0.1] and state["profile_error"] is None
    json.dumps(state)


def test_the_service_shows_its_stop_signs_to_viewers_and_tells_a_reload(store: Store) -> None:
    sign = STOP_SIGN | {"direction": -1}
    store.set_profile({"stop_signs": [sign]})
    service = run_service(store, frames=3)
    state = service.live.snapshot()
    assert state["stop_signs"] == [
        {
            "id": "elm",
            "name": "Elm Street",
            "approach": [[0.1, 0.5], [0.5, 0.5], [0.5, 0.95], [0.1, 0.95]],
            "a": [0.5, 0.9],
            "b": [0.5, 0.3],
            "direction": -1,
        }
    ]
    json.dumps(state)
    # with none in the profile there are none to show
    store.set_profile({"lines": [LINE]})
    assert run_service(store, frames=3).live.snapshot()["stop_signs"] == []


def test_a_car_in_an_exclusion_zone_is_not_counted_by_the_service(store: Store) -> None:
    store.set_profile({"lines": [LINE], "exclusion_zones": [zone("road", 0.3, 0.1, 0.7, 0.9)]})
    service = run_service(store)
    assert service.events_recorded == 0 and service.live.snapshot()["tracks"] == []


def test_a_profile_that_cannot_be_used_at_start_is_reported(store: Store) -> None:
    store.set_profile({"lines": [LINE | {"a": [2, 2]}]})
    service = run_service(store, 30)
    assert service.frames == 30 and service.events_recorded == 0 and service.profile_version == 0
    problem = service.live.snapshot()["profile_error"]
    assert "version 1" in problem and "lines[0].a" in problem


def test_a_bad_profile_saved_while_running_leaves_the_working_one_in_use(store: Store) -> None:
    store.set_profile({"lines": [LINE]})
    holder: list[VisionService] = []

    def at_frame(i: int) -> None:
        if i == 20:  # someone damages the saved profile while the service counts
            store.set_profile({"lines": [LINE | {"a": [2, 2]}]})
            holder[0].request_reload()

    source = ScriptedSource(car_crossing(), on_read=at_frame)
    service = VisionService(source, ScriptedDetector(source), store)
    holder.append(service)
    service.run(StopRequest())
    assert service.events_recorded == 1 and service.profile_version == 1  # still counting
    assert "lines[0].a" in service.live.snapshot()["profile_error"]


def test_saving_a_good_profile_clears_the_complaint(store: Store) -> None:
    store.set_profile({"lines": [LINE | {"a": [2, 2]}]})
    holder: list[VisionService] = []

    def at_frame(i: int) -> None:
        if i == 10:  # the owner corrects the profile
            store.set_profile({"lines": [LINE]})
            holder[0].request_reload()

    source = ScriptedSource(car_crossing(60), on_read=at_frame)
    service = VisionService(source, ScriptedDetector(source), store)
    holder.append(service)
    service.run(StopRequest())
    assert service.live.snapshot()["profile_error"] is None and service.profile_version == 2
