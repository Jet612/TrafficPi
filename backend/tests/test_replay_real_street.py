"""Real detections through the real rules: 103 seconds of a daylight afternoon at the camera's own
three-way stop (plan.md §7, M2 and M5), replayed with the profile saved on the camera.

``tests/data/street_daylight_103s.jsonl.gz`` is the start of a 12-minute recording made with
``trafficpi-vision record`` at 17:22 on 2026-10-10 (yolov8s on the Hailo-8L, 30 frames a second),
with the detections scored under 0.25 left out, as the live service leaves them out. It holds boxes
and no picture. ``street_profile.json`` is the profile that was saved on the camera that day (two
counting lines, a stop sign and the owner's calibration).

What each vehicle did was checked by eye against the frames of the clip (docs/accuracy.md): six
vehicles crossed a counting line in these 103 seconds, and three of them went through the stop sign
without stopping. A change to the tracker or a rule that moves these is a change of behaviour on
a real street, and the test says so; update the numbers only after looking at the clip again.
"""

import gzip
import shutil
from pathlib import Path

import pytest

from trafficpi.vision.profile import load_compiled_profile
from trafficpi.vision.replay import ReplayResult, replay_log

DATA = Path(__file__).parent / "data"
LEFT, RIGHT = "line-76125b", "line-772ebe"  # "Left Counter" and "Right Counter"


@pytest.fixture(scope="module")
def replayed(tmp_path_factory: pytest.TempPathFactory) -> ReplayResult:
    log = tmp_path_factory.mktemp("street") / "detections.jsonl"
    with gzip.open(DATA / "street_daylight_103s.jsonl.gz", "rb") as source, log.open("wb") as out:
        shutil.copyfileobj(source, out)
    profile = load_compiled_profile(DATA / "street_profile.json")
    return replay_log(
        log,
        profile.counting_lines,
        detection_filter=profile.detection_filter,
        stop_signs=profile.stop_signs,
        calibration=profile.calibration,
    )


def seconds(replayed: ReplayResult, timestamp_ns: int) -> float:
    return (timestamp_ns - replayed.timestamps_ns[0]) / 1e9


def test_the_clip_is_the_one_that_was_looked_at(replayed: ReplayResult) -> None:
    assert replayed.header["model"] == "yolov8s_h8l" and replayed.header["frame_size"] == [640, 360]
    assert len(replayed.results) == 3076
    assert replayed.duration_s == pytest.approx(103.0, abs=0.2)


def test_each_vehicle_that_crossed_a_counting_line_was_counted_once_and_nothing_else(
    replayed: ReplayResult,
) -> None:
    """Looked at frame by frame: a white van, a grey saloon and a black SUV driving right past the
    Left Counter (near lane), and three cars driving left past the Right Counter (far lane)."""
    found = [
        (round(seconds(replayed, e.timestamp_ns), 1), e.line_id, e.direction, e.label)
        for e in replayed.events
    ]
    assert found == [
        (3.4, LEFT, 1, "truck"),  # the van (a "truck": the model's word for it)
        (15.0, RIGHT, 1, "car"),
        (16.1, LEFT, 1, "car"),
        (69.2, RIGHT, 1, "car"),
        (80.3, RIGHT, 1, "car"),
        (99.0, LEFT, 1, "car"),
    ]
    assert len({e.track_id for e in replayed.events}) == 6, "no vehicle counted twice"


def test_each_pass_at_the_stop_sign_is_judged_as_it_looked(replayed: ReplayResult) -> None:
    """The van, the saloon and the SUV that went right past the Left Counter also went through the
    stop sign without standing still (they were moving the whole way across the frames): the rule
    says rolled through, and the numbers say how slowly and how fast."""
    stops = [
        (round(seconds(replayed, e.timestamp_ns), 1), e.outcome, e.label) for e in replayed.stops
    ]
    assert stops == [(3.6, "rolling", "truck"), (16.1, "rolling", "car"), (99.1, "rolling", "car")]
    for stop in replayed.stops:
        assert stop.hold_s < 0.5, "none of them stood still"
        # a speed measured on this road: a few metres a second, a vehicle at a crawl to a trot
        assert 1.0 < stop.min_speed_ms <= stop.max_speed_ms < 9.0


def test_the_speeds_of_the_counted_vehicles_are_those_of_slow_traffic_at_a_stop_sign(
    replayed: ReplayResult,
) -> None:
    speeds = [e.speed_ms for e in replayed.events]
    assert all(speed is not None and 1.0 < speed < 9.0 for speed in speeds), speeds
