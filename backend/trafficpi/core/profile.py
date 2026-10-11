"""The camera profile: the lines and zones drawn in the dashboard and obeyed by the vision service.

A profile is one JSON document per camera::

    {"schema_version": 1,
     "lines": [{"id": "street", "name": "Across the street", "a": [0.1, 0.6], "b": [0.9, 0.6],
                "classes": ["car", "truck", "bus"], "point": "ground",
                "margin": null, "persistence": null}],
     "detection_zones": [{"id": "road", "name": "The road", "points": [[0, 0.4], [1, 0.4], ...]}],
     "exclusion_zones": [{"id": "parking", "name": "Parked cars", "points": [...]}],
     "stop_signs": [{"id": "elm", "name": "Elm Street", "approach": [[0.1, 0.5], ...],
                     "a": [0.5, 0.9], "b": [0.5, 0.3], "direction": 1,
                     "classes": ["car", "truck", "bus", "motorcycle"], "hold_s": 1.0, ...}],
     "calibration": {"corners": [[0.2, 0.9], [0.8, 0.9], [0.7, 0.4], [0.3, 0.4]],
                     "width_m": 7.0, "length_m": 30.0,
                     "checks": [{"id": "drive", "name": "Driveway to pole", "a": [0.3, 0.8],
                                 "b": [0.6, 0.5], "length_m": 18.0}]}}

Points are fractions of the frame (0-1) with x to the right and y *downwards*, so a profile does
not depend on the capture or inference resolution. A vehicle is "in" a zone when the point where
it meets the road, the middle of the bottom edge of its box, is inside the outline.

These models are the single source of truth for the shape: the API's OpenAPI schema, and from it
the dashboard's TypeScript types, are generated from them. Every member is required in the
canonical form (the form the API returns and stores); a document with members left out, such as
one saved before zones existed or typed by hand, is filled in by the "before" step of each model,
so older documents keep loading. ``schema_version`` goes up when a new kind of element appears (a
document of an older version is filled in and loads as the newest; a document of a newer version
is refused, with a message to update TrafficPi): 2 added stop signs, 3 the calibration that puts the
picture in metres (and so speeds in metres a second).
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Annotated, Any, Literal, Self

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
    model_validator,
)

from trafficpi.core.coco import COCO_NAMES
from trafficpi.core.geometry import Polygon
from trafficpi.core.ground import (
    MAX_LENGTH_M,
    MAX_WIDTH_M,
    MIN_LENGTH_M,
    MIN_WIDTH_M,
    CalibrationError,
    rectangle_map,
)

SCHEMA_VERSION = 3
MAX_ELEMENTS = 32  # lines, or zones of one kind
MAX_CHECKS = 8  # known distances that test a calibration
MIN_CHECK_M = 0.5
MAX_POINTS = 32  # corners in one zone
MIN_ZONE_AREA = 1e-4  # of the frame (about 10 x 10 pixels at 720p): smaller is a slip of the mouse


class ProfileError(ValueError):
    """A document that is not a usable profile. The message says what is wrong, and where."""


def _inside_the_picture(value: tuple[float, float]) -> tuple[float, float]:
    x, y = value
    if not (0 <= x <= 1 and 0 <= y <= 1):  # false for NaN as well
        raise ValueError(f"must be inside the picture (x and y from 0 to 1), got [{x:g}, {y:g}]")
    return value


def _known_classes(names: list[str] | None) -> list[str] | None:
    if names is None:
        return None
    return _known_class_names(names)


def _known_class_names(names: list[str]) -> list[str]:
    if not names:
        raise ValueError("must name at least one kind of object (or be null for all)")
    for name in names:
        if name not in COCO_NAMES:
            raise ValueError(
                f"unknown class {name!r}; use COCO names such as car, truck, bus, motorcycle, "
                "bicycle or person"
            )
    return list(dict.fromkeys(names))  # without repeats, in the order given


Number = Annotated[float, Field(strict=True)]  # a JSON number: not a string, not true or false
Point = Annotated[tuple[Number, Number], AfterValidator(_inside_the_picture)]
ElementId = Annotated[str, StringConstraints(min_length=1, max_length=64)]
Label = Annotated[str, StringConstraints(max_length=80)]
ClassList = Annotated[list[str] | None, AfterValidator(_known_classes)]


def _one_way_or_the_other(value: int) -> int:
    if value not in (1, -1):
        raise ValueError("must be 1 or -1")
    return value


# Not Literal[1, -1]: that would take a JSON `true` for 1, as a strict whole number does not.
Direction = Annotated[int, Field(strict=True), AfterValidator(_one_way_or_the_other)]
# A stop sign must say which kinds have to stop: "all" would include the people crossing the road.
StopClassList = Annotated[list[str], AfterValidator(_known_class_names)]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")

    @classmethod
    def defaults(cls) -> dict[str, Any]:
        """What a member that a document leaves out means."""
        return {}

    @model_validator(mode="before")
    @classmethod
    def _fill_in_what_was_left_out(cls, data: Any) -> Any:
        return {**cls.defaults(), **data} if isinstance(data, dict) else data


class CountingLineConfig(_Model):
    """A line that vehicles are counted crossing.

    The direction is that of the line itself, from ``a`` to ``b``: a vehicle that crosses from its
    left to its right (on screen, with y pointing down) counts as forward, the other way as
    reverse. ``classes`` limits what is counted (null: every kind the detector reports). The
    tuning members are left null unless the defaults misbehave: ``point`` is the part of the box
    that is tested, ``margin`` a fixed dead band around the line and ``persistence`` how many
    frames on the other side make a crossing (see ``CountingLine``).
    """

    id: ElementId
    name: Label
    a: Point
    b: Point
    classes: ClassList
    point: Literal["ground", "center"]
    margin: Annotated[float, Field(strict=True, gt=0, le=0.2)] | None
    persistence: Annotated[int, Field(strict=True, ge=1, le=30)] | None

    @classmethod
    def defaults(cls) -> dict[str, Any]:
        return {"name": "", "classes": None, "point": "ground", "margin": None, "persistence": None}

    @model_validator(mode="after")
    def _has_a_direction(self) -> Self:
        if self.a == self.b:
            raise ValueError("the two ends are in the same place")
        return self


class ZoneConfig(_Model):
    """An area of the picture, given by its corners in order (either way round)."""

    id: ElementId
    name: Label
    points: Annotated[list[Point], Field(min_length=3, max_length=MAX_POINTS)]

    @classmethod
    def defaults(cls) -> dict[str, Any]:
        return {"name": ""}

    @model_validator(mode="after")
    def _has_a_proper_outline(self) -> Self:
        _check_outline(self.polygon())
        return self

    def polygon(self) -> Polygon:
        return Polygon(tuple(self.points))


def _check_outline(polygon: Polygon) -> None:
    if not polygon.is_simple():
        raise ValueError("the outline crosses itself, repeats a point or is flat")
    if polygon.area() < MIN_ZONE_AREA:
        raise ValueError("the zone is too small to matter")


STOP_KINDS = ["car", "truck", "bus", "motorcycle"]  # who must stop, unless a sign says otherwise


class StopSignConfig(_Model):
    """A stop line, and the stretch of road before it where a vehicle has to stop.

    ``approach`` is that stretch, as corners in order: a stop counts anywhere in it, so a vehicle
    that waited in a queue has stopped as much as one that waited at the line. ``a`` and ``b`` are
    the ends of the stop line. ``direction`` is the way across it that goes through the sign:
    1 from the left of the line's own direction (``a`` to ``b``, as on screen with y pointing
    down) to its right, -1 the other way; a vehicle crossing the other way came out of the
    intersection and is not judged. ``classes`` are the kinds that must stop. A vehicle has
    stopped if it is still for ``hold_s`` seconds, moving less than ``still_radius`` of its own
    size meanwhile; it is rolling if it slowed below ``roll_speed`` of its own sizes a second but
    never held still; it cannot be judged if it was watched for less than ``min_observed_s`` seconds
    before the line.
    """

    id: ElementId
    name: Label
    approach: Annotated[list[Point], Field(min_length=3, max_length=MAX_POINTS)]
    a: Point
    b: Point
    direction: Direction
    classes: StopClassList
    hold_s: Annotated[float, Field(strict=True, ge=0.2, le=10)]
    still_radius: Annotated[float, Field(strict=True, gt=0, le=0.5)]
    roll_speed: Annotated[float, Field(strict=True, gt=0, le=10)]
    min_observed_s: Annotated[float, Field(strict=True, ge=0.1, le=10)]

    @classmethod
    def defaults(cls) -> dict[str, Any]:
        return {
            "name": "",
            "direction": 1,
            "classes": list(STOP_KINDS),
            "hold_s": 1.0,
            "still_radius": 0.08,
            "roll_speed": 0.7,
            "min_observed_s": 0.8,
        }

    @model_validator(mode="after")
    def _has_a_line_and_a_proper_approach(self) -> Self:
        if self.a == self.b:
            raise ValueError("the two ends of the stop line are in the same place")
        _check_outline(self.polygon())
        return self

    def polygon(self) -> Polygon:
        return Polygon(tuple(self.approach))


class GroundCheckConfig(_Model):
    """A distance on the road that is known, drawn between two points of the picture.

    It does not make the calibration, it tests it: the dashboard says how long the calibrated map
    finds the distance, and how far that is from ``length_m``. A check that is wrong by much says
    that a corner, a length, or the check itself was measured badly. Put them where speeds will be
    measured, and not only in the rectangle (the map is only sure of what it was made from).
    """

    id: ElementId
    name: Label
    a: Point
    b: Point
    length_m: Annotated[float, Field(strict=True, ge=MIN_CHECK_M, le=MAX_LENGTH_M)]

    @classmethod
    def defaults(cls) -> dict[str, Any]:
        return {"name": ""}

    @model_validator(mode="after")
    def _has_a_length_in_the_picture(self) -> Self:
        if self.a == self.b:
            raise ValueError("the two ends are in the same place")
        return self


class CalibrationConfig(_Model):
    """Four points of the picture that are the corners of a rectangle on the road, and its size.

    The corners go round the rectangle one after the other, either way. The side from the first
    corner to the second is ``width_m`` long (across the road) and the side from the second to the
    third is ``length_m`` (along it). With that the picture can be put in metres:
    the speed of a vehicle follows from where it was on the road and when. It is only as good as
    the rectangle is big, flat and well measured; the dashboard says where speeds are believed.
    """

    corners: Annotated[list[Point], Field(min_length=4, max_length=4)]
    width_m: Annotated[float, Field(strict=True, ge=MIN_WIDTH_M, le=MAX_WIDTH_M)]
    length_m: Annotated[float, Field(strict=True, ge=MIN_LENGTH_M, le=MAX_LENGTH_M)]
    checks: Annotated[list[GroundCheckConfig], Field(max_length=MAX_CHECKS)]

    @classmethod
    def defaults(cls) -> dict[str, Any]:
        return {"checks": []}

    @model_validator(mode="after")
    def _is_a_rectangle_on_a_road(self) -> Self:
        try:
            rectangle_map(self.corners, self.width_m, self.length_m)
        except CalibrationError as exc:
            raise ValueError(str(exc)) from exc
        return self


class Profile(_Model):
    """Everything configured for one camera.

    Detections are ignored unless they are inside a detection zone (when there are any; with none
    the whole picture counts) and also when they are inside an exclusion zone, which wins.
    """

    schema_version: Annotated[int, Field(strict=True)]
    lines: Annotated[list[CountingLineConfig], Field(max_length=MAX_ELEMENTS)]
    detection_zones: Annotated[list[ZoneConfig], Field(max_length=MAX_ELEMENTS)]
    exclusion_zones: Annotated[list[ZoneConfig], Field(max_length=MAX_ELEMENTS)]
    stop_signs: Annotated[list[StopSignConfig], Field(max_length=MAX_ELEMENTS)]
    calibration: CalibrationConfig | None  # None: the camera is not calibrated, no speeds in metres

    @classmethod
    def defaults(cls) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "lines": [],
            "detection_zones": [],
            "exclusion_zones": [],
            "stop_signs": [],
            "calibration": None,
        }

    @model_validator(mode="before")
    @classmethod
    def _refuse_a_newer_schema(cls, data: Any) -> Any:
        version = data.get("schema_version", SCHEMA_VERSION) if isinstance(data, dict) else None
        if isinstance(version, int) and not isinstance(version, bool):
            if version > SCHEMA_VERSION:
                raise ValueError(
                    f"this profile has schema version {version}, but this TrafficPi understands "
                    f"up to {SCHEMA_VERSION}; update TrafficPi to use it"
                )
            if version < 1:
                raise ValueError(
                    f"schema_version must be from 1 to {SCHEMA_VERSION}, got {version}"
                )
            if (
                version < SCHEMA_VERSION
            ):  # an older document: what it lacks is filled in, so it is current
                return {**data, "schema_version": SCHEMA_VERSION}
        return data

    @model_validator(mode="after")
    def _ids_are_unique(self) -> Self:
        seen: set[str] = set()
        for element in (
            *self.lines,
            *self.detection_zones,
            *self.exclusion_zones,
            *self.stop_signs,
            *(self.calibration.checks if self.calibration is not None else ()),
        ):
            if element.id in seen:
                raise ValueError(f"the id {element.id!r} is used twice; every id must be unique")
            seen.add(element.id)
        return self


def parse_profile(document: Any) -> Profile:
    """The profile in a JSON document, or a :class:`ProfileError` saying what is wrong with it."""
    if not isinstance(document, dict):
        raise ProfileError('a profile is a JSON object, like {"lines": [], "detection_zones": []}')
    try:
        return Profile.model_validate(document)
    except ValidationError as exc:
        raise ProfileError(describe_errors(exc.errors())) from exc


def empty_profile() -> Profile:
    return Profile.model_validate({})


def load_profile_file(path: Path) -> Profile:
    """Read a profile from a JSON file (exported from the dashboard, or written by hand)."""
    try:
        document = json.loads(Path(path).read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ProfileError(f"{path} is not valid JSON: {exc}") from exc
    except OSError as exc:
        raise ProfileError(f"cannot read {path}: {exc.strerror or exc}") from exc
    return parse_profile(document)


# --- readable error messages ----------------------------------------------------------------------

_PLAIN_MESSAGES = {
    "missing": "is missing",
    "extra_forbidden": "is not something TrafficPi knows (a typo?)",
    "float_type": "must be a number",
    "int_type": "must be a whole number",
    "string_type": "must be text",
    "list_type": "must be a list",
    "dict_type": "must be an object",
    "model_type": "must be an object",
    "tuple_type": "must be a pair [x, y]",
}
MAX_REPORTED_PROBLEMS = 5


def _where(location: Iterable[str | int]) -> str:
    text = ""
    for part in location:
        if isinstance(part, int):
            text += f"[{part}]"
        else:
            text += f".{part}" if text else part
    return text


def describe_errors(errors: Sequence[Any]) -> str:
    """Pydantic's error list as a few short sentences, each starting with where the problem is.

    A problem inside a named element also gives the name: ``lines[0] ("Main street"): ...``.
    """
    problems: list[str] = []
    for error in errors:
        where = _where(error["loc"])
        given = error.get("input")
        name = given.get("name") if isinstance(given, dict) else None
        if where and isinstance(name, str) and name:
            where += f' ("{name}")'
        kind = error["type"]
        text = _PLAIN_MESSAGES.get(kind) or str(error["msg"]).removeprefix("Value error, ")
        message = f"{where}: {text}" if where else text
        if message not in problems:
            problems.append(message)
    shown = problems[:MAX_REPORTED_PROBLEMS]
    if len(problems) > len(shown):
        shown.append(f"and {len(problems) - len(shown)} more")
    return "; ".join(shown)
