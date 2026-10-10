"""A calibrated camera through the whole of the vision pipeline: the tracker, the speed meter and
the rules, against a pretend camera that knows the truth (plan.md M6)."""

import math
from collections.abc import Sequence
from dataclasses import replace

import numpy as np
import pytest

from tests.camera_sim import PinholeCamera
from trafficpi.core.coco import class_name
from trafficpi.core.geometry import Line, Polygon
from trafficpi.core.ground import calibrate_rectangle
from trafficpi.core.types import Detection
from trafficpi.vision.engine import TrafficEngine
from trafficpi.vision.rules import (
    CountEvent,
    CountingLine,
    CountingRule,
    StopEvent,
    StopSign,
    StopSignRule,
)
from trafficpi.vision.speedmeter import SpeedMeter
from trafficpi.vision.tracker import ByteTracker, TrackUpdate

FRAME_NS = 33_333_333
CAMERA = PinholeCamera()
ROAD = (-4.0, 4.0, 8.0, 30.0)
x0, y0, WIDTH, LENGTH = ROAD
CORNERS = [
    CAMERA.project(*p)
    for p in ((x0, y0), (x0 + WIDTH, y0), (x0 + WIDTH, y0 + LENGTH), (x0, y0 + LENGTH))
]
CALIBRATION = calibrate_rectangle([c for c in CORNERS if c is not None], WIDTH, LENGTH)


def picture(x: float, y: float) -> tuple[float, float]:
    seen = CAMERA.project(x, y)
    assert seen is not None
    return seen


# A line across the road 18 m along it, and a stop sign: the road from 8 m to 24 m is the approach
# and the stop line is at 24 m. The sign's way through is up the road, to the top of the screen.
COUNTING_LINE = CountingLine("across", Line(picture(-4.0, 18.0), picture(4.0, 18.0)))
STOP_LINE = Line(picture(-4.0, 24.0), picture(4.0, 24.0))
APPROACH = Polygon((picture(-4.0, 8.0), picture(4.0, 8.0), picture(4.0, 24.0), picture(-4.0, 24.0)))
STOP_SIGN = StopSign("elm", APPROACH, STOP_LINE, direction=-1)


def drive(segments: Sequence[tuple[float, float, float]], y0: float = -4.0) -> list[float]:
    """Where a vehicle driving up the road is at each frame: (seconds, speed at the start, speed at
    the end) in metres a second for each stretch."""
    ys = [y0]
    for seconds, v0, v1 in segments:
        frames = round(seconds * 30)
        for i in range(frames):
            ys.append(ys[-1] + (v0 + (v1 - v0) * (i + 0.5) / frames) / 30)
    return ys


def run(
    engine: TrafficEngine,
    ys: Sequence[float],
    *,
    x: float = 1.75,
    shake: float = 2.0,
    seed: int = 4,
) -> tuple[list[CountEvent], list[StopEvent], list[TrackUpdate]]:
    rng = np.random.default_rng(seed)
    counts: list[CountEvent] = []
    stops: list[StopEvent] = []
    tracks: list[TrackUpdate] = []
    for i, y in enumerate(ys):
        box = CAMERA.box_of(x, y, 0.0)
        found: list[Detection] = []
        if box is not None and box[0] > 0 and box[2] < 1 and box[1] > 0 and box[3] < 1:
            noise = rng.normal(size=4) * np.array([shake / 1280, shake / 720] * 2)
            bx0, by0, bx1, by1 = (float(v) for v in np.array(box) + noise)
            found = [Detection(2, class_name(2), 0.9, bx0, by0, bx1, by1)]
        result = engine.update(i * FRAME_NS, found)
        tracks += result.tracks
        for event in result.events:
            if isinstance(event, CountEvent):
                counts.append(event)
            elif isinstance(event, StopEvent):
                stops.append(event)
    return counts, stops, tracks


def engine(*, calibrated: bool) -> TrafficEngine:
    return TrafficEngine(
        ByteTracker(),
        rules=[CountingRule([COUNTING_LINE]), StopSignRule([STOP_SIGN])],
        speed_meter=SpeedMeter(CALIBRATION) if calibrated else None,
    )


# --- a count carries the speed --------------------------------------------------------------------


@pytest.mark.parametrize("mph", [12, 20, 30])
def test_a_vehicle_that_crosses_a_counting_line_is_counted_with_its_speed(mph: int) -> None:
    speed = mph / 2.2369362920544
    counts, _, _ = run(engine(calibrated=True), drive([(40 / speed, speed, speed)]))
    (event,) = counts
    assert event.speed_ms is not None
    assert event.speed_ms == pytest.approx(speed, rel=0.07)
    assert event.calibration == CALIBRATION.id


def test_without_a_calibration_a_count_has_no_speed_and_no_calibration() -> None:
    counts, _, tracks = run(engine(calibrated=False), drive([(6.0, 8.0, 8.0)]))
    (event,) = counts
    assert event.speed_ms is None and event.calibration is None
    assert all(t.ground is None and t.speed_ms is None for t in tracks)


def test_a_count_made_before_the_speed_can_be_believed_has_none_and_no_calibration() -> None:
    """A fast vehicle first seen near the line: counted, but there is not half a second of it."""
    counts, _, _ = run(engine(calibrated=True), drive([(2.0, 20.0, 20.0)], y0=16.0))
    assert len(counts) == 1
    assert counts[0].speed_ms is None and counts[0].calibration is None


def test_what_a_rule_judges_is_the_same_with_or_without_a_calibration() -> None:
    """The speeds are extra: the outcome of a pass, and what is counted, do not depend on them."""
    drives = {
        "straight through": [(6.0, 7.0, 7.0)],
        "a full stop": [
            (1.5, 7.0, 7.0),
            (1.2, 7.0, 0.0),
            (2.5, 0.0, 0.0),
            (1.0, 0.0, 7.0),
            (3.0, 7.0, 7.0),
        ],
        "a roll": [
            (1.5, 7.0, 7.0),
            (1.2, 7.0, 1.6),
            (1.5, 1.6, 1.6),
            (1.0, 1.6, 7.0),
            (3.0, 7.0, 7.0),
        ],
    }
    for name, segments in drives.items():
        with_meter = run(engine(calibrated=True), drive(segments))
        without = run(engine(calibrated=False), drive(segments))
        assert [(e.line_id, e.direction) for e in with_meter[0]] == [
            (e.line_id, e.direction) for e in without[0]
        ], name
        assert [(e.outcome, e.hold_s, e.min_speed, e.max_speed) for e in with_meter[1]] == [
            (e.outcome, e.hold_s, e.min_speed, e.max_speed) for e in without[1]
        ], name


# --- a stop carries the speeds --------------------------------------------------------------------


def test_a_pass_that_did_not_stop_records_how_fast_it_went_in_metres_a_second() -> None:
    _, stops, _ = run(engine(calibrated=True), drive([(7.0, 9.0, 9.0)]))
    (event,) = stops
    assert event.outcome == "did_not_stop"
    assert event.min_speed_ms == pytest.approx(9.0, rel=0.08)
    assert event.max_speed_ms == pytest.approx(9.0, rel=0.08)
    assert event.line_speed_ms == pytest.approx(9.0, rel=0.08)
    assert event.calibration == CALIBRATION.id


def test_a_pass_with_a_stop_records_how_slowly_it_went() -> None:
    segments = [(1.5, 8.0, 8.0), (1.5, 8.0, 0.0), (2.5, 0.0, 0.0), (1.2, 0.0, 8.0), (3.0, 8.0, 8.0)]
    _, stops, _ = run(engine(calibrated=True), drive(segments, y0=0.0))
    (event,) = stops
    assert event.outcome == "stopped"
    assert event.min_speed_ms < 0.8  # standing still, to what a shaking box can say about it
    assert event.max_speed_ms > 5.0  # and it arrived at a good speed
    assert event.min_speed_ms < event.max_speed_ms


def test_a_rolling_pass_shows_the_speed_it_rolled_at() -> None:
    segments = [(1.0, 8.0, 8.0), (2.0, 8.0, 2.0), (2.0, 2.0, 2.0), (1.0, 2.0, 8.0), (3.0, 8.0, 8.0)]
    _, stops, _ = run(engine(calibrated=True), drive(segments, y0=0.0))
    (event,) = stops
    assert event.outcome == "rolling"
    assert event.min_speed_ms == pytest.approx(2.0, abs=0.7)


def test_without_a_calibration_a_stop_has_no_speeds_in_metres() -> None:
    _, stops, _ = run(engine(calibrated=False), drive([(7.0, 9.0, 9.0)]))
    (event,) = stops
    assert (event.min_speed_ms, event.max_speed_ms, event.line_speed_ms) == (-1.0, -1.0, -1.0)
    assert event.calibration is None and event.max_speed > 0  # the sizes a second are there


# --- the speed meter wired in or out --------------------------------------------------------------


def test_the_meter_can_be_given_and_taken_away_while_the_engine_runs() -> None:
    eng = engine(calibrated=False)
    first = run(eng, drive([(2.0, 8.0, 8.0)]))
    assert all(t.speed_ms is None for t in first[2])
    eng.speed_meter = SpeedMeter(CALIBRATION)
    ys = drive([(3.0, 8.0, 8.0)], y0=-4.0)
    later = []
    for i, y in enumerate(ys):
        box = CAMERA.box_of(1.75, y, 0.0)
        found = [Detection(2, "car", 0.9, *box)] if box and box[0] > 0 else []  # type: ignore[misc]
        later += eng.update((1000 + i) * FRAME_NS, found).tracks
    assert any(t.speed_ms is not None for t in later)


def test_the_meters_history_is_dropped_when_the_tracker_drops_a_track() -> None:
    eng = engine(calibrated=True)
    run(eng, drive([(3.0, 8.0, 8.0)]))
    meter = eng.speed_meter
    assert meter is not None
    for i in range(60):  # nothing in view for two seconds: the tracker lets the track go
        eng.update((200 + i) * FRAME_NS, [])
    assert meter.following == 0


def test_a_replaced_event_keeps_everything_but_the_calibration() -> None:
    counts, _, _ = run(engine(calibrated=True), drive([(40 / 8.0, 8.0, 8.0)]))
    (event,) = counts
    assert replace(event, calibration=None).speed_ms == event.speed_ms
    assert math.isclose(event.speed_ms or 0.0, round(event.speed_ms or 0.0, 2))  # two places
