"""What a calibration means: the endpoint behind the calibration editor (plan.md M6, D44).

The editor shows a rectangle on the road and a few known distances, and has to say what follows
from them: how long the map finds each known distance, how many metres a pixel is where the stop
lines and counting lines are, how far a speed measured there can be believed, and over which part of
the picture speeds will be given at all. That is worked out here, once, by the same maths the vision
service uses (``core/ground``), so the dashboard has no copy of it to drift from. The calibration is
a draft that is not saved; one that makes no sense is answered with the reason, not with an error,
because it is usually a corner on its way somewhere.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, Request
from pydantic import ValidationError

from trafficpi.api.schemas import (
    CalibrationAnswer,
    CalibrationCheckResult,
    CalibrationGrid,
    CalibrationProbe,
    CalibrationProbeResult,
    CalibrationReport,
    CalibrationRequest,
)
from trafficpi.core.ground import (
    LENGTH_SIGMA,
    MAX_TRUSTED_ERROR,
    CalibrationError,
    calibration_for,
    estimate_length_spread,
)
from trafficpi.core.profile import CalibrationConfig, describe_errors

if TYPE_CHECKING:
    from trafficpi.api.app import ApiContext


def _pct(value: float | None) -> float | None:
    """A share as a percentage to a tenth; None for no answer (or a road that is not there)."""
    if value is None or not math.isfinite(value):
        return None
    return round(100 * value, 1)


def build_report(config: CalibrationConfig, probes: list[CalibrationProbe]) -> CalibrationReport:
    """Everything the editor shows about a calibration (``CalibrationError`` if it is unusable)."""
    corners = tuple((float(x), float(y)) for x, y in config.corners)
    calibration = calibration_for(corners, config.width_m, config.length_m)
    ground, accuracy = calibration.ground, calibration.accuracy

    spreads = (
        estimate_length_spread(
            corners, config.width_m, config.length_m, [(c.a, c.b) for c in config.checks]
        )
        if config.checks
        else []
    )
    checks = []
    for check, spread in zip(config.checks, spreads, strict=True):
        measured = ground.distance(check.a, check.b)
        error = None if measured is None else measured / check.length_m - 1.0
        agrees = None
        if error is not None and spread is not None:
            # the map's own doubt about this distance and the doubt about the length that was typed
            agrees = abs(error) <= 2 * math.hypot(spread, LENGTH_SIGMA)
        checks.append(
            CalibrationCheckResult(
                id=check.id,
                entered_m=check.length_m,
                measured_m=None if measured is None else round(measured, 2),
                error_pct=_pct(error),
                expected_pct=_pct(spread),
                agrees=agrees,
            )
        )

    results = []
    for probe in probes:
        per_pixel = ground.metres_per_pixel(probe.point)
        along = accuracy.error_at(probe.point, 0.0)
        across = accuracy.error_at(probe.point, math.pi / 2)
        results.append(
            CalibrationProbeResult(
                id=probe.id,
                metres_per_pixel_along=None if per_pixel is None else round(per_pixel[1], 3),
                metres_per_pixel_across=None if per_pixel is None else round(per_pixel[0], 3),
                error_along_pct=_pct(along),
                error_across_pct=_pct(across),
                believed=along <= MAX_TRUSTED_ERROR,
            )
        )

    centre = (sum(x for x, _ in corners) / 4, sum(y for _, y in corners) / 4)
    rows, columns = accuracy.along.shape
    return CalibrationReport(
        id=calibration.id,
        limit_pct=round(100 * MAX_TRUSTED_ERROR, 1),
        centre_error_pct=_pct(accuracy.error_at(centre, 0.0)),
        checks=checks,
        probes=results,
        grid=CalibrationGrid(
            columns=columns,
            rows=rows,
            along_pct=[[_pct(float(v)) for v in row] for row in accuracy.along],
            across_pct=[[_pct(float(v)) for v in row] for row in accuracy.across],
        ),
    )


def answer(body: CalibrationRequest) -> CalibrationAnswer:
    """The report for a calibration, or the reason it cannot be used."""
    try:
        config = CalibrationConfig.model_validate(body.calibration)
    except ValidationError as exc:
        return CalibrationAnswer(ok=False, problem=describe_errors(exc.errors()), report=None)
    try:
        return CalibrationAnswer(ok=True, problem=None, report=build_report(config, body.probes))
    except CalibrationError as exc:
        return CalibrationAnswer(ok=False, problem=str(exc), report=None)


def build_router(
    get_ctx: Callable[[Request], ApiContext], require_user: Callable[..., str]
) -> APIRouter:
    router = APIRouter(prefix="/api/calibration", dependencies=[Depends(require_user)])

    @router.post("/report", response_model=CalibrationAnswer)
    def report(body: CalibrationRequest) -> CalibrationAnswer:
        return answer(body)

    return router
