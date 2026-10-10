import logging
import os
import re
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
from tests.test_service_speed import drive as calibrated_drive
from tests.test_service_speed import profile as calibrated_profile
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


@pytest.mark.parametrize(
    ("units", "low", "high", "unit"), [("mph", 18, 22, "mph"), ("kmh", 29, 36, "km/h")]
)
def test_a_pass_measured_on_a_calibrated_road_has_its_speeds_in_the_caption(
    store: Store,
    tmp_path: Path,
    recorder: Recorded,
    units: str,
    low: int,
    high: int,
    unit: str,
) -> None:
    """A car at 9 m/s (20 mph, 32 km/h) through the calibrated stop sign: the picture says how fast,
    in the owner's units, and that the calibration is not checked (D46, MEASURED_CAVEAT)."""
    store.set_profile(calibrated_profile())
    store.set_setting("units", units)
    run(calibrated_drive(9.0, 8.0) + AFTER, store, tmp_path, recorder)
    (job,) = recorder.jobs
    found = re.search(
        rf" - speed \(calibrated, not checked\): slowest (\d+) {unit}, at the line (\d+) {unit}$",
        job.caption,
    )
    assert found, job.caption
    assert low <= int(found[1]) <= high and low <= int(found[2]) <= high
    assert job.caption.startswith("Elm: car #") and " did not stop - 20" in job.caption


def test_a_pass_with_no_measured_speed_has_none_in_its_caption(
    store: Store, tmp_path: Path, recorder: Recorded
) -> None:
    run(car_driving(ROLLING_STOP) + AFTER, store, tmp_path, recorder)  # no calibration
    (job,) = recorder.jobs
    assert "speed" not in job.caption and " rolled through - 20" in job.caption


def test_the_time_in_the_caption_is_the_cameras(
    store: Store, tmp_path: Path, recorder: Recorded
) -> None:
    store.set_setting("timezone", "Pacific/Auckland")  # 12 or 13 hours from UTC
    run(car_driving(ROLLING_STOP) + AFTER, store, tmp_path, recorder)
    (job,) = recorder.jobs
    (event,) = stops(store)
    local = datetime.fromtimestamp(event.occurred_at / 1000, ZoneInfo("Pacific/Auckland"))
    assert job.caption.endswith(local.strftime("%Y-%m-%d %H:%M:%S"))


# --- one switch for each outcome (D33) ------------------------------------------------------------

ROLLED = (ROLLING_STOP, "rolling", "keep_rolling")
DID_NOT_STOP = (STRAIGHT_THROUGH, "did_not_stop", "keep_did_not_stop")


@pytest.mark.parametrize(("drive", "outcome", "switch"), [ROLLED, DID_NOT_STOP])
def test_an_outcome_whose_switch_is_off_gets_no_picture_and_no_clip_but_is_still_counted(
    store: Store,
    tmp_path: Path,
    recorder: Recorded,
    encoder: FakeEncoder,
    drive: list[tuple[float, float, float]],
    outcome: str,
    switch: str,
) -> None:
    store.set_evidence_settings(EvidenceSettings(**{switch: False}))
    service = run(car_driving(drive) + AFTER, store, tmp_path, recorder)
    (event,) = stops(store)  # the pass is judged and stored: the Data page and the email need it
    assert (event.details or {})["outcome"] == outcome
    assert recorder.jobs == [] and not (tmp_path / "evidence").exists()
    assert event.evidence_path is None and "clip" not in (event.details or {})
    assert encoder.calls == 0 and service.evidence_written == 0


@pytest.mark.parametrize(
    ("drive", "outcome", "other_switch"),
    [
        (ROLLING_STOP, "rolling", "keep_did_not_stop"),
        (STRAIGHT_THROUGH, "did_not_stop", "keep_rolling"),
    ],
)
def test_the_switch_of_the_other_outcome_does_not_touch_this_one(
    store: Store,
    tmp_path: Path,
    recorder: Recorded,
    drive: list[tuple[float, float, float]],
    outcome: str,
    other_switch: str,
) -> None:
    store.set_evidence_settings(EvidenceSettings(**{other_switch: False}))
    run(car_driving(drive) + AFTER, store, tmp_path, recorder)
    (event,) = stops(store)
    assert (event.details or {})["outcome"] == outcome
    assert len(recorder.jobs) == 1 and event.evidence_path is not None


def test_a_pass_that_could_not_be_told_follows_the_main_switch_alone(
    store: Store, tmp_path: Path, recorder: Recorded
) -> None:
    store.set_evidence_settings(EvidenceSettings(keep_rolling=False, keep_did_not_stop=False))
    first_seen_at_the_line = car_driving([(1.0, 0.15, 0.15)], x0=0.46)
    run(first_seen_at_the_line + AFTER, store, tmp_path, recorder)
    (event,) = stops(store)
    assert (event.details or {})["outcome"] == "indeterminate"
    assert len(recorder.jobs) == 1 and event.evidence_path is not None


def test_a_clean_stop_gets_none_whatever_the_switches_say(
    store: Store, tmp_path: Path, recorder: Recorded
) -> None:
    store.set_evidence_settings(EvidenceSettings(keep_rolling=True, keep_did_not_stop=True))
    run(car_driving(FULL_STOP) + AFTER, store, tmp_path, recorder)
    assert recorder.jobs == []


@pytest.mark.parametrize(("drive", "outcome", "switch"), [ROLLED, DID_NOT_STOP])
def test_an_outcomes_switch_is_turned_off_for_a_running_service_when_it_is_told(
    store: Store,
    tmp_path: Path,
    recorder: Recorded,
    drive: list[tuple[float, float, float]],
    outcome: str,
    switch: str,
) -> None:
    holder: list[VisionService] = []

    def at_frame(i: int) -> None:
        time.sleep(0.001)
        if i == 5:  # the owner turns the switch off, and the API tells the service
            store.set_evidence_settings(EvidenceSettings(**{switch: False}))
            holder[0].request_reload()

    source = ScriptedSource(car_driving(drive) + AFTER, on_read=at_frame)
    source.frame_size = (640, 360)
    service = VisionService(
        source, ScriptedDetector(source), store, data_dir=tmp_path, evidence=recorder
    )
    holder.append(service)
    service.run(StopRequest())
    (event,) = stops(store)
    assert (event.details or {})["outcome"] == outcome  # still judged
    assert recorder.jobs == [] and event.evidence_path is None


def test_what_was_kept_stays_when_an_outcomes_switch_is_turned_off(
    store: Store, tmp_path: Path, recorder: Recorded
) -> None:
    run(car_driving(ROLLING_STOP) + AFTER, store, tmp_path, recorder)
    (first,) = stops(store)
    assert first.evidence_path is not None
    store.set_evidence_settings(EvidenceSettings(keep_rolling=False))
    later = Recorded(tmp_path, encoder=FakeEncoder(), max_waiting=2_000)
    try:
        run(car_driving(ROLLING_STOP) + AFTER, store, tmp_path, later)
    finally:
        later.close()
    assert later.jobs == []  # nothing new is made ...
    assert len(stops(store)) == 2  # ... the second pass is counted all the same ...
    assert (tmp_path / first.evidence_path).exists()  # ... and nothing already kept is deleted


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


def test_a_clip_is_deleted_by_the_hours_of_its_outcome_and_the_count_stays(
    store: Store, tmp_path: Path, recorder: Recorded
) -> None:
    from tests.test_store import minute, stop

    store.set_evidence_settings(EvidenceSettings(rolling_clip_hours=3, did_not_stop_clip_hours=48))
    ids = {
        "rolling": store.record_stop(stop("rolling"), minute(0) + 1_000),
        "did_not_stop": store.record_stop(stop("did_not_stop"), minute(0) + 2_000),
        "indeterminate": store.record_stop(stop("indeterminate"), minute(0) + 3_000),
    }
    folder = tmp_path / "evidence" / "2026-10-08"
    folder.mkdir(parents=True)
    made = {}
    for name, hours in (("rolling", 4), ("did_not_stop", 4), ("indeterminate", 4)):
        path = folder / f"stop-{ids[name]}.mp4"
        path.write_bytes(b"x" * 50)
        modified = time.time() - hours * 3_600
        os.utime(path, (modified, modified))
        made[name] = path
    run([[]], store, tmp_path, recorder)
    assert not made["rolling"].exists()  # 4 hours is past 3
    assert made["did_not_stop"].exists() and made["indeterminate"].exists()
    assert len(store.stops_between(minute(0), minute(1))) == 3  # the passes are all still counted


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


def test_frames_are_kept_only_while_there_is_a_stop_sign_to_show(
    store: Store, tmp_path: Path, recorder: Recorded
) -> None:
    store.set_profile({"stop_signs": []})
    run([[] for _ in range(60)], store, tmp_path, recorder)
    assert recorder._incoming.qsize() == 0 and len(recorder._ring) == 0
    assert recorder.frames_dropped == 0


def test_the_recorder_is_stopped_when_the_service_is(
    store: Store, tmp_path: Path, recorder: Recorded
) -> None:
    run(car_driving(ROLLING_STOP) + AFTER, store, tmp_path, recorder)
    assert not recorder._thread.is_alive()
