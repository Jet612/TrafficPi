#!/usr/bin/env python3
"""Record a running made-up street for the static demo of the dashboard.

    cd backend && E2E_PASSWORD=... uv run python ../scripts/record_demo.py \\
        --api http://127.0.0.1:18100 --out /tmp/demo-site [--set-profile] [--warmup 600] [--loop 45]

It writes what the static demo answers from, as files to put at the root of the built site:
``demo/recording.json`` (the API's answers, every pass at a stop sign), ``demo/live.json`` and
``demo/street.mp4`` (one loop of the live view: the messages and the picture),
``demo/trails/*.json`` (the paths, one file a day), and under ``api/`` the pictures, clips and CSV
files the dashboard links to, at the paths the API serves them from, so that a static host serves
them as they are.

It refuses a service that is not the made-up street (``trafficpi-vision run --demo``): a recording
is published, and a real street must never be in it. ``--set-profile`` saves a profile with a
counting line, a stop sign and a detection zone first; ``--warmup`` then waits for counts and passes
to build up. ``scripts/build_static_demo.sh`` starts a street of its own and runs this.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlsplit

import httpx
import websockets

RECORDING_FORMAT = 1  # RECORDING_FORMAT in dashboard/src/demo/recording.ts
LIVE_PER_FRAME = ("frame", "uptime_s", "fps", "tracks", "events")
DATA_DAYS = (1, 7, 30, 90, 0)  # DATA_PERIODS in dashboard/src/lib/data.ts
HEALTH_HOURS = (1, 6, 24, 168, 720)  # HEALTH_PERIODS in dashboard/src/lib/health.ts
EXPORT_KINDS = ("counts", "passes")  # ExportKind in dashboard/src/lib/export.ts
TRAILS_PER_DAY = 2500  # an even sample of each day's paths; the demo scales the totals back up
VIDEO_FPS = 10  # the rate the live view's preview stream is sent at

# The made-up street: the near lane drives left to right with the bottoms of its vehicles at 0.80 of
# the picture's height, the far lane right to left at 0.65 (trafficpi/vision/demo.py).
_CLASSES = ["car", "truck", "bus", "motorcycle"]
DEMO_PROFILE: dict[str, Any] = {
    "schema_version": 3,
    "lines": [
        {
            "id": "main",
            "name": "Main Street",
            "a": [0.72, 0.5],
            "b": [0.72, 0.95],
            "classes": [*_CLASSES, "bicycle"],
            "point": "ground",
            "margin": None,
            "persistence": None,
        }
    ],
    "detection_zones": [
        {"id": "road", "name": "The road", "points": [[0, 0.42], [1, 0.42], [1, 1], [0, 1]]}
    ],
    "exclusion_zones": [],
    "stop_signs": [
        {
            "id": "elm",
            "name": "Elm Street",
            "approach": [[0.15, 0.55], [0.5, 0.55], [0.5, 0.9], [0.15, 0.9]],
            "a": [0.5, 0.94],
            "b": [0.5, 0.5],
            "direction": 1,
            "classes": _CLASSES,
            "hold_s": 1.0,
            "still_radius": 0.08,
            "roll_speed": 0.7,
            "min_observed_s": 0.8,
        }
    ],
    "calibration": None,
}


def request_key(path: str, params: dict[str, Any] | None = None) -> str:
    """``requestKey`` in dashboard/src/demo/recording.ts: the path, then the sorted query."""
    if not params:
        return path
    pairs = sorted((str(k), str(v)) for k, v in params.items())
    return f"{path}?{urlencode(pairs)}"


def say(text: str) -> None:
    print(text, flush=True)


class Recorder:
    def __init__(self, api: str, password: str, username: str, out: Path) -> None:
        self.api = api.rstrip("/")
        self.out = out
        self.client = httpx.Client(base_url=self.api, timeout=60)
        response = self.client.post(
            "/api/auth/login", json={"username": username, "password": password}
        )
        if response.status_code != 204:
            raise SystemExit(f"cannot sign in: {response.status_code} {response.text}")
        self.answers: dict[str, Any] = {}

    def get(self, path: str, params: dict[str, Any] | None = None, *, keep: bool = True) -> Any:
        response = self.client.get(path, params=params)
        response.raise_for_status()
        body = response.json()
        if keep:
            self.answers[request_key(path, params)] = body
        return body

    def save_file(self, url: str, params: dict[str, Any] | None = None) -> bool:
        """Fetch a file the dashboard links to and keep it at the same path under ``out``."""
        response = self.client.get(url, params=params)
        if response.status_code != 200:
            return False
        target = self.out / urlsplit(url).path.lstrip("/")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(response.content)
        return True

    # --- safety -----------------------------------------------------------------------------

    def first_live_message(self) -> dict[str, Any]:
        async def read() -> dict[str, Any]:
            async with websockets.connect(self.ws_url(), additional_headers=self.cookie()) as ws:
                while True:
                    message = json.loads(await asyncio.wait_for(ws.recv(), 10))
                    if message.get("type") == "state":
                        return message

        return asyncio.run(read())

    def check_made_up_street(self) -> None:
        detector = self.first_live_message().get("detector")
        if detector != "demo":
            raise SystemExit(
                f"the vision service runs the detector {detector!r}, not the made-up street: "
                "a recording is published, so only `trafficpi-vision run --demo` may be recorded"
            )

    def ws_url(self) -> str:
        parts = urlsplit(self.api)
        scheme = "wss" if parts.scheme == "https" else "ws"
        return f"{scheme}://{parts.netloc}/api/live"

    def cookie(self) -> dict[str, str]:
        return {"Cookie": "; ".join(f"{k}={v}" for k, v in self.client.cookies.items())}

    # --- the live loop ------------------------------------------------------------------------

    def record_live(
        self, seconds: float
    ) -> tuple[int, list[tuple[float, bytes]], list[tuple[float, dict[str, Any]]]]:
        """The preview stream's pictures and the live socket's messages over ``seconds``, each
        with when it arrived (seconds on one clock), and the wall time that clock started at."""
        frames: list[tuple[float, bytes]] = []
        messages: list[tuple[float, dict[str, Any]]] = []
        start = time.monotonic()
        started_at = time.time_ns() // 1_000_000

        async def socket() -> None:
            async with websockets.connect(self.ws_url(), additional_headers=self.cookie()) as ws:
                while time.monotonic() - start < seconds:
                    text = await asyncio.wait_for(ws.recv(), 10)
                    message = json.loads(text)
                    if message.get("type") == "state":
                        messages.append((time.monotonic() - start, message))

        async def stream() -> None:
            async with (
                httpx.AsyncClient(
                    base_url=self.api, cookies=self.client.cookies, timeout=30
                ) as client,
                client.stream("GET", "/api/preview.mjpg") as response,
            ):
                response.raise_for_status()
                buffer = b""
                async for chunk in response.aiter_bytes():
                    buffer += chunk
                    while True:
                        head_end = buffer.find(b"\r\n\r\n")
                        if head_end < 0:
                            break
                        length = next(
                            (
                                int(line.split(b":", 1)[1])
                                for line in buffer[:head_end].split(b"\r\n")
                                if line.lower().startswith(b"content-length:")
                            ),
                            None,
                        )
                        if length is None:
                            raise SystemExit("a part of the preview stream has no Content-Length")
                        body_start = head_end + 4
                        if len(buffer) < body_start + length:
                            break
                        frames.append(
                            (time.monotonic() - start, buffer[body_start : body_start + length])
                        )
                        buffer = buffer[body_start + length :].lstrip(b"\r\n")
                    if time.monotonic() - start >= seconds:
                        return

        async def both() -> None:
            await asyncio.gather(socket(), stream())

        asyncio.run(both())
        return started_at, frames, messages

    def write_live(self, seconds: float) -> int:
        """Record one loop of the live view; returns the wall time its first picture arrived."""
        started_at, frames, messages = self.record_live(seconds + 1.0)
        if len(frames) < seconds * VIDEO_FPS / 2:
            raise SystemExit(f"only {len(frames)} pictures came in {seconds:.0f} s")
        first = frames[0][0]
        frames = [(t - first, image) for t, image in frames if t - first < seconds]
        demo = self.out / "demo"
        demo.mkdir(parents=True, exist_ok=True)
        video = demo / "street.mp4"
        with tempfile.TemporaryDirectory() as work:
            listing = []
            for index, (t, image) in enumerate(frames):
                name = f"{index:05d}.jpg"
                (Path(work) / name).write_bytes(image)
                end = frames[index + 1][0] if index + 1 < len(frames) else seconds
                listing.append(f"file '{name}'\nduration {max(end - t, 0.001):.4f}\n")
            # The concat demuxer needs the last picture named again for its duration to count.
            listing.append(f"file '{len(frames) - 1:05d}.jpg'\n")
            (Path(work) / "list.txt").write_text("".join(listing))
            subprocess.run(
                [
                    "ffmpeg", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0",
                    "-i", str(Path(work) / "list.txt"), "-vf", f"fps={VIDEO_FPS},format=yuv420p",
                    "-t", f"{seconds:.3f}", "-c:v", "libx264", "-preset", "slow", "-crf", "28",
                    "-g", str(VIDEO_FPS), "-movflags", "+faststart", "-an", str(video),
                ],
                check=True,
            )  # fmt: skip
        duration = float(
            subprocess.run(
                [
                    "ffprobe", "-v", "error", "-show_entries", "format=duration",
                    "-of", "default=noprint_wrappers=1:nokey=1", str(video),
                ],
                check=True, capture_output=True, text=True,
            ).stdout.strip()
        )  # fmt: skip

        # Each frame's first message (the same frame is sent again to keep the link alive), placed
        # by its frame number: the street makes frames at a steady rate, and a message arrives up
        # to a tenth of a second after its frame (the socket is polled ten times a second), so the
        # time it arrived says less about when its frame was than the number does. The numbers are
        # tied to the pictures' clock by the message that arrived soonest after its frame.
        unique: dict[int, tuple[float, dict[str, Any]]] = {}
        for t, message in messages:
            unique.setdefault(message["frame"], (t - first, message))
        timeline = sorted(unique.items())
        if len(timeline) < 2:
            raise SystemExit("too few live messages came during the loop")
        (f0, (a0, _)), (f1, (a1, _)) = timeline[0], timeline[-1]
        rate = (f1 - f0) / (a1 - a0)
        offset = min(arrived - (frame - f0) / rate for frame, (arrived, _) in timeline)

        loop: list[dict[str, Any]] = []
        base: dict[str, Any] | None = None
        for frame, (_, message) in timeline:
            t = (frame - f0) / rate + offset
            if not 0 <= t < duration:
                continue
            if base is None:
                base = {
                    k: v
                    for k, v in message.items()
                    if k not in LIVE_PER_FRAME and k not in ("type", "updated_ms")
                }
            loop.append({"t": round(t, 3), **{k: message[k] for k in LIVE_PER_FRAME}})
        if base is None:
            raise SystemExit("no live message came during the loop")
        live = {"duration_s": duration, "base": base, "frames": loop}
        (demo / "live.json").write_text(json.dumps(live, separators=(",", ":")))
        say(f"live loop: {len(frames)} pictures and {len(loop)} messages over {duration:.1f} s, "
            f"{video.stat().st_size // 1024} KiB of video")  # fmt: skip
        return started_at + round(first * 1000)

    # --- the rest of the API ------------------------------------------------------------------

    def record_api(self, max_evidence: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        for path in (
            "/api/auth/state",
            "/api/status",
            "/api/stats/today",
            "/api/profile",
            "/api/settings",
            "/api/stops/hours",
            "/api/mail",
            "/api/mail/preview",
        ):
            self.get(path)
        self.get("/api/events", {"limit": 10, "type": "count"})
        signs = [sign["id"] for sign in self.answers["/api/profile"]["profile"]["stop_signs"]]
        for days in DATA_DAYS:
            self.get("/api/stats/stops", {"days": days})
            for sign in signs:
                self.get("/api/stats/stops", {"days": days, "sign": sign})
            self.get("/api/stats/traffic", {"days": days})
            self.get("/api/stats/speeds", {"days": days})
        for hours in HEALTH_HOURS:
            self.get("/api/system/history", {"hours": hours})

        stops = self.record_stops(max_evidence)
        trail_days = self.record_trails()
        if self.answers["/api/profile"].get("reference_at") is not None:
            self.save_file("/api/profile/reference.jpg")
        self.save_file("/api/snapshot.jpg")
        for kind in EXPORT_KINDS:
            self.save_file(f"/api/export/{kind}.csv", {"days": 0})
        return stops, trail_days

    def record_stops(self, max_evidence: int) -> list[dict[str, Any]]:
        """Every pass, newest first, with the pictures and clips of the newest ``max_evidence``."""
        now_ms = time.time_ns() // 1_000_000
        stops: list[dict[str, Any]] = []
        before: int | None = None
        while True:
            params: dict[str, Any] = {
                "from_ms": now_ms - 360 * 86_400_000,
                "to_ms": now_ms + 3_600_000,
                "limit": 500,
            }
            if before is not None:
                params["before_id"] = before
            page = self.get("/api/stops", params, keep=False)
            stops.extend(page)
            if len(page) < 500:
                break
            before = page[-1]["id"]
        kept = 0
        for stop in stops:
            has = stop["snapshot_url"] is not None or stop["clip_url"] is not None
            if has and kept < max_evidence:
                for key in ("snapshot_url", "clip_url"):
                    if stop[key] is not None and not self.save_file(stop[key]):
                        stop[key] = None
                kept += 1
            else:
                stop["snapshot_url"] = stop["clip_url"] = None
        say(f"stop signs: {len(stops)} passes, evidence for {kept}")
        return stops

    def record_trails(self) -> list[dict[str, Any]]:
        today = self.get("/api/trails/day")
        first, last = today.get("first_day"), today.get("last_day")
        days: list[dict[str, Any]] = []
        if first is None or last is None:
            return days
        day = first
        while day <= last:
            by_hour = self.get("/api/trails/day", {"day": day})
            hours = by_hour["hours"]
            if hours and by_hour["total"] > 0:
                from_ms, to_ms = hours[0]["t"], hours[-1]["end"]
                paths = self.get(
                    "/api/trails",
                    {"from_ms": from_ms, "to_ms": to_ms, "limit": TRAILS_PER_DAY},
                    keep=False,
                )
                url = f"/demo/trails/{day}.json"
                target = self.out / url.lstrip("/")
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(json.dumps(paths, separators=(",", ":")))
                days.append({"day": day, "from_ms": from_ms, "to_ms": to_ms, "url": url})
            day = _next_day(day)
        say(f"trails: {len(days)} days")
        return days


def _next_day(day: str) -> str:
    from datetime import date, timedelta

    return (date.fromisoformat(day) + timedelta(days=1)).isoformat()


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--api", required=True, help="the made-up street's API, e.g. http://127.0.0.1:18100"
    )
    parser.add_argument(
        "--out", required=True, type=Path, help="the folder to write into (the root of the site)"
    )
    parser.add_argument("--username", default="admin")
    parser.add_argument(
        "--set-profile", action="store_true", help="save the demo's lines, zone and stop sign first"
    )
    parser.add_argument(
        "--warmup", type=float, default=0, help="seconds to wait for counts and passes first"
    )
    parser.add_argument(
        "--loop", type=float, default=45, help="seconds of live view to loop (default 45)"
    )
    parser.add_argument(
        "--max-evidence", type=int, default=40, help="passes to keep pictures and clips of"
    )
    args = parser.parse_args()
    password = os.environ.get("E2E_PASSWORD")
    if not password:
        parser.error("set E2E_PASSWORD to the street's password")
    for tool in ("ffmpeg", "ffprobe"):
        if shutil.which(tool) is None:
            parser.error(f"{tool} is needed")

    recorder = Recorder(args.api, password, args.username, args.out)
    recorder.check_made_up_street()
    if args.set_profile:
        response = recorder.client.put("/api/profile", json=DEMO_PROFILE)
        response.raise_for_status()
        say(f"profile saved as version {response.json()['version']}")
        # The zone editor draws on the reference picture, which the demo cannot take itself.
        recorder.client.post("/api/profile/reference").raise_for_status()
    if args.warmup > 0:
        say(f"waiting {args.warmup:.0f} s for counts and passes")
        time.sleep(args.warmup)

    recorded_at = recorder.write_live(args.loop)
    stops, trail_days = recorder.record_api(args.max_evidence)
    recording = {
        "format": RECORDING_FORMAT,
        "recorded_at": recorded_at,
        "get": recorder.answers,
        "stops": stops,
        "trail_days": trail_days,
    }
    (args.out / "demo" / "recording.json").write_text(json.dumps(recording, separators=(",", ":")))
    (args.out / "demo" / "recorded_at").write_text(f"{recorded_at}\n")
    say(f"recorded at {recorded_at} into {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
