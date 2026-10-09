"""The health table and the store's way to it (plan.md M8 step 4)."""

import pytest

from trafficpi.db import create_db_engine, upgrade_to_head
from trafficpi.db.store import Store
from trafficpi.vision.health import HealthRow


@pytest.fixture
def store() -> Store:
    engine = create_db_engine(":memory:")
    upgrade_to_head(engine)
    s = Store(engine)
    s.ensure_camera()
    return s


def row(minute_start: int, **over: object) -> HealthRow:
    fields: dict[str, object] = {
        "minute_start": minute_start,
        "temp_c_max": 72.5,
        "cpu_percent": 21.0,
        "fps_mean": 30.0,
        "fps_min": 28.5,
        "throttled": 0,
        "samples": 12,
        "camera_ok_samples": 12,
    }
    return HealthRow(**{**fields, **over})  # type: ignore[arg-type]


def test_rows_come_back_as_they_went_in_and_in_time_order(store: Store) -> None:
    store.add_health([row(300), row(120, temp_c_max=None, fps_mean=None, fps_min=None), row(180)])
    got = store.health_between(0, 1_000)
    assert [r.minute_start for r in got] == [120, 180, 300]
    assert got[0] == row(120, temp_c_max=None, fps_mean=None, fps_min=None)  # nulls stay null
    assert got[1] == row(180)


def test_a_range_is_from_inclusive_to_exclusive(store: Store) -> None:
    store.add_health([row(m) for m in (60, 120, 180, 240)])
    assert [r.minute_start for r in store.health_between(120, 240)] == [120, 180]


def test_a_minute_that_is_written_twice_is_replaced_not_doubled(store: Store) -> None:
    """The clock can be set back, and a write that failed half way is tried again."""
    store.add_health([row(120, temp_c_max=70.0)])
    store.add_health([row(120, temp_c_max=81.0, samples=7)])
    got = store.health_between(0, 1_000)
    assert len(got) == 1 and got[0].temp_c_max == 81.0 and got[0].samples == 7


def test_nothing_to_add_is_fine(store: Store) -> None:
    store.add_health([])
    assert store.health_between(0, 10**9) == []


def test_old_rows_are_deleted_a_few_at_a_time(store: Store) -> None:
    store.add_health([row(60 * m) for m in range(1, 11)])  # minutes 1 to 10
    assert store.delete_health_before(60 * 6, limit=3) == 3  # the oldest three, at most
    assert store.delete_health_before(60 * 6, limit=100) == 2  # the rest of those before minute 6
    assert [r.minute_start for r in store.health_between(0, 10**9)] == [360, 420, 480, 540, 600]


def test_health_belongs_to_a_camera(store: Store) -> None:
    store.add_health([row(120)])
    other = Store(store._engine, camera_id="other")
    other.ensure_camera()
    assert other.health_between(0, 10**9) == []
    assert other.delete_health_before(10**9, limit=100) == 0
    assert len(store.health_between(0, 10**9)) == 1


def test_the_first_minute_ever_recorded_is_known_for_the_daily_email(store: Store) -> None:
    assert store.first_health_s() is None
    store.add_health([row(300), row(120), row(180)])
    assert store.first_health_s() == 120
