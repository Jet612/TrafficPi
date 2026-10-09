#!/usr/bin/env python3
"""Hardware smoke test: camera -> Hailo accelerator -> detections.

Runs for a fixed time, then prints throughput, latency and what was detected.
Nothing is written to disk and no frames are saved.

Uses the system Python (Picamera2 and HailoRT come from apt):

    python3 scripts/smoke_hailo.py                  # 60 s, camera + Hailo
    python3 scripts/smoke_hailo.py --source blank   # Hailo only (no camera)
    python3 scripts/smoke_hailo.py --seconds 10 --model yolov6n

Exit code 0 means frames were processed without errors.
"""

from __future__ import annotations

import argparse
import os
import statistics
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

import numpy as np

# HailoRT writes a log line per inference to ./hailort.log unless told otherwise.
os.environ.setdefault("HAILORT_LOGGER_PATH", "NONE")

MODEL_DIR = Path("/usr/share/hailo-models")

# COCO class ids that matter for traffic; everything else is printed as classN.
COCO_NAMES = {0: "person", 1: "bicycle", 2: "car", 3: "motorcycle", 5: "bus", 7: "truck"}

# `hailortcli fw-control identify` reports the architecture; HEF files are per architecture.
ARCH_SUFFIX = {"HAILO8": "h8", "HAILO8L": "h8l"}


def hailo_architecture() -> str | None:
    """Return e.g. 'HAILO8' or 'HAILO8L' from hailortcli, or None if unavailable."""
    try:
        out = subprocess.run(
            ["hailortcli", "fw-control", "identify"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    for line in out.splitlines():
        if "Device Architecture" in line:
            return line.split(":", 1)[1].strip().upper()
    return None


def pick_hef(model: str, explicit: str | None) -> Path:
    if explicit:
        return Path(explicit)
    arch = hailo_architecture()
    if arch not in ARCH_SUFFIX:
        sys.exit(
            f"Could not determine the Hailo architecture (got {arch!r}). "
            "Is the driver loaded? Try `hailortcli fw-control identify`, or pass --hef."
        )
    path = MODEL_DIR / f"{model}_{ARCH_SUFFIX[arch]}.hef"
    if not path.exists():
        available = sorted(p.name for p in MODEL_DIR.glob(f"*_{ARCH_SUFFIX[arch]}.hef"))
        sys.exit(f"{path} not found for {arch}. Available: {', '.join(available) or 'none'}")
    print(f"accelerator: {arch}  model: {path}")
    return path


def describe(obj, depth: int = 0) -> str:
    """Short, one-line description of a nested result, to learn the output layout."""
    if isinstance(obj, dict):
        return "dict{" + ", ".join(f"{k}: {describe(v, depth + 1)}" for k, v in list(obj.items())[:3]) + "}"
    if isinstance(obj, (list, tuple)):
        inner = describe(obj[0], depth + 1) if obj else "empty"
        return f"{type(obj).__name__}[len={len(obj)}] of {inner}"
    if isinstance(obj, np.ndarray):
        return f"ndarray{obj.shape} {obj.dtype}"
    return type(obj).__name__


def per_class_detections(result) -> list:
    """Normalise Hailo's NMS output to a list with one (N, 5) array per class.

    Each row is [y_min, x_min, y_max, x_max, score], coordinates normalised to 0..1.
    """
    if isinstance(result, dict):
        result = next(iter(result.values()))
    # Some versions wrap the per-class list in a batch dimension.
    if isinstance(result, (list, tuple)) and len(result) == 1 and isinstance(result[0], (list, tuple)):
        result = result[0]
    return list(result)


def count_detections(per_class: list, threshold: float) -> Counter:
    counts: Counter = Counter()
    for class_id, rows in enumerate(per_class):
        rows = np.asarray(rows)
        if rows.size == 0:
            continue
        n = int((rows.reshape(-1, rows.shape[-1])[:, 4] >= threshold).sum())
        if n:
            counts[COCO_NAMES.get(class_id, f"class{class_id}")] += n
    return counts


def soc_temp_c() -> float | None:
    try:
        return int(Path("/sys/class/thermal/thermal_zone0/temp").read_text()) / 1000
    except (OSError, ValueError):
        return None


def throttled_flags() -> str | None:
    try:
        out = subprocess.run(["vcgencmd", "get_throttled"], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    return out.strip().split("=", 1)[-1] or None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seconds", type=float, default=60, help="how long to run (default 60)")
    ap.add_argument("--model", default="yolov8s", help="model in /usr/share/hailo-models (default yolov8s)")
    ap.add_argument("--hef", help="explicit path to a .hef file (overrides --model)")
    ap.add_argument("--threshold", type=float, default=0.4, help="minimum detection score (default 0.4)")
    ap.add_argument(
        "--source",
        choices=("camera", "blank"),
        default="camera",
        help="camera frames, or blank frames to test the accelerator alone",
    )
    args = ap.parse_args()

    from picamera2.devices import Hailo  # imported late so --help works anywhere

    hef = pick_hef(args.model, args.hef)

    infer_ms: list[float] = []
    capture_ms: list[float] = []
    totals: Counter = Counter()
    frames = 0
    shape_reported = False
    temp_start = soc_temp_c()

    picam2 = None
    try:
        # HailoRT 4.23.0 crashes a native thread shortly after the device is released, so the
        # helper is deliberately never closed; the operating system cleans up at exit.
        hailo = Hailo(str(hef))
        model_h, model_w, _ = hailo.get_input_shape()
        print(f"model input: {model_w}x{model_h}")

        if args.source == "camera":
            from picamera2 import Picamera2

            picam2 = Picamera2()
            config = picam2.create_video_configuration(
                main={"size": (1280, 720)},
                lores={"size": (model_w, model_h), "format": "RGB888"},
                controls={"FrameRate": 30},
            )
            picam2.configure(config)
            picam2.start()
            time.sleep(0.5)  # let auto-exposure settle
        blank = np.zeros((model_h, model_w, 3), dtype=np.uint8)

        start = time.perf_counter()
        window_start, window_frames, window_counts = start, 0, Counter()
        while time.perf_counter() - start < args.seconds:
            t0 = time.perf_counter()
            frame = picam2.capture_array("lores") if picam2 else blank
            t1 = time.perf_counter()
            result = hailo.run(frame)
            t2 = time.perf_counter()

            if not shape_reported:
                print(f"hailo.run() returned: {describe(result)}")
                shape_reported = True

            counts = count_detections(per_class_detections(result), args.threshold)
            capture_ms.append((t1 - t0) * 1000)
            infer_ms.append((t2 - t1) * 1000)
            frames += 1
            window_frames += 1
            totals.update(counts)
            window_counts.update(counts)

            now = time.perf_counter()
            if now - window_start >= 5:
                fps = window_frames / (now - window_start)
                print(
                    f"t={now - start:5.1f}s  {fps:5.1f} fps  "
                    f"capture {statistics.mean(capture_ms[-window_frames:]):5.1f} ms  "
                    f"infer {statistics.mean(infer_ms[-window_frames:]):5.1f} ms  "
                    f"detections(last 5s) {dict(window_counts) or '-'}"
                )
                window_start, window_frames, window_counts = now, 0, Counter()
        elapsed = time.perf_counter() - start
    except Exception as exc:  # report, don't hide: this is a diagnostic tool
        print(f"FAILED after {frames} frames: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    finally:
        if picam2 is not None:
            picam2.stop()
            picam2.close()

    if frames == 0:
        print("FAILED: no frames processed", file=sys.stderr)
        return 1

    p95 = sorted(infer_ms)[int(len(infer_ms) * 0.95) - 1] if len(infer_ms) >= 20 else max(infer_ms)
    print("\n== summary ==")
    print(f"frames: {frames} in {elapsed:.1f}s = {frames / elapsed:.1f} fps ({args.source})")
    print(f"capture: mean {statistics.mean(capture_ms):.1f} ms")
    print(f"infer:   mean {statistics.mean(infer_ms):.1f} ms, p95 {p95:.1f} ms")
    print(f"detections >= {args.threshold}: {dict(totals) or 'none'}")
    print(f"SoC temp: {temp_start} C -> {soc_temp_c()} C, throttled flags: {throttled_flags()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
