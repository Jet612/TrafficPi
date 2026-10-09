"""What every rule produces and consumes."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Protocol

from trafficpi.vision.tracker import TrackUpdate


@dataclass(frozen=True, slots=True)
class Event:
    """Something a rule noticed about one tracked object. Subclasses add the details."""

    timestamp_ns: int  # camera time of the frame that triggered it
    track_id: int
    class_id: int
    label: str


@dataclass(frozen=True, slots=True)
class CountEvent(Event):
    """An object crossed a counting line."""

    line_id: str
    direction: int  # +1: from the left of the line's direction to the right; -1: the other way


class Rule(Protocol):
    """Looks at the tracked objects of each frame and reports events.

    Rules are pure logic over tracks: they never touch the camera or the accelerator, so they can
    be tested and replayed anywhere.
    """

    def update(self, timestamp_ns: int, tracks: Sequence[TrackUpdate]) -> list[Event]: ...

    def forget(self, track_ids: Iterable[int]) -> None:
        """Drop what is remembered about tracks that no longer exist."""
        ...
