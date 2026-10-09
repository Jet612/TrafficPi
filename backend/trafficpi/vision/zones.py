"""Which detections the pipeline looks at at all: the detection and exclusion zones of a profile."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from trafficpi.core.geometry import Polygon
from trafficpi.core.types import Detection


@dataclass(frozen=True, slots=True)
class DetectionFilter:
    """Keeps the detections inside the detection zones and outside the exclusion zones.

    A detection is "in" a zone when the point where the object meets the road, the middle of the
    bottom edge of its box, is inside the outline (the boundary counts as inside). With no
    detection zones everything outside the exclusion zones is kept; an exclusion zone always wins.

    It runs before the tracker: an object that is ignored never becomes a track, so parked cars
    in an exclusion zone cost nothing and show up nowhere. A vehicle that drives out of an
    exclusion zone is a new track from the moment it leaves.
    """

    include: tuple[Polygon, ...] = ()
    exclude: tuple[Polygon, ...] = ()

    def accepts(self, detection: Detection) -> bool:
        point = detection.bottom_center
        if self.include and not any(zone.contains(point) for zone in self.include):
            return False
        return not any(zone.contains(point) for zone in self.exclude)

    def apply(self, detections: Sequence[Detection]) -> list[Detection]:
        return [d for d in detections if self.accepts(d)]
