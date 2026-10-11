"""The camera-drift check: the estimate, and the monitor around it."""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path

import cv2
import numpy as np
import pytest

from trafficpi.core.profile import Profile, empty_profile
from trafficpi.vision import drift
from trafficpi.vision.drift import (
    DriftConfig,
    DriftEstimate,
    DriftMonitor,
    estimate_drift,
    shape_points,
)

W, H = 640, 360


def make_scene(seed: int = 0, size: tuple[int, int] = (W, H)) -> np.ndarray:
    """A made-up street, as a grayscale picture: smooth texture at three scales plus the hard
    edges of buildings, kerbs and poles. Plenty of corners for a feature detector, as a real one
    has, and the same picture every time for a seed."""
    rng = np.random.default_rng(seed)
    w, h = size
    layers = np.zeros((h, w), np.float32)
    for sigma, weight in ((28, 45.0), (9, 30.0), (3, 18.0)):
        noise = cv2.GaussianBlur(rng.normal(0, 1, (h, w)).astype(np.float32), (0, 0), sigma)
        layers += weight * noise / float(noise.std())
    picture = np.clip(128 + layers, 0, 255).astype(np.uint8)
    for _ in range(70):
        x0, y0 = int(rng.integers(0, w - 20)), int(rng.integers(0, h - 20))
        x1, y1 = x0 + int(rng.integers(8, 90)), y0 + int(rng.integers(8, 70))
        shade = int(rng.integers(20, 235))
        if rng.random() < 0.5:
            cv2.rectangle(picture, (x0, y0), (x1, y1), shade, -1)
        else:
            cv2.rectangle(picture, (x0, y0), (x1, y1), shade, 2)
    for _ in range(25):
        x, y = int(rng.integers(0, w)), int(rng.integers(0, h))
        cv2.line(
            picture,
            (x, y),
            (x + int(rng.integers(-60, 60)), y + int(rng.integers(-60, 60))),
            255,
            2,
        )
    return picture


def moved(picture: np.ndarray, dx: float = 0.0, dy: float = 0.0) -> np.ndarray:
    matrix = np.array([[1, 0, dx], [0, 1, dy]], dtype=np.float64)
    return cv2.warpAffine(picture, matrix, (W, H), borderMode=cv2.BORDER_REFLECT)


def turned(picture: np.ndarray, degrees: float = 0.0, scale: float = 1.0) -> np.ndarray:
    """Counterclockwise on the screen for a positive angle (OpenCV's way), about the middle."""
    matrix = cv2.getRotationMatrix2D((W / 2, H / 2), degrees, scale)
    return cv2.warpAffine(picture, matrix, (W, H), borderMode=cv2.BORDER_REFLECT)


SCENE = make_scene()

# --- the estimate ---------------------------------------------------------------------------------


def test_a_picture_compared_with_itself_has_not_moved() -> None:
    result = estimate_drift(SCENE, SCENE)
    assert result.trusted
    assert result.shift_pct < 0.05
    assert abs(result.rotation_deg) < 0.05
    assert result.scale == pytest.approx(1.0, abs=0.001)


@pytest.mark.parametrize(("dx", "dy"), [(3, 0), (0, -6), (8, 5), (-12, 2)])
def test_a_shift_is_found_to_about_a_pixel(dx: float, dy: float) -> None:
    result = estimate_drift(SCENE, moved(SCENE, dx, dy))
    assert result.trusted
    assert result.dx_pct == pytest.approx(dx / W * 100, abs=0.2)
    assert result.dy_pct == pytest.approx(dy / W * 100, abs=0.2)
    assert result.shift_pct == pytest.approx(np.hypot(dx, dy) / W * 100, abs=0.25)


def test_a_turn_is_found_with_its_direction() -> None:
    result = estimate_drift(SCENE, turned(SCENE, 1.0))  # counterclockwise
    assert result.trusted
    assert result.rotation_deg == pytest.approx(-1.0, abs=0.15)  # negative: not clockwise
    clockwise = estimate_drift(SCENE, turned(SCENE, -1.5))
    assert clockwise.rotation_deg == pytest.approx(1.5, abs=0.15)


def test_a_change_of_size_is_found() -> None:
    result = estimate_drift(SCENE, turned(SCENE, 0.0, 1.03))
    assert result.trusted
    assert result.scale == pytest.approx(1.03, abs=0.006)
    assert estimate_drift(SCENE, turned(SCENE, 0.0, 0.97)).scale == pytest.approx(0.97, abs=0.006)


def test_what_matters_is_how_far_the_drawn_shapes_are_off_not_the_pictures_corners() -> None:
    """A turn about the middle moves the corners far and the middle not at all."""
    frame = turned(SCENE, 1.5)
    near_the_middle = estimate_drift(SCENE, frame, [(0.5, 0.5), (0.52, 0.48)])
    at_the_corners = estimate_drift(SCENE, frame, [(0.0, 0.0), (1.0, 1.0)])
    assert near_the_middle.trusted and at_the_corners.trusted
    assert near_the_middle.shift_pct < 0.3
    assert at_the_corners.shift_pct > 1.5 * 5 / 6  # a 1.5 degree turn moves a corner by about 1.7%


def test_a_bigger_reference_picture_is_compared_at_the_live_pictures_size() -> None:
    reference = cv2.resize(SCENE, (1280, 720), interpolation=cv2.INTER_CUBIC)
    result = estimate_drift(reference, moved(SCENE, 6, 0))
    assert result.trusted
    assert result.dx_pct == pytest.approx(6 / W * 100, abs=0.25)


def test_vehicles_that_were_not_in_the_reference_picture_do_not_fool_it() -> None:
    frame = moved(SCENE, 0, 0).copy()
    for x, y in [(60, 250), (200, 270), (330, 240), (450, 280), (540, 255), (120, 150)]:
        cv2.rectangle(frame, (x, y), (x + 46, y + 22), 235, -1)
        cv2.rectangle(frame, (x + 8, y + 4), (x + 36, y + 14), 40, -1)
    result = estimate_drift(SCENE, frame)
    assert result.trusted
    assert result.shift_pct < 0.3


@pytest.mark.parametrize("gain", [0.6, 0.35])
def test_a_darker_picture_with_noise_still_matches(gain: float) -> None:
    rng = np.random.default_rng(3)
    dark = np.clip(SCENE * gain + rng.normal(0, 4, SCENE.shape), 0, 255).astype(np.uint8)
    result = estimate_drift(SCENE, moved(dark, 5, 0))
    assert result.trusted
    assert result.dx_pct == pytest.approx(5 / W * 100, abs=0.3)


def test_a_picture_washed_out_by_light_can_still_be_matched() -> None:
    washed = np.clip(SCENE.astype(np.float32) * 1.7 + 25, 0, 255).astype(np.uint8)
    result = estimate_drift(SCENE, moved(washed, -4, 3))
    assert result.trusted
    assert result.dx_pct == pytest.approx(-4 / W * 100, abs=0.3)


@pytest.mark.parametrize("gain", [0.3, 0.12, 0.06, 0.03, 0.015])
@pytest.mark.parametrize("noise", [3.0, 8.0])
def test_in_the_dark_it_either_cannot_tell_or_is_right_and_never_cries_wolf(
    gain: float, noise: float
) -> None:
    """The one thing a warning must not do is invent a move: a dark, noisy picture may be too hard
    to match, but if it is matched the answer has to be the truth."""
    rng = np.random.default_rng(int(gain * 1000 + noise))
    for dx in (0, 5):
        dark = np.clip(moved(SCENE, dx, 0) * gain + rng.normal(0, noise, SCENE.shape), 0, 255)
        result = estimate_drift(SCENE, dark.astype(np.uint8))
        if result.trusted:
            assert result.dx_pct == pytest.approx(dx / W * 100, abs=0.4)
            assert result.shift_pct == pytest.approx(dx / W * 100, abs=0.5)


@pytest.mark.parametrize(
    "frame",
    [
        pytest.param(np.full((H, W), 90, np.uint8), id="a plain grey picture"),
        pytest.param(
            np.random.default_rng(1).integers(0, 255, (H, W)).astype(np.uint8), id="unrelated noise"
        ),
        pytest.param(make_scene(seed=99), id="another place altogether"),
        pytest.param(
            np.clip(np.random.default_rng(2).normal(6, 4, SCENE.shape), 0, 255).astype(np.uint8),
            id="only the noise of a sensor in the dark",
        ),
    ],
)
def test_when_the_pictures_cannot_be_matched_it_says_it_cannot_tell(frame: np.ndarray) -> None:
    result = estimate_drift(SCENE, frame)
    assert not result.trusted
    assert result.reason
    assert result.shift_pct == 0.0  # no numbers are made up


def test_pictures_of_different_shapes_are_not_compared() -> None:
    result = estimate_drift(SCENE, make_scene(size=(640, 480)))
    assert not result.trusted
    assert "shape" in result.reason


def test_colour_pictures_are_refused() -> None:
    with pytest.raises(ValueError, match="grayscale"):
        estimate_drift(np.zeros((H, W, 3), np.uint8), SCENE)


# --- the shapes -----------------------------------------------------------------------------------


def test_the_corners_of_every_line_zone_and_stop_sign_are_what_is_checked() -> None:
    profile = Profile.model_validate(
        {
            "schema_version": 2,
            "lines": [{"id": "l1", "name": "A", "a": [0.1, 0.2], "b": [0.3, 0.4]}],
            "detection_zones": [
                {"id": "d1", "name": "D", "points": [[0.0, 0.0], [0.5, 0.0], [0.5, 0.5]]}
            ],
            "exclusion_zones": [
                {"id": "x1", "name": "X", "points": [[0.6, 0.6], [0.9, 0.6], [0.9, 0.9]]}
            ],
            "stop_signs": [
                {
                    "id": "s1",
                    "name": "S",
                    "approach": [[0.2, 0.5], [0.4, 0.5], [0.3, 0.7]],
                    "a": [0.2, 0.7],
                    "b": [0.4, 0.7],
                }
            ],
        }
    )
    points = shape_points(profile)
    assert len(points) == 2 + 3 + 3 + 3 + 2
    assert (0.1, 0.2) in points and (0.9, 0.9) in points and (0.3, 0.7) in points


def test_the_corners_of_a_calibration_and_the_ends_of_its_checks_are_points_too() -> None:
    profile = Profile.model_validate(
        {
            "calibration": {
                "corners": [[0.62, 0.42], [0.86, 0.42], [0.58, 0.31], [0.43, 0.31]],
                "width_m": 7.0,
                "length_m": 30.0,
                "checks": [{"id": "c", "a": [0.5, 0.4], "b": [0.6, 0.35], "length_m": 12.0}],
            }
        }
    )
    points = shape_points(profile)
    assert len(points) == 4 + 2
    assert (0.62, 0.42) in points and (0.43, 0.31) in points and (0.6, 0.35) in points


def test_a_profile_without_shapes_has_no_points() -> None:
    assert shape_points(empty_profile()) == []


# --- the monitor ----------------------------------------------------------------------------------


class Clock:
    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now


def sure(
    shift: float,
    *,
    dx_pct: float = 0.0,
    dy_pct: float = 0.0,
    rotation_deg: float = 0.0,
    scale: float = 1.0,
) -> DriftEstimate:
    return DriftEstimate(
        trusted=True,
        shift_pct=shift,
        dx_pct=dx_pct,
        dy_pct=dy_pct,
        rotation_deg=rotation_deg,
        scale=scale,
        inliers=80,
        matches=100,
    )


UNSURE = DriftEstimate(trusted=False, reason="too few features to compare")


@pytest.fixture
def reference(tmp_path: Path) -> Path:
    path = tmp_path / "reference.jpg"
    cv2.imwrite(str(path), SCENE)
    return path


def monitor_that_finds(
    results: list[DriftEstimate], reference: Path, **kw: object
) -> tuple[DriftMonitor, list[dict]]:
    """A monitor (no thread) whose estimates are the given ones, in order, and what it published."""
    answers = iter(results)
    published: list[dict] = []
    m = DriftMonitor(
        reference,
        estimator=lambda *a: next(answers),
        publish=published.append,
        threaded=False,
        wall=lambda: 1_700_000_000.0,
        **kw,  # type: ignore[arg-type]
    )
    return m, published


FRAME = np.zeros((H, W, 3), np.uint8)


def look(m: DriftMonitor, times: int = 1) -> None:
    for _ in range(times):
        m.check(FRAME)


def test_before_the_first_look_nothing_is_known(reference: Path) -> None:
    m, _ = monitor_that_finds([], reference)
    status = m.status()
    assert status["state"] == "unknown"
    assert status["shift_pct"] is None and status["checked_at"] is None


def test_one_sure_look_within_the_limit_is_enough_to_say_ok(reference: Path) -> None:
    m, published = monitor_that_finds([sure(0.2)], reference)
    look(m)
    status = m.status()
    assert status["state"] == "ok"
    assert status["shift_pct"] == 0.2
    assert status["sure_at"] == status["checked_at"] == 1_700_000_000_000
    assert published and published[-1] == status


def test_a_warning_needs_three_sure_looks_in_a_row_above_the_limit(reference: Path) -> None:
    m, _ = monitor_that_finds([sure(1.4), sure(1.5), sure(1.6)], reference)
    look(m, 2)
    assert m.status()["state"] == "unknown"  # nothing says ok yet, and two looks are not enough
    look(m)
    status = m.status()
    assert status["state"] == "drifted"
    assert "off by up to 1.6%" in status["message"]


def test_one_odd_look_among_good_ones_does_not_warn(reference: Path) -> None:
    m, _ = monitor_that_finds([sure(0.1), sure(2.5), sure(0.2), sure(2.5), sure(2.6)], reference)
    look(m, 5)
    assert m.status()["state"] == "ok"


def test_a_view_that_has_moved_is_told_in_words(reference: Path) -> None:
    moved_view = sure(2.4, dx_pct=-2.2, dy_pct=0.9, rotation_deg=0.8, scale=1.02)
    m, _ = monitor_that_finds([moved_view] * 3, reference)
    look(m, 3)
    message = m.status()["message"]
    assert "2.2% of the width to the left" in message
    assert "0.9% down" in message
    assert "0.8 degrees clockwise" in message
    assert "2% bigger" in message


def test_the_warning_is_taken_back_after_two_looks_well_within_the_limit(reference: Path) -> None:
    m, _ = monitor_that_finds([sure(2.0)] * 3 + [sure(0.9), sure(0.5), sure(0.4)], reference)
    look(m, 4)
    assert m.status()["state"] == "drifted"  # 0.9 is under the warning but not within the clearing
    look(m)
    assert m.status()["state"] == "drifted"  # one good look is not enough
    look(m)
    assert m.status()["state"] == "ok"


def test_a_look_that_cannot_tell_leaves_the_verdict_and_its_numbers(reference: Path) -> None:
    m, _ = monitor_that_finds([sure(2.0)] * 3 + [UNSURE, UNSURE], reference)
    look(m, 3)
    sure_at = m.status()["sure_at"]
    look(m, 2)
    status = m.status()
    assert status["state"] == "drifted"
    assert status["shift_pct"] == 2.0  # the last numbers that were sure
    assert status["unsure_because"] == "too few features to compare"
    assert status["sure_at"] == sure_at


def test_after_a_sure_look_the_reason_for_doubt_is_gone(reference: Path) -> None:
    m, _ = monitor_that_finds([UNSURE, sure(0.1)], reference)
    look(m)
    assert m.status()["unsure_because"] == "too few features to compare"
    look(m)
    assert m.status()["unsure_because"] is None


def test_without_a_reference_picture_it_says_so(tmp_path: Path) -> None:
    m, published = monitor_that_finds([sure(0.1)], tmp_path / "missing.jpg")
    look(m)
    status = m.status()
    assert status["state"] == "no_reference"
    assert "No reference picture" in status["message"]
    assert published[-1]["state"] == "no_reference"


def test_a_reference_that_is_not_a_picture_is_reported(tmp_path: Path) -> None:
    broken = tmp_path / "reference.jpg"
    broken.write_bytes(b"not a jpeg")
    m, _ = monitor_that_finds([], broken)
    look(m)
    assert m.status()["state"] == "no_reference"
    assert "cannot be read" in m.status()["message"]


def test_a_new_reference_picture_wipes_the_old_verdict(reference: Path) -> None:
    m, _ = monitor_that_finds([sure(2.0)] * 3 + [sure(0.1)], reference)
    look(m, 3)
    assert m.status()["state"] == "drifted"
    cv2.imwrite(str(reference), make_scene(seed=7))  # the owner keeps a new picture
    look(m)
    status = m.status()
    assert status["state"] == "ok"  # judged afresh against the new picture: 0.1
    assert status["shift_pct"] == 0.1


def test_the_new_picture_is_not_judged_by_looks_made_against_the_old_one(reference: Path) -> None:
    m, _ = monitor_that_finds([sure(2.0)] * 3 + [sure(2.0), sure(2.0)], reference)
    look(m, 3)
    cv2.imwrite(str(reference), make_scene(seed=8))
    look(m, 2)
    assert m.status()["state"] == "unknown"  # two looks at the new picture are not enough


def test_the_shapes_are_handed_to_the_estimate(reference: Path) -> None:
    seen: list[object] = []

    def estimator(ref: np.ndarray, frame: np.ndarray, points: object) -> DriftEstimate:
        seen.append((ref.shape, frame.shape, points))
        return sure(0.0)

    m = DriftMonitor(reference, estimator=estimator, threaded=False)
    m.set_points([(0.1, 0.2), (0.3, 0.4)])
    m.check(FRAME)
    assert seen == [((H, W), (H, W), ((0.1, 0.2), (0.3, 0.4)))]


def test_the_status_can_be_sent_as_json(reference: Path) -> None:
    m, _ = monitor_that_finds([sure(1.2, dx_pct=1.0)], reference)
    look(m)
    assert json.loads(json.dumps(m.status()))["shift_pct"] == 1.2


# --- the thread -----------------------------------------------------------------------------------


def threaded_monitor(
    reference: Path, clock: Clock, calls: list[float], done: threading.Event, **kw: object
) -> DriftMonitor:
    def estimator(*_: object) -> DriftEstimate:
        calls.append(clock.now)
        done.set()
        return sure(0.1)

    return DriftMonitor(
        reference,
        DriftConfig(first_check_after_s=10, check_every_s=60),
        estimator=estimator,
        clock=clock,
        **kw,  # type: ignore[arg-type]
    )


def test_a_frame_is_looked_at_once_a_minute_not_at_every_frame(reference: Path) -> None:
    clock, calls, done = Clock(), [], threading.Event()
    m = threaded_monitor(reference, clock, calls, done)
    try:
        for _ in range(200):  # 200 frames in the first seconds: none is due
            m.offer(FRAME)
        clock.now += 10
        for _ in range(200):  # due: the first one is taken, the rest only return
            m.offer(FRAME)
        assert done.wait(5)
        for _ in range(200):
            m.offer(FRAME)
        clock.now += 59
        m.offer(FRAME)
        m.close()  # waits for what is in hand
        assert len(calls) == 1
    finally:
        m.close()


def test_the_next_look_comes_a_minute_after_the_last_was_asked_for(reference: Path) -> None:
    clock, calls, first, second = Clock(), [], threading.Event(), threading.Event()
    m = threaded_monitor(reference, clock, calls, first)
    try:
        clock.now += 10
        m.offer(FRAME)
        assert first.wait(5)
        first.clear()
        clock.now += 60
        m.offer(FRAME)
        assert first.wait(5)
        assert len(calls) == 2
        _ = second
    finally:
        m.close()


def test_the_frame_is_copied_so_the_camera_can_go_on_using_its_buffer(reference: Path) -> None:
    clock, seen, done = Clock(), [], threading.Event()
    frame = np.full((H, W, 3), 200, np.uint8)
    gate = threading.Event()

    def estimator(ref: np.ndarray, gray: np.ndarray, points: object) -> DriftEstimate:
        gate.wait(5)
        seen.append(int(gray[0, 0]))
        done.set()
        return sure(0.0)

    m = DriftMonitor(
        reference,
        DriftConfig(first_check_after_s=0),
        estimator=estimator,
        clock=clock,
    )
    try:
        m.offer(frame)
        frame[:] = 0  # the camera reuses its buffer while the check is still waiting
        gate.set()
        assert done.wait(5)
        assert seen[0] > 150
    finally:
        m.close()


def test_a_check_that_fails_is_logged_and_the_monitor_goes_on(
    reference: Path, caplog: pytest.LogCaptureFixture
) -> None:
    clock, done = Clock(), threading.Event()
    outcomes = iter(["boom", "fine"])

    def estimator(*_: object) -> DriftEstimate:
        if next(outcomes) == "boom":
            raise RuntimeError("the matcher fell over")
        done.set()
        return sure(0.1)

    m = DriftMonitor(
        reference,
        DriftConfig(first_check_after_s=0, check_every_s=1),
        estimator=estimator,
        clock=clock,
    )
    try:
        with caplog.at_level(logging.ERROR, logger="trafficpi.vision"):
            m.offer(FRAME)
            for _ in range(500):  # until the failed check has been through
                if "the camera-drift check failed" in caplog.text:
                    break
                threading.Event().wait(0.01)
            clock.now += 5
            m.offer(FRAME)
            assert done.wait(5)
        assert "the camera-drift check failed" in caplog.text
        assert m.status()["state"] == "ok"
    finally:
        m.close()


def test_closing_ends_the_thread_and_later_offers_are_ignored(reference: Path) -> None:
    clock, calls, done = Clock(), [], threading.Event()
    m = threaded_monitor(reference, clock, calls, done)
    m.close()
    clock.now += 100
    m.offer(FRAME)
    assert not calls
    assert not any(t.name == "drift" and t.is_alive() for t in threading.enumerate())


def test_the_module_keeps_its_limits_where_the_dashboard_can_be_told() -> None:
    assert drift.MIN_INLIERS >= 20  # fewer agreeing features are too easy to get by chance
    assert DriftConfig().warn_shift_pct > DriftConfig().clear_shift_pct
