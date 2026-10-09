"""Frame sources: where the pipeline gets its images from."""

from __future__ import annotations

import contextlib
import time
from collections.abc import Callable
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Any, Literal, Protocol, Self

import numpy as np

# A frame arrives every 33 ms; if none comes for this long the camera has stalled. The service
# then restarts it, instead of waiting forever with a frozen picture.
READ_TIMEOUT_S = 5.0


@dataclass(frozen=True, slots=True)
class CameraSettings:
    """How the Raspberry Pi camera is configured.

    The defaults suit a fixed roadside view in daylight: the full field of view, focus fixed at
    infinity (autofocus hunting would blur vehicles), and exposure biased towards short shutter
    times to limit motion blur.
    """

    # The 2304x1296 sensor mode is the full field of view (2x2 binned). For a 1280x720 stream
    # libcamera would otherwise pick the 1536x864 mode, a centre crop with 2/3 of the field of view.
    sensor_size: tuple[int, int] | None = (2304, 1296)
    main_size: tuple[int, int] = (1280, 720)  # stills and recordings
    lores_size: tuple[int, int] = (640, 360)  # what the detector sees
    frame_rate: float = 30.0
    lens_position: float | None = (
        0.0  # dioptres; 0.0 is infinity, None enables continuous autofocus
    )
    exposure_mode: Literal["normal", "short", "long"] = "short"
    exposure_time_us: int | None = None  # fixed exposure; turns automatic exposure off
    analogue_gain: float | None = None  # fixed gain; turns automatic gain off
    # Picamera2 names pixel formats by the byte order of a 32-bit word, so its "BGR888" is
    # R, G, B in memory. The detectors are trained on RGB.
    channel_order: Literal["rgb", "bgr"] = "rgb"


@dataclass(slots=True)
class Frame:
    """One image from a source, with the camera's own timestamp."""

    index: int
    timestamp_ns: (
        int  # sensor timestamp on the monotonic clock; use this for timing, not arrival time
    )
    image: np.ndarray  # (height, width, 3) uint8, RGB unless the source was set up otherwise
    exposure_us: int | None = None
    analogue_gain: float | None = None
    lens_position: float | None = None


class EndOfStream(Exception):
    """Raised by ``read`` when a recorded source has no more frames."""


class FrameSource(Protocol):
    """Anything that produces frames: the camera now, recorded video later."""

    @property
    def frame_size(self) -> tuple[int, int]:
        """(width, height) of ``Frame.image``."""
        ...

    def start(self) -> None: ...

    def read(self) -> Frame:
        """Block until the next frame is available; raises ``EndOfStream`` when a recording ends."""
        ...

    def stop(self) -> None: ...


class PiCameraSource:
    """A Raspberry Pi camera through Picamera2.

    Two streams run at once. The small ``lores`` stream, scaled by the ISP at no CPU cost, feeds
    the detector. The larger ``main`` stream is only used when recording a clip.
    """

    def __init__(self, settings: CameraSettings | None = None) -> None:
        self.settings = settings or CameraSettings()
        self.frame_size: tuple[int, int] = self.settings.lores_size
        self._picam2: Any = None
        self._encoder: Any = None
        self._index = 0

    def __enter__(self) -> Self:
        self.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.stop()

    def _controls(self) -> dict[str, Any]:
        from libcamera import controls

        s = self.settings
        modes = {
            "normal": controls.AeExposureModeEnum.Normal,
            "short": controls.AeExposureModeEnum.Short,
            "long": controls.AeExposureModeEnum.Long,
        }
        result: dict[str, Any] = {
            "FrameRate": s.frame_rate,
            "AeExposureMode": modes[s.exposure_mode],
        }
        if s.lens_position is None:
            result["AfMode"] = controls.AfModeEnum.Continuous
        else:
            result["AfMode"] = controls.AfModeEnum.Manual
            result["LensPosition"] = s.lens_position
        if s.exposure_time_us is not None:
            result["ExposureTime"] = s.exposure_time_us
        if s.analogue_gain is not None:
            result["AnalogueGain"] = s.analogue_gain
        return result

    def start(self) -> None:
        from picamera2 import Picamera2

        s = self.settings
        try:
            picam2 = Picamera2()
        except RuntimeError as exc:
            raise RuntimeError(
                f"cannot open the camera ({exc}); is another program using it?"
            ) from exc
        try:
            sensor = {"output_size": s.sensor_size, "bit_depth": 10} if s.sensor_size else None
            config = picam2.create_video_configuration(
                main={"size": s.main_size, "format": "YUV420"},
                lores={
                    "size": s.lores_size,
                    "format": "BGR888" if s.channel_order == "rgb" else "RGB888",
                },
                sensor=sensor,
                controls=self._controls(),
                queue=False,  # always hand out the freshest frame; if slow, frames are skipped
            )
            picam2.configure(config)
            picam2.start()
        except BaseException:
            # An opened camera that is not closed stays acquired by this process, and then no
            # later attempt, however long we wait, can open it.
            with contextlib.suppress(Exception):
                picam2.close()
            raise
        self._picam2 = picam2

    def describe(self) -> dict[str, Any]:
        """The configuration the camera actually ended up with, for logs and recordings."""
        picam2 = self._require_started()
        config = picam2.camera_configuration()
        return {
            "sensor": dict(config.get("sensor", {})),
            "main": {"size": config["main"]["size"], "format": config["main"]["format"]},
            "lores": {"size": config["lores"]["size"], "format": config["lores"]["format"]},
            "controls": {k: str(v) for k, v in self._controls().items()},
        }

    def read(self) -> Frame:
        picam2 = self._require_started()
        try:
            request = picam2.capture_request(wait=READ_TIMEOUT_S)
        except TimeoutError:
            raise RuntimeError(f"no frame from the camera for {READ_TIMEOUT_S:.0f} s") from None
        try:
            image = request.make_array("lores")
            meta = request.get_metadata()
        finally:
            request.release()
        frame = Frame(
            index=self._index,
            timestamp_ns=int(meta["SensorTimestamp"]),
            image=image,
            exposure_us=meta.get("ExposureTime"),
            analogue_gain=meta.get("AnalogueGain"),
            lens_position=meta.get("LensPosition"),
        )
        self._index += 1
        return frame

    def capture_still(self) -> np.ndarray:
        """A full-size RGB image from the main stream, for snapshots."""
        import cv2

        yuv = self._require_started().capture_array("main")  # planar YUV 4:2:0
        return cv2.cvtColor(yuv, cv2.COLOR_YUV2RGB_I420)

    def start_recording(self, path: Path, *, bitrate: int = 4_000_000) -> None:
        """Record the ``main`` stream to an MP4 file while frames keep flowing to ``read``.

        The Pi 5 has no hardware H.264 encoder, so this costs CPU; use it for short clips.
        """
        from picamera2.encoders import LibavH264Encoder
        from picamera2.outputs import PyavOutput

        picam2 = self._require_started()
        # PyAV wants an exact rational for the frame rate, not a float.
        framerate = Fraction(self.settings.frame_rate).limit_denominator(1001)
        encoder = LibavH264Encoder(bitrate=bitrate, framerate=framerate)
        picam2.start_encoder(encoder, PyavOutput(str(path)), name="main")
        self._encoder = encoder

    def stop_recording(self) -> None:
        if self._encoder is not None:
            self._require_started().stop_encoder(self._encoder)
            self._encoder = None

    def stop(self) -> None:
        if self._picam2 is None:
            return
        self.stop_recording()
        self._picam2.stop()
        self._picam2.close()
        self._picam2 = None

    def _require_started(self) -> Any:
        if self._picam2 is None:
            raise RuntimeError("camera not started")
        return self._picam2


class VideoFileSource:
    """Frames from a recorded video file, to run clips through the pipeline offline.

    Frames come as fast as they can be decoded. Timestamps are ``index / fps`` from the start of
    the file, so replays are deterministic.
    """

    def __init__(
        self,
        path: Path,
        *,
        size: tuple[int, int] = (640, 360),
        channel_order: Literal["rgb", "bgr"] = "rgb",
    ) -> None:
        self.path = Path(path)
        self.frame_size: tuple[int, int] = size
        self.channel_order = channel_order
        self.fps = 30.0
        self.frame_count = 0  # as reported by the container; may be approximate
        self._capture: Any = None
        self._index = 0

    def __enter__(self) -> Self:
        self.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.stop()

    def start(self) -> None:
        import cv2

        capture = cv2.VideoCapture(str(self.path))
        if not capture.isOpened():
            raise RuntimeError(f"cannot open video {self.path}")
        self._capture = capture
        self.fps = capture.get(cv2.CAP_PROP_FPS) or 30.0
        self.frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))

    def read(self) -> Frame:
        import cv2

        if self._capture is None:
            raise RuntimeError("video not opened")
        ok, bgr = self._capture.read()
        if not ok:
            raise EndOfStream
        image = cv2.resize(bgr, self.frame_size, interpolation=cv2.INTER_AREA)
        if self.channel_order == "rgb":
            image = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
        frame = Frame(self._index, round(self._index * 1e9 / self.fps), image)
        self._index += 1
        return frame

    def rewind(self) -> None:
        """Go back to the first frame. Timestamps keep counting up, so time never runs backwards."""
        import cv2

        if self._capture is None:
            raise RuntimeError("video not opened")
        self._capture.set(cv2.CAP_PROP_POS_FRAMES, 0)

    def stop(self) -> None:
        if self._capture is not None:
            self._capture.release()
            self._capture = None


class RealtimeLoopSource:
    """A recorded video played like a live camera: at its own speed, over and over.

    This is for developing the dashboard and the rules without a camera or any traffic. Frames
    come at the video's frame rate rather than as fast as they decode, and when the clip ends it
    starts again with timestamps that keep rising, so the tracker sees one endless recording.
    """

    def __init__(
        self,
        video: VideoFileSource,
        *,
        loop: bool = True,
        monotonic_ns: Callable[[], int] = time.monotonic_ns,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.video = video
        self.loop = loop
        self._monotonic_ns = monotonic_ns
        self._sleep = sleep
        self._started_ns = 0
        self._first_ts: int | None = None

    @property
    def frame_size(self) -> tuple[int, int]:
        return self.video.frame_size

    def start(self) -> None:
        self.video.start()
        self._started_ns = self._monotonic_ns()
        self._first_ts = None

    def read(self) -> Frame:
        try:
            frame = self.video.read()
        except EndOfStream:
            if not self.loop:
                raise
            self.video.rewind()
            frame = self.video.read()
        if self._first_ts is None:
            self._first_ts = frame.timestamp_ns
        due_ns = self._started_ns + frame.timestamp_ns - self._first_ts
        wait_ns = due_ns - self._monotonic_ns()
        if wait_ns > 0:
            self._sleep(wait_ns / 1e9)
        return frame

    def stop(self) -> None:
        self.video.stop()
