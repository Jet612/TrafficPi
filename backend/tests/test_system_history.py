"""What the System page can say about the past: points to chart, and episodes worth a sentence."""

import pytest

from trafficpi.api import system_history
from trafficpi.vision.health import SLOWED_DOWN, UNDER_VOLTAGE, HealthRow

T0 = 1_760_000_040  # a minute boundary, 2025-10-09 08:54:00 UTC
MIN = 60


def minute(
    n: int,
    *,
    temp: float | None = 70.0,
    cpu: float | None = 20.0,
    fps: float | None = 30.0,
    fps_min: float | None = None,
    throttled: int = 0,
    samples: int = 12,
    camera_ok: int | None = None,
) -> HealthRow:
    """The n-th minute from T0, a healthy one unless told otherwise."""
    ok = samples if camera_ok is None else camera_ok
    return HealthRow(
        minute_start=T0 + n * MIN,
        temp_c_max=temp,
        cpu_percent=cpu,
        fps_mean=fps if ok else None,
        fps_min=(fps if fps_min is None else fps_min) if ok else None,
        throttled=throttled,
        samples=samples,
        camera_ok_samples=ok,
    )


def history(rows: list[HealthRow], hours: int = 1, *, now_minute: int | None = None):  # type: ignore[no-untyped-def]
    """The history as the endpoint would build it, `now_minute` minutes after T0 (default: the
    end of the last row, as a service that is running would see it)."""
    now_s = T0 + (rows[-1].minute_start - T0 + MIN if now_minute is None else now_minute * MIN)
    return system_history.build(rows, hours=hours, now_s=now_s)


# --- how finely the past is drawn ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("hours", "bucket_s"),
    [(1, 60), (3, 60), (6, 120), (12, 300), (24, 600), (72, 1200), (168, 3600), (720, 14400)],
)
def test_the_longer_the_period_the_coarser_the_points(hours: int, bucket_s: int) -> None:
    assert system_history.bucket_seconds(hours) == bucket_s


def test_no_period_makes_more_than_240_points() -> None:
    for hours in range(1, 721):
        bucket_s = system_history.bucket_seconds(hours)
        assert hours * 3600 / bucket_s <= 240, hours
        assert bucket_s in (60, 120, 300, 600, 900, 1200, 1800, 3600, 7200, 14400)


# --- points ------------------------------------------------------------------------------------


def test_the_points_cover_the_period_with_nothing_where_the_service_was_not_recording() -> None:
    rows = [minute(0), minute(1), minute(30), minute(59)]
    h = history(rows, hours=1)
    assert h["bucket_s"] == 60
    assert len(h["points"]) == 60
    assert h["to_s"] - h["from_s"] == 3600
    assert [p["t"] for p in h["points"]] == list(range(h["from_s"], h["to_s"], 60))
    by_t = {p["t"]: p for p in h["points"]}
    assert by_t[T0]["minutes"] == 1 and by_t[T0]["temp_c"] == 70.0
    gap = by_t[T0 + 10 * MIN]
    assert gap["minutes"] == 0
    assert gap["temp_c"] is None and gap["cpu_percent"] is None and gap["fps"] is None
    assert gap["fps_min"] is None and gap["camera_up_percent"] is None and gap["throttled"] == 0


def test_a_point_that_gathers_several_minutes_takes_the_hottest_the_slowest_and_the_mean() -> None:
    rows = [
        minute(0, temp=68.0, cpu=10.0, fps=30.0, fps_min=28.0),
        minute(1, temp=74.5, cpu=30.0, fps=20.0, fps_min=12.0),
        minute(2, temp=70.0, cpu=20.0, fps=30.0, fps_min=29.0, throttled=UNDER_VOLTAGE),
    ]
    h = history(rows, hours=24)  # ten-minute points: the three fall into one
    point = next(p for p in h["points"] if p["minutes"])
    assert point["minutes"] == 3
    assert point["temp_c"] == 74.5
    assert point["fps_min"] == 12.0
    assert point["cpu_percent"] == pytest.approx(20.0, abs=0.05)
    assert point["fps"] == pytest.approx(26.7, abs=0.05)
    assert point["throttled"] == UNDER_VOLTAGE
    assert point["camera_up_percent"] == 100.0


def test_the_mean_is_weighted_by_the_readings_each_minute_rests_on() -> None:
    rows = [
        minute(0, cpu=10.0, fps=30.0, samples=12),
        minute(1, cpu=70.0, fps=10.0, samples=3, camera_ok=3),  # a short minute, as after a restart
    ]
    point = next(p for p in history(rows, hours=24)["points"] if p["minutes"])
    assert point["cpu_percent"] == pytest.approx((10 * 12 + 70 * 3) / 15, abs=0.05)
    assert point["fps"] == pytest.approx((30 * 12 + 10 * 3) / 15, abs=0.05)


def test_minutes_without_a_camera_count_against_the_cameras_uptime_and_have_no_frame_rate() -> None:
    rows = [
        minute(0, fps=30.0),
        minute(1, camera_ok=0),
        minute(2, samples=12, camera_ok=6, fps=24.0),
    ]
    point = next(p for p in history(rows, hours=24)["points"] if p["minutes"])
    assert point["camera_up_percent"] == pytest.approx(100 * 18 / 36, abs=0.05)
    assert point["fps"] == pytest.approx((30 * 12 + 24 * 6) / 18, abs=0.05)  # only while it worked


def test_the_period_ends_with_the_point_that_holds_the_last_finished_minute() -> None:
    rows = [minute(5)]
    h = system_history.build(rows, hours=6, now_s=T0 + 6 * MIN + 17)  # minute 6 is in progress
    assert h["bucket_s"] == 120
    assert h["to_s"] % 120 == 0 and h["to_s"] == T0 + 6 * MIN  # minute 5 ended at 6; 6 is not in
    assert h["to_s"] - h["from_s"] == 6 * 3600
    assert all(p["t"] % 120 == 0 for p in h["points"])
    assert h["points"][-1]["minutes"] == 1 and h["points"][-2]["minutes"] == 0


def test_a_period_that_is_not_a_whole_number_of_points_is_made_one_by_going_back_further() -> None:
    h = system_history.build([], hours=301, now_s=T0)  # two-hour points: 150.5 of them
    assert h["bucket_s"] == 7200
    assert (h["to_s"] - h["from_s"]) % 7200 == 0
    assert h["to_s"] - h["from_s"] >= 301 * 3600
    assert len(h["points"]) == 151


def test_no_rows_at_all_is_an_empty_history_not_an_error() -> None:
    h = system_history.build([], hours=24, now_s=T0)
    assert h["episodes"] == []
    assert h["summary"] == {
        "minutes": 0,
        "temp_c_max": None,
        "camera_up_percent": None,
        "slowed_minutes": 0,
        "under_voltage_minutes": 0,
    }
    assert all(p["minutes"] == 0 for p in h["points"])


# --- the summary -------------------------------------------------------------------------------


def test_the_summary_counts_the_minutes_and_finds_the_hottest() -> None:
    rows = [
        minute(0, temp=70.0),
        minute(1, temp=83.5, throttled=SLOWED_DOWN & 0b0100),
        minute(2, temp=82.0, throttled=UNDER_VOLTAGE | 0b0010),
        minute(3, temp=69.0, camera_ok=0),
    ]
    summary = history(rows)["summary"]
    assert summary["minutes"] == 4
    assert summary["temp_c_max"] == 83.5
    assert summary["slowed_minutes"] == 2
    assert summary["under_voltage_minutes"] == 1
    assert summary["camera_up_percent"] == pytest.approx(100 * 36 / 48, abs=0.05)


# --- episodes ----------------------------------------------------------------------------------


def kinds(h) -> list[tuple[str, int, int]]:  # type: ignore[no-untyped-def]
    return [(e["kind"], e["from_s"], e["to_s"]) for e in h["episodes"]]


def test_a_run_of_throttled_minutes_is_one_episode_with_the_temperature_it_reached() -> None:
    rows = [
        minute(0),
        minute(1, temp=81.0, throttled=0b0100),
        minute(2, temp=84.2, throttled=0b1100),
        minute(3, temp=82.0, throttled=0b0100),
        minute(4),
        minute(5),
        minute(6),
    ]
    h = history(rows, now_minute=7)
    assert kinds(h) == [("slowed", T0 + 1 * MIN, T0 + 4 * MIN)]
    episode = h["episodes"][0]
    assert episode["peak_temp_c"] == 84.2
    assert episode["ongoing"] is False


def test_an_episode_still_going_on_at_the_end_is_ongoing() -> None:
    rows = [minute(0), minute(1, throttled=0b0100), minute(2, throttled=0b0100)]
    episode = history(rows)["episodes"][0]
    assert episode["kind"] == "slowed" and episode["ongoing"] is True


def test_one_good_minute_between_two_bad_ones_does_not_split_an_episode() -> None:
    rows = [minute(0, throttled=0b0100), minute(1), minute(2, throttled=0b0100), minute(3)]
    assert kinds(history(rows)) == [("slowed", T0, T0 + 3 * MIN)]


def test_a_long_good_stretch_separates_two_episodes() -> None:
    rows = [
        minute(0, throttled=0b0100),
        *[minute(n) for n in range(1, 9)],
        minute(9, throttled=0b0100),
    ]
    h = history(rows, now_minute=11)
    assert [k for k, _, _ in kinds(h)] == ["slowed", "slowed"]


def test_under_voltage_is_told_on_its_own_and_the_newest_episode_is_first() -> None:
    rows = [minute(0, throttled=UNDER_VOLTAGE), minute(1, throttled=UNDER_VOLTAGE | 0b0100)]
    assert kinds(history(rows)) == [
        ("slowed", T0 + MIN, T0 + 2 * MIN),
        ("under-voltage", T0, T0 + 2 * MIN),
    ]
    assert history(rows)["episodes"][1]["peak_temp_c"] is None  # only a slowdown has a temperature


def test_minutes_with_no_camera_at_all_are_a_camera_episode() -> None:
    rows = [minute(0), minute(1, camera_ok=0), minute(2, camera_ok=0), minute(3), minute(4)]
    h = history(rows, now_minute=5)
    assert kinds(h) == [("camera-offline", T0 + 1 * MIN, T0 + 3 * MIN)]
    assert h["episodes"][0]["peak_temp_c"] is None


def test_a_minute_when_the_camera_worked_part_of_the_time_is_not_an_outage() -> None:
    rows = [minute(0), minute(1, samples=12, camera_ok=2), minute(2)]
    assert history(rows)["episodes"] == []


def test_a_short_minute_without_the_camera_is_a_service_starting_not_an_outage() -> None:
    """A restart near the end of a minute leaves a row of a reading or two, taken before the camera
    was open. That is not the camera being away."""
    rows = [minute(0), minute(1, samples=2, camera_ok=0), minute(2)]
    assert history(rows)["episodes"] == []


def test_a_gap_of_a_few_minutes_between_rows_is_a_gap_in_the_record() -> None:
    rows = [minute(0), minute(1), minute(10), minute(11)]  # minutes 2 to 9 are missing
    h = history(rows)
    assert kinds(h) == [("no-data", T0 + 2 * MIN, T0 + 10 * MIN)]
    assert h["episodes"][0]["ongoing"] is False


def test_a_restart_that_cost_a_minute_or_two_is_not_worth_a_mention() -> None:
    rows = [minute(0), minute(1), minute(3), minute(4)]  # minute 2 is missing
    assert history(rows)["episodes"] == []
    rows = [minute(0), minute(1), minute(4), minute(5)]  # minutes 2 and 3
    assert history(rows)["episodes"] == []


def test_when_nothing_has_been_recorded_for_a_while_that_is_told_as_going_on() -> None:
    rows = [minute(0), minute(1)]
    h = history(rows, now_minute=12)
    assert kinds(h) == [("no-data", T0 + 2 * MIN, T0 + 12 * MIN)]
    assert h["episodes"][0]["ongoing"] is True


def test_the_minute_in_progress_is_not_a_gap() -> None:
    rows = [minute(0), minute(1)]
    h = system_history.build(rows, hours=1, now_s=T0 + 2 * MIN + 59)  # it is stored when it ends
    assert h["episodes"] == []


def test_nothing_before_the_first_row_is_reported_missing() -> None:
    rows = [minute(50), minute(51)]
    assert history(rows, hours=1)["episodes"] == []


def test_the_newest_episodes_come_first_and_there_are_at_most_fifty() -> None:
    rows = [minute(n, throttled=0b0100 if n % 10 == 0 else 0) for n in range(0, 700)]
    h = system_history.build(rows, hours=12, now_s=T0 + 700 * MIN)
    assert len(h["episodes"]) == 50
    starts = [e["from_s"] for e in h["episodes"]]
    assert starts == sorted(starts, reverse=True)
    assert starts[0] == T0 + 690 * MIN
