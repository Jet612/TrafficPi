from datetime import datetime
from itertools import pairwise
from zoneinfo import ZoneInfo

import pytest

from trafficpi.mail.schedule import (
    MAX_LATE_MS,
    comparison_start,
    instants,
    latest_at_or_before,
    next_after,
    period_for,
    previous_before,
)
from trafficpi.mail.settings import MailSchedule

NEW_YORK = ZoneInfo("America/New_York")
HOUR = 3_600_000
DAY = 24 * HOUR


def ms(year: int, month: int, day: int, hour: int, minute: int = 0, tz: ZoneInfo = NEW_YORK) -> int:
    return int(datetime(year, month, day, hour, minute, tzinfo=tz).timestamp()) * 1000


def local(epoch_ms: int, tz: ZoneInfo = NEW_YORK) -> str:
    return datetime.fromtimestamp(epoch_ms / 1000, tz).strftime("%a %Y-%m-%d %H:%M")


EVERY_DAY = MailSchedule()  # 20:00, all seven days
WEEKDAYS = MailSchedule(days=(0, 1, 2, 3, 4))
MONDAYS = MailSchedule(days=(0,))


def test_a_message_more_than_six_hours_late_is_too_late() -> None:
    assert MAX_LATE_MS == 6 * HOUR


# --- which instants ---------------------------------------------------------------------------


def test_the_instants_are_the_set_time_of_each_chosen_day_in_the_cameras_zone() -> None:
    found = instants(EVERY_DAY, NEW_YORK, ms(2026, 10, 7, 12), ms(2026, 10, 10, 12))
    assert [local(t) for t in found] == [
        "Wed 2026-10-07 20:00",
        "Thu 2026-10-08 20:00",
        "Fri 2026-10-09 20:00",
    ]


def test_an_instant_is_after_the_start_and_up_to_and_including_the_end() -> None:
    eight = ms(2026, 10, 9, 20)
    assert instants(EVERY_DAY, NEW_YORK, eight - 1, eight) == [eight]
    assert instants(EVERY_DAY, NEW_YORK, eight, eight + 1) == []
    assert instants(EVERY_DAY, NEW_YORK, eight, eight) == []
    assert instants(EVERY_DAY, NEW_YORK, eight + 5, eight) == []


def test_only_the_chosen_days_have_an_instant() -> None:
    found = instants(WEEKDAYS, NEW_YORK, ms(2026, 10, 2, 0), ms(2026, 10, 13, 0))
    assert [local(t) for t in found] == [
        "Fri 2026-10-02 20:00",
        "Mon 2026-10-05 20:00",
        "Tue 2026-10-06 20:00",
        "Wed 2026-10-07 20:00",
        "Thu 2026-10-08 20:00",
        "Fri 2026-10-09 20:00",
        "Mon 2026-10-12 20:00",
    ]


def test_the_time_follows_the_zone_the_owner_set() -> None:
    kolkata = ZoneInfo("Asia/Kolkata")  # +5:30
    schedule = MailSchedule(time="07:15")
    morning, night = ms(2026, 10, 9, 0, tz=kolkata), ms(2026, 10, 9, 23, tz=kolkata)
    (found,) = instants(schedule, kolkata, morning, night)
    assert local(found, kolkata) == "Fri 2026-10-09 07:15"
    assert found == ms(2026, 10, 9, 7, 15, kolkata)
    assert local(found, ZoneInfo("UTC")) == "Fri 2026-10-09 01:45"


def test_a_time_that_the_clock_skips_still_gets_one_instant_that_day() -> None:
    """02:30 does not exist on 8 March 2026 in New York: the clock goes from 02:00 to 03:00."""
    schedule = MailSchedule(time="02:30")
    found = instants(schedule, NEW_YORK, ms(2026, 3, 7, 12), ms(2026, 3, 10, 12))
    assert len(found) == 3
    days = [datetime.fromtimestamp(t / 1000, NEW_YORK).date().isoformat() for t in found]
    assert days == ["2026-03-08", "2026-03-09", "2026-03-10"]
    assert found == sorted(set(found))


def test_a_time_that_happens_twice_gets_one_instant_that_day() -> None:
    """01:30 happens twice on 1 November 2026 in New York (the clock goes back at 02:00)."""
    schedule = MailSchedule(time="01:30")
    found = instants(schedule, NEW_YORK, ms(2026, 10, 31, 12), ms(2026, 11, 2, 12))
    days = [datetime.fromtimestamp(t / 1000, NEW_YORK).date().isoformat() for t in found]
    assert days == ["2026-11-01", "2026-11-02"]


# --- latest, previous, next -------------------------------------------------------------------


def test_the_latest_instant_is_the_last_one_that_has_passed() -> None:
    assert local(latest_at_or_before(EVERY_DAY, NEW_YORK, ms(2026, 10, 9, 21)) or 0) == (
        "Fri 2026-10-09 20:00"
    )
    assert local(latest_at_or_before(EVERY_DAY, NEW_YORK, ms(2026, 10, 9, 19, 59)) or 0) == (
        "Thu 2026-10-08 20:00"
    )
    assert local(latest_at_or_before(EVERY_DAY, NEW_YORK, ms(2026, 10, 9, 20)) or 0) == (
        "Fri 2026-10-09 20:00"
    )


def test_on_a_weekend_the_latest_instant_is_fridays() -> None:
    assert local(latest_at_or_before(WEEKDAYS, NEW_YORK, ms(2026, 10, 10, 12)) or 0) == (
        "Fri 2026-10-09 20:00"
    )
    assert local(latest_at_or_before(WEEKDAYS, NEW_YORK, ms(2026, 10, 11, 23)) or 0) == (
        "Fri 2026-10-09 20:00"
    )


def test_one_day_a_week_looks_back_far_enough() -> None:
    assert local(latest_at_or_before(MONDAYS, NEW_YORK, ms(2026, 10, 9, 12)) or 0) == (
        "Mon 2026-10-05 20:00"
    )
    assert local(latest_at_or_before(MONDAYS, NEW_YORK, ms(2026, 10, 12, 19)) or 0) == (
        "Mon 2026-10-05 20:00"
    )
    assert local(latest_at_or_before(MONDAYS, NEW_YORK, ms(2026, 10, 12, 20, 1)) or 0) == (
        "Mon 2026-10-12 20:00"
    )


def test_the_previous_instant_is_the_one_before_not_this_one() -> None:
    friday = ms(2026, 10, 9, 20)
    assert local(previous_before(EVERY_DAY, NEW_YORK, friday) or 0) == "Thu 2026-10-08 20:00"
    monday = ms(2026, 10, 12, 20)
    assert local(previous_before(WEEKDAYS, NEW_YORK, monday) or 0) == "Fri 2026-10-09 20:00"
    assert local(previous_before(MONDAYS, NEW_YORK, monday) or 0) == "Mon 2026-10-05 20:00"


def test_the_next_instant_is_the_first_one_after_now() -> None:
    def next_local(schedule: MailSchedule, now: int) -> str:
        return local(next_after(schedule, NEW_YORK, now) or 0)

    assert next_local(EVERY_DAY, ms(2026, 10, 9, 19)) == "Fri 2026-10-09 20:00"
    assert next_local(EVERY_DAY, ms(2026, 10, 9, 20)) == "Sat 2026-10-10 20:00"
    assert next_local(MONDAYS, ms(2026, 10, 9, 21)) == "Mon 2026-10-12 20:00"


# --- the period each one covers ---------------------------------------------------------------


def test_every_day_covers_the_twenty_four_hours_up_to_the_instant() -> None:
    start, end = period_for(EVERY_DAY, NEW_YORK, ms(2026, 10, 9, 20))
    assert (local(start), local(end)) == ("Thu 2026-10-08 20:00", "Fri 2026-10-09 20:00")
    assert end - start == DAY


def test_mondays_mail_covers_the_weekend_when_weekends_are_off() -> None:
    start, end = period_for(WEEKDAYS, NEW_YORK, ms(2026, 10, 12, 20))
    assert (local(start), local(end)) == ("Fri 2026-10-09 20:00", "Mon 2026-10-12 20:00")
    assert end - start == 3 * DAY


def test_one_day_a_week_covers_the_week() -> None:
    start, end = period_for(MONDAYS, NEW_YORK, ms(2026, 10, 12, 20))
    assert end - start == 7 * DAY


def test_the_day_the_clocks_go_forward_covers_twenty_three_hours() -> None:
    start, end = period_for(EVERY_DAY, NEW_YORK, ms(2026, 3, 8, 20))
    assert (local(start), local(end)) == ("Sat 2026-03-07 20:00", "Sun 2026-03-08 20:00")
    assert end - start == 23 * HOUR


def test_the_day_the_clocks_go_back_covers_twenty_five_hours() -> None:
    start, end = period_for(EVERY_DAY, NEW_YORK, ms(2026, 11, 1, 20))
    assert (local(start), local(end)) == ("Sat 2026-10-31 20:00", "Sun 2026-11-01 20:00")
    assert end - start == 25 * HOUR


def test_a_period_has_neither_gap_nor_overlap_with_the_next_one() -> None:
    instants_found = instants(EVERY_DAY, NEW_YORK, ms(2026, 10, 25, 0), ms(2026, 11, 8, 0))
    periods = [period_for(EVERY_DAY, NEW_YORK, t) for t in instants_found]
    for (_, end), (start, _) in pairwise(periods):
        assert end == start


def test_the_period_before_a_period_is_as_long_and_ends_where_it_starts() -> None:
    start, end = period_for(WEEKDAYS, NEW_YORK, ms(2026, 10, 12, 20))
    before = comparison_start(start, end)
    assert start - before == end - start


@pytest.mark.parametrize("days", [(0,), (6,), (0, 3), tuple(range(7))])
def test_every_schedule_finds_a_latest_and_a_next_instant_within_a_week_and_a_bit(
    days: tuple[int, ...],
) -> None:
    schedule = MailSchedule(days=days)
    now = ms(2026, 10, 9, 12)
    latest = latest_at_or_before(schedule, NEW_YORK, now)
    following = next_after(schedule, NEW_YORK, now)
    assert latest is not None and following is not None
    assert now - latest < 8 * DAY and following - now < 8 * DAY
    assert following - latest <= 7 * DAY + HOUR


def test_with_no_earlier_instant_in_reach_the_period_is_the_day_before_the_instant() -> None:
    monday = ms(2026, 10, 12, 20)
    assert previous_before(MONDAYS, NEW_YORK, monday, lookback_days=3) is None
    start, end = period_for(MONDAYS, NEW_YORK, monday, lookback_days=3)
    assert end - start == DAY and end == monday
