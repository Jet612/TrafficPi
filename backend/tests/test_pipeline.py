from tests.fakes import FakeDetector, FakeSource
from trafficpi.vision import pipeline


def test_run_collects_counts_stats_and_reports() -> None:
    seen: list[int] = []
    windows: list[pipeline.WindowReport] = []
    report = pipeline.run(
        FakeSource(),
        FakeDetector(),
        seconds=0.5,
        report_every=0.2,
        on_frame=lambda f, d: seen.append(f.index),
        on_report=windows.append,
    )
    assert report.frames == len(seen) > 20
    assert report.detections["car"] == report.detections["person"] == report.frames
    assert report.skipped == report.frames // 10 or report.skipped == report.frames // 10 - 1
    infer = report.stats.summary(pipeline.INFER)
    assert (infer.count, infer.mean) == (report.frames, 12.0)
    assert report.stats.summary(pipeline.LOOP).mean >= 4  # at least the camera's 4 ms wait
    assert report.stats.summary(pipeline.INTERVAL).count == report.frames - 1
    assert 0 < report.fps < 300
    assert len(windows) >= 2
    assert sum(w.frames for w in windows) <= report.frames
    assert all(w.run_elapsed_s >= 0.2 for w in windows)
    assert windows[0].stage_means_ms[pipeline.INFER] == 12.0
