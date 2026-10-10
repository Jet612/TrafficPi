"""A calibration for tests that need a profile to hold one, and the name speeds are kept under."""

from typing import Any

from trafficpi.core.ground import calibration_id

CALIBRATION: dict[str, Any] = {
    "corners": [[0.62, 0.42], [0.86, 0.42], [0.58, 0.31], [0.43, 0.31]],
    "width_m": 7.0,
    "length_m": 30.0,
}


def name_of(document: dict[str, Any] = CALIBRATION) -> str:
    """``GroundCalibration.id`` for this calibration: the name its speeds are stored under."""
    corners = [(float(x), float(y)) for x, y in document["corners"]]
    return calibration_id(corners, float(document["width_m"]), float(document["length_m"]))


OTHER: dict[str, Any] = {**CALIBRATION, "length_m": 31.0}  # the same road, corrected
