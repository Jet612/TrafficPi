"""The sums behind the Data page: periods of local days, stop-sign shares, traffic (D25, M7)."""

from datetime import UTC, datetime, tzinfo
from zoneinfo import ZoneInfo

import pytest

from trafficpi.api.analytics import (
    MAX_DAYS,
    local_midnight_ms,
    period_for,
    stop_stats,
    traffic_stats,
)
from trafficpi.db.store import CountRow, StopStatRow

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
    tz: tzinfo = NY,
) -> StopStatRow:
    return StopStatRow(at(when, tz), kind, sign, outcome, review, hold_s, min_speed, 4.0)


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
