import math

import pytest

from trafficpi.core.geometry import Line, Polygon, cross

EAST = Line((0.2, 0.5), (0.8, 0.5))  # facing right on screen: "right-hand" is downwards


def test_cross_sign_matches_the_right_hand_side() -> None:
    assert cross((0, 0), (1, 0), (0.5, 0.5)) > 0  # below a line pointing east
    assert cross((0, 0), (1, 0), (0.5, -0.5)) < 0  # above it


def test_signed_distance_is_perpendicular_and_signed() -> None:
    assert EAST.signed_distance((0.5, 0.7)) == pytest.approx(0.2)
    assert EAST.signed_distance((0.5, 0.4)) == pytest.approx(-0.1)
    assert EAST.signed_distance((0.9, 0.5)) == pytest.approx(0.0)  # on the infinite line
    diagonal = Line((0, 0), (1, 1))
    assert diagonal.signed_distance((0, 1)) == pytest.approx(math.sqrt(2) / 2)


def test_degenerate_line_has_no_sides() -> None:
    point = Line((0.5, 0.5), (0.5, 0.5))
    assert point.signed_distance((0.9, 0.1)) == 0.0
    assert point.side((0.9, 0.1)) == 0


def test_side_with_margin() -> None:
    assert EAST.side((0.5, 0.6)) == 1
    assert EAST.side((0.5, 0.4)) == -1
    assert EAST.side((0.5, 0.502), margin=0.01) == 0
    assert EAST.side((0.5, 0.52), margin=0.01) == 1


def test_crossing_direction() -> None:
    assert (
        EAST.crossing((0.5, 0.4), (0.5, 0.6)) == 1
    )  # top to bottom: left to right of an east-facing line
    assert EAST.crossing((0.5, 0.6), (0.5, 0.4)) == -1
    assert EAST.crossing((0.5, 0.4), (0.5, 0.45)) == 0  # does not reach the line
    assert EAST.crossing((0.5, 0.6), (0.6, 0.7)) == 0  # stays on one side


def test_crossing_beyond_the_segment_ends_is_not_a_crossing() -> None:
    assert EAST.crossing((0.9, 0.4), (0.9, 0.6)) == 0  # passes the infinite line, but past the end
    assert EAST.crossing((0.1, 0.4), (0.1, 0.6)) == 0
    assert EAST.crossing((0.2, 0.4), (0.2, 0.6)) == 1  # exactly at the end counts


def test_oblique_crossing() -> None:
    assert EAST.crossing((0.3, 0.4), (0.7, 0.6)) == 1


def test_intersect_reports_where() -> None:
    t, u = EAST.intersect((0.5, 0.4), (0.5, 0.6)) or (None, None)
    assert t == pytest.approx(0.5) and u == pytest.approx(0.5)
    assert EAST.intersect((0.2, 0.4), (0.8, 0.4)) is None  # parallel
    assert EAST.intersect((0.5, 0.0), (0.5, 0.3)) is None  # does not reach


SQUARE = Polygon(((0.2, 0.2), (0.8, 0.2), (0.8, 0.8), (0.2, 0.8)))
L_SHAPE = Polygon(((0.0, 0.0), (0.6, 0.0), (0.6, 0.3), (0.3, 0.3), (0.3, 0.6), (0.0, 0.6)))


def test_polygon_contains() -> None:
    assert SQUARE.contains((0.5, 0.5))
    assert not SQUARE.contains((0.1, 0.5))
    assert not SQUARE.contains((0.5, 0.9))
    assert SQUARE.contains((0.2, 0.5))  # boundary counts as inside
    assert SQUARE.contains((0.2, 0.2))  # corner too


def test_polygon_concave_shape() -> None:
    assert L_SHAPE.contains((0.1, 0.5))
    assert L_SHAPE.contains((0.5, 0.1))
    assert not L_SHAPE.contains((0.5, 0.5))  # the notch of the L


def test_polygon_bounds_and_validation() -> None:
    assert L_SHAPE.bounds() == (0.0, 0.0, 0.6, 0.6)
    with pytest.raises(ValueError):
        Polygon(((0, 0), (1, 1)))
