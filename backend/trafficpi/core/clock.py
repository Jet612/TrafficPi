"""Turning the camera's monotonic timestamps into wall-clock time."""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path


class WallClock:
    """Maps sensor timestamps (monotonic nanoseconds) to UTC milliseconds since the epoch.

    Timing and speed maths use the monotonic timestamps; only what is stored or shown to people
    needs wall-clock time. The offset between the two clocks is re-measured now and then so that
    a clock step by NTP is followed.
    """

    def __init__(
        self,
        refresh_s: float = 60.0,
        *,
        wall_ns: Callable[[], int] = time.time_ns,
        monotonic_ns: Callable[[], int] = time.monotonic_ns,
    ) -> None:
        self._wall_ns = wall_ns
        self._monotonic_ns = monotonic_ns
        self._refresh_ns = int(refresh_s * 1e9)
        self._offset_ns = 0
        self._measured_at = -(10**18)
        self._measure()

    def _measure(self) -> None:
        now = self._monotonic_ns()
        self._offset_ns = self._wall_ns() - now
        self._measured_at = now

    def epoch_ms(self, monotonic_ns: int) -> int:
        if self._monotonic_ns() - self._measured_at > self._refresh_ns:
            self._measure()
        return (monotonic_ns + self._offset_ns) // 1_000_000

    def now_ms(self) -> int:
        return self._wall_ns() // 1_000_000


def system_timezone_name() -> str:
    """The machine's IANA time zone name (for example ``America/New_York``), or ``UTC``."""
    try:
        text = Path("/etc/timezone").read_text(encoding="utf-8").strip()
        if text:
            return text
    except OSError:
        pass
    try:
        target = str(Path("/etc/localtime").readlink())
    except OSError:
        return "UTC"
    marker = "zoneinfo/"
    return target.split(marker, 1)[1] if marker in target else "UTC"
