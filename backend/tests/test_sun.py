"""Sunrise and sunset from a place and a day (plan.md D42), checked against the Naval Observatory's.

``data/sun_cases.json`` holds what the U.S. Naval Observatory's service answered for 24 places and
days, word for word (see the file for where it came from). The formulas under test are NOAA's, good
to about a minute away from the poles; the Observatory rounds to the minute.
"""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from trafficpi.core.sun import MS_PER_DAY, SunDay, solar_day_label, sun_day

FILE = json.loads((Path(__file__).parent / "data" / "sun_cases.json").read_text())
CASES: list[dict[str, Any]] = FILE["cases"]
MINUTE_MS = 60_000
# The Observatory rounds to a whole minute, and so do we: the two are never more than a minute
# apart on an ordinary day (the formulas themselves were within 0.6 of its times when this was
# written).
TOLERANCE_MIN = 1.0
# Within the polar circles the sun grazes the horizon, so a small error in its height is a large one
# in the clock (1.0 minute was the worst seen before rounding); allow two and a half.
POLAR_TOLERANCE_MIN = 2.5
# The days on which the midnight sun begins: the sun rises, and does not set that night. A day here
# either has both or neither (a solar day is one thing), so these are called polar days, which is
# what matters: it is not dark. They have a test of their own.
EDGE_DAYS = {"tromso_may18"}
ORDINARY = [c for c in CASES if c["name"] not in EDGE_DAYS]


def _instant(case: dict[str, Any], clock: str) -> int:
    """The Observatory's "HH:MM" on the case's date, in the case's zone, as ms since the epoch."""
    year, month, day = (int(part) for part in case["date"].split("-"))
    hours, minutes = (int(part) for part in clock.split(":"))
    local_midnight = datetime(year, month, day, tzinfo=UTC) - timedelta(
        hours=case["utc_offset_hours"]
    )
    return int((local_midnight + timedelta(hours=hours, minutes=minutes)).timestamp() * 1000)


def _label(case: dict[str, Any]) -> int:
    year, month, day = (int(part) for part in case["date"].split("-"))
    return (datetime(year, month, day, tzinfo=UTC) - datetime(1970, 1, 1, tzinfo=UTC)).days


def _days_around(case: dict[str, Any]) -> list[SunDay]:
    label = _label(case)
    return [sun_day(d, case["latitude"], case["longitude"]) for d in (label - 1, label, label + 1)]


def _tolerance_ms(case: dict[str, Any]) -> float:
    polar = abs(case["latitude"]) > 60
    return (POLAR_TOLERANCE_MIN if polar else TOLERANCE_MIN) * MINUTE_MS


def _observed(case: dict[str, Any], phen: str) -> list[int]:
    return [_instant(case, e["time"]) for e in case["sundata"] if e["phen"] == phen and e["time"]]


def _local_span(case: dict[str, Any]) -> tuple[int, int]:
    return _instant(case, "00:00"), _instant(case, "00:00") + MS_PER_DAY


def _ids(case: dict[str, Any]) -> str:
    return case["name"]


def _nearest(instants: list[int], target: int) -> float:
    return min((abs(i - target) for i in instants), default=float("inf"))


@pytest.mark.parametrize("case", ORDINARY, ids=_ids)
def test_every_sunrise_the_observatory_gives_is_found(case: dict[str, Any]) -> None:
    mine = [d.sunrise_ms for d in _days_around(case) if d.sunrise_ms is not None]
    for rise in _observed(case, "Rise"):
        assert _nearest(mine, rise) <= _tolerance_ms(case)


@pytest.mark.parametrize("case", ORDINARY, ids=_ids)
def test_every_sunset_the_observatory_gives_is_found(case: dict[str, Any]) -> None:
    # A sunset just after midnight is listed on the day it falls on and belongs to the day before.
    mine = [d.sunset_ms for d in _days_around(case) if d.sunset_ms is not None]
    for set_ in _observed(case, "Set"):
        assert _nearest(mine, set_) <= _tolerance_ms(case)


@pytest.mark.parametrize("case", CASES, ids=_ids)
def test_solar_noon_is_the_observatorys_upper_transit(case: dict[str, Any]) -> None:
    mine = [d.solar_noon_ms for d in _days_around(case)]
    for transit in _observed(case, "Upper Transit"):
        assert _nearest(mine, transit) <= TOLERANCE_MIN * MINUTE_MS


@pytest.mark.parametrize("case", ORDINARY, ids=_ids)
def test_no_sunrise_or_sunset_is_made_up_that_the_observatory_does_not_have(
    case: dict[str, Any],
) -> None:
    first, last = _local_span(case)
    tolerance = _tolerance_ms(case)
    seen = {
        "Rise": _observed(case, "Rise"),
        "Set": _observed(case, "Set"),
    }
    for day in _days_around(case):
        for name, instant in (("Rise", day.sunrise_ms), ("Set", day.sunset_ms)):
            if instant is not None and first + tolerance <= instant < last - tolerance:
                assert _nearest(seen[name], instant) <= tolerance, (name, instant)


def test_where_the_midnight_sun_begins_the_last_sunset_is_found_then_the_days_are_polar() -> None:
    by_name = {c["name"]: c for c in CASES}
    last = by_name["tromso_may17"]
    # The Observatory lists that sunset (00:27) on May 18; it ends the solar day of May 17.
    sunset = _observed(by_name["tromso_may18"], "Set")[0]
    day = sun_day(_label(last), last["latitude"], last["longitude"])
    assert day.polar is None and day.sunset_ms is not None
    assert abs(day.sunset_ms - sunset) <= POLAR_TOLERANCE_MIN * MINUTE_MS
    for name in ("tromso_may18", "tromso_may19", "tromso_may20"):
        case = by_name[name]
        assert sun_day(_label(case), case["latitude"], case["longitude"]).polar == "day"


def test_a_sunset_after_midnight_belongs_to_the_day_before_it() -> None:
    reykjavik = next(c for c in CASES if c["name"] == "reykjavik_jun")
    sunset_after_midnight = _observed(reykjavik, "Set")[0]
    label = _label(reykjavik)
    before, today = (
        sun_day(d, reykjavik["latitude"], reykjavik["longitude"]) for d in (label - 1, label)
    )
    assert before.sunset_ms is not None and today.sunrise_ms is not None
    assert abs(before.sunset_ms - sunset_after_midnight) <= TOLERANCE_MIN * MINUTE_MS
    assert before.sunset_ms < today.sunrise_ms  # the sun set, then it rose again


@pytest.mark.parametrize(
    ("name", "polar"),
    [
        ("tromso_dec", "night"),
        ("tromso_jun", "day"),
        ("tromso_may20", "day"),
        ("tromso_jan21", None),
        ("tromso_may17", None),
        ("newyork", None),
    ],
)
def test_a_day_with_no_sunrise_or_no_sunset_is_told_from_one_with_both(
    name: str, polar: str | None
) -> None:
    case = next(c for c in CASES if c["name"] == name)
    day = sun_day(_label(case), case["latitude"], case["longitude"])
    assert day.polar == polar
    assert (day.sunrise_ms is None) == (polar is not None)
    assert (day.sunset_ms is None) == (polar is not None)


def test_the_longest_day_in_new_york_is_about_fifteen_hours_and_the_shortest_about_nine() -> None:
    summer = sun_day(_label({"date": "2026-06-21"}), 40.7128, -74.006)
    winter = sun_day(_label({"date": "2026-12-21"}), 40.7128, -74.006)
    assert summer.sunrise_ms is not None and summer.sunset_ms is not None
    assert winter.sunrise_ms is not None and winter.sunset_ms is not None
    assert (summer.sunset_ms - summer.sunrise_ms) / 3_600_000 == pytest.approx(15.1, abs=0.1)
    assert (winter.sunset_ms - winter.sunrise_ms) / 3_600_000 == pytest.approx(9.3, abs=0.1)


def test_on_the_equator_every_day_is_about_twelve_hours() -> None:
    first = _label({"date": "2026-01-01"})
    for label in range(first, first + 365, 7):
        day = sun_day(label, 0.0, 10.0)
        assert day.sunrise_ms is not None and day.sunset_ms is not None
        assert (day.sunset_ms - day.sunrise_ms) / 3_600_000 == pytest.approx(12.1, abs=0.15)


def test_going_fifteen_degrees_east_brings_every_event_an_hour_earlier() -> None:
    label = _label({"date": "2026-03-01"})
    here = sun_day(label, 45.0, 0.0)
    east = sun_day(label, 45.0, 15.0)
    for a, b in ((here.sunrise_ms, east.sunrise_ms), (here.sunset_ms, east.sunset_ms)):
        assert a is not None and b is not None
        assert (a - b) / MINUTE_MS == pytest.approx(60, abs=1.5)
    assert (here.solar_noon_ms - east.solar_noon_ms) / MINUTE_MS == pytest.approx(60, abs=1.5)


def test_the_two_hemispheres_mirror_each_other_half_a_year_apart() -> None:
    north = sun_day(_label({"date": "2026-12-21"}), 40.0, 20.0)
    south = sun_day(_label({"date": "2026-06-21"}), -40.0, 20.0)
    assert north.sunrise_ms is not None and north.sunset_ms is not None
    assert south.sunrise_ms is not None and south.sunset_ms is not None
    assert (north.sunset_ms - north.sunrise_ms) / MINUTE_MS == pytest.approx(
        (south.sunset_ms - south.sunrise_ms) / MINUTE_MS, abs=8
    )


def test_every_day_of_four_years_at_every_latitude_gives_an_answer_that_makes_sense() -> None:
    first = _label({"date": "2024-01-01"})
    for latitude in (-90.0, -80.0, -66.0, -45.0, 0.0, 45.0, 66.0, 70.0, 80.0, 90.0):
        for longitude in (-180.0, -74.0, 0.0, 87.6, 180.0):
            for label in range(first, first + 4 * 366, 3):
                day = sun_day(label, latitude, longitude)
                # the solar day is the 24 hours around its noon, give or take the equation of time
                assert (
                    abs(
                        day.solar_noon_ms
                        - (label * MS_PER_DAY + 12 * 3_600_000 - round(longitude * 240_000))
                    )
                    < 17 * MINUTE_MS
                )
                if day.polar is None:
                    assert day.sunrise_ms is not None and day.sunset_ms is not None
                    assert day.sunrise_ms < day.solar_noon_ms < day.sunset_ms
                    assert day.sunset_ms - day.sunrise_ms < MS_PER_DAY
                else:
                    assert day.sunrise_ms is None and day.sunset_ms is None


def test_the_poles_have_half_a_year_of_day_and_half_a_year_of_night() -> None:
    first = _label({"date": "2026-01-01"})
    kinds = [sun_day(first + i, 90.0, 0.0).polar for i in range(365)]
    assert {"day", "night"} <= set(kinds)
    # Around each equinox the sun rises (or sets) on a day or two, even a tenth of a degree from
    # the pole, where the formulas are held. Refraction and the sun's width lengthen the sunlit
    # half (190 days against 174 when this was written).
    assert kinds.count(None) <= 6
    assert kinds.count("day") == pytest.approx(kinds.count("night"), abs=20)
    assert sun_day(_label({"date": "2026-06-21"}), 90.0, 0.0).polar == "day"
    assert sun_day(_label({"date": "2026-12-21"}), 90.0, 0.0).polar == "night"
    assert sun_day(_label({"date": "2026-06-21"}), -90.0, 0.0).polar == "night"


def test_an_instant_belongs_to_the_solar_day_around_its_local_noon() -> None:
    # Mean solar midnight at 74 degrees west is 4h56m after midnight UTC.
    label = _label({"date": "2026-10-09"})
    midnight = label * MS_PER_DAY + round(74 / 15 * 3_600_000)
    assert solar_day_label(midnight, -74.0) == label
    assert solar_day_label(midnight - 1, -74.0) == label - 1
    assert solar_day_label(midnight + MS_PER_DAY - 1, -74.0) == label
    assert solar_day_label(midnight + MS_PER_DAY, -74.0) == label + 1


def test_an_instant_is_within_half_a_day_and_the_equation_of_time_of_its_days_noon() -> None:
    # Solar days begin at mean solar midnight; true noon wanders from mean noon by up to 17 minutes.
    # So the solar day an instant is named for is the right one to within that, and the window
    # logic, which looks at the days either side as well, never misses.
    for longitude in (-179.0, -74.0, 0.0, 87.6, 179.0):
        for hour in range(0, 24 * 5):
            at = _label({"date": "2026-10-09"}) * MS_PER_DAY + hour * 3_600_000
            noon = sun_day(solar_day_label(at, longitude), 30.0, longitude).solar_noon_ms
            assert abs(noon - at) <= MS_PER_DAY // 2 + 17 * MINUTE_MS


def test_sunrise_and_sunset_are_whole_minutes_like_every_table_gives_them() -> None:
    first = _label({"date": "2026-01-01"})
    for latitude, longitude in ((40.7128, -74.006), (-33.9, 151.2), (64.1, -21.9), (0.0, 10.0)):
        for label in range(first, first + 365, 9):
            day = sun_day(label, latitude, longitude)
            for instant in (day.sunrise_ms, day.sunset_ms):
                assert instant is None or instant % MINUTE_MS == 0
