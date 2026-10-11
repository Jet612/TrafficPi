import json
import math
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

from trafficpi.core.profile import (
    SCHEMA_VERSION,
    Profile,
    ProfileError,
    empty_profile,
    load_profile_file,
    parse_profile,
)

LINE: dict[str, Any] = {"id": "north", "name": "Northbound", "a": [0.2, 0.5], "b": [0.8, 0.5]}
SQUARE = [[0.2, 0.2], [0.8, 0.2], [0.8, 0.8], [0.2, 0.8]]
ROAD: dict[str, Any] = {"id": "road", "name": "The road", "points": SQUARE}
PARKING: dict[str, Any] = {"id": "parking", "name": "Parked cars", "points": SQUARE}


def test_an_empty_document_is_an_empty_profile() -> None:
    profile = parse_profile({})
    assert profile == empty_profile()
    assert profile.model_dump() == {
        "schema_version": SCHEMA_VERSION,
        "lines": [],
        "detection_zones": [],
        "exclusion_zones": [],
        "stop_signs": [],
        "calibration": None,
    }


def test_a_profile_saved_before_zones_existed_still_loads() -> None:
    old = {"lines": [{"id": "street", "a": [0.1, 0.6], "b": [0.9, 0.6], "classes": ["car"]}]}
    profile = parse_profile(old)
    (line,) = profile.lines
    assert (line.id, line.name, line.classes, line.point) == ("street", "", ["car"], "ground")
    assert (line.margin, line.persistence) == (None, None)
    assert profile.detection_zones == [] and profile.exclusion_zones == []


def test_the_canonical_form_round_trips() -> None:
    document = {
        "schema_version": 1,
        "lines": [
            {
                **LINE,
                "classes": ["car", "truck"],
                "point": "center",
                "margin": 0.01,
                "persistence": 3,
            }
        ],
        "detection_zones": [ROAD],
        "exclusion_zones": [PARKING | {"id": "p2"}],
    }
    profile = parse_profile(document)
    dumped = profile.model_dump(mode="json")
    assert dumped["detection_zones"][0]["points"][0] == [0.2, 0.2]  # JSON has lists, not tuples
    assert json.loads(json.dumps(dumped)) == dumped
    assert parse_profile(dumped) == profile
    assert Profile.model_validate_json(json.dumps(dumped)) == profile


def test_integers_are_numbers_and_repeated_classes_are_merged() -> None:
    profile = parse_profile(
        {"lines": [{**LINE, "a": [0, 0], "b": [1, 1], "classes": ["car", "car"]}]}
    )
    assert profile.lines[0].a == (0.0, 0.0) and profile.lines[0].classes == ["car"]


@pytest.mark.parametrize(
    ("document", "message"),
    [
        # Where a line is wrong, the message says which line and which member.
        ({"lines": [{**LINE, "a": [1.2, 0.5]}]}, r"lines\[0\]\.a: must be inside the picture"),
        ({"lines": [{**LINE, "b": [0.5, -0.1]}]}, r"lines\[0\]\.b: .*got \[0\.5, -0\.1\]"),
        ({"lines": [{**LINE, "a": [0.2]}]}, r"lines\[0\]\.a"),
        ({"lines": [{**LINE, "a": [0.2, 0.5, 0.1]}]}, r"lines\[0\]\.a"),
        ({"lines": [{**LINE, "a": ["0.2", 0.5]}]}, r"lines\[0\]\.a\[0\]: must be a number"),
        ({"lines": [{**LINE, "a": [True, 0.5]}]}, r"lines\[0\]\.a\[0\]: must be a number"),
        ({"lines": [{**LINE, "b": LINE["a"]}]}, r'lines\[0\] \("Northbound"\): .*same place'),
        ({"lines": [{**LINE, "classes": ["spaceship"]}]}, "unknown class 'spaceship'"),
        ({"lines": [{**LINE, "classes": []}]}, r"lines\[0\]\.classes: must name at least one"),
        ({"lines": [{**LINE, "point": "top"}]}, r"lines\[0\]\.point"),
        ({"lines": [{**LINE, "persistence": 0}]}, r"lines\[0\]\.persistence"),
        ({"lines": [{**LINE, "persistence": 2.5}]}, r"lines\[0\]\.persistence"),
        ({"lines": [{**LINE, "margin": 0}]}, r"lines\[0\]\.margin"),
        ({"lines": [{**LINE, "id": ""}]}, r"lines\[0\]\.id"),
        ({"lines": [{**LINE, "name": "x" * 81}]}, r"lines\[0\]\.name"),
        ({"lines": [{**LINE, "clases": ["car"]}]}, r"lines\[0\]\.clases: is not something"),
        ({"lines": [{"id": "x", "b": [0, 0]}]}, r"lines\[0\]\.a: is missing"),
        ({"lines": {"id": "x"}}, r"lines: must be a list"),
        ({"lines": ["north"]}, r"lines\[0\]: must be an object"),
    ],
)
def test_a_bad_line_is_refused_with_its_place_and_reason(
    document: dict[str, Any], message: str
) -> None:
    with pytest.raises(ProfileError, match=message):
        parse_profile(document)


@pytest.mark.parametrize(
    ("zone", "message"),
    [
        ({"points": SQUARE[:2]}, r"detection_zones\[0\]\.points"),
        ({"points": []}, r"detection_zones\[0\]\.points"),
        ({"points": [[0.2, 0.2]] * 3}, "repeats a point"),
        ({"points": [[0.1, 0.1], [0.5, 0.5], [0.9, 0.9]]}, "is flat"),
        ({"points": [[0.2, 0.2], [0.8, 0.8], [0.8, 0.2], [0.2, 0.8]]}, "crosses itself"),
        ({"points": [[0.5, 0.5], [0.5001, 0.5], [0.5, 0.5001]]}, "too small"),
        ({"points": [*SQUARE, [1.5, 0.5]]}, r"detection_zones\[0\]\.points\[4\]: must be inside"),
        ({"points": [[0.1, 0.1]] * 40}, r"detection_zones\[0\]\.points"),
    ],
)
def test_a_bad_zone_is_refused(zone: dict[str, Any], message: str) -> None:
    with pytest.raises(ProfileError, match=message):
        parse_profile({"detection_zones": [{**ROAD, **zone}]})


def test_the_name_of_a_zone_is_part_of_the_complaint() -> None:
    bow_tie = [[0.2, 0.2], [0.8, 0.8], [0.8, 0.2], [0.2, 0.8]]
    with pytest.raises(ProfileError) as caught:
        parse_profile({"exclusion_zones": [{**PARKING, "points": bow_tie}]})
    assert str(caught.value) == (
        'exclusion_zones[0] ("Parked cars"): the outline crosses itself, repeats a point or is flat'
    )


def test_ids_must_be_unique_across_lines_and_zones() -> None:
    with pytest.raises(ProfileError, match="'x' is used twice"):
        parse_profile({"lines": [{**LINE, "id": "x"}], "detection_zones": [{**ROAD, "id": "x"}]})
    with pytest.raises(ProfileError, match="'x' is used twice"):
        parse_profile({"lines": [{**LINE, "id": "x"}, {**LINE, "id": "x"}]})
    with pytest.raises(ProfileError, match="'x' is used twice"):
        parse_profile(
            {"detection_zones": [{**ROAD, "id": "x"}], "exclusion_zones": [{**PARKING, "id": "x"}]}
        )


def test_zone_ids_may_reuse_a_name_and_names_may_repeat() -> None:
    profile = parse_profile(
        {"detection_zones": [ROAD, ROAD | {"id": "road-2"}], "lines": [LINE | {"name": "The road"}]}
    )
    assert len(profile.detection_zones) == 2


def test_a_profile_from_a_newer_version_is_refused_plainly() -> None:
    newer = SCHEMA_VERSION + 1
    with pytest.raises(ProfileError, match=rf"schema version {newer}.*update TrafficPi"):
        parse_profile({"schema_version": newer, "lines": [], "something_new": True})
    with pytest.raises(ProfileError, match=rf"schema_version must be from 1 to {SCHEMA_VERSION}"):
        parse_profile({"schema_version": 0})
    with pytest.raises(ProfileError, match="schema_version"):
        parse_profile({"schema_version": "1"})


@pytest.mark.parametrize("document", [[], "profile", 3, None, [LINE]])
def test_a_document_that_is_not_an_object_is_refused(document: Any) -> None:
    with pytest.raises(ProfileError, match="a profile is a JSON object"):
        parse_profile(document)


def test_too_many_elements_are_refused() -> None:
    lines = [{**LINE, "id": f"l{i}"} for i in range(33)]
    with pytest.raises(ProfileError, match=r"lines: .*at most 32"):
        parse_profile({"lines": lines})


def test_many_problems_are_summarised() -> None:
    lines = [{**LINE, "id": f"l{i}", "a": [2, 2]} for i in range(9)]
    with pytest.raises(ProfileError) as caught:
        parse_profile({"lines": lines})
    message = str(caught.value)
    assert message.count("must be inside the picture") == 5 and message.endswith("and 4 more")


def test_parsing_does_not_change_the_input() -> None:
    document = {"lines": [dict(LINE)], "detection_zones": [deepcopy(ROAD)]}
    before = deepcopy(document)
    parse_profile(document)
    assert document == before


def test_loading_from_a_file(tmp_path: Path) -> None:
    path = tmp_path / "profile.json"
    path.write_text(json.dumps({"lines": [LINE]}))
    assert [line.id for line in load_profile_file(path).lines] == ["north"]
    path.write_text("{not json")
    with pytest.raises(ProfileError, match="not valid JSON"):
        load_profile_file(path)
    with pytest.raises(ProfileError, match="cannot read"):
        load_profile_file(tmp_path / "missing.json")
    path.write_text(json.dumps({"lines": [{**LINE, "a": [9, 9]}]}))
    with pytest.raises(ProfileError, match=r"lines\[0\]\.a"):
        load_profile_file(path)


# --- stop signs ---------------------------------------------------------------------------

STOP: dict[str, Any] = {
    "id": "elm",
    "name": "Elm Street",
    "approach": [[0.1, 0.5], [0.5, 0.5], [0.5, 0.95], [0.1, 0.95]],
    "a": [0.5, 0.9],
    "b": [0.5, 0.3],
}


def test_a_stop_sign_needs_only_its_shapes_and_the_rest_is_filled_in() -> None:
    (sign,) = parse_profile({"stop_signs": [STOP]}).stop_signs
    assert (sign.id, sign.name, sign.direction) == ("elm", "Elm Street", 1)
    assert sign.classes == ["car", "truck", "bus", "motorcycle"]  # not bicycles, not people
    assert (sign.hold_s, sign.still_radius, sign.roll_speed, sign.min_observed_s) == (
        1.0,
        0.08,
        0.7,
        0.8,
    )
    assert sign.polygon().contains((0.3, 0.7))


def test_a_stop_sign_comes_back_whole_in_the_canonical_form() -> None:
    dumped = parse_profile({"stop_signs": [STOP | {"hold_s": 2, "classes": ["car"]}]}).model_dump(
        mode="json"
    )
    assert dumped["stop_signs"] == [
        {
            **STOP,
            "direction": 1,
            "classes": ["car"],
            "hold_s": 2.0,
            "still_radius": 0.08,
            "roll_speed": 0.7,
            "min_observed_s": 0.8,
        }
    ]
    assert parse_profile(dumped).model_dump(mode="json") == dumped  # and is stable


def test_a_profile_of_an_older_version_loads_as_the_newest() -> None:
    old = parse_profile({"schema_version": 1, "lines": [LINE]})
    assert old.schema_version == SCHEMA_VERSION and old.stop_signs == []
    assert [line.id for line in old.lines] == ["north"]


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"a": [0.5, 0.5], "b": [0.5, 0.5]}, r'stop_signs\[0\] \("Elm Street"\): the two ends'),
        ({"approach": [[0, 0], [1, 1], [1, 0], [0, 1]]}, "the outline crosses itself"),
        ({"approach": [[0.1, 0.1], [0.1001, 0.1], [0.1, 0.1001]]}, "too small"),
        ({"approach": [[0.1, 0.1], [0.5, 0.5]]}, "at least 3"),
        ({"approach": [[0.1, 0.1], [0.5, 0.5], [0.2, 1.5]]}, "must be inside the picture"),
        ({"direction": 0}, "direction"),
        ({"direction": 2}, "direction"),
        ({"direction": True}, "direction"),
        ({"direction": "1"}, "direction"),
        ({"classes": None}, "classes"),
        ({"classes": []}, "at least one kind"),
        ({"classes": ["unicorn"]}, "unknown class"),
        ({"hold_s": 0.05}, "hold_s"),
        ({"hold_s": 11}, "hold_s"),
        ({"hold_s": "1"}, "hold_s"),
        ({"hold_s": True}, "hold_s"),
        ({"still_radius": 0}, "still_radius"),
        ({"still_radius": 0.9}, "still_radius"),
        ({"roll_speed": 0}, "roll_speed"),
        ({"roll_speed": 11}, "roll_speed"),
        ({"min_observed_s": 0}, "min_observed_s"),
        ({"min_observed_s": 11}, "min_observed_s"),
        ({"surprise": 1}, "not something TrafficPi knows"),
    ],
)
def test_a_stop_sign_that_makes_no_sense_is_refused_with_the_reason(
    change: dict[str, Any], message: str
) -> None:
    with pytest.raises(ProfileError, match=message):
        parse_profile({"stop_signs": [STOP | change]})


def test_ids_are_unique_across_lines_zones_and_stop_signs() -> None:
    with pytest.raises(ProfileError, match="'north' is used twice"):
        parse_profile({"lines": [LINE], "stop_signs": [STOP | {"id": "north"}]})
    with pytest.raises(ProfileError, match="'road' is used twice"):
        parse_profile({"detection_zones": [ROAD], "stop_signs": [STOP | {"id": "road"}]})
    parse_profile({"stop_signs": [STOP, STOP | {"id": "oak"}]})  # two different ones are fine


def test_there_is_a_limit_to_the_stop_signs_too() -> None:
    signs = [STOP | {"id": f"s{i}"} for i in range(33)]
    with pytest.raises(ProfileError, match="stop_signs"):
        parse_profile({"stop_signs": signs})


# --- the calibration that puts the picture in metres ------------------------------------------

# A rectangle across a road 7 m wide and 30 m long, seen from a camera standing to one side.
CALIBRATION: dict[str, Any] = {
    "corners": [[0.62, 0.42], [0.86, 0.42], [0.58, 0.31], [0.43, 0.31]],
    "width_m": 7.0,
    "length_m": 30.0,
}


def test_a_profile_has_no_calibration_until_one_is_drawn() -> None:
    assert parse_profile({}).calibration is None
    assert parse_profile({"calibration": None}).calibration is None


def test_a_calibration_needs_only_its_corners_and_its_size() -> None:
    profile = parse_profile({"calibration": CALIBRATION})
    assert profile.calibration is not None
    assert profile.calibration.checks == []
    assert profile.calibration.width_m == 7.0 and profile.calibration.length_m == 30.0


def test_a_calibration_comes_back_whole_in_the_canonical_form() -> None:
    check = {"id": "drive", "a": [0.5, 0.4], "b": [0.6, 0.35], "length_m": 12.0}
    profile = parse_profile({"calibration": {**CALIBRATION, "checks": [check]}})
    dumped = profile.model_dump(mode="json")["calibration"]
    assert dumped["checks"] == [{**check, "name": ""}]
    assert parse_profile({"calibration": dumped}) == profile
    assert Profile.model_validate(profile.model_dump(mode="json")) == profile


def test_a_profile_saved_before_the_calibration_existed_loads_as_the_newest() -> None:
    old = parse_profile({"schema_version": 2, "lines": [LINE]})
    assert old.schema_version == SCHEMA_VERSION == 3 and old.calibration is None


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"corners": CALIBRATION["corners"][:3]}, r"calibration\.corners"),
        ({"corners": [*CALIBRATION["corners"], [0.5, 0.5]]}, r"calibration\.corners"),
        (
            {"corners": [[0.62, 0.42], [1.2, 0.42], [0.58, 0.31], [0.43, 0.31]]},
            "inside the picture",
        ),
        ({"width_m": 0.1}, r"calibration\.width_m"),
        ({"width_m": 400.0}, r"calibration\.width_m"),
        ({"length_m": 0.2}, r"calibration\.length_m"),
        ({"length_m": 900.0}, r"calibration\.length_m"),
        ({"width_m": "7"}, "must be a number"),
        ({"width_m": True}, "must be a number"),
        ({"width_m": math.nan}, r"calibration\.width_m"),
        (
            {"corners": [[0.62, 0.42], [0.58, 0.31], [0.86, 0.42], [0.43, 0.31]]},
            "one after the other",
        ),
        ({"corners": [[0.1, 0.1], [0.2, 0.2], [0.3, 0.3], [0.5, 0.2]]}, "one after the other"),
        ({"colour": "red"}, "not something TrafficPi knows"),
    ],
)
def test_a_calibration_that_makes_no_sense_is_refused_with_the_reason(
    change: dict[str, Any], message: str
) -> None:
    with pytest.raises(ProfileError, match=message):
        parse_profile({"calibration": {**CALIBRATION, **change}})


def test_a_check_is_a_known_distance_between_two_points() -> None:
    check = {"id": "drive", "name": "Driveway to the pole", "a": [0.5, 0.4], "b": [0.6, 0.35]}
    profile = parse_profile({"calibration": {**CALIBRATION, "checks": [{**check, "length_m": 12}]}})
    assert profile.calibration is not None and profile.calibration.checks[0].length_m == 12.0


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"b": [0.5, 0.4]}, "same place"),
        ({"length_m": 0.1}, "length_m"),
        ({"length_m": -3.0}, "length_m"),
        ({"a": [0.5, 1.5]}, "inside the picture"),
        ({"length_m": "12"}, "must be a number"),
    ],
)
def test_a_check_that_makes_no_sense_is_refused(change: dict[str, Any], message: str) -> None:
    check = {"id": "drive", "name": "Driveway", "a": [0.5, 0.4], "b": [0.6, 0.35], "length_m": 12.0}
    with pytest.raises(ProfileError, match=message):
        parse_profile({"calibration": {**CALIBRATION, "checks": [{**check, **change}]}})


def test_the_name_of_a_check_is_part_of_the_complaint() -> None:
    check = {"id": "drive", "name": "Driveway", "a": [0.5, 0.4], "b": [0.5, 0.4], "length_m": 12.0}
    with pytest.raises(ProfileError, match=r'checks\[0\] \("Driveway"\)'):
        parse_profile({"calibration": {**CALIBRATION, "checks": [check]}})


def test_a_check_may_not_share_an_id_with_a_line_or_with_another_check() -> None:
    check = {"id": "north", "a": [0.5, 0.4], "b": [0.6, 0.35], "length_m": 12.0}
    with pytest.raises(ProfileError, match="'north' is used twice"):
        parse_profile({"lines": [LINE], "calibration": {**CALIBRATION, "checks": [check]}})
    twin = {**check, "id": "south"}
    with pytest.raises(ProfileError, match="'south' is used twice"):
        parse_profile({"calibration": {**CALIBRATION, "checks": [twin, twin]}})


def test_there_is_a_limit_to_the_checks() -> None:
    checks = [
        {"id": f"c{i}", "a": [0.5, 0.4], "b": [0.6, 0.35], "length_m": 12.0} for i in range(9)
    ]
    with pytest.raises(ProfileError, match=r"calibration\.checks"):
        parse_profile({"calibration": {**CALIBRATION, "checks": checks}})
    parse_profile({"calibration": {**CALIBRATION, "checks": checks[:8]}})


def test_a_profile_with_a_calibration_survives_a_round_trip_through_a_file(tmp_path: Path) -> None:
    original = parse_profile({"lines": [LINE], "calibration": CALIBRATION})
    path = tmp_path / "profile.json"
    path.write_text(json.dumps(original.model_dump(mode="json")))
    assert load_profile_file(path) == original
