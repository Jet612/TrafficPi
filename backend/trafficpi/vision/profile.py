"""From a validated camera profile to the objects the vision pipeline runs on.

The shape of the profile, and its validation, live in :mod:`trafficpi.core.profile`; this module
only turns it into counting lines, stop signs and a detection filter. Used by the vision service
when it loads or reloads the profile, and by the replay tools (``--profile``).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from trafficpi.core.coco import COCO_NAMES
from trafficpi.core.geometry import Line
from trafficpi.core.ground import CalibrationError, GroundCalibration, calibration_for
from trafficpi.core.profile import (
    CalibrationConfig,
    CountingLineConfig,
    Profile,
    StopSignConfig,
    load_profile_file,
    parse_profile,
)
from trafficpi.vision.rules import CountingLine, StopSign
from trafficpi.vision.zones import DetectionFilter

DEFAULT_ASPECT = 16 / 9  # the width over the height of the picture, when it is not known

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class CompiledProfile:
    counting_lines: list[CountingLine]
    detection_filter: DetectionFilter | None  # None: no zones, every detection is looked at
    stop_signs: list[StopSign]
    calibration: GroundCalibration | None = None  # None: no speeds in metres


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


def stop_sign(config: StopSignConfig, aspect: float = DEFAULT_ASPECT) -> StopSign:
    """The stop-sign rule's sign for a configured one. ``aspect`` is the picture's width over its
    height: a distance needs both of the picture's sides in one unit."""
    return StopSign(
        id=config.id,
        approach=config.polygon(),
        line=Line(config.a, config.b),
        name=config.name,
        direction=config.direction,
        aspect=aspect,
        classes=frozenset(COCO_NAMES.index(name) for name in config.classes),
        hold_s=config.hold_s,
        still_radius=config.still_radius,
        roll_speed=config.roll_speed,
        min_observed_s=config.min_observed_s,
    )


def calibration(config: CalibrationConfig | None) -> GroundCalibration | None:
    """The calibration of a configured rectangle, or None for no rectangle. A rectangle that passed
    the profile's checks but still cannot be worked with (it should not happen) is reported and
    left out: the counting goes on without speeds rather than not at all."""
    if config is None:
        return None
    try:
        corners = tuple((x, y) for x, y in config.corners)
        return calibration_for(corners, config.width_m, config.length_m)
    except CalibrationError as exc:
        log.error("the speed calibration cannot be used, so no speeds are measured: %s", exc)
        return None


def compile_profile(profile: Profile, aspect: float = DEFAULT_ASPECT) -> CompiledProfile:
    include = tuple(zone.polygon() for zone in profile.detection_zones)
    exclude = tuple(zone.polygon() for zone in profile.exclusion_zones)
    return CompiledProfile(
        counting_lines=[counting_line(line) for line in profile.lines],
        detection_filter=DetectionFilter(include, exclude) if include or exclude else None,
        stop_signs=[stop_sign(sign, aspect) for sign in profile.stop_signs],
        calibration=calibration(profile.calibration),
    )


def parse_compiled_profile(document: Any, aspect: float = DEFAULT_ASPECT) -> CompiledProfile:
    """Validate a JSON document and compile it (raises ``ProfileError`` if it is not usable)."""
    return compile_profile(parse_profile(document), aspect)


def load_compiled_profile(path: Path, aspect: float = DEFAULT_ASPECT) -> CompiledProfile:
    return compile_profile(load_profile_file(path), aspect)
