"""Object detection on the Hailo accelerator."""

from __future__ import annotations

import os
import subprocess
import time
from collections import deque
from collections.abc import Iterable, Iterator
from concurrent.futures import Future
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Protocol, Self

import numpy as np

from trafficpi.core.coco import TRAFFIC_CLASS_IDS, class_name
from trafficpi.core.letterbox import Letterbox
from trafficpi.core.types import Detection
from trafficpi.vision.sources import Frame

MODEL_DIR = Path("/usr/share/hailo-models")

# `hailortcli fw-control identify` reports the architecture; compiled models (HEF files) are
# specific to it. Both the 13 TOPS Hailo-8L and the 26 TOPS Hailo-8 are called "Hailo-8" elsewhere.
ARCH_SUFFIX = {"HAILO8": "h8", "HAILO8L": "h8l"}

DEFAULT_MODEL = "yolov8s"
DEFAULT_SCORE_THRESHOLD = 0.25

# How many frames of a recording go to the accelerator together, and how many such groups are on
# their way at once. A model too big for the chip runs in several contexts, and changing context
# costs the same for one frame as for a group: yolov8s on the Hailo-8L does 59 frames a second one
# at a time and 136 in groups of 8. The second group keeps the chip busy while the first comes back.
# The boxes are the same bit for bit; each frame only waits longer, so this is not for live frames.
OFFLINE_BATCH = 8
GROUPS_IN_FLIGHT = 2


@dataclass(slots=True)
class DetectTimings:
    """How long the last call to ``detect`` spent in each step, in milliseconds."""

    preprocess_ms: float = 0.0
    infer_ms: float = 0.0
    postprocess_ms: float = 0.0

    @property
    def total_ms(self) -> float:
        return self.preprocess_ms + self.infer_ms + self.postprocess_ms


class Detector(Protocol):
    """Finds objects in an RGB image. Boxes are fractions of the image that was passed in."""

    timings: DetectTimings

    def detect(self, image: np.ndarray) -> list[Detection]: ...

    def close(self) -> None: ...


class StreamDetector(Detector, Protocol):
    """A detector that can work ahead of its results, for frames that are all there already."""

    def detect_stream(self, frames: Iterable[Frame]) -> Iterator[tuple[Frame, list[Detection]]]: ...


def _hailort_env() -> dict[str, str]:
    """HailoRT writes ``hailort.log`` to the working directory, one line per inference.

    That is hundreds of megabytes a day on an SD card, so file logging is off unless the
    caller set ``HAILORT_LOGGER_PATH`` themselves.
    """
    return {**os.environ, "HAILORT_LOGGER_PATH": os.environ.get("HAILORT_LOGGER_PATH", "NONE")}


@lru_cache(maxsize=1)
def hailo_identity() -> dict[str, str]:
    """What ``hailortcli fw-control identify`` says about the accelerator, asked for once:
    ``architecture`` (``HAILO8L``), ``firmware`` (``4.23.0``) and ``board`` (``Hailo-8``; the
    board's own name does not tell the 8 from the 8L). Empty if it does not answer."""
    try:
        out = subprocess.run(
            ["hailortcli", "fw-control", "identify"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
            env=_hailort_env(),
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return {}
    wanted = {
        "Device Architecture": ("architecture", str.upper),
        "Firmware Version": ("firmware", lambda text: text.split()[0] if text.split() else ""),
        "Board Name": ("board", str),
    }
    found: dict[str, str] = {}
    for line in out.splitlines():
        label, _, value = line.partition(":")
        if label.strip() in wanted and value.strip():
            key, convert = wanted[label.strip()]
            found[key] = convert(value.strip())
    return {key: value for key, value in found.items() if value}


@lru_cache(maxsize=1)
def hailo_architecture() -> str | None:
    """The accelerator's architecture, e.g. ``HAILO8L``, or None if it cannot be determined."""
    return hailo_identity().get("architecture")


def find_hef(model: str = DEFAULT_MODEL, *, model_dir: Path = MODEL_DIR) -> Path:
    """Path of the ``model`` HEF built for the installed accelerator.

    Raises ``RuntimeError`` with a helpful message if the accelerator or the file is missing.
    """
    arch = hailo_architecture()
    if arch not in ARCH_SUFFIX:
        raise RuntimeError(
            f"Could not determine the Hailo architecture (got {arch!r}). Is the driver loaded? "
            "Try `hailortcli fw-control identify`, or give an explicit HEF path."
        )
    path = model_dir / f"{model}_{ARCH_SUFFIX[arch]}.hef"
    if not path.exists():
        available = sorted(p.name for p in model_dir.glob(f"*_{ARCH_SUFFIX[arch]}*.hef"))
        raise RuntimeError(
            f"{path} not found for {arch}. Available: {', '.join(available) or 'none'}"
        )
    return path


def _per_class(raw: Any) -> list[Any]:
    """Normalise the output of ``Hailo.run`` to one array per class.

    For these models HailoRT runs non-maximum suppression on the CPU, on what the chip sends back,
    and returns, per class, an array of rows ``[y_min, x_min, y_max, x_max, score]`` with
    coordinates as fractions of the input.
    Depending on version and batch settings the list may be wrapped in a dict or a batch list.
    """
    if isinstance(raw, dict):
        raw = next(iter(raw.values()))
    if isinstance(raw, list | tuple) and len(raw) == 1 and isinstance(raw[0], list | tuple):
        raw = raw[0]
    return list(raw)


def parse_nms_output(
    raw: Any,
    letterbox: Letterbox,
    *,
    threshold: float = DEFAULT_SCORE_THRESHOLD,
    class_ids: frozenset[int] | None = TRAFFIC_CLASS_IDS,
) -> list[Detection]:
    """Turn HailoRT's per-class output into detections in source-image coordinates.

    ``class_ids=None`` keeps every class. The result is sorted by score, best first.
    """
    detections: list[Detection] = []
    for class_id, rows in enumerate(_per_class(raw)):
        if class_ids is not None and class_id not in class_ids:
            continue
        arr = np.asarray(rows, dtype=np.float64)
        if arr.size == 0:
            continue
        arr = arr.reshape(-1, arr.shape[-1])
        for y0, x0, y1, x1, score in arr[arr[:, 4] >= threshold, :5]:
            sx0, sy0, sx1, sy1 = letterbox.box_to_source(float(x0), float(y0), float(x1), float(y1))
            if sx1 <= sx0 or sy1 <= sy0:  # entirely inside the padding
                continue
            detections.append(
                Detection(class_id, class_name(class_id), float(score), sx0, sy0, sx1, sy1)
            )
    detections.sort(key=lambda d: d.score, reverse=True)
    return detections


# Hailo helper objects that were "closed" but are deliberately kept alive; see HailoDetector.close.
_RETAINED: list[Any] = []


@dataclass(slots=True)
class _Group:
    """Frames on their way through the accelerator together."""

    frames: list[Frame]
    letterbox: Letterbox
    result: Future[list[Any]]
    preprocess_ms: float


class HailoDetector:
    """A YOLO-family detector running on the Hailo accelerator via Picamera2's helper.

    ``batch_size`` makes :meth:`detect_stream` send that many frames at a time. It is fixed when
    the model is loaded, and the model cannot be loaded twice in a process (see :meth:`close`).
    """

    def __init__(
        self,
        hef_path: Path,
        *,
        score_threshold: float = DEFAULT_SCORE_THRESHOLD,
        class_ids: frozenset[int] | None = TRAFFIC_CLASS_IDS,
        batch_size: int | None = None,
    ) -> None:
        os.environ.setdefault("HAILORT_LOGGER_PATH", "NONE")  # before HailoRT initialises
        from hailo_platform import FormatOrder  # imported late: the package works without hardware
        from picamera2.devices import Hailo

        class ChipOrderHailo(Hailo):
            """Takes its input in the order the chip reads, so that HailoRT need not rearrange it.

            The hook is private to the helper. If it is ever renamed this is simply not called:
            frames stay in the ordinary order, a millisecond slower and otherwise the same.
            """

            row_planar = False

            def _set_input_output(self, output_type: str) -> None:
                super()._set_input_output(output_type)
                if self.hef.get_input_stream_infos()[0].format.order == FormatOrder.NHCW:
                    self.infer_model.input().set_format_order(FormatOrder.NHCW)
                    self.row_planar = True

        self.hef_path = Path(hef_path)
        self.score_threshold = score_threshold
        self.class_ids = class_ids
        self.batch_size = batch_size
        self.timings = DetectTimings()
        self._hailo: Any = ChipOrderHailo(str(self.hef_path), batch_size=batch_size)
        self._row_planar: bool = self._hailo.row_planar
        height, width, _ = self._hailo.get_input_shape()
        self.input_size: tuple[int, int] = (int(width), int(height))
        self._letterbox: Letterbox | None = None
        self._canvas: np.ndarray | None = None
        self._group_canvases: deque[list[np.ndarray]] = deque()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _prepare(self, image: np.ndarray) -> tuple[Letterbox, np.ndarray]:
        height, width = image.shape[:2]
        lb = self._letterbox
        if lb is None or (lb.src_w, lb.src_h) != (width, height) or self._canvas is None:
            lb = Letterbox.fit(width, height, *self.input_size)
            self._letterbox, self._canvas = lb, lb.new_canvas(row_planar=self._row_planar)
            self._group_canvases.clear()
        return lb, self._canvas

    def _fill(self, lb: Letterbox, image: np.ndarray, canvas: np.ndarray) -> None:
        if self._row_planar:
            lb.apply_row_planar(image, canvas)
        else:
            lb.apply(image, out=canvas)

    def detect(self, image: np.ndarray) -> list[Detection]:
        if self._hailo is None:
            raise RuntimeError("detector is closed")
        t0 = time.perf_counter()
        lb, canvas = self._prepare(image)
        self._fill(lb, image, canvas)
        t1 = time.perf_counter()
        raw = self._hailo.run(canvas if self.batch_size is None else [canvas])
        t2 = time.perf_counter()
        detections = parse_nms_output(
            raw, lb, threshold=self.score_threshold, class_ids=self.class_ids
        )
        t3 = time.perf_counter()
        self.timings = DetectTimings((t1 - t0) * 1000, (t2 - t1) * 1000, (t3 - t2) * 1000)
        return detections

    def detect_stream(self, frames: Iterable[Frame]) -> Iterator[tuple[Frame, list[Detection]]]:
        """Each frame with what ``detect`` would have found in it, in order, sent ``batch_size`` at
        a time so that the accelerator never waits for the caller. ``timings`` describes the frame
        just returned: the time a group took is shared out over its frames.
        """
        if self.batch_size is None:
            for frame in frames:
                yield frame, self.detect(frame.image)
            return
        in_flight: deque[_Group] = deque()
        waiting: list[Frame] = []
        for frame in frames:
            waiting.append(frame)
            if len(waiting) == self.batch_size:
                in_flight.append(self._send(waiting))
                waiting = []
                if len(in_flight) == GROUPS_IN_FLIGHT:
                    yield from self._collect(in_flight.popleft())
        if waiting:
            in_flight.append(self._send(waiting))
        while in_flight:
            yield from self._collect(in_flight.popleft())

    def _send(self, frames: list[Frame]) -> _Group:
        if self._hailo is None:
            raise RuntimeError("detector is closed")
        t0 = time.perf_counter()
        lb, _ = self._prepare(frames[0].image)
        canvases = self._next_canvases(lb)[: len(frames)]
        for frame, canvas in zip(frames, canvases, strict=True):
            self._fill(lb, frame.image, canvas)
        result = self._hailo.run_async(canvases)
        return _Group(frames, lb, result, (time.perf_counter() - t0) * 1000 / len(frames))

    def _next_canvases(self, lb: Letterbox) -> list[np.ndarray]:
        """The canvases of the oldest group, whose frames have left the accelerator by now."""
        if len(self._group_canvases) < GROUPS_IN_FLIGHT:
            size = self.batch_size or 1
            canvases = [lb.new_canvas(row_planar=self._row_planar) for _ in range(size)]
        else:
            canvases = self._group_canvases.popleft()
        self._group_canvases.append(canvases)
        return canvases

    def _collect(self, group: _Group) -> Iterator[tuple[Frame, list[Detection]]]:
        t0 = time.perf_counter()
        raw = group.result.result()
        infer_ms = (time.perf_counter() - t0) * 1000 / len(group.frames)
        for frame, output in zip(group.frames, raw, strict=True):
            t1 = time.perf_counter()
            detections = parse_nms_output(
                output, group.letterbox, threshold=self.score_threshold, class_ids=self.class_ids
            )
            postprocess_ms = (time.perf_counter() - t1) * 1000
            self.timings = DetectTimings(group.preprocess_ms, infer_ms, postprocess_ms)
            yield frame, detections

    def close(self) -> None:
        """Stop using the detector. The accelerator is deliberately not released.

        With HailoRT 4.23.0 on this platform (Pi 5, kernel 6.18, 16 KB pages), releasing the
        virtual device makes a native HailoRT thread crash within a second (15 of 15 runs), and
        even dropping a configured model does so about one run in three. Never tearing down is
        stable: the objects stay alive until the process exits and the operating system cleans
        up (0 of 15 crashes). So a closed detector is just retained, and changing the model
        means restarting the process.
        """
        if self._hailo is not None:
            _RETAINED.append(self._hailo)
            self._hailo = None
