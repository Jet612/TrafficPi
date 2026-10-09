"""Graceful shutdown on SIGINT and SIGTERM."""

from __future__ import annotations

import os
import signal
import threading
import time
from types import FrameType

FORCE_AFTER_S = 2.0


class StopRequest:
    """Set when the process is asked to stop: Ctrl-C, or SIGTERM from systemd.

    Loops poll :attr:`requested` and wind down cleanly. A terminal's Ctrl-C and a wrapper such as
    ``uv run`` can each deliver a signal, so extra signals in the first couple of seconds are
    ignored; a second request after that means cleanup is stuck, and the process exits at once.
    """

    def __init__(self) -> None:
        self._event = threading.Event()
        self._first_at: float | None = None

    def install(self) -> None:
        signal.signal(signal.SIGINT, self._handle)
        signal.signal(signal.SIGTERM, self._handle)

    def request(self) -> None:
        if self._first_at is None:
            self._first_at = time.monotonic()
        self._event.set()

    def _handle(self, signum: int, frame: FrameType | None) -> None:
        if self._first_at is not None and time.monotonic() - self._first_at > FORCE_AFTER_S:
            os._exit(128 + signum)
        self.request()

    @property
    def requested(self) -> bool:
        return self._event.is_set()

    def wait(self, timeout: float) -> bool:
        """Sleep up to ``timeout`` seconds, waking early on a stop request; True if one came."""
        return self._event.wait(timeout)
