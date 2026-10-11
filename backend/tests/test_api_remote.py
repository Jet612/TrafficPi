"""Viewers who come through Cloudflare.

Cloudflare's terms let it limit a customer who uses its CDN without a paid plan for video, and they
do not say whether a Tunnel counts, so the continuous live stream is held back from viewers who
arrive through Cloudflare unless the owner switches it on. Everything else works for them.
"""

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

PASSWORD = "correct horse battery"
LOGIN = {"username": "admin", "password": PASSWORD}
# What Cloudflare adds to every request it passes to the origin.
CLOUDFLARE = {"cf-ray": "9a1b2c3d4e5f6a7b-EWR", "cf-connecting-ip": "203.0.113.9"}


def a_vision_service(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path == "/preview.mjpg":
        return httpx.Response(
            200,
            headers={"content-type": "multipart/x-mixed-replace; boundary=frame"},
            stream=httpx.ByteStream(b"--frame\r\n"),
        )
    if path == "/snapshot.jpg":
        return httpx.Response(200, content=b"\xff\xd8jpeg\xff\xd9")
    return httpx.Response(404)


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
def app(tmp_path: Path, store: Store) -> FastAPI:
    vision = VisionClient("http://vision", transport=httpx.MockTransport(a_vision_service))
    return create_app(
        ApiSettings(data_dir=tmp_path, dashboard_dir=None), store=store, vision=vision
    )


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app, base_url="http://testserver") as c:
        assert c.post("/api/auth/login", json=LOGIN).status_code == 204
        yield c


def settings_body(client: TestClient, **changes: object) -> dict[str, object]:
    return {**client.get("/api/settings").json(), **changes}


# --- the switch ---------------------------------------------------------------------------------


def test_live_video_for_remote_viewers_is_off_until_the_owner_switches_it_on(
    client: TestClient,
) -> None:
    assert client.get("/api/settings").json()["remote_live_video"] is False
    saved = client.put("/api/settings", json=settings_body(client, remote_live_video=True))
    assert saved.status_code == 200 and saved.json()["remote_live_video"] is True
    assert client.get("/api/settings").json()["remote_live_video"] is True
    client.put("/api/settings", json=settings_body(client, remote_live_video=False))
    assert client.get("/api/settings").json()["remote_live_video"] is False


@pytest.mark.parametrize("value", ["yes", "true", 1, 0, None])
def test_the_switch_must_be_true_or_false(client: TestClient, value: object) -> None:
    body = settings_body(client, remote_live_video=value)
    assert client.put("/api/settings", json=body).status_code == 422
    assert client.get("/api/settings").json()["remote_live_video"] is False


# --- what a remote viewer may open --------------------------------------------------------------


def test_the_live_stream_is_held_back_from_viewers_who_come_through_cloudflare(
    client: TestClient,
) -> None:
    refused = client.get("/api/preview.mjpg", headers=CLOUDFLARE)
    assert refused.status_code == 403
    assert "Cloudflare" in refused.json()["detail"] and "Settings" in refused.json()["detail"]


def test_either_of_cloudflares_headers_marks_a_viewer_as_remote(client: TestClient) -> None:
    for header in ("cf-ray", "cf-connecting-ip"):
        refused = client.get("/api/preview.mjpg", headers={header: CLOUDFLARE[header]})
        assert refused.status_code == 403, header


def test_a_viewer_on_the_lan_gets_the_stream(client: TestClient) -> None:
    assert client.get("/api/preview.mjpg").status_code == 200


def test_other_proxies_do_not_count_as_cloudflare(client: TestClient) -> None:
    """The terms are Cloudflare's: a VPN or another reverse proxy is not held back."""
    headers = {
        "x-forwarded-for": "198.51.100.7",
        "x-forwarded-proto": "https",
        "forwarded": "for=x",
    }
    assert client.get("/api/preview.mjpg", headers=headers).status_code == 200


def test_the_owner_can_allow_the_stream_for_remote_viewers(client: TestClient) -> None:
    client.put("/api/settings", json=settings_body(client, remote_live_video=True))
    assert client.get("/api/preview.mjpg", headers=CLOUDFLARE).status_code == 200
    client.put("/api/settings", json=settings_body(client, remote_live_video=False))
    assert client.get("/api/preview.mjpg", headers=CLOUDFLARE).status_code == 403


def test_everything_else_works_for_remote_viewers(client: TestClient) -> None:
    """Stats, snapshots and the live socket's data are not video; short clips are let through."""
    for path in ("/api/status", "/api/stats/today", "/api/settings", "/api/events"):
        assert client.get(path, headers=CLOUDFLARE).status_code == 200, path
    snapshot = client.get("/api/snapshot.jpg", headers=CLOUDFLARE)
    assert snapshot.status_code == 200 and snapshot.headers["content-type"] == "image/jpeg"
    with client.websocket_connect("/api/live", headers=CLOUDFLARE) as socket:
        assert socket.receive_json()["type"] in ("offline", "state")


def test_a_lan_client_that_forges_the_header_only_restricts_itself(client: TestClient) -> None:
    """The header holds back bandwidth; it is not a login. Claiming to be remote never opens
    anything that being local would not."""
    assert client.get("/api/status", headers=CLOUDFLARE).status_code == 200
    assert client.get("/api/preview.mjpg", headers=CLOUDFLARE).status_code == 403
    anonymous = TestClient(client.app)
    assert anonymous.get("/api/status", headers=CLOUDFLARE).status_code == 401


# --- what the dashboard is told -----------------------------------------------------------------


def test_the_dashboard_is_told_whether_this_viewer_is_remote_and_may_have_the_stream(
    client: TestClient,
) -> None:
    assert client.get("/api/auth/me").json() == {
        "username": "admin",
        "remote": False,
        "live_video": True,
    }
    held_back = client.get("/api/auth/me", headers=CLOUDFLARE).json()
    assert held_back == {"username": "admin", "remote": True, "live_video": False}
    client.put("/api/settings", json=settings_body(client, remote_live_video=True))
    allowed = client.get("/api/auth/me", headers=CLOUDFLARE).json()
    assert allowed == {"username": "admin", "remote": True, "live_video": True}
