"""The daily email's content: what it says for a stretch of time, in text and in HTML (D32)."""

from datetime import datetime, tzinfo
from zoneinfo import ZoneInfo

import pytest

from trafficpi.db.store import CountRow, StopStatRow
from trafficpi.mail.digest import (
    MIN_DOWN_MINUTES,
    Digest,
    DigestInput,
    build_digest,
    camera_down_minutes,
    duration_text,
    hour_range_text,
)
from trafficpi.vision.health import HealthRow

NY = ZoneInfo("America/New_York")
D = "\u2013"  # an en dash
HOUR = 3_600_000


def at(text: str, tz: tzinfo = NY) -> int:
    return int(datetime.fromisoformat(text).replace(tzinfo=tz).timestamp() * 1000)


START = at("2026-10-08 20:00")
END = at("2026-10-09 20:00")
BEFORE = START - (END - START)


def stop(
    outcome: str, when: str = "2026-10-09 08:15", *, review: str | None = None, kind: str = "car"
) -> StopStatRow:
    return StopStatRow(at(when), kind, "elm", outcome, review, 0.0, -1.0, 4.0)


def passes(**by_outcome: int) -> list[StopStatRow]:
    return [stop(name) for name, n in by_outcome.items() for _ in range(n)]


def count(hour: int, n: int, *, day: str = "2026-10-09") -> CountRow:
    start = at(f"{day} {hour:02d}:10") // 1000
    return CountRow(start, "north", 1, "car", n)


def build(
    stops: list[StopStatRow] | None = None,
    previous: list[StopStatRow] | None = None,
    counts: list[CountRow] | None = None,
    **over: object,
) -> Digest:
    data = DigestInput(
        start_ms=START,
        end_ms=END,
        tz=NY,
        stops=stops if stops is not None else [],
        previous_stops=previous,
        counts=counts if counts is not None else [],
        has_stop_sign=True,
        down_minutes=0,
        generated_ms=END + 20_000,
    )
    from dataclasses import replace

    return build_digest(replace(data, **over))  # type: ignore[arg-type]


# --- the numbers ----------------------------------------------------------------------------------


def test_the_headline_is_the_vehicles_that_did_not_stop_with_the_period_before_beside_it() -> None:
    digest = build(passes(did_not_stop=3), previous=passes(did_not_stop=4))
    assert digest.facts.did_not_stop == 3 and digest.facts.previous_did_not_stop == 4
    assert "3 vehicles did not stop" in digest.text
    assert "Before: 4" in digest.text and "1 fewer than before" in digest.text
    assert "3 vehicles did not stop" in digest.html and "1 fewer than before" in digest.html


def test_one_vehicle_is_one_vehicle() -> None:
    digest = build(passes(did_not_stop=1))
    assert "1 vehicle did not stop" in digest.text and "1 vehicles" not in digest.text


@pytest.mark.parametrize(
    ("now", "before", "words"),
    [(5, 3, "2 more than before"), (3, 5, "2 fewer than before"), (3, 3, "the same as before"),
     (1, 0, "1 more than before"), (0, 1, "1 fewer than before")],
)  # fmt: skip
def test_the_change_is_told_in_words(now: int, before: int, words: str) -> None:
    digest = build(passes(did_not_stop=now), previous=passes(did_not_stop=before))
    assert words in digest.text


def test_nothing_is_compared_when_nothing_was_recorded_before() -> None:
    digest = build(passes(did_not_stop=3), previous=None)
    assert digest.facts.previous_did_not_stop is None
    assert "Before" not in digest.text and "than before" not in digest.text
    assert "earlier" in digest.text  # it says why: there is nothing earlier to compare with


def test_an_empty_period_says_zero_and_not_nothing() -> None:
    digest = build([], previous=passes(did_not_stop=2))
    assert "0 vehicles did not stop" in digest.text and "2 fewer than before" in digest.text


def test_the_other_outcomes_follow_in_the_order_of_the_plan() -> None:
    digest = build(passes(rolling=12, stopped=140, indeterminate=2, did_not_stop=3))
    text = digest.text
    order = ["Rolled through", "Stopped", "Could not tell", "Stop rate", "Busiest hour", "Vehicles"]
    positions = [text.index(label) for label in order]
    assert positions == sorted(positions)
    assert digest.facts.rolling == 12 and digest.facts.stopped == 140
    assert digest.facts.could_not_tell == 2 and digest.facts.judged == 157


def test_the_stop_rate_is_the_data_pages_stopped_out_of_those_that_could_be_told() -> None:
    digest = build(passes(stopped=140, rolling=12, did_not_stop=3, indeterminate=40))
    assert digest.facts.stop_rate_percent == 90  # 140 of 155, rounded as the Data page does
    assert "90%" in digest.text and "155" in digest.text


def test_the_stop_rate_rounds_half_up_like_the_data_page() -> None:
    assert build(passes(stopped=1, rolling=1)).facts.stop_rate_percent == 50
    assert build(passes(stopped=5, rolling=3)).facts.stop_rate_percent == 63  # 62.5 -> 63
    assert build(passes(stopped=199, did_not_stop=1)).facts.stop_rate_percent == 100  # 99.5


def test_the_stop_rate_is_a_dash_when_no_pass_could_be_told() -> None:
    digest = build(passes(indeterminate=4))
    assert digest.facts.stop_rate_percent is None
    assert "Stop rate" in digest.text and D in digest.text


def test_passes_a_person_called_false_alarms_do_not_count_as_the_data_page_does() -> None:
    rows = [*passes(did_not_stop=2), stop("did_not_stop", review="false_positive")]
    assert build(rows).facts.did_not_stop == 2
    assert build(rows, previous=rows).facts.previous_did_not_stop == 2


def test_the_busiest_hour_is_the_hour_of_the_day_with_the_most_vehicles() -> None:
    digest = build(counts=[count(8, 5), count(17, 23), count(17, 4, day="2026-10-08"), count(9, 7)])
    assert digest.facts.busiest_hour == 17 and digest.facts.busiest_hour_vehicles == 27
    assert f"5{D}6 pm" in digest.text and "27 vehicles" in digest.text
    assert digest.facts.vehicles == 5 + 23 + 4 + 7


def test_a_tie_goes_to_the_earlier_hour_and_no_traffic_has_no_busiest_hour() -> None:
    assert build(counts=[count(9, 5), count(14, 5)]).facts.busiest_hour == 9
    quiet = build(counts=[])
    assert quiet.facts.busiest_hour is None and quiet.facts.vehicles == 0
    assert "Vehicles counted" in quiet.text and "no vehicles" in quiet.text.lower()


def test_hours_are_told_on_a_twelve_hour_clock() -> None:
    assert [hour_range_text(h) for h in (0, 1, 8, 11, 12, 13, 17, 23)] == [
        f"12{D}1 am",
        f"1{D}2 am",
        f"8{D}9 am",
        f"11 am{D}12 pm",
        f"12{D}1 pm",
        f"1{D}2 pm",
        f"5{D}6 pm",
        f"11 pm{D}12 am",
    ]


# --- when there is a reason to doubt the numbers --------------------------------------------------


def test_no_stop_sign_is_said_so_and_the_headline_is_not_a_zero() -> None:
    digest = build([], has_stop_sign=False, counts=[count(8, 12)])
    assert "No stop sign is set up" in digest.text
    assert "did not stop" not in digest.text.split("No stop sign")[0]
    assert digest.facts.has_stop_sign is False
    assert digest.subject == "TrafficPi daily report (Fri 9 Oct)"
    assert "12 vehicles" in digest.text


def test_a_camera_that_was_away_for_a_while_is_told_in_the_mail() -> None:
    digest = build(passes(did_not_stop=1), down_minutes=95)
    assert "not counting for about 1 hour 35 minutes" in digest.text
    assert "lower than" in digest.text
    assert "not counting for about 1 hour 35 minutes" in digest.html


def test_a_short_gap_is_not_worth_a_line() -> None:
    assert (
        "not counting" not in build(passes(did_not_stop=1), down_minutes=MIN_DOWN_MINUTES - 1).text
    )
    assert "not counting" in build(passes(did_not_stop=1), down_minutes=MIN_DOWN_MINUTES).text


def test_a_camera_that_was_away_all_the_time_says_so() -> None:
    digest = build([], down_minutes=24 * 60)
    assert "not counting at all" in digest.text


@pytest.mark.parametrize(
    ("minutes", "words"),
    [(5, "5 minutes"), (59, "59 minutes"), (60, "1 hour"), (61, "1 hour 1 minute"),
     (95, "1 hour 35 minutes"), (120, "2 hours"), (125, "2 hours 5 minutes"),
     (24 * 60, "24 hours"), (3 * 24 * 60, "3 days"), (3 * 24 * 60 + 60, "3 days 1 hour")],
)  # fmt: skip
def test_a_length_of_time_is_told_in_words(minutes: int, words: str) -> None:
    assert duration_text(minutes) == words


# --- the minutes the camera was away --------------------------------------------------------------


def minute(start_ms: int, *, ok: int = 12, samples: int = 12) -> HealthRow:
    return HealthRow(start_ms // 1000, 50.0, 20.0, 30.0, 29.0, 0, samples, ok)


def every_minute(start_ms: int, end_ms: int) -> list[HealthRow]:
    return [minute(t) for t in range(start_ms, end_ms, 60_000)]


def test_a_period_recorded_every_minute_has_no_downtime() -> None:
    rows = every_minute(START, END)
    assert camera_down_minutes(rows, START, END, recorded_since_ms=START - 10 * HOUR) == 0


def test_minutes_with_no_record_are_minutes_the_camera_was_not_counting() -> None:
    rows = [
        r
        for r in every_minute(START, END)
        if not at("2026-10-09 03:00") // 1000 <= r.minute_start < at("2026-10-09 03:30") // 1000
    ]
    assert camera_down_minutes(rows, START, END, recorded_since_ms=START - HOUR) == 30


def test_minutes_with_a_camera_that_never_worked_are_downtime_too() -> None:
    rows = every_minute(START, END)
    for i in range(100, 110):
        rows[i] = minute(rows[i].minute_start * 1000, ok=0)
    assert camera_down_minutes(rows, START, END, recorded_since_ms=START - HOUR) == 10


def test_a_minute_with_too_few_readings_does_not_say_the_camera_was_away() -> None:
    """A service that started near the end of a minute has a few readings only (health episodes)."""
    rows = every_minute(START, END)
    rows[50] = minute(rows[50].minute_start * 1000, ok=0, samples=2)
    assert camera_down_minutes(rows, START, END, recorded_since_ms=START - HOUR) == 0


def test_the_time_before_anything_was_recorded_is_not_counted() -> None:
    """The record begins at some moment; before it nothing is known, so nothing is claimed."""
    began = at("2026-10-09 08:00")
    rows = every_minute(began, END)
    assert camera_down_minutes(rows, START, END, recorded_since_ms=began) == 0
    assert camera_down_minutes([], START, END, recorded_since_ms=None) == 0


def test_a_period_with_no_record_at_all_since_the_record_began_is_all_downtime() -> None:
    assert camera_down_minutes([], START, END, recorded_since_ms=START - HOUR) == 24 * 60


def test_rows_outside_the_period_are_ignored() -> None:
    rows = every_minute(START - HOUR, END + HOUR)
    assert camera_down_minutes(rows, START, END, recorded_since_ms=START - 5 * HOUR) == 0


# --- the words around them ------------------------------------------------------------------------


def test_the_period_is_stated_in_the_cameras_time_zone() -> None:
    digest = build()
    assert "Thursday 8 Oct, 8:00 pm" in digest.text and "Friday 9 Oct, 8:00 pm" in digest.text
    assert "EDT" in digest.text
    kolkata = ZoneInfo("Asia/Kolkata")
    other = build(tz=kolkata)  # 20:00 in New York is 05:30 the next morning in Kolkata
    assert "Friday 9 Oct, 5:30 am to Saturday 10 Oct, 5:30 am" in other.text
    assert "IST" in other.text


def test_a_period_across_a_weekend_names_both_days() -> None:
    from dataclasses import replace

    monday = DigestInput(
        start_ms=at("2026-10-09 20:00"),
        end_ms=at("2026-10-12 20:00"),
        tz=NY,
        stops=[],
        previous_stops=None,
        counts=[],
        has_stop_sign=True,
        down_minutes=0,
        generated_ms=at("2026-10-12 20:00"),
    )
    text = build_digest(replace(monday)).text
    assert "Friday 9 Oct, 8:00 pm to Monday 12 Oct, 8:00 pm" in text


def test_the_subject_says_the_headline_and_the_day_without_odd_characters() -> None:
    digest = build(passes(did_not_stop=3, rolling=12))
    assert digest.subject == "TrafficPi: 3 did not stop, 12 rolled through (Fri 9 Oct)"
    assert "\n" not in digest.subject and "\r" not in digest.subject


def test_a_test_mail_says_it_is_one() -> None:
    digest = build(passes(did_not_stop=1), kind="test")
    assert digest.subject.startswith("[Test] ")
    assert "test of the daily email" in digest.text.lower()
    assert "test of the daily email" in digest.html.lower()


def test_the_mail_says_what_it_holds_and_why_the_reader_gets_it() -> None:
    digest = build(passes(did_not_stop=1))
    for words in ("numbers only", "no pictures", "added your address", "Settings"):
        assert words.lower() in digest.text.lower(), words


def test_the_link_is_given_only_when_the_owner_filled_one_in() -> None:
    plain = build(passes(did_not_stop=1))
    assert "http" not in plain.text and "<a " not in plain.html
    linked = build(passes(did_not_stop=1), link_url="https://traffic.example.com/?a=1&b=2")
    assert "https://traffic.example.com/?a=1&b=2" in linked.text
    assert 'href="https://traffic.example.com/?a=1&amp;b=2"' in linked.html


def test_nothing_that_is_given_can_break_out_of_the_html() -> None:
    nasty = 'https://x.example.com/"><script>alert(1)</script>'
    digest = build(passes(did_not_stop=1), link_url=nasty)
    assert "<script>" not in digest.html and "alert(1)</script>" not in digest.html


def test_the_html_is_a_complete_page_with_a_language_and_a_title_and_the_text_has_no_tags() -> None:
    digest = build(passes(did_not_stop=2, stopped=10), counts=[count(8, 20)])
    assert digest.html.lstrip().lower().startswith("<!doctype html>")
    assert '<html lang="en"' in digest.html and "<title>" in digest.html
    assert "<" not in digest.text and ">" not in digest.text.replace("->", "")
    assert len(digest.html) < 20_000


def test_the_html_uses_no_pictures_and_no_scripts_so_nothing_is_fetched_when_it_is_opened() -> None:
    digest = build(passes(did_not_stop=2))
    for forbidden in ("<img", "<script", "<link", "@import", "url(", "http://", "https://"):
        assert forbidden not in digest.html, forbidden


def test_the_numbers_in_the_html_and_the_text_agree() -> None:
    digest = build(
        passes(did_not_stop=7, rolling=11, stopped=130, indeterminate=3),
        counts=[count(7, 40), count(16, 90)],
    )
    assert digest.facts.judged == 151 and digest.facts.told == 148
    assert digest.facts.stop_rate_percent == 88  # 130 of 148
    assert digest.facts.vehicles == 130
    for words in (
        "7 vehicles did not stop",
        "88% (of the 148 passes that could be told)",
        f"4{D}5 pm (90 vehicles)",
    ):
        assert words in digest.text and words in digest.html, words


def test_what_has_its_own_colour_can_be_recoloured_for_a_dark_screen() -> None:
    """An inline colour beats a style sheet, so each text that the dark rules must change carries
    the class they name (the first dark-mode look found two that did not: dark grey on dark)."""
    digest = build(
        passes(did_not_stop=2), previous=passes(did_not_stop=1), link_url="https://x.example.com"
    )
    html = digest.html
    assert "prefers-color-scheme: dark" in html
    for dark_rule in (".headline", ".good", ".muted", ".link", ".card"):
        assert dark_rule in html.split("</style>")[0], dark_rule
    assert 'class="muted" style="margin:4px 0 0' in html  # the line beside the headline
    assert '<a class="link"' in html
    assert 'class="headline"' in html
    assert 'class="good"' in build(passes(did_not_stop=0)).html  # the all-clear is recoloured too
