"""The sums behind the Data page: periods of local days, stop-sign shares, traffic (D25, M7)."""

import json
from datetime import UTC, datetime, tzinfo
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from trafficpi.api.analytics import (
    MAX_DAYS,
    local_midnight_ms,
    period_for,
    shares_of,
    speed_stats,
    stop_stats,
    traffic_stats,
)
from trafficpi.db.store import CountRow, SpeedRow, StopStatRow

NY = ZoneInfo("America/New_York")
DAY_S = 86_400


def at(text: str, tz: tzinfo = UTC) -> int:
    """ms since the epoch of a wall-clock time like '2026-10-09 18:30' in a zone (default UTC)."""
    return int(datetime.fromisoformat(text).replace(tzinfo=tz).timestamp() * 1000)


def stop_row(
    when: str,
    outcome: str = "stopped",
    *,
    kind: str = "car",
    sign: str = "elm",
    review: str | None = None,
    hold_s: float = 0.0,
    min_speed: float = -1.0,
    max_speed: float = -1.0,
    min_speed_ms: float = -1.0,
    max_speed_ms: float = -1.0,
    tz: tzinfo = NY,
) -> StopStatRow:
    return StopStatRow(
        at(when, tz),
        kind,
        sign,
        outcome,
        review,
        hold_s,
        min_speed,
        4.0,
        max_speed,
        min_speed_ms,
        max_speed_ms,
    )


# --- the period ----------------------------------------------------------------------------------


def test_seven_days_are_today_and_the_six_before_each_from_local_midnight() -> None:
    now = at("2026-10-09 12:30", NY)
    period = period_for(now, NY, 7, None)
    assert (period.days, period.all_time) == (7, False)
    assert period.start_ms == at("2026-10-03 00:00", NY)
    assert period.end_ms == at("2026-10-10 00:00", NY)
    assert period.previous_start_ms == at("2026-09-26 00:00", NY)  # seven days before that
    assert (period.first_day.isoformat(), period.today.isoformat()) == ("2026-10-03", "2026-10-09")


def test_today_alone_is_compared_with_yesterday() -> None:
    period = period_for(at("2026-10-09 00:00", NY), NY, 1, None)  # the very first moment of it
    assert period.start_ms == at("2026-10-09 00:00", NY)
    assert period.previous_start_ms == at("2026-10-08 00:00", NY)


def test_a_day_when_the_clocks_go_back_is_25_hours_long_and_the_days_stay_whole() -> None:
    now = at("2026-11-02 10:00", NY)  # the clocks went back in the night of 1 November
    period = period_for(now, NY, 3, None)
    assert period.start_ms == at("2026-10-31 00:00", NY)
    assert period.end_ms == at("2026-11-03 00:00", NY)
    assert (period.end_ms - period.start_ms) / 3_600_000 == 73  # 3 days and the extra hour
    assert period.previous_start_ms == at("2026-10-28 00:00", NY)


def test_the_day_is_the_one_in_the_zone_not_the_one_in_utc() -> None:
    now = at("2026-10-10 02:00", UTC)  # 22:00 on the 9th in New York
    assert period_for(now, NY, 1, None).today.isoformat() == "2026-10-09"
    assert period_for(now, ZoneInfo("Asia/Tokyo"), 1, None).today.isoformat() == "2026-10-10"


def test_all_time_starts_on_the_day_of_the_first_event_and_has_nothing_to_compare_with() -> None:
    now = at("2026-10-09 12:30", NY)
    period = period_for(now, NY, 0, at("2026-09-29 15:00", NY))
    assert (period.days, period.all_time) == (11, True)
    assert period.start_ms == at("2026-09-29 00:00", NY)
    assert period.previous_start_ms is None


@pytest.mark.parametrize(
    ("first_event", "days"),
    [
        (None, 1),  # nothing yet: today
        ("2026-10-09 01:00", 1),  # today
        ("2026-10-20 01:00", 1),  # a clock that ran ahead once: never fewer than a day
        ("2024-01-01 01:00", MAX_DAYS),  # more than a year ago: a year, no more
    ],
)
def test_all_time_is_at_least_a_day_and_at_most_a_year(first_event: str | None, days: int) -> None:
    first = None if first_event is None else at(first_event, NY)
    assert period_for(at("2026-10-09 12:30", NY), NY, 0, first).days == days


def test_a_longer_request_than_a_year_is_cut_to_a_year() -> None:
    assert period_for(at("2026-10-09 12:30", NY), NY, 5_000, None).days == MAX_DAYS


def test_local_midnight_is_the_start_of_the_day_in_the_zone() -> None:
    assert local_midnight_ms(datetime(2026, 10, 9).date(), NY) == at("2026-10-09 04:00", UTC)


# --- stop signs ----------------------------------------------------------------------------------

PERIOD = period_for(at("2026-10-09 20:00", NY), NY, 3, None)  # 7, 8 and 9 October


def sample_rows() -> list[StopStatRow]:
    return [
        stop_row("2026-10-09 18:30", "stopped", hold_s=2.0),
        stop_row("2026-10-09 18:40", "stopped", hold_s=3.0),
        stop_row("2026-10-09 18:50", "rolling", kind="truck", min_speed=0.4),
        stop_row("2026-10-08 08:00", "did_not_stop", sign="oak", min_speed=1.2),
        stop_row("2026-10-08 08:10", "indeterminate", kind="bicycle"),
        stop_row("2026-10-08 08:20", "rolling", review="false_positive", min_speed=0.9),
        stop_row("2026-10-08 08:30", "rolling", review="confirmed", min_speed=0.2),
        stop_row("2026-10-08 08:40", "weird"),  # an outcome that a newer version knew of
    ]


def test_the_shares_leave_out_false_alarms_and_outcomes_nobody_knows() -> None:
    stats = stop_stats(sample_rows(), None, NY, PERIOD)
    assert stats["totals"] == {"stopped": 2, "rolling": 2, "did_not_stop": 1, "indeterminate": 1}
    assert stats["previous"] is None
    assert (stats["timezone"], stats["days"], stats["all_time"]) == ("America/New_York", 3, False)
    assert (stats["from_ms"], stats["to_ms"]) == (PERIOD.start_ms, PERIOD.end_ms)
    assert stats["sign"] is None


def test_what_a_person_made_of_the_passes_is_counted_apart() -> None:
    review = stop_stats(sample_rows(), None, NY, PERIOD)["review"]
    # one agreed with; one called a false alarm; three need a look and have not had one
    # (the roll, the miss and the one that could not be told); the unknown outcome is nowhere
    assert review == {"confirmed": 1, "false_alarms": 1, "waiting": 3}


def test_hours_are_the_local_hours_of_the_day_over_all_the_days() -> None:
    by_hour = stop_stats(sample_rows(), None, NY, PERIOD)["by_hour"]
    assert [row["hour"] for row in by_hour] == list(range(24))
    assert by_hour[18] == {
        "hour": 18,
        "stopped": 2,
        "rolling": 1,
        "did_not_stop": 0,
        "indeterminate": 0,
    }
    assert by_hour[8] == {
        "hour": 8,
        "stopped": 0,
        "rolling": 1,
        "did_not_stop": 1,
        "indeterminate": 1,
    }
    assert (
        sum(row["stopped"] + row["rolling"] for i, row in enumerate(by_hour) if i not in (8, 18))
        == 0
    )


def test_every_day_of_the_period_is_there_oldest_first_with_the_empty_ones() -> None:
    by_day = stop_stats(sample_rows(), None, NY, PERIOD)["by_day"]
    assert [row["date"] for row in by_day] == ["2026-10-07", "2026-10-08", "2026-10-09"]
    assert [row["t"] for row in by_day] == [
        at("2026-10-07 00:00", NY),
        at("2026-10-08 00:00", NY),
        at("2026-10-09 00:00", NY),
    ]
    assert [
        (r["stopped"], r["rolling"], r["did_not_stop"], r["indeterminate"]) for r in by_day
    ] == [
        (0, 0, 0, 0),
        (0, 1, 1, 1),
        (2, 1, 0, 0),
    ]


def test_weekdays_start_on_monday() -> None:
    by_weekday = stop_stats(sample_rows(), None, NY, PERIOD)["by_weekday"]
    assert [row["weekday"] for row in by_weekday] == list(range(7))
    assert by_weekday[3]["did_not_stop"] == 1  # Thursday, 8 October
    assert by_weekday[4]["stopped"] == 2  # Friday, 9 October
    assert sum(row["stopped"] + row["rolling"] + row["did_not_stop"] for row in by_weekday) == 5


def test_kinds_and_signs_are_ranked_by_passes_then_by_name() -> None:
    stats = stop_stats(sample_rows(), None, NY, PERIOD)
    assert [
        (k["label"], k["stopped"] + k["rolling"] + k["did_not_stop"] + k["indeterminate"])
        for k in stats["by_kind"]
    ] == [
        ("car", 4),
        ("bicycle", 1),
        ("truck", 1),
    ]
    assert [
        (s["sign"], s["stopped"] + s["rolling"] + s["did_not_stop"] + s["indeterminate"])
        for s in stats["by_sign"]
    ] == [
        ("elm", 5),
        ("oak", 1),
    ]


def test_a_vehicle_without_a_kind_is_unknown_and_a_missing_sign_is_empty() -> None:
    row = StopStatRow(at("2026-10-09 10:00", NY), "", "", "stopped", None, 1.0, -1.0, 4.0)
    stats = stop_stats([row], None, NY, PERIOD)
    assert [k["label"] for k in stats["by_kind"]] == ["unknown"]
    assert [s["sign"] for s in stats["by_sign"]] == [""]


def test_a_typical_stop_and_a_typical_roll_are_the_middle_values() -> None:
    timing = stop_stats(sample_rows(), None, NY, PERIOD)["timing"]
    assert timing == {
        "stopped_hold_s": 2.5,  # the middle of 2.0 and 3.0
        "rolling_min_speed": 0.3,  # of 0.4 and 0.2 (the false alarm's 0.9 is not in it)
        "did_not_stop_min_speed": 1.2,
        "rolling_min_speed_ms": None,  # no calibrated camera, no speeds in metres
        "did_not_stop_min_speed_ms": None,
    }


def test_one_odd_pass_does_not_move_the_typical_one() -> None:
    rows = [
        stop_row("2026-10-09 10:00", "stopped", hold_s=1.0),
        stop_row("2026-10-09 10:05", "stopped", hold_s=1.0),
        stop_row("2026-10-09 10:10", "stopped", hold_s=40.0),  # waited for a friend
    ]
    assert stop_stats(rows, None, NY, PERIOD)["timing"]["stopped_hold_s"] == 1.0  # not the mean, 14


def test_a_speed_that_was_not_measured_is_not_a_speed() -> None:
    rows = [stop_row("2026-10-09 10:00", "rolling", min_speed=-1.0)]
    assert stop_stats(rows, None, NY, PERIOD)["timing"] == {
        "stopped_hold_s": None,
        "rolling_min_speed": None,
        "did_not_stop_min_speed": None,
        "rolling_min_speed_ms": None,
        "did_not_stop_min_speed_ms": None,
    }


def test_the_days_before_are_summed_the_same_way() -> None:
    before = [
        stop_row("2026-10-05 10:00", "stopped"),
        stop_row("2026-10-05 10:05", "stopped"),
        stop_row("2026-10-05 10:10", "rolling"),
        stop_row("2026-10-05 10:15", "did_not_stop", review="false_positive"),
        stop_row("2026-10-05 10:20", "weird"),
    ]
    previous = stop_stats([], before, NY, PERIOD)["previous"]
    assert previous == {"stopped": 2, "rolling": 1, "did_not_stop": 0, "indeterminate": 0}


def test_an_empty_period_is_all_zeros_and_still_has_every_hour_and_day() -> None:
    stats = stop_stats([], [], NY, PERIOD)
    assert stats["totals"] == {"stopped": 0, "rolling": 0, "did_not_stop": 0, "indeterminate": 0}
    assert (
        len(stats["by_hour"]) == 24 and len(stats["by_day"]) == 3 and len(stats["by_weekday"]) == 7
    )
    assert stats["by_kind"] == [] and stats["by_sign"] == []
    assert stats["review"] == {"confirmed": 0, "false_alarms": 0, "waiting": 0}


def test_the_sign_asked_for_is_reported_back() -> None:
    assert stop_stats([], None, NY, PERIOD, sign="elm")["sign"] == "elm"


def test_both_1_30_when_the_clocks_go_back_land_in_hour_1() -> None:
    rows = [
        stop_row("2026-11-01 05:30", "stopped", tz=UTC),  # 01:30 EDT
        stop_row("2026-11-01 06:30", "stopped", tz=UTC),  # 01:30 EST, an hour later
        stop_row("2026-11-01 07:00", "stopped", tz=UTC),  # 02:00 EST
    ]
    period = period_for(at("2026-11-01 20:00", NY), NY, 1, None)
    by_hour = stop_stats(rows, None, NY, period)["by_hour"]
    assert [by_hour[h]["stopped"] for h in (0, 1, 2)] == [0, 2, 1]


@pytest.mark.parametrize(
    ("zone", "utc", "hour"),
    [
        ("America/New_York", "2026-10-09 22:59:59", 18),
        ("America/New_York", "2026-10-09 23:00:00", 19),
        ("Asia/Kolkata", "2026-10-09 00:29:59", 5),  # UTC+5:30
        ("Asia/Kolkata", "2026-10-09 00:30:00", 6),
        ("Asia/Kathmandu", "2026-10-09 00:14:59", 5),  # UTC+5:45
        ("Asia/Kathmandu", "2026-10-09 00:15:00", 6),
    ],
)
def test_the_hour_is_right_at_the_edges_also_in_zones_with_odd_offsets(
    zone: str, utc: str, hour: int
) -> None:
    tz = ZoneInfo(zone)
    ms = at(utc, UTC) + (999 if utc.endswith("59") else 0)  # the last millisecond of the second
    row = StopStatRow(ms, "car", "elm", "stopped", None, 1.0, -1.0, 4.0)
    period = period_for(ms, tz, 2, None)
    by_hour = stop_stats([row], None, tz, period)["by_hour"]
    assert [h["hour"] for h in by_hour if h["stopped"]] == [hour]


# --- traffic -------------------------------------------------------------------------------------


def count_row(when: str, line: str, direction: int, kind: str, n: int = 1) -> CountRow:
    return CountRow(at(when, NY) // 1000, line, direction, kind, n)


def test_traffic_is_summed_by_hour_day_weekday_kind_and_line() -> None:
    rows = [
        count_row("2026-10-09 18:30", "north", 1, "car", 5),
        count_row("2026-10-09 18:31", "north", -1, "truck", 2),
        count_row("2026-10-09 18:59", "south", 1, "car", 1),
        count_row("2026-10-08 08:00", "north", 1, "car", 4),
        count_row("2026-10-08 08:00", "gone", 1, "bus", 3),  # a line that was deleted since
    ]
    stats = traffic_stats(rows, NY, PERIOD, {"north": "Northbound", "south": "Southbound"})
    assert (stats["total"], stats["removed"]) == (15, 3)
    assert stats["by_class"] == {"car": 10, "bus": 3, "truck": 2}
    assert [(line["id"], line["forward"], line["reverse"]) for line in stats["lines"]] == [
        ("north", 9, 2),
        ("south", 1, 0),
    ]
    assert stats["by_hour"][18] == {"hour": 18, "total": 8, "by_class": {"car": 6, "truck": 2}}
    assert stats["by_hour"][8] == {"hour": 8, "total": 7, "by_class": {"car": 4, "bus": 3}}
    assert stats["by_hour"][3]["total"] == 0 and len(stats["by_hour"]) == 24
    assert [(d["date"], d["total"]) for d in stats["by_day"]] == [
        ("2026-10-07", 0),
        ("2026-10-08", 7),
        ("2026-10-09", 8),
    ]
    assert stats["by_day"][2]["by_class"] == {"car": 6, "truck": 2}
    assert [d["total"] for d in stats["by_weekday"]] == [0, 0, 0, 7, 8, 0, 0]


def test_traffic_with_nothing_counted_is_zeros_everywhere() -> None:
    stats = traffic_stats([], NY, PERIOD, {"north": "Northbound"})
    assert (stats["total"], stats["removed"], stats["by_class"]) == (0, 0, {})
    assert [line["id"] for line in stats["lines"]] == ["north"]
    assert all(h["total"] == 0 for h in stats["by_hour"])
    assert [d["total"] for d in stats["by_day"]] == [0, 0, 0]


# --- the fastest estimated speed of each day ------------------------------------------------------


def fast_rows() -> list[StopStatRow]:
    return [
        stop_row("2026-10-09 08:15", "did_not_stop", max_speed=2.0),  # a car: 2.0 x 2.5 m = 5 m/s
        stop_row("2026-10-09 17:42", "did_not_stop", max_speed=4.0),  # a car: 10 m/s, the day's
        stop_row("2026-10-09 18:05", "stopped", max_speed=1.5),
        stop_row("2026-10-08 12:00", "rolling", kind="truck", max_speed=3.0),  # 3.0 x 3.5 = 10.5
        stop_row("2026-10-08 12:30", "stopped", max_speed=-1.0),  # not measured: left out
        # 2026-10-07 has no measured speed at all
        stop_row("2026-10-07 09:00", "stopped", max_speed=-1.0),
    ]


def test_each_day_has_the_fastest_estimate_with_the_kind_and_the_time() -> None:
    stats = stop_stats(fast_rows(), None, NY, PERIOD)
    by_date = {d["date"]: d["fastest"] for d in stats["by_day"]}
    assert by_date["2026-10-09"] == {
        "speed_ms": 10.0,
        "label": "car",
        "at": at("2026-10-09 17:42", NY),
        "calibrated": False,
    }
    assert by_date["2026-10-08"] == {
        "speed_ms": 10.5,
        "label": "truck",
        "at": at("2026-10-08 12:00", NY),
        "calibrated": False,
    }
    assert by_date["2026-10-07"] is None  # nothing there had a speed


def test_the_fastest_of_the_period_is_the_fastest_of_its_days_and_says_which() -> None:
    stats = stop_stats(fast_rows(), None, NY, PERIOD)
    assert stats["fastest"] == {
        "speed_ms": 10.5,
        "label": "truck",
        "at": at("2026-10-08 12:00", NY),
        "calibrated": False,
    }


def test_a_kind_is_converted_by_its_own_size_so_a_bus_is_faster_than_a_car_at_the_same_pace() -> (
    None
):
    rows = [
        stop_row("2026-10-09 10:00", "did_not_stop", kind="car", max_speed=2.0),
        stop_row("2026-10-09 11:00", "did_not_stop", kind="bus", max_speed=2.0),
        stop_row("2026-10-09 12:00", "did_not_stop", kind="motorcycle", max_speed=2.0),
    ]
    fastest = stop_stats(rows, None, NY, PERIOD)["fastest"]
    assert fastest == {
        "speed_ms": 11.0,
        "label": "bus",
        "at": at("2026-10-09 11:00", NY),
        "calibrated": False,
    }  # 2 x 5.5


def test_a_pass_with_no_measured_speed_is_never_the_fastest_and_nothing_is_made_up() -> None:
    rows = [stop_row("2026-10-09 10:00", "did_not_stop", max_speed=-1.0)]
    stats = stop_stats(rows, None, NY, PERIOD)
    assert stats["fastest"] is None
    assert all(d["fastest"] is None for d in stats["by_day"])
    assert stop_stats([], None, NY, PERIOD)["fastest"] is None


def test_a_pass_that_a_person_called_a_false_alarm_is_not_the_fastest() -> None:
    rows = [
        stop_row("2026-10-09 10:00", "did_not_stop", max_speed=5.0, review="false_positive"),
        stop_row("2026-10-09 11:00", "did_not_stop", max_speed=2.0),
    ]
    assert stop_stats(rows, None, NY, PERIOD)["fastest"]["speed_ms"] == 5.0  # type: ignore[index]


def test_a_vehicle_that_stood_still_has_a_speed_of_nothing_not_no_speed() -> None:
    rows = [stop_row("2026-10-09 10:00", "stopped", max_speed=0.0)]
    assert stop_stats(rows, None, NY, PERIOD)["fastest"] == {
        "speed_ms": 0.0,
        "label": "car",
        "at": at("2026-10-09 10:00", NY),
        "calibrated": False,
    }


def test_two_equally_fast_passes_give_the_earlier_one() -> None:
    rows = [
        stop_row("2026-10-09 11:00", "did_not_stop", max_speed=2.0),
        stop_row("2026-10-09 10:00", "did_not_stop", max_speed=2.0),
    ]
    rows.sort(key=lambda r: r.occurred_at)  # the store gives them oldest first
    assert stop_stats(rows, None, NY, PERIOD)["fastest"]["at"] == at("2026-10-09 10:00", NY)  # type: ignore[index]


def test_the_day_is_the_one_in_the_zone_not_the_one_in_utc_for_the_fastest_too() -> None:
    late = stop_row("2026-10-09 22:30", "did_not_stop", max_speed=2.0)  # 02:30 UTC on the 10th
    stats = stop_stats([late], None, NY, PERIOD)
    assert [d["date"] for d in stats["by_day"] if d["fastest"]] == ["2026-10-09"]


def test_a_speed_is_rounded_to_a_tenth_of_a_metre_a_second() -> None:
    rows = [stop_row("2026-10-09 10:00", "did_not_stop", max_speed=2.3333)]  # 5.83325 m/s
    assert stop_stats(rows, None, NY, PERIOD)["fastest"]["speed_ms"] == 5.8  # type: ignore[index]


# --- speeds measured on a calibrated road (plan.md M6) -------------------------------------------


def test_a_measured_speed_is_the_speed_in_metres_a_second_as_it_is() -> None:
    rows = [stop_row("2026-10-09 10:00", "did_not_stop", max_speed_ms=12.34, max_speed=1.0)]
    assert stop_stats(rows, None, NY, PERIOD)["fastest"] == {
        "speed_ms": 12.3,
        "label": "car",
        "at": at("2026-10-09 10:00", NY),
        "calibrated": True,
    }


def test_a_measured_speed_beats_an_estimate_even_a_bigger_one() -> None:
    """An estimate is good to about 40%: it could be anything, and it is never preferred."""
    rows = [
        stop_row("2026-10-09 10:00", "did_not_stop", max_speed=6.0),  # an estimate: 15 m/s
        stop_row("2026-10-09 11:00", "did_not_stop", max_speed_ms=8.0),  # measured, and slower
    ]
    fastest = stop_stats(rows, None, NY, PERIOD)["fastest"]
    assert fastest is not None and fastest["calibrated"] is True and fastest["speed_ms"] == 8.0


def test_among_measured_speeds_the_faster_wins_and_among_estimates_too() -> None:
    measured = [
        stop_row("2026-10-09 10:00", "did_not_stop", max_speed_ms=8.0),
        stop_row("2026-10-09 11:00", "did_not_stop", max_speed_ms=9.5),
        stop_row("2026-10-09 12:00", "did_not_stop", max_speed_ms=9.0),
    ]
    fastest = stop_stats(measured, None, NY, PERIOD)["fastest"]
    assert fastest is not None and fastest["speed_ms"] == 9.5
    assert fastest["at"] == at("2026-10-09 11:00", NY)


def test_a_day_with_both_shows_the_measured_one_and_a_day_with_only_estimates_its_estimate() -> (
    None
):
    rows = [
        stop_row("2026-10-08 10:00", "did_not_stop", max_speed=4.0),  # before the calibration
        stop_row("2026-10-09 10:00", "did_not_stop", max_speed=4.0),
        stop_row("2026-10-09 15:00", "did_not_stop", max_speed_ms=7.0),  # after it, the same day
    ]
    stats = stop_stats(rows, None, NY, PERIOD)
    by_date = {d["date"]: d["fastest"] for d in stats["by_day"]}
    assert by_date["2026-10-08"] == {
        "speed_ms": 10.0,
        "label": "car",
        "at": at("2026-10-08 10:00", NY),
        "calibrated": False,
    }
    assert by_date["2026-10-09"]["speed_ms"] == 7.0 and by_date["2026-10-09"]["calibrated"] is True
    assert stats["fastest"]["calibrated"] is True and stats["fastest"]["speed_ms"] == 7.0  # type: ignore[index]


def test_a_measured_speed_of_nothing_is_a_speed_and_not_a_missing_one() -> None:
    rows = [stop_row("2026-10-09 10:00", "stopped", max_speed_ms=0.0)]
    fastest = stop_stats(rows, None, NY, PERIOD)["fastest"]
    assert fastest is not None and fastest["speed_ms"] == 0.0 and fastest["calibrated"] is True


def test_a_false_alarm_is_not_the_fastest_measured_either() -> None:
    rows = [
        stop_row("2026-10-09 10:00", "did_not_stop", max_speed_ms=30.0, review="false_positive"),
        stop_row("2026-10-09 11:00", "did_not_stop", max_speed_ms=9.0),
    ]
    assert stop_stats(rows, None, NY, PERIOD)["fastest"]["speed_ms"] == 9.0  # type: ignore[index]


def test_a_typical_roll_and_a_typical_miss_in_metres_a_second_are_the_middle_values() -> None:
    rows = [
        stop_row("2026-10-09 10:00", "rolling", min_speed_ms=1.0),
        stop_row("2026-10-09 10:05", "rolling", min_speed_ms=3.0),
        stop_row("2026-10-09 10:10", "rolling", min_speed_ms=20.0),  # one odd one
        stop_row("2026-10-09 10:15", "rolling", min_speed_ms=-1.0),  # not measured: left out
        stop_row("2026-10-09 10:20", "did_not_stop", min_speed_ms=8.0),
        stop_row("2026-10-09 10:25", "did_not_stop", min_speed_ms=10.0),
        stop_row("2026-10-09 10:30", "stopped", min_speed_ms=0.2),  # a stop has no roll speed
    ]
    timing = stop_stats(rows, None, NY, PERIOD)["timing"]
    assert timing["rolling_min_speed_ms"] == 3.0
    assert timing["did_not_stop_min_speed_ms"] == 9.0


# --- the shares, as the Data page rounds them --------------------------------------------------

SHARE_CASES = json.loads((Path(__file__).parent / "data" / "share_cases.json").read_text())["cases"]


def test_the_shares_are_the_ones_the_data_page_shows_in_every_case_it_was_asked() -> None:
    """The cases were made by running the dashboard's own `sharesOf` (dashboard/src/lib/data.ts),
    so that the email, which says the same numbers, cannot round differently."""
    assert len(SHARE_CASES) > 600
    wrong = [c for c in SHARE_CASES if shares_of(c["counts"]) != c["shares"]]
    assert wrong[:3] == []


def test_the_shares_add_up_to_a_hundred_unless_nothing_was_judged() -> None:
    assert sum(shares_of({"stopped": 1, "rolling": 1, "did_not_stop": 1}).values()) == 100
    assert shares_of({"stopped": 1, "rolling": 1, "did_not_stop": 1}) == {
        "stopped": 34,
        "rolling": 33,
        "did_not_stop": 33,
        "indeterminate": 0,
    }
    assert shares_of({}) == {"stopped": 0, "rolling": 0, "did_not_stop": 0, "indeterminate": 0}


def test_an_outcome_that_did_not_happen_has_no_share_and_a_missing_one_is_none() -> None:
    assert shares_of({"stopped": 2, "rolling": 1})["did_not_stop"] == 0
    assert shares_of({"stopped": 5}) == {
        "stopped": 100,
        "rolling": 0,
        "did_not_stop": 0,
        "indeterminate": 0,
    }


# --- the speeds of the vehicles counted (plan.md M6) --------------------------------------------

MPH = 2.2369362920544


def speed_row(
    when: str,
    mph: float,
    *,
    line: str = "main",
    kind: str = "car",
    cal: str = "ab12cd34",
) -> SpeedRow:
    return SpeedRow(at(when, NY), kind, line, 1, mph / MPH, cal)


def speeds(rows: list[SpeedRow], counted: dict[str, int] | None = None, units: str = "mph"):  # type: ignore[no-untyped-def]
    return speed_stats(rows, counted or {}, NY, PERIOD, units, line_names={"main": "Main street"})


def test_the_speeds_are_summed_in_the_units_that_are_set() -> None:
    rows = [speed_row("2026-10-09 09:00", mph) for mph in (20, 25, 30)]
    mph = speeds(rows)
    assert mph["units"] == "mph"
    assert mph["figures"] == {
        "count": 3,
        "mean": 25.0,
        "median": 25.0,
        "p85": 30.0,
        "fastest": 30.0,
        "slowest": 20.0,
    }
    kmh = speeds(rows, units="kmh")
    assert kmh["units"] == "kmh"
    assert kmh["figures"]["median"] == pytest.approx(25 * 1.609344, abs=0.1)


def test_the_85th_percentile_is_the_speed_85_percent_of_vehicles_did_not_exceed() -> None:
    rows = [speed_row("2026-10-09 09:00", float(mph)) for mph in range(1, 21)]  # 1 to 20 mph
    # 85% of 20 vehicles is 17; the 17th by speed is 17 mph (nearest rank)
    assert speeds(rows)["figures"]["p85"] == 17.0
    single = speeds([speed_row("2026-10-09 09:00", 33.0)])
    assert single["figures"]["p85"] == single["figures"]["median"] == 33.0


def test_the_histogram_has_bars_of_five_from_zero_with_the_empty_ones_between() -> None:
    rows = [speed_row("2026-10-09 09:00", mph) for mph in (3, 4, 17, 17.5, 29.9)]
    bins = speeds(rows)["bins"]
    assert [(b["low"], b["high"], b["count"]) for b in bins] == [
        (0.0, 5.0, 2),
        (5.0, 10.0, 0),
        (10.0, 15.0, 0),
        (15.0, 20.0, 2),
        (20.0, 25.0, 0),
        (25.0, 30.0, 1),
    ]


def test_a_vehicle_faster_than_the_last_bar_is_in_the_last_bar() -> None:
    bins = speeds([speed_row("2026-10-09 09:00", 400.0)])["bins"]
    assert len(bins) == 40 and bins[-1]["count"] == 1 and bins[-1]["high"] == 200.0


def test_with_nothing_measured_there_are_no_figures_and_the_shape_is_whole() -> None:
    stats = speeds([], {"main": 120})
    assert stats["figures"] is None and stats["bins"] == []
    assert stats["counted"] == 120 and stats["calibration"] is None and stats["excluded"] == 0
    assert len(stats["by_hour"]) == 24 and all(h["count"] == 0 for h in stats["by_hour"])
    assert [d["count"] for d in stats["by_day"]] == [0, 0, 0]
    assert stats["by_line"] == [
        {"line": "main", "name": "Main street", "counted": 120, "figures": None}
    ]
    assert stats["by_kind"] == []


def test_the_hours_and_days_are_those_of_the_zone_not_of_utc() -> None:
    rows = [
        speed_row("2026-10-09 22:30", 40.0),  # 02:30 UTC the next day
        speed_row("2026-10-09 22:40", 50.0),
        speed_row("2026-10-08 08:05", 20.0),
    ]
    stats = speeds(rows)
    assert {h["hour"]: h["count"] for h in stats["by_hour"] if h["count"]} == {22: 2, 8: 1}
    hour_22 = next(h for h in stats["by_hour"] if h["hour"] == 22)
    assert hour_22["mean"] == 45.0 and hour_22["p85"] == 50.0
    assert {d["date"]: d["count"] for d in stats["by_day"] if d["count"]} == {
        "2026-10-09": 2,
        "2026-10-08": 1,
    }


def test_each_line_and_each_kind_has_its_own_figures_and_the_vehicles_counted() -> None:
    rows = [
        speed_row("2026-10-09 09:00", 20.0, line="main"),
        speed_row("2026-10-09 09:05", 30.0, line="main", kind="truck"),
        speed_row("2026-10-09 09:10", 40.0, line="side"),
    ]
    stats = speeds(rows, {"main": 10, "side": 3, "quiet": 7})
    assert [(row["line"], row["name"], row["counted"]) for row in stats["by_line"]] == [
        ("main", "Main street", 10),
        ("quiet", "", 7),
        ("side", "", 3),
    ]
    by_line = {row["line"]: row["figures"] for row in stats["by_line"]}
    assert by_line["main"]["count"] == 2 and by_line["main"]["mean"] == 25.0
    assert by_line["quiet"] is None
    assert [k["label"] for k in stats["by_kind"]] == ["car", "truck"]
    assert stats["counted"] == 20


def test_a_line_with_speeds_but_no_count_in_the_period_is_still_listed() -> None:
    stats = speeds([speed_row("2026-10-09 09:00", 20.0, line="side")], {})
    assert [(row["line"], row["counted"]) for row in stats["by_line"]] == [("side", 0)]


def test_the_calibration_in_force_and_the_counts_left_out_are_passed_on() -> None:
    stats = speed_stats(
        [speed_row("2026-10-09 09:00", 20.0)],
        {"main": 5},
        NY,
        PERIOD,
        "mph",
        calibration="bbbbbbbb",
        excluded=3,
    )
    assert stats["calibration"] == "bbbbbbbb" and stats["excluded"] == 3
    assert speeds([])["calibration"] is None and speeds([])["excluded"] == 0


def test_speeds_are_rounded_to_a_tenth() -> None:
    stats = speeds([speed_row("2026-10-09 09:00", 27.349)])
    assert stats["figures"]["mean"] == 27.3
