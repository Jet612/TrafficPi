"""Performance measurements for the vision pipeline."""

from __future__ import annotations

import random
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import psutil

THERMAL_ZONE = Path("/sys/class/thermal/thermal_zone0/temp")

# Bits of `vcgencmd get_throttled`: 0-3 describe the present, 16-19 what has happened since boot.
_THROTTLE_NOW = {
    0: "under-voltage",
    1: "ARM frequency capped",
    2: "throttled",
    3: "soft temperature limit",
}
_THROTTLE_SINCE_BOOT = {
    16: "under-voltage occurred",
    17: "ARM frequency cap occurred",
    18: "throttling occurred",
    19: "soft temperature limit occurred",
}


@dataclass(frozen=True, slots=True)
class Summary:
    count: int
    mean: float
    p50: float
    p95: float
    max: float


def percentile(sorted_values: Sequence[float], q: float) -> float:
    """The ``q`` quantile (0-1) of already sorted values, by linear interpolation."""
    if not sorted_values:
        return 0.0
    pos = q * (len(sorted_values) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(sorted_values) - 1)
    return sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * (pos - lo)


def summarize(values: Sequence[float]) -> Summary:
    if not values:
        return Summary(0, 0.0, 0.0, 0.0, 0.0)
    ordered = sorted(values)
    return Summary(
        count=len(ordered),
        mean=sum(ordered) / len(ordered),
        p50=percentile(ordered, 0.5),
        p95=percentile(ordered, 0.95),
        max=ordered[-1],
    )


class _Series:
    """Exact count, mean and max of a stream of values, with a bounded sample for percentiles.

    The sample is a reservoir (Algorithm R): every value ever seen is equally likely to be in it,
    so percentiles stay representative however long the run, in constant memory.
    """

    __slots__ = ("count", "max", "reservoir", "total")

    def __init__(self) -> None:
        self.count = 0
        self.total = 0.0
        self.max = 0.0
        self.reservoir: list[float] = []

    def add(self, value: float, capacity: int, rng: random.Random) -> None:
        self.count += 1
        self.total += value
        self.max = value if self.count == 1 else max(self.max, value)
        if len(self.reservoir) < capacity:
            self.reservoir.append(value)
        else:
            slot = rng.randrange(self.count)
            if slot < capacity:
                self.reservoir[slot] = value


class StageStats:
    """Collects how long each pipeline stage took, in milliseconds, in bounded memory."""

    def __init__(self, capacity: int = 10_000) -> None:
        self._capacity = capacity
        self._series: dict[str, _Series] = {}
        self._rng = random.Random(0)  # fixed seed: the same run reports the same percentiles

    def add(self, stage: str, ms: float) -> None:
        self._series.setdefault(stage, _Series()).add(ms, self._capacity, self._rng)

    def stages(self) -> list[str]:
        return list(self._series)

    def summary(self, stage: str) -> Summary:
        series = self._series.get(stage)
        if series is None or series.count == 0:
            return Summary(0, 0.0, 0.0, 0.0, 0.0)
        ordered = sorted(series.reservoir)
        return Summary(
            count=series.count,
            mean=series.total / series.count,
            p50=percentile(ordered, 0.5),
            p95=percentile(ordered, 0.95),
            max=series.max,
        )

    def clear(self) -> None:
        self._series.clear()


def decode_throttled(value: int) -> tuple[list[str], list[str]]:
    """Split ``vcgencmd get_throttled`` bits into (problems now, problems since boot)."""
    now = [text for bit, text in _THROTTLE_NOW.items() if value >> bit & 1]
    since = [text for bit, text in _THROTTLE_SINCE_BOOT.items() if value >> bit & 1]
    return now, since


def soc_temperature_c() -> float | None:
    try:
        return int(THERMAL_ZONE.read_text()) / 1000
    except (OSError, ValueError):
        return None


def read_throttled() -> int | None:
    """The raw throttle flags, or None if ``vcgencmd`` is unavailable."""
    try:
        out = subprocess.run(
            ["vcgencmd", "get_throttled"], capture_output=True, text=True, timeout=5
        ).stdout
        return int(out.strip().split("=", 1)[1], 16)
    except (OSError, subprocess.SubprocessError, IndexError, ValueError):
        return None


@dataclass(frozen=True, slots=True)
class SystemSample:
    process_cores: float  # CPU used by this process, in cores (1.0 = one core fully busy)
    total_percent: float  # CPU used by the whole machine, 0-100 across all cores
    rss_mb: float
    soc_temp_c: float | None
    throttled: int | None


class SystemSampler:
    """Samples CPU, memory and temperature. Each ``sample`` covers the time since the last."""

    def __init__(self) -> None:
        self._process = psutil.Process()
        self._process.cpu_percent(None)  # the first call only starts the measurement
        psutil.cpu_percent(None)

    def sample(self) -> SystemSample:
        return SystemSample(
            process_cores=self._process.cpu_percent(None) / 100,
            total_percent=psutil.cpu_percent(None),
            rss_mb=self._process.memory_info().rss / 1e6,
            soc_temp_c=soc_temperature_c(),
            throttled=read_throttled(),
        )
