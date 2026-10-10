"""What every rule produces and consumes."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Protocol

from trafficpi.vision.tracker import TrackUpdate


@dataclass(frozen=True, slots=True)
class Event:
    """Something a rule noticed about one tracked object. Subclasses add the details."""

    timestamp_ns: int  # camera time of the frame that triggered it
    track_id: int
    class_id: int
    label: str
    # The calibration (``GroundCalibration.id``) that the speeds in the event were measured under;
    # None if it has none. A rule never knows it: the engine stamps it, so that a rule stays a pure
    # function of tracks.
    calibration: str | None = field(default=None, kw_only=True)

    @property
    def has_speed(self) -> bool:
        """Whether the event carries a speed measured on a calibrated road. The engine then names
        the calibration on it; an event kind that carries speeds says so by overriding this."""
        return False


@dataclass(frozen=True, slots=True)
class CountEvent(Event):
    """An object crossed a counting line."""

    line_id: str
    direction: int  # +1: from the left of the line's direction to the right; -1: the other way
    # How fast it was going as it crossed, in metres a second over the last second; None if the
    # camera is not calibrated or the speed could not be believed (see ``SpeedMeter``).
    speed_ms: float | None = None

    @property
    def has_speed(self) -> bool:
        return self.speed_ms is not None


class Rule(Protocol):
    """Looks at the tracked objects of each frame and reports events.

    Rules are pure logic over tracks: they never touch the camera or the accelerator, so they can
    be tested and replayed anywhere.
    """

    def update(self, timestamp_ns: int, tracks: Sequence[TrackUpdate]) -> list[Event]: ...

    def forget(self, track_ids: Iterable[int]) -> None:
        """Drop what is remembered about tracks that no longer exist."""
        ...
