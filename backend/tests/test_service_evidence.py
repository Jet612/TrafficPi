import logging
import os
import time
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from tests.fakes import (
    FULL_STOP,
    ROLLING_STOP,
    STOP_SIGN,
    STRAIGHT_THROUGH,
    ScriptedDetector,
    ScriptedSource,
    car_driving,
)
from tests.test_evidence import FakeEncoder
from trafficpi.core.evidence_settings import EvidenceSettings
from trafficpi.core.types import Detection
from trafficpi.db import create_db_engine, upgrade_to_head
from trafficpi.db.store import Store
from trafficpi.vision import evidence as evidence_module
from trafficpi.vision.evidence import EvidenceJob, EvidenceRecorder
from trafficpi.vision.service import ServiceConfig, VisionService
from trafficpi.vision.shutdown import StopRequest

AFTER = [[] for _ in range(150)]  # five more seconds of an empty street: the seconds after a pass


class Recorded(EvidenceRecorder):
    """A recorder that remembers what it was asked for."""

    def __init__(self, *args, **kw) -> None:  # type: ignore[no-untyped-def]
        super().__init__(*args, **kw)
        self.jobs: list[EvidenceJob] = []

    def request(self, job: EvidenceJob) -> None:
        self.jobs.append(job)
        super().request(job)


@pytest.fixture
def store() -> Store:
    engine = create_db_engine(":memory:")
    upgrade_to_head(engine)
    s = Store(engine)
    s.ensure_camera()
    s.set_profile({"stop_signs": [STOP_SIGN | {"name": "Elm Street"}]})
    return s


@pytest.fixture
def encoder() -> FakeEncoder:
    return FakeEncoder()


@pytest.fixture
def recorder(tmp_path: Path, encoder: FakeEncoder) -> Iterator[Recorded]:
    """A recorder on its own thread, that the service is given instead of making one."""
    recorder = Recorded(tmp_path, encoder=encoder, max_waiting=2_000)
    yield recorder
    recorder.close()  # one the service never took up is still running


def run(
    script: list[list[Detection]],
    store: Store,
    tmp_path: Path,
    recorder: EvidenceRecorder,
    *,
    data_dir: bool = True,
    config: ServiceConfig | None = None,
) -> VisionService:
    source = ScriptedSource(script, on_read=lambda i: time.sleep(0.001))
    source.frame_size = (640, 360)
    service = VisionService(
        source,
        ScriptedDetector(source),
        store,
        config=config or ServiceConfig(),
        data_dir=tmp_path if data_dir else None,
        evidence=recorder,
    )
    service.run(StopRequest())
    return service


def stops(store: Store):  # type: ignore[no-untyped-def]
    return store.recent_events(type="stop", limit=100)


def day_of(ms: int) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(ms / 1000))


# --- what gets evidence ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("drive", "outcome"), [(ROLLING_STOP, "rolling"), (STRAIGHT_THROUGH, "did_not_stop")]
)
def test_a_pass_that_needs_a_look_gets_a_picture_and_a_clip_noted_in_the_database(
    store: Store,
    tmp_path: Path,
    recorder: Recorded,
    encoder: FakeEncoder,
    drive: list[tuple[float, float, float]],
    outcome: str,
) -> None:
    run(car_driving(drive) + AFTER, store, tmp_path, recorder)
    (event,) = stops(store)
    assert (event.details or {})["outcome"] == outcome
    stem = f"evidence/{day_of(event.occurred_at)}/stop-{event.id}"
    clip = (event.details or {})["clip"]
    assert event.evidence_path == f"{stem}.jpg" and clip == f"{stem}.mp4"
    assert (tmp_path / f"{stem}.jpg").stat().st_size > 100
    assert (tmp_path / clip).exists()
    assert encoder.calls == 1


def test_a_clean_stop_needs_no_evidence(store: Store, tmp_path: Path, recorder: Recorded) -> None:
    service = run(car_driving(FULL_STOP) + AFTER, store, tmp_path, recorder)
    (event,) = stops(store)
    assert (event.details or {})["outcome"] == "stopped"
    assert event.evidence_path is None and "clip" not in (event.details or {})
    assert recorder.jobs == [] and not (tmp_path / "evidence").exists()
    assert service.evidence_written == 0


def test_what_has_been_written_and_what_waits_is_counted_for_the_health_report(
    store: Store, tmp_path: Path, recorder: Recorded
) -> None:
    service = run(car_driving(ROLLING_STOP), store, tmp_path, recorder)
    assert service.evidence_written == 1  # written as the service stopped, from the frames in hand
    assert service.evidence_waiting == 0  # and nothing is left to wait (the recorder is closed)
    assert service.evidence_frames_dropped == 0


def test_frames_the_recorder_had_no_room_for_are_counted_even_after_it_is_closed(
    store: Store, tmp_path: Path, encoder: FakeEncoder
) -> None:
    crowded = Recorded(
        tmp_path, encoder=encoder, max_waiting=1, threaded=False
    )  # nothing takes frames
    service = run(car_driving(ROLLING_STOP) + AFTER, store, tmp_path, crowded)
    assert service.evidence_frames_dropped == crowded.frames_dropped > 100


def test_the_job_says_where_what_and_when_and_which_vehicle(
    store: Store, tmp_path: Path, recorder: Recorded
) -> None:
    run(car_driving(ROLLING_STOP) + AFTER, store, tmp_path, recorder)
    (job,) = recorder.jobs
    (event,) = stops(store)
    assert job.event_id == event.id and job.track_id == event.track_id
    assert job.sign is not None and job.sign.id == "elm" and job.sign.name == "Elm Street"
    assert job.caption.startswith("Elm Street: car #") and " rolled through - 20" in job.caption
    assert job.stem == f"{day_of(event.occurred_at)}/stop-{event.id}"


def test_the_time_in_the_caption_is_the_cameras(
    store: Store, tmp_path: Path, recorder: Recorded
) -> None:
    store.set_setting("timezone", "Pacific/Auckland")  # 12 or 13 hours from UTC
    run(car_driving(ROLLING_STOP) + AFTER, store, tmp_path, recorder)
    (job,) = recorder.jobs
    (event,) = stops(store)
    local = datetime.fromtimestamp(event.occurred_at / 1000, ZoneInfo("Pacific/Auckland"))
    assert job.caption.endswith(local.strftime("%Y-%m-%d %H:%M:%S"))


# --- when there is none ---------------------------------------------------------------------------


def test_no_evidence_when_it_is_switched_off(
    store: Store, tmp_path: Path, recorder: Recorded
) -> None:
    store.set_evidence_settings(EvidenceSettings(enabled=False))
    run(car_driving(ROLLING_STOP) + AFTER, store, tmp_path, recorder)
    assert recorder.jobs == [] and not (tmp_path / "evidence").exists()
    (event,) = stops(store)
    assert event.evidence_path is None


def test_no_evidence_when_the_service_has_nowhere_to_put_it(
    store: Store, tmp_path: Path, recorder: Recorded
) -> None:
    run(car_driving(ROLLING_STOP) + AFTER, store, tmp_path, recorder, data_dir=False)
    assert recorder.jobs == []


def test_evidence_is_switched_on_for_a_running_service_when_it_is_told(
    store: Store, tmp_path: Path, recorder: Recorded
) -> None:
    holder: list[VisionService] = []

    def at_frame(i: int) -> None:
        time.sleep(0.001)
        if i == 5:  # the owner switches evidence on, and the API tells the service
            store.set_evidence_settings(EvidenceSettings(enabled=True))
            holder[0].request_reload()

    store.set_evidence_settings(EvidenceSettings(enabled=False))
    source = ScriptedSource(car_driving(ROLLING_STOP) + AFTER, on_read=at_frame)
    source.frame_size = (640, 360)
    service = VisionService(
        source, ScriptedDetector(source), store, data_dir=tmp_path, evidence=recorder
    )
    holder.append(service)
    service.run(StopRequest())
    assert len(recorder.jobs) == 1
    (event,) = stops(store)
    assert event.evidence_path is not None


def test_evidence_is_switched_off_for_a_running_service_when_it_is_told(
    store: Store, tmp_path: Path, recorder: Recorded
) -> None:
    holder: list[VisionService] = []

    def at_frame(i: int) -> None:
        time.sleep(0.001)
        if i == 5:
            store.set_evidence_settings(EvidenceSettings(enabled=False))
            holder[0].request_reload()

    source = ScriptedSource(car_driving(ROLLING_STOP) + AFTER, on_read=at_frame)
    source.frame_size = (640, 360)
    service = VisionService(
        source, ScriptedDetector(source), store, data_dir=tmp_path, evidence=recorder
    )
    holder.append(service)
    service.run(StopRequest())
    assert recorder.jobs == [] and not recorder._thread.is_alive()  # stopped when told
    (event,) = stops(store)
    assert event.evidence_path is None


# --- when something goes wrong --------------------------------------------------------------------


def test_a_database_that_is_busy_when_the_evidence_is_ready_is_asked_again(
    store: Store,
    tmp_path: Path,
    recorder: Recorded,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    real, failures = store.attach_evidence, []

    def flaky(event_id, snapshot, clip):  # type: ignore[no-untyped-def]
        if not failures:
            failures.append(1)
            raise OSError("database is locked")
        return real(event_id, snapshot, clip)

    monkeypatch.setattr(store, "attach_evidence", flaky)
    with caplog.at_level(logging.ERROR, logger="trafficpi.vision"):
        run(car_driving(ROLLING_STOP) + AFTER, store, tmp_path, recorder)
    (event,) = stops(store)
    assert failures == [1] and event.evidence_path is not None  # it got there in the end
    assert any("could not note the evidence" in r.getMessage() for r in caplog.records)


def test_a_pass_seen_just_before_the_service_stops_still_gets_what_the_frames_in_hand_show(
    store: Store, tmp_path: Path, recorder: Recorded, encoder: FakeEncoder
) -> None:
    run(car_driving(ROLLING_STOP), store, tmp_path, recorder)
    (event,) = stops(store)  # the script ends at the crossing: the 3 s after it never come
    assert event.evidence_path is not None and (tmp_path / event.evidence_path).exists()
    assert (event.details or {})["clip"] and encoder.calls == 1


def test_a_failure_in_asking_for_evidence_does_not_stop_the_counting(
    store: Store,
    tmp_path: Path,
    recorder: Recorded,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    def broken(job: EvidenceJob) -> None:
        raise RuntimeError("the recorder fell over")

    monkeypatch.setattr(recorder, "request", broken)
    with caplog.at_level(logging.ERROR, logger="trafficpi.vision"):
        service = run(car_driving(ROLLING_STOP) + AFTER, store, tmp_path, recorder)
    (event,) = stops(store)
    assert event.evidence_path is None and service.events_recorded == 1
    assert any("could not ask for evidence" in r.getMessage() for r in caplog.records)


def test_settings_that_cannot_be_read_leave_evidence_as_it_was(
    store: Store,
    tmp_path: Path,
    recorder: Recorded,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    def busy() -> EvidenceSettings:
        raise OSError("database is locked")

    monkeypatch.setattr(store, "get_evidence_settings", busy)
    with caplog.at_level(logging.ERROR, logger="trafficpi.vision"):
        service = run(car_driving(ROLLING_STOP) + AFTER, store, tmp_path, recorder)
    assert recorder.jobs == [] and service.events_recorded == 1  # not started, and no harm done
    assert any("could not read the evidence settings" in r.getMessage() for r in caplog.records)


# --- keeping it in check --------------------------------------------------------------------------


def leave_two_files(tmp_path: Path) -> tuple[Path, Path]:
    """A file from 40 days ago and one from just now, in the evidence folder."""
    old = tmp_path / "evidence" / "2026-01-01" / "stop-1.jpg"
    recent = tmp_path / "evidence" / "2026-10-08" / "stop-2.jpg"
    for path, age_days in ((old, 40), (recent, 0)):
        path.parent.mkdir(parents=True)
        path.write_bytes(b"x" * 50)
        modified = time.time() - age_days * 86_400
        os.utime(path, (modified, modified))
    return old, recent


def test_evidence_that_is_too_old_is_deleted_when_the_service_starts(
    store: Store, tmp_path: Path, recorder: Recorded
) -> None:
    store.set_evidence_settings(EvidenceSettings(keep_days=2))
    old, recent = leave_two_files(tmp_path)
    run([[]], store, tmp_path, recorder)
    assert not old.exists() and recent.exists()


def test_when_there_is_more_than_the_set_room_the_oldest_goes(
    store: Store, tmp_path: Path, recorder: Recorded
) -> None:
    store.set_evidence_settings(EvidenceSettings(max_mb=50))  # room for one of these, not two
    folder = tmp_path / "evidence" / "2026-10-08"
    folder.mkdir(parents=True)
    names = ["stop-1.mp4", "stop-2.mp4", "stop-3.mp4"]
    for age_hours, name in zip((30, 20, 10), names, strict=True):
        path = folder / name
        with path.open("wb") as handle:
            handle.truncate(30_000_000)  # 30 MB that take no room on the disk
        modified = time.time() - age_hours * 3_600
        os.utime(path, (modified, modified))
    run([[]], store, tmp_path, recorder)
    assert [name for name in names if (folder / name).exists()] == ["stop-3.mp4"]


def test_old_evidence_is_deleted_also_while_no_more_is_being_made(
    store: Store, tmp_path: Path, recorder: Recorded
) -> None:
    store.set_evidence_settings(EvidenceSettings(enabled=False, keep_days=2))
    old, recent = leave_two_files(tmp_path)
    run([[]], store, tmp_path, recorder)
    assert not old.exists() and recent.exists()


def test_new_limits_are_applied_at_once_when_the_service_is_told(
    store: Store, tmp_path: Path, recorder: Recorded
) -> None:
    store.set_evidence_settings(EvidenceSettings(keep_days=60))  # 40 days old is fine for now
    old, recent = leave_two_files(tmp_path)
    holder: list[VisionService] = []
    seen: dict[int, bool] = {}  # whether the old file was still there when each frame was read

    def at_frame(i: int) -> None:
        time.sleep(0.001)
        seen[i] = old.exists()
        if i == 5:  # the owner shortens the time, and the API tells the service
            store.set_evidence_settings(EvidenceSettings(keep_days=1))
            holder[0].request_reload()

    source = ScriptedSource([[] for _ in range(20)], on_read=at_frame)
    source.frame_size = (640, 360)
    service = VisionService(
        source, ScriptedDetector(source), store, data_dir=tmp_path, evidence=recorder
    )
    holder.append(service)
    service.run(StopRequest())
    assert all(seen[i] for i in range(6))  # kept, though the first clean-up had run
    assert not seen[7]  # gone soon after: the hour between clean-ups was not waited for
    assert recent.exists()


@pytest.mark.parametrize(("every_s", "times"), [(3600.0, 1), (0.0, 60)])
def test_the_cleanup_runs_once_an_interval_and_not_at_every_frame(
    store: Store,
    tmp_path: Path,
    recorder: Recorded,
    monkeypatch: pytest.MonkeyPatch,
    every_s: float,
    times: int,
) -> None:
    calls: list[int] = []
    real = evidence_module.clean_up
    monkeypatch.setattr(
        evidence_module, "clean_up", lambda *a, **k: (calls.append(1), real(*a, **k))[1]
    )
    run(
        [[] for _ in range(60)],
        store,
        tmp_path,
        recorder,
        config=ServiceConfig(evidence_cleanup_every_s=every_s),
    )
    assert len(calls) == times


def test_the_recorder_is_stopped_when_the_service_is(
    store: Store, tmp_path: Path, recorder: Recorded
) -> None:
    run(car_driving(ROLLING_STOP) + AFTER, store, tmp_path, recorder)
    assert not recorder._thread.is_alive()
