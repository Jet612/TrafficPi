"""The thread that looks at the daily email every half minute (plan.md D32).

A thread of its own, not a task on the event loop: a look makes HTTP calls that can wait up to
fifteen seconds each, and the loop must never be held up by that (nor the watchdog's heartbeat,
which needs it, plan.md D27). It is a daemon thread, so that a look in progress never keeps the
process from stopping: a copy that was being sent is claimed but unfinished, the claim is given
back two minutes after the next start, and the copy's idempotency key makes a repeat harmless.
"""

from __future__ import annotations

import logging
import threading
from typing import Protocol

log = logging.getLogger("trafficpi.mail")

TICK_S = 30.0  # between two looks
FIRST_LOOK_AFTER_S = 10.0  # after the start, so that the service has settled


class Ticker(Protocol):
    def tick(self) -> object: ...


class MailScheduler:
    def __init__(
        self, service: Ticker, every_s: float = TICK_S, *, first_after_s: float | None = None
    ) -> None:
        self._service = service
        self._every_s = every_s
        self._first_after_s = (
            min(FIRST_LOOK_AFTER_S, every_s) if first_after_s is None else first_after_s
        )
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name="mail-scheduler", daemon=True)
        self._thread.start()

    def stop(self, timeout_s: float = 2.0) -> None:
        """Ask it to stop and wait a moment for it; a look that is still going is left to the
        process's end (it is a daemon thread)."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout_s)
            self._thread = None

    def _run(self) -> None:
        wait = self._first_after_s
        while not self._stop.wait(wait):
            wait = self._every_s
            try:
                self._service.tick()
            except Exception:  # a look never raises, but a thread that dies is silent for ever
                log.exception("a look at the daily email raised; the next one goes on")
