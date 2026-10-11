"""The health recorder: a sample every few seconds becomes one row a minute."""

from trafficpi.vision.health import HealthRecorder, HealthRow


def sample(
    recorder: HealthRecorder,
    at_s: float,
    *,
    temp: float | None = 70.0,
    cpu: float | None = 20.0,
    fps: float = 30.0,
    ok: bool = True,
    throttled: int | None = 0,
) -> list[HealthRow]:
    return recorder.add(
        int(at_s * 1000),
        temp_c=temp,
        cpu_percent=cpu,
        fps=fps,
        camera_ok=ok,
        throttled=throttled,
    )


def test_the_samples_of_a_minute_become_one_row_when_the_next_minute_starts() -> None:
    recorder = HealthRecorder()
    for i in range(12):  # the minute from 120 s to 179 s, a sample every 5 s
        assert sample(recorder, 120 + 5 * i, temp=70 + i, cpu=10 + i, fps=29 + i % 3) == []
    assert sample(recorder, 180) == [
        HealthRow(
            minute_start=120,
            temp_c_max=81.0,
            cpu_percent=15.5,  # the mean of 10 to 21
            fps_mean=30.0,
            fps_min=29.0,
            throttled=0,
            samples=12,
            camera_ok_samples=12,
        )
    ]


def test_minutes_with_nothing_in_them_have_no_row_and_the_gap_is_not_filled() -> None:
    recorder = HealthRecorder()
    sample(recorder, 120)  # minute 2
    rows = sample(recorder, 330)  # minute 5: the service was not running for two minutes
    assert [row.minute_start for row in rows] == [120]
    assert [row.minute_start for row in recorder.flush() or []] == [300]


def test_flush_gives_the_minute_in_progress_once() -> None:
    recorder = HealthRecorder()
    assert recorder.flush() == []
    sample(recorder, 125)
    sample(recorder, 130)
    rows = recorder.flush()
    assert [(r.minute_start, r.samples) for r in rows] == [(120, 2)]
    assert recorder.flush() == []


def test_the_hottest_sample_is_kept_not_the_mean_and_a_missing_reading_is_skipped() -> None:
    recorder = HealthRecorder()
    sample(recorder, 120, temp=70.0)
    sample(recorder, 125, temp=84.4)
    sample(recorder, 130, temp=None)
    sample(recorder, 135, temp=75.0)
    assert recorder.flush()[0].temp_c_max == 84.4


def test_no_temperature_at_all_is_none_not_zero() -> None:
    recorder = HealthRecorder()
    sample(recorder, 120, temp=None, cpu=None)
    row = recorder.flush()[0]
    assert row.temp_c_max is None and row.cpu_percent is None


def test_the_frame_rate_counts_only_while_the_camera_works() -> None:
    recorder = HealthRecorder()
    sample(recorder, 120, fps=30.0)
    sample(recorder, 125, fps=0.0, ok=False)  # the camera is down: not a frame rate of 0
    sample(recorder, 130, fps=28.0)
    row = recorder.flush()[0]
    assert (row.fps_mean, row.fps_min) == (29.0, 28.0)
    assert (row.samples, row.camera_ok_samples) == (3, 2)


def test_a_minute_with_no_working_camera_has_no_frame_rate() -> None:
    recorder = HealthRecorder()
    sample(recorder, 120, fps=0.0, ok=False)
    sample(recorder, 125, fps=0.0, ok=False)
    row = recorder.flush()[0]
    assert (row.fps_mean, row.fps_min, row.camera_ok_samples) == (None, None, 0)


def test_the_throttle_flags_of_the_moment_are_added_up_and_the_old_ones_ignored() -> None:
    """Bits 0 to 3 of `vcgencmd get_throttled` are the present; 16 to 19 stay set since the boot
    and say nothing about this minute."""
    recorder = HealthRecorder()
    sample(recorder, 120, throttled=0xE0000)  # only what happened since the boot
    assert recorder.flush()[0].throttled == 0
    sample(recorder, 180, throttled=0x0)
    sample(recorder, 185, throttled=0x80008)  # the soft limit now
    sample(recorder, 190, throttled=0x5)  # under-voltage and a capped clock
    sample(recorder, 195, throttled=None)  # vcgencmd did not answer
    assert recorder.flush()[0].throttled == 0b1101


def test_time_going_backwards_ends_the_minute_and_starts_another() -> None:
    """A clock that is set back must not mix two minutes into one row."""
    recorder = HealthRecorder()
    sample(recorder, 600)
    rows = sample(recorder, 300)
    assert [row.minute_start for row in rows] == [600]
    assert [row.minute_start for row in recorder.flush()] == [300]


def test_the_mean_is_rounded_to_a_tenth() -> None:
    recorder = HealthRecorder()
    for i, cpu in enumerate((10.0, 10.0, 11.0)):
        sample(recorder, 120 + 5 * i, cpu=cpu, fps=30.0 + i / 7)
    row = recorder.flush()[0]
    assert row.cpu_percent == 10.3 and row.fps_mean == 30.1
