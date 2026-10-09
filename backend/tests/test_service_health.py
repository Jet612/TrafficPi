"""The vision service keeps a row of health a minute (plan.md M8 step 4)."""

import threading
import time

import pytest

from tests.fakes import ScriptedDetector, ScriptedSource, car_crossing
from trafficpi.core.clock import WallClock
from trafficpi.db import create_db_engine, upgrade_to_head
from trafficpi.db.store import Store
from trafficpi.vision import service as service_module
from trafficpi.vision.health import HealthRow
from trafficpi.vision.service import ServiceConfig, VisionService
from trafficpi.vision.shutdown import StopRequest

START_MS = 1_760_000_040_000  # 2025-10-09 08:54:00 UTC, on a minute


class StepClock:
    """Wall time that moves five seconds every time it is read, so that minutes pass in a test."""

    def __init__(self, start_ms: int = START_MS) -> None:
        self.ns = start_ms * 1_000_000

    def __call__(self) -> int:
        value, self.ns = self.ns, self.ns + 5_000_000_000
        return value


@pytest.fixture
def store() -> Store:
    engine = create_db_engine(":memory:")
    upgrade_to_head(engine)
    s = Store(engine)
    s.ensure_camera()
    return s


def run_service(store: Store, frames: int = 60, **config: object) -> VisionService:
    source = ScriptedSource(car_crossing(frames))
    service = VisionService(
        source,
        ScriptedDetector(source),
        store,
        clock=WallClock(wall_ns=StepClock()),
        config=ServiceConfig(metrics_every_s=0.0, **config),  # type: ignore[arg-type]
    )
    service.run(StopRequest())
    return service


def stored(store: Store) -> list[HealthRow]:
    return store.health_between(0, 10**12)


def test_a_row_is_stored_for_each_minute_the_service_runs(store: Store) -> None:
    run_service(store, frames=60)  # a reading per pass of the loop: five seconds each in this clock
    rows = stored(store)
    assert len(rows) >= 5
    assert [r.minute_start for r in rows] == sorted(
        {r.minute_start for r in rows}
    )  # one per minute
    assert all(r.minute_start % 60 == 0 for r in rows)
    full = rows[1:-1]  # not the first, which began part way into a minute, nor the last, cut short
    assert full and all(r.samples == 12 for r in full)
    assert all(r.camera_ok_samples == r.samples for r in full)
    assert all(r.fps_mean is not None and r.fps_mean > 0 for r in full)


def test_the_minute_in_progress_is_stored_when_the_service_stops(store: Store) -> None:
    run_service(store, frames=3)  # under half a minute of this clock: none is over, one is begun
    rows = stored(store)
    assert len(rows) == 1 and rows[0].samples >= 3


def test_a_camera_that_will_not_start_is_recorded_as_minutes_with_no_camera(store: Store) -> None:
    """The wait for a camera is where an outage is spent, so that is where the readings must go on:
    otherwise an outage would look like a service that was not running."""

    class DeadSource(ScriptedSource):
        def start(self) -> None:
            raise RuntimeError("camera busy")

        def stop(self) -> None: ...

    source = DeadSource(car_crossing(5))
    service = VisionService(
        source,
        ScriptedDetector(source),
        store,
        clock=WallClock(wall_ns=StepClock()),
        config=ServiceConfig(metrics_every_s=0.0, camera_retry_s=0.001, camera_giveup_s=10**6),
    )
    stop = StopRequest()
    thread = threading.Thread(target=service.run, args=(stop,))
    thread.start()
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline and len(stored(store)) < 3:
        time.sleep(0.02)
    stop.request()
    thread.join(10)
    rows = stored(store)
    assert len(rows) >= 3
    assert all(r.camera_ok_samples == 0 and r.fps_mean is None and r.fps_min is None for r in rows)
    assert all(r.samples > 0 for r in rows)


def test_a_database_that_fails_neither_stops_the_loop_nor_loses_the_minutes(
    store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(service_module, "HEALTH_RETRY_S", 0.0)  # try again on the next reading
    real = store.add_health
    attempts: list[int] = []

    def flaky(rows):  # type: ignore[no-untyped-def]
        attempts.append(len(rows))
        if len(attempts) <= 2:
            raise RuntimeError("database is locked")
        real(rows)

    monkeypatch.setattr(store, "add_health", flaky)
    service = run_service(store, frames=60)
    assert service.frames == 60  # the camera loop did not notice
    assert len(attempts) >= 3 and len(stored(store)) >= 5  # and what failed was kept and written


def test_health_older_than_thirty_days_is_deleted_and_newer_is_kept(store: Store) -> None:
    day_s = 86_400
    now_s = START_MS // 1000
    store.add_health(
        [
            HealthRow(now_s - 31 * day_s, 70.0, 20.0, 30.0, 30.0, 0, 12, 12),
            HealthRow(now_s - 29 * day_s, 70.0, 20.0, 30.0, 30.0, 0, 12, 12),
        ]
    )
    run_service(store, frames=3)  # the clean-up runs when the service starts
    minutes = [r.minute_start for r in stored(store)]
    assert now_s - 31 * day_s not in minutes
    assert (now_s - 29 * day_s) // 60 * 60 in minutes
