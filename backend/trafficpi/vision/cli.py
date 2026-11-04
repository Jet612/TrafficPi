"""Command-line entry point: ``trafficpi-vision``."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import threading
import time
import traceback
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import NoReturn

import numpy as np

# Quiet libcamera's start-up chatter and HailoRT's per-inference file log before they initialise.
os.environ.setdefault("LIBCAMERA_LOG_LEVELS", "*:WARN")
os.environ.setdefault("HAILORT_LOGGER_PATH", "NONE")

from trafficpi.core.clock import resolve_timezone
from trafficpi.core.coco import TRAFFIC_CLASS_IDS
from trafficpi.core.notify import Heartbeat, notify, watchdog_period
from trafficpi.core.paths import DATA_DIR_HELP, default_data_dir
from trafficpi.core.profile import ProfileError
from trafficpi.core.types import Detection
from trafficpi.db.store import Store, open_store
from trafficpi.vision import pipeline
from trafficpi.vision.annotate import annotate_clip
from trafficpi.vision.compare import compare_logs, format_comparison
from trafficpi.vision.demo import DemoStreet, demo_health, demo_history
from trafficpi.vision.detector import (
    DEFAULT_MODEL,
    DEFAULT_SCORE_THRESHOLD,
    OFFLINE_BATCH,
    Detector,
    HailoDetector,
    find_hef,
    hailo_identity,
)
from trafficpi.vision.metrics import decode_throttled
from trafficpi.vision.profile import CompiledProfile, load_compiled_profile
from trafficpi.vision.recorder import DetectionLogWriter
from trafficpi.vision.replay import replay_log
from trafficpi.vision.server import ServerThread
from trafficpi.vision.server import create_app as create_server_app
from trafficpi.vision.service import VisionService
from trafficpi.vision.shutdown import StopRequest
from trafficpi.vision.sources import (
    CameraSettings,
    PiCameraSource,
    RealtimeLoopSource,
    VideoFileSource,
)

# Recordings feed the tracker, which makes use of weak detections, so keep them.
RECORD_THRESHOLD = 0.1
PROFILE_HELP = (
    "camera profile JSON: counting lines and zones, as exported from the dashboard "
    "(see trafficpi.core.profile)"
)


def _size(text: str) -> tuple[int, int]:
    try:
        width, height = text.lower().split("x")
        return int(width), int(height)
    except ValueError:
        raise argparse.ArgumentTypeError(f"expected WIDTHxHEIGHT, got {text!r}") from None


def _size_or_auto(text: str) -> tuple[int, int] | None:
    return None if text.lower() == "auto" else _size(text)


def _data_dir() -> Path:
    return default_data_dir()


def _add_camera_args(parser: argparse.ArgumentParser) -> None:
    defaults = CameraSettings()
    group = parser.add_argument_group("camera")
    group.add_argument("--fps", type=float, default=defaults.frame_rate)
    group.add_argument(
        "--sensor-size", type=_size_or_auto, default=defaults.sensor_size,
        help="sensor mode, e.g. 2304x1296 (full field of view); 'auto' lets libcamera choose",
    )  # fmt: skip
    group.add_argument("--main-size", type=_size, default=defaults.main_size)
    group.add_argument("--lores-size", type=_size, default=defaults.lores_size)
    group.add_argument(
        "--lens-position",
        default="0",
        help="dioptres, 0 = infinity (default); 'auto' = continuous autofocus",
    )
    group.add_argument(
        "--exposure-mode", choices=("normal", "short", "long"), default=defaults.exposure_mode
    )
    group.add_argument(
        "--exposure-time-us", type=int, help="fixed exposure (turns auto exposure off)"
    )
    group.add_argument("--gain", type=float, help="fixed analogue gain (turns auto gain off)")
    group.add_argument("--channel-order", choices=("rgb", "bgr"), default=defaults.channel_order)


def _add_detector_args(
    parser: argparse.ArgumentParser, threshold: float = DEFAULT_SCORE_THRESHOLD
) -> None:
    group = parser.add_argument_group("detector")
    group.add_argument("--model", default=DEFAULT_MODEL, help="model in /usr/share/hailo-models")
    group.add_argument("--hef", type=Path, help="explicit HEF path (overrides --model)")
    group.add_argument("--threshold", type=float, default=threshold, help="minimum detection score")
    group.add_argument(
        "--all-classes", action="store_true", help="keep every COCO class, not just traffic"
    )


def _camera_settings(args: argparse.Namespace) -> CameraSettings:
    lens = None if args.lens_position == "auto" else float(args.lens_position)
    return CameraSettings(
        sensor_size=args.sensor_size,
        main_size=args.main_size,
        lores_size=args.lores_size,
        frame_rate=args.fps,
        lens_position=lens,
        exposure_mode=args.exposure_mode,
        exposure_time_us=args.exposure_time_us,
        analogue_gain=args.gain,
        channel_order=args.channel_order,
    )


def _make_detector(
    args: argparse.Namespace, batch_size: int | None = None
) -> tuple[HailoDetector, Path]:
    hef = args.hef or find_hef(args.model)
    try:
        detector = HailoDetector(
            hef,
            score_threshold=args.threshold,
            class_ids=None if args.all_classes else TRAFFIC_CLASS_IDS,
            batch_size=batch_size,
        )
    except Exception as exc:
        # Only one process can use the accelerator at a time.
        raise RuntimeError(
            f"cannot open the Hailo accelerator ({exc}). Is another trafficpi-vision, or any "
            "other program that uses the Hailo device, still running?"
        ) from exc
    return detector, hef


def _flags(value: int | None) -> str:
    if value is None:
        return "n/a"
    now, since = decode_throttled(value)
    parts = []
    if now:
        parts.append("NOW: " + ", ".join(now))
    if since:
        parts.append("since boot: " + ", ".join(since))
    return "; ".join(parts) or "none"


def _print_window(report: pipeline.WindowReport) -> None:
    m, s = report.stage_means_ms, report.system
    temp = f"{s.soc_temp_c:.1f}C" if s.soc_temp_c is not None else "n/a"
    dets = " ".join(f"{k}:{v}" for k, v in report.detections.most_common(4)) or "-"
    print(
        f"t={report.run_elapsed_s:5.0f}s {report.fps:5.1f} fps skip {report.skipped:<3d}"
        f" | wait {m.get(pipeline.CAPTURE, 0):4.1f} infer {m.get(pipeline.INFER, 0):4.1f}"
        f" loop {m.get(pipeline.LOOP, 0):4.1f} latency {m.get(pipeline.LATENCY, 0):5.1f} ms"
        f" | cpu {s.process_cores:3.1f} cores ({s.total_percent:3.0f}%)"
        f" rss {s.rss_mb:4.0f} MB {temp}"
        f" | {dets}",
        flush=True,
    )


def _print_summary(report: pipeline.RunReport) -> None:
    print("\n== summary ==")
    print(
        f"frames: {report.frames} in {report.elapsed_s:.1f} s = {report.fps:.1f} fps; "
        f"skipped (estimated): {report.skipped}"
    )
    print(f"{'stage (ms)':16}{'mean':>8}{'p50':>8}{'p95':>8}{'max':>8}")
    for stage in report.stats.stages():
        x = report.stats.summary(stage)
        print(f"{stage:16}{x.mean:8.1f}{x.p50:8.1f}{x.p95:8.1f}{x.max:8.1f}")
    if report.samples:
        n = len(report.samples)
        temps = [s.soc_temp_c for s in report.samples if s.soc_temp_c is not None]
        print(
            f"cpu: process {sum(s.process_cores for s in report.samples) / n:.2f} cores avg, "
            f"machine {sum(s.total_percent for s in report.samples) / n:.0f}% avg "
            f"(peak {max(s.total_percent for s in report.samples):.0f}%); "
            f"rss peak {max(s.rss_mb for s in report.samples):.0f} MB"
        )
        if temps:
            print(
                f"soc temperature: {temps[0]:.1f} C -> {temps[-1]:.1f} C (max {max(temps):.1f} C)"
            )
        print(f"throttle flags: {_flags(report.samples[-1].throttled)}")
    print(f"detections: {dict(report.detections.most_common()) or 'none'}")


def cmd_dry_run(args: argparse.Namespace) -> int:
    """Run camera and detector for a while and report speed, latency and load. Stores nothing."""
    settings = _camera_settings(args)
    detector, hef = _make_detector(args)
    with detector, PiCameraSource(settings) as camera:
        print(f"model {hef.name}, input {detector.input_size}, camera {camera.describe()}")
        report = pipeline.run(
            camera,
            detector,
            seconds=args.seconds,
            frame_rate=settings.frame_rate,
            on_report=_print_window,
            report_every=args.report_every,
            stop=args.stop,
        )
    _print_summary(report)
    return 0


def _draw(image_rgb: np.ndarray, detections: list[Detection]) -> np.ndarray:
    import cv2

    img = cv2.cvtColor(image_rgb, cv2.COLOR_RGB2BGR)
    h, w = img.shape[:2]
    for d in detections:
        p0, p1 = (round(d.x0 * w), round(d.y0 * h)), (round(d.x1 * w), round(d.y1 * h))
        cv2.rectangle(img, p0, p1, (0, 255, 0), 2)
        cv2.putText(img, f"{d.label} {d.score:.2f}", (p0[0], max(14, p0[1] - 5)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)  # fmt: skip
    return img


def cmd_snapshot(args: argparse.Namespace) -> int:
    """Save one full-size frame with the detections drawn on it."""
    import cv2

    settings = _camera_settings(args)
    detector, hef = _make_detector(args)
    out = args.out or _data_dir() / "snapshots" / f"{datetime.now():%Y%m%d-%H%M%S}.jpg"
    out.parent.mkdir(parents=True, exist_ok=True)
    with detector, PiCameraSource(settings) as camera:
        for _ in range(int(settings.frame_rate)):  # let auto exposure and white balance settle
            camera.read()
        frame = camera.read()
        still = camera.capture_still()
        detections = detector.detect(frame.image)
    cv2.imwrite(str(out), _draw(still, detections), [cv2.IMWRITE_JPEG_QUALITY, 92])
    if args.raw:
        cv2.imwrite(
            str(out.with_name(out.stem + "_raw.jpg")), cv2.cvtColor(still, cv2.COLOR_RGB2BGR)
        )
    print(
        f"{hef.name}, channel order {settings.channel_order}: {len(detections)} detections -> {out}"
    )
    for d in detections:
        print(f"  {d.label:12} {d.score:.2f}  x {d.x0:.3f}-{d.x1:.3f}  y {d.y0:.3f}-{d.y1:.3f}")
    return 0


def cmd_record(args: argparse.Namespace) -> int:
    """Record a clip and the detections for it (test fixtures and offline model comparison)."""
    settings = _camera_settings(args)
    detector, hef = _make_detector(args)
    name = args.name or f"{datetime.now():%Y%m%d-%H%M%S}"
    folder = (args.out_dir or _data_dir() / "clips") / name
    folder.mkdir(parents=True, exist_ok=True)
    with detector, PiCameraSource(settings) as camera:
        header = {
            "started_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "model": hef.stem,
            "hef": str(hef),
            "score_threshold": args.threshold,
            "classes": None if args.all_classes else sorted(TRAFFIC_CLASS_IDS),
            "frame_size": list(camera.frame_size),
            "camera_settings": asdict(settings),
            "camera": camera.describe(),
            "clip": None if args.no_video else "clip.mp4",
        }
        if not args.no_video:
            camera.start_recording(folder / "clip.mp4", bitrate=args.bitrate)
        with DetectionLogWriter(folder / "detections.jsonl", header) as log:
            report = pipeline.run(
                camera,
                detector,
                seconds=args.seconds,
                frame_rate=settings.frame_rate,
                on_frame=lambda frame, dets: log.write_frame(frame, dets),
                on_report=_print_window,
                report_every=5.0,
                stop=args.stop,
            )
    _print_summary(report)
    for f in sorted(folder.iterdir()):
        print(f"  {f}  ({f.stat().st_size / 1e6:.1f} MB)")
    return 0


def cmd_redetect(args: argparse.Namespace) -> int:
    """Run an accelerator over a recorded clip and write a detection log (to compare models)."""
    detector, hef = _make_detector(args, batch_size=OFFLINE_BATCH)
    source = VideoFileSource(args.clip, size=args.lores_size, channel_order=args.channel_order)
    out = args.out or args.clip.parent / f"detections-{hef.stem}.jsonl"
    header = {
        "started_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "model": hef.stem,
        "hef": str(hef),
        "score_threshold": args.threshold,
        "classes": None if args.all_classes else sorted(TRAFFIC_CLASS_IDS),
        "frame_size": list(args.lores_size),
        "channel_order": args.channel_order,
        "source_clip": str(args.clip),
    }
    with detector, source, DetectionLogWriter(out, header) as log:
        report = pipeline.run_ahead(
            source,
            detector,
            seconds=args.seconds,
            on_frame=log.write_frame,
            on_report=_print_window,
            report_every=5.0,
            stop=args.stop,
        )
    _print_summary(report)
    print(f"wrote {out}")
    return 0


def _load_profile(path: Path | None) -> CompiledProfile:
    """The profile in a JSON file (see ``trafficpi.core.profile``), or an empty one."""
    return load_compiled_profile(path) if path else CompiledProfile([], None, [], None)


def _print_stops(replayed) -> None:  # type: ignore[no-untyped-def]
    """What each vehicle did at each stop sign: the outcome and the numbers behind it."""
    t0 = replayed.timestamps_ns[0] if replayed.timestamps_ns else 0
    print(
        "stop signs (time since the first frame; still = the longest wait, slow = the slowest and"
    )
    print(
        "fast = the fastest it went, in the vehicle's own sizes a second, watched = how long it was"
    )
    print("followed in the zone; with a calibration, also the slowest and fastest speed measured")
    print("there and the last one before the line, in metres a second):")
    for e in replayed.stops:
        seconds = (e.timestamp_ns - t0) / 1e9
        metres = (
            f"  {e.min_speed_ms:5.1f} to {e.max_speed_ms:5.1f} m/s, "
            f"{e.line_speed_ms:5.1f} at the line"
            if e.max_speed_ms >= 0
            else ""
        )
        print(
            f"  {seconds:8.2f} s  {e.sign_id:16} {e.outcome:13} {e.label:10} track {e.track_id}"
            f"  still {e.hold_s:4.1f} s  slow {e.min_speed:5.2f}  fast {e.max_speed:5.2f}"
            f"  watched {e.observed_s:4.1f} s{metres}"
        )


def _print_replay(path: Path, replayed, lines) -> None:  # type: ignore[no-untyped-def]
    header = replayed.header
    print(
        f"replay of {path}: model {header.get('model', '?')}, {len(replayed.results)} frames, "
        f"{replayed.duration_s:.1f} s, {len(replayed.confirmed_tracks)} confirmed tracks"
    )
    if not lines:
        print("no counting lines given (use --profile)")
        _print_stops(replayed)
        return
    print("counts (+1 is left to right of a -> b on screen, -1 the other way):")
    for cfg in lines:
        label = f"{cfg.id} ({cfg.name})" if cfg.name else cfg.id
        for direction in (1, -1):
            by_class = {
                name: n
                for (line_id, d, name), n in sorted(replayed.totals.counts.items())
                if line_id == cfg.id and d == direction
            }
            total = replayed.totals.total(cfg.id, direction)
            detail = ", ".join(f"{name} {n}" for name, n in by_class.items()) or "-"
            print(f"  {label:32} {direction:+d}: {total:4d}   {detail}")
    t0 = replayed.timestamps_ns[0] if replayed.timestamps_ns else 0
    print("events (time since the first frame):")
    for e in replayed.events:
        seconds = (e.timestamp_ns - t0) / 1e9
        speed = f"  {e.speed_ms:5.1f} m/s" if e.speed_ms is not None else ""
        print(
            f"  {seconds:8.2f} s  {e.line_id:16} {e.direction:+d}  {e.label:10} track {e.track_id}"
            f"{speed}"
        )
    _print_stops(replayed)


def cmd_replay(args: argparse.Namespace) -> int:
    """Run the tracker, zones and counting lines over a recorded detection log."""
    profile = _load_profile(args.profile)
    lines = profile.counting_lines
    replayed = replay_log(
        args.log,
        lines,
        detection_filter=profile.detection_filter,
        stop_signs=profile.stop_signs,
        calibration=profile.calibration,
    )
    _print_replay(args.log, replayed, lines)
    if args.events_json:
        t0 = replayed.timestamps_ns[0] if replayed.timestamps_ns else 0
        records = [
            {
                "t_s": round((e.timestamp_ns - t0) / 1e9, 3),
                "line": e.line_id,
                "direction": e.direction,
                "class": e.label,
                "track": e.track_id,
                "speed_ms": e.speed_ms,
            }
            for e in replayed.events
        ]
        args.events_json.write_text(json.dumps(records, indent=1) + "\n", encoding="utf-8")
        print(f"wrote {args.events_json}")
    return 0


def cmd_compare(args: argparse.Namespace) -> int:
    """Compare two detection logs of the same footage, frame by frame."""
    print(format_comparison(compare_logs(args.a, args.b, min_score=args.min_score)))
    return 0


def cmd_annotate(args: argparse.Namespace) -> int:
    """Draw tracks and counts onto a recorded clip, with a still at every count event."""
    folder: Path = args.folder
    profile = _load_profile(args.profile)
    report = annotate_clip(
        args.clip or folder / "clip.mp4",
        args.log or folder / "detections.jsonl",
        profile.counting_lines,
        detection_filter=profile.detection_filter,
        video_out=args.video or folder / "annotated.mp4",
        stills_dir=args.stills or folder / "event-stills",
        stop_signs=profile.stop_signs,
        calibration=profile.calibration,
    )
    print(
        f"{report.frames_written} frames written, {len(report.events)} count events, "
        f"{len(report.stops)} stop events"
    )
    for still in report.stills:
        print(f"  {still}")
    return 0


def _configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        # systemd's journal adds the time; a terminal needs it.
        format=("%(asctime)s " if sys.stderr.isatty() else "")
        + "%(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )
    logging.getLogger("picamera2").setLevel(logging.WARNING)  # it logs every (re)configuration
    logging.getLogger("alembic").setLevel(logging.WARNING)  # and so does the migration runner


def _seed_demo_history(store: Store, days: int, log: logging.Logger) -> None:
    """Give a demo database that has no trails yet some made-up ones for the days before today,
    and one that has no health record a few days of that."""
    now_ms = time.time_ns() // 1_000_000
    if store.health_count() == 0:
        hours = min(days, 3) * 24
        store.add_health(demo_health(hours, now_ms=now_ms))
        log.warning("DEMO: wrote %d hours of made-up machine health", hours)
    if store.trail_count() > 0:
        return
    trails = demo_history(days, now_ms=now_ms, tz=resolve_timezone(store.get_setting("timezone")))
    for start in range(0, len(trails), 2_000):
        store.add_trails(trails[start : start + 2_000])
    log.warning("DEMO: wrote %d made-up trails for the %d days before today", len(trails), days)


def cmd_run(args: argparse.Namespace) -> int:
    """The vision service: count traffic into the database and serve the live view locally."""
    _configure_logging()
    log = logging.getLogger("trafficpi.vision")
    data_dir = args.data_dir or _data_dir()
    if (args.replay or args.demo) and args.data_dir is None:
        # Replayed or made-up traffic must not mix with the real counts.
        data_dir = data_dir / ("demo" if args.demo else "replay")
    store = open_store(data_dir)
    snapshot = None  # the live view serves its latest frame unless the camera can do better
    if args.demo:
        street = DemoStreet()
        detector: Detector = street
        model = "demo"
        service = VisionService(street, street, store, data_dir=data_dir)
        log.warning(
            "DEMO: a made-up street instead of the camera; counts go to %s",
            data_dir / "trafficpi.db",
        )
        if args.history_days:
            _seed_demo_history(store, args.history_days, log)
    else:
        settings = _camera_settings(args)
        detector, hef = _make_detector(args)
        model = hef.stem
        if args.replay:
            video = VideoFileSource(
                args.replay, size=settings.lores_size, channel_order=settings.channel_order
            )
            service = VisionService(
                RealtimeLoopSource(video),
                detector,
                store,
                data_dir=data_dir,
                timestamp_ms=lambda _frame: time.time_ns() // 1_000_000,  # the clip has no clock
            )
            log.warning(
                "REPLAY: playing %s in a loop instead of the camera; counts go to %s",
                args.replay,
                data_dir / "trafficpi.db",
            )
        else:
            camera = PiCameraSource(settings)
            service = VisionService(camera, detector, store, data_dir=data_dir)
            snapshot = camera.capture_still
    service.live.set_detector(model)
    # What the status page says about the chip: its own identity when there is one.
    service.live.set_accelerator(
        {"architecture": "simulated", "model": model}
        if (args.demo or args.replay)
        else {**hailo_identity(), "model": model}
    )
    server = ServerThread(
        create_server_app(service, snapshot=snapshot), host=args.host, port=args.port
    )
    # Under systemd (Type=notify, WatchdogSec=) the service says when it is up and, while its
    # camera loop moves, that it is alive; a loop stuck in a call that never returns stops the
    # messages and systemd restarts the service. By hand none of this sends anything.
    period = watchdog_period()
    heartbeat = Heartbeat(
        service.alive,
        period=period,
        status=service.status_line,
        tick=min(1.0, period / 4) if period else 1.0,
    )
    try:
        server.start()
        log.info(
            "vision service up: model %s, data in %s, live view on http://%s:%d",
            model,
            data_dir.resolve(),
            args.host,
            args.port,
        )
        notify("READY=1")
        heartbeat.start()
        _end_streams_when_stopping(args.stop, server)
        service.run(args.stop)
    finally:
        heartbeat.stop()
        server.stop()
        store.close()
        detector.close()  # for the accelerator this only lets go of our reference (see its close)
    log.info("vision service stopped after %d frames", service.frames)
    return 0


def _end_streams_when_stopping(stop: StopRequest, server: ServerThread) -> None:
    """End the viewers' streams and sockets as soon as a stop is asked for, not when the whole
    shutdown is done: the web server is stopped last, after the camera and the evidence, and a
    viewer still connected then is cancelled by the server's grace period, with an ERROR in the
    journal (seen once, 2026-10-09 17:01)."""

    def wait_then_end() -> None:
        stop.wait(10**9)
        server.begin_stopping()

    threading.Thread(target=wait_then_end, name="end-streams", daemon=True).start()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="trafficpi-vision", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    dry = sub.add_parser("dry-run", help="measure speed, latency and load; stores nothing")
    _add_camera_args(dry)
    _add_detector_args(dry)
    dry.add_argument("--seconds", type=float, default=30)
    dry.add_argument("--report-every", type=float, default=5)
    dry.set_defaults(func=cmd_dry_run)

    run = sub.add_parser("run", help="the vision service: count traffic, serve the live view")
    _add_camera_args(run)
    _add_detector_args(run)
    run.add_argument("--data-dir", type=Path, help=DATA_DIR_HELP)
    run.add_argument(
        "--host",
        default="127.0.0.1",
        help="address of the live-view server (default: this machine only; the API relays it)",
    )
    run.add_argument("--port", type=int, default=8001)
    source = run.add_mutually_exclusive_group()
    source.add_argument(
        "--replay",
        type=Path,
        metavar="CLIP",
        help="play a recorded clip in a loop instead of using the camera, for developing without "
        "traffic; counts go to <data-dir>/replay unless --data-dir is given",
    )
    source.add_argument(
        "--demo",
        action="store_true",
        help="a made-up street with cars, trucks and bicycles instead of the camera and the "
        "accelerator, neither of which is needed; counts go to <data-dir>/demo unless "
        "--data-dir is given",
    )
    run.add_argument(
        "--history-days",
        type=int,
        default=0,
        metavar="N",
        help="with --demo: if the demo has no trails yet, make up some for each of the N days "
        "before today, so that the Data page's Trails card has something to show at once",
    )
    run.set_defaults(func=cmd_run)

    snap = sub.add_parser("snapshot", help="save one frame with detections drawn on it")
    _add_camera_args(snap)
    _add_detector_args(snap)
    snap.add_argument("--out", type=Path, help="output JPEG (default: data/snapshots/<time>.jpg)")
    snap.add_argument("--raw", action="store_true", help="also save the frame without boxes")
    snap.set_defaults(func=cmd_snapshot)

    rec = sub.add_parser("record", help="record a clip and its detections")
    _add_camera_args(rec)
    _add_detector_args(rec, threshold=RECORD_THRESHOLD)
    rec.add_argument("--seconds", type=float, default=60)
    rec.add_argument("--name", help="folder name (default: the start time)")
    rec.add_argument("--out-dir", type=Path, help="parent folder (default: data/clips)")
    rec.add_argument("--no-video", action="store_true", help="log detections only; no clip")
    rec.add_argument(
        "--bitrate", type=int, default=4_000_000, help="clip bitrate in bits per second"
    )
    rec.set_defaults(func=cmd_record)

    redet = sub.add_parser("redetect", help="run the accelerator over a recorded clip")
    _add_detector_args(redet, threshold=RECORD_THRESHOLD)
    redet.add_argument("clip", type=Path)
    redet.add_argument("--out", type=Path, help="detection log (default: next to the clip)")
    redet.add_argument("--seconds", type=float, default=float("inf"), help="stop after this long")
    redet.add_argument("--lores-size", type=_size, default=CameraSettings().lores_size)
    redet.add_argument("--channel-order", choices=("rgb", "bgr"), default="rgb")
    redet.set_defaults(func=cmd_redetect)

    rep = sub.add_parser("replay", help="count over a recorded detection log (no hardware needed)")
    rep.add_argument("log", type=Path, help="a detections.jsonl file")
    rep.add_argument("--profile", "--lines", type=Path, help=PROFILE_HELP)
    rep.add_argument("--events-json", type=Path, help="also write the count events as JSON")
    rep.set_defaults(func=cmd_replay)

    cmp = sub.add_parser("compare", help="compare two detection logs of the same footage")
    cmp.add_argument("a", type=Path)
    cmp.add_argument("b", type=Path)
    cmp.add_argument("--min-score", type=float, default=0.25)
    cmp.set_defaults(func=cmd_compare)

    ann = sub.add_parser("annotate", help="draw tracks and counts onto a recorded clip")
    ann.add_argument(
        "folder", type=Path, help="a folder made by `record` (clip.mp4, detections.jsonl)"
    )
    ann.add_argument("--profile", "--lines", type=Path, help=PROFILE_HELP)
    ann.add_argument("--clip", type=Path)
    ann.add_argument("--log", type=Path)
    ann.add_argument("--video", type=Path, help="annotated video (default: <folder>/annotated.mp4)")
    ann.add_argument(
        "--stills", type=Path, help="event stills folder (default: <folder>/event-stills)"
    )
    ann.set_defaults(func=cmd_annotate)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    args.stop = StopRequest()
    args.stop.install()
    try:
        return args.func(args)
    except (RuntimeError, ProfileError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


def entry() -> NoReturn:
    """The console script: run :func:`main`, then leave without HailoRT's native exit code.

    HailoRT 4.23.0 on this platform crashes while the process exits (a jump into unmapped memory
    inside libhailort, reproduced after the live-view server had run; see Troubleshooting in
    docs/dev-setup.md). Everything we own has been flushed and closed by then, so exit directly and
    let the operating system clean up, which is what ``HailoDetector.close`` already relies on.
    """
    try:
        code = main()
    except SystemExit as exc:  # argparse: --help, or a usage error
        code = exc.code if isinstance(exc.code, int) else int(exc.code is not None)
    except BaseException:
        traceback.print_exc()
        code = 1
    logging.shutdown()
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(code)


if __name__ == "__main__":
    entry()
