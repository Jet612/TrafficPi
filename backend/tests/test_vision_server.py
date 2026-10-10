import threading
import time

import cv2
import httpx
import numpy as np
import pytest
from fastapi.testclient import TestClient

from tests.fakes import ScriptedDetector, ScriptedSource, car_crossing
from trafficpi.db import create_db_engine, upgrade_to_head
from trafficpi.db.store import Store
from trafficpi.vision.server import (
    PreviewEncoder,
    ServerThread,
    create_app,
    encode_jpeg,
    mjpeg_parts,
)
from trafficpi.vision.service import LiveState, VisionService
from trafficpi.vision.shutdown import StopRequest


@pytest.fixture
def service() -> VisionService:
    engine = create_db_engine(":memory:")
    upgrade_to_head(engine)
    store = Store(engine)
    store.ensure_camera()
    source = ScriptedSource(car_crossing(30))
    svc = VisionService(source, ScriptedDetector(source), store)
    svc.run(StopRequest())  # leaves a finished run's state published
    return svc


def test_health_and_state(service: VisionService) -> None:
    client = TestClient(create_app(service))
    health = client.get("/health").json()
    assert health["status"] == "ok" and health["frames"] == 30 and health["profile_version"] == 0
    assert health["trails_recorded"] == 1  # the car that drove through was saved when the run ended
    assert (health["evidence_written"], health["evidence_waiting"]) == (0, 0)
    assert health["evidence_frames_dropped"] == 0
    assert health["stops_skipped"] == 0  # no hours are set, so no pass was turned away
    assert health["drift"] is None  # the service has no data directory, so it keeps no check
    state = client.get("/state").json()
    assert state["frame"] == 29 and state["camera"]["ok"] and len(state["tracks"]) == 1
    assert state["drift"] == {}


def test_health_and_state_say_what_the_drift_check_has_found(service: VisionService) -> None:
    service.live.set_drift(
        {"state": "drifted", "message": "The view has changed.", "shift_pct": 2.4}
    )
    client = TestClient(create_app(service))
    assert client.get("/health").json()["drift"] == "drifted"
    assert client.get("/state").json()["drift"]["shift_pct"] == 2.4


def test_health_reports_a_camera_that_is_offline(service: VisionService) -> None:
    service.live.set_camera_error("camera lost")
    health = TestClient(create_app(service)).get("/health").json()
    assert health["status"] == "camera-offline" and health["camera"]["error"] == "camera lost"


def test_reload_asks_the_service_to_reload(service: VisionService) -> None:
    assert not service._reload.is_set()
    assert TestClient(create_app(service)).post("/reload").json() == {"requested": True}
    assert service._reload.is_set()


def test_snapshot_is_a_jpeg_and_failures_are_a_503(service: VisionService) -> None:
    def good() -> np.ndarray:
        return np.full((72, 128, 3), (200, 40, 40), np.uint8)

    client = TestClient(create_app(service, snapshot=good))
    response = client.get("/snapshot.jpg")
    assert response.headers["content-type"] == "image/jpeg"
    image = cv2.imdecode(np.frombuffer(response.content, np.uint8), cv2.IMREAD_COLOR)
    assert (
        image is not None and image.shape == (72, 128, 3) and image[0, 0, 2] > 150
    )  # BGR: the red channel is high

    def broken() -> np.ndarray:
        raise RuntimeError("camera busy")

    failing = TestClient(create_app(service, snapshot=broken)).get("/snapshot.jpg")
    assert failing.status_code == 503 and "camera busy" in failing.json()["error"]


def test_snapshot_falls_back_to_the_latest_frame(service: VisionService) -> None:
    assert TestClient(create_app(service)).get("/snapshot.jpg").status_code == 200
    empty = VisionService(ScriptedSource([]), ScriptedDetector(ScriptedSource([])), service.store)
    assert TestClient(create_app(empty)).get("/snapshot.jpg").status_code == 503


def test_the_live_socket_pushes_state(service: VisionService) -> None:
    client = TestClient(create_app(service, live_hz=50))
    with client.websocket_connect("/live") as socket:
        message = socket.receive_json()
    assert message["type"] == "state" and message["frame"] == 29 and message["tracks"]


def test_mjpeg_parts_are_jpeg_frames_in_a_multipart_stream() -> None:
    live = LiveState()
    source = ScriptedSource(car_crossing(3))
    live.publish_frame(source.read(), [], 1, 30.0)
    parts = mjpeg_parts(live, PreviewEncoder(live), fps=100, keepalive_s=0.05)
    first = next(parts)
    head, _, body = first.partition(b"\r\n\r\n")
    assert head.startswith(b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: ")
    assert body.startswith(b"\xff\xd8") and body.rstrip(b"\r\n").endswith(b"\xff\xd9")
    # With no new frame the last one is repeated, so a closed connection gets noticed.
    assert next(parts) == first


def test_the_preview_encoder_encodes_each_frame_once() -> None:
    live = LiveState()
    encoder = PreviewEncoder(live)
    image = np.zeros((36, 64, 3), np.uint8)
    a = encoder.jpeg_for(1, image)
    assert encoder.jpeg_for(1, image) is a  # cached
    assert encoder.jpeg_for(2, image) is not a


def test_the_mjpeg_generator_stops_when_asked() -> None:
    live = LiveState()
    stopped = threading.Event()
    parts = mjpeg_parts(live, PreviewEncoder(live), keepalive_s=0.02, should_stop=stopped.is_set)
    stopped.set()
    assert list(parts) == []


def test_a_real_server_serves_requests_in_a_thread(service: VisionService) -> None:
    server = ServerThread(create_app(service), port=18741)
    server.start()
    try:
        assert httpx.get("http://127.0.0.1:18741/health", timeout=5).json()["status"] == "ok"
        with httpx.stream("GET", "http://127.0.0.1:18741/preview.mjpg", timeout=5) as response:
            assert response.headers["content-type"].startswith("multipart/x-mixed-replace")
            chunk = next(response.iter_raw())
            assert chunk.startswith(b"--frame")
    finally:
        server.stop()
    time.sleep(0.1)
    with pytest.raises(httpx.ConnectError):
        httpx.get("http://127.0.0.1:18741/health", timeout=1)


def test_encode_jpeg_swaps_to_the_right_channel_order() -> None:
    rgb = np.zeros((8, 8, 3), np.uint8)
    rgb[..., 0] = 255  # pure red in RGB
    decoded = cv2.imdecode(np.frombuffer(encode_jpeg(rgb, 95), np.uint8), cv2.IMREAD_COLOR)
    assert (
        decoded is not None and decoded[4, 4, 2] > 200 and decoded[4, 4, 0] < 60
    )  # BGR: red lives in the last channel
