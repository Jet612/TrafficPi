"""Counting lines in a JSON file.

The editor in the dashboard will produce these later; until then the file is written by hand::

    {"lines": [
        {"id": "stop-north", "name": "Northbound at the stop sign",
         "a": [0.52, 0.37], "b": [0.68, 0.37],
         "classes": ["car", "truck", "bus"], "point": "ground"}
    ]}

Points are fractions of the frame (0-1) with y pointing down. Crossing from the left of the line's
direction ``a -> b`` to its right counts as ``+1``. ``classes``, ``point``, ``margin`` and
``persistence`` are optional and default to the values of :class:`CountingLine`.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from trafficpi.core.coco import COCO_NAMES
from trafficpi.core.geometry import Line
from trafficpi.vision.rules import CountingLine


def _point(value: Any, where: str) -> tuple[float, float]:
    if (
        not isinstance(value, list | tuple)
        or len(value) != 2
        or not all(isinstance(v, int | float) and not isinstance(v, bool) for v in value)
    ):
        raise ValueError(f"{where} must be [x, y], got {value!r}")
    x, y = float(value[0]), float(value[1])
    if not (0 <= x <= 1 and 0 <= y <= 1):
        raise ValueError(f"{where} must be within 0-1, got {value!r}")
    return x, y


def _classes(value: Any, where: str) -> frozenset[int]:
    if not isinstance(value, list) or not value:
        raise ValueError(f"{where} must be a non-empty list of class names")
    ids = set()
    for name in value:
        if name not in COCO_NAMES:
            raise ValueError(f"{where}: unknown class {name!r}")
        ids.add(COCO_NAMES.index(name))
    return frozenset(ids)


def parse_counting_lines(data: Mapping[str, Any]) -> list[CountingLine]:
    raw = data.get("lines")
    if not isinstance(raw, list):
        raise ValueError('expected an object with a "lines" list')
    lines: list[CountingLine] = []
    seen: set[str] = set()
    for i, item in enumerate(raw):
        where = f"lines[{i}]"
        if not isinstance(item, dict):
            raise ValueError(f"{where} must be an object")
        line_id = item.get("id")
        if not isinstance(line_id, str) or not line_id:
            raise ValueError(f"{where} needs a non-empty string id")
        if line_id in seen:
            raise ValueError(f"{where}: duplicate id {line_id!r}")
        seen.add(line_id)
        a, b = _point(item.get("a"), f"{where}.a"), _point(item.get("b"), f"{where}.b")
        if a == b:
            raise ValueError(f"{where}: a and b are the same point")
        extra: dict[str, Any] = {}
        if "classes" in item:
            extra["classes"] = _classes(item["classes"], f"{where}.classes")
        if "point" in item:
            if item["point"] not in ("ground", "center"):
                raise ValueError(f"{where}.point must be 'ground' or 'center'")
            extra["point"] = item["point"]
        if "margin" in item:
            extra["margin"] = float(item["margin"])
        if "persistence" in item:
            if not isinstance(item["persistence"], int) or item["persistence"] < 1:
                raise ValueError(f"{where}.persistence must be a positive integer")
            extra["persistence"] = item["persistence"]
        lines.append(CountingLine(line_id, Line(a, b), str(item.get("name", "")), **extra))
    return lines


def load_counting_lines(path: Path) -> list[CountingLine]:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path} is not valid JSON: {exc}") from exc
    return parse_counting_lines(data)
