"""A speed in a vehicle's own sizes a second, put in real units: a rough estimate (plan.md D41)."""

import json
from pathlib import Path

import pytest

from trafficpi.core.speed import (
    DEFAULT_SIZE_M,
    ESTIMATE_CAVEAT,
    MEASURED_CAVEAT,
    SIZE_M,
    estimated_speed_ms,
    measured_speed,
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


@pytest.mark.parametrize("case", CASES["measured"], ids=lambda c: str(c["value"]))
def test_a_measured_speed_is_rounded_to_a_whole_mile_or_kilometre_an_hour(
    case: dict[str, float],
) -> None:
    assert measured_speed(case["value"]) == case["whole"]


@pytest.mark.parametrize(
    "case", CASES["measured_phrases"], ids=lambda c: f"{c['speed_ms']}-{c['units']}"
)
def test_a_measured_speed_is_put_in_words_without_an_about(case: dict[str, object]) -> None:
    assert speed_phrase(float(case["speed_ms"]), str(case["units"]), measured=True) == case["text"]  # type: ignore[arg-type]


def test_an_estimate_is_still_an_about_unless_it_is_said_to_be_measured() -> None:
    assert speed_phrase(13.411, "mph") == "about 30 mph"
    assert speed_phrase(13.411, "mph", measured=False) == "about 30 mph"
    assert speed_phrase(13.411, "mph", measured=True) == "30 mph"


def test_measured_rounding_never_goes_backwards() -> None:
    rounded = [measured_speed(i / 10) for i in range(0, 1500)]
    assert rounded == sorted(rounded)


def test_the_sentences_that_say_what_a_speed_is_worth_are_the_dashboards_too() -> None:
    assert CASES["caveats"]["estimate"] == ESTIMATE_CAVEAT
    assert CASES["caveats"]["measured"] == MEASURED_CAVEAT
    assert "40%" in ESTIMATE_CAVEAT and "unproven" in MEASURED_CAVEAT
