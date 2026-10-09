"""What the status page says about the accelerator (plan.md M8 step 4)."""

import subprocess

import pytest

from tests.fakes import FakeDetector, FakeSource, ScriptedDetector, ScriptedSource, car_crossing
from trafficpi.db import create_db_engine, upgrade_to_head
from trafficpi.db.store import Store
from trafficpi.vision import detector as detector_module
from trafficpi.vision.detector import hailo_identity
from trafficpi.vision.service import LiveState, ServiceConfig, VisionService
from trafficpi.vision.shutdown import StopRequest

IDENTIFY = """Executing on device: 0001:01:00.0
Identifying board
Control Protocol Version: 2
Firmware Version: 4.23.0 (release,app,extended context switch buffer)
Logger Version: 0
Board Name: Hailo-8
Device Architecture: HAILO8L
"""


@pytest.fixture(autouse=True)
def fresh_identity() -> None:
    hailo_identity.cache_clear()
    detector_module.hailo_architecture.cache_clear()


def runs_giving(output: str, monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    calls: list[list[str]] = []

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, stdout=output, stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    return calls


def test_the_chip_its_firmware_and_its_board_are_read_from_hailortcli(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runs_giving(IDENTIFY, monkeypatch)
    assert hailo_identity() == {"architecture": "HAILO8L", "firmware": "4.23.0", "board": "Hailo-8"}


def test_the_identity_is_asked_for_once(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = runs_giving(IDENTIFY, monkeypatch)
    hailo_identity()
    hailo_identity()
    assert detector_module.hailo_architecture() == "HAILO8L"  # which is read from the same answer
    assert len(calls) == 1


def test_an_accelerator_that_does_not_answer_gives_an_empty_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def broken(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        raise FileNotFoundError("hailortcli")

    monkeypatch.setattr(subprocess, "run", broken)
    assert hailo_identity() == {}
    assert detector_module.hailo_architecture() is None


def test_a_line_that_is_missing_is_missing_from_the_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runs_giving("Device Architecture: HAILO8\n", monkeypatch)
    assert hailo_identity() == {"architecture": "HAILO8"}


# --- the live state -------------------------------------------------------------------------------


def test_the_accelerator_is_empty_until_it_is_told() -> None:
    assert LiveState().snapshot()["accelerator"] == {}


def test_what_is_known_about_the_accelerator_is_published_with_the_live_state() -> None:
    live = LiveState()
    live.set_accelerator({"architecture": "HAILO8L", "firmware": "4.23.0", "model": "yolov8s_h8l"})
    live.update_accelerator(inference_ms=17.43, inference_p95_ms=17.91)
    assert live.snapshot()["accelerator"] == {
        "architecture": "HAILO8L",
        "firmware": "4.23.0",
        "model": "yolov8s_h8l",
        "inference_ms": 17.4,
        "inference_p95_ms": 17.9,
    }


@pytest.fixture
def store() -> Store:
    engine = create_db_engine(":memory:")
    upgrade_to_head(engine)
    s = Store(engine)
    s.ensure_camera()
    return s


def test_the_service_publishes_how_long_inference_takes(store: Store) -> None:
    service = VisionService(
        FakeSource(limit=120),
        FakeDetector(),  # says 12.0 ms for every frame
        store,
        config=ServiceConfig(metrics_every_s=0.0),
    )
    service.run(StopRequest())
    accelerator = service.live.snapshot()["accelerator"]
    assert accelerator["inference_ms"] == 12.0 and accelerator["inference_p95_ms"] == 12.0


def test_a_detector_that_does_not_time_itself_publishes_no_figure(store: Store) -> None:
    """The made-up street and the replay have no accelerator: no 0.0 ms to be mistaken for speed."""
    source = ScriptedSource(car_crossing(40))
    service = VisionService(
        source, ScriptedDetector(source), store, config=ServiceConfig(metrics_every_s=0.0)
    )
    service.run(StopRequest())
    assert "inference_ms" not in service.live.snapshot()["accelerator"]
