import io
import logging
import os
import shutil
import subprocess
import time
from collections.abc import Iterable
from pathlib import Path
from typing import IO

import cv2
import numpy as np
import pytest

from tests.fakes import STOP_SIGN
from trafficpi.core.coco import class_name
from trafficpi.vision.evidence import (
    AFTER_S,
    BEFORE_S,
    EvidenceFiles,
    EvidenceJob,
    EvidenceRecorder,
    FrameRing,
    RingFrame,
    clean_up,
    encode_clip,
    free_bytes,
)
from trafficpi.vision.profile import parse_compiled_profile
from trafficpi.vision.tracker import TrackUpdate

SEC = 1_000_000_000
SIGN = parse_compiled_profile({"stop_signs": [STOP_SIGN]}).stop_signs[0]


def frame_at(seconds: float, color: int = 90) -> np.ndarray:
    """A 640 x 360 RGB picture, as the camera gives them."""
    return np.full((360, 640, 3), color, np.uint8)


def vehicle(track_id: int = 7) -> TrackUpdate:
    return TrackUpdate(
        track_id, 2, class_name(2), 0.9, (0.4, 0.5, 0.5, 0.7), (0.0, 0.0), 10, 1.0, True
    )


class FakeEncoder:
    """Stands in for ffmpeg: keeps the frames it is given, and writes something to the path."""

    def __init__(self, ok: bool = True) -> None:
        self.ok = ok
        self.frames: list[np.ndarray] = []
        self.calls = 0
        self.fps: float | None = None

    def __call__(self, frames: Iterable[np.ndarray], fps: float, path: Path) -> bool:
        self.calls += 1
        self.fps = fps
        self.frames = [f.copy() for f in frames]
        if self.ok:
            path.write_bytes(b"not really a clip")
        return self.ok


def job(
    at_s: float, *, event_id: int = 5, deadline: float = 10**9, stem: str = "2026-10-09/stop-5"
) -> EvidenceJob:
    return EvidenceJob(
        event_id, 7, round(at_s * SEC), "Elm Street: rolling car #7", SIGN, stem, deadline
    )


def feed_seconds(
    recorder: EvidenceRecorder,
    start_s: float,
    end_s: float,
    fps: int = 30,
    tracks=None,  # type: ignore[no-untyped-def]
) -> None:
    """A camera's frames for a stretch of time; a recorder with no thread is drained as it goes."""
    for i in range(round(start_s * fps), round(end_s * fps)):
        recorder.feed(
            round(i / fps * SEC), frame_at(i / fps), tracks if tracks is not None else [vehicle()]
        )
        if i % fps == fps - 1 and not recorder._thread.is_alive():
            recorder.drain()


@pytest.fixture
def encoder() -> FakeEncoder:
    return FakeEncoder()


@pytest.fixture
def recorder(tmp_path: Path, encoder: FakeEncoder) -> EvidenceRecorder:
    return EvidenceRecorder(tmp_path, encoder=encoder, threaded=False)


# --- the frames in memory -----------------------------------------------------------------------


def ring_frame(seconds: float) -> RingFrame:
    return RingFrame(round(seconds * SEC), b"", ())


def test_the_ring_forgets_what_is_older_than_its_span_and_gives_a_window() -> None:
    ring = FrameRing(seconds=10)
    assert ring.newest_ns is None and len(ring) == 0
    for second in range(0, 25):
        ring.add(ring_frame(second))
    assert ring.newest_ns == 24 * SEC and len(ring) == 11  # 14 to 24 s: ten seconds and the newest
    assert [f.t_ns // SEC for f in ring.window(16 * SEC, 19 * SEC)] == [
        16,
        17,
        18,
        19,
    ]  # both ends in
    assert ring.window(0, 5 * SEC) == []  # gone
    assert [f.t_ns // SEC for f in ring.window(20 * SEC, 99 * SEC)] == [20, 21, 22, 23, 24]


# --- the recorder -------------------------------------------------------------------------------


def test_evidence_is_written_when_the_seconds_after_the_event_have_been_seen(
    recorder: EvidenceRecorder, encoder: FakeEncoder, tmp_path: Path
) -> None:
    feed_seconds(recorder, 0, 8)
    recorder.request(job(8.0))
    recorder.drain()
    assert (
        recorder.poll() == [] and recorder.waiting == 1
    )  # the three seconds after have not happened
    feed_seconds(recorder, 8, 10.9)
    recorder.drain()
    assert recorder.poll() == []  # nearly
    feed_seconds(recorder, 10.9, 11.5)
    recorder.drain()
    (done,) = recorder.poll()
    assert done == EvidenceFiles(
        5, "evidence/2026-10-09/stop-5.jpg", "evidence/2026-10-09/stop-5.mp4"
    )
    assert recorder.waiting == 0 and recorder.written == 1
    assert (tmp_path / done.snapshot).stat().st_size > 1_000  # type: ignore[arg-type]
    assert (tmp_path / done.clip).read_bytes() == b"not really a clip"  # type: ignore[arg-type]
    assert recorder.poll() == []  # told once


def test_the_clip_runs_from_six_seconds_before_to_three_after_at_half_the_cameras_rate(
    recorder: EvidenceRecorder, encoder: FakeEncoder
) -> None:
    feed_seconds(recorder, 0, 12)
    recorder.request(job(8.0))
    recorder.drain()
    assert recorder.poll()
    assert encoder.fps == 15
    assert 133 <= len(encoder.frames) <= 137  # 9 seconds at 15 a second
    assert (BEFORE_S, AFTER_S) == (6.0, 3.0)


def test_the_picture_is_the_moment_with_the_caption_the_stop_sign_and_the_vehicle_drawn(
    recorder: EvidenceRecorder, tmp_path: Path
) -> None:
    feed_seconds(recorder, 0, 12)
    recorder.request(job(8.0))
    recorder.drain()
    (done,) = recorder.poll()
    image = cv2.imread(str(tmp_path / done.snapshot))  # type: ignore[arg-type]
    assert image is not None and image.shape == (360, 640, 3)
    background = image[300, 20]  # nothing drawn there
    assert abs(int(background[0]) - 90) < 12  # the picture, as it was
    top_left = image[10:60, 5:300]
    assert (np.abs(top_left.astype(int) - 90) > 60).any()  # the caption's lettering
    vehicle_box = image[int(0.5 * 360) - 2 : int(0.5 * 360) + 3, int(0.4 * 640) : int(0.5 * 640)]
    assert (np.abs(vehicle_box.astype(int) - 90) > 60).any()  # the box round the vehicle
    stop_line = image[int(0.6 * 360), int(0.5 * 640) - 4 : int(0.5 * 640) + 4]
    assert (stop_line[:, 2].astype(int) > 150).any()  # the red of the stop line (BGR: red is last)


def test_the_picture_is_of_the_frame_nearest_the_crossing(
    recorder: EvidenceRecorder, tmp_path: Path
) -> None:
    for i in range(12 * 30):  # a picture that gets lighter every second
        recorder.feed(round(i / 30 * SEC), frame_at(i / 30, color=20 + 10 * (i // 30)), [])
        if i % 30 == 29:
            recorder.drain()
    recorder.request(job(8.0))
    recorder.drain()
    (done,) = recorder.poll()
    image = cv2.imread(str(tmp_path / done.snapshot))  # type: ignore[arg-type]
    assert image is not None
    assert abs(int(image[300, 20][0]) - 100) < 12  # 20 + 10 * 8: the frame at 8 s, not 2 or 11


def test_only_the_vehicle_that_was_judged_is_boxed(
    recorder: EvidenceRecorder, tmp_path: Path
) -> None:
    other = TrackUpdate(8, 2, "car", 0.9, (0.7, 0.55, 0.85, 0.75), (0.0, 0.0), 10, 1.0, True)
    feed_seconds(recorder, 0, 12, tracks=[vehicle(7), other])
    recorder.request(job(8.0))
    recorder.drain()
    (done,) = recorder.poll()
    image = cv2.imread(str(tmp_path / done.snapshot))  # type: ignore[arg-type]
    assert image is not None

    def drawn_along(box: tuple[float, float, float, float]) -> bool:
        x0, y0, x1, _ = box
        edge = image[int(y0 * 360) - 1 : int(y0 * 360) + 2, int(x0 * 640) : int(x1 * 640)]
        return bool((np.abs(edge.astype(int) - 90) > 60).any())

    assert drawn_along((0.4, 0.5, 0.5, 0.7)) is True  # the vehicle that was judged
    assert drawn_along((0.7, 0.55, 0.85, 0.75)) is False  # another one in the picture


def test_a_job_whose_seconds_never_come_is_finished_with_what_there_is_when_its_time_is_up(
    tmp_path: Path, encoder: FakeEncoder
) -> None:
    clock = [100.0]
    recorder = EvidenceRecorder(
        tmp_path, encoder=encoder, threaded=False, monotonic=lambda: clock[0]
    )
    feed_seconds(recorder, 0, 9)  # the camera stops at 9 s: the 3 seconds after 8 s never come
    recorder.request(job(8.0, deadline=110.0))
    recorder.drain()
    assert recorder.poll() == []
    clock[0] = 111.0
    recorder.drain()
    (done,) = recorder.poll()
    assert done.snapshot is not None and done.clip is not None
    assert (
        103 <= len(encoder.frames) <= 107
    )  # from 2 s to the 9 s that there are: 7 s at 15 a second


def test_nothing_to_show_gives_a_result_with_no_files_and_says_so(
    tmp_path: Path, encoder: FakeEncoder, caplog: pytest.LogCaptureFixture
) -> None:
    recorder = EvidenceRecorder(
        tmp_path, encoder=encoder, threaded=False, monotonic=lambda: 1_000.0
    )
    recorder.request(job(8.0, deadline=0.0))
    with caplog.at_level(logging.WARNING, logger="trafficpi.vision"):
        recorder.drain()
    assert recorder.poll() == [EvidenceFiles(5, None, None)]
    assert any("no frames to show for event 5" in r.getMessage() for r in caplog.records)
    assert encoder.calls == 0 and not (tmp_path / "evidence").exists()


def test_a_clip_that_could_not_be_made_leaves_the_picture_and_no_stray_files(
    tmp_path: Path,
) -> None:
    failing = FakeEncoder(ok=False)
    recorder = EvidenceRecorder(tmp_path, encoder=failing, threaded=False)
    feed_seconds(recorder, 0, 12)
    recorder.request(job(8.0))
    recorder.drain()
    (done,) = recorder.poll()
    assert done.snapshot is not None and done.clip is None
    assert sorted(p.name for p in (tmp_path / "evidence" / "2026-10-09").iterdir()) == [
        "stop-5.jpg"
    ]


def test_a_picture_that_could_not_be_written_is_not_named_and_the_clip_still_is(
    recorder: EvidenceRecorder, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(cv2, "imwrite", lambda *args, **kwargs: False)  # the encoder refuses
    feed_seconds(recorder, 0, 12)
    recorder.request(job(8.0))
    recorder.drain()
    (done,) = recorder.poll()
    assert done.snapshot is None and done.clip == "evidence/2026-10-09/stop-5.mp4"
    assert [p.name for p in (tmp_path / "evidence" / "2026-10-09").iterdir()] == ["stop-5.mp4"]


def test_an_encoder_that_crashes_does_not_take_the_recorder_down(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    def broken(frames: Iterable[np.ndarray], fps: float, path: Path) -> bool:
        path.write_bytes(b"half")
        raise RuntimeError("the encoder fell over")

    recorder = EvidenceRecorder(tmp_path, encoder=broken, threaded=False)
    feed_seconds(recorder, 0, 12)
    recorder.request(job(8.0))
    with caplog.at_level(logging.ERROR, logger="trafficpi.vision"):
        recorder.drain()
    (done,) = recorder.poll()
    assert done.snapshot is not None and done.clip is None
    assert [p.name for p in (tmp_path / "evidence" / "2026-10-09").iterdir()] == ["stop-5.jpg"]
    assert any("could not write" in r.getMessage() for r in caplog.records)


def test_a_folder_that_cannot_be_made_gives_no_files_once_and_does_not_block_the_next(
    tmp_path: Path, encoder: FakeEncoder, caplog: pytest.LogCaptureFixture
) -> None:
    (tmp_path / "evidence").write_text("a file where the folder should be")
    recorder = EvidenceRecorder(tmp_path, encoder=encoder, threaded=False)
    feed_seconds(recorder, 0, 12)
    recorder.request(job(8.0))
    recorder.request(job(8.5, event_id=6, stem="2026-10-09/stop-6"))
    with caplog.at_level(logging.ERROR, logger="trafficpi.vision"):
        recorder.drain()
        recorder.drain()  # nothing is tried a second time
    assert recorder.poll() == [EvidenceFiles(5, None, None), EvidenceFiles(6, None, None)]
    assert recorder.waiting == 0 and encoder.calls == 0
    assert [r.getMessage() for r in caplog.records].count(
        "evidence: could not write the evidence of event 5"
    ) == 1


def test_frames_are_thinned_to_the_clips_rate_and_dropped_not_waited_for(tmp_path: Path) -> None:
    recorder = EvidenceRecorder(tmp_path, encoder=FakeEncoder(), threaded=False)
    for i in range(60):  # 60 frames from a 30 fps camera, and nothing takes them
        recorder.feed(round(i / 30 * SEC), frame_at(0), [])
    assert recorder._incoming.qsize() == 30  # every other one
    started = time.perf_counter()
    for i in range(100):  # nothing takes them, so the queue fills and then frames are dropped
        recorder.feed(round((2 + i / 15) * SEC), frame_at(0), [])
    assert time.perf_counter() - started < 1.0  # the camera loop is never made to wait
    assert recorder._incoming.qsize() == 30
    assert recorder.frames_dropped == 100  # the 30 places were taken; the rest are gone


def test_the_recorder_does_its_work_on_a_thread_of_its_own(tmp_path: Path) -> None:
    # Twelve seconds of frames arrive in an instant, which the camera never does (it feeds 15 a
    # second): the queue must have room for all of them, or the thread's pace decides which frames
    # are dropped, the last ones among them, and the job then waits for seconds that never come.
    # That happened on a busy machine (100 to 150 of the 180 frames dropped, one run in two).
    recorder = EvidenceRecorder(tmp_path, encoder=FakeEncoder(), max_waiting=2_000)  # threaded
    try:
        feed_seconds(recorder, 0, 12)
        assert recorder.frames_dropped == 0
        recorder.request(job(8.0))
        done: list[EvidenceFiles] = []
        deadline = time.monotonic() + 30  # a thread starved of the CPU is slow, not wrong
        while not done and time.monotonic() < deadline:
            done = recorder.poll()
            time.sleep(0.02)
        assert [d.event_id for d in done] == [5]
    finally:
        recorder.close()
    assert not recorder._thread.is_alive()


def test_close_writes_what_still_waits_from_the_frames_in_hand(
    tmp_path: Path, encoder: FakeEncoder
) -> None:
    recorder = EvidenceRecorder(tmp_path, encoder=encoder, threaded=False)
    feed_seconds(recorder, 0, 9)  # a second after the event, when the service is stopped
    recorder.request(job(8.0))
    recorder.drain()
    assert recorder.poll() == [] and recorder.waiting == 1  # the seconds after it are not in yet
    recorder.close()
    (done,) = recorder.poll()
    assert done.snapshot is not None and done.clip is not None and recorder.waiting == 0
    assert 103 <= len(encoder.frames) <= 107  # 2 s to 9 s at 15 a second: all there was


# --- the clip itself ----------------------------------------------------------------------------

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")


def probe(path: Path) -> dict[str, str]:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-count_frames",
         "-show_entries", "stream=codec_name,pix_fmt,width,height,nb_read_frames",
         "-of", "default=noprint_wrappers=1", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout  # fmt: skip
    return dict(line.split("=", 1) for line in out.splitlines())


@needs_ffmpeg
def test_a_clip_is_h264_in_an_mp4_that_a_browser_plays(tmp_path: Path) -> None:
    frames = [np.full((360, 640, 3), i * 8, np.uint8) for i in range(30)]
    path = tmp_path / "clip.mp4"
    assert encode_clip(frames, 15.0, path) is True
    info = probe(path)
    assert info["codec_name"] == "h264" and info["pix_fmt"] == "yuv420p"  # what every browser takes
    assert (info["width"], info["height"]) == ("640", "360")
    assert info["nb_read_frames"] == "30"
    data = path.read_bytes()
    assert data[4:8] == b"ftyp"  # an MP4
    assert 0 < data.find(b"moov") < data.find(b"mdat")  # with its index first: it plays as it loads


@needs_ffmpeg
def test_a_picture_with_odd_sides_is_made_even_for_the_encoder(tmp_path: Path) -> None:
    path = tmp_path / "odd.mp4"
    assert encode_clip([np.zeros((361, 641, 3), np.uint8)] * 5, 15.0, path) is True
    assert (probe(path)["width"], probe(path)["height"]) == ("640", "360")


@needs_ffmpeg
def test_no_frames_make_no_clip_and_a_failure_is_reported_not_raised(tmp_path: Path) -> None:
    assert encode_clip([], 15.0, tmp_path / "none.mp4") is False
    assert not (tmp_path / "none.mp4").exists()
    unwritable = tmp_path / "no-such-folder" / "clip.mp4"
    assert encode_clip([np.zeros((360, 640, 3), np.uint8)] * 3, 15.0, unwritable) is False


class FailingFfmpeg:
    """A stand-in for the ffmpeg process that fails after leaving something behind."""

    returncode = 1

    def __init__(self, command: list[str], stdin: int, stderr: IO[bytes]) -> None:
        Path(command[-1]).write_bytes(b"half a clip")
        stderr.write(b"Error while encoding: no space left on device")
        self.stdin = io.BytesIO()

    def wait(self, timeout: float | None = None) -> int:
        return self.returncode


def test_an_ffmpeg_that_fails_is_a_failure_even_when_it_left_a_file_and_says_why(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/ffmpeg")
    monkeypatch.setattr(subprocess, "Popen", FailingFfmpeg)
    with caplog.at_level(logging.ERROR, logger="trafficpi.vision"):
        assert encode_clip([np.zeros((360, 640, 3), np.uint8)], 15.0, tmp_path / "a.mp4") is False
    said = [r.getMessage() for r in caplog.records]
    assert "evidence: ffmpeg failed: Error while encoding: no space left on device" in said


def test_an_ffmpeg_that_takes_too_long_is_stopped_and_is_a_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    class Stuck(FailingFfmpeg):
        killed = False

        def wait(self, timeout: float | None = None) -> int:
            if timeout is not None:
                raise subprocess.TimeoutExpired("ffmpeg", timeout)
            return -9

        def kill(self) -> None:
            Stuck.killed = True

    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/ffmpeg")
    monkeypatch.setattr(subprocess, "Popen", Stuck)
    with caplog.at_level(logging.ERROR, logger="trafficpi.vision"):
        assert encode_clip([np.zeros((360, 640, 3), np.uint8)], 15.0, tmp_path / "a.mp4") is False
    assert Stuck.killed
    assert any("took over a minute" in r.getMessage() for r in caplog.records)


def test_without_ffmpeg_there_is_no_clip_and_one_warning(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    from trafficpi.vision import evidence

    monkeypatch.setattr(evidence.shutil, "which", lambda name: None)
    monkeypatch.setattr(evidence, "_warned_no_ffmpeg", False)
    frames = [np.zeros((360, 640, 3), np.uint8)]
    with caplog.at_level(logging.WARNING, logger="trafficpi.vision"):
        assert encode_clip(frames, 15.0, tmp_path / "a.mp4") is False
        assert encode_clip(frames, 15.0, tmp_path / "b.mp4") is False
    assert sum("ffmpeg is not installed" in r.getMessage() for r in caplog.records) == 1


# --- keeping it in check --------------------------------------------------------------------------

DAY = 86_400


def make(
    folder: Path, name: str, age_days: float, size: int = 100, now: float = 1_000_000_000
) -> Path:
    path = folder / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)
    os.utime(path, (now - age_days * DAY, now - age_days * DAY))
    return path


def test_evidence_older_than_the_keep_time_is_deleted_and_the_rest_stays(tmp_path: Path) -> None:
    now = 1_000_000_000
    folder = tmp_path / "evidence"
    old = make(folder, "2026-09-01/stop-1.jpg", 40, now=now)
    old_clip = make(folder, "2026-09-01/stop-1.mp4", 40, now=now)
    recent = make(folder, "2026-10-08/stop-2.jpg", 1, now=now)
    removed = clean_up(folder, keep_days=30, max_bytes=10**9, now_s=now)
    assert removed == (2, 200)
    assert not old.exists() and not old_clip.exists() and recent.exists()
    assert not (folder / "2026-09-01").exists()  # and the empty day's folder goes too
    assert (folder / "2026-10-08").exists()


def test_a_file_goes_when_it_is_older_than_the_keep_time_and_not_before(tmp_path: Path) -> None:
    now = 1_000_000_000
    folder = tmp_path / "evidence"
    just_inside = make(folder, "d/inside.jpg", 29.9, now=now)
    just_outside = make(folder, "d/outside.jpg", 30.1, now=now)
    assert clean_up(folder, keep_days=30, max_bytes=10**9, now_s=now) == (1, 100)
    assert just_inside.exists() and not just_outside.exists()


def test_when_there_is_too_much_the_oldest_goes_first_until_it_fits(tmp_path: Path) -> None:
    now = 1_000_000_000
    folder = tmp_path / "evidence"
    files = [
        make(folder, f"d/{i}.jpg", age, size=1_000, now=now)
        for i, age in enumerate([5, 4, 3, 2, 1])
    ]
    removed = clean_up(folder, keep_days=30, max_bytes=2_500, now_s=now)
    assert removed == (3, 3_000)
    assert [f.exists() for f in files] == [False, False, False, True, True]


def test_a_half_written_file_is_left_alone_while_it_is_new_and_removed_when_it_is_a_leftover(
    tmp_path: Path,
) -> None:
    now = 1_000_000_000
    folder = tmp_path / "evidence"
    fresh = make(folder, "d/stop-1.mp4.part", 0.0001, now=now)  # 8 seconds old
    stale = make(folder, "d/stop-2.mp4.part", 0.5, now=now)
    make(folder, "d/stop-3.jpg", 1, now=now)
    clean_up(folder, keep_days=30, max_bytes=10**9, now_s=now)
    assert fresh.exists() and not stale.exists()


def test_there_is_nothing_to_clean_where_there_is_no_folder(tmp_path: Path) -> None:
    assert clean_up(tmp_path / "evidence", keep_days=30, max_bytes=1) == (0, 0)


HOUR = 3_600


def hours_old(folder: Path, name: str, hours: float, now: float) -> Path:
    path = make(folder, name, 0, now=now)
    os.utime(path, (now - hours * HOUR, now - hours * HOUR))
    return path


OUTCOMES = {1: "rolling", 2: "did_not_stop", 3: "indeterminate", 4: "stopped"}
LIMITS = {"rolling": 3, "did_not_stop": 48}


def test_a_clip_goes_when_it_is_older_than_its_outcome_allows_and_its_picture_stays(
    tmp_path: Path,
) -> None:
    now = 1_000_000_000
    folder = tmp_path / "evidence"
    clips = {
        "rolling_old": hours_old(folder, "d/stop-1.mp4", 3.1, now),
        "rolling_new": hours_old(folder, "d/stop-5.mp4", 2.9, now),
        "dns_old": hours_old(folder, "d/stop-2.mp4", 49, now),
        "dns_new": hours_old(folder, "d/stop-6.mp4", 47, now),
    }
    outcomes = {1: "rolling", 5: "rolling", 2: "did_not_stop", 6: "did_not_stop"}
    pictures = [hours_old(folder, f"d/stop-{i}.jpg", 100, now) for i in (1, 2)]
    removed = clean_up(
        folder,
        keep_days=30,
        max_bytes=10**9,
        clip_hours=LIMITS,
        outcomes_of=lambda ids: {i: outcomes[i] for i in ids if i in outcomes},
        now_s=now,
    )
    assert removed == (2, 200)
    assert [name for name, path in clips.items() if path.exists()] == ["rolling_new", "dns_new"]
    assert all(path.exists() for path in pictures)  # the pictures are the days' business


def test_a_clip_with_no_time_of_its_own_is_judged_by_the_days_alone(tmp_path: Path) -> None:
    now = 1_000_000_000
    folder = tmp_path / "evidence"
    could_not_tell = hours_old(folder, "d/stop-3.mp4", 100, now)
    unknown = hours_old(folder, "d/stop-9.mp4", 100, now)  # no such event any more
    ancient = hours_old(folder, "d/stop-4.mp4", 31 * 24, now)
    clean_up(
        folder,
        keep_days=30,
        max_bytes=10**9,
        clip_hours=LIMITS,
        outcomes_of=lambda ids: {i: OUTCOMES[i] for i in ids if i in OUTCOMES},
        now_s=now,
    )
    assert could_not_tell.exists() and unknown.exists() and not ancient.exists()


def test_the_days_still_cap_a_clip_whose_hours_are_longer(tmp_path: Path) -> None:
    now = 1_000_000_000
    folder = tmp_path / "evidence"
    clip = hours_old(folder, "d/stop-2.mp4", 3 * 24 + 1, now)
    clean_up(
        folder,
        keep_days=3,
        max_bytes=10**9,
        clip_hours={"did_not_stop": 24 * 30},
        outcomes_of=lambda ids: {2: "did_not_stop"},
        now_s=now,
    )
    assert not clip.exists()


def test_a_clip_is_only_asked_about_when_it_is_a_finished_one(tmp_path: Path) -> None:
    now = 1_000_000_000
    folder = tmp_path / "evidence"
    hours_old(folder, "d/stop-1.mp4", 10, now)
    hours_old(folder, "d/stop-2.part.mp4", 0.0001, now)  # still being written
    hours_old(folder, "d/stop-3.jpg", 10, now)
    hours_old(folder, "d/notes.mp4", 10, now)
    asked: list[set[int]] = []
    clean_up(
        folder,
        keep_days=30,
        max_bytes=10**9,
        clip_hours=LIMITS,
        outcomes_of=lambda ids: (asked.append(set(ids)), {})[1],
        now_s=now,
    )
    assert asked == [{1}]


def test_when_the_outcomes_cannot_be_read_the_days_are_still_kept_to(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    now = 1_000_000_000
    folder = tmp_path / "evidence"
    fresh = hours_old(folder, "d/stop-1.mp4", 10, now)
    ancient = hours_old(folder, "d/stop-2.mp4", 31 * 24, now)

    def locked(ids: object) -> dict[int, str]:
        raise OSError("database is locked")

    with caplog.at_level(logging.ERROR, logger="trafficpi.vision"):
        clean_up(
            folder,
            keep_days=30,
            max_bytes=10**9,
            clip_hours=LIMITS,
            outcomes_of=locked,
            now_s=now,
        )
    assert fresh.exists() and not ancient.exists()
    assert any("could not read the clips" in r.getMessage() for r in caplog.records)


def test_no_hours_given_means_the_old_behaviour(tmp_path: Path) -> None:
    now = 1_000_000_000
    folder = tmp_path / "evidence"
    clip = hours_old(folder, "d/stop-1.mp4", 10, now)
    clean_up(folder, keep_days=30, max_bytes=10**9, now_s=now)
    assert clip.exists()


# --- the disk floor (plan.md D64) ---------------------------------------------------------------


def test_no_evidence_is_written_while_the_disk_has_less_free_than_the_floor(
    tmp_path: Path, encoder: FakeEncoder, caplog: pytest.LogCaptureFixture
) -> None:
    free = [500_000_000]
    recorder = EvidenceRecorder(
        tmp_path,
        encoder=encoder,
        threaded=False,
        min_free_bytes=1_000_000_000,
        free_of=lambda path: free[0],
    )
    feed_seconds(recorder, 0, 12)
    recorder.request(job(8.0, event_id=1))
    recorder.request(job(8.0, event_id=2, stem="2026-10-09/stop-2"))
    with caplog.at_level("WARNING", logger="trafficpi.vision"):
        recorder.drain()
    done = recorder.poll()
    assert done == [EvidenceFiles(1, None, None), EvidenceFiles(2, None, None)]
    assert recorder.skipped_for_space == 2 and recorder.written == 0 and encoder.calls == 0
    assert not (tmp_path / "evidence").exists(), "not even a folder"
    warnings = [r.getMessage() for r in caplog.records if "kept free" in r.getMessage()]
    assert len(warnings) == 1 and "0.5 GB is free" in warnings[0], "said once, not for every pass"

    free[0] = 1_000_000_000  # room again: exactly the floor is enough
    recorder.request(job(8.0, event_id=3, stem="2026-10-09/stop-3"))
    recorder.drain()
    (written,) = recorder.poll()
    assert written.snapshot == "evidence/2026-10-09/stop-3.jpg" and recorder.written == 1


def test_the_warning_about_a_full_disk_comes_again_an_hour_later(
    tmp_path: Path, encoder: FakeEncoder, caplog: pytest.LogCaptureFixture
) -> None:
    now = [0.0]
    recorder = EvidenceRecorder(
        tmp_path,
        encoder=encoder,
        threaded=False,
        min_free_bytes=10,
        free_of=lambda path: 1,
        monotonic=lambda: now[0],
    )
    feed_seconds(recorder, 0, 12)
    with caplog.at_level("WARNING", logger="trafficpi.vision"):
        for event_id, at in [(1, 0.0), (2, 100.0), (3, 3_601.0)]:
            now[0] = at
            recorder.request(job(8.0, event_id=event_id, deadline=0.0))
            recorder.drain()
    assert len([r for r in caplog.records if "kept free" in r.getMessage()]) == 2
    assert recorder.skipped_for_space == 3


def test_not_knowing_how_much_is_free_does_not_keep_evidence_from_anyone(
    tmp_path: Path, encoder: FakeEncoder
) -> None:
    def broken(path: Path) -> int:
        raise OSError("no such disk")

    recorder = EvidenceRecorder(
        tmp_path, encoder=encoder, threaded=False, min_free_bytes=10**12, free_of=broken
    )
    feed_seconds(recorder, 0, 12)
    recorder.request(job(8.0))
    recorder.drain()
    assert recorder.poll()[0].snapshot is not None and recorder.skipped_for_space == 0


def test_the_floor_is_the_nearest_folder_that_exists_when_the_evidence_folder_does_not(
    tmp_path: Path,
) -> None:
    assert free_bytes(tmp_path / "evidence" / "2026-10-09") == free_bytes(tmp_path) > 0


def test_a_disk_that_is_too_full_loses_its_oldest_evidence_first_until_there_is_room(
    tmp_path: Path,
) -> None:
    now = 1_000_000_000
    folder = tmp_path / "evidence"
    files = [
        make(folder, f"d/{i}.jpg", age, size=1_000_000, now=now)
        for i, age in enumerate([4, 3, 2, 1])
    ]
    # 1 MB is free and 3.5 MB are wanted: three files (1 MB each) must go, the newest may stay
    removed = clean_up(
        folder,
        keep_days=30,
        max_bytes=10**9,
        now_s=now,
        min_free_bytes=3_500_000,
        free_of=lambda path: 1_000_000,
    )
    assert removed == (3, 3_000_000)
    assert [f.exists() for f in files] == [False, False, False, True]


def test_enough_room_deletes_nothing_for_the_floor_and_what_the_age_rules_freed_counts(
    tmp_path: Path,
) -> None:
    now = 1_000_000_000
    folder = tmp_path / "evidence"
    old = make(folder, "d/old.jpg", 40, size=2_000_000, now=now)  # goes by its age
    kept = [make(folder, f"d/{i}.jpg", 1, size=1_000_000, now=now) for i in range(2)]
    # the disk said 1 MB free after the old file was deleted: 1 MB short of 2 MB, so one more goes
    removed = clean_up(
        folder,
        keep_days=30,
        max_bytes=10**9,
        now_s=now,
        min_free_bytes=2_000_000,
        free_of=lambda path: 1_000_000,
    )
    assert removed == (2, 3_000_000) and not old.exists()
    assert [k.exists() for k in kept] == [False, True] or [k.exists() for k in kept] == [
        True,
        False,
    ]
    assert clean_up(folder, keep_days=30, max_bytes=10**9, now_s=now, min_free_bytes=0) == (0, 0)
