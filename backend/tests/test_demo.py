import itertools
import logging
import statistics
from collections import Counter
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from trafficpi.db import create_db_engine, upgrade_to_head
from trafficpi.db.store import Store
from trafficpi.vision import cli
from trafficpi.vision.demo import (
    FPS,
    FRAME_SIZE,
    HOURLY_RATE,
    KINDS,
    LANES,
    PERIOD_NS,
    DemoStreet,
    demo_history,
)
from trafficpi.vision.service import VisionService
from trafficpi.vision.shutdown import StopRequest
from trafficpi.vision.trails import MIN_EXTENT, decode_path


class FakeTime:
    """A clock that only moves when the street sleeps, so a minute of traffic takes no time."""

    def __init__(self) -> None:
        self.now_ns = 5_000_000_000
        self.slept_s = 0.0

    def monotonic_ns(self) -> int:
        return self.now_ns

    def sleep(self, seconds: float) -> None:
        self.slept_s += seconds
        self.now_ns += round(seconds * 1e9)


def street(seed: int = 7) -> tuple[DemoStreet, FakeTime]:
    clock = FakeTime()
    demo = DemoStreet(seed, monotonic_ns=clock.monotonic_ns, sleep=clock.sleep)
    demo.start()
    return demo, clock


def watch(demo: DemoStreet, frames: int):  # type: ignore[no-untyped-def]
    for _ in range(frames):
        frame = demo.read()
        yield frame, demo.detect(frame.image)


def test_frames_are_pictures_at_thirty_a_second() -> None:
    demo, clock = street()
    frames = [frame for frame, _ in watch(demo, 90)]
    assert (
        frames[0].image.shape == (FRAME_SIZE[1], FRAME_SIZE[0], 3)
        and frames[0].image.dtype.name == "uint8"
    )
    assert [b.timestamp_ns - a.timestamp_ns for a, b in itertools.pairwise(frames)] == [
        PERIOD_NS
    ] * 89
    assert [f.index for f in frames[:3]] == [0, 1, 2]
    assert clock.slept_s == pytest.approx(89 / FPS, abs=0.05)  # paced like a camera, not faster


def test_the_same_seed_gives_the_same_traffic() -> None:
    def trace(seed: int) -> list[tuple[str, float, float]]:
        demo, _ = street(seed)
        return [
            (d.label, round(d.x0, 3), round(d.y1, 3))
            for _, found in watch(demo, 600)
            for d in found
        ]

    assert trace(7) == trace(7)
    assert trace(7) != trace(8)


def test_vehicles_drive_across_and_stay_inside_the_picture() -> None:
    demo, _ = street()
    seen: set[str] = set()
    sizes = []
    for _, found in watch(demo, 30 * 90):  # a minute and a half
        sizes.append(len(found))
        for d in found:
            seen.add(d.label)
            assert 0.0 <= d.x0 < d.x1 <= 1.0 and 0.0 <= d.y0 < d.y1 <= 1.0
            assert 0.5 <= d.score <= 1.0
    assert {"car", "truck", "bicycle"} <= seen  # a mix, led by cars
    assert max(sizes) >= 3  # busy at times
    assert sum(1 for n in sizes if n > 0) > len(sizes) / 2  # and mostly something to see


def lane_of(d) -> str:  # type: ignore[no-untyped-def]
    return "near" if d.y1 > 0.72 else "far"  # the lane nearer the camera sits lower in the picture


def test_vehicles_in_a_lane_never_overlap() -> None:
    demo, _ = street()
    for _, found in watch(demo, 30 * 120):
        for lane in ("near", "far"):
            row = sorted((d for d in found if lane_of(d) == lane), key=lambda d: d.x0)
            for a, b in itertools.pairwise(row):
                assert a.x1 <= b.x0 + 0.02, "two vehicles in one lane overlap"


def test_the_near_lane_goes_right_and_the_far_lane_goes_left() -> None:
    demo, _ = street()
    moves: dict[str, list[float]] = {"near": [], "far": []}
    previous: list = []  # type: ignore[type-arg]
    for _, found in watch(demo, 30 * 90):
        for d in found:
            centre = (d.x0 + d.x1) / 2
            near = [
                p
                for p in previous
                if lane_of(p) == lane_of(d) and abs((p.x0 + p.x1) / 2 - centre) < 0.02
            ]
            if near and 0.1 < centre < 0.9:  # not while it is entering or leaving the picture
                moves[lane_of(d)].append(centre - (near[0].x0 + near[0].x1) / 2)
        previous = found
    assert len(moves["near"]) > 100 and len(moves["far"]) > 100
    # The boxes jitter a little, like a real detector's, so single steps can be wrong; most are not.
    assert statistics.mean(moves["near"]) > 0.004 and statistics.mean(moves["far"]) < -0.004
    assert sum(step > 0 for step in moves["near"]) > 0.9 * len(moves["near"])
    assert sum(step < 0 for step in moves["far"]) > 0.9 * len(moves["far"])


def test_a_counting_line_counts_the_demo_traffic_in_both_directions() -> None:
    engine = create_db_engine(":memory:")
    upgrade_to_head(engine)
    store = Store(engine)
    store.ensure_camera()
    store.set_profile(
        {"lines": [{"id": "mid", "name": "Middle", "a": [0.5, 0.4], "b": [0.5, 0.9]}]}
    )
    demo, _ = street()
    service = VisionService(demo, demo, store)
    service.run(StopRequest(), max_frames=30 * 90)
    events = store.recent_events(limit=500)
    assert len(events) >= 15
    assert {e.direction for e in events} == {1, -1}
    assert {e.class_label for e in events} >= {"car"}
    assert all(e.line_id == "mid" for e in events)


# --- the made-up past ---------------------------------------------------------------------------

NEW_YORK = ZoneInfo("America/New_York")
THURSDAY_NOON = int(datetime(2026, 10, 8, 12, 0, tzinfo=NEW_YORK).timestamp() * 1000)


def local_date(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, NEW_YORK).date().isoformat()


def test_the_history_covers_each_day_before_today_and_not_today() -> None:
    trails = demo_history(3, now_ms=THURSDAY_NOON, tz=NEW_YORK)
    days = Counter(local_date(t.started_at) for t in trails)
    assert sorted(days) == ["2026-10-05", "2026-10-06", "2026-10-07"]
    assert all(3_000 < n < 7_000 for n in days.values())  # about a weekday's worth each
    assert [t.started_at for t in trails] == sorted(t.started_at for t in trails)


def test_no_days_is_no_history() -> None:
    assert demo_history(0, now_ms=THURSDAY_NOON, tz=NEW_YORK) == []


def test_the_same_seed_gives_the_same_history_and_another_gives_another() -> None:
    first = demo_history(1, now_ms=THURSDAY_NOON, tz=NEW_YORK, seed=3)
    again = demo_history(1, now_ms=THURSDAY_NOON, tz=NEW_YORK, seed=3)
    other = demo_history(1, now_ms=THURSDAY_NOON, tz=NEW_YORK, seed=4)
    assert first == again
    assert first != other


def test_the_rush_hours_are_busier_than_the_night_and_weekends_than_weekdays() -> None:
    # Monday 5 October to Sunday 11 October; "today" is the Monday after
    now = int(datetime(2026, 10, 12, 12, 0, tzinfo=NEW_YORK).timestamp() * 1000)
    trails = demo_history(7, now_ms=now, tz=NEW_YORK)
    by_hour = Counter(datetime.fromtimestamp(t.started_at / 1000, NEW_YORK).hour for t in trails)
    assert by_hour[8] > 8 * by_hour[3] and by_hour[17] > by_hour[8]
    assert HOURLY_RATE[17] == max(HOURLY_RATE)
    by_day = Counter(local_date(t.started_at) for t in trails)
    assert by_day["2026-10-10"] < 0.85 * by_day["2026-10-08"]  # a Saturday and a Thursday
    assert by_day["2026-10-11"] < 0.85 * by_day["2026-10-09"]  # a Sunday and a Friday


def test_every_made_up_trail_is_a_vehicle_driving_along_a_lane_the_recorder_would_keep() -> None:
    trails = demo_history(1, now_ms=THURSDAY_NOON, tz=NEW_YORK)[:400]
    kinds = {kind.name for kind in KINDS}
    for trail in trails:
        path = decode_path(trail.path)
        assert trail.label in kinds
        assert trail.points == len(path) and trail.points >= 3
        assert trail.ended_at - trail.started_at == path.offsets_ms[-1]
        assert trail.extent >= MIN_EXTENT and trail.extent > 0.8  # all the way across
        assert (path.offsets_ms[1:] > path.offsets_ms[:-1]).all()  # time only goes forward
        assert path.x.min() >= 0.0 and path.x.max() <= 1.0
        lane = min(LANES.values(), key=lambda bottom: abs(bottom - path.y.mean()))
        assert abs(path.y.mean() - lane) < 0.02
        assert abs(path.y.max() - path.y.min()) < 0.02  # a vehicle holds its lane
    # in both directions, in both lanes
    directions = {decode_path(t.path).x[-1] > decode_path(t.path).x[0] for t in trails}
    assert directions == {True, False}


def test_a_vehicle_takes_as_long_to_cross_as_the_live_street_gives_it() -> None:
    trails = demo_history(1, now_ms=THURSDAY_NOON, tz=NEW_YORK)
    seconds = {k.name: [] for k in KINDS}
    for trail in trails:
        seconds[trail.label].append((trail.ended_at - trail.started_at) / 1000)
    for kind in KINDS:
        slowest, fastest = 0.94 / kind.speed[0], 0.94 / kind.speed[1]
        assert fastest - 0.1 <= min(seconds[kind.name]) and max(seconds[kind.name]) <= slowest + 0.1


def test_the_demo_is_given_a_past_once_and_only_if_it_has_none(
    caplog: pytest.LogCaptureFixture,
) -> None:
    engine = create_db_engine(":memory:")
    upgrade_to_head(engine)
    store = Store(engine)
    store.ensure_camera()
    store.set_setting("timezone", "America/New_York")
    log = logging.getLogger("trafficpi.vision")
    with caplog.at_level(logging.WARNING, logger="trafficpi.vision"):
        cli._seed_demo_history(store, 2, log)
    written = store.trail_count()
    assert written > 5_000
    assert f"wrote {written} made-up trails for the 2 days" in caplog.text
    # a second start of the same demo keeps what it has: the same days, not twice as many
    cli._seed_demo_history(store, 2, log)
    assert store.trail_count() == written
    # and a demo that has live trails of its own is left alone
    other_engine = create_db_engine(":memory:")
    upgrade_to_head(other_engine)
    other = Store(other_engine)
    other.ensure_camera()
    other.add_trails(demo_history(1, now_ms=THURSDAY_NOON, tz=NEW_YORK)[:1])
    cli._seed_demo_history(other, 2, log)
    assert other.trail_count() == 1
