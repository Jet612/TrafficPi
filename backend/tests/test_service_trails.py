import logging

import pytest

from tests.fakes import ScriptedDetector, ScriptedSource, car_crossing
from trafficpi.core.clock import WallClock
from trafficpi.core.trail_settings import TrailSettings
from trafficpi.core.types import Detection
from trafficpi.db import create_db_engine, upgrade_to_head
from trafficpi.db.store import Store
from trafficpi.vision import service as service_module
from trafficpi.vision.service import ServiceConfig, VisionService
from trafficpi.vision.shutdown import StopRequest
from trafficpi.vision.trails import FinishedTrail, decode_path, encode_path

DAY_MS = 86_400_000


@pytest.fixture
def store() -> Store:
    engine = create_db_engine(":memory:")
    upgrade_to_head(engine)
    s = Store(engine)
    s.ensure_camera()
    return s


def run(script: list[list[Detection]], store: Store, **kw) -> VisionService:  # type: ignore[no-untyped-def]
    source = kw.pop("source", None) or ScriptedSource(script)
    service = VisionService(source, ScriptedDetector(source), store, **kw)
    service.run(StopRequest())
    return service


def walker(frames: int = 100) -> list[list[Detection]]:
    """A person walking across the picture."""
    return [
        [Detection(0, "person", 0.9, 0.18 + 0.006 * i, 0.4, 0.22 + 0.006 * i, 0.5)]
        for i in range(frames)
    ]


def old_trail(ended_ms_ago: int, *, track_id: int = 1) -> FinishedTrail:
    end = WallClock().now_ms() - ended_ms_ago
    return FinishedTrail(
        track_id=track_id,
        started_at=end - 4_000,
        ended_at=end,
        label="car",
        points=3,
        extent=0.4,
        path=encode_path([0, 2_000, 4_000], [0.1, 0.3, 0.5], [0.6, 0.6, 0.6]),
    )


def test_a_car_driving_across_the_picture_is_saved_as_a_trail(store: Store) -> None:
    service = run(car_crossing(100), store)
    assert service.trails_recorded == 1
    (row,) = store.trails_between(0, 2**50)
    # The path begins when the tracker confirms the car, a couple of frames after it appears, so
    # it spans a little less than the 0.594 the car drove.
    assert row.class_label == "car" and row.extent == pytest.approx(0.58, abs=0.02)
    path = decode_path(row.path)
    assert path.x.tolist() == pytest.approx([0.5] * len(path), abs=1e-3)
    assert path.y[0] == pytest.approx(0.2, abs=0.02) and path.y[-1] == pytest.approx(
        0.794, abs=0.02
    )
    assert row.ended_at - row.started_at == path.offsets_ms[-1]


def test_a_trail_is_stored_when_its_vehicle_is_gone_not_only_when_the_service_stops(
    store: Store,
) -> None:
    seen: list[int] = []
    script = car_crossing(60) + [[] for _ in range(60)]  # the car leaves; the street stays empty
    source = ScriptedSource(
        script, on_read=lambda i: seen.append(store.trail_count()) if i == 110 else None
    )
    service = run(script, store, source=source)
    assert seen == [1]  # already there, ten frames before the stream ended
    assert service.trails_recorded == 1 and store.trail_count() == 1


def test_trails_can_be_switched_off(store: Store) -> None:
    store.set_trail_settings(TrailSettings(enabled=False))
    service = run(car_crossing(100), store)
    assert service.trails_recorded == 0 and store.trail_count() == 0
    assert service.frames == 100  # and everything else carries on


def test_people_are_not_saved_unless_switched_on(store: Store) -> None:
    run(walker(), store)
    assert store.trail_count() == 0
    store.set_trail_settings(TrailSettings(people=True))
    run(walker(), store)
    (row,) = store.trails_between(0, 2**50)
    assert row.class_label == "person"


def test_a_change_of_settings_applies_to_a_running_service_when_it_is_told(store: Store) -> None:
    holder: list[VisionService] = []

    def at_frame(i: int) -> None:
        if i == 40:  # the owner switches people on, and the API tells the service
            store.set_trail_settings(TrailSettings(people=True))
            holder[0].request_reload()

    source = ScriptedSource(walker(100), on_read=at_frame)
    service = VisionService(source, ScriptedDetector(source), store)
    holder.append(service)
    service.run(StopRequest())
    (row,) = store.trails_between(0, 2**50)
    path = decode_path(row.path)
    assert path.x[0] > 0.3  # it began where the walker was at the change, not at the start
    assert path.x[-1] == pytest.approx(0.2 + 0.006 * 99, abs=0.02)


def test_switching_trails_off_on_a_running_service_stops_saving(store: Store) -> None:
    holder: list[VisionService] = []

    def at_frame(i: int) -> None:
        if i == 40:
            store.set_trail_settings(TrailSettings(enabled=False))
            holder[0].request_reload()

    source = ScriptedSource(car_crossing(100), on_read=at_frame)
    service = VisionService(source, ScriptedDetector(source), store)
    holder.append(service)
    service.run(StopRequest())
    assert store.trail_count() == 0  # the path in progress was dropped, not saved half way


def test_a_database_that_fails_for_a_while_loses_nothing(
    store: Store, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(service_module, "TRAIL_RETRY_S", 0.0)  # try again on the next frame
    real, failures = store.add_trails, []

    def flaky(trails):  # type: ignore[no-untyped-def]
        if len(failures) < 3:
            failures.append(1)
            raise OSError("database is locked")
        real(trails)

    monkeypatch.setattr(store, "add_trails", flaky)
    script = car_crossing(60) + [[] for _ in range(60)]
    with caplog.at_level(logging.ERROR, logger="trafficpi.vision"):
        service = run(script, store)
    assert len(failures) == 3
    assert service.trails_recorded == 1 and store.trail_count() == 1
    assert sum("could not store" in r.getMessage() for r in caplog.records) == 3


def test_a_database_that_stays_away_does_not_flood_the_log_or_stop_the_service(
    store: Store, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def broken(trails):  # type: ignore[no-untyped-def]
        raise OSError("disk I/O error")

    monkeypatch.setattr(store, "add_trails", broken)
    with caplog.at_level(logging.ERROR, logger="trafficpi.vision"):
        service = run(car_crossing(60) + [[] for _ in range(80)], store)
    assert service.frames == 140 and service.trails_recorded == 0
    # one failure while it ran (then a wait of five seconds), one more when it stopped
    assert sum("could not store" in r.getMessage() for r in caplog.records) <= 2


def test_trails_older_than_the_keep_time_are_deleted_when_the_service_starts(store: Store) -> None:
    store.add_trails([old_trail(400 * DAY_MS, track_id=1), old_trail(10 * DAY_MS, track_id=2)])
    run([[]], store)  # one empty frame is enough for the loop to do its housekeeping
    (row,) = store.trails_between(0, 2**50)  # the 10-day-old one stays, with the default year
    assert row.ended_at > WallClock().now_ms() - 11 * DAY_MS


def test_the_keep_time_is_the_owners_to_set(store: Store) -> None:
    store.set_trail_settings(TrailSettings(keep_days=30))
    store.add_trails([old_trail(40 * DAY_MS, track_id=1), old_trail(10 * DAY_MS, track_id=2)])
    run([[]], store)
    (row,) = store.trails_between(0, 2**50)
    assert row.ended_at > WallClock().now_ms() - 11 * DAY_MS


def test_a_big_backlog_is_cleared_a_few_chunks_at_a_time(
    store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(service_module, "TRAIL_CLEANUP_CHUNK", 3)
    monkeypatch.setattr(service_module, "TRAIL_CLEANUP_CHUNKS_PER_PASS", 2)
    store.add_trails([old_trail(500 * DAY_MS + i, track_id=i) for i in range(10)])
    service = run([[]], store)
    assert store.trail_count() == 4  # two chunks of three went
    assert service._next_trail_cleanup < service_module.time.monotonic() + 31
    service._clean_up_trails()
    service._clean_up_trails()
    assert store.trail_count() == 0


def test_the_cleanup_runs_again_after_the_set_interval_not_at_every_frame(store: Store) -> None:
    calls: list[int] = []
    real = store.delete_trails_before

    def counting(cutoff, *, limit):  # type: ignore[no-untyped-def]
        calls.append(1)
        return real(cutoff, limit=limit)

    store.delete_trails_before = counting  # type: ignore[method-assign]
    run([[] for _ in range(50)], store, config=ServiceConfig(trail_cleanup_every_s=3600.0))
    assert len(calls) == 1  # once, on the first frame, not fifty times
