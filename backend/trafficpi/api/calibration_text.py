"""A saved speed calibration, in words: what the map finds for each known distance and how far a
speed can be believed at each line. ``trafficpi-admin calibration-report``
prints it.

It is the editor's own report (``calibration_routes.build_report``) for the profile that is saved,
put in text that can be kept, quoted in ``docs/speed-calibration.md`` or sent to someone. It says
what the calibration is expected to give, from the corners, the lengths and the known distances. It
does not say how a vehicle of known speed was measured: that is ``speed-check``.
"""

from __future__ import annotations

import math

from trafficpi.api.calibration_routes import build_report
from trafficpi.api.schemas import CalibrationProbe, CalibrationReport
from trafficpi.core.ground import LENGTH_SIGMA, CalibrationError
from trafficpi.core.profile import CalibrationConfig, Profile

METRES_PER_FOOT = 0.3048


def _length(metres: float) -> str:
    """ "10.67 m (35.0 ft)": a length in the units the road was measured in, and in feet."""
    return f"{metres:.2f} m ({metres / METRES_PER_FOOT:.1f} ft)"


def _signed(percent: float) -> str:
    """ "+5.1%", "-0.9%", and "+0.0%" for a hair below nothing (not "-0.0%")."""
    text = f"{percent:+.1f}%"
    return "+0.0%" if text == "-0.0%" else text


def places_of(profile: Profile) -> list[tuple[str, str, CalibrationProbe]]:
    """Where the editor asks about: the middle of each counting line and of each stop line, as
    ``(what it is called, its kind, the question)``."""

    def middle(a: tuple[float, float], b: tuple[float, float]) -> tuple[float, float]:
        return ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)

    places: list[tuple[str, str, CalibrationProbe]] = []
    for line in profile.lines:
        point = middle(tuple(line.a), tuple(line.b))  # type: ignore[arg-type]
        places.append(
            (line.name or line.id, "counting line", CalibrationProbe(id=line.id, point=point))
        )
    for sign in profile.stop_signs:
        point = middle(tuple(sign.a), tuple(sign.b))  # type: ignore[arg-type]
        places.append(
            (sign.name or sign.id, "stop line", CalibrationProbe(id=sign.id, point=point))
        )
    return places


def report_text(profile: Profile) -> str:
    """The saved calibration's report as text; ``CalibrationError`` if it cannot be used,
    ``ValueError`` if the profile has none."""
    config = profile.calibration
    if config is None:
        raise ValueError("the saved profile has no speed calibration")
    places = places_of(profile)
    report = build_report(config, [probe for _, _, probe in places])
    return _format(config, report, places)


def _format(
    config: CalibrationConfig,
    report: CalibrationReport,
    places: list[tuple[str, str, CalibrationProbe]],
) -> str:
    lines = [
        f"Speed calibration {report.id}",
        f"  a rectangle {_length(config.width_m)} across the road (corner 1 to corner 2) by "
        f"{_length(config.length_m)} along it (corner 2 to corner 3)",
        f"  speeds are given only where the error along the road is expected to be at most "
        f"{report.limit_pct:g}%",
    ]
    if report.centre_error_pct is not None:
        lines.append(
            f"  expected error along the road at the middle of the rectangle: "
            f"{report.centre_error_pct:g}%"
        )
    lines.append(
        "  ('expected' is one standard deviation of what a careful person gets wrong: the corners "
        "shaken by 3 pixels and the lengths by 2%)"
    )

    lines += ["", "Known distances (they test the map; they do not make it)"]
    named = {check.id: check.name for check in config.checks}
    if not report.checks:
        lines.append("  none: add one or two on the Setup page, and run this again")
    for result in report.checks:
        name = named.get(result.id, result.id)
        if result.measured_m is None or result.error_pct is None:
            lines.append(f"  {name}: typed {_length(result.entered_m)}; the map cannot measure it")
            continue
        verdict = {True: "agrees", False: "DOES NOT AGREE", None: "cannot be judged"}[result.agrees]
        doubt = ""
        if result.expected_pct is not None:
            # the editor's rule: within twice the map's doubt and the typed length's, together
            limit = 2 * math.hypot(result.expected_pct, 100 * LENGTH_SIGMA)
            doubt = (
                f" (the map's own doubt about it is {result.expected_pct:g}% and a typed length is "
                f"good to {100 * LENGTH_SIGMA:g}%; it agrees within {limit:.1f}%)"
            )
        lines.append(
            f"  {name}: typed {_length(result.entered_m)}; the map finds "
            f"{_length(result.measured_m)}, {_signed(result.error_pct)}{doubt}: {verdict}"
        )

    lines += ["", "Where speeds are measured"]
    if not places:
        lines.append("  no counting line or stop sign is drawn")
    by_id = {place.id: (name, kind) for name, kind, place in places}
    for probe in report.probes:
        name, kind = by_id.get(probe.id, (probe.id, "place"))
        if probe.metres_per_pixel_along is None:
            lines.append(f"  {name} ({kind}): not on the road the calibration describes")
            continue
        along = (
            "no estimate"
            if probe.error_along_pct is None
            else f"{probe.error_along_pct:g}% along the road"
        )
        across = (
            "" if probe.error_across_pct is None else f", {probe.error_across_pct:g}% across it"
        )
        given = (
            "speeds are given here"
            if probe.believed
            else "NO SPEED IS GIVEN HERE (too far from what the rectangle reaches)"
        )
        lines.append(
            f"  {name} ({kind}): a pixel is {probe.metres_per_pixel_along * 100:.1f} cm along the "
            f"road; expected error {along}{across}: {given}"
        )

    lines += [
        "",
        "These are what the calibration is expected to give. None of this is a measurement against "
        "a vehicle of known speed: that is `trafficpi-admin speed-check`.",
    ]
    return "\n".join(lines) + "\n"


__all__ = ["CalibrationError", "places_of", "report_text"]
