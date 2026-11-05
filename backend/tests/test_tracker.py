import numpy as np
import pytest

from trafficpi.core.coco import class_name
from trafficpi.core.types import Detection
from trafficpi.vision.tracker import (
    ByteTracker,
    KalmanBoxFilter,
    TrackerConfig,
    TrackerResult,
    iou_matrix,
    suppress_duplicates,
    to_box,
    to_xyah,
    tracker_config_for,
)

FRAME_NS = 33_333_333  # 30 fps


def det(
    cx: float, cy: float, *, w: float = 0.08, h: float = 0.07, score: float = 0.9, cls: int = 2
) -> Detection:
    return Detection(cls, class_name(cls), score, cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2)


def feed(
    tracker: ByteTracker, frames: list[list[Detection]], *, start: int = 0
) -> list[TrackerResult]:
    return [tracker.update((start + i) * FRAME_NS, dets) for i, dets in enumerate(frames)]


def drive(
    n: int, *, x0: float = 0.1, y: float = 0.5, speed: float = 0.01, score: float = 0.9
) -> list[list[Detection]]:
    """A car moving right at ``speed`` per frame."""
    return [[det(x0 + speed * i, y, score=score)] for i in range(n)]


def test_one_object_keeps_one_id_and_is_confirmed_after_three_hits() -> None:
    results = feed(ByteTracker(), drive(60))
    ids = {t.id for r in results for t in r.tracks}
    assert ids == {1}
    assert [r.tracks[0].confirmed for r in results[:4]] == [False, False, True, True]
    last = results[-1].tracks[0]
    assert last.center[0] == pytest.approx(0.1 + 0.01 * 59, abs=0.01)
    assert last.label == "car" and last.hits == 60
    assert last.age_s == pytest.approx(59 * FRAME_NS / 1e9)


def test_velocity_is_reported_in_fractions_of_the_frame_per_second() -> None:
    results = feed(ByteTracker(), drive(60, speed=0.01))  # 0.01 per frame = 0.3 per second
    vx, vy = results[-1].tracks[0].velocity
    assert vx == pytest.approx(0.3, rel=0.1)
    assert vy == pytest.approx(0.0, abs=0.02)


def test_objects_in_different_lanes_do_not_swap_ids() -> None:
    frames = [
        [det(0.1 + 0.01 * i, 0.3), det(0.9 - 0.01 * i, 0.55)]  # they pass each other at i = 40
        for i in range(80)
    ]
    results = feed(ByteTracker(), frames)
    lane_of = {}
    for r in results:
        for t in r.tracks:
            lane_of.setdefault(t.id, set()).add("top" if t.center[1] < 0.4 else "bottom")
    assert len(lane_of) == 2 and all(len(v) == 1 for v in lane_of.values())


def test_a_short_occlusion_keeps_the_same_id() -> None:
    frames = drive(60)
    for i in range(20, 30):  # 10 frames (a third of a second) with no detection
        frames[i] = []
    results = feed(ByteTracker(), frames)
    assert {t.id for r in results for t in r.tracks} == {1}
    assert all(not r.ended for r in results)


def test_a_long_disappearance_ends_the_track_and_a_return_is_a_new_object() -> None:
    frames = drive(120)
    for i in range(20, 70):  # 50 frames = 1.7 s, longer than max_lost_s
        frames[i] = []
    results = feed(ByteTracker(), frames)
    ended = [i for r in results for i in r.ended]
    assert ended == [1]
    assert {t.id for r in results[70:] for t in r.tracks} == {2}


def test_weak_detections_extend_a_track() -> None:
    frames = [[det(0.1 + 0.01 * i, 0.5, score=0.8 if i % 2 == 0 else 0.3)] for i in range(60)]
    results = feed(ByteTracker(), frames)
    assert {t.id for r in results for t in r.tracks} == {1}
    assert all(t.confirmed for r in results[4:] for t in r.tracks)


def test_weak_detections_alone_never_start_a_track() -> None:
    results = feed(ByteTracker(), drive(30, score=0.3))
    assert all(not r.tracks for r in results)


def test_a_one_frame_blip_never_becomes_a_confirmed_object() -> None:
    frames = [[] for _ in range(10)]
    frames[4] = [det(0.5, 0.5)]
    results = feed(ByteTracker(), frames)
    assert all(not t.confirmed for r in results for t in r.tracks)
    assert [i for r in results for i in r.ended] == [1]  # dropped as soon as it was missed


def test_the_class_is_decided_by_a_score_weighted_vote() -> None:
    frames = [
        [
            det(
                0.1 + 0.01 * i,
                0.5,
                cls=7 if i in (3, 4, 5) else 2,
                score=0.6 if i in (3, 4, 5) else 0.7,
            )
        ]
        for i in range(12)
    ]
    results = feed(ByteTracker(), frames)
    assert results[-1].tracks[0].label == "car"


def test_one_vehicle_detected_as_two_classes_is_one_track() -> None:
    frames = [
        [det(0.1 + 0.01 * i, 0.5, cls=2, score=0.8), det(0.1 + 0.01 * i, 0.5, cls=7, score=0.6)]
        for i in range(30)
    ]
    results = feed(ByteTracker(), frames)
    assert {t.id for r in results for t in r.tracks} == {1}
    assert all(len(r.tracks) == 1 for r in results)


def test_irregular_time_steps_are_followed() -> None:
    """The camera skips frames when the pipeline is slow; a longer step is just a longer step."""
    tracker = ByteTracker()
    speed_per_s, t_ns, ids = 0.3, 0, set()
    gaps = [1, 1, 1, 2, 1, 3, 1, 1, 2, 1, 1, 1, 4, 1, 1, 2, 1, 1, 1, 1, 2, 1, 1, 3, 1, 1]
    last = None
    for gap in gaps:
        t_ns += gap * FRAME_NS
        x = 0.1 + speed_per_s * t_ns / 1e9
        result = tracker.update(t_ns, [det(x, 0.5)])
        ids |= {t.id for t in result.tracks}
        last = result.tracks[0] if result.tracks else last
    assert ids == {1}
    assert last is not None and last.velocity[0] == pytest.approx(0.3, rel=0.2)


def test_a_gap_in_time_longer_than_the_limit_drops_all_tracks() -> None:
    tracker = ByteTracker(TrackerConfig(max_lost_s=1.0))
    feed(tracker, drive(10))
    result = tracker.update(10 * FRAME_NS + 3_000_000_000, [det(0.9, 0.5)])  # 3 s later
    assert result.ended == [1]
    assert [t.id for t in result.tracks] == [2]


def test_iou() -> None:
    a = [(0.0, 0.0, 0.2, 0.2)]
    b = [(0.0, 0.0, 0.2, 0.2), (0.1, 0.0, 0.3, 0.2), (0.5, 0.5, 0.6, 0.6)]
    assert iou_matrix(a, b)[0] == pytest.approx([1.0, 1 / 3, 0.0])
    assert iou_matrix([], b).shape == (0, 3)


def test_duplicate_suppression_keeps_the_best_score() -> None:
    dets = [
        det(0.5, 0.5, cls=7, score=0.6),
        det(0.5, 0.5, cls=2, score=0.8),
        det(0.9, 0.9, score=0.5),
    ]
    kept = suppress_duplicates(dets, 0.7)
    assert [(d.label, d.score) for d in kept] == [("car", 0.8), ("car", 0.5)]


def test_box_conversion_round_trip() -> None:
    box = (0.2, 0.3, 0.4, 0.6)
    assert to_box(to_xyah(box)) == pytest.approx(box)


def test_kalman_predicts_motion_and_update_reduces_uncertainty() -> None:
    kf = KalmanBoxFilter()
    mean, cov = kf.initiate(to_xyah((0.1, 0.1, 0.2, 0.2)))
    for i in range(1, 11):  # move 0.01 per step
        mean, cov = kf.predict(mean, cov, 1.0)
        mean, cov = kf.update(mean, cov, to_xyah((0.1 + 0.01 * i, 0.1, 0.2 + 0.01 * i, 0.2)))
    assert mean[4] == pytest.approx(0.01, rel=0.2)  # velocity learned
    predicted, _ = kf.predict(mean, cov, 1.0)
    assert predicted[0] == pytest.approx(0.25 + 0.01, abs=0.003)
    position = [0, 1, 3]  # the aspect ratio stays near its process-noise floor by design
    initial_cov = kf.initiate(to_xyah((0.1, 0.1, 0.2, 0.2)))[1]
    assert np.trace(cov[np.ix_(position, position)]) < np.trace(
        initial_cov[np.ix_(position, position)]
    )


@pytest.mark.parametrize(
    ("model", "start"),
    [
        ("yolov6n", 0.4),
        ("yolov6n_h8l", 0.4),
        ("yolov6n_h8", 0.4),
        ("yolov11s", 0.45),
        ("yolov11s_h8l", 0.45),
        ("yolov11m_h10", 0.5),
        ("yolov8s_h8l", 0.5),
        ("yolox_s_leaky_h8l_rpi", 0.5),
        ("synthetic", 0.5),
        (None, 0.5),
    ],
)
def test_a_model_that_scores_low_starts_tracks_at_a_lower_score(
    model: str | None, start: float
) -> None:
    assert tracker_config_for(model).high_score == start
