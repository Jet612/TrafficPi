"""From a validated camera profile to the objects the vision pipeline runs on.

The shape of the profile, and its validation, live in :mod:`trafficpi.core.profile`; this module
only turns it into counting lines and a detection filter. Used by the vision service when it
loads or reloads the profile, and by the replay tools (``--profile``).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from trafficpi.core.coco import COCO_NAMES
from trafficpi.core.geometry import Line
from trafficpi.core.profile import CountingLineConfig, Profile, load_profile_file, parse_profile
from trafficpi.vision.rules import CountingLine
from trafficpi.vision.zones import DetectionFilter


@dataclass(frozen=True, slots=True)
class CompiledProfile:
    counting_lines: list[CountingLine]
    detection_filter: DetectionFilter | None  # None: no zones, every detection is looked at


def counting_line(config: CountingLineConfig) -> CountingLine:
    """The counting rule's line for a configured one; unset tuning members keep their defaults."""
    tuning: dict[str, Any] = {}
    if config.classes is not None:
        tuning["classes"] = frozenset(COCO_NAMES.index(name) for name in config.classes)
    if config.margin is not None:
        tuning["margin"] = config.margin
    if config.persistence is not None:
        tuning["persistence"] = config.persistence
    return CountingLine(
        config.id, Line(config.a, config.b), config.name, point=config.point, **tuning
    )


def compile_profile(profile: Profile) -> CompiledProfile:
    include = tuple(zone.polygon() for zone in profile.detection_zones)
    exclude = tuple(zone.polygon() for zone in profile.exclusion_zones)
    return CompiledProfile(
        counting_lines=[counting_line(line) for line in profile.lines],
        detection_filter=DetectionFilter(include, exclude) if include or exclude else None,
    )


def parse_compiled_profile(document: Any) -> CompiledProfile:
    """Validate a JSON document and compile it (raises ``ProfileError`` if it is not usable)."""
    return compile_profile(parse_profile(document))


def load_compiled_profile(path: Path) -> CompiledProfile:
    return compile_profile(load_profile_file(path))
