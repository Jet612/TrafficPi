import json
import logging
import threading
import time

import cv2
import httpx
import numpy as np
import pytest
import websockets.exceptions
from fastapi.testclient import TestClient
from websockets.sync.client import connect

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
    assert health["evidence_skipped_for_space"] == 0 and health["disk_free_mb"] is None
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


def test_an_open_live_socket_is_closed_when_the_server_stops_and_nothing_is_logged_as_an_error(
    service: VisionService, caplog: pytest.LogCaptureFixture
) -> None:
    """A viewer that holds the live socket open used to be cancelled by the server after its
    three seconds of grace, and the cancellation was logged as an ERROR with a traceback on every
    restart of the service. Now the socket ends itself with 1001 ("going away") as soon as the
    service stops."""
    server = ServerThread(create_app(service, live_hz=50), port=18744)
    server.start()
    try:
        with connect("ws://127.0.0.1:18744/live", open_timeout=5) as socket:
            assert json.loads(socket.recv(timeout=5))["type"] == "state"
            started = time.monotonic()
            with caplog.at_level(logging.WARNING):
                server.stop()
                with pytest.raises(websockets.exceptions.ConnectionClosed) as closed:
                    while True:
                        socket.recv(timeout=2)
            assert closed.value.rcvd is not None and closed.value.rcvd.code == 1001
            assert time.monotonic() - started < 2.0, (
                "it did not wait for the end of the grace period"
            )
    finally:
        server.stop()
    assert [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR] == []


def test_streams_and_sockets_end_when_stopping_begins_and_the_server_goes_on_answering(
    service: VisionService,
) -> None:
    """The web server is stopped last, after the camera and the evidence; the viewers must not be
    left connected through all that (the server's grace period then cancels them, and logs an
    ERROR). ``begin_stopping`` ends them at the stop request; the server answers until ``stop``."""
    server = ServerThread(create_app(service, live_hz=50), port=18745)
    server.start()
    try:
        with connect("ws://127.0.0.1:18745/live", open_timeout=5) as socket:
            assert json.loads(socket.recv(timeout=5))["type"] == "state"
            with httpx.stream("GET", "http://127.0.0.1:18745/preview.mjpg", timeout=5) as stream:
                chunks = stream.iter_raw()
                assert next(chunks).startswith(b"--frame")
                server.begin_stopping()
                with pytest.raises(websockets.exceptions.ConnectionClosed) as closed:
                    while True:
                        socket.recv(timeout=2)
                assert closed.value.rcvd is not None and closed.value.rcvd.code == 1001
                started = time.monotonic()
                list(chunks)  # the rest of the stream: it ends, within a moment, and does not go on
                assert time.monotonic() - started < 3.0
        assert httpx.get("http://127.0.0.1:18745/health", timeout=5).json()["status"] == "ok"
    finally:
        server.stop()


def test_encode_jpeg_swaps_to_the_right_channel_order() -> None:
    rgb = np.zeros((8, 8, 3), np.uint8)
    rgb[..., 0] = 255  # pure red in RGB
    decoded = cv2.imdecode(np.frombuffer(encode_jpeg(rgb, 95), np.uint8), cv2.IMREAD_COLOR)
    assert (
        decoded is not None and decoded[4, 4, 2] > 200 and decoded[4, 4, 0] < 60
    )  # BGR: red lives in the last channel
