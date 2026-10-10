"""Evidence: a picture and a short clip of the stop-sign passes that need a look (plan.md D8, D24).

The service hands every frame to an :class:`EvidenceRecorder`, which keeps the last dozen seconds
as small JPEGs in memory (and nowhere else: nothing is written to disk until something happens).
When a pass that needs a look is recorded, the recorder waits for the few seconds after it, then
writes a picture of the moment and a clip around it into a folder under the data directory, with
the vehicle in question boxed and the stop sign drawn. It is for a person to look at and decide:
the picture is as small as the detector's own (640 by 360), so a number plate or a face is not
readable in it, which is as the project wants it.

All the work happens on a thread of its own. The camera loop only passes frames in, asks for
evidence when there is an event, and picks up the names of the files that have been written.
"""

from __future__ import annotations

import contextlib
import logging
import math
import queue
import re
import shutil
import subprocess
import tempfile
import threading
import time
from collections import deque
from collections.abc import Callable, Collection, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from trafficpi.core.evidence_settings import MIN_FREE_BYTES
from trafficpi.core.paths import evidence_dir
from trafficpi.vision.annotate import draw
from trafficpi.vision.engine import CountTotals
from trafficpi.vision.rules import StopSign
from trafficpi.vision.tracker import TrackUpdate

log = logging.getLogger("trafficpi.vision")

RING_SECONDS = 12.0  # how much is kept in memory: more than the clip needs
BEFORE_S = 6.0  # the clip starts this long before the vehicle crossed the stop line
AFTER_S = 3.0  # and ends this long after it
CLIP_FPS = 15.0  # half the camera's rate: enough to see a vehicle stop, and half the work
JPEG_QUALITY = 80  # of the frames in memory
SNAPSHOT_QUALITY = 90
MAX_WAITING_FRAMES = 30  # two seconds at the clip's rate; beyond that frames are dropped
GIVE_UP_AFTER_S = AFTER_S + 5.0  # wall-clock seconds: a clip is finished with what there is by then
STALE_PART_S = 600.0  # a half-written file this old is a leftover

# Frames in, a clip out: True if it was written. Replaceable, so that tests need no encoder.
ClipEncoder = Callable[[Iterable[np.ndarray], float, Path], bool]

SPACE_WARNING_EVERY_S = 3600.0  # the log says once an hour that the disk is too full for evidence


def free_bytes(path: Path) -> int:
    """How many bytes are free on the disk that holds ``path`` (or the nearest folder above it that
    exists: the evidence folder is made when the first evidence is written)."""
    probe = path
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    return shutil.disk_usage(probe).free


# --- the frames in memory -------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RingFrame:
    t_ns: int  # camera time
    jpeg: bytes  # BGR, compressed
    tracks: tuple[TrackUpdate, ...]  # what the tracker had in this frame, to box the vehicle in


class FrameRing:
    """The newest few seconds of frames. Only the recorder's own thread touches it."""

    def __init__(self, seconds: float = RING_SECONDS) -> None:
        self._span_ns = round(seconds * 1e9)
        self._frames: deque[RingFrame] = deque()

    def add(self, frame: RingFrame) -> None:
        self._frames.append(frame)
        while self._frames and frame.t_ns - self._frames[0].t_ns > self._span_ns:
            self._frames.popleft()

    def window(self, start_ns: int, end_ns: int) -> list[RingFrame]:
        """The frames from ``start_ns`` to ``end_ns``, both included, oldest first."""
        return [f for f in self._frames if start_ns <= f.t_ns <= end_ns]

    @property
    def newest_ns(self) -> int | None:
        return self._frames[-1].t_ns if self._frames else None

    def __len__(self) -> int:
        return len(self._frames)


# --- jobs and results -----------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class EvidenceJob:
    event_id: int
    track_id: int
    at_ns: int  # camera time at which the vehicle crossed the line
    caption: str  # written over the picture: where, what and when
    sign: StopSign | None  # drawn on it
    stem: str  # where the files go, relative to the evidence folder and without an extension
    deadline: float  # time.monotonic(): by then the job is finished with whatever the ring has


@dataclass(frozen=True, slots=True)
class EvidenceFiles:
    """What was written for one event: paths relative to the data directory, None if not made."""

    event_id: int
    snapshot: str | None
    clip: str | None


# --- the recorder ---------------------------------------------------------------------------------


class EvidenceRecorder:
    def __init__(
        self,
        data_dir: Path,
        *,
        encoder: ClipEncoder | None = None,
        threaded: bool = True,
        before_s: float = BEFORE_S,
        after_s: float = AFTER_S,
        fps: float = CLIP_FPS,
        ring_seconds: float = RING_SECONDS,
        max_waiting: int = MAX_WAITING_FRAMES,
        monotonic: Callable[[], float] = time.monotonic,
        min_free_bytes: int = MIN_FREE_BYTES,
        free_of: Callable[[Path], int] = free_bytes,
    ) -> None:
        self.data_dir = data_dir
        self.folder = evidence_dir(data_dir)
        self._encoder = encoder or encode_clip
        self._before_ns = round(before_s * 1e9)
        self._after_ns = round(after_s * 1e9)
        self._fps = fps
        self._interval_ns = round(1e9 / fps)
        self._monotonic = monotonic
        self._ring = FrameRing(ring_seconds)
        self._incoming: queue.Queue[tuple[int, np.ndarray, tuple[TrackUpdate, ...]]] = queue.Queue(
            max_waiting
        )
        self._jobs: deque[EvidenceJob] = deque()
        self._finished: deque[EvidenceFiles] = deque()
        self._lock = threading.Lock()  # the jobs and the finished list
        self._last_fed_ns: int | None = None
        self._wake = threading.Event()
        self._closing = False
        self.frames_dropped = 0
        self.written = 0
        self.skipped_for_space = 0  # passes that got no evidence because the disk was too full
        self._min_free = min_free_bytes
        self._free_of = free_of
        self._space_warned_at = -math.inf
        self._thread = threading.Thread(target=self._work, name="evidence", daemon=True)
        if threaded:
            self._thread.start()

    # --- the camera loop's side -----------------------------------------------------------

    def feed(self, t_ns: int, image_rgb: np.ndarray, tracks: Sequence[TrackUpdate]) -> None:
        """Offer a frame. Cheap: it goes to the recorder's thread, thinned to the clip's rate."""
        last = self._last_fed_ns
        if last is not None and 0 <= t_ns - last < self._interval_ns * 0.9:
            return
        self._last_fed_ns = t_ns
        try:
            self._incoming.put_nowait((t_ns, image_rgb, tuple(tracks)))
        except queue.Full:
            self.frames_dropped += 1  # the thread cannot keep up: a gap in a clip beats a stall
            return
        self._wake.set()

    def request(self, job: EvidenceJob) -> None:
        """Ask for evidence of an event: it is written when the seconds after it have been seen."""
        with self._lock:
            self._jobs.append(job)
        self._wake.set()

    def poll(self) -> list[EvidenceFiles]:
        """The evidence written since the last call."""
        with self._lock:
            done = list(self._finished)
            self._finished.clear()
        return done

    @property
    def waiting(self) -> int:
        """How many events are still waiting for their evidence."""
        with self._lock:
            return len(self._jobs)

    def close(self) -> None:
        """Stop the thread, and write what is still waiting from the frames already in hand.

        A pass seen a second before a restart gets a clip that ends a second after it, not none.
        """
        self._closing = True
        self._wake.set()
        if self._thread.is_alive():
            self._thread.join(timeout=5.0)
        try:
            self.drain(final=True)
        except Exception:
            log.exception("evidence: could not write what was waiting as the recorder closed")

    # --- the recorder's own side ----------------------------------------------------------

    def _work(self) -> None:
        while not self._closing:
            self._wake.wait(timeout=0.25)
            self._wake.clear()
            try:
                self.drain()
            except Exception:
                log.exception("evidence: could not process the frames and requests waiting")

    def drain(self, *, final: bool = False) -> None:
        """Take in the frames that are waiting, and write the evidence that is ready.

        ``final``: write all that is asked for now, with the frames there are.
        """
        while True:
            try:
                t_ns, image, tracks = self._incoming.get_nowait()
            except queue.Empty:
                break
            self._ring.add(RingFrame(t_ns, _to_jpeg(image), tracks))
            self._write_ready(final=False)
        self._write_ready(final=final)

    def _write_ready(self, *, final: bool) -> None:
        with self._lock:
            waiting = list(self._jobs)
        newest = self._ring.newest_ns
        now = self._monotonic()
        for job in waiting:
            seen_enough = newest is not None and newest >= job.at_ns + self._after_ns
            if not (seen_enough or final or now >= job.deadline):
                continue
            if self._too_full():
                files = EvidenceFiles(job.event_id, None, None)
            else:
                try:
                    files = self._write(job)
                except (
                    Exception
                ):  # a folder that cannot be made, a full disk: the job is not tried again
                    log.exception(
                        "evidence: could not write the evidence of event %d", job.event_id
                    )
                    files = EvidenceFiles(job.event_id, None, None)
            with self._lock:
                if job in self._jobs:
                    self._jobs.remove(job)
                self._finished.append(files)

    def _too_full(self) -> bool:
        """Whether the disk has less free than the floor: then nothing is written, so that the
        database on the same card is never starved of room (plan.md D64)."""
        if self._min_free <= 0:
            return False
        try:
            free = self._free_of(self.folder)
        except OSError:
            return False  # not knowing is not a reason to keep evidence from anyone
        if free >= self._min_free:
            return False
        self.skipped_for_space += 1
        now = self._monotonic()
        if now - self._space_warned_at >= SPACE_WARNING_EVERY_S:
            self._space_warned_at = now
            log.warning(
                "evidence: only %.1f GB is free on the disk, under the %.1f GB that is kept free: "
                "no picture or clip is written until there is room (%d passes so far)",
                free / 1e9,
                self._min_free / 1e9,
                self.skipped_for_space,
            )
        return True

    def _write(self, job: EvidenceJob) -> EvidenceFiles:
        frames = self._ring.window(job.at_ns - self._before_ns, job.at_ns + self._after_ns)
        if not frames:
            log.warning("evidence: no frames to show for event %d", job.event_id)
            return EvidenceFiles(job.event_id, None, None)
        base = self.folder / job.stem
        base.parent.mkdir(parents=True, exist_ok=True)
        moment = min(frames, key=lambda f: abs(f.t_ns - job.at_ns))
        snapshot = self._snapshot(job, moment, base.with_suffix(".jpg"))
        clip = self._clip(job, frames, base.with_suffix(".mp4"))
        if snapshot or clip:
            self.written += 1
        relative = lambda path: path.relative_to(self.data_dir).as_posix()  # noqa: E731
        return EvidenceFiles(
            job.event_id,
            relative(base.with_suffix(".jpg")) if snapshot else None,
            relative(base.with_suffix(".mp4")) if clip else None,
        )

    def _snapshot(self, job: EvidenceJob, frame: RingFrame, path: Path) -> bool:
        image = _render(frame, job, [job.caption, "the moment it crossed the stop line"])
        temporary = path.with_name(path.stem + ".part.jpg")
        try:
            if not cv2.imwrite(str(temporary), image, [cv2.IMWRITE_JPEG_QUALITY, SNAPSHOT_QUALITY]):
                return False
            temporary.replace(path)
            return True
        except OSError:
            log.exception("evidence: could not write %s", path)
            return False
        finally:
            temporary.unlink(missing_ok=True)

    def _clip(self, job: EvidenceJob, frames: list[RingFrame], path: Path) -> bool:
        def rendered() -> Iterator[np.ndarray]:
            for frame in frames:  # one at a time: a clip is never all in memory as pictures
                seconds = (frame.t_ns - job.at_ns) / 1e9
                yield _render(frame, job, [job.caption, f"{seconds:+.1f} s"])

        temporary = path.with_name(path.name + ".part")
        try:
            ok = self._encoder(rendered(), self._fps, temporary)
            if ok and temporary.exists():
                temporary.replace(path)
                return True
            return False
        except Exception:
            log.exception("evidence: could not write %s", path)
            return False
        finally:
            temporary.unlink(missing_ok=True)


# --- drawing --------------------------------------------------------------------------------------


def _to_jpeg(image_rgb: np.ndarray) -> bytes:
    ok, buffer = cv2.imencode(
        ".jpg", cv2.cvtColor(image_rgb, cv2.COLOR_RGB2BGR), [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY]
    )
    if not ok:
        raise RuntimeError("could not compress a frame")
    return buffer.tobytes()


def _render(frame: RingFrame, job: EvidenceJob, banners: list[str]) -> np.ndarray:
    """The frame as a BGR picture with the stop sign drawn and the vehicle in question boxed."""
    image = cv2.imdecode(np.frombuffer(frame.jpeg, np.uint8), cv2.IMREAD_COLOR)
    if image is None:  # a damaged frame in memory: show nothing rather than fail the whole clip
        image = np.zeros((360, 640, 3), np.uint8)
    mine = [t for t in frame.tracks if t.id == job.track_id]
    draw(
        image,
        lines=[],
        tracks=mine,
        trails={},
        banners=banners,
        totals=CountTotals(),
        stop_signs=[job.sign] if job.sign is not None else [],
    )
    return image


# --- the clip -------------------------------------------------------------------------------------

_warned_no_ffmpeg = False


def encode_clip(frames: Iterable[np.ndarray], fps: float, path: Path) -> bool:
    """Write BGR frames as an H.264 MP4 that any browser plays, with the system's ffmpeg.

    The Pi 5 has no hardware H.264 encoder (plan.md D8), but a clip this small takes a fraction of
    a second in software. Without ffmpeg there is no clip, only the picture.
    """
    global _warned_no_ffmpeg
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        if not _warned_no_ffmpeg:
            log.warning(
                "evidence: ffmpeg is not installed, so no clips are made (apt install ffmpeg)"
            )
            _warned_no_ffmpeg = True
        return False
    iterator = iter(frames)
    first = next(iterator, None)
    if first is None:
        return False
    height, width = first.shape[:2]
    command = [
        ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
        "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{width}x{height}",
        "-r", f"{fps:g}", "-i", "-",
        "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "27", "-threads", "2",
        "-pix_fmt", "yuv420p", "-movflags", "+faststart", "-f", "mp4", str(path),
    ]  # fmt: skip
    with tempfile.TemporaryFile() as errors:  # a file, not a pipe: nothing to fill up and block
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stderr=errors)
        assert process.stdin is not None
        try:
            process.stdin.write(np.ascontiguousarray(first).tobytes())
            for frame in iterator:
                process.stdin.write(np.ascontiguousarray(frame).tobytes())
        except BrokenPipeError:
            pass  # ffmpeg stopped reading: its message below says why
        finally:
            with contextlib.suppress(BrokenPipeError):
                process.stdin.close()
        try:
            process.wait(timeout=60)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
            log.error("evidence: ffmpeg took over a minute and was stopped")
            return False
        if process.returncode != 0:
            errors.seek(0)
            said = errors.read().decode(errors="replace").strip()[-300:]
            log.error("evidence: ffmpeg failed: %s", said)
            return False
    return path.exists() and path.stat().st_size > 0


# --- keeping it in check --------------------------------------------------------------------------


def clean_up(
    folder: Path,
    *,
    keep_days: int,
    max_bytes: int,
    clip_hours: Mapping[str, float] | None = None,
    outcomes_of: Callable[[Collection[int]], Mapping[int, str]] | None = None,
    now_s: float | None = None,
    min_free_bytes: int = 0,
    free_of: Callable[[Path], int] = free_bytes,
) -> tuple[int, int]:
    """Delete evidence older than ``keep_days``, then the oldest until the rest fits ``max_bytes``
    and, if ``min_free_bytes`` is given, until that much is free on the disk (D64).

    A clip is also deleted once it is older than ``clip_hours`` allows for the outcome of its pass
    (``outcomes_of`` says which event ids had which outcome; a clip whose outcome is not known, or
    not in ``clip_hours``, is judged by ``keep_days`` alone). Returns how many files went and how
    many bytes that freed. A file that is half written is only a leftover once it is old.
    """
    now = time.time() if now_s is None else now_s  # (a folder that is not there has nothing in it)
    cutoff = now - keep_days * 86_400
    clip_cutoffs: dict[int, float] = {}
    if clip_hours and outcomes_of is not None:
        clip_cutoffs = _clip_cutoffs(folder, clip_hours, outcomes_of, now)
    kept: list[tuple[float, int, Path]] = []
    removed_files = removed_bytes = 0

    def remove(path: Path, size: int) -> None:
        nonlocal removed_files, removed_bytes
        try:
            path.unlink()
        except OSError:
            log.exception("evidence: could not delete %s", path)
            return
        removed_files += 1
        removed_bytes += size

    for path in folder.rglob("*"):
        if not path.is_file():
            continue
        try:
            stat = path.stat()
        except OSError:
            continue
        part = ".part" in path.name
        clip_id = _clip_id(path)
        too_old = stat.st_mtime < cutoff or (
            clip_id is not None and stat.st_mtime < clip_cutoffs.get(clip_id, -math.inf)
        )
        if (part and now - stat.st_mtime > STALE_PART_S) or (not part and too_old):
            remove(path, stat.st_size)
        elif not part:
            kept.append((stat.st_mtime, stat.st_size, path))
    total = sum(size for _, size, _ in kept)
    freed_so_far = (
        removed_bytes  # already given back by the age rules, and so already in the free space
    )
    free = free_of(folder) if min_free_bytes > 0 else 0
    for _, size, path in sorted(kept):  # the oldest first
        too_big = total > max_bytes
        too_full = min_free_bytes > 0 and free + (removed_bytes - freed_so_far) < min_free_bytes
        if not (too_big or too_full):
            break
        remove(path, size)
        total -= size
    for directory in sorted((d for d in folder.rglob("*") if d.is_dir()), reverse=True):
        with contextlib.suppress(OSError):
            directory.rmdir()  # only if it is empty
    return removed_files, removed_bytes


_CLIP_NAME = re.compile(r"stop-(\d+)\.mp4")


def _clip_id(path: Path) -> int | None:
    """The event a clip belongs to, from its name (``stop-<id>.mp4``); None for other files."""
    found = _CLIP_NAME.fullmatch(path.name)
    return int(found.group(1)) if found else None


def _clip_cutoffs(
    folder: Path,
    clip_hours: Mapping[str, float],
    outcomes_of: Callable[[Collection[int]], Mapping[int, str]],
    now: float,
) -> dict[int, float]:
    """For each clip in the folder whose outcome has its own time: the modified time before which it
    goes. If the outcomes cannot be read, no clip is judged by its outcome this time (the days
    still are, and the next clean-up tries again)."""
    ids = {i for path in folder.rglob("stop-*.mp4") if (i := _clip_id(path)) is not None}
    if not ids:
        return {}
    try:
        outcomes = outcomes_of(ids)
    except Exception:
        log.exception("evidence: could not read the clips' outcomes; judging them by age only")
        return {}
    return {
        event_id: now - clip_hours[name] * 3_600
        for event_id, name in outcomes.items()
        if name in clip_hours
    }
