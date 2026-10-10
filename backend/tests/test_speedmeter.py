"""Speeds in metres a second from where a vehicle is on the road (plan.md M6)."""

import math
from dataclasses import replace

import numpy as np
import pytest

from tests.camera_sim import PinholeCamera
from trafficpi.core.coco import class_name
from trafficpi.core.ground import GroundCalibration, calibrate_rectangle
from trafficpi.vision.speedmeter import MeterConfig, SpeedMeter, _Seen, fit_velocity
from trafficpi.vision.tracker import TrackUpdate

FRAME_NS = 33_333_333  # a 30 fps camera
CAMERA = PinholeCamera()
ROAD = (-4.0, 4.0, 8.0, 30.0)  # x, y of the first corner, width, length (m)


def calibration() -> GroundCalibration:
    x, y, width, length = ROAD
    world = [(x, y), (x + width, y), (x + width, y + length), (x, y + length)]
    corners = [CAMERA.project(*point) for point in world]
    return calibrate_rectangle([c for c in corners if c is not None], width, length)


CALIBRATION = calibration()


def track_at(track_id: int, x: float, y: float, *, confirmed: bool = True) -> TrackUpdate:
    """A car whose middle is at (x, y) on the road, as the tracker would report it, exactly."""
    box = CAMERA.box_of(x, y, 0.0)
    assert box is not None
    return TrackUpdate(track_id, 2, class_name(2), 0.9, box, (0.0, 0.0), 10, 1.0, confirmed)


def drive(
    meter: SpeedMeter,
    speed_ms: float,
    *,
    seconds: float,
    x: float = 1.5,
    y0: float = 6.0,
    track_id: int = 1,
    start_ns: int = 0,
) -> list[TrackUpdate]:
    """A car driving up the road at a steady speed for some seconds, one frame at a time."""
    seen = []
    for i in range(round(seconds * 30) + 1):
        (found,) = meter.update(
            start_ns + i * FRAME_NS, [track_at(track_id, x, y0 + speed_ms * i / 30)]
        )
        seen.append(found)
    return seen


# --- the fit ---------------------------------------------------------------------------------


def _points(*rows: tuple[float, float, float]) -> list[_Seen]:
    return [_Seen(t, x, y, 0.5, 0.5) for t, x, y in rows]


def test_the_velocity_is_the_slope_of_the_line_through_the_points() -> None:
    points = _points(*((t / 10, 2.0 + 3.0 * t / 10, 5.0 - 4.0 * t / 10) for t in range(11)))
    found = fit_velocity(points)
    assert found is not None
    vx, vy, farthest = found
    assert (vx, vy) == pytest.approx((3.0, -4.0))
    assert farthest == pytest.approx(0.0, abs=1e-9)


def test_a_point_off_the_line_is_reported_as_far_from_it() -> None:
    rows = [(t / 10, 3.0 * t / 10, 0.0) for t in range(11)]
    rows[5] = (0.5, 1.5, 2.0)  # two metres to the side
    found = fit_velocity(_points(*rows))
    assert found is not None
    assert found[2] > 1.5


def test_points_all_at_one_time_have_no_velocity() -> None:
    assert fit_velocity(_points((1.0, 0.0, 0.0), (1.0, 1.0, 0.0), (1.0, 2.0, 0.0))) is None


# --- a vehicle at a steady speed ----------------------------------------------------------------


@pytest.mark.parametrize("mph", [5, 15, 25, 40])
def test_a_vehicle_driving_at_a_steady_speed_is_measured_to_within_a_percent(mph: int) -> None:
    speed = mph / 2.2369362920544
    meter = SpeedMeter(CALIBRATION)
    measured = [t.speed_ms for t in drive(meter, speed, seconds=40 / speed) if t.speed_ms]
    assert len(measured) > 20
    assert measured == pytest.approx([speed] * len(measured), rel=0.01)


def test_the_track_is_given_where_it_is_on_the_road() -> None:
    meter = SpeedMeter(CALIBRATION)
    (found,) = meter.update(0, [track_at(1, 1.5, 10.0)])
    assert found.ground is not None
    # The tracker's box bottom is the front of the car's footprint seen from the camera, so the
    # point is near the car's middle but not exactly; the rectangle's first corner is the origin.
    assert found.ground == pytest.approx((1.5 + 4.0, 10.0 - 4.0), abs=2.5)


def test_what_the_tracker_said_is_left_as_it_was() -> None:
    meter = SpeedMeter(CALIBRATION)
    original = track_at(7, 1.5, 10.0)
    (found,) = meter.update(0, [original])
    assert replace(found, ground=None, speed_ms=None) == original


def test_a_vehicle_has_no_speed_until_enough_of_it_has_been_seen() -> None:
    meter = SpeedMeter(CALIBRATION)
    seen = drive(meter, 8.0, seconds=2.0)
    first = next(i for i, t in enumerate(seen) if t.speed_ms is not None)
    assert 0.55 <= first / 30 <= 0.8  # about min_span_s, after the points needed
    assert all(t.speed_ms is None for t in seen[:first])


def test_a_vehicle_that_stands_still_has_no_speed_to_speak_of() -> None:
    meter = SpeedMeter(CALIBRATION)
    seen = [meter.update(i * FRAME_NS, [track_at(1, 1.5, 12.0)])[0] for i in range(60)]
    assert seen[-1].speed_ms == pytest.approx(0.0, abs=1e-6)


def test_the_speed_is_the_speed_of_the_last_second_not_of_the_whole_drive() -> None:
    meter = SpeedMeter(CALIBRATION)
    seen = drive(meter, 2.0, seconds=3.0)  # from 6 m to 12 m along the road
    seen += drive(meter, 8.0, seconds=2.5, y0=12.0, start_ns=len(seen) * FRAME_NS)
    assert seen[89].speed_ms == pytest.approx(2.0, rel=0.02)
    assert seen[-1].speed_ms == pytest.approx(8.0, rel=0.02)
    # In between it is a blend, which is what an average over a second is.
    assert 2.0 < seen[105].speed_ms < 8.0  # type: ignore[operator]


def test_two_vehicles_are_measured_apart() -> None:
    meter = SpeedMeter(CALIBRATION)
    out: list[list[TrackUpdate]] = []
    for i in range(60):
        out.append(
            meter.update(
                i * FRAME_NS,
                [track_at(1, 1.5, 6.0 + 6.0 * i / 30), track_at(2, -1.5, 40.0 - 10.0 * i / 30)],
            )
        )
    first, second = out[-1]
    assert first.speed_ms == pytest.approx(6.0, rel=0.02)
    assert second.speed_ms == pytest.approx(10.0, rel=0.02)


# --- when a speed cannot be believed --------------------------------------------------------------


def test_a_break_in_the_points_starts_the_second_again() -> None:
    meter = SpeedMeter(CALIBRATION)
    drive(meter, 8.0, seconds=2.0)
    later = 2 * 30 + 30  # a second of silence
    seen = [
        meter.update((later + i) * FRAME_NS, [track_at(1, 1.5, 6.0 + 8.0 * (2.0 + 1.0 + i / 30))])[
            0
        ]
        for i in range(30)
    ]
    assert seen[0].speed_ms is None and seen[3].speed_ms is None
    assert seen[-1].speed_ms == pytest.approx(8.0, rel=0.02)


def test_a_vehicle_seen_only_now_and_then_is_not_given_a_speed() -> None:
    """Three looks in 0.7 s: enough time, too few points to fit a line through."""
    meter = SpeedMeter(CALIBRATION)
    found = [
        meter.update(round(t * 1e9), [track_at(1, 1.5, 6.0 + 8.0 * t)])[0] for t in (0.0, 0.35, 0.7)
    ]
    assert all(t.speed_ms is None for t in found)


def test_a_short_break_in_the_points_starts_the_second_again() -> None:
    """A break of 0.6 s is longer than the meter bridges, and shorter than the second it looks
    back over: the points before it must not be joined to those after."""
    meter = SpeedMeter(CALIBRATION)
    drive(meter, 8.0, seconds=2.0)  # 60 frames
    resumed = [
        meter.update(
            (60 + 18 + i) * FRAME_NS, [track_at(1, 1.5, 6.0 + 8.0 * (2.0 + 0.6 + i / 30))]
        )[0]
        for i in range(30)
    ]
    assert all(t.speed_ms is None for t in resumed[:12])  # not yet 0.6 s of the new run
    assert resumed[-1].speed_ms == pytest.approx(8.0, rel=0.02)


def test_a_brief_glimpse_of_the_sky_is_not_a_speed_and_does_not_stop_the_measuring() -> None:
    horizon_camera = PinholeCamera(x=0.0, y=-25.0, yaw_deg=0.0, pitch_deg=18.0, height=6.0)
    corners = [
        horizon_camera.project(*p) for p in ((-4.0, 4.0), (4.0, 4.0), (4.0, 34.0), (-4.0, 34.0))
    ]
    meter = SpeedMeter(calibrate_rectangle([c for c in corners if c is not None], 8.0, 30.0))

    def seen(i: int, ground_y: float) -> TrackUpdate:
        box = horizon_camera.box_of(1.0, ground_y, 0.0)
        assert box is not None
        return TrackUpdate(1, 2, "car", 0.9, box, (0.0, 0.0), 10, 1.0, True)

    sky = TrackUpdate(1, 2, "car", 0.9, (0.4, 0.0, 0.5, 0.03), (0.0, 0.0), 10, 1.0, True)
    found = []
    for i in range(60):
        track = sky if i == 30 else seen(i, 6.0 + 5.0 * i / 30)
        found.append(meter.update(i * FRAME_NS, [track])[0])
    assert found[30].speed_ms is None and found[30].ground is None
    assert found[31].speed_ms == pytest.approx(5.0, rel=0.05)  # the second goes on from before


def test_a_track_that_jumps_to_another_vehicle_has_no_speed_for_that_second() -> None:
    meter = SpeedMeter(CALIBRATION)
    seen = []
    for i in range(120):
        y = 6.0 + 6.0 * i / 30 if i < 60 else 6.0 + 6.0 * i / 30 + 9.0  # nine metres in one frame
        seen.append(meter.update(i * FRAME_NS, [track_at(1, 1.5, y)])[0])
    assert seen[50].speed_ms == pytest.approx(6.0, rel=0.02)
    assert all(t.speed_ms is None for t in seen[60:75])  # the jump is in the window: no speed
    assert seen[-1].speed_ms == pytest.approx(6.0, rel=0.02)  # and then the window has left it


def test_a_speed_faster_than_any_road_vehicle_is_not_given() -> None:
    meter = SpeedMeter(CALIBRATION, MeterConfig(max_speed_ms=20.0))
    seen = drive(meter, 25.0, seconds=1.2, y0=0.0)
    assert all(t.speed_ms is None for t in seen)
    assert all(t.ground is not None for t in seen)  # where it is, it is still known


def test_a_part_of_the_picture_the_calibration_does_not_reach_has_no_speed() -> None:
    narrow = calibrate_rectangle(
        [
            p
            for p in (
                CAMERA.project(0.0, 4.0),
                CAMERA.project(3.5, 4.0),
                CAMERA.project(3.5, 24.0),
                CAMERA.project(0.0, 24.0),
            )
            if p is not None
        ],
        3.5,
        20.0,
    )
    meter = SpeedMeter(narrow)
    inside = drive(meter, 8.0, seconds=1.5, x=1.75, y0=6.0)
    assert inside[-1].speed_ms == pytest.approx(8.0, rel=0.03)
    outside = drive(meter, 8.0, seconds=1.5, x=-11.0, y0=6.0, track_id=2)  # three lanes over
    assert outside[-1].speed_ms is None


def test_a_track_above_the_horizon_is_left_alone() -> None:
    horizon_camera = PinholeCamera(x=0.0, y=-25.0, yaw_deg=0.0, pitch_deg=18.0, height=6.0)
    corners = [
        horizon_camera.project(*p) for p in ((-4.0, 4.0), (4.0, 4.0), (4.0, 34.0), (-4.0, 34.0))
    ]
    meter = SpeedMeter(calibrate_rectangle([c for c in corners if c is not None], 8.0, 30.0))
    sky = TrackUpdate(
        3, 2, "car", 0.9, (0.4, 0.0, 0.5, 0.03), (0.0, 0.0), 10, 1.0, True
    )  # a box whose bottom is above the horizon
    (found,) = meter.update(0, [sky])
    assert found == sky and found.ground is None and found.speed_ms is None


def _cut_off(track: TrackUpdate, side: str) -> TrackUpdate:
    """The same car with its box run up against an edge of the picture."""
    x0, y0, x1, y1 = track.box
    box = {
        "left": (0.005, y0, x1, y1),
        "right": (x0, y0, 0.996, y1),
        "bottom": (x0, y0, x1, 0.995),
    }[side]
    return replace(track, box=box)


@pytest.mark.parametrize("side", ["left", "right", "bottom"])
def test_a_box_cut_off_by_the_edge_of_the_picture_is_put_on_the_road_but_has_no_speed(
    side: str,
) -> None:
    """The vehicle goes on past the edge and the box does not, so a line through the box looks like
    a vehicle slowing down: it is not measured, and nothing of it is kept to be joined to later."""
    meter = SpeedMeter(CALIBRATION)
    found = [
        meter.update(i * FRAME_NS, [_cut_off(track_at(1, 1.5, 6.0 + 8.0 * i / 30), side)])[0]
        for i in range(60)
    ]
    assert all(t.ground is not None for t in found)
    assert all(t.speed_ms is None for t in found)
    assert meter.following == 0


def test_a_vehicle_that_leaves_by_the_edge_keeps_the_speed_it_had_until_the_box_is_cut() -> None:
    meter = SpeedMeter(CALIBRATION)
    seen = drive(meter, 8.0, seconds=2.0)
    assert seen[-1].speed_ms == pytest.approx(8.0, rel=0.02)
    (cut,) = meter.update(
        61 * FRAME_NS, [_cut_off(track_at(1, 1.5, 6.0 + 8.0 * 61 / 30), "bottom")]
    )
    assert cut.speed_ms is None and cut.ground is not None
    # and when the box is whole again the second goes on from the points kept, not from nothing
    (back,) = meter.update(62 * FRAME_NS, [track_at(1, 1.5, 6.0 + 8.0 * 62 / 30)])
    assert back.speed_ms == pytest.approx(8.0, rel=0.02)


def test_a_box_one_pixel_inside_the_edge_is_measured() -> None:
    meter = SpeedMeter(CALIBRATION, MeterConfig(edge=0.01))
    track = track_at(1, 1.5, 12.0)
    _, y0, x1, y1 = track.box
    inside = replace(track, box=(0.0101, y0, x1, y1))
    meter.update(0, [inside])
    assert meter.following == 1


def test_a_track_in_the_sky_is_not_kept_in_mind() -> None:
    horizon_camera = PinholeCamera(x=0.0, y=-25.0, yaw_deg=0.0, pitch_deg=18.0, height=6.0)
    corners = [
        horizon_camera.project(*p) for p in ((-4.0, 4.0), (4.0, 4.0), (4.0, 34.0), (-4.0, 34.0))
    ]
    meter = SpeedMeter(calibrate_rectangle([c for c in corners if c is not None], 8.0, 30.0))
    sky = TrackUpdate(3, 2, "car", 0.9, (0.4, 0.0, 0.5, 0.03), (0.0, 0.0), 10, 1.0, True)
    for i in range(40):
        meter.update(i * FRAME_NS, [sky])
    assert meter.following == 0


def test_the_same_instant_twice_adds_nothing() -> None:
    meter = SpeedMeter(CALIBRATION)
    seen = drive(meter, 8.0, seconds=2.0)
    again = meter.update(2 * 30 * FRAME_NS, [track_at(1, 1.5, 6.0 + 8.0 * 2.0)])[0]
    assert again.speed_ms == pytest.approx(seen[-1].speed_ms)


def test_time_going_backwards_adds_nothing() -> None:
    meter = SpeedMeter(CALIBRATION)
    seen = drive(meter, 8.0, seconds=2.0)
    (found,) = meter.update(30 * FRAME_NS, [track_at(1, 1.5, 6.0 + 8.0)])
    assert found.speed_ms == pytest.approx(seen[-1].speed_ms)


# --- bookkeeping ----------------------------------------------------------------------------------


def test_a_track_that_ended_is_forgotten() -> None:
    meter = SpeedMeter(CALIBRATION)
    drive(meter, 8.0, seconds=1.0, track_id=1)
    drive(meter, 8.0, seconds=1.0, track_id=2)
    assert meter.following == 2
    meter.forget([1, 99])  # an id it never heard of is no trouble
    assert meter.following == 1
    meter.forget([2])
    assert meter.following == 0


def test_the_history_of_a_track_has_a_limit() -> None:
    meter = SpeedMeter(CALIBRATION, MeterConfig(history_points=10))
    drive(meter, 1.0, seconds=5.0)
    assert len(meter._seen[1]) == 10  # type: ignore[attr-defined]


def test_tracks_that_are_not_confirmed_are_measured_too_and_the_rules_decide() -> None:
    meter = SpeedMeter(CALIBRATION)
    found: list[TrackUpdate] = []
    for i in range(30):
        found = meter.update(i * FRAME_NS, [track_at(1, 1.5, 6.0 + 6.0 * i / 30, confirmed=False)])
    (last,) = found
    assert not last.confirmed and last.speed_ms == pytest.approx(6.0, rel=0.02)


# --- with the tracker in the way: the whole thing against the truth -------------------------------


def _through_the_tracker(speed_ms: float, lane_x: float, seed: int) -> list[float]:
    """Drive a car up the road past a camera whose box shakes by about two pixels, through the
    real tracker, and return every speed measured."""
    from trafficpi.core.types import Detection
    from trafficpi.vision.engine import TrafficEngine
    from trafficpi.vision.tracker import ByteTracker

    rng = np.random.default_rng(seed)
    engine = TrafficEngine(ByteTracker(), speed_meter=SpeedMeter(CALIBRATION))
    speeds = []
    for i in range(round(60 / speed_ms * 30)):
        box = CAMERA.box_of(lane_x, -6.0 + speed_ms * i / 30, 0.0)
        found: list[Detection] = []
        if box is not None and box[0] > 0 and box[2] < 1 and box[1] > 0 and box[3] < 1:
            shake = rng.normal(size=4) * np.array([2 / 1280, 2 / 720, 2 / 1280, 2 / 720])
            x0, y0, x1, y1 = (float(v) for v in np.array(box) + shake)
            found = [Detection(2, class_name(2), 0.9, x0, y0, x1, y1)]
        result = engine.update(i * FRAME_NS, found)
        speeds += [t.speed_ms for t in result.tracks if t.confirmed and t.speed_ms is not None]
    return speeds


# The slower a vehicle goes, the more the box's shaking is worth as a share of the speed: the
# shake moves the fitted line by about 0.25 m/s whatever the speed, and the rest is the calibration.
@pytest.mark.parametrize("lane_x", [-1.75, 1.75])
@pytest.mark.parametrize(
    ("mph", "rms_limit", "worst_limit"), [(10, 0.08, 0.25), (20, 0.045, 0.1), (35, 0.035, 0.08)]
)
def test_through_the_real_tracker_with_a_shaking_box_a_speed_is_right_to_a_few_percent(
    mph: int, rms_limit: float, worst_limit: float, lane_x: float
) -> None:
    speed = mph / 2.2369362920544
    speeds = _through_the_tracker(speed, lane_x, seed=mph)
    assert len(speeds) > 25
    relative = np.array(speeds) / speed - 1.0
    assert abs(relative.mean()) < 0.02
    assert math.sqrt(float((relative**2).mean())) < rms_limit
    assert np.abs(relative).max() < worst_limit
