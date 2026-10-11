"""When stop signs are judged: the daylight limit and the set hours."""

from collections.abc import Iterable
from datetime import UTC, datetime, tzinfo
from typing import Any, TypedDict
from zoneinfo import ZoneInfo

import pytest

from trafficpi.core import stop_hours as sh
from trafficpi.core.stop_hours import (
    StopHours,
    TimeRange,
    daylight_day,
    days_text,
    describe_hours,
    format_clock,
    judging_at,
    next_change_ms,
    parse_clock,
    read_stop_hours,
)
from trafficpi.core.sun import MS_PER_DAY, solar_day_label

NEW_YORK = ZoneInfo("America/New_York")
# The Naval Observatory for New York on 2026-10-09: rise 07:01, transit 12:43, set 18:25 (EDT).


class Place(TypedDict):
    latitude: float
    longitude: float


NYC: Place = {"latitude": 40.7128, "longitude": -74.006}
REYKJAVIK: Place = {"latitude": 64.1466, "longitude": -21.9426}
TROMSO: Place = {"latitude": 69.6492, "longitude": 18.9553}
MINUTE = 60_000


def at(
    year: int,
    month: int,
    day: int,
    hour: int,
    minute: int = 0,
    second: int = 0,
    tz: tzinfo = NEW_YORK,
) -> int:
    return int(datetime(year, month, day, hour, minute, second, tzinfo=tz).timestamp() * 1000)


def daylight(**kw: Any) -> StopHours:
    return StopHours(**{"daylight": True, **NYC, **kw})


def clock(*ranges: tuple[str, str], days: Iterable[int] = range(7)) -> StopHours:
    return StopHours(
        clock=True,
        ranges=tuple(TimeRange(parse_clock(a), parse_clock(b)) for a, b in ranges),
        days=frozenset(days),
    )


# --- what is allowed -------------------------------------------------------------------------


def test_nothing_is_limited_until_a_limit_is_switched_on() -> None:
    hours = StopHours()
    assert (hours.daylight, hours.clock, hours.limited) == (False, False, False)
    for hour in range(24):
        assert judging_at(hours, at(2026, 10, 9, hour), NEW_YORK)


def test_the_defaults_are_the_owners_thirty_minutes_either_side_and_a_working_day() -> None:
    hours = StopHours()
    assert (hours.before_sunrise_min, hours.after_sunset_min) == (30, 30)
    assert hours.ranges == (TimeRange(7 * 60, 19 * 60),)
    assert hours.days == frozenset(range(7))


def test_a_limit_that_is_on_is_what_makes_the_hours_limited() -> None:
    assert daylight().limited and clock(("07:00", "19:00")).limited
    assert not StopHours(**NYC).limited  # a place alone limits nothing


def test_the_daylight_limit_needs_a_place_to_work_from() -> None:
    with pytest.raises(ValueError, match="latitude and longitude"):
        StopHours(daylight=True)
    with pytest.raises(ValueError, match="latitude and longitude"):
        StopHours(daylight=True, latitude=40.0)


@pytest.mark.parametrize(
    ("latitude", "longitude"), [(90.1, 0.0), (-90.1, 0.0), (0.0, 180.1), (0.0, -180.1)]
)
def test_a_place_off_the_globe_is_refused(latitude: float, longitude: float) -> None:
    with pytest.raises(ValueError, match="between"):
        StopHours(latitude=latitude, longitude=longitude)


def test_a_latitude_without_a_longitude_is_refused_whether_or_not_the_limit_is_on() -> None:
    with pytest.raises(ValueError, match="both"):
        StopHours(latitude=40.0)
    with pytest.raises(ValueError, match="both"):
        StopHours(longitude=-74.0)


@pytest.mark.parametrize("minutes", [sh.MIN_MARGIN_MIN - 1, sh.MAX_MARGIN_MIN + 1])
def test_a_margin_beyond_what_makes_sense_is_refused(minutes: int) -> None:
    with pytest.raises(ValueError, match="minutes"):
        StopHours(before_sunrise_min=minutes)
    with pytest.raises(ValueError, match="minutes"):
        StopHours(after_sunset_min=minutes)


def test_the_least_and_most_are_allowed() -> None:
    StopHours(before_sunrise_min=sh.MIN_MARGIN_MIN, after_sunset_min=sh.MAX_MARGIN_MIN)
    StopHours(latitude=-90.0, longitude=180.0)
    StopHours(latitude=90.0, longitude=-180.0)


def test_a_time_range_must_have_some_length_and_stay_inside_a_day() -> None:
    with pytest.raises(ValueError, match="same"):
        TimeRange(60, 60)
    with pytest.raises(ValueError, match="00:00"):
        TimeRange(-1, 60)
    with pytest.raises(ValueError, match="00:00"):
        TimeRange(0, 24 * 60)


def test_there_must_be_a_range_and_not_too_many() -> None:
    ranges = tuple(TimeRange(i * 60, i * 60 + 30) for i in range(sh.MAX_RANGES + 1))
    with pytest.raises(ValueError, match="at least one"):
        StopHours(ranges=())
    with pytest.raises(ValueError, match=str(sh.MAX_RANGES)):
        StopHours(ranges=ranges)
    StopHours(ranges=ranges[: sh.MAX_RANGES])


def test_there_must_be_a_day_and_only_days_of_the_week() -> None:
    with pytest.raises(ValueError, match="at least one day"):
        StopHours(days=frozenset())
    with pytest.raises(ValueError, match="days of the week"):
        StopHours(days=frozenset({0, 7}))
    with pytest.raises(ValueError, match="days of the week"):
        StopHours(days=frozenset({-1}))


@pytest.mark.parametrize("text", ["07:00", "00:00", "23:59", "12:30"])
def test_a_time_of_day_is_read_and_written_in_the_same_form(text: str) -> None:
    assert format_clock(parse_clock(text)) == text


@pytest.mark.parametrize("text", ["7:00", "24:00", "07:60", "07", "7am", "", "07:00:00", " 07:00"])
def test_a_time_of_day_in_any_other_form_is_refused(text: str) -> None:
    with pytest.raises(ValueError, match="HH:MM"):
        parse_clock(text)


# --- the daylight limit ----------------------------------------------------------------------


def test_in_daylight_means_from_before_sunrise_to_after_sunset() -> None:
    # Rise 07:01 and set 18:25: with the owner's thirty minutes, 06:31 to 18:55. The formulas are
    # within a minute of the Observatory's, so look two minutes either side of each edge.
    hours = daylight()
    day = (2026, 10, 9)
    assert not judging_at(hours, at(*day, 6, 29), NEW_YORK)
    assert judging_at(hours, at(*day, 6, 33), NEW_YORK)
    assert judging_at(hours, at(*day, 12, 0), NEW_YORK)
    assert judging_at(hours, at(*day, 18, 53), NEW_YORK)
    assert not judging_at(hours, at(*day, 18, 57), NEW_YORK)
    assert not judging_at(hours, at(*day, 23, 0), NEW_YORK)
    assert not judging_at(hours, at(*day, 3, 0), NEW_YORK)


def test_with_no_margin_it_is_the_sun_itself_and_a_negative_margin_waits_for_it() -> None:
    day = (2026, 10, 9)
    sun = daylight(before_sunrise_min=0, after_sunset_min=0)
    assert not judging_at(sun, at(*day, 6, 58), NEW_YORK)
    assert judging_at(sun, at(*day, 7, 4), NEW_YORK)
    assert judging_at(sun, at(*day, 18, 22), NEW_YORK)
    assert not judging_at(sun, at(*day, 18, 28), NEW_YORK)
    later = daylight(before_sunrise_min=-15, after_sunset_min=-15)  # 15 minutes into the day
    assert not judging_at(later, at(*day, 7, 12), NEW_YORK)
    assert judging_at(later, at(*day, 7, 18), NEW_YORK)
    assert judging_at(later, at(*day, 18, 7), NEW_YORK)
    assert not judging_at(later, at(*day, 18, 13), NEW_YORK)


def test_the_daylight_limit_does_not_care_what_the_clock_says() -> None:
    # The same instant, however the clock names it, gets the same answer: only the place matters.
    instant = at(2026, 10, 9, 19, 30)
    hours = daylight()
    assert judging_at(hours, instant, NEW_YORK) == judging_at(hours, instant, UTC)
    assert judging_at(hours, instant, UTC) == judging_at(hours, instant, ZoneInfo("Asia/Tokyo"))


def test_the_days_get_longer_in_summer_and_shorter_in_winter() -> None:
    hours = daylight()
    assert judging_at(hours, at(2026, 6, 21, 20, 40), NEW_YORK)  # sunset 20:31, so until 21:01
    assert not judging_at(hours, at(2026, 12, 21, 17, 10), NEW_YORK)  # sunset 16:32, so until 17:02
    assert judging_at(hours, at(2026, 12, 21, 16, 55), NEW_YORK)


def test_the_zone_is_ignored_by_the_daylight_limit_across_a_change_of_the_clocks() -> None:
    # 2026-11-01 is the day the clocks go back in New York. The sun's time did not move.
    hours = daylight(before_sunrise_min=0, after_sunset_min=0)
    on_edt_day = at(2026, 10, 31, 12, 0)
    for hour in range(0, 24 * 3):
        instant = on_edt_day + hour * 3_600_000
        assert judging_at(hours, instant, NEW_YORK) == judging_at(hours, instant, UTC)


def test_where_the_sun_sets_after_midnight_the_margin_runs_on_into_the_next_date() -> None:
    hours = StopHours(daylight=True, after_sunset_min=30, **REYKJAVIK)
    set_ = at(2026, 6, 22, 0, 4, tz=UTC)  # the Observatory: 00:04 on 22 June, UTC
    assert judging_at(hours, set_ - 10 * MINUTE, UTC)
    assert judging_at(hours, set_ + 20 * MINUTE, UTC)  # 20 minutes after sunset, 30 allowed
    assert not judging_at(hours, set_ + 40 * MINUTE, UTC)
    assert not judging_at(hours, at(2026, 6, 22, 1, 30, tz=UTC), UTC)  # sunrise is 02:56, less 30
    assert judging_at(hours, at(2026, 6, 22, 4, 0, tz=UTC), UTC)  # the sun is up again


def test_a_window_that_runs_past_solar_midnight_still_holds_the_instants_after_it() -> None:
    # Reykjavik at midsummer: sunset 00:04 UTC (22:37 by the sun), the sun's midnight 01:27 UTC and
    # the next sunrise 02:56 UTC. Three hours of margin carry the window to 03:04 UTC, so 02:00 UTC,
    # which is already the next solar day's, is inside the window of the day before it.
    late = StopHours(daylight=True, after_sunset_min=180, **REYKJAVIK)
    usual = StopHours(daylight=True, after_sunset_min=30, **REYKJAVIK)
    at_two = at(2026, 6, 22, 2, 0, tz=UTC)
    assert judging_at(late, at_two, UTC)
    assert not judging_at(usual, at_two, UTC)  # the same instant with the usual margin: a gap


def test_a_window_that_begins_before_solar_midnight_holds_the_instants_before_it() -> None:
    # The sun rises at 02:56 UTC (01:30 by the sun); three hours before that is 22:30 by the sun on
    # the day before, and a sunset two hours early (20:37) leaves a gap until then.
    hours = StopHours(daylight=True, before_sunrise_min=180, after_sunset_min=-120, **REYKJAVIK)
    assert not judging_at(hours, at(2026, 6, 21, 22, 30, tz=UTC), UTC)  # 21:03 by the sun: the gap
    assert judging_at(
        hours, at(2026, 6, 22, 0, 30, tz=UTC), UTC
    )  # 23:03 by the sun, the day before


def test_the_midnight_sun_is_judged_round_the_clock_and_the_polar_night_never() -> None:
    hours = StopHours(daylight=True, **TROMSO)
    for hour in range(24):
        assert judging_at(hours, at(2026, 6, 21, hour, tz=UTC), UTC)
        assert not judging_at(hours, at(2026, 12, 21, hour, tz=UTC), UTC)


def test_a_margin_that_leaves_no_day_leaves_no_window() -> None:
    # In Tromso in January the sun is up for under 3 hours: 3 hours less either side is nothing.
    hours = StopHours(
        daylight=True,
        latitude=69.6492,
        longitude=18.9553,
        before_sunrise_min=-120,
        after_sunset_min=-120,
    )
    for hour in range(24):
        assert not judging_at(hours, at(2026, 1, 21, hour, tz=UTC), UTC)


def test_no_instant_is_covered_by_a_window_of_a_day_more_than_one_away() -> None:
    # The check looks at the solar day an instant is in and the two next to it. With the widest
    # margins nothing further away can reach it, whatever the place and the season.
    for latitude, longitude in (
        (0.0, 0.0),
        (40.7, -74.0),
        (-36.8, 174.8),
        (64.1, -21.9),
        (43.8, 87.6),
    ):
        for label in range(20_400, 20_400 + 365, 11):
            for step in range(0, 24, 2):
                instant = label * MS_PER_DAY + step * 3_600_000
                own = solar_day_label(instant, longitude)
                for other in range(own - 4, own + 5):
                    if abs(other - own) <= 1:
                        continue
                    window = sh.daylight_window(
                        StopHours(
                            daylight=True,
                            latitude=latitude,
                            longitude=longitude,
                            before_sunrise_min=sh.MAX_MARGIN_MIN,
                            after_sunset_min=sh.MAX_MARGIN_MIN,
                        ),
                        other,
                    )
                    assert window is None or not (window[0] <= instant < window[1])


# --- the set hours ---------------------------------------------------------------------------


def test_set_hours_judge_from_the_start_to_just_before_the_end() -> None:
    hours = clock(("07:00", "19:00"))
    day = (2026, 10, 9)  # a Friday
    assert not judging_at(hours, at(*day, 6, 59, 59), NEW_YORK)
    assert judging_at(hours, at(*day, 7, 0, 0), NEW_YORK)
    assert judging_at(hours, at(*day, 18, 59, 59), NEW_YORK)
    assert not judging_at(hours, at(*day, 19, 0, 0), NEW_YORK)


def test_set_hours_are_on_the_cameras_clock_not_greenwich() -> None:
    hours = clock(("07:00", "19:00"))
    instant = at(2026, 10, 9, 12, 0)  # noon in New York, 16:00 UTC, 21:00 in Tokyo
    assert judging_at(hours, instant, NEW_YORK)
    assert judging_at(hours, instant, UTC)
    assert not judging_at(hours, instant, ZoneInfo("Asia/Tokyo"))


def test_only_the_chosen_days_are_judged() -> None:
    weekdays = clock(("07:00", "19:00"), days=range(5))
    assert judging_at(weekdays, at(2026, 10, 9, 12), NEW_YORK)  # Friday
    assert not judging_at(weekdays, at(2026, 10, 10, 12), NEW_YORK)  # Saturday
    assert not judging_at(weekdays, at(2026, 10, 11, 12), NEW_YORK)  # Sunday
    assert judging_at(weekdays, at(2026, 10, 12, 12), NEW_YORK)  # Monday


def test_several_ranges_in_a_day_are_each_judged_and_the_gaps_between_are_not() -> None:
    school = clock(("07:30", "09:00"), ("14:00", "16:00"), days=range(5))
    day = (2026, 10, 8)  # a Thursday
    verdicts = {
        (7, 29): False,
        (7, 30): True,
        (8, 59): True,
        (9, 0): False,
        (12, 0): False,
        (14, 0): True,
        (15, 59): True,
        (16, 0): False,
        (22, 0): False,
    }
    for (hour, minute), expected in verdicts.items():
        assert judging_at(school, at(*day, hour, minute), NEW_YORK) is expected, (hour, minute)


def test_a_range_can_run_through_midnight_and_belongs_to_the_day_it_starts_on() -> None:
    nights = clock(("22:00", "06:00"), days={4})  # Friday nights only
    assert judging_at(nights, at(2026, 10, 9, 23, 0), NEW_YORK)  # Friday 23:00
    assert judging_at(nights, at(2026, 10, 10, 3, 0), NEW_YORK)  # Saturday 03:00, Friday's night
    assert not judging_at(nights, at(2026, 10, 10, 6, 0), NEW_YORK)
    assert not judging_at(nights, at(2026, 10, 10, 23, 0), NEW_YORK)  # Saturday night
    assert not judging_at(nights, at(2026, 10, 9, 3, 0), NEW_YORK)  # Friday 03:00, Thursday's
    assert not judging_at(nights, at(2026, 10, 9, 12, 0), NEW_YORK)


def test_a_range_through_midnight_can_leave_out_one_stretch_of_the_day() -> None:
    # "Everything except 16:00 to 18:00" (the sun is in the lens) is a range from 18:00 to 16:00.
    not_the_glare = clock(("18:00", "16:00"))
    assert judging_at(not_the_glare, at(2026, 10, 9, 12), NEW_YORK)
    assert judging_at(not_the_glare, at(2026, 10, 9, 15, 59), NEW_YORK)
    assert not judging_at(not_the_glare, at(2026, 10, 9, 16, 0), NEW_YORK)
    assert not judging_at(not_the_glare, at(2026, 10, 9, 17, 59), NEW_YORK)
    assert judging_at(not_the_glare, at(2026, 10, 9, 18, 0), NEW_YORK)
    assert judging_at(not_the_glare, at(2026, 10, 9, 23, 30), NEW_YORK)
    assert judging_at(not_the_glare, at(2026, 10, 10, 2, 0), NEW_YORK)


def test_set_hours_follow_the_clock_across_the_change_to_standard_time() -> None:
    hours = clock(("12:00", "13:00"))
    noon_before = at(2026, 10, 31, 12, 0)  # EDT: 16:00 UTC
    noon_after = at(2026, 11, 1, 12, 0)  # EST: 17:00 UTC
    assert noon_after - noon_before == 25 * 3_600_000
    for noon in (noon_before, noon_after):
        assert judging_at(hours, noon, NEW_YORK)
        assert judging_at(hours, noon + 59 * MINUTE, NEW_YORK)
        assert not judging_at(hours, noon - MINUTE, NEW_YORK)
        assert not judging_at(hours, noon + 60 * MINUTE, NEW_YORK)


def test_the_range_given_last_is_found_as_well_as_the_first() -> None:
    hours = clock(("01:00", "02:00"), ("03:00", "04:00"), ("05:00", "06:00"))
    assert judging_at(hours, at(2026, 10, 9, 5, 30), NEW_YORK)


# --- both limits ------------------------------------------------------------------------------


def test_with_both_limits_a_pass_must_be_in_daylight_and_in_the_set_hours() -> None:
    both = StopHours(
        daylight=True,
        clock=True,
        ranges=(TimeRange(parse_clock("09:00"), parse_clock("20:00")),),
        **NYC,
    )
    day = (2026, 10, 9)  # the window is 06:31 to 18:55
    assert not judging_at(both, at(*day, 8, 0), NEW_YORK)  # in daylight, before the set hours
    assert judging_at(both, at(*day, 12, 0), NEW_YORK)  # in both
    assert not judging_at(both, at(*day, 19, 30), NEW_YORK)  # in the set hours, after daylight
    assert not judging_at(both, at(*day, 23, 0), NEW_YORK)  # in neither
    winter = (2026, 12, 21)  # the window is 06:47 to 17:02
    assert judging_at(both, at(*winter, 12, 0), NEW_YORK)
    assert not judging_at(both, at(*winter, 18, 0), NEW_YORK)  # in the hours, not in daylight


def test_a_limit_that_is_off_does_not_limit_whatever_it_holds() -> None:
    assert judging_at(
        StopHours(clock=False, ranges=(TimeRange(0, 60),)), at(2026, 10, 9, 12), NEW_YORK
    )
    assert judging_at(StopHours(daylight=False, **NYC), at(2026, 10, 9, 3), NEW_YORK)


# --- when the answer next changes ------------------------------------------------------------


def test_with_no_limit_the_answer_never_changes() -> None:
    assert next_change_ms(StopHours(), at(2026, 10, 9, 12), NEW_YORK) is None


def test_by_day_the_next_change_is_the_end_of_the_window_and_by_night_its_start() -> None:
    hours = daylight()
    noon = at(2026, 10, 9, 12)
    end = next_change_ms(hours, noon, NEW_YORK)
    assert end is not None
    assert abs(end - at(2026, 10, 9, 18, 55)) <= 2 * MINUTE
    night = at(2026, 10, 9, 20, 41)
    start = next_change_ms(hours, night, NEW_YORK)
    assert start is not None
    assert abs(start - at(2026, 10, 10, 6, 32)) <= 3 * MINUTE  # sunrise 07:02 the next day, less 30


def test_the_next_change_is_the_first_instant_at_which_the_answer_is_different() -> None:
    cases = [
        (daylight(), at(2026, 10, 9, 12)),
        (daylight(), at(2026, 10, 9, 22)),
        (clock(("07:00", "19:00"), days=range(5)), at(2026, 10, 9, 20)),
        (clock(("22:00", "06:00")), at(2026, 10, 9, 23)),
        (clock(("07:30", "09:00"), ("14:00", "16:00")), at(2026, 10, 9, 10)),
        (
            StopHours(
                daylight=True,
                clock=True,
                ranges=(TimeRange(parse_clock("09:00"), parse_clock("17:00")),),
                **NYC,
            ),
            at(2026, 12, 21, 12),
        ),
    ]
    for hours, instant in cases:
        change = next_change_ms(hours, instant, NEW_YORK)
        assert change is not None and change > instant
        now = judging_at(hours, instant, NEW_YORK)
        assert judging_at(hours, change, NEW_YORK) is not now
        assert judging_at(hours, change - 1, NEW_YORK) is now


def test_set_hours_on_weekdays_next_change_over_the_weekend_is_monday_morning() -> None:
    hours = clock(("07:00", "19:00"), days=range(5))
    assert next_change_ms(hours, at(2026, 10, 9, 20), NEW_YORK) == at(2026, 10, 12, 7)


def test_a_change_further_than_a_week_and_a_half_off_is_not_looked_for() -> None:
    polar_night = StopHours(daylight=True, latitude=69.6492, longitude=18.9553)
    assert next_change_ms(polar_night, at(2026, 12, 10, 12, tz=UTC), UTC) is None


# --- today's daylight, for the page -----------------------------------------------------------


def test_margins_that_eat_the_day_leave_the_page_with_the_sun_and_no_window() -> None:
    tromso_in_january = StopHours(
        latitude=69.6492, longitude=18.9553, before_sunrise_min=-120, after_sunset_min=-120
    )
    day = daylight_day(tromso_in_january, at(2026, 1, 21, 12, tz=UTC), UTC)
    assert day is not None and day.polar is None
    assert day.sunrise_ms is not None and day.sunset_ms is not None
    assert (day.start_ms, day.end_ms) == (None, None)  # not a window that ends before it begins


def test_the_page_is_told_when_the_sun_rises_and_sets_today_and_the_window_around_it() -> None:
    day = daylight_day(daylight(), at(2026, 10, 9, 20, 41), NEW_YORK)
    assert day is not None
    assert day.date == "2026-10-09"
    assert day.polar is None
    assert day.sunrise_ms is not None and day.sunset_ms is not None
    assert abs(day.sunrise_ms - at(2026, 10, 9, 7, 1)) <= MINUTE
    assert abs(day.sunset_ms - at(2026, 10, 9, 18, 25)) <= MINUTE
    assert day.start_ms == day.sunrise_ms - 30 * MINUTE
    assert day.end_ms == day.sunset_ms + 30 * MINUTE
    assert abs(day.noon_offset_min - 43) <= 1  # solar noon at 12:43 by the clock


def test_today_is_the_date_on_the_cameras_clock_not_greenwichs() -> None:
    # 22:30 in New York on the 9th is already the 10th in Greenwich.
    evening = at(2026, 10, 9, 22, 30)
    assert datetime.fromtimestamp(evening / 1000, UTC).day == 10
    day = daylight_day(daylight(), evening, NEW_YORK)
    assert day is not None and day.date == "2026-10-09"
    # it is the 9th's sun: it has risen and set by 22:30, and the 10th's has not risen yet
    assert day.sunrise_ms is not None and day.sunset_ms is not None
    assert day.sunrise_ms < day.sunset_ms < evening


def test_the_day_is_the_one_whose_noon_falls_on_the_local_date_even_a_long_way_from_the_zone() -> (
    None
):
    # Urumqi keeps Beijing time, 2.2 hours ahead of the sun: solar noon is at 13:57 by the clock.
    urumqi = ZoneInfo("Asia/Urumqi")  # UTC+6, the local custom; Beijing time is UTC+8
    beijing = ZoneInfo("Asia/Shanghai")
    hours = StopHours(latitude=43.8256, longitude=87.6168)
    day = daylight_day(hours, at(2026, 10, 9, 12, tz=beijing), beijing)
    assert day is not None and day.date == "2026-10-09"
    assert abs(day.noon_offset_min - 117) <= 1
    day = daylight_day(hours, at(2026, 10, 9, 12, tz=urumqi), urumqi)
    assert day is not None and abs(day.noon_offset_min - (117 - 120)) <= 1


def test_a_wrong_sign_on_the_longitude_puts_noon_hours_from_noon() -> None:
    # 74 east instead of 74 west: the sun rises in New York's night. The page says so.
    wrong = StopHours(latitude=40.7128, longitude=74.006)
    day = daylight_day(wrong, at(2026, 10, 9, 12), NEW_YORK)
    assert day is not None
    assert abs(day.noon_offset_min) > 8 * 60


def test_there_is_no_daylight_day_without_a_place_and_a_polar_one_has_no_sunrise() -> None:
    assert daylight_day(StopHours(), at(2026, 10, 9, 12), NEW_YORK) is None
    polar = daylight_day(
        StopHours(latitude=69.6492, longitude=18.9553), at(2026, 12, 21, 12, tz=UTC), UTC
    )
    assert polar is not None and polar.polar == "night"
    assert polar.sunrise_ms is None and polar.start_ms is None and polar.end_ms is None


# --- reading what is stored ------------------------------------------------------------------


def _stored(**values: Any):
    return lambda key, default=None: values.get(key, default)


def test_nothing_stored_means_nothing_is_limited() -> None:
    assert read_stop_hours(_stored()) == StopHours()


def test_what_is_stored_is_read_back() -> None:
    stored = {
        sh.DAYLIGHT_KEY: True,
        sh.LATITUDE_KEY: 40.7128,
        sh.LONGITUDE_KEY: -74.006,
        sh.BEFORE_SUNRISE_KEY: 45,
        sh.AFTER_SUNSET_KEY: -10,
        sh.CLOCK_KEY: True,
        sh.RANGES_KEY: [{"start": "07:30", "end": "09:00"}, {"start": "14:00", "end": "16:00"}],
        sh.DAYS_KEY: [0, 1, 2, 3, 4],
    }
    hours = read_stop_hours(_stored(**stored))
    assert hours == StopHours(
        daylight=True,
        latitude=40.7128,
        longitude=-74.006,
        before_sunrise_min=45,
        after_sunset_min=-10,
        clock=True,
        ranges=(TimeRange(450, 540), TimeRange(840, 960)),
        days=frozenset(range(5)),
    )


@pytest.mark.parametrize("value", [False, None, "true", "yes", 1, 0, [], {}])
def test_only_a_plain_true_switches_a_limit_on(value: object) -> None:
    # A limit throws passes away, so a damaged value must never switch one on by accident.
    stored = _stored(
        **{
            sh.DAYLIGHT_KEY: value,
            sh.LATITUDE_KEY: 40.7,
            sh.LONGITUDE_KEY: -74.0,
            sh.CLOCK_KEY: value,
        }
    )
    hours = read_stop_hours(stored)
    assert not hours.daylight and not hours.clock and not hours.limited


@pytest.mark.parametrize(
    "place",
    [
        {},
        {sh.LATITUDE_KEY: 40.7},
        {sh.LONGITUDE_KEY: -74.0},
        {sh.LATITUDE_KEY: "40.7", sh.LONGITUDE_KEY: "-74.0"},
        {sh.LATITUDE_KEY: True, sh.LONGITUDE_KEY: True},
        {sh.LATITUDE_KEY: 91.0, sh.LONGITUDE_KEY: 0.0},
        {sh.LATITUDE_KEY: 0.0, sh.LONGITUDE_KEY: 181.0},
        {sh.LATITUDE_KEY: float("nan"), sh.LONGITUDE_KEY: 0.0},
        {sh.LATITUDE_KEY: float("inf"), sh.LONGITUDE_KEY: 0.0},
    ],
)
def test_daylight_with_an_unusable_place_limits_nothing(place: dict) -> None:
    hours = read_stop_hours(_stored(**{sh.DAYLIGHT_KEY: True, **place}))
    assert hours.daylight is False and not hours.limited


@pytest.mark.parametrize(
    "ranges",
    [
        None,
        [],
        "07:00-19:00",
        [{"start": "07:00"}],
        [{"start": "7:00", "end": "19:00"}],
        [{"start": "07:00", "end": "07:00"}],
        [["07:00", "19:00"]],
        [{"start": "07:00", "end": "19:00"}] * 7,
        [{"start": "07:00", "end": "19:00"}, "junk"],
    ],
)
def test_set_hours_with_unusable_ranges_limit_nothing(ranges: object) -> None:
    hours = read_stop_hours(_stored(**{sh.CLOCK_KEY: True, sh.RANGES_KEY: ranges}))
    assert hours.clock is False and not hours.limited


@pytest.mark.parametrize("days", [None, [], "mon", [7], [-1], [True], [0.5], ["0"], [1, 1, "x"]])
def test_unusable_days_become_every_day_rather_than_none(days: object) -> None:
    hours = read_stop_hours(
        _stored(
            **{
                sh.CLOCK_KEY: True,
                sh.RANGES_KEY: [{"start": "07:00", "end": "19:00"}],
                sh.DAYS_KEY: days,
            }
        )
    )
    assert hours.days == frozenset(range(7))
    assert hours.clock is True


@pytest.mark.parametrize("margin", [None, "30", 30.5, True, 999, -999, [30]])
def test_an_unusable_margin_is_the_default(margin: object) -> None:
    hours = read_stop_hours(_stored(**{sh.BEFORE_SUNRISE_KEY: margin, sh.AFTER_SUNSET_KEY: margin}))
    assert (hours.before_sunrise_min, hours.after_sunset_min) == (30, 30)


def test_a_place_without_the_switch_is_kept_so_that_it_can_be_shown() -> None:
    hours = read_stop_hours(_stored(**{sh.LATITUDE_KEY: 40.7, sh.LONGITUDE_KEY: -74.0}))
    assert (hours.latitude, hours.longitude) == (40.7, -74.0)
    assert not hours.daylight


def test_whole_numbers_are_taken_as_places_too() -> None:
    hours = read_stop_hours(
        _stored(**{sh.DAYLIGHT_KEY: True, sh.LATITUDE_KEY: 41, sh.LONGITUDE_KEY: -74})
    )
    assert hours.daylight and (hours.latitude, hours.longitude) == (41.0, -74.0)


# --- in words --------------------------------------------------------------------------------


def test_the_hours_are_put_in_words_for_the_journal_and_the_email() -> None:
    assert describe_hours(StopHours()) == "at all hours"
    assert describe_hours(daylight()) == "from 30 minutes before sunrise to 30 minutes after sunset"
    assert describe_hours(daylight(before_sunrise_min=0, after_sunset_min=0)) == (
        "from sunrise to sunset"
    )
    assert describe_hours(daylight(before_sunrise_min=-15, after_sunset_min=1)) == (
        "from 15 minutes after sunrise to 1 minute after sunset"
    )
    assert describe_hours(daylight(before_sunrise_min=1, after_sunset_min=-1)) == (
        "from 1 minute before sunrise to 1 minute before sunset"
    )
    assert describe_hours(clock(("07:30", "09:00"), ("14:00", "16:00"), days=range(5))) == (
        "from 07:30 to 09:00 and from 14:00 to 16:00 on weekdays"
    )
    assert describe_hours(clock(("22:00", "06:00"))) == "from 22:00 to 06:00 every day"
    both = StopHours(
        daylight=True,
        clock=True,
        ranges=(TimeRange(parse_clock("09:00"), parse_clock("20:00")),),
        days=frozenset({5, 6}),
        **NYC,
    )
    assert describe_hours(both) == (
        "from 30 minutes before sunrise to 30 minutes after sunset, "
        "and from 09:00 to 20:00 on weekends"
    )


def test_a_limit_that_is_off_is_not_described() -> None:
    assert describe_hours(StopHours(clock=False, **NYC)) == "at all hours"


@pytest.mark.parametrize(
    ("days", "text"),
    [
        (range(7), "every day"),
        (range(5), "weekdays"),
        ({5, 6}, "weekends"),
        ({0, 2, 4}, "Mon, Wed, Fri"),
        ({6}, "Sun"),
        ({3, 1}, "Tue, Thu"),
    ],
)
def test_days_are_told_the_way_the_dashboard_tells_them(days: object, text: str) -> None:
    assert days_text(frozenset(days)) == text  # type: ignore[call-overload]
