"""``trafficpi-admin calibration-report``: what a saved speed calibration is expected to give,
in words."""

from pathlib import Path
from typing import Any

import pytest

from tests.test_service_speed import picture, profile
from trafficpi.api import admin
from trafficpi.api.calibration_text import places_of, report_text
from trafficpi.core.profile import parse_profile
from trafficpi.db.store import open_store


def check(name: str, near: float, far: float, length_m: float, x: float = 0.0) -> dict[str, Any]:
    """A known distance along the road, from ``near`` to ``far`` metres up it."""
    return {
        "id": f"check-{name}",
        "name": name,
        "a": picture(x, near),
        "b": picture(x, far),
        "length_m": length_m,
    }


def with_checks(*checks: dict[str, Any]) -> dict[str, Any]:
    document = profile()
    document["calibration"]["checks"] = list(checks)
    return document


def saved(tmp_path: Path, document: dict[str, Any]) -> Path:
    store = open_store(tmp_path)
    store.set_profile(document)
    store.close()
    return tmp_path


def test_the_places_asked_about_are_the_middle_of_each_counting_line_and_stop_line() -> None:
    parsed = parse_profile(profile())
    found = [(name, kind) for name, kind, _ in places_of(parsed)]
    assert found == [("Across", "counting line"), ("Elm", "stop line")]
    (_, _, line), (_, _, stop) = places_of(parsed)[0], places_of(parsed)[1]

    def middle(a: list[float], b: list[float]) -> list[float]:  # in the picture, not on the road
        return [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2]

    assert line.point == pytest.approx(middle(picture(-4.0, 18.0), picture(4.0, 18.0)))
    assert stop.point == pytest.approx(middle(picture(-4.0, 24.0), picture(4.0, 24.0)))


def test_the_report_names_the_calibration_its_sizes_and_each_known_distance_and_place() -> None:
    parsed = parse_profile(
        with_checks(check("Near", 10.0, 20.0, 10.0), check("Far", 12.0, 24.0, 12.0))
    )
    text = report_text(parsed)
    assert text.startswith("Speed calibration ")
    assert "8.00 m (26.2 ft) across the road" in text and "30.00 m (98.4 ft) along it" in text
    assert "at most 10%" in text
    # the lengths are exactly right on this simulated camera, so the map finds them
    assert "Near: typed 10.00 m (32.8 ft); the map finds 10.00 m (32.8 ft), +0.0%" in text
    assert "Far: typed 12.00 m (39.4 ft); the map finds 12.00 m (39.4 ft), +0.0%" in text
    assert text.count(": agrees") == 2 and "DOES NOT AGREE" not in text
    assert "Across (counting line): a pixel is " in text and "Elm (stop line): a pixel is " in text
    assert "speeds are given here" in text
    assert text.endswith("that is `trafficpi-admin speed-check`.\n")


def test_a_known_distance_that_is_typed_wrongly_is_said_not_to_agree() -> None:
    text = report_text(parse_profile(with_checks(check("Near", 10.0, 20.0, 15.0))))  # it is 10 m
    assert "typed 15.00 m" in text and "the map finds 10.00 m" in text
    assert "-33.3%" in text and "DOES NOT AGREE" in text


def test_a_calibration_with_no_known_distance_says_so() -> None:
    text = report_text(parse_profile(profile()))
    assert "none: add one or two on the Setup page" in text


def test_a_place_beyond_the_reach_of_the_rectangle_is_told_to_have_no_speed() -> None:
    document = profile()
    document["lines"][0]["a"] = picture(-4.0, 70.0)  # far up the road, well past the rectangle
    document["lines"][0]["b"] = picture(4.0, 70.0)
    text = report_text(parse_profile(document))
    assert "NO SPEED IS GIVEN HERE" in text and "Elm (stop line)" in text


def test_a_profile_with_no_calibration_is_refused() -> None:
    with pytest.raises(ValueError, match="no speed calibration"):
        report_text(parse_profile({}))


# --- the command ----------------------------------------------------------------------------------


def test_the_command_prints_the_report_of_the_saved_calibration_and_changes_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    data_dir = saved(tmp_path, with_checks(check("Near", 10.0, 20.0, 10.0)))
    before = open_store(data_dir).get_profile()
    assert admin.main(["--data-dir", str(data_dir), "calibration-report"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("Speed calibration ") and "Near: typed 10.00 m" in out
    assert open_store(data_dir).get_profile() == before


def test_the_command_says_what_is_missing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert admin.main(["--data-dir", str(tmp_path), "calibration-report"]) == 1
    assert "no profile is saved yet" in capsys.readouterr().err
    saved(tmp_path, {"lines": []})
    assert admin.main(["--data-dir", str(tmp_path), "calibration-report"]) == 1
    err = capsys.readouterr().err
    assert "no speed calibration" in err and "Setup page" in err
