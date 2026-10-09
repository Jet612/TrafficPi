import json
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
    with pytest.raises(ProfileError, match=r"schema version 2.*update TrafficPi"):
        parse_profile({"schema_version": 2, "lines": [], "something_new": True})
    with pytest.raises(ProfileError, match="schema_version must be 1"):
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
