"""Sunrise and sunset from a place and a day (plan.md D42).

Stop signs can be judged only in daylight. That needs the times of sunrise and sunset where the
camera is, for any day, with no table, no network and no library. These are the formulas of NOAA's
solar calculator (Jean Meeus, *Astronomical Algorithms*): the sun's declination and the equation
of time from the Julian century, then the hour angle at which the sun's upper edge meets the
horizon, with refraction. They agree with the U.S. Naval Observatory's published times to a minute
or two (the tests compare 24 places and days, polar ones included); near the polar circles the sun
grazes the horizon and a small error in its height is a large one in the clock, so they are good
to a few minutes there.

**A day here is a solar day**, not a calendar day of the clock: the 24 hours around local solar
noon, named by a number (days since 1970-01-01, as if the place's mean solar time were UTC). That
way sunrise comes before solar noon and solar noon before sunset, whatever the zone and the clock
say: in Auckland (UTC+13) sunrise is on the previous day of the UTC calendar, in Reykjavik at
midsummer the sun sets after midnight, and in Urumqi (China keeps one time for the whole country)
noon is at 14:00 by the clock. Which solar day an instant belongs to is ``solar_day_label``.

A day on which the sun does not rise (``polar == "night"``) or does not set (``"day"``) has no
sunrise and no sunset. All instants are milliseconds since the epoch. Sunrise and sunset are
rounded to the nearest minute, as every published table gives them: the formulas are not better
than that, and then the page, the journal and the judging all say the same minute.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from functools import lru_cache
from typing import Literal

MS_PER_DAY = 86_400_000
_UNIX_EPOCH_JD = 2_440_587.5  # the Julian day number of 1970-01-01T00:00:00Z
_ZENITH_DEG = 90.833  # the sun's upper edge on the horizon: its radius (16') and refraction (34')
_MS_PER_MINUTE = 60_000
_MAX_LATITUDE = 89.9  # exactly at a pole the hour-angle formula divides by zero


@dataclass(frozen=True, slots=True)
class SunDay:
    """The sun on one solar day at one place."""

    label: int  # the solar day: days since 1970-01-01, by the place's mean solar time
    solar_noon_ms: int
    sunrise_ms: int | None
    sunset_ms: int | None
    # "night": the sun stays below the horizon all day; "day": it stays above it; None: it rises and
    # sets, and then ``sunrise_ms`` and ``sunset_ms`` are both given.
    polar: Literal["day", "night"] | None


def solar_day_label(at_ms: int, longitude: float) -> int:
    """The solar day that ``at_ms`` falls in at this longitude (east is positive).

    Solar days begin at mean solar midnight, which is 4 minutes of the clock earlier for every
    degree east of Greenwich."""
    return math.floor((at_ms + longitude * 240_000) / MS_PER_DAY)


def _sun(julian_day: float) -> tuple[float, float]:
    """The sun's declination (radians) and the equation of time (minutes) at a Julian day."""
    t = (julian_day - 2_451_545.0) / 36_525.0  # Julian centuries since J2000
    mean_longitude = (280.46646 + t * (36_000.76983 + t * 0.0003032)) % 360.0
    mean_anomaly = math.radians(357.52911 + t * (35_999.05029 - 0.0001537 * t))
    eccentricity = 0.016708634 - t * (0.000042037 + 0.0000001267 * t)
    centre = (
        math.sin(mean_anomaly) * (1.914602 - t * (0.004817 + 0.000014 * t))
        + math.sin(2 * mean_anomaly) * (0.019993 - 0.000101 * t)
        + math.sin(3 * mean_anomaly) * 0.000289
    )
    node = math.radians(125.04 - 1934.136 * t)
    apparent_longitude = math.radians(mean_longitude + centre - 0.00569 - 0.00478 * math.sin(node))
    seconds = 21.448 - t * (46.815 + t * (0.00059 - t * 0.001813))
    obliquity = math.radians(23.0 + (26.0 + seconds / 60.0) / 60.0 + 0.00256 * math.cos(node))
    declination = math.asin(math.sin(obliquity) * math.sin(apparent_longitude))
    y = math.tan(obliquity / 2) ** 2
    l0 = math.radians(mean_longitude)
    equation_of_time = 4.0 * math.degrees(
        y * math.sin(2 * l0)
        - 2 * eccentricity * math.sin(mean_anomaly)
        + 4 * eccentricity * y * math.sin(mean_anomaly) * math.cos(2 * l0)
        - 0.5 * y * y * math.sin(4 * l0)
        - 1.25 * eccentricity * eccentricity * math.sin(2 * mean_anomaly)
    )
    return declination, equation_of_time


def _cos_hour_angle(latitude: float, declination: float) -> float:
    """Below -1 the sun never sets; above 1 it never rises; between, its cosine."""
    lat = math.radians(latitude)
    return math.cos(math.radians(_ZENITH_DEG)) / (math.cos(lat) * math.cos(declination)) - (
        math.tan(lat) * math.tan(declination)
    )


@lru_cache(maxsize=256)
def sun_day(label: int, latitude: float, longitude: float) -> SunDay:
    """The sun on solar day ``label`` at this place (latitude north and longitude east positive)."""
    latitude = max(-_MAX_LATITUDE, min(_MAX_LATITUDE, latitude))
    midnight_jd = label + _UNIX_EPOCH_JD  # 0h UT of the date the label names

    def at(minutes: float) -> tuple[float, float]:
        return _sun(midnight_jd + minutes / 1440.0)

    noon = 720.0 - 4.0 * longitude  # minutes after 0h UT; the equation of time moves it a little
    for _ in range(2):
        noon = 720.0 - 4.0 * longitude - at(noon)[1]
    declination, _ = at(noon)
    cosine = _cos_hour_angle(latitude, declination)
    solar_noon_ms = label * MS_PER_DAY + round(noon * _MS_PER_MINUTE)
    if cosine >= 1.0:
        return SunDay(label, solar_noon_ms, None, None, "night")
    if cosine <= -1.0:
        return SunDay(label, solar_noon_ms, None, None, "day")

    def event(rising: bool) -> int:
        hour_angle = math.degrees(math.acos(cosine))
        minutes = noon - 4.0 * hour_angle if rising else noon + 4.0 * hour_angle
        for _ in range(2):  # the sun moves between noon and the event: look again where it is
            declination, equation = at(minutes)
            here = _cos_hour_angle(latitude, declination)
            if abs(here) >= 1.0:  # the sun just grazes the horizon: keep the estimate we have
                break
            angle = math.degrees(math.acos(here))
            minutes = 720.0 - 4.0 * (longitude + angle if rising else longitude - angle) - equation
        return (
            label * MS_PER_DAY + round(minutes) * _MS_PER_MINUTE
        )  # whole minutes, like the tables

    return SunDay(label, solar_noon_ms, event(True), event(False), None)
