"""The camera profile: the lines and zones drawn in the dashboard and obeyed by the vision service.

A profile is one JSON document per camera::

    {"schema_version": 1,
     "lines": [{"id": "street", "name": "Across the street", "a": [0.1, 0.6], "b": [0.9, 0.6],
                "classes": ["car", "truck", "bus"], "point": "ground",
                "margin": null, "persistence": null}],
     "detection_zones": [{"id": "road", "name": "The road", "points": [[0, 0.4], [1, 0.4], ...]}],
     "exclusion_zones": [{"id": "parking", "name": "Parked cars", "points": [...]}]}

Points are fractions of the frame (0-1) with x to the right and y *downwards*, so a profile does
not depend on the capture or inference resolution. A vehicle is "in" a zone when the point where
it meets the road, the middle of the bottom edge of its box, is inside the outline.

These models are the single source of truth for the shape: the API's OpenAPI schema, and from it
the dashboard's TypeScript types, are generated from them. Every member is required in the
canonical form (the form the API returns and stores); a document with members left out, such as
one saved before zones existed or typed by hand, is filled in by the "before" step of each model,
so older documents keep loading. ``schema_version`` is for changes that need a migration; adding
an optional member does not need one.
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

SCHEMA_VERSION = 1
MAX_ELEMENTS = 32  # lines, or zones of one kind
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
        polygon = self.polygon()
        if not polygon.is_simple():
            raise ValueError("the outline crosses itself, repeats a point or is flat")
        if polygon.area() < MIN_ZONE_AREA:
            raise ValueError("the zone is too small to matter")
        return self

    def polygon(self) -> Polygon:
        return Polygon(tuple(self.points))


class Profile(_Model):
    """Everything configured for one camera.

    Detections are ignored unless they are inside a detection zone (when there are any; with none
    the whole picture counts) and also when they are inside an exclusion zone, which wins.
    """

    schema_version: Annotated[int, Field(strict=True)]
    lines: Annotated[list[CountingLineConfig], Field(max_length=MAX_ELEMENTS)]
    detection_zones: Annotated[list[ZoneConfig], Field(max_length=MAX_ELEMENTS)]
    exclusion_zones: Annotated[list[ZoneConfig], Field(max_length=MAX_ELEMENTS)]

    @classmethod
    def defaults(cls) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "lines": [],
            "detection_zones": [],
            "exclusion_zones": [],
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
                raise ValueError(f"schema_version must be {SCHEMA_VERSION}, got {version}")
        return data

    @model_validator(mode="after")
    def _ids_are_unique(self) -> Self:
        seen: set[str] = set()
        for element in (*self.lines, *self.detection_zones, *self.exclusion_zones):
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
