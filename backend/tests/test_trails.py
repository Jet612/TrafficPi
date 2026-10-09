import random
import struct
import zlib

import numpy as np
import pytest

from trafficpi.core.coco import class_name
from trafficpi.core.geometry import Polygon
from trafficpi.core.types import Detection
from trafficpi.vision.engine import TrafficEngine
from trafficpi.vision.tracker import TrackUpdate
from trafficpi.vision.trails import (
    FORMAT_VERSION,
    TrailConfig,
    TrailRecorder,
    decode_path,
    encode_path,
    trail_classes,
)
from trafficpi.vision.zones import DetectionFilter

FRAME_MS = 33  # a 30 fps camera
EPOCH_MS = 1_700_000_000_000


def track(
    track_id: int, x: float, y: float, *, label: str = "car", confirmed: bool = True
) -> TrackUpdate:
    """A track whose ground point (middle of the bottom edge) is at (x, y)."""
    return TrackUpdate(
        id=track_id,
        class_id=2,
        label=label,
        score=0.9,
        box=(x - 0.04, y - 0.07, x + 0.04, y),
        velocity=(0.0, 0.0),
        hits=10,
        age_s=1.0,
        confirmed=confirmed,
    )


def drive(
    recorder: TrailRecorder,
    *,
    track_id: int = 1,
    frames: int = 100,
    start: tuple[float, float] = (0.5, 0.2),
    step: tuple[float, float] = (0.0, 0.006),
    label: str = "car",
    begin_ms: int = EPOCH_MS,
):
    """A vehicle driving for ``frames`` frames; returns everything the recorder finished."""
    done = []
    for i in range(frames):
        x, y = start[0] + step[0] * i, start[1] + step[1] * i
        done += recorder.update(begin_ms + i * FRAME_MS, [track(track_id, x, y, label=label)], [])
    return done


# --- the stored form ------------------------------------------------------------------------------


def test_a_path_survives_encoding_to_within_a_hundred_thousandth_of_the_picture() -> None:
    rng = random.Random(7)
    offsets = sorted(rng.randrange(0, 90_000) for _ in range(200))
    xs = [rng.random() for _ in offsets]
    ys = [rng.random() for _ in offsets]
    back = decode_path(encode_path(offsets, xs, ys))
    assert back.offsets_ms.tolist() == offsets
    assert np.max(np.abs(back.x - xs)) <= 0.5 / 65535 + 1e-12
    assert np.max(np.abs(back.y - ys)) <= 0.5 / 65535 + 1e-12
    assert len(back) == 200


def test_a_vehicle_crossing_the_picture_takes_a_few_hundred_bytes_at_most() -> None:
    offsets = [i * 200 for i in range(40)]  # eight seconds at five a second
    xs = [0.1 + 0.02 * i for i in range(40)]
    ys = [0.6 + 0.001 * i for i in range(40)]
    assert len(encode_path(offsets, xs, ys)) < 250
    # and a five-minute path, the longest one saved in one piece, stays small too
    long_offsets = [i * 200 for i in range(1500)]
    long_xs = [0.5 + 0.3 * np.sin(i / 50) for i in range(1500)]
    long_ys = [0.5 + 0.3 * np.cos(i / 70) for i in range(1500)]
    assert len(encode_path(long_offsets, long_xs, long_ys)) < 12_000


def test_coordinates_outside_the_picture_are_clipped_not_wrapped() -> None:
    back = decode_path(encode_path([0, 100], [-0.2, 1.3], [0.5, 2.0]))
    assert back.x.tolist() == [0.0, 1.0]
    assert back.y[1] == 1.0


def test_an_empty_path_and_a_single_point_round_trip() -> None:
    assert len(decode_path(encode_path([], [], []))) == 0
    one = decode_path(encode_path([0], [0.25], [0.75]))
    assert (one.x[0], one.y[0]) == pytest.approx((0.25, 0.75), abs=1e-5)


def test_mismatched_columns_are_refused() -> None:
    with pytest.raises(ValueError, match="as many"):
        encode_path([0, 1], [0.1], [0.1, 0.2])


@pytest.mark.parametrize(
    "blob",
    [
        b"",
        b"not compressed at all",
        zlib.compress(b"\x01"),  # too short for its header
        zlib.compress(struct.pack("<BI", FORMAT_VERSION + 1, 0)),  # a format from the future
        zlib.compress(struct.pack("<BI", FORMAT_VERSION, 5) + b"\x00" * 7),  # the wrong length
    ],
)
def test_something_that_is_not_a_stored_path_is_refused_with_a_reason(blob: bytes) -> None:
    with pytest.raises(ValueError):
        decode_path(blob)


# --- what is kept ---------------------------------------------------------------------------------


def test_a_car_driving_across_the_picture_becomes_one_trail() -> None:
    recorder = TrailRecorder()
    assert drive(recorder) == []  # nothing is finished while the car is still going
    assert recorder.following == 1
    (trail,) = recorder.update(EPOCH_MS + 100 * FRAME_MS, [], [1])
    assert recorder.following == 0
    assert (trail.label, trail.started_at) == ("car", EPOCH_MS)
    assert (
        trail.ended_at == EPOCH_MS + 99 * FRAME_MS
    )  # when it was last seen, not when it was dropped
    assert trail.extent == pytest.approx(0.594, abs=0.001)

    path = decode_path(trail.path)
    assert len(path) == trail.points
    assert path.offsets_ms[0] == 0 and path.offsets_ms[-1] == 99 * FRAME_MS
    assert (path.x[0], path.y[0]) == pytest.approx((0.5, 0.2), abs=1e-4)
    assert (path.x[-1], path.y[-1]) == pytest.approx(
        (0.5, 0.2 + 0.006 * 99), abs=1e-4
    )  # the last position, too
    assert np.all(np.diff(path.offsets_ms) > 0)


def test_points_are_taken_about_five_times_a_second() -> None:
    recorder = TrailRecorder()
    drive(recorder, frames=300)  # ten seconds
    (trail,) = recorder.update(EPOCH_MS + 300 * FRAME_MS, [], [1])
    gaps = np.diff(decode_path(trail.path).offsets_ms)
    assert 48 <= trail.points <= 54  # five a second for ten seconds, and the last position
    assert np.all(
        gaps[:-1] >= 180
    )  # all but the last, which is the final position wherever it fell
    assert np.all(gaps[:-1] <= 230)


def test_a_parked_car_that_flickers_is_not_a_trail_however_long_it_stays() -> None:
    rng = random.Random(1)
    recorder = TrailRecorder()
    for i in range(3000):  # a hundred seconds of detections jittering by a third of a percent
        x, y = 0.3 + rng.uniform(-0.003, 0.003), 0.7 + rng.uniform(-0.003, 0.003)
        recorder.update(EPOCH_MS + i * FRAME_MS, [track(4, x, y)], [])
    assert recorder.update(EPOCH_MS + 3000 * FRAME_MS, [], [4]) == []
    assert recorder.following == 0  # and nothing is left behind


def test_a_vehicle_that_stops_for_a_while_is_still_one_trail_with_the_wait_in_it() -> None:
    recorder = TrailRecorder()
    t = EPOCH_MS
    for i in range(60):  # drives up to a stop line
        recorder.update(t, [track(2, 0.2 + 0.01 * i, 0.6)], [])
        t += FRAME_MS
    for _ in range(150):  # waits for five seconds
        recorder.update(t, [track(2, 0.79, 0.6)], [])
        t += FRAME_MS
    for i in range(60):  # and goes on
        recorder.update(t, [track(2, 0.79 + 0.003 * i, 0.6)], [])
        t += FRAME_MS
    (trail,) = recorder.update(t, [], [2])
    assert trail.ended_at - trail.started_at == pytest.approx(269 * FRAME_MS, abs=FRAME_MS)
    assert trail.extent > 0.5


def test_a_track_is_not_followed_until_it_is_confirmed() -> None:
    recorder = TrailRecorder()
    for i in range(5):
        recorder.update(EPOCH_MS + i * FRAME_MS, [track(3, 0.1 * i, 0.5, confirmed=False)], [])
    assert recorder.following == 0
    assert (
        recorder.update(EPOCH_MS + 6 * FRAME_MS, [], [3]) == []
    )  # dropped before ever being confirmed
    # confirmed later: it starts then, not at its first sighting
    recorder = TrailRecorder()
    begin = EPOCH_MS + 10 * FRAME_MS
    recorder.update(EPOCH_MS, [track(3, 0.1, 0.5, confirmed=False)], [])
    done = []
    for i in range(80):
        done += recorder.update(begin + i * FRAME_MS, [track(3, 0.1 + 0.01 * i, 0.5)], [])
    (trail,) = recorder.update(begin + 81 * FRAME_MS, [], [3])
    assert trail.started_at == begin


def test_people_are_not_followed_unless_they_are_switched_on() -> None:
    walker = {"start": (0.2, 0.5), "step": (0.004, 0.0), "label": "person"}
    assert drive(TrailRecorder(), **walker) == []
    off = TrailRecorder()
    drive(off, **walker)
    assert off.following == 0 and off.update(EPOCH_MS, [], [1]) == []

    on = TrailRecorder(TrailConfig(classes=trail_classes(people=True)))
    drive(on, **walker)
    (trail,) = on.update(EPOCH_MS + 100 * FRAME_MS, [], [1])
    assert trail.label == "person"


def test_the_kinds_that_are_saved_are_vehicles_and_people_only_when_asked() -> None:
    assert trail_classes() == {"car", "truck", "bus", "motorcycle", "bicycle"}
    assert trail_classes(people=True) == trail_classes() | {"person"}
    assert all(class_name(i) for i in (1, 2, 3, 5, 7))  # these are COCO's names for them


def test_the_label_is_the_one_the_detector_settled_on() -> None:
    recorder = TrailRecorder()
    t = EPOCH_MS
    for i in range(60):
        label = "car" if i < 20 else "truck"  # the vote turns over part-way
        recorder.update(t, [track(1, 0.2 + 0.01 * i, 0.5, label=label)], [])
        t += FRAME_MS
    (trail,) = recorder.update(t, [], [1])
    assert trail.label == "truck"


def test_a_label_that_turns_into_something_unwanted_drops_the_path() -> None:
    recorder = TrailRecorder()
    t = EPOCH_MS
    for i in range(30):
        recorder.update(t, [track(1, 0.2 + 0.01 * i, 0.5)], [])
        t += FRAME_MS
    recorder.update(t, [track(1, 0.5, 0.5, label="person")], [])  # people are off
    assert recorder.following == 0
    assert recorder.update(t + FRAME_MS, [], [1]) == []


def test_very_short_tracks_are_not_trails_even_if_they_jump() -> None:
    recorder = TrailRecorder()
    recorder.update(EPOCH_MS, [track(1, 0.1, 0.1)], [])
    recorder.update(EPOCH_MS + FRAME_MS, [track(1, 0.9, 0.9)], [])
    assert recorder.update(EPOCH_MS + 2 * FRAME_MS, [], [1]) == []  # two points are not a path


def test_a_track_that_goes_on_for_ever_is_saved_in_pieces_and_does_not_grow_without_bound() -> None:
    recorder = TrailRecorder(TrailConfig(max_points=10, sample_interval_ms=100))
    pieces = []
    for i in range(400):  # 400 frames, a point every 4th frame (about 133 ms): 100 points
        pieces += recorder.update(EPOCH_MS + i * FRAME_MS, [track(1, 0.05 + 0.002 * i, 0.5)], [])
    assert len(pieces) >= 8
    assert all(p.points <= 10 for p in pieces)
    assert all(p.started_at < p.ended_at for p in pieces)
    assert pieces == sorted(pieces, key=lambda p: p.started_at)
    assert recorder.following <= 1


def test_a_clock_that_steps_backwards_never_makes_a_path_run_backwards() -> None:
    recorder = TrailRecorder()
    times = [EPOCH_MS + i * FRAME_MS for i in range(40)] + [
        EPOCH_MS + 5_000 + i * FRAME_MS for i in range(40)
    ]
    times[40:] = [t - 3_000 for t in times[40:]]  # the clock jumps back three seconds half-way
    for i, at in enumerate(times):
        recorder.update(at, [track(1, 0.1 + 0.01 * i, 0.5)], [])
    (trail,) = recorder.update(times[-1] + FRAME_MS, [], [1])
    offsets = decode_path(trail.path).offsets_ms
    assert np.all(np.diff(offsets) >= 0)
    assert trail.ended_at >= trail.started_at


def test_stopping_the_service_finishes_every_path_in_progress() -> None:
    recorder = TrailRecorder()
    drive(recorder, track_id=1)
    drive(recorder, track_id=2, start=(0.2, 0.3), step=(0.005, 0.0))
    assert recorder.following == 2
    finished = recorder.finish_all()
    assert sorted(t.track_id for t in finished) == [1, 2]
    assert recorder.following == 0
    assert recorder.finish_all() == []


def test_several_vehicles_at_once_each_get_their_own_trail() -> None:
    recorder = TrailRecorder()
    for i in range(100):
        at = EPOCH_MS + i * FRAME_MS
        recorder.update(
            at,
            [track(1, 0.1 + 0.008 * i, 0.4), track(2, 0.9 - 0.008 * i, 0.7)],
            [],
        )
    finished = recorder.update(EPOCH_MS + 101 * FRAME_MS, [], [1, 2])
    assert {t.track_id for t in finished} == {1, 2}
    first = decode_path(next(t for t in finished if t.track_id == 1).path)
    second = decode_path(next(t for t in finished if t.track_id == 2).path)
    assert first.x[-1] > first.x[0] and second.x[-1] < second.x[0]  # opposite directions


# --- with the real tracker and the zones in front of it ---------------------------------------


def _car_detections(
    frames: int, *, x0: float, y: float, dx: float = 0.006
) -> list[list[Detection]]:
    return [
        [Detection(2, class_name(2), 0.9, x0 + dx * i - 0.04, y - 0.07, x0 + dx * i + 0.04, y)]
        for i in range(frames)
    ]


def _run(engine: TrafficEngine, script: list[list[Detection]]) -> list:
    recorder = TrailRecorder()
    finished = []
    for i, detections in enumerate(script):
        result = engine.update(i * 33_333_333, detections)
        finished += recorder.update(EPOCH_MS + i * FRAME_MS, result.tracks, result.ended)
    for i in range(
        len(script), len(script) + 60
    ):  # the track is dropped a second after it vanishes
        result = engine.update(i * 33_333_333, [])
        finished += recorder.update(EPOCH_MS + i * FRAME_MS, result.tracks, result.ended)
    finished += recorder.finish_all()
    return finished


def test_the_real_tracker_hands_over_a_usable_trail() -> None:
    finished = _run(TrafficEngine(), _car_detections(100, x0=0.1, y=0.6))
    (trail,) = finished
    path = decode_path(trail.path)
    assert path.x[-1] - path.x[0] == pytest.approx(0.594, abs=0.06)  # the whole way across
    assert abs(path.y[-1] - path.y[0]) < 0.01  # on its own lane


def test_nothing_is_recorded_from_an_exclusion_zone_but_the_same_drive_outside_it_is() -> None:
    parking = Polygon(((0.0, 0.5), (1.0, 0.5), (1.0, 0.8), (0.0, 0.8)))
    ignoring = TrafficEngine(detection_filter=DetectionFilter(exclude=(parking,)))
    assert _run(ignoring, _car_detections(100, x0=0.1, y=0.6)) == []  # inside the ignored area
    assert len(_run(ignoring, _car_detections(100, x0=0.1, y=0.3))) == 1  # the same drive, above it


def test_with_a_detection_zone_only_what_is_inside_it_is_recorded() -> None:
    road = Polygon(((0.0, 0.5), (1.0, 0.5), (1.0, 0.8), (0.0, 0.8)))
    looking = TrafficEngine(detection_filter=DetectionFilter(include=(road,)))
    assert len(_run(looking, _car_detections(100, x0=0.1, y=0.6))) == 1
    assert _run(looking, _car_detections(100, x0=0.1, y=0.3)) == []
