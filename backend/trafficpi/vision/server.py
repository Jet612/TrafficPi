"""The vision service's local web interface.

It listens on localhost only. The API service is the public door: it adds the login and relays
these endpoints to browsers. Everything here is read from :class:`LiveState`, so serving viewers
never slows the camera loop.
"""

from __future__ import annotations

import asyncio
import contextlib
import threading
import time
from collections.abc import Callable, Iterator

import cv2
import numpy as np
import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, Response, StreamingResponse

from trafficpi.vision.service import LiveState, VisionService

BOUNDARY = "frame"
SnapshotProvider = Callable[[], np.ndarray]


def encode_jpeg(image_rgb: np.ndarray, quality: int = 75) -> bytes:
    ok, buffer = cv2.imencode(
        ".jpg", cv2.cvtColor(image_rgb, cv2.COLOR_RGB2BGR), [cv2.IMWRITE_JPEG_QUALITY, quality]
    )
    if not ok:
        raise RuntimeError("could not encode a JPEG")
    return buffer.tobytes()


class PreviewEncoder:
    """Encodes the newest frame to JPEG once, however many viewers are watching."""

    def __init__(self, live: LiveState, quality: int = 75) -> None:
        self._live = live
        self._quality = quality
        self._lock = threading.Lock()
        self._cached_seq = -1
        self._cached: bytes | None = None

    def jpeg_for(self, seq: int, image: np.ndarray) -> bytes:
        with self._lock:
            if seq != self._cached_seq or self._cached is None:
                self._cached, self._cached_seq = encode_jpeg(image, self._quality), seq
            return self._cached


def mjpeg_parts(
    live: LiveState,
    encoder: PreviewEncoder,
    *,
    fps: float = 10.0,
    should_stop: Callable[[], bool] = lambda: False,
    keepalive_s: float = 2.0,
) -> Iterator[bytes]:
    """The body of a ``multipart/x-mixed-replace`` stream: one JPEG part per frame, at most ``fps``.

    If the camera stalls, the last frame is repeated every ``keepalive_s`` so that a closed
    connection is noticed instead of leaving this generator waiting forever. It waits in short
    slices, so a stop request is noticed within a fraction of a second.
    """
    seq = 0
    last: bytes | None = None
    last_sent = float("-inf")
    min_interval = 1 / fps
    while not should_stop():
        spare = min_interval - (time.monotonic() - last_sent)
        if spare > 0:
            time.sleep(spare)  # keep to the preview rate; frames in between are skipped
        new_seq, image = live.wait_for_frame(seq, 0.2)
        fresh = image is not None and new_seq > seq
        if fresh and image is not None:
            last, seq = encoder.jpeg_for(new_seq, image), new_seq
        if last is None or not (fresh or time.monotonic() - last_sent >= keepalive_s):
            continue
        header = f"--{BOUNDARY}\r\nContent-Type: image/jpeg\r\nContent-Length: {len(last)}\r\n\r\n"
        yield header.encode() + last + b"\r\n"
        last_sent = time.monotonic()


def create_app(
    service: VisionService,
    *,
    snapshot: SnapshotProvider | None = None,
    preview_fps: float = 10.0,
    live_hz: float = 10.0,
    jpeg_quality: int = 75,
) -> FastAPI:
    """The web app for ``service``. ``snapshot`` gives a full-size still (default: latest frame)."""
    live = service.live
    encoder = PreviewEncoder(live, jpeg_quality)
    app = FastAPI(title="TrafficPi vision", docs_url=None, redoc_url=None, openapi_url=None)
    # Set by ServerThread.stop() so open preview streams end instead of holding up shutdown.
    app.state.stopping = threading.Event()

    @app.get("/health")
    def health() -> dict[str, object]:
        state = live.snapshot()
        return {
            "status": "ok" if state["camera"]["ok"] else "camera-offline",
            "camera": state["camera"],
            "fps": state["fps"],
            "uptime_s": state["uptime_s"],
            "frames": service.frames,
            "events_recorded": service.events_recorded,
            "trails_recorded": service.trails_recorded,
            "evidence_written": service.evidence_written,
            "evidence_waiting": service.evidence_waiting,
            "evidence_frames_dropped": service.evidence_frames_dropped,
            "stops_skipped": service.stops_skipped,  # passes the owner's hours turned away
            "profile_version": service.profile_version,
            "drift": state["drift"].get("state"),  # ok, drifted, unknown, no_reference; None: off
        }

    @app.get("/state")
    def state() -> dict[str, object]:
        return live.snapshot()

    @app.post("/reload")
    def reload() -> dict[str, bool]:
        service.request_reload()
        return {"requested": True}

    @app.get("/snapshot.jpg")
    def snapshot_jpg() -> Response:
        try:
            image = snapshot() if snapshot is not None else live.latest_frame()[1]
        except Exception as exc:
            return JSONResponse({"error": f"snapshot failed: {exc}"}, status_code=503)
        if image is None:
            return JSONResponse({"error": "no frame yet"}, status_code=503)
        return Response(encode_jpeg(image, 90), media_type="image/jpeg")

    @app.get("/preview.mjpg")
    def preview() -> StreamingResponse:
        return StreamingResponse(
            mjpeg_parts(live, encoder, fps=preview_fps, should_stop=app.state.stopping.is_set),
            media_type=f"multipart/x-mixed-replace; boundary={BOUNDARY}",
        )

    @app.websocket("/live")
    async def live_socket(websocket: WebSocket) -> None:
        await websocket.accept()
        last_frame, last_sent = -2, 0.0
        try:
            while not app.state.stopping.is_set():
                current = live.snapshot()
                if current["frame"] != last_frame or time.monotonic() - last_sent > 1.0:
                    await websocket.send_json({"type": "state", **current})
                    last_frame, last_sent = current["frame"], time.monotonic()
                await asyncio.sleep(1 / live_hz)
            # The service is stopping: say goodbye, so that the server need not wait for this
            # viewer and then cancel it (which logs an ERROR on every restart with a viewer open).
            await websocket.close(code=1001)
        except (WebSocketDisconnect, RuntimeError):
            return

    return app


class ServerThread:
    """Runs a uvicorn server in a background thread, so the main thread can run the camera loop."""

    def __init__(self, app: FastAPI, host: str = "127.0.0.1", port: int = 8001) -> None:
        self.host, self.port = host, port
        config = uvicorn.Config(
            app,
            host=host,
            port=port,
            log_level="warning",
            access_log=False,
            timeout_graceful_shutdown=3,  # do not wait for viewers that keep a stream open
        )
        self._app = app
        self._server = uvicorn.Server(config)
        self._thread = threading.Thread(target=self._run, name="vision-server", daemon=True)

    def _run(self) -> None:
        # uvicorn exits, instead of raising, when it cannot bind its port.
        with contextlib.suppress(SystemExit):
            self._server.run()

    def start(self, timeout: float = 10.0) -> None:
        self._thread.start()
        deadline = time.monotonic() + timeout
        while not self._server.started:
            if not self._thread.is_alive() or time.monotonic() > deadline:
                raise RuntimeError(
                    f"web server did not start on {self.host}:{self.port} (is the port in use?)"
                )
            time.sleep(0.02)

    def stop(self, timeout: float = 5.0) -> None:
        stopping = getattr(self._app.state, "stopping", None)
        if stopping is not None:
            stopping.set()
        self._server.should_exit = True
        self._thread.join(timeout)
