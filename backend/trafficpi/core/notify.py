"""systemd's notification protocol, with no dependency.

A unit with ``Type=notify`` says when it is ready, and with ``WatchdogSec=`` it must say now and
then that it is still alive, or systemd kills it and starts it again. ``Restart=always`` brings
back a service that crashed; this brings back one that hung, inside the camera driver, the
accelerator's runtime or a database call, where nothing ever returns or raises.

The messages are datagrams to the socket named in ``$NOTIFY_SOCKET``. Outside systemd there is no
such variable and none of this does anything, so the same code runs by hand, in the tests and in CI.
"""

from __future__ import annotations

import logging
import os
import socket
import sys
import threading
import time
import traceback
from collections.abc import Callable, Mapping

log = logging.getLogger("trafficpi.watchdog")


def notify(message: str, environ: Mapping[str, str] | None = None) -> bool:
    """Send a state message (``READY=1``, ``WATCHDOG=1``, ``STATUS=...``) to systemd.

    False when there is nobody to tell (not run by systemd, or the socket is gone); that is never
    an error here, because the service must not stop for want of a supervisor.
    """
    env = os.environ if environ is None else environ
    address = env.get("NOTIFY_SOCKET", "")
    if not address or address[0] not in "/@":  # a path, or "@name" for an abstract socket
        return False
    target = "\0" + address[1:] if address[0] == "@" else address
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM | socket.SOCK_CLOEXEC) as sock:
            sock.settimeout(2.0)
            sock.sendto(message.encode(), target)
    except OSError:
        return False
    return True


def watchdog_period(environ: Mapping[str, str] | None = None) -> float | None:
    """Seconds between ``WATCHDOG=1`` messages: half of what systemd allows, as it advises.

    None when no watchdog is armed for this process (no ``WatchdogSec=``, or the variables are
    meant for another process, such as the one that started this).
    """
    env = os.environ if environ is None else environ
    pid = env.get("WATCHDOG_PID")
    if pid and pid != str(os.getpid()):
        return None
    try:
        limit_s = int(env.get("WATCHDOG_USEC", "")) / 1_000_000
    except ValueError:
        return None
    return limit_s / 2 if limit_s > 0 else None


class Heartbeat:
    """Tells systemd that the service is alive, but only while ``alive()`` says its work moves.

    A thread of its own sends ``WATCHDOG=1`` every ``period`` seconds for as long as ``alive()``
    is true. If the work loop hangs, ``alive()`` turns false, the messages stop, and systemd
    restarts the service after its ``WatchdogSec=``. The thread also writes down, once, where the
    watched thread is stuck: a hang in native code leaves no other trace.

    ``status`` is an optional line for ``systemctl status``; it is sent when it changes. With
    ``period`` None no watchdog is armed and only the status is kept up.
    """

    def __init__(
        self,
        alive: Callable[[], bool],
        *,
        period: float | None,
        status: Callable[[], str] | None = None,
        watched_thread: int | None = None,
        tick: float = 1.0,
        send: Callable[[str], bool] = notify,
    ) -> None:
        self._alive = alive
        self._period = period
        self._status = status
        self._watched = watched_thread
        self._tick = tick
        self._send = send
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="watchdog-heartbeat", daemon=True)
        self._last_status: str | None = None
        self._stuck = False

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread.is_alive():
            self._thread.join(2.0)

    def _run(self) -> None:
        last_ping = float("-inf")
        while not self._stop.wait(self._tick):
            now = time.monotonic()
            if self._is_alive():
                self._stuck = False
                if self._period is not None and now - last_ping >= self._period:
                    self._send("WATCHDOG=1")
                    last_ping = now
            elif not self._stuck:
                self._stuck = True
                self._report_stuck()
            self._publish_status()

    def _is_alive(self) -> bool:
        try:
            return bool(self._alive())
        except Exception:  # a fault in the check itself must not get the service killed
            log.exception("could not tell whether the work loop is moving; assuming it is")
            return True

    def _publish_status(self) -> None:
        if self._status is None:
            return
        try:
            text = " ".join(self._status().split())  # one line: a newline would start a new message
        except Exception:
            log.debug("could not work out the status line", exc_info=True)
            return
        if text != self._last_status:
            self._last_status = text
            self._send(f"STATUS={text}")

    def _report_stuck(self) -> None:
        ident = self._watched if self._watched is not None else threading.main_thread().ident
        frame = sys._current_frames().get(ident) if ident is not None else None
        where = "".join(traceback.format_stack(frame)) if frame is not None else "(no stack)\n"
        log.error(
            "the work loop has stopped moving, so no more watchdog messages are sent and systemd "
            "will restart the service. It is stuck here:\n%s",
            where,
        )
