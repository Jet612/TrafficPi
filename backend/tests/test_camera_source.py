import sys
from types import ModuleType, SimpleNamespace
from typing import ClassVar

import pytest

from trafficpi.vision.sources import READ_TIMEOUT_S, PiCameraSource


def test_a_camera_that_delivers_nothing_raises_instead_of_waiting_forever() -> None:
    class Silent:
        def capture_request(self, wait=None):  # type: ignore[no-untyped-def]
            assert wait == READ_TIMEOUT_S  # Picamera2 would block for ever without a timeout
            raise TimeoutError

    source = PiCameraSource()
    source._picam2 = Silent()
    with pytest.raises(RuntimeError, match="no frame from the camera"):
        source.read()


def test_reading_before_the_camera_is_started_is_an_error() -> None:
    with pytest.raises(RuntimeError, match="not started"):
        PiCameraSource().read()


class FakePicamera2:
    """Stands in for Picamera2: records what happens to it, and can fail to start."""

    instances: ClassVar[list["FakePicamera2"]] = []
    failures_left = 0
    construction_fails = False

    def __init__(self) -> None:
        if FakePicamera2.construction_fails:
            raise RuntimeError("Camera __init__ sequence did not complete.")
        self.started = self.closed = False
        FakePicamera2.instances.append(self)

    def create_video_configuration(self, **kwargs):  # type: ignore[no-untyped-def]
        return kwargs

    def configure(self, config) -> None:  # type: ignore[no-untyped-def]
        pass

    def start(self) -> None:
        if FakePicamera2.failures_left > 0:
            FakePicamera2.failures_left -= 1
            raise RuntimeError("Failed to start camera: Invalid argument")
        self.started = True

    def stop(self) -> None:
        self.started = False

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def fake_picamera2(monkeypatch: pytest.MonkeyPatch) -> type[FakePicamera2]:
    FakePicamera2.instances, FakePicamera2.failures_left = [], 0
    FakePicamera2.construction_fails = False
    picamera2 = ModuleType("picamera2")
    picamera2.Picamera2 = FakePicamera2  # type: ignore[attr-defined]
    modes = SimpleNamespace(Normal=1, Short=2, Long=3)
    af_modes = SimpleNamespace(Manual=0, Continuous=2)
    libcamera = ModuleType("libcamera")
    libcamera.controls = SimpleNamespace(  # type: ignore[attr-defined]
        AeExposureModeEnum=modes, AfModeEnum=af_modes
    )
    monkeypatch.setitem(sys.modules, "picamera2", picamera2)
    monkeypatch.setitem(sys.modules, "libcamera", libcamera)
    return FakePicamera2


def test_a_camera_that_opens_but_will_not_start_is_closed_again(
    fake_picamera2: type[FakePicamera2],
) -> None:
    """Otherwise it stays acquired by this process and no later attempt could ever open it."""
    fake_picamera2.failures_left = 1
    source = PiCameraSource()
    with pytest.raises(RuntimeError, match="Invalid argument"):
        source.start()
    (first,) = fake_picamera2.instances
    assert first.closed and not first.started
    with pytest.raises(RuntimeError, match="not started"):
        source.read()  # nothing was kept
    source.start()  # the next attempt works
    assert fake_picamera2.instances[1].started and not fake_picamera2.instances[1].closed
    source.stop()
    assert fake_picamera2.instances[1].closed


def test_a_camera_in_use_elsewhere_gets_a_helpful_message(
    fake_picamera2: type[FakePicamera2],
) -> None:
    fake_picamera2.construction_fails = True
    with pytest.raises(RuntimeError, match="is another program using it"):
        PiCameraSource().start()
