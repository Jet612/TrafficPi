"""Checking the camera's speeds against vehicles of known speed."""

from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from tests.calibrations import CALIBRATION, OTHER, name_of
from trafficpi.api import admin, speed_check
from trafficpi.api.speed_check import Match, Reading, ReadingsError
from trafficpi.core.speed import MPS_TO_MPH
from trafficpi.db.store import SpeedRow
from trafficpi.vision.rules import CountEvent

NY = ZoneInfo("America/New_York")
DAY = date(2026, 10, 11)
MPH = MPS_TO_MPH


def at(text: str) -> int:
    return int(datetime.fromisoformat(text).replace(tzinfo=NY).timestamp() * 1000)


def row(when: str, mph: float, line: str = "main") -> SpeedRow:
    return SpeedRow(at(when), "car", line, 1, mph / MPH, "ab12cd34")


def reading(when: str, mph: float, line: str | None = None, note: str = "") -> Reading:
    return Reading(at(when), mph / MPH, line, note)


# --- the file of passes ---------------------------------------------------------------------------


def test_a_pass_is_a_time_and_a_speed_on_a_line() -> None:
    found = speed_check.parse_readings("14:03:20, 25\n", NY, "mph", DAY)
    assert found == [Reading(at("2026-10-11 14:03:20"), 25 / MPH, None, "")]


def test_a_time_may_carry_its_date_and_the_line_and_a_note_may_follow() -> None:
    text = "2026-10-10 09:15:00, 30, main, cruise control\n2026-10-10T09:20, 20 mph\n"
    first, second = speed_check.parse_readings(text, NY, "mph", DAY)
    assert (first.at_ms, first.line, first.note) == (
        at("2026-10-10 09:15:00"),
        "main",
        "cruise control",
    )
    assert second.at_ms == at("2026-10-10 09:20:00") and second.line is None


def test_a_speed_says_its_units_or_is_in_the_ones_asked_for() -> None:
    text = "10:00:00, 40 km/h\n10:01:00, 11.2 m/s\n10:02:00, 25 mph\n10:03:00, 50\n"
    speeds = [r.speed_ms for r in speed_check.parse_readings(text, NY, "kmh", DAY)]
    assert speeds == pytest.approx([40 / 3.6, 11.2, 25 / MPH, 50 / 3.6])
    assert speed_check.parse_readings("10:00:00, 50\n", NY, "mph", DAY)[
        0
    ].speed_ms == pytest.approx(50 / MPH)


def test_blank_lines_comments_and_a_header_are_skipped() -> None:
    text = "# the drive-bys of Saturday\n\ntime, speed, line\n14:03:20, 25\n   \n# done\n"
    assert len(speed_check.parse_readings(text, NY, "mph", DAY)) == 1


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("", "no passes"),
        ("# nothing\n", "no passes"),
        ("14:03:20\n", "line 1: expected a time and a speed"),
        ("noon, 25\n", "line 1: 'noon' is not a time"),
        ("25:61:00, 25\n", "is not a time"),
        ("14:03:20, fast\n", "line 1: 'fast' is not a speed"),
        ("14:03:20, 0\n", "is not a speed"),
        ("14:03:20, -5\n", "is not a speed"),
        ("14:03:20, 25\n14:04:20, many\n", "line 2: 'many' is not a speed"),
        ("14:03:20, 25 knots\n", "is not a speed"),
    ],
)
def test_a_file_that_is_not_a_list_of_passes_is_refused_with_the_line_and_the_reason(
    text: str, message: str
) -> None:
    with pytest.raises(ReadingsError, match=message):
        speed_check.parse_readings(text, NY, "mph", DAY)


def test_there_is_a_limit_to_the_passes() -> None:
    text = "\n".join(f"10:{i // 60:02d}:{i % 60:02d}, 25" for i in range(501))
    with pytest.raises(ReadingsError, match="more than 500"):
        speed_check.parse_readings(text, NY, "mph", DAY)


# --- matching a pass to a count -------------------------------------------------------------------


def test_a_pass_is_matched_to_the_nearest_count_in_time() -> None:
    events = [
        row("2026-10-11 14:03:10", 31.0),
        row("2026-10-11 14:03:22", 26.0),
        row("2026-10-11 14:03:40", 40.0),
    ]
    (match,) = speed_check.match_readings([reading("2026-10-11 14:03:20", 25.0)], events)
    assert match.event is events[1]
    assert match.error == pytest.approx(26.0 / 25.0 - 1)


def test_a_count_that_is_too_far_in_time_is_not_the_pass() -> None:
    events = [row("2026-10-11 14:03:40", 26.0)]
    (match,) = speed_check.match_readings(
        [reading("2026-10-11 14:03:20", 25.0)], events, tolerance_s=8
    )
    assert match.event is None and match.error is None
    (loose,) = speed_check.match_readings(
        [reading("2026-10-11 14:03:20", 25.0)], events, tolerance_s=25
    )
    assert loose.event is events[0]


def test_two_counts_as_near_make_the_match_ambiguous_and_the_nearest_wins() -> None:
    events = [row("2026-10-11 14:03:18", 24.0), row("2026-10-11 14:03:22", 26.0)]
    (match,) = speed_check.match_readings([reading("2026-10-11 14:03:21", 25.0)], events)
    assert match.event is events[1] and match.ambiguous is True


def test_one_count_answers_for_one_pass_only() -> None:
    events = [row("2026-10-11 14:03:20", 25.0)]
    first, second = speed_check.match_readings(
        [reading("2026-10-11 14:03:19", 25.0), reading("2026-10-11 14:03:22", 25.0)], events
    )
    assert first.event is events[0] and second.event is None


def test_a_pass_on_a_named_line_is_matched_only_on_that_line_by_id_or_by_name() -> None:
    events = [
        row("2026-10-11 14:03:20", 25.0, line="side"),
        row("2026-10-11 14:03:24", 27.0, line="main"),
    ]
    by_id = speed_check.match_readings([reading("2026-10-11 14:03:20", 25.0, line="main")], events)
    assert by_id[0].event is events[1]
    by_name = speed_check.match_readings(
        [reading("2026-10-11 14:03:20", 25.0, line="Main Street")],
        events,
        line_ids={"Main street": "main"},
    )
    assert by_name[0].event is events[1]
    nowhere = speed_check.match_readings(
        [reading("2026-10-11 14:03:20", 25.0, line="other")], events
    )
    assert nowhere[0].event is None


def test_passes_are_matched_in_time_order_whatever_order_they_are_written_in() -> None:
    events = [row("2026-10-11 14:00:00", 20.0), row("2026-10-11 14:01:00", 30.0)]
    matches = speed_check.match_readings(
        [reading("2026-10-11 14:01:00", 30.0), reading("2026-10-11 14:00:00", 20.0)], events
    )
    assert [m.event for m in matches] == events


# --- how far out the camera was -------------------------------------------------------------------


def matches_of(pairs: list[tuple[float, float]]) -> list[Match]:
    """Passes at the true speeds and counts at the measured ones, in mph, each just on time."""
    return [
        Match(
            reading(f"2026-10-11 14:0{i}:00", true), row(f"2026-10-11 14:0{i}:00", measured), False
        )
        for i, (true, measured) in enumerate(pairs)
    ]


def test_the_error_is_the_cameras_speed_over_the_true_one_less_one() -> None:
    (match,) = matches_of([(20.0, 22.0)])
    assert match.error == pytest.approx(0.10)
    (slow,) = matches_of([(20.0, 18.0)])
    assert slow.error == pytest.approx(-0.10)


def test_the_summary_gives_the_bias_the_mean_and_the_largest_error_and_the_rms() -> None:
    found = speed_check.summarize(
        matches_of([(20.0, 22.0), (20.0, 19.0), (40.0, 41.0), (40.0, 36.0)])
    )
    assert found["matched"] == 4 and found["passes"] == 4 and found["unmatched"] == 0
    errors = [0.10, -0.05, 0.025, -0.10]
    assert found["mean_error"] == pytest.approx(sum(errors) / 4)
    assert found["mean_abs_error"] == pytest.approx(sum(abs(e) for e in errors) / 4)
    assert found["max_abs_error"] == pytest.approx(0.10)
    assert found["rms_error"] == pytest.approx((sum(e * e for e in errors) / 4) ** 0.5)
    assert found["max_abs_error_ms"] == pytest.approx(4.0 / MPH)
    assert found["mean_abs_error_ms"] == pytest.approx((2 + 1 + 1 + 4) / 4 / MPH)


def test_a_pass_with_no_count_is_counted_as_unmatched_and_left_out_of_the_error() -> None:
    matches = [
        *matches_of([(20.0, 22.0)]),
        Match(reading("2026-10-11 15:00:00", 30.0), None, False),
    ]
    found = speed_check.summarize(matches)
    assert (found["passes"], found["matched"], found["unmatched"]) == (2, 1, 1)
    assert found["mean_error"] == pytest.approx(0.10)


def test_with_nothing_matched_there_is_no_error_to_give() -> None:
    found = speed_check.summarize([Match(reading("2026-10-11 15:00:00", 30.0), None, False)])
    assert "mean_error" not in found and found["matched"] == 0


def test_ambiguous_passes_can_be_left_out() -> None:
    clean = matches_of([(20.0, 22.0)])
    muddled = [Match(reading("2026-10-11 14:05:00", 20.0), row("2026-10-11 14:05:00", 40.0), True)]
    both = speed_check.summarize([*clean, *muddled])
    only_clean = speed_check.summarize([*clean, *muddled], exclude_ambiguous=True)
    assert both["max_abs_error"] == pytest.approx(1.0) and only_clean[
        "max_abs_error"
    ] == pytest.approx(0.10)
    assert only_clean["matched"] == 1 and only_clean["ambiguous"] == 1


# --- the report -----------------------------------------------------------------------------------


def test_the_report_has_a_row_for_each_pass_and_the_summary() -> None:
    matches = [
        *matches_of([(20.0, 22.0), (40.0, 38.0)]),
        Match(reading("2026-10-11 15:00:00", 30.0), None, False),
    ]
    text = speed_check.report(matches, NY, "mph")
    assert "2026-10-11 14:00:00" in text and "+10.0%" in text and "-5.0%" in text
    assert "no count with a speed within the tolerance" in text
    assert "2 of 3 passes matched a count with a speed (1 had none)." in text
    assert "mean error (bias):      +2.5%" in text
    assert "mean absolute error:    7.5%" in text
    assert "largest absolute error: 10.0%" in text
    assert "Only 2 passes: too few" in text


def test_the_report_does_not_scold_a_set_of_ten_or_more() -> None:
    text = speed_check.report(matches_of([(20.0, 21.0)] * 5) * 2, NY, "mph")
    assert "too few" not in text


def test_the_report_is_in_the_units_asked_for() -> None:
    matches = matches_of([(20.0, 22.0)])
    assert "(2.0 mph)" in speed_check.report(matches, NY, "mph")
    assert "(3.2 km/h)" in speed_check.report(matches, NY, "kmh")


def test_a_pass_is_marked_when_another_vehicle_may_be_the_one_counted() -> None:
    muddled = [Match(reading("2026-10-11 14:05:00", 20.0), row("2026-10-11 14:05:00", 22.0), True)]
    text = speed_check.report(muddled, NY, "mph")
    assert "another count was as near" in text and "could be another vehicle" in text


# --- the command ----------------------------------------------------------------------------------


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    from trafficpi.db.store import open_store

    store = open_store(tmp_path)
    store.ensure_camera()
    store.set_setting("timezone", "America/New_York")
    store.set_profile(
        {
            "lines": [{"id": "main", "name": "Main street", "a": [0.2, 0.5], "b": [0.8, 0.5]}],
            "calibration": CALIBRATION,
        }
    )
    for when, mph in (
        ("2026-10-11 14:03:21", 26.0),
        ("2026-10-11 14:10:02", 41.0),
        ("2026-10-11 14:20:00", 33.0),
    ):
        store.record_count(
            CountEvent(0, 1, 2, "car", "main", 1, speed_ms=mph / MPH, calibration=name_of()),
            at(when),
        )
    store.record_count(CountEvent(0, 2, 2, "car", "main", 1), at("2026-10-11 14:30:00"))  # no speed
    store.close()
    return tmp_path


def test_the_command_matches_the_passes_to_the_counts_and_says_how_far_out_the_camera_was(
    data_dir: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    passes = tmp_path / "passes.csv"
    passes.write_text(
        "2026-10-11 14:03:20, 25, Main street\n2026-10-11 14:10:00, 40\n2026-10-11 14:30:00, 30\n"
    )
    status = admin.main(["--data-dir", str(data_dir), "speed-check", str(passes)])
    out = capsys.readouterr().out
    assert status == 0
    assert "2 of 3 passes matched" in out and "(1 had none)" in out  # the third count has no speed
    assert "+4.0%" in out and "+2.5%" in out
    assert "mean error (bias):      +3.2%" in out or "mean error (bias):      +3.3%" in out


def test_the_command_takes_clock_only_times_on_the_day_given(
    data_dir: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    passes = tmp_path / "passes.csv"
    passes.write_text("14:03:20, 25\n")
    assert (
        admin.main(
            ["--data-dir", str(data_dir), "speed-check", str(passes), "--date", "2026-10-11"]
        )
        == 0
    )
    assert "1 of 1 passes matched" in capsys.readouterr().out


def test_the_command_fails_when_nothing_matched_so_that_a_wrong_clock_is_noticed(
    data_dir: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    passes = tmp_path / "passes.csv"
    passes.write_text("2026-10-11 09:00:00, 25\n")
    assert admin.main(["--data-dir", str(data_dir), "speed-check", str(passes)]) == 1
    assert "0 of 1 passes matched" in capsys.readouterr().out


def test_the_command_says_what_is_wrong_with_the_file(
    data_dir: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    passes = tmp_path / "passes.csv"
    passes.write_text("14:03:20, fast\n")
    assert admin.main(["--data-dir", str(data_dir), "speed-check", str(passes)]) == 1
    assert "line 1: 'fast' is not a speed" in capsys.readouterr().err
    assert (
        admin.main(["--data-dir", str(data_dir), "speed-check", str(tmp_path / "missing.csv")]) == 1
    )
    assert "cannot read" in capsys.readouterr().err


def test_the_command_changes_nothing(data_dir: Path, tmp_path: Path) -> None:
    from trafficpi.db.store import open_store

    before = open_store(data_dir)
    count = len(before.recent_events(limit=100))
    before.close()
    passes = tmp_path / "passes.csv"
    passes.write_text("2026-10-11 14:03:20, 25\n")
    admin.main(["--data-dir", str(data_dir), "speed-check", str(passes)])
    after = open_store(data_dir)
    assert len(after.recent_events(limit=100)) == count
    after.close()


def test_the_units_of_a_bare_number_are_those_set_or_asked_for(
    data_dir: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from trafficpi.db.store import open_store

    store = open_store(data_dir)
    store.set_setting("units", "kmh")
    store.close()
    passes = tmp_path / "passes.csv"
    passes.write_text("2026-10-11 14:03:20, 40\n")
    admin.main(["--data-dir", str(data_dir), "speed-check", str(passes)])
    assert (
        "km/h" in capsys.readouterr().out
    )  # 40 km/h against the camera's 26 mph: far out, in km/h
    admin.main(["--data-dir", str(data_dir), "speed-check", str(passes), "--units", "mph"])
    out = capsys.readouterr().out
    assert "mph" in out and "40.0" in out


def test_counts_measured_under_another_calibration_are_left_out_unless_asked_for(
    data_dir: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The rectangle was corrected after these were measured: they say nothing about the new one."""
    from trafficpi.db.store import open_store

    store = open_store(data_dir)
    store.set_profile({"lines": [], "calibration": OTHER})
    store.close()
    passes = tmp_path / "passes.csv"
    passes.write_text("2026-10-11 14:03:20, 25\n")
    assert admin.main(["--data-dir", str(data_dir), "speed-check", str(passes)]) == 1
    out = capsys.readouterr().out
    assert "1 count(s) with a speed in these minutes were measured under another" in out
    assert "0 of 1 passes matched" in out
    status = admin.main(
        ["--data-dir", str(data_dir), "speed-check", str(passes), "--any-calibration"]
    )
    assert status == 0 and "1 of 1 passes matched" in capsys.readouterr().out
    assert name_of(OTHER) != name_of(CALIBRATION)


def test_a_time_with_its_own_offset_is_the_moment_it_says_and_a_date_alone_is_no_moment() -> None:
    utc = speed_check.parse_readings("2026-10-11T18:03:20+00:00, 25\n", NY, "mph", DAY)
    assert utc[0].at_ms == at("2026-10-11 14:03:20")  # 18:03 UTC is 14:03 in New York
    z = speed_check.parse_readings("2026-10-11 18:03:20Z, 25\n", NY, "mph", DAY)
    assert z[0].at_ms == at("2026-10-11 14:03:20")
    with pytest.raises(ReadingsError, match="is not a time"):
        speed_check.parse_readings("2026-10-11, 25\n", NY, "mph", DAY)
