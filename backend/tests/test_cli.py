import argparse
import contextlib
import os
import shutil
import socket
import threading
import time
from pathlib import Path

import cv2
import httpx
import numpy as np
import pytest

from tests.fakes import STOP_SIGN
from trafficpi.db.store import open_store
from trafficpi.vision import cli
from trafficpi.vision.shutdown import StopRequest


def test_size_parsing() -> None:
    assert cli._size("1280x720") == (1280, 720)
    assert cli._size("640X360") == (640, 360)
    with pytest.raises(argparse.ArgumentTypeError):
        cli._size("big")
    assert cli._size_or_auto("auto") is None
    assert cli._size_or_auto("2304x1296") == (2304, 1296)


def test_defaults_describe_a_fixed_roadside_view() -> None:
    args = cli.build_parser().parse_args(["dry-run"])
    settings = cli._camera_settings(args)
    assert settings.sensor_size == (2304, 1296)  # full field of view
    assert settings.lens_position == 0.0  # focus fixed at infinity
    assert settings.exposure_mode == "short"
    assert settings.channel_order == "rgb"
    assert (args.seconds, args.threshold, args.model) == (30, 0.25, "yolov8s")


def test_camera_options_are_passed_through() -> None:
    args = cli.build_parser().parse_args(
        [
            "record",
            "--sensor-size",
            "auto",
            "--lens-position",
            "auto",
            "--fps",
            "15",
            "--gain",
            "2",
            "--no-video",
        ]
    )
    settings = cli._camera_settings(args)
    assert settings.sensor_size is None and settings.lens_position is None
    assert (settings.frame_rate, settings.analogue_gain) == (15, 2)
    assert args.no_video and args.seconds == 60


def test_throttle_text() -> None:
    assert cli._flags(None) == "n/a"
    assert cli._flags(0) == "none"
    assert (
        cli._flags(0x50005)
        == "NOW: under-voltage, throttled; since boot: under-voltage occurred, throttling occurred"
    )


# --- leaving the process ---------------------------------------------------------------------


def run_entry(monkeypatch: pytest.MonkeyPatch, main) -> list[int]:  # type: ignore[no-untyped-def]
    exits: list[int] = []
    monkeypatch.setattr(os, "_exit", exits.append)
    monkeypatch.setattr(cli, "main", main)
    cli.entry()
    return exits


def test_the_console_script_exits_directly_with_the_codes_of_main(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert run_entry(monkeypatch, lambda: 0) == [0]
    assert run_entry(monkeypatch, lambda: 3) == [3]


def test_argparse_exits_keep_their_codes(monkeypatch: pytest.MonkeyPatch) -> None:
    def exit_with(code):  # type: ignore[no-untyped-def]
        def main() -> int:
            raise SystemExit(code)

        return main

    assert run_entry(monkeypatch, exit_with(2)) == [2]  # a usage error
    assert run_entry(monkeypatch, exit_with(None)) == [0]  # --help
    assert run_entry(monkeypatch, exit_with("message")) == [1]


def test_a_crash_is_reported_with_a_traceback_and_exit_code_1(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def main() -> int:
        raise ValueError("boom")

    assert run_entry(monkeypatch, main) == [1]
    assert "ValueError: boom" in capsys.readouterr().err


# --- the run command ---------------------------------------------------------------------------


class QuietDetector:
    def detect(self, image: np.ndarray) -> list:  # type: ignore[type-arg]
        return []

    def close(self) -> None: ...

    def __enter__(self) -> "QuietDetector":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


@pytest.fixture
def clip(tmp_path: Path) -> Path:
    path = tmp_path / "clip.mp4"
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter.fourcc(*"mp4v"), 30.0, (320, 180))
    for i in range(20):
        writer.write(np.full((180, 320, 3), 8 * i, np.uint8))
    writer.release()
    return path


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def run_in_thread(argv: list[str]) -> tuple[threading.Thread, StopRequest, list[int]]:
    args = cli.build_parser().parse_args(argv)
    args.stop = StopRequest()
    result: list[int] = []
    thread = threading.Thread(target=lambda: result.append(args.func(args)))
    thread.start()
    return thread, args.stop, result


def wait_for(url: str, ready, seconds: float = 20) -> dict:  # type: ignore[no-untyped-def,type-arg]
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            body = httpx.get(url, timeout=2).json()
            if ready(body):
                return body
        except httpx.HTTPError:
            pass
        time.sleep(0.1)
    raise AssertionError(f"{url} never became ready")


def test_run_replays_a_clip_and_serves_the_live_view(
    tmp_path: Path, clip: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "_make_detector", lambda args: (QuietDetector(), Path("quiet.hef")))
    monkeypatch.setenv("TRAFFICPI_DATA_DIR", str(tmp_path / "data"))
    port = free_port()
    thread, stop, result = run_in_thread(["run", "--replay", str(clip), "--port", str(port)])
    try:
        base = f"http://127.0.0.1:{port}"
        health = wait_for(f"{base}/health", lambda h: h["frames"] > 40)  # more than one loop
        assert health["status"] == "ok"
        state = httpx.get(f"{base}/state").json()
        assert state["detector"] == "quiet" and state["camera"]["ok"]
        assert httpx.get(f"{base}/snapshot.jpg").status_code == 200  # the latest frame
    finally:
        stop.request()
        thread.join(timeout=20)
    assert result == [0] and not thread.is_alive()
    # Replayed traffic goes to its own database, never into the real counts.
    assert (tmp_path / "data" / "replay" / "trafficpi.db").exists()
    assert not (tmp_path / "data" / "trafficpi.db").exists()


def test_run_with_an_explicit_data_dir_uses_exactly_that(
    tmp_path: Path, clip: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "_make_detector", lambda args: (QuietDetector(), Path("quiet.hef")))
    port = free_port()
    argv = ["run", "--replay", str(clip), "--port", str(port), "--data-dir", str(tmp_path / "here")]
    thread, stop, result = run_in_thread(argv)
    try:
        wait_for(f"http://127.0.0.1:{port}/health", lambda h: h["frames"] > 3)
    finally:
        stop.request()
        thread.join(timeout=20)
    assert result == [0]
    assert open_store(tmp_path / "here").event_count() == 0


def test_run_reports_a_port_that_is_taken(
    tmp_path: Path, clip: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "_make_detector", lambda args: (QuietDetector(), Path("quiet.hef")))
    with socket.socket() as taken:
        taken.bind(("127.0.0.1", 0))
        taken.listen()
        port = taken.getsockname()[1]
        args = cli.build_parser().parse_args(
            ["run", "--replay", str(clip), "--port", str(port), "--data-dir", str(tmp_path)]
        )
        args.stop = StopRequest()
        with pytest.raises(RuntimeError, match="did not start"):
            args.func(args)


def test_run_demo_needs_no_camera_or_accelerator_and_keeps_its_own_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def no_accelerator(args):  # type: ignore[no-untyped-def]
        raise AssertionError("the demo must not open the accelerator")

    monkeypatch.setattr(cli, "_make_detector", no_accelerator)
    monkeypatch.setenv("TRAFFICPI_DATA_DIR", str(tmp_path / "data"))
    port = free_port()
    thread, stop, result = run_in_thread(["run", "--demo", "--port", str(port)])
    try:
        base = f"http://127.0.0.1:{port}"
        wait_for(f"{base}/health", lambda h: h["frames"] > 90)
        state = httpx.get(f"{base}/state").json()
        assert state["detector"] == "demo" and state["camera"]["ok"]
        assert httpx.get(f"{base}/snapshot.jpg").status_code == 200
    finally:
        stop.request()
        thread.join(timeout=20)
    assert result == [0]
    assert (tmp_path / "data" / "demo" / "trafficpi.db").exists()
    assert not (tmp_path / "data" / "trafficpi.db").exists()


def test_run_demo_keeps_a_picture_and_a_clip_of_a_pass_that_needs_a_look(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The whole of it: the demo's traffic, a stop sign in the profile, the service given the data
    directory by the command, the files written, and where they are noted in the database."""
    monkeypatch.setattr(cli, "_make_detector", lambda args: pytest.fail("the demo needs none"))
    data = tmp_path / "data"
    store = open_store(data)
    store.set_profile({"stop_signs": [STOP_SIGN]})  # the demo's cars do not stop at it
    store.close()
    port = free_port()
    thread, stop, result = run_in_thread(
        ["run", "--demo", "--port", str(port), "--data-dir", str(data)]
    )
    try:
        health = wait_for(
            f"http://127.0.0.1:{port}/health", lambda h: h["evidence_written"] >= 1, seconds=60
        )
        assert health["evidence_frames_dropped"] == 0
    finally:
        stop.request()
        thread.join(timeout=30)
    assert result == [0]
    store = open_store(data)
    kept = [e for e in store.recent_events(type="stop", limit=50) if e.evidence_path]
    assert kept, "a pass was judged, and its picture is noted in the database"
    picture = data / str(kept[0].evidence_path)
    assert picture.read_bytes()[:2] == b"\xff\xd8"  # a JPEG
    if shutil.which("ffmpeg"):
        clip = data / str((kept[0].details or {}).get("clip"))
        assert clip.read_bytes()[4:8] == b"ftyp"  # an MP4
    store.close()


def test_demo_and_replay_cannot_be_combined(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as refused:
        cli.build_parser().parse_args(["run", "--demo", "--replay", "clip.mp4"])
    assert refused.value.code == 2
    assert "not allowed with" in capsys.readouterr().err


def test_run_tells_systemd_it_is_ready_and_keeps_the_watchdog_fed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Under Type=notify with WatchdogSec the service must say READY=1 once it is up, then ping
    for as long as its camera loop moves, and keep systemd's status line current (D27)."""
    monkeypatch.setattr(cli, "_make_detector", lambda args: pytest.fail("the demo needs none"))
    monkeypatch.setenv("TRAFFICPI_DATA_DIR", str(tmp_path / "data"))
    systemd = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    systemd.bind(str(tmp_path / "notify"))
    systemd.settimeout(0.2)
    monkeypatch.setenv("NOTIFY_SOCKET", str(tmp_path / "notify"))
    monkeypatch.setenv("WATCHDOG_USEC", "800000")  # 0.8 s: a ping every 0.4 s
    port = free_port()
    thread, stop, result = run_in_thread(["run", "--demo", "--port", str(port)])
    messages: list[str] = []
    try:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline and not (
            "READY=1" in messages
            and messages.count("WATCHDOG=1") >= 3
            and any(m.startswith("STATUS=counting at") for m in messages)
        ):
            with contextlib.suppress(TimeoutError):
                messages.append(systemd.recv(4096).decode())
        assert "READY=1" in messages[:2]  # first, or just after the status line
        assert messages.count("WATCHDOG=1") >= 3, messages
        assert any(m.startswith("STATUS=counting at") and "fps" in m for m in messages), messages
    finally:
        stop.request()
        thread.join(timeout=20)
        systemd.close()
    assert result == [0]
