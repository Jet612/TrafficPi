import pytest

from trafficpi.vision.metrics import (
    StageStats,
    decode_throttled,
    percentile,
    soc_temperature_c,
    summarize,
)


def test_percentile_interpolates() -> None:
    values = [10.0, 20.0, 30.0, 40.0]
    assert percentile(values, 0) == 10
    assert percentile(values, 1) == 40
    assert percentile(values, 0.5) == pytest.approx(25)
    assert percentile([], 0.5) == 0


def test_summarize() -> None:
    s = summarize([1.0, 2.0, 3.0, 4.0, 100.0])
    assert (s.count, s.mean, s.p50, s.max) == (5, 22.0, 3.0, 100.0)
    assert s.p95 == pytest.approx(80.8)
    assert summarize([]).count == 0


def test_stage_stats_collects_per_stage() -> None:
    stats = StageStats()
    for ms in (10, 20, 30):
        stats.add("infer", ms)
    stats.add("capture", 5)
    assert stats.stages() == ["infer", "capture"]
    assert stats.summary("infer").mean == 20
    assert stats.summary("missing").count == 0
    stats.clear()
    assert stats.stages() == []


def test_throttle_flags_are_decoded() -> None:
    assert decode_throttled(0) == ([], [])
    now, since = decode_throttled(0x50005)
    assert now == ["under-voltage", "throttled"]
    assert since == ["under-voltage occurred", "throttling occurred"]


def test_soc_temperature_is_a_plausible_number_or_none() -> None:
    temp = soc_temperature_c()
    assert temp is None or 0 < temp < 120


def test_stage_stats_memory_is_bounded_and_the_summary_stays_accurate() -> None:
    stats = StageStats(capacity=500)
    for i in range(50_000):  # a 0..99 ms sawtooth: mean 49.5, median about 49.5, p95 about 94
        stats.add("loop", float(i % 100))
    s = stats.summary("loop")
    assert s.count == 50_000 and s.max == 99.0 and s.mean == pytest.approx(49.5)
    assert s.p50 == pytest.approx(49.5, abs=6) and s.p95 == pytest.approx(94, abs=4)
    assert len(stats._series["loop"].reservoir) == 500
