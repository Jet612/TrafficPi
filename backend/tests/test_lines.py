import json
from pathlib import Path

import pytest

from trafficpi.core.geometry import Line
from trafficpi.vision.lines import load_counting_lines, parse_counting_lines

GOOD = {"lines": [{"id": "north", "name": "Northbound", "a": [0.2, 0.5], "b": [0.8, 0.5]}]}


def test_a_minimal_line() -> None:
    (line,) = parse_counting_lines(GOOD)
    assert (line.id, line.name, line.line) == ("north", "Northbound", Line((0.2, 0.5), (0.8, 0.5)))
    assert line.classes is None and line.point == "ground" and line.persistence == 2


def test_optional_fields() -> None:
    data = {"lines": [{"id": "x", "a": [0, 0], "b": [1, 1], "classes": ["car", "truck"],
                       "point": "center", "margin": 0.01, "persistence": 3}]}  # fmt: skip
    (line,) = parse_counting_lines(data)
    assert line.classes == frozenset({2, 7}) and line.point == "center"
    assert (line.margin, line.persistence) == (0.01, 3)


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"id": ""}, "id"),
        ({"a": [0.2]}, r"\[x, y\]"),
        ({"a": [0.2, "x"]}, r"\[x, y\]"),
        ({"a": [1.2, 0.5]}, "within 0-1"),
        ({"b": [0.2, 0.5]}, "same point"),
        ({"classes": ["spaceship"]}, "unknown class"),
        ({"classes": []}, "non-empty"),
        ({"point": "top"}, "point"),
        ({"persistence": 0}, "persistence"),
    ],
)
def test_rejects_bad_lines(change: dict, message: str) -> None:  # type: ignore[type-arg]
    data = {"lines": [{**GOOD["lines"][0], **change}]}
    with pytest.raises(ValueError, match=message):
        parse_counting_lines(data)


def test_rejects_duplicate_ids_and_a_missing_list() -> None:
    line = GOOD["lines"][0]
    with pytest.raises(ValueError, match="duplicate"):
        parse_counting_lines({"lines": [line, line]})
    with pytest.raises(ValueError, match='"lines"'):
        parse_counting_lines({})


def test_loading_from_a_file(tmp_path: Path) -> None:
    path = tmp_path / "lines.json"
    path.write_text(json.dumps(GOOD))
    assert [line.id for line in load_counting_lines(path)] == ["north"]
    path.write_text("{not json")
    with pytest.raises(ValueError, match="not valid JSON"):
        load_counting_lines(path)
