import math
import random
from collections.abc import Iterator, Sequence
from dataclasses import replace

import pytest

from tests.fakes import FULL_STOP, ROLLING_STOP, STRAIGHT_THROUGH
from tests.fakes import drive as positions
from trafficpi.core.coco import class_name
from trafficpi.core.geometry import Line, Polygon
from trafficpi.vision.rules import StopEvent, StopSign, StopSignRule
from trafficpi.vision.rules.stop_sign import _Sample, fastest, longest_still, slowest
from trafficpi.vision.tracker import TrackUpdate

FRAME_NS = 33_333_333  # a 30 fps camera
# The stop line is vertical at x = 0.5; going from the left of the picture to its right is the way
# through the stop sign (+1). The approach zone is the road to the left of the line.
LINE = Line((0.5, 0.9), (0.5, 0.3))
ZONE = Polygon(((0.1, 0.5), (0.5, 0.5), (0.5, 0.95), (0.1, 0.95)))
SIGN = StopSign("main", ZONE, LINE, name="Main street")
LANE = 0.8  # the vehicles' ground point, as a fraction of the picture's height


def track(
    track_id: int,
    x: float,
    y: float = LANE,
    *,
    scale: float = 1.0,
    cls: int = 2,
    confirmed: bool = True,
) -> TrackUpdate:
    """A car whose ground point is (x, y). ``scale`` is its size: smaller is further away."""
    w, h = 0.12 * scale, 0.14 * scale
    box = (x - w / 2, y - h, x + w / 2, y)
    return TrackUpdate(track_id, cls, class_name(cls), 0.9, box, (0.0, 0.0), 10, 1.0, confirmed)


def run(
    rule: StopSignRule,
    xs: Iterator[float] | Sequence[float],
    *,
    track_id: int = 1,
    noise: float = 0.0,
    seed: int = 3,
    start_ns: int = 0,
    scale: float = 1.0,
    cls: int = 2,
    confirmed: bool = True,
) -> list[StopEvent]:
    """Drive a vehicle through the picture, ``noise`` being how far the detector's box shakes."""
    rng = random.Random(seed)
    events: list[StopEvent] = []
    for i, x in enumerate(xs):
        jitter = (rng.uniform(-noise, noise), rng.uniform(-noise, noise))
        seen = track(
            track_id, x + jitter[0], LANE + jitter[1], scale=scale, cls=cls, confirmed=confirmed
        )
        for event in rule.update(start_ns + i * FRAME_NS, [seen]):
            assert isinstance(event, StopEvent)
            events.append(event)
    return events


def outcome(
    segments: Sequence[tuple[float, float, float]],
    *,
    noise: float = 0.0,
    scale: float = 1.0,
    start_ns: int = 0,
) -> StopEvent:
    """The one event a drive along the road makes."""
    (event,) = run(
        StopSignRule([SIGN]), positions(segments), noise=noise, scale=scale, start_ns=start_ns
    )
    return event


# --- the four outcomes --------------------------------------------------------------------------


def test_a_vehicle_that_waits_at_the_line_has_stopped() -> None:
    event = outcome(FULL_STOP)
    assert event.outcome == "stopped"
    assert event.hold_s >= 1.5 and event.min_speed < 0.1


def test_a_vehicle_that_slows_but_never_holds_still_is_rolling() -> None:
    event = outcome(ROLLING_STOP)
    assert event.outcome == "rolling"
    assert event.hold_s < 1.0 and 0 < event.min_speed < SIGN.roll_speed


def test_a_vehicle_that_drives_straight_through_did_not_stop() -> None:
    event = outcome([(5.0, 0.15, 0.15)])
    assert event.outcome == "did_not_stop"
    assert event.min_speed > 1.0 and event.hold_s < 0.2


def test_a_vehicle_first_seen_at_the_line_cannot_be_judged() -> None:
    event = run(StopSignRule([SIGN]), positions([(1.0, 0.15, 0.15)], x0=0.46))[0]
    assert event.outcome == "indeterminate"
    assert event.observed_s < SIGN.min_observed_s


def test_a_vehicle_the_tracker_lost_and_found_again_at_the_line_cannot_be_judged() -> None:
    """The drive was a good stop, but only its last third of a second was seen under this number."""
    xs = list(positions(FULL_STOP))
    crossing = next(i for i, x in enumerate(xs) if x > 0.5)
    (event,) = run(StopSignRule([SIGN]), xs[crossing - 10 :])
    assert event.outcome == "indeterminate"
    assert event.observed_s < SIGN.min_observed_s


def test_a_short_look_is_not_enough_even_when_a_speed_could_be_measured() -> None:
    """Seen for about 0.75 s before the line: a whole half second to measure a speed in, but too
    little to say that the vehicle did not stop (it might have, just before it was first seen)."""
    (event,) = run(StopSignRule([SIGN]), positions([(1.5, 0.15, 0.15)], x0=0.42))
    assert event.outcome == "indeterminate"
    assert 0.5 < event.observed_s < SIGN.min_observed_s
    assert event.min_speed > SIGN.roll_speed  # it was measured, and it was not slow


# --- what counts as still, and as slow ------------------------------------------------------------


def test_a_stop_is_a_stop_wherever_in_the_zone_it_happens() -> None:
    """A queue: the vehicle waits well back from the line, then creeps up and goes through."""
    queue = [(1.0, 0.1, 0.1), (3.0, 0.0, 0.0), (6.0, 0.03, 0.03), (2.0, 0.15, 0.15)]
    assert outcome(queue).outcome == "stopped"


def test_how_long_it_waited_decides_between_stopped_and_rolling() -> None:
    def waits(seconds: float) -> StopEvent:
        return outcome(
            [
                (1.5, 0.15, 0.15),
                (0.4, 0.15, 0.0),
                (seconds, 0.0, 0.0),
                (0.4, 0.0, 0.15),
                (2.0, 0.15, 0.15),
            ]
        )

    assert waits(0.3).outcome != "stopped"
    assert waits(0.3).hold_s < SIGN.hold_s
    assert waits(2.0).outcome == "stopped"
    assert waits(2.0).hold_s >= SIGN.hold_s


def test_a_slow_drift_is_not_a_stop() -> None:
    """Creeping at 0.03 of the picture a second is still a car's length in about 4 s."""
    event = outcome(
        [
            (1.5, 0.15, 0.15),
            (1.0, 0.15, 0.03),
            (3.0, 0.03, 0.03),
            (1.0, 0.03, 0.15),
            (2.0, 0.15, 0.15),
        ]
    )
    assert event.outcome == "rolling"


def test_detector_jitter_does_not_stop_a_vehicle_from_having_stopped() -> None:
    """The detector's box shakes by a pixel or two on a vehicle that is not moving at all."""
    assert outcome(FULL_STOP, noise=0.003).outcome == "stopped"


def test_a_very_shaky_detector_leaves_it_undecided_rather_than_inventing_a_stop() -> None:
    event = outcome(FULL_STOP, noise=0.03)
    assert event.outcome != "stopped"


def test_a_pause_in_the_points_is_not_a_stop() -> None:
    """The vehicle went behind something: 1.5 s with no points does not join two short waits."""
    before = list(positions([(1.5, 0.15, 0.15), (0.5, 0.15, 0.0), (0.4, 0.0, 0.0)]))
    rule = StopSignRule([SIGN])
    events: list[StopEvent] = []
    for i, x in enumerate(before):
        events += rule.update(i * FRAME_NS, [track(1, x)])  # type: ignore[arg-type]
    after = len(before) + 45  # nothing is seen for 1.5 s, then it is where it was and moves off
    again = positions([(0.4, 0.0, 0.0), (0.3, 0.0, 0.15), (3.0, 0.15, 0.15)], x0=before[-1])
    for k, x in enumerate(again):
        events += rule.update((after + k) * FRAME_NS, [track(1, x)])  # type: ignore[arg-type]
    (event,) = events
    assert event.hold_s < 1.0  # a wait of 0.4 s and one of about 0.6, not one of 2.5 s
    assert event.outcome != "stopped"


def test_distance_from_the_camera_makes_no_difference() -> None:
    """The same drive and the same shake, a third as big: the zone, the vehicle and its
    movements are all a third the size, as for a stop sign further away."""
    far_zone = Polygon(((0.368, 0.5), (0.5, 0.5), (0.5, 0.95), (0.368, 0.95)))
    far_sign = StopSign("far", far_zone, LINE)

    def far(segments: Sequence[tuple[float, float, float]], noise: float = 0.0) -> StopEvent:
        xs = [0.5 + (x - 0.5) * 0.33 for x in positions(segments)]
        (event,) = run(StopSignRule([far_sign]), xs, scale=0.33, noise=noise * 0.33)
        return event

    assert far(FULL_STOP).outcome == "stopped"
    assert far(ROLLING_STOP).outcome == "rolling"
    assert far([(5.0, 0.15, 0.15)]).outcome == "did_not_stop"
    assert far(FULL_STOP, noise=0.003).outcome == "stopped"
    # and the numbers are the near ones: how long it stood, how slowly it went
    near_event, far_event = outcome(FULL_STOP), far(FULL_STOP)
    assert far_event.hold_s == pytest.approx(near_event.hold_s, abs=0.15)
    assert far_event.min_speed == pytest.approx(near_event.min_speed, abs=0.1)


def test_the_speed_is_the_same_in_a_taller_or_wider_picture() -> None:
    wide = StopSign("main", ZONE, LINE, aspect=2.4)
    (event,) = run(StopSignRule([wide]), positions(FULL_STOP))
    assert event.outcome == "stopped"


# --- which passes are judged -------------------------------------------------------------------


def test_each_vehicle_is_judged_once_per_sign_however_often_it_crosses() -> None:
    back_and_forth = [0.7, 0.6, 0.45, 0.4, 0.45, 0.55, 0.6, 0.45, 0.55, 0.6]
    events = run(StopSignRule([SIGN]), [*positions(FULL_STOP), *back_and_forth])
    assert len(events) == 1


def test_a_vehicle_leaving_the_intersection_is_not_judged() -> None:
    xs = [0.9 - 0.005 * i for i in range(90)]  # from the right of the picture to the left
    assert run(StopSignRule([SIGN]), xs) == []


def test_a_vehicle_that_enters_the_zone_and_turns_away_is_not_judged() -> None:
    xs = [0.15 + 0.004 * i for i in range(60)]
    rule = StopSignRule([SIGN])
    events: list[StopEvent] = []
    for i, x in enumerate(xs):
        events += rule.update(i * FRAME_NS, [track(1, x)])  # type: ignore[arg-type]
    for k in range(
        60
    ):  # then it drives off up the picture, out of the zone, never nearing the line
        events += rule.update((60 + k) * FRAME_NS, [track(1, xs[-1], LANE - 0.005 * k)])  # type: ignore[arg-type]
    assert events == []


def test_a_parked_vehicle_in_the_zone_is_never_judged() -> None:
    assert run(StopSignRule([SIGN]), [0.3] * 600) == []


def test_a_track_that_is_not_confirmed_is_ignored() -> None:
    assert run(StopSignRule([SIGN]), positions([(5.0, 0.15, 0.15)]), confirmed=False) == []


def test_only_the_kinds_that_must_stop_are_judged() -> None:
    cars_only = StopSign("main", ZONE, LINE, classes=frozenset({2}))
    assert run(StopSignRule([cars_only]), positions(FULL_STOP), cls=7) == []  # a truck
    assert len(run(StopSignRule([cars_only]), positions(FULL_STOP), cls=2)) == 1


def test_either_way_across_the_line_can_be_the_one_that_counts() -> None:
    # vehicles drive from the right of the picture to its left, so the zone is on the right
    zone = Polygon(((0.5, 0.5), (0.9, 0.5), (0.9, 0.95), (0.5, 0.95)))
    leftwards = [1 - x for x in positions(FULL_STOP)]
    (event,) = run(StopSignRule([StopSign("main", zone, LINE, direction=-1)]), leftwards)
    assert event.outcome == "stopped"
    # a sign that is about the other way does not judge them
    assert run(StopSignRule([StopSign("main", zone, LINE, direction=1)]), leftwards) == []


def test_two_signs_judge_a_vehicle_separately() -> None:
    first = StopSign("near", ZONE, LINE)
    second_line = Line((0.8, 0.9), (0.8, 0.3))
    second_zone = Polygon(((0.55, 0.5), (0.8, 0.5), (0.8, 0.95), (0.55, 0.95)))
    second = StopSign("far", second_zone, second_line)
    events = run(StopSignRule([first, second]), positions([*FULL_STOP, (2.0, 0.15, 0.15)]))
    assert [(e.sign_id, e.outcome) for e in events] == [
        ("near", "stopped"),
        ("far", "did_not_stop"),
    ]


def test_two_vehicles_at_once_are_judged_by_their_own_movements() -> None:
    rule = StopSignRule([SIGN])
    driver = list(positions(FULL_STOP, x0=0.12))
    speeder = list(positions([(7.0, 0.15, 0.15)], x0=0.12))
    events: list[StopEvent] = []
    for i in range(max(len(driver), len(speeder))):
        tracks = []
        if i < len(driver):
            tracks.append(track(1, driver[i]))
        if i < len(speeder):
            tracks.append(track(2, speeder[i], LANE - 0.1))
        events += rule.update(i * FRAME_NS, tracks)  # type: ignore[arg-type]
    by_track = {e.track_id: e.outcome for e in events}  # type: ignore[attr-defined]
    assert by_track == {1: "stopped", 2: "did_not_stop"}


def test_the_event_says_what_was_seen() -> None:
    event = outcome(FULL_STOP, start_ns=5_000_000_000)
    assert (event.track_id, event.sign_id, event.label, event.class_id) == (1, "main", "car", 2)
    assert event.timestamp_ns > 5_000_000_000 + 4 * 1_000_000_000  # the crossing, late in the drive
    assert 3.0 < event.observed_s < 6.0


def test_nothing_is_kept_for_vehicles_that_will_never_be_judged() -> None:
    """The service runs for months: what is not needed must not pile up."""
    rule = StopSignRule([SIGN])
    for i in range(60):
        rule.update(i * FRAME_NS, [track(1, 0.3, confirmed=False), track(2, 0.3, cls=7)])  # type: ignore[arg-type]
    cars_only = StopSignRule([StopSign("main", ZONE, LINE, classes=frozenset({2}))])
    for i in range(60):
        cars_only.update(i * FRAME_NS, [track(2, 0.3, cls=7)])  # type: ignore[arg-type]
    assert not cars_only._samples  # a truck, where only cars must stop
    unconfirmed_only = StopSignRule([SIGN])
    for i in range(60):
        unconfirmed_only.update(i * FRAME_NS, [track(1, 0.3, confirmed=False)])  # type: ignore[arg-type]
    assert not unconfirmed_only._samples


def test_after_a_vehicle_is_judged_it_is_no_longer_followed() -> None:
    rule = StopSignRule([SIGN])
    events = run(
        rule, [*positions(FULL_STOP), *([0.4] * 90)]
    )  # it drives back into the zone and waits
    assert len(events) == 1
    assert not rule._samples  # and what it does there is not kept


def test_forgetting_a_track_forgets_what_was_seen_of_it() -> None:
    rule = StopSignRule([SIGN])
    for i, x in enumerate([0.2] * 30):
        rule.update(i * FRAME_NS, [track(9, x)])  # type: ignore[arg-type]
    rule.forget([9])
    assert not rule._samples and not rule._judged
    # the number is used again by another vehicle: it starts afresh, nothing carried over
    event = run(StopSignRule([SIGN]), positions([(5.0, 0.15, 0.15)]), track_id=9)[0]
    assert event.outcome == "did_not_stop"


# --- the measures themselves -------------------------------------------------------------------


def samples(points: Sequence[tuple[float, float, float]], size: float = 0.17) -> list[_Sample]:
    return [_Sample(round(t * 1e9), x, y, size) for t, x, y in points]


def test_the_longest_still_run_is_found_among_movement() -> None:
    pts = [(i * 0.1, 0.1 * i, 0.8) for i in range(10)]  # moving for a second
    pts += [(1.0 + i * 0.1, 1.0, 0.8) for i in range(1, 21)]  # then still for 2 s
    pts += [(3.0 + i * 0.1, 1.0 + 0.1 * i, 0.8) for i in range(1, 6)]  # then off
    assert longest_still(samples(pts), SIGN) == pytest.approx(2.0, abs=0.15)


def test_a_vehicle_still_to_the_end_is_measured_in_one_pass() -> None:
    pts = [(i * 0.06, 0.4, 0.8) for i in range(2_000)]
    assert longest_still(samples(pts), SIGN) == pytest.approx(119.94)


def test_the_slowest_speed_is_in_the_vehicles_own_sizes_a_second() -> None:
    # 0.17 in size, moving at 0.17 a second (one size a second) for a while: 1.0
    pts = [(i * 0.1, 0.17 * i * 0.1, 0.8) for i in range(30)]
    assert slowest(samples(pts), SIGN) == pytest.approx(1.0)
    still = [(i * 0.1, 0.5, 0.8) for i in range(30)]
    assert slowest(samples(still), SIGN) == pytest.approx(0.0)


def test_a_speed_needs_a_whole_window_of_points() -> None:
    assert slowest(samples([(0.0, 0.1, 0.8), (0.2, 0.2, 0.8)]), SIGN) is None
    assert slowest([], SIGN) is None
    broken = samples([(0.0, 0.1, 0.8), (0.1, 0.11, 0.8), (1.5, 0.3, 0.8), (1.6, 0.31, 0.8)])
    assert slowest(broken, SIGN) is None  # no half second without a break in it


# --- the fastest speed: the same windows, but only those that can be trusted ---------------------


def test_the_fastest_speed_is_in_the_vehicles_own_sizes_a_second_like_the_slowest() -> None:
    steady = [(i * 0.1, 0.17 * i * 0.1, 0.8) for i in range(30)]  # one size a second throughout
    assert fastest(samples(steady), SIGN) == pytest.approx(1.0)
    assert slowest(samples(steady), SIGN) == pytest.approx(1.0)


def test_the_fastest_speed_is_where_the_vehicle_went_fastest() -> None:
    slow = [(i * 0.1, 0.17 * i * 0.1, 0.8) for i in range(15)]  # a size a second for 1.5 s ...
    x0 = slow[-1][1]
    fast = [(1.5 + i * 0.1, x0 + 0.17 * 3 * i * 0.1, 0.8) for i in range(1, 16)]  # ... then three
    pts = samples(slow + fast)
    assert fastest(pts, SIGN) == pytest.approx(3.0)
    assert slowest(pts, SIGN) == pytest.approx(1.0)


def test_a_fastest_speed_needs_a_whole_window_of_points_like_a_slowest() -> None:
    assert fastest(samples([(0.0, 0.1, 0.8), (0.2, 0.2, 0.8)]), SIGN) is None
    assert fastest([], SIGN) is None
    broken = samples([(0.0, 0.1, 0.8), (0.1, 0.11, 0.8), (1.5, 0.3, 0.8), (1.6, 0.31, 0.8)])
    assert fastest(broken, SIGN) is None


def test_the_fastest_is_never_less_than_the_slowest() -> None:
    rng = random.Random(7)
    for _ in range(40):
        x, pts = 0.1, []
        for i in range(40):
            x += rng.uniform(0.0, 0.03)
            pts.append((i * 0.1, x, 0.8))
        quick, slow = fastest(samples(pts), SIGN), slowest(samples(pts), SIGN)
        assert slow is not None
        assert quick is None or quick >= slow - 1e-9


def test_a_jump_of_the_track_is_not_a_speed() -> None:
    """Two cars close together swap numbers, and the track leaps across the gap between them in
    one frame. That is not a car doing 5 sizes a second: the motion is all in one half of the
    window, and a speed counts only if it is steady through it."""
    pts = [
        (i * 0.1, 0.3 + 0.017 * i * 0.1 * 0.5, 0.8) for i in range(30)
    ]  # a creep: 0.1 a size a s
    jumped = [(t, x + (0.35 if t >= 1.5 else 0.0), y) for t, x, y in pts]  # one leap of 0.35
    assert fastest(samples(jumped), SIGN) == pytest.approx(0.05, abs=0.02)
    # the same leap gives the slowest nothing to worry about, but the windows that hold it would
    # have made the fastest 0.35 / 0.5 s / 0.17 = 4.1 sizes a second
    assert 0.35 / 0.5 / 0.17 > 4.0


def test_a_window_whose_two_ends_are_very_different_in_size_is_not_trusted() -> None:
    """The box is of another vehicle at one end, or half hidden: a speed in sizes means nothing."""
    steady = [_Sample(round(i * 0.1 * 1e9), 0.17 * i * 0.1, 0.8, 0.17) for i in range(30)]
    swapped = [
        _Sample(s.t_ns, s.x, s.y, s.size * (2.0 if s.t_ns >= 1.5e9 else 1.0)) for s in steady
    ]
    # windows that end in the other size are left out; windows wholly inside either are not
    assert fastest(swapped, SIGN) == pytest.approx(1.0, abs=0.1)
    only_mixed = [steady[0], steady[3], steady[6]]
    only_mixed = [
        _Sample(s.t_ns, s.x, s.y, s.size * (2.0 if i else 1.0)) for i, s in enumerate(only_mixed)
    ]
    assert fastest(only_mixed, SIGN) is None


def test_a_speed_faster_than_any_road_vehicle_is_a_glitch_and_left_out() -> None:
    pts = [(i * 0.1, 0.05 + 0.17 * 30 * i * 0.1, 0.8) for i in range(8)]  # 30 sizes a second
    assert fastest(samples(pts), SIGN) is None
    assert slowest(samples(pts), SIGN) == pytest.approx(30.0)  # the slowest is not filtered


def test_a_pass_records_the_fastest_it_went_and_the_slowest() -> None:
    (miss,) = run(StopSignRule([SIGN]), positions([(5.0, 0.15, 0.15)]))
    assert miss.outcome == "did_not_stop"
    assert miss.max_speed == pytest.approx(1.54, abs=0.02)  # 0.15 a second of the width
    assert miss.max_speed == pytest.approx(miss.min_speed, abs=0.02)  # at one speed throughout
    (roll,) = run(StopSignRule([SIGN]), positions(ROLLING_STOP))
    assert roll.outcome == "rolling"
    assert roll.max_speed == pytest.approx(1.54, abs=0.05)  # it arrived at the same speed
    assert roll.min_speed < 0.5 < roll.max_speed


def test_a_stop_records_how_fast_the_vehicle_arrived() -> None:
    (stop,) = run(StopSignRule([SIGN]), positions(FULL_STOP))
    assert stop.outcome == "stopped"
    assert stop.max_speed == pytest.approx(1.54, abs=0.05)


def test_a_pass_with_no_whole_window_has_no_fastest_speed() -> None:
    first_seen_at_the_line = positions([(1.0, 0.15, 0.15)], x0=0.46)
    (event,) = run(StopSignRule([SIGN]), first_seen_at_the_line)
    assert event.outcome == "indeterminate" and event.max_speed == -1.0


def test_a_slow_jerky_crawl_still_has_a_fastest_speed() -> None:
    """The steady test is for fast windows, where a leap could pass for a speed. A crawl that
    moves in the first half of a window and not in the second is slow, whatever its rhythm: it is
    measured, or every slow, shaky pass would be recorded as "not measured"."""
    pts = [(0.0, 0.300, 0.8), (0.1, 0.316, 0.8), (0.2, 0.332, 0.8)]
    pts += [(0.3, 0.332, 0.8), (0.4, 0.332, 0.8), (0.5, 0.332, 0.8)]
    assert fastest(samples(pts), SIGN) == pytest.approx(0.032 / (0.5 * 0.17))


# --- the speeds of a calibrated camera (plan.md M6) -----------------------------------------------


def measured_run(
    speeds: Sequence[float | None], xs: Sequence[float], rule: StopSignRule | None = None
) -> StopEvent:
    """Drive a vehicle along ``xs`` with the speed meter's reading at each frame."""
    from dataclasses import replace

    rule = rule or StopSignRule([SIGN])
    events: list[StopEvent] = []
    for i, (x, speed) in enumerate(zip(xs, speeds, strict=True)):
        for event in rule.update(i * FRAME_NS, [replace(track(1, x), speed_ms=speed)]):
            assert isinstance(event, StopEvent)
            events.append(event)
    (event,) = events
    return event


def test_a_pass_records_the_slowest_and_the_fastest_speed_the_meter_measured_in_the_zone() -> None:
    xs = list(positions(STRAIGHT_THROUGH))
    # Fast on the way in, slowest in the middle of the zone, and a little quicker at the line.
    speeds = [8.0 - 4.0 * math.sin(math.pi * min(1.0, i / 90)) for i in range(len(xs))]
    event = measured_run(speeds, xs)
    zone_start = next(i for i, x in enumerate(xs) if x >= 0.1)
    inside = speeds[zone_start : next(i for i, x in enumerate(xs) if x > 0.5)]
    assert event.min_speed_ms == pytest.approx(min(inside), abs=0.06)
    assert event.max_speed_ms == pytest.approx(max(inside), abs=0.06)


def test_what_was_measured_outside_the_zone_is_not_part_of_the_pass() -> None:
    xs = list(positions(STRAIGHT_THROUGH, x0=0.02))  # starts left of the zone, which begins at 0.1
    speeds = [30.0 if x < 0.1 else 6.0 for x in xs]
    event = measured_run(speeds, xs)
    assert event.max_speed_ms == pytest.approx(6.0)
    assert event.min_speed_ms == pytest.approx(6.0)


def test_the_speed_at_the_line_is_the_last_one_measured_before_it() -> None:
    xs = list(positions(STRAIGHT_THROUGH))
    speeds = [10.0 - 5.0 * i / len(xs) for i in range(len(xs))]
    event = measured_run(speeds, xs)
    crossing = next(i for i, x in enumerate(xs) if x > 0.5)
    assert event.line_speed_ms == pytest.approx(speeds[crossing - 1], abs=0.2)
    assert event.line_speed_ms < event.max_speed_ms


def test_a_frame_with_no_speed_is_skipped_and_one_with_none_at_all_leaves_minus_one() -> None:
    xs = list(positions(STRAIGHT_THROUGH))
    some = [5.0 if i % 3 else None for i in range(len(xs))]
    event = measured_run(some, xs)
    assert event.min_speed_ms == event.max_speed_ms == 5.0
    none = measured_run([None] * len(xs), xs)
    assert (none.min_speed_ms, none.max_speed_ms, none.line_speed_ms) == (-1.0, -1.0, -1.0)


def test_a_speed_measured_long_before_the_line_is_not_the_speed_at_it() -> None:
    """The vehicle was last measured more than two seconds before the crossing was decided: that
    is the slowest and fastest it went, but not how fast it went at the line."""
    xs = list(positions(STRAIGHT_THROUGH))
    crossing = next(i for i, x in enumerate(xs) if x > 0.5)
    speeds: list[float | None] = [7.0] * len(xs)
    for i in range(crossing - 66, len(xs)):  # nothing measured for the last 2.2 s or more
        speeds[i] = None
    event = measured_run(speeds, xs)
    assert event.max_speed_ms == pytest.approx(7.0)
    assert event.line_speed_ms == -1.0


def test_a_speed_measured_a_moment_before_the_line_is_the_speed_at_it() -> None:
    """The crossing is only decided once the vehicle is clear of the line, so the last speed in the
    zone is a few frames older than the event, and it is still the speed at the line."""
    xs = list(positions(STRAIGHT_THROUGH))
    crossing = next(i for i, x in enumerate(xs) if x > 0.5)
    speeds: list[float | None] = [7.0] * len(xs)
    for i in range(crossing - 20, len(xs)):  # the last 0.7 s are not measured
        speeds[i] = None
    assert measured_run(speeds, xs).line_speed_ms == pytest.approx(7.0)


def test_the_outcome_does_not_depend_on_the_speed_the_meter_gave() -> None:
    xs = list(positions(FULL_STOP))
    plain = measured_run([None] * len(xs), xs)
    wild = measured_run([40.0] * len(xs), xs)  # a meter that is wrong changes no verdict
    assert (plain.outcome, plain.hold_s, plain.min_speed, plain.max_speed) == (
        wild.outcome,
        wild.hold_s,
        wild.min_speed,
        wild.max_speed,
    )


def test_the_speeds_are_rounded_to_a_centimetre_a_second() -> None:
    xs = list(positions(STRAIGHT_THROUGH))
    event = measured_run([6.123456] * len(xs), xs)
    assert event.max_speed_ms == 6.12 and event.min_speed_ms == 6.12


@pytest.mark.parametrize("field", ["min_speed_ms", "max_speed_ms", "line_speed_ms"])
def test_a_stop_has_a_speed_when_any_of_its_speeds_in_metres_was_measured(field: str) -> None:
    bare = StopEvent(0, 7, 2, "car", "elm", "stopped", 1.0, 0.0, 4.0)
    assert not bare.has_speed
    assert replace(bare, **{field: 0.0}).has_speed
    assert replace(bare, **{field: 6.5}).has_speed
    assert not replace(bare, **{field: -1.0}).has_speed
