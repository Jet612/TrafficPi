"""A speed in a vehicle's own sizes a second, put in real units: a rough estimate (plan.md D41)."""

import json
from pathlib import Path

import pytest

from trafficpi.core.speed import (
    DEFAULT_SIZE_M,
    SIZE_M,
    estimated_speed_ms,
    rough_speed,
    size_metres,
    speed_phrase,
    to_units,
)

CASES = json.loads((Path(__file__).parent / "data" / "speed_cases.json").read_text())


def test_a_size_is_what_a_vehicle_of_that_kind_looks_like_in_the_picture_not_its_length() -> None:
    # A size is the geometric mean of the box's width and height (stop_sign.py). A car 4.5 m long
    # and 1.5 m high, seen from the side, is sqrt(4.5 * 1.5) = 2.6 m; seen end-on it is about 1.6 m.
    assert 1.6 <= SIZE_M["car"] <= 2.6
    assert SIZE_M["motorcycle"] < SIZE_M["car"] < SIZE_M["truck"] < SIZE_M["bus"]
    assert size_metres("car") == SIZE_M["car"] == DEFAULT_SIZE_M


@pytest.mark.parametrize("label", [None, "", "person", "boat", "CAR"])
def test_a_kind_with_no_size_of_its_own_is_taken_to_be_a_car(label: str | None) -> None:
    assert size_metres(label) == DEFAULT_SIZE_M


def test_a_speed_in_sizes_a_second_is_the_size_times_the_speed() -> None:
    assert estimated_speed_ms(2.0, "car") == pytest.approx(2.0 * SIZE_M["car"])
    assert estimated_speed_ms(2.0, "bus") == pytest.approx(2.0 * SIZE_M["bus"])
    assert estimated_speed_ms(0.0, "car") == 0.0


def test_metres_a_second_become_miles_or_kilometres_an_hour() -> None:
    assert to_units(10.0, "mph") == pytest.approx(22.37, abs=0.01)
    assert to_units(10.0, "kmh") == pytest.approx(36.0)


@pytest.mark.parametrize("case", CASES["rough"], ids=lambda c: str(c["value"]))
def test_a_speed_is_rounded_to_what_an_estimate_can_claim(case: dict[str, float]) -> None:
    assert rough_speed(case["value"]) == case["rough"]


@pytest.mark.parametrize("case", CASES["phrases"], ids=lambda c: f"{c['speed_ms']}-{c['units']}")
def test_a_speed_is_put_in_the_words_the_dashboard_uses_too(case: dict[str, object]) -> None:
    assert speed_phrase(float(case["speed_ms"]), str(case["units"])) == case["text"]  # type: ignore[arg-type]


def test_rounding_never_goes_backwards() -> None:
    values = [i / 10 for i in range(0, 1500)]
    rounded = [rough_speed(v) for v in values]
    assert rounded == sorted(rounded)
