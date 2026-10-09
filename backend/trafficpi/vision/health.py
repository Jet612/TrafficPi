"""One row of machine health a minute, from the samples the vision service takes every few seconds.

The System page shows the temperature, the load and the frame rate *now*; this keeps what they were,
so that a board that throttled at half past one can be asked about at four (plan.md M8 step 4, R13).
A minute is the right size: a day is 1,440 rows of a few dozen bytes, and nothing that matters
happens faster. The recorder is plain arithmetic with no hardware and no database; the service feeds
it and stores what comes out.
"""

from __future__ import annotations

from dataclasses import dataclass

# Bits 0 to 3 of `vcgencmd get_throttled` describe the present (under-voltage, the clock capped,
# throttled, the soft temperature limit); bits 16 to 19 stay set since the boot and say nothing
# about any one minute.
THROTTLE_NOW_BITS = 0xF
UNDER_VOLTAGE = 0b0001
SLOWED_DOWN = 0b1110  # capped, throttled or at the soft limit: the board is holding itself back


@dataclass(frozen=True, slots=True)
class HealthRow:
    minute_start: int  # seconds since the epoch, on a minute
    temp_c_max: float | None  # the hottest reading in the minute
    cpu_percent: float | None  # the whole machine, mean
    fps_mean: float | None  # while the camera worked; None if it did not at all
    fps_min: float | None
    throttled: int  # the present-tense flags seen at any time in the minute
    samples: int  # how many readings the minute rests on
    camera_ok_samples: int  # and in how many of them the camera worked


class _Minute:
    """The readings of one minute so far."""

    def __init__(self, minute_start: int) -> None:
        self.minute_start = minute_start
        self.samples = 0
        self.camera_ok = 0
        self.temp_max: float | None = None
        self.cpu_sum = 0.0
        self.cpu_n = 0
        self.fps_sum = 0.0
        self.fps_min: float | None = None
        self.throttled = 0

    def add(
        self,
        temp_c: float | None,
        cpu_percent: float | None,
        fps: float,
        camera_ok: bool,
        throttled: int | None,
    ) -> None:
        self.samples += 1
        if temp_c is not None:
            self.temp_max = temp_c if self.temp_max is None else max(self.temp_max, temp_c)
        if cpu_percent is not None:
            self.cpu_sum += cpu_percent
            self.cpu_n += 1
        if camera_ok:
            self.camera_ok += 1
            self.fps_sum += fps
            self.fps_min = fps if self.fps_min is None else min(self.fps_min, fps)
        if throttled is not None:
            self.throttled |= throttled & THROTTLE_NOW_BITS

    def row(self) -> HealthRow:
        return HealthRow(
            minute_start=self.minute_start,
            temp_c_max=None if self.temp_max is None else round(self.temp_max, 1),
            cpu_percent=round(self.cpu_sum / self.cpu_n, 1) if self.cpu_n else None,
            fps_mean=round(self.fps_sum / self.camera_ok, 1) if self.camera_ok else None,
            fps_min=None if self.fps_min is None else round(self.fps_min, 1),
            throttled=self.throttled,
            samples=self.samples,
            camera_ok_samples=self.camera_ok,
        )


class HealthRecorder:
    """Collects samples and hands back a row for each minute that has ended."""

    def __init__(self) -> None:
        self._current: _Minute | None = None

    def add(
        self,
        at_ms: int,
        *,
        temp_c: float | None,
        cpu_percent: float | None,
        fps: float,
        camera_ok: bool,
        throttled: int | None,
    ) -> list[HealthRow]:
        """Note one reading; returns the minute that it ended, if it did. A minute with no
        reading in it has no row, so a service that was not running leaves a gap, not zeros. If the
        clock goes back the minute in progress is closed, not mixed with the earlier one."""
        minute = at_ms // 60_000 * 60
        finished: list[HealthRow] = []
        if self._current is not None and self._current.minute_start != minute:
            finished.append(self._current.row())
            self._current = None
        if self._current is None:
            self._current = _Minute(minute)
        self._current.add(temp_c, cpu_percent, fps, camera_ok, throttled)
        return finished

    def flush(self) -> list[HealthRow]:
        """The minute in progress, when the service stops: better a short minute than none."""
        if self._current is None:
            return []
        row, self._current = self._current.row(), None
        return [row]
