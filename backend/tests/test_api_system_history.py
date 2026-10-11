"""GET /api/system/history: the machine's past, for the System page."""

import time
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from trafficpi.api.app import create_app
from trafficpi.api.auth import hash_password
from trafficpi.api.settings import ApiSettings
from trafficpi.api.vision_client import VisionClient
from trafficpi.db import create_db_engine, upgrade_to_head
from trafficpi.db.store import Store
from trafficpi.vision.health import HealthRow


@pytest.fixture(scope="module")
def password_hash() -> str:
    return hash_password("correct horse battery")


@pytest.fixture
def store(password_hash: str) -> Store:
    engine = create_db_engine(":memory:")
    upgrade_to_head(engine)
    s = Store(engine)
    s.ensure_camera()
    s.create_user("admin", password_hash)
    return s


def an_app(store: Store, tmp_path: Path) -> FastAPI:
    vision = VisionClient(
        "http://vision", transport=httpx.MockTransport(lambda _: httpx.Response(404))
    )
    return create_app(
        ApiSettings(data_dir=tmp_path, dashboard_dir=None), store=store, vision=vision
    )


@pytest.fixture
def client(store: Store, tmp_path: Path) -> Iterator[TestClient]:
    with TestClient(an_app(store, tmp_path)) as c:
        login = {"username": "admin", "password": "correct horse battery"}
        assert c.post("/api/auth/login", json=login).status_code == 204
        yield c


def minutes_ago(n: int, **fields: object) -> HealthRow:
    """A minute that ended `n` minutes ago (1: the minute before the one in progress)."""
    start = int(time.time()) // 60 * 60 - n * 60
    values: dict[str, object] = {
        "minute_start": start,
        "temp_c_max": 71.0,
        "cpu_percent": 25.0,
        "fps_mean": 30.0,
        "fps_min": 29.0,
        "throttled": 0,
        "samples": 12,
        "camera_ok_samples": 12,
    }
    values.update(fields)
    return HealthRow(**values)  # type: ignore[arg-type]


def test_it_needs_a_login(store: Store, tmp_path: Path) -> None:
    with TestClient(an_app(store, tmp_path)) as anonymous:
        assert anonymous.get("/api/system/history").status_code == 401


@pytest.mark.parametrize("hours", [0, -1, 721, "week"])
def test_a_period_that_makes_no_sense_is_refused(client: TestClient, hours: int | str) -> None:
    assert client.get("/api/system/history", params={"hours": hours}).status_code == 422


def test_a_new_install_with_nothing_recorded_gets_an_empty_but_complete_answer(
    client: TestClient,
) -> None:
    body = client.get("/api/system/history").json()
    assert body["timezone"]  # the clock the chart is read by
    assert body["bucket_s"] == 600  # a day, in ten-minute points
    assert len(body["points"]) == 144
    assert all(p["minutes"] == 0 and p["temp_c"] is None for p in body["points"])
    assert body["episodes"] == []
    assert body["summary"]["minutes"] == 0 and body["summary"]["temp_c_max"] is None


def test_the_recorded_minutes_are_the_points_and_a_slowdown_is_an_episode(
    client: TestClient, store: Store
) -> None:
    store.add_health(
        [
            minutes_ago(6),
            minutes_ago(5, throttled=0b0100, temp_c_max=82.5),
            minutes_ago(4, throttled=0b1100, temp_c_max=85.1),
            minutes_ago(3),
            minutes_ago(2, camera_ok_samples=0, fps_mean=None, fps_min=None),
            minutes_ago(1),
        ]
    )
    body = client.get("/api/system/history", params={"hours": 1}).json()
    assert body["bucket_s"] == 60 and len(body["points"]) == 60
    recorded = [p for p in body["points"] if p["minutes"]]
    assert len(recorded) == 6
    assert max(p["temp_c"] for p in recorded) == 85.1
    assert body["summary"]["minutes"] == 6 and body["summary"]["temp_c_max"] == 85.1
    assert body["summary"]["slowed_minutes"] == 2
    [slowed] = [e for e in body["episodes"] if e["kind"] == "slowed"]
    assert slowed["peak_temp_c"] == 85.1 and slowed["ongoing"] is False
    assert slowed["to_s"] - slowed["from_s"] == 120
    # The minute that just ended is the last point (or, if a minute turned over between storing
    # the rows and asking, the one before it).
    assert [p["minutes"] for p in body["points"][-2:]] in ([1, 1], [1, 0])


def test_a_longer_period_gives_coarser_points_over_the_same_minutes(
    client: TestClient, store: Store
) -> None:
    store.add_health([minutes_ago(n) for n in range(1, 40)])
    body = client.get("/api/system/history", params={"hours": 168}).json()
    assert body["bucket_s"] == 3600
    assert sum(p["minutes"] for p in body["points"]) == 39
    assert body["summary"]["minutes"] == 39


def test_minutes_from_before_the_period_are_left_out(client: TestClient, store: Store) -> None:
    store.add_health([minutes_ago(1), minutes_ago(3 * 60)])
    body = client.get("/api/system/history", params={"hours": 1}).json()
    assert body["summary"]["minutes"] == 1
