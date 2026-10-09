"""End to end: synthetic vehicles through the real tracker and rules."""

import numpy as np
import pytest

from trafficpi.core.coco import class_name
from trafficpi.core.geometry import Line
from trafficpi.core.types import Detection
from trafficpi.vision.engine import CountTotals, TrafficEngine
from trafficpi.vision.rules import CountEvent, CountingLine, CountingRule

FRAME_NS = 33_333_333
LINE = CountingLine("main", Line((0.1, 0.5), (0.9, 0.5)))


def car(
    x: float,
    ground_y: float,
    *,
    cls: int = 2,
    score: float = 0.85,
    w: float = 0.08,
    h: float = 0.07,
) -> Detection:
    return Detection(cls, class_name(cls), score, x - w / 2, ground_y - h, x + w / 2, ground_y)


def run(engine: TrafficEngine, frames: list[list[Detection]]) -> list[CountEvent]:
    events: list[CountEvent] = []
    for i, detections in enumerate(frames):
        events += [
            e for e in engine.update(i * FRAME_NS, detections).events if isinstance(e, CountEvent)
        ]
    return events


def engine() -> TrafficEngine:
    return TrafficEngine(rules=[CountingRule([LINE])])


def test_a_car_driving_across_the_line_is_counted_once() -> None:
    frames = [
        [car(0.5, 0.2 + 0.006 * i)] for i in range(100)
    ]  # ground point passes y = 0.5 at i = 50
    (event,) = run(engine(), frames)
    assert event.direction == 1 and event.label == "car"
    assert 50 * FRAME_NS <= event.timestamp_ns <= 60 * FRAME_NS  # soon after reaching the line


def test_two_cars_in_opposite_directions() -> None:
    frames = [[car(0.3, 0.2 + 0.006 * i), car(0.7, 0.8 - 0.006 * i)] for i in range(100)]
    events = run(engine(), frames)
    assert sorted(e.direction for e in events) == [-1, 1]


def test_a_car_that_is_missed_for_a_few_frames_at_the_line_is_still_counted_once() -> None:
    frames = [[car(0.5, 0.2 + 0.006 * i)] for i in range(100)]
    for i in range(45, 56):
        frames[i] = []
    assert len(run(engine(), frames)) == 1


def test_a_car_stopping_short_of_the_line_with_noisy_detections_is_not_counted() -> None:
    rng = np.random.default_rng(3)
    frames = []
    for i in range(120):
        y = min(0.2 + 0.006 * i, 0.47)  # creeps up to the line and stops 0.03 short of it
        frames.append([car(0.5 + rng.normal(0, 0.002), y + rng.normal(0, 0.004))])
    assert run(engine(), frames) == []


@pytest.mark.parametrize("seed", range(40))
def test_a_car_stopped_right_on_the_line_is_not_counted_back_and_forth(seed: int) -> None:
    """Vehicles stop at stop lines, so a count line there must survive an object sitting on it."""
    rng = np.random.default_rng(seed)
    frames = []
    for i in range(150):
        y = min(0.2 + 0.006 * i, 0.5)  # arrives at the line and stays exactly on it
        frames.append([car(0.5 + rng.normal(0, 0.002), y + rng.normal(0, 0.004))])
    assert run(engine(), frames) == []


def test_noise_while_crossing_still_counts_once() -> None:
    rng = np.random.default_rng(5)
    frames = [
        [car(0.5 + rng.normal(0, 0.003), 0.2 + 0.006 * i + rng.normal(0, 0.004))]
        for i in range(100)
    ]
    assert len(run(engine(), frames)) == 1


def test_class_flicker_does_not_split_the_count_and_the_majority_class_wins() -> None:
    frames = []
    for i in range(100):
        cls = 7 if i % 5 == 0 else 2  # a "truck" in one frame out of five
        frames.append([car(0.5, 0.2 + 0.006 * i, cls=cls, score=0.7)])
    (event,) = run(engine(), frames)
    assert event.label == "car"


def test_parked_cars_and_pedestrians_beside_the_line_are_not_counted() -> None:
    frames = [[car(0.2, 0.7), car(0.8, 0.3)] for _ in range(100)]
    assert run(engine(), frames) == []


def test_a_weakly_detected_car_is_still_counted() -> None:
    frames = [[car(0.5, 0.2 + 0.006 * i, score=0.8 if i % 3 == 0 else 0.35)] for i in range(100)]
    assert len(run(engine(), frames)) == 1


def test_a_fast_car_seen_in_few_frames_is_counted() -> None:
    frames = [
        [car(0.5, 0.3 + 0.04 * i)] for i in range(10)
    ]  # 0.04 per frame: 1.2 frames' height per step
    assert len(run(engine(), frames)) == 1


def test_totals_by_line_direction_and_class() -> None:
    totals = CountTotals()
    for e in run(
        engine(),
        [[car(0.3, 0.2 + 0.006 * i), car(0.7, 0.8 - 0.006 * i, cls=7)] for i in range(100)],
    ):
        totals.add(e)
    assert totals.total() == 2
    assert totals.total("main", 1) == 1 and totals.total("main", -1) == 1
    assert totals.counts[("main", 1, "car")] == 1 and totals.counts[("main", -1, "truck")] == 1
    assert totals.total("elsewhere") == 0


def test_the_engine_reports_ended_tracks_and_forgets_them() -> None:
    eng = engine()
    run(eng, [[car(0.5, 0.3)] for _ in range(10)])
    ended: list[int] = []
    for i in range(10, 80):
        ended += eng.update(i * FRAME_NS, []).ended
    assert ended == [1]
    assert eng.rules[0]._memory == {}  # type: ignore[attr-defined]


@pytest.mark.parametrize("speed_px", [0.002, 0.006, 0.012])
def test_counting_is_independent_of_speed(speed_px: float) -> None:
    n = int(0.6 / speed_px)
    assert len(run(engine(), [[car(0.5, 0.2 + speed_px * i)] for i in range(n)])) == 1
