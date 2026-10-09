"""systemd's notification protocol and the heartbeat built on it (plan.md D27)."""

import logging
import os
import socket
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from trafficpi.core.notify import Heartbeat, notify, watchdog_period


@pytest.fixture
def systemd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[socket.socket]:
    """A socket that stands in for systemd's $NOTIFY_SOCKET."""
    path = tmp_path / "notify"
    server = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    server.bind(str(path))
    server.settimeout(2.0)
    monkeypatch.setenv("NOTIFY_SOCKET", str(path))
    yield server
    server.close()


def received(server: socket.socket) -> list[str]:
    """Everything that has arrived so far, without waiting for more."""
    server.setblocking(False)
    messages = []
    try:
        while True:
            messages.append(server.recv(4096).decode())
    except BlockingIOError:
        return messages


# --- the protocol -------------------------------------------------------------------------------


def test_a_message_reaches_the_socket(systemd: socket.socket) -> None:
    assert notify("READY=1") is True
    assert systemd.recv(4096) == b"READY=1"


def test_several_assignments_travel_in_one_datagram(systemd: socket.socket) -> None:
    assert notify("STATUS=counting at 30 fps\nWATCHDOG=1") is True
    assert systemd.recv(4096) == b"STATUS=counting at 30 fps\nWATCHDOG=1"


def test_an_abstract_socket_address_is_understood(monkeypatch: pytest.MonkeyPatch) -> None:
    name = f"trafficpi-test-{os.getpid()}-{time.monotonic_ns()}"
    server = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    server.bind("\0" + name)
    server.settimeout(2.0)
    monkeypatch.setenv("NOTIFY_SOCKET", "@" + name)
    try:
        assert notify("READY=1") is True
        assert server.recv(4096) == b"READY=1"
    finally:
        server.close()


def test_outside_systemd_there_is_nobody_to_tell(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NOTIFY_SOCKET", raising=False)
    assert notify("READY=1") is False


def test_a_socket_that_is_gone_is_not_an_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NOTIFY_SOCKET", str(tmp_path / "nobody-listens-here"))
    assert notify("READY=1") is False


@pytest.mark.parametrize("address", ["", "vsock:2:1234", "relative/path", "notify"])
def test_an_address_of_another_kind_is_not_used(
    address: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NOTIFY_SOCKET", address)
    assert notify("READY=1") is False


# --- how often systemd wants to hear --------------------------------------------------------------


def test_the_period_is_half_of_what_systemd_allows() -> None:
    assert watchdog_period({"WATCHDOG_USEC": "60000000"}) == 30.0
    assert watchdog_period({"WATCHDOG_USEC": "60000000", "WATCHDOG_PID": str(os.getpid())}) == 30.0


def test_no_watchdog_is_armed_without_a_limit() -> None:
    assert watchdog_period({}) is None
    assert watchdog_period({"WATCHDOG_USEC": ""}) is None
    assert watchdog_period({"WATCHDOG_USEC": "0"}) is None
    assert watchdog_period({"WATCHDOG_USEC": "soon"}) is None


def test_a_watchdog_meant_for_another_process_is_not_ours() -> None:
    env = {"WATCHDOG_USEC": "60000000", "WATCHDOG_PID": str(os.getpid() + 1)}
    assert watchdog_period(env) is None


# --- the heartbeat ------------------------------------------------------------------------------


def wait_until(condition, timeout: float = 5.0) -> bool:  # type: ignore[no-untyped-def]
    """Poll rather than sleep a fixed time: these threads share a busy Pi with a camera."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.01)
    return False


def heartbeat(alive, **kw) -> Heartbeat:  # type: ignore[no-untyped-def]
    return Heartbeat(alive, period=kw.pop("period", 0.04), tick=0.01, **kw)


class Inbox:
    """Collects what arrives at the fake systemd, as it arrives."""

    def __init__(self, server: socket.socket) -> None:
        self.messages: list[str] = []
        self._server = server
        server.settimeout(0.02)

    def drain(self) -> list[str]:
        try:
            while True:
                self.messages.append(self._server.recv(4096).decode())
        except TimeoutError:
            return self.messages


def test_a_loop_that_is_moving_is_reported_alive_again_and_again(systemd: socket.socket) -> None:
    inbox = Inbox(systemd)
    started = time.monotonic()
    beat = heartbeat(lambda: True)
    beat.start()
    assert wait_until(lambda: inbox.drain().count("WATCHDOG=1") >= 4)
    elapsed = time.monotonic() - started
    beat.stop()
    # About one every 0.04 s: not one every tick (0.01 s).
    assert inbox.drain().count("WATCHDOG=1") <= elapsed / 0.04 + 2


def test_a_loop_that_has_stopped_moving_is_not_reported_and_systemd_is_left_to_act(
    systemd: socket.socket,
) -> None:
    inbox = Inbox(systemd)
    moving = threading.Event()
    moving.set()
    beat = heartbeat(moving.is_set)
    beat.start()
    assert wait_until(lambda: inbox.drain().count("WATCHDOG=1") >= 2)
    moving.clear()  # the loop is stuck from here
    time.sleep(0.15)  # a ping already in flight lands
    seen = inbox.drain().count("WATCHDOG=1")
    time.sleep(0.4)
    beat.stop()
    assert inbox.drain().count("WATCHDOG=1") == seen


def test_the_pings_start_again_when_the_loop_does(systemd: socket.socket) -> None:
    inbox = Inbox(systemd)
    moving = threading.Event()
    beat = heartbeat(moving.is_set)
    beat.start()
    time.sleep(0.2)
    assert inbox.drain().count("WATCHDOG=1") == 0
    moving.set()
    assert wait_until(lambda: inbox.drain().count("WATCHDOG=1") >= 2)
    beat.stop()


def test_where_a_stuck_loop_is_stuck_goes_to_the_log_once(
    systemd: socket.socket, caplog: pytest.LogCaptureFixture
) -> None:
    release = threading.Event()

    def waits_for_the_camera_that_never_answers() -> None:
        release.wait(30)

    worker = threading.Thread(target=waits_for_the_camera_that_never_answers)
    worker.start()
    caplog.set_level(logging.ERROR, logger="trafficpi.watchdog")
    try:
        beat = heartbeat(lambda: False, watched_thread=worker.ident)
        beat.start()
        assert wait_until(lambda: len(caplog.records) >= 1)
        time.sleep(0.3)  # many more ticks, and still the one report
        beat.stop()
    finally:
        release.set()
        worker.join()
    reports = [r.getMessage() for r in caplog.records]
    assert len(reports) == 1
    assert "waits_for_the_camera_that_never_answers" in reports[0]
    assert "systemd" in reports[0]


def test_a_second_stall_is_reported_again(caplog: pytest.LogCaptureFixture) -> None:
    moving = threading.Event()
    caplog.set_level(logging.ERROR, logger="trafficpi.watchdog")
    beat = heartbeat(moving.is_set, watched_thread=threading.get_ident())
    beat.start()
    assert wait_until(lambda: len(caplog.records) == 1)
    moving.set()
    time.sleep(0.1)  # the heartbeat sees it moving again
    moving.clear()
    assert wait_until(lambda: len(caplog.records) == 2)
    beat.stop()


def test_the_status_line_is_sent_when_it_changes_and_not_otherwise(systemd: socket.socket) -> None:
    inbox = Inbox(systemd)
    text = ["counting at 30 fps"]
    beat = heartbeat(lambda: True, status=lambda: text[0], period=None)
    beat.start()
    assert wait_until(lambda: len(inbox.drain()) >= 1)
    time.sleep(0.15)  # several more ticks with the same text
    text[0] = "camera offline: busy"
    assert wait_until(lambda: len(inbox.drain()) >= 2)
    time.sleep(0.15)
    beat.stop()
    assert inbox.drain() == ["STATUS=counting at 30 fps", "STATUS=camera offline: busy"]


def test_without_a_watchdog_limit_only_the_status_is_sent(systemd: socket.socket) -> None:
    inbox = Inbox(systemd)
    beat = heartbeat(lambda: True, status=lambda: "up", period=None)
    beat.start()
    assert wait_until(lambda: len(inbox.drain()) >= 1)
    time.sleep(0.15)
    beat.stop()
    assert inbox.drain() == ["STATUS=up"]


def test_a_status_that_fails_does_not_stop_the_pings(systemd: socket.socket) -> None:
    inbox = Inbox(systemd)

    def broken() -> str:
        raise RuntimeError("no status today")

    beat = heartbeat(lambda: True, status=broken)
    beat.start()
    assert wait_until(lambda: inbox.drain().count("WATCHDOG=1") >= 2)
    beat.stop()


def test_a_new_line_in_a_status_cannot_smuggle_in_another_assignment(
    systemd: socket.socket,
) -> None:
    inbox = Inbox(systemd)
    beat = heartbeat(lambda: True, status=lambda: "ok\nREADY=1", period=None)
    beat.start()
    assert wait_until(lambda: len(inbox.drain()) >= 1)
    beat.stop()
    assert inbox.drain() == ["STATUS=ok READY=1"]


def test_a_check_that_fails_is_taken_to_mean_the_loop_is_moving(
    systemd: socket.socket, caplog: pytest.LogCaptureFixture
) -> None:
    """A fault in the monitor must not be what gets the service killed."""
    inbox = Inbox(systemd)

    def broken() -> bool:
        raise RuntimeError("bug in the check")

    beat = heartbeat(broken)
    beat.start()
    assert wait_until(lambda: inbox.drain().count("WATCHDOG=1") >= 2)
    beat.stop()


def test_stopping_is_prompt_even_with_a_long_period() -> None:
    beat = Heartbeat(lambda: True, period=3600.0, tick=3600.0)
    beat.start()
    started = time.monotonic()
    beat.stop()
    assert time.monotonic() - started < 1.0
