"""The Settings API carries the switch for looking for people (plan.md D34)."""

from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from trafficpi.api.app import create_app
from trafficpi.api.auth import hash_password
from trafficpi.api.settings import ApiSettings
from trafficpi.api.vision_client import VisionClient
from trafficpi.db import create_db_engine, upgrade_to_head
from trafficpi.db.store import Store

PASSWORD = "correct horse battery"


@pytest.fixture(scope="module")
def password_hash() -> str:
    return hash_password(PASSWORD)


@pytest.fixture
def store(password_hash: str) -> Store:
    engine = create_db_engine(":memory:")
    upgrade_to_head(engine)
    s = Store(engine)
    s.ensure_camera()
    s.create_user("admin", password_hash)
    return s


@pytest.fixture
def reloads() -> list[str]:
    return []


@pytest.fixture
def client(store: Store, tmp_path: Path, reloads: list[str]) -> Iterator[TestClient]:
    def vision(request: httpx.Request) -> httpx.Response:
        reloads.append(request.url.path)
        return httpx.Response(200, json={"ok": True})

    app = create_app(
        ApiSettings(data_dir=tmp_path, dashboard_dir=None),
        store=store,
        vision=VisionClient("http://vision", transport=httpx.MockTransport(vision)),
    )
    with TestClient(app) as c:
        assert (
            c.post("/api/auth/login", json={"username": "admin", "password": PASSWORD}).status_code
            == 204
        )
        yield c


def body(client: TestClient, **changes: object) -> dict[str, object]:
    return {**client.get("/api/settings").json(), **changes}


def test_people_are_looked_for_until_the_owner_says_not_to(client: TestClient) -> None:
    assert client.get("/api/settings").json()["detect_people"] is True


def test_the_switch_is_saved_and_the_vision_service_is_told_to_reload(
    client: TestClient, store: Store, reloads: list[str]
) -> None:
    saved = client.put("/api/settings", json=body(client, detect_people=False))
    assert saved.status_code == 200 and saved.json()["detect_people"] is False
    assert client.get("/api/settings").json()["detect_people"] is False
    assert store.get_setting("detect_people") is False  # what the vision service reads
    assert reloads == ["/reload"]  # so that it stops looking at once, with no restart
    client.put("/api/settings", json=body(client, detect_people=True))
    assert client.get("/api/settings").json()["detect_people"] is True


@pytest.mark.parametrize("value", ["no", "false", 0, 1, None])
def test_the_switch_must_be_true_or_false(client: TestClient, value: object) -> None:
    assert client.put("/api/settings", json=body(client, detect_people=value)).status_code == 422
    assert client.get("/api/settings").json()["detect_people"] is True


def test_a_settings_body_without_the_switch_is_refused_not_read_as_on(client: TestClient) -> None:
    """A PUT replaces everything, so a missing member is a mistake in the sender, not a default."""
    incomplete = body(client)
    del incomplete["detect_people"]
    assert client.put("/api/settings", json=incomplete).status_code == 422


def test_a_damaged_stored_value_reads_as_looking_for_people(
    client: TestClient, store: Store
) -> None:
    store.set_setting("detect_people", "off")
    assert client.get("/api/settings").json()["detect_people"] is True
