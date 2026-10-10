"""The road as a flat plane: a rectangle of known size puts the picture in metres (plan.md M6)."""

import math

import numpy as np
import pytest

from tests.camera_sim import PinholeCamera
from trafficpi.core.ground import (
    CHORD_ACROSS_M,
    CHORD_ALONG_M,
    MAX_TRUSTED_ERROR,
    CalibrationError,
    GroundMap,
    calibrate_rectangle,
    calibration_for,
    calibration_id,
    estimate_accuracy,
    estimate_length_spread,
    fit_homography,
    rectangle_map,
)

CAMERAS = {
    "from the side, high": PinholeCamera(),
    "down the road": PinholeCamera(x=0.0, y=-25.0, yaw_deg=0.0, pitch_deg=18.0, height=6.0),
    "steeply down, near": PinholeCamera(x=-5.0, y=-8.0, yaw_deg=25.0, pitch_deg=35.0, height=9.0),
    "wide lens": PinholeCamera(hfov_deg=90.0, y=-12.0, pitch_deg=28.0, yaw_deg=-10.0),
}
# A lane 3.5 m wide and 20 m long, and a two-lane road 8 m wide and 30 m long.
LANE = (0.0, 4.0, 3.5, 20.0)
ROAD = (-4.0, 4.0, 8.0, 30.0)


def corners_of(camera: PinholeCamera, rectangle: tuple[float, float, float, float]):  # type: ignore[no-untyped-def]
    x, y, width, length = rectangle
    world = [(x, y), (x + width, y), (x + width, y + length), (x, y + length)]
    seen = [camera.project(*point) for point in world]
    assert all(point is not None for point in seen)
    return [point for point in seen if point is not None]


# --- the homography --------------------------------------------------------------------------


def test_four_points_give_a_map_that_hits_all_four_exactly() -> None:
    picture = [(0.2, 0.3), (0.7, 0.35), (0.65, 0.8), (0.15, 0.75)]
    ground = [(0.0, 0.0), (3.0, 0.0), (3.0, 12.0), (0.0, 12.0)]
    matrix = fit_homography(picture, ground)
    ground_map = GroundMap.from_matrix(matrix)
    for point, expected in zip(picture, ground, strict=True):
        found = ground_map.to_ground(point)
        assert found is not None
        assert found == pytest.approx(expected, abs=1e-9)


def test_the_map_is_scaled_so_that_the_third_coordinate_is_one_at_the_middle_of_the_points() -> (
    None
):
    picture = [(0.2, 0.3), (0.7, 0.35), (0.65, 0.8), (0.15, 0.75)]
    matrix = fit_homography(picture, [(0, 0), (3, 0), (3, 12), (0, 12)])
    centre = np.mean(picture, axis=0)
    assert matrix[2] @ [centre[0], centre[1], 1.0] == pytest.approx(1.0)


def test_more_than_four_points_are_fitted_by_least_squares() -> None:
    camera = CAMERAS["from the side, high"]
    rng = np.random.default_rng(3)
    world = [(float(x), float(y)) for x in (-3.0, 0.0, 3.0) for y in (5.0, 15.0, 25.0, 35.0)]
    picture = [camera.project(*point) for point in world]
    noisy = [(u + rng.normal() * 0.0004, v + rng.normal() * 0.0007) for u, v in picture]  # type: ignore[misc]
    ground_map = GroundMap.from_matrix(fit_homography(noisy, world))
    # Away from the points the fit is the road, to a few centimetres.
    for x, y in ((-1.0, 10.0), (2.0, 20.0), (0.0, 30.0)):
        seen = camera.project(x, y)
        assert seen is not None
        found = ground_map.to_ground(seen)
        assert found is not None
        assert math.hypot(found[0] - x, found[1] - y) < 0.15


@pytest.mark.parametrize(
    ("picture", "why"),
    [
        ([(0.1, 0.1), (0.2, 0.2), (0.3, 0.3), (0.4, 0.5)], "on one line"),  # three on a line
        ([(0.1, 0.1), (0.1, 0.1), (0.5, 0.2), (0.4, 0.5)], "on one line"),  # two in one place
        ([(0.3, 0.3)] * 4, "same place"),  # all in one place
    ],
    ids=["three-on-a-line", "two-in-one-place", "all-in-one-place"],
)
def test_points_that_do_not_fix_a_map_are_refused(
    picture: list[tuple[float, float]], why: str
) -> None:
    with pytest.raises(CalibrationError, match=why):
        fit_homography(picture, [(0, 0), (3, 0), (3, 12), (0, 12)])


def test_points_on_a_line_in_the_picture_and_on_the_road_fix_nothing() -> None:
    """Every point on one line, in both: any number of maps fit, so none is chosen."""
    picture = [(0.1, 0.1), (0.2, 0.2), (0.3, 0.3), (0.4, 0.4)]
    road = [(0.0, 0.0), (1.0, 0.0), (2.0, 0.0), (3.0, 0.0)]
    with pytest.raises(CalibrationError, match="on one line"):
        fit_homography(picture, road)


def test_too_few_points_or_points_that_are_not_numbers_are_refused() -> None:
    with pytest.raises(CalibrationError, match="at least four"):
        fit_homography([(0.1, 0.1), (0.5, 0.2), (0.4, 0.5)], [(0, 0), (3, 0), (3, 12)])
    with pytest.raises(CalibrationError, match="at least four"):
        fit_homography([(0.1, 0.1)] * 4, [(0, 0)] * 3)
    with pytest.raises(CalibrationError, match="not a number"):
        fit_homography(
            [(0.1, math.nan), (0.5, 0.2), (0.4, 0.5), (0.2, 0.6)],
            [(0, 0), (3, 0), (3, 12), (0, 12)],
        )


# --- a rectangle on the road, seen by a camera ----------------------------------------------------


@pytest.mark.parametrize("name", CAMERAS)
def test_a_rectangle_puts_every_point_of_the_road_in_its_place(name: str) -> None:
    """Exact corners, so the map is the road to the last digit, near and far, inside the
    rectangle and well outside it."""
    camera = CAMERAS[name]
    x0, y0, width, length = ROAD
    ground_map = rectangle_map(corners_of(camera, ROAD), width, length)
    rng = np.random.default_rng(1)
    checked = 0
    for _ in range(400):
        x, y = rng.uniform(-10, 10), rng.uniform(-5, 70)
        seen = camera.project(x, y)
        if seen is None or not (0 <= seen[0] <= 1 and 0 <= seen[1] <= 1):
            continue
        found = ground_map.to_ground(seen)
        assert found is not None
        # The map's own origin is the rectangle's first corner.
        assert found == pytest.approx((x - x0, y - y0), abs=1e-7)
        checked += 1
    assert checked > 50


@pytest.mark.parametrize("name", CAMERAS)
def test_a_distance_in_the_picture_is_the_distance_on_the_road(name: str) -> None:
    camera = CAMERAS[name]
    ground_map = rectangle_map(corners_of(camera, ROAD), ROAD[2], ROAD[3])
    a, b = camera.project(-1.0, 8.0), camera.project(2.0, 29.0)
    assert a is not None and b is not None
    assert ground_map.distance(a, b) == pytest.approx(math.hypot(3.0, 21.0), rel=1e-7)


def test_the_road_in_metres_goes_back_to_the_picture() -> None:
    camera = CAMERAS["from the side, high"]
    ground_map = rectangle_map(corners_of(camera, LANE), LANE[2], LANE[3])
    for x, y in ((0.5, 2.0), (1.5, 15.0), (3.0, 30.0)):
        seen = camera.project(x, y + 4.0)  # +4: the rectangle starts 4 m along the road
        assert seen is not None
        back = ground_map.to_picture((x, y))
        assert back is not None
        assert back == pytest.approx(seen, abs=1e-7)


def test_the_sky_is_not_the_road() -> None:
    camera = CAMERAS["down the road"]  # the horizon is in the picture, at about 5% of its height
    ground_map = rectangle_map(corners_of(camera, ROAD), ROAD[2], ROAD[3])
    assert camera.ground(0.5, 0.02) is None
    assert ground_map.to_ground((0.5, 0.02)) is None  # above the horizon
    assert ground_map.distance((0.5, 0.02), (0.5, 0.5)) is None
    assert ground_map.metres_per_pixel((0.5, 0.02)) is None
    assert ground_map.to_ground((0.5, 0.1)) is not None  # just below it: the road, far away


def test_how_many_metres_a_pixel_is_grows_with_the_distance() -> None:
    camera = CAMERAS["from the side, high"]
    ground_map = rectangle_map(corners_of(camera, ROAD), ROAD[2], ROAD[3])
    near, far = camera.project(0.0, 6.0), camera.project(0.0, 60.0)
    assert near is not None and far is not None
    across_near, along_near = ground_map.metres_per_pixel(near) or (0.0, 0.0)
    across_far, along_far = ground_map.metres_per_pixel(far) or (0.0, 0.0)
    assert 0 < across_near < across_far and 0 < along_near < along_far
    # Along the road a pixel is always worth more than across it: the road is seen at a slant.
    assert along_near > across_near and along_far > across_far
    # And it is the truth: two points one pixel apart, along the road's own direction on screen.
    step = 1.0 / 1280
    p, q = camera.ground(near[0], near[1]), camera.ground(near[0] + step, near[1])
    assert p is not None and q is not None
    assert math.hypot(q[0] - p[0], q[1] - p[1]) == pytest.approx(
        ground_map.metres_per_pixel(near)[0] * 1.0,  # type: ignore[index]
        rel=0.15,
    )  # a pixel to the right is mostly across the road when the camera looks along it


def test_the_corners_may_go_round_either_way() -> None:
    camera = CAMERAS["down the road"]
    corners = corners_of(camera, LANE)
    forwards = rectangle_map(corners, LANE[2], LANE[3])
    # The other way round, starting at the same corner: now the second side is the length of the
    # first, so the two lengths swap places as well.
    backwards = rectangle_map([corners[0], corners[3], corners[2], corners[1]], LANE[3], LANE[2])
    a, b = camera.project(1.0, 10.0), camera.project(2.5, 20.0)
    assert a is not None and b is not None
    assert forwards.distance(a, b) == pytest.approx(backwards.distance(a, b), rel=1e-9)


@pytest.mark.parametrize(
    ("corners", "message"),
    [
        ([(0.3, 0.3), (0.7, 0.7), (0.7, 0.3), (0.3, 0.7)], "without crossing"),  # a bow tie
        ([(0.3, 0.3), (0.5, 0.5), (0.7, 0.7), (0.3, 0.7)], "without crossing"),  # three on a line
        ([(0.3, 0.3), (0.7, 0.3), (0.7, 0.7)], "four corners"),
    ],
    ids=["bow-tie", "three-on-a-line", "three-corners"],
)
def test_corners_that_are_not_a_rectangle_are_refused(
    corners: list[tuple[float, float]], message: str
) -> None:
    with pytest.raises(CalibrationError, match=message):
        rectangle_map(corners, 3.5, 12.0)


def test_a_rectangle_with_two_corners_a_few_pixels_apart_is_a_slip_of_the_mouse() -> None:
    corners = corners_of(CAMERAS["from the side, high"], ROAD)
    squashed = [corners[0], (corners[0][0] + 0.004, corners[0][1] + 0.002), corners[2], corners[3]]
    with pytest.raises(CalibrationError, match="pixels apart"):
        rectangle_map(squashed, ROAD[2], ROAD[3])


@pytest.mark.parametrize(
    ("width", "length"), [(0.0, 10.0), (3.5, 0.5), (200.0, 10.0), (3.5, 900.0)]
)
def test_a_rectangle_of_an_impossible_size_is_refused(width: float, length: float) -> None:
    corners = corners_of(CAMERAS["from the side, high"], LANE)
    with pytest.raises(CalibrationError, match="must be from"):
        rectangle_map(corners, width, length)


def test_corners_matched_to_the_road_in_the_wrong_order_are_refused() -> None:
    """A map is only a map of a road if every point it is made from is in front of the horizon.
    Four picture points in order round a quadrilateral, matched to the corners of a rectangle in
    an order that crosses it, are not."""
    picture = [(0.35, 0.8), (0.65, 0.8), (0.55, 0.3), (0.45, 0.3)]
    crossed = [(0.0, 0.0), (3.5, 0.0), (0.0, 12.0), (3.5, 12.0)]
    with pytest.raises(CalibrationError, match="horizon"):
        fit_homography(picture, crossed)


# --- how far it can be believed -------------------------------------------------------------------


def test_the_accuracy_is_the_same_every_time_it_is_worked_out() -> None:
    corners = corners_of(CAMERAS["from the side, high"], ROAD)
    first = estimate_accuracy(corners, ROAD[2], ROAD[3])
    second = estimate_accuracy(corners, ROAD[2], ROAD[3])
    assert np.array_equal(first.along, second.along)
    assert np.array_equal(first.across, second.across)


def test_a_larger_rectangle_is_believed_further_than_a_small_one() -> None:
    camera = CAMERAS["from the side, high"]
    small = estimate_accuracy(corners_of(camera, LANE), LANE[2], LANE[3])
    large = estimate_accuracy(corners_of(camera, ROAD), ROAD[2], ROAD[3])
    middle = camera.project(0.5, 20.0)
    assert middle is not None
    assert large.error_at(middle) < small.error_at(middle)
    assert (large.along < MAX_TRUSTED_ERROR).sum() > (small.along < MAX_TRUSTED_ERROR).sum()


def test_the_further_from_the_rectangle_the_less_a_distance_is_believed() -> None:
    camera = CAMERAS["from the side, high"]
    accuracy = estimate_accuracy(corners_of(camera, ROAD), ROAD[2], ROAD[3])
    errors = []
    for y in (20.0, 45.0, 70.0, 110.0):  # the rectangle ends at y = 34
        seen = camera.project(0.0, y)
        assert seen is not None
        errors.append(accuracy.error_at(seen))
    assert errors == sorted(errors) and errors[-1] > 2 * errors[0]


def test_the_sky_has_no_accuracy() -> None:
    corners = corners_of(CAMERAS["down the road"], ROAD)
    accuracy = estimate_accuracy(corners, ROAD[2], ROAD[3])
    assert math.isinf(accuracy.error_at((0.5, 0.01)))
    assert math.isinf(accuracy.best_error_at((0.5, 0.01)))


def test_with_no_doubt_about_the_corners_or_the_lengths_the_map_is_believed_everywhere() -> None:
    corners = corners_of(CAMERAS["from the side, high"], ROAD)
    exact = estimate_accuracy(corners, ROAD[2], ROAD[3], corner_sigma_px=0.0, length_sigma=0.0)
    finite = exact.along[np.isfinite(exact.along)]
    assert finite.size > 0 and finite.max() < 1e-9


def test_doubt_about_the_lengths_alone_is_doubt_about_the_scale_and_nothing_else() -> None:
    corners = corners_of(CAMERAS["from the side, high"], ROAD)
    none = estimate_accuracy(corners, ROAD[2], ROAD[3], corner_sigma_px=0.0, length_sigma=0.0)
    some = estimate_accuracy(corners, ROAD[2], ROAD[3], corner_sigma_px=0.0, length_sigma=0.05)
    more = estimate_accuracy(corners, ROAD[2], ROAD[3], corner_sigma_px=0.0, length_sigma=0.10)
    inside = np.isfinite(some.along)
    assert np.nanmax(none.along[inside]) < 1e-9
    assert (
        0.02 < np.median(some.along[inside]) < 0.2
    )  # a few percent of the lengths is a few percent of a speed
    assert np.median(more.along[inside]) > 1.5 * np.median(some.along[inside])


def test_more_doubt_about_the_corners_means_less_belief() -> None:
    corners = corners_of(CAMERAS["from the side, high"], ROAD)
    careful = estimate_accuracy(corners, ROAD[2], ROAD[3], corner_sigma_px=1.0)
    careless = estimate_accuracy(corners, ROAD[2], ROAD[3], corner_sigma_px=6.0)
    both = np.isfinite(careful.along) & np.isfinite(careless.along)
    assert (careless.along[both] > careful.along[both]).mean() > 0.95


def test_it_is_about_right_about_how_wrong_a_speed_over_a_real_stretch_can_be() -> None:
    """The estimate is a prediction; here is the thing it predicts. Shake the corners as a person
    would, measure a 6 m stretch of the real road (the pretend camera knows it) with each shaken
    map, and the spread of what comes out is what the accuracy said it would be, or less (it
    prefers to err on the safe side)."""
    camera = CAMERAS["from the side, high"]
    corners = corners_of(camera, ROAD)
    accuracy = estimate_accuracy(corners, ROAD[2], ROAD[3])
    rng = np.random.default_rng(11)
    sigma = np.array([1.0, 16 / 9]) * 3.0 / 1280
    a, b = camera.project(0.0, 14.0), camera.project(0.0, 14.0 + CHORD_ALONG_M)
    assert a is not None and b is not None
    seen = []
    for _ in range(300):
        moved = [tuple(np.array(c) + rng.normal(size=2) * sigma) for c in corners]
        width, length = ROAD[2] * (1 + rng.normal() * 0.02), ROAD[3] * (1 + rng.normal() * 0.02)
        try:
            shaken = rectangle_map([(float(x), float(y)) for x, y in moved], width, length)
        except CalibrationError:
            continue
        measured = shaken.distance(a, b)
        assert measured is not None
        seen.append(measured / CHORD_ALONG_M - 1.0)
    observed = float(np.sqrt(np.mean(np.square(seen))))
    predicted = accuracy.error_at(((a[0] + b[0]) / 2, (a[1] + b[1]) / 2))
    assert 0.3 * predicted < observed < 1.3 * predicted
    assert CHORD_ACROSS_M < CHORD_ALONG_M  # the lane's width is the shorter stretch


def test_the_gate_looks_in_the_direction_the_vehicle_is_going() -> None:
    camera = CAMERAS["from the side, high"]
    calibration = calibrate_rectangle(corners_of(camera, LANE), LANE[2], LANE[3])
    inside = camera.project(1.75, 14.0)
    assert inside is not None
    assert calibration.believed(inside, (0.0, 8.0))  # along the lane, inside the rectangle
    beside = camera.project(1.75 + 12.0, 14.0)  # three lanes over, which no corner can vouch for
    assert beside is not None
    assert not calibration.believed(beside, (0.0, 8.0))
    far = camera.project(1.75, 90.0)
    assert far is not None
    assert not calibration.believed(far, (0.0, 8.0))


def _along_but_not_across(calibration):  # type: ignore[no-untyped-def]
    """The cell where a distance along the road is believed and one across it is believed least."""
    accuracy = calibration.accuracy
    rows, columns = accuracy.along.shape
    both_known = np.isfinite(accuracy.along) & np.isfinite(accuracy.across)
    margin = np.where(
        both_known
        & (accuracy.along <= 0.9 * MAX_TRUSTED_ERROR)
        & (accuracy.across > MAX_TRUSTED_ERROR),
        np.where(both_known, accuracy.across - accuracy.along, 0.0),
        -1.0,
    )
    row, column = np.unravel_index(int(np.argmax(margin)), margin.shape)
    assert margin[row, column] > 0, "no such place"
    return (column + 0.5) / columns, (row + 0.5) / rows


def test_a_speed_along_the_road_is_believed_where_one_across_it_is_not() -> None:
    """The error is judged in the direction the vehicle goes: a vehicle driving along a lane is
    given a speed where the map is good along the road, even if it is poor across it, and a vehicle
    crossing the road there is not."""
    calibration = calibrate_rectangle(
        corners_of(CAMERAS["from the side, high"], LANE), LANE[2], LANE[3]
    )
    place = _along_but_not_across(calibration)
    assert calibration.believed(place, (0.0, 8.0))  # along the road
    assert not calibration.believed(place, (8.0, 0.0))  # across it
    assert not calibration.believed(place, (-8.0, 0.0))  # across it the other way, as well


def test_a_vehicle_standing_still_has_no_direction_and_is_judged_along_the_road() -> None:
    camera = CAMERAS["from the side, high"]
    calibration = calibrate_rectangle(corners_of(camera, ROAD), ROAD[2], ROAD[3])
    inside = camera.project(0.0, 20.0)
    assert inside is not None
    assert calibration.believed(inside, (0.0, 0.0))


# --- how far a distance the owner knows can be believed ------------------------------------------


def test_the_length_the_map_finds_for_a_segment_varies_by_what_the_shaking_says() -> None:
    camera = CAMERAS["from the side, high"]
    corners = corners_of(camera, ROAD)
    segments = [
        (camera.project(0.0, 8.0), camera.project(0.0, 14.0)),
        (camera.project(-3.0, 10.0), camera.project(3.0, 10.0)),
    ]
    spreads = estimate_length_spread(corners, ROAD[2], ROAD[3], segments)  # type: ignore[arg-type]
    assert all(s is not None and 0.005 < s < 0.2 for s in spreads)
    # The same question, the same answer.
    assert estimate_length_spread(corners, ROAD[2], ROAD[3], segments) == spreads  # type: ignore[arg-type]


def test_it_agrees_with_shaking_the_rectangle_and_measuring_the_real_road() -> None:
    camera = CAMERAS["from the side, high"]
    corners = corners_of(camera, ROAD)
    a, b = camera.project(-2.0, 10.0), camera.project(2.0, 28.0)
    assert a is not None and b is not None
    (predicted,) = estimate_length_spread(corners, ROAD[2], ROAD[3], [(a, b)])
    rng = np.random.default_rng(99)  # not the generator the estimate uses
    sigma = np.array([1.0, 16 / 9]) * 3.0 / 1280
    truth = math.hypot(4.0, 18.0)
    changes = []
    for _ in range(300):
        moved = [tuple(np.array(c) + rng.normal(size=2) * sigma) for c in corners]
        width, length = ROAD[2] * (1 + rng.normal() * 0.02), ROAD[3] * (1 + rng.normal() * 0.02)
        measured = rectangle_map([(float(x), float(y)) for x, y in moved], width, length).distance(
            a, b
        )
        assert measured is not None
        changes.append(measured / truth - 1.0)
    observed = float(np.sqrt(np.mean(np.square(changes))))
    assert predicted is not None and 0.6 * observed < predicted < 1.6 * observed


def test_no_doubt_about_the_corners_or_the_lengths_means_no_spread() -> None:
    camera = CAMERAS["from the side, high"]
    corners = corners_of(camera, ROAD)
    a, b = camera.project(0.0, 8.0), camera.project(0.0, 20.0)
    (spread,) = estimate_length_spread(
        corners,
        ROAD[2],
        ROAD[3],
        [(a, b)],  # type: ignore[list-item]
        corner_sigma_px=0.0,
        length_sigma=0.0,
    )
    assert spread == pytest.approx(0.0, abs=1e-9)


def test_a_segment_with_an_end_in_the_sky_has_no_spread() -> None:
    camera = CAMERAS["down the road"]
    corners = corners_of(camera, ROAD)
    below = camera.project(0.0, 20.0)
    assert below is not None
    (spread,) = estimate_length_spread(corners, ROAD[2], ROAD[3], [(below, (0.5, 0.01))])
    assert spread is None


def test_a_calibration_asked_for_again_is_the_same_one_and_a_bad_one_is_not_kept() -> None:
    corners = tuple(corners_of(CAMERAS["from the side, high"], ROAD))
    first = calibration_for(corners, ROAD[2], ROAD[3])
    assert calibration_for(corners, ROAD[2], ROAD[3]) is first  # kept: it takes a tenth of a second
    assert calibration_for(corners, ROAD[2], ROAD[3] + 1.0) is not first
    for _ in range(2):  # a failure is raised every time, and nothing is kept for it
        with pytest.raises(CalibrationError):
            calibration_for((corners[0], corners[2], corners[1], corners[3]), 8.0, 30.0)


# --- a name for a calibration ---------------------------------------------------------------------


def test_a_rectangle_has_a_short_name_that_follows_what_it_is() -> None:
    corners = corners_of(CAMERAS["from the side, high"], ROAD)
    name = calibration_id(corners, 8.0, 30.0)
    assert len(name) == 8 and int(name, 16) >= 0
    assert calibration_id(corners, 8.0, 30.0) == name
    assert calibration_id(corners, 8.0, 31.0) != name
    assert calibration_id(corners, 8.5, 30.0) != name
    moved = [(corners[0][0] + 0.01, corners[0][1]), *corners[1:]]
    assert calibration_id(moved, 8.0, 30.0) != name


def test_a_calibration_is_the_map_the_accuracy_and_the_name() -> None:
    corners = corners_of(CAMERAS["from the side, high"], ROAD)
    calibration = calibrate_rectangle(corners, ROAD[2], ROAD[3])
    assert calibration.id == calibration_id(corners, ROAD[2], ROAD[3])
    seen = corners[0]
    assert calibration.to_ground(seen) == pytest.approx((0.0, 0.0), abs=1e-9)
