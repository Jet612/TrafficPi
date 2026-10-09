"""Object detection on the Hailo accelerator."""

from __future__ import annotations

import os
import subprocess
import time
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Protocol, Self

import numpy as np

from trafficpi.core.coco import TRAFFIC_CLASS_IDS, class_name
from trafficpi.core.letterbox import Letterbox
from trafficpi.core.types import Detection

MODEL_DIR = Path("/usr/share/hailo-models")

# `hailortcli fw-control identify` reports the architecture; compiled models (HEF files) are
# specific to it. Both the 13 TOPS Hailo-8L and the 26 TOPS Hailo-8 are called "Hailo-8" elsewhere.
ARCH_SUFFIX = {"HAILO8": "h8", "HAILO8L": "h8l"}

DEFAULT_MODEL = "yolov8s"
DEFAULT_SCORE_THRESHOLD = 0.25


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


def _hailort_env() -> dict[str, str]:
    """HailoRT writes ``hailort.log`` to the working directory, one line per inference.

    That is hundreds of megabytes a day on an SD card, so file logging is off unless the
    caller set ``HAILORT_LOGGER_PATH`` themselves.
    """
    return {**os.environ, "HAILORT_LOGGER_PATH": os.environ.get("HAILORT_LOGGER_PATH", "NONE")}


@lru_cache(maxsize=1)
def hailo_architecture() -> str | None:
    """The accelerator's architecture, e.g. ``HAILO8L``, or None if it cannot be determined."""
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
        return None
    for line in out.splitlines():
        if "Device Architecture" in line:
            return line.split(":", 1)[1].strip().upper()
    return None


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

    For these models the chip runs non-maximum suppression and returns, per class, an array of
    rows ``[y_min, x_min, y_max, x_max, score]`` with coordinates as fractions of the input.
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
    """Turn the chip's per-class output into detections in source-image coordinates.

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


class HailoDetector:
    """A YOLO-family detector running on the Hailo accelerator via Picamera2's helper."""

    def __init__(
        self,
        hef_path: Path,
        *,
        score_threshold: float = DEFAULT_SCORE_THRESHOLD,
        class_ids: frozenset[int] | None = TRAFFIC_CLASS_IDS,
    ) -> None:
        os.environ.setdefault("HAILORT_LOGGER_PATH", "NONE")  # before HailoRT initialises
        from picamera2.devices import Hailo  # imported late so the package works without hardware

        self.hef_path = Path(hef_path)
        self.score_threshold = score_threshold
        self.class_ids = class_ids
        self.timings = DetectTimings()
        self._hailo: Any = Hailo(str(self.hef_path))
        height, width, _ = self._hailo.get_input_shape()
        self.input_size: tuple[int, int] = (int(width), int(height))
        self._letterbox: Letterbox | None = None
        self._canvas: np.ndarray | None = None

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _prepare(self, image: np.ndarray) -> tuple[Letterbox, np.ndarray]:
        height, width = image.shape[:2]
        lb = self._letterbox
        if lb is None or (lb.src_w, lb.src_h) != (width, height) or self._canvas is None:
            lb = Letterbox.fit(width, height, *self.input_size)
            self._letterbox, self._canvas = lb, lb.new_canvas()
        return lb, self._canvas

    def detect(self, image: np.ndarray) -> list[Detection]:
        if self._hailo is None:
            raise RuntimeError("detector is closed")
        t0 = time.perf_counter()
        lb, canvas = self._prepare(image)
        lb.apply(image, out=canvas)
        t1 = time.perf_counter()
        raw = self._hailo.run(canvas)
        t2 = time.perf_counter()
        detections = parse_nms_output(
            raw, lb, threshold=self.score_threshold, class_ids=self.class_ids
        )
        t3 = time.perf_counter()
        self.timings = DetectTimings((t1 - t0) * 1000, (t2 - t1) * 1000, (t3 - t2) * 1000)
        return detections

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
