import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete
from websockets.sync.client import connect

from tests.fakes import ScriptedDetector, ScriptedSource, car_crossing
from trafficpi.api.app import create_app
from trafficpi.api.auth import COOKIE_NAME, hash_password
from trafficpi.api.settings import ApiSettings
from trafficpi.api.vision_client import VisionClient
from trafficpi.db import create_db_engine, upgrade_to_head
from trafficpi.db.models import User
from trafficpi.db.store import Store
from trafficpi.vision.rules.base import CountEvent
from trafficpi.vision.server import ServerThread
from trafficpi.vision.server import create_app as create_vision_app
from trafficpi.vision.service import VisionService
from trafficpi.vision.shutdown import StopRequest

PASSWORD = "correct horse battery"
LINES = {"lines": [{"id": "north", "name": "Northbound", "a": [0.2, 0.5], "b": [0.8, 0.5]}]}


class FakeVision:
    """A vision service that answers from a script, standing in for the real one."""

    def __init__(self) -> None:
        self.up = True
        self.reloads = 0

    def handler(self, request: httpx.Request) -> httpx.Response:
        if not self.up:
            raise httpx.ConnectError("refused")
        path = request.url.path
        if path == "/state":
            return httpx.Response(
                200,
                json={
                    "camera": {"ok": True, "error": None},
                    "fps": 29.9,
                    "uptime_s": 12.0,
                    "profile_version": 3,
                    "detector": "yolov8s_h8l",
                    "system": {"cpu_percent": 20.0},
                },
            )
        if path == "/reload":
            self.reloads += 1
            return httpx.Response(200, json={"requested": True})
        if path == "/snapshot.jpg":
            return httpx.Response(200, content=b"\xff\xd8jpeg\xff\xd9")
        if path == "/preview.mjpg":
            return httpx.Response(
                200,
                headers={"content-type": "multipart/x-mixed-replace; boundary=frame"},
                stream=httpx.ByteStream(b"--frame\r\n"),
            )
        return httpx.Response(404)


@pytest.fixture
def vision() -> FakeVision:
    return FakeVision()


@pytest.fixture
def store() -> Store:
    engine = create_db_engine(":memory:")
    upgrade_to_head(engine)
    s = Store(engine)
    s.ensure_camera()
    s.create_user("admin", hash_password(PASSWORD))
    return s


def make_client(tmp_path: Path, store: Store, vision: FakeVision, **settings: object) -> TestClient:
    config = ApiSettings(data_dir=tmp_path, dashboard_dir=None, **settings)  # type: ignore[arg-type]
    client_for_vision = VisionClient("http://vision", transport=httpx.MockTransport(vision.handler))
    app = create_app(config, store=store, vision=client_for_vision)
    return TestClient(app, base_url="http://testserver")


@pytest.fixture
def client(tmp_path: Path, store: Store, vision: FakeVision):  # type: ignore[no-untyped-def]
    with make_client(tmp_path, store, vision) as c:
        yield c


@pytest.fixture
def logged_in(client: TestClient) -> TestClient:
    response = client.post("/api/auth/login", json={"username": "admin", "password": PASSWORD})
    assert response.status_code == 204
    return client


def count(store: Store, line: str, direction: int, label: str, at_ms: int) -> None:
    store.record_count(CountEvent(0, 1, 2, label, line, direction), at_ms)


# --- login ------------------------------------------------------------------------------------


def test_health_and_auth_state_are_public(client: TestClient, store: Store) -> None:
    assert client.get("/api/health").json() == {"status": "ok"}
    assert client.get("/api/auth/state").json() == {"configured": True}


def test_auth_state_says_when_no_password_is_set(tmp_path: Path, vision: FakeVision) -> None:
    engine = create_db_engine(":memory:")
    upgrade_to_head(engine)
    with make_client(tmp_path, Store(engine), vision) as c:
        assert c.get("/api/auth/state").json() == {"configured": False}


def test_every_data_endpoint_needs_a_login(client: TestClient) -> None:
    paths = [
        "/api/auth/me",
        "/api/status",
        "/api/stats/today",
        "/api/stats/series?from_ms=0&to_ms=3600000",
        "/api/events",
        "/api/profile",
        "/api/settings",
        "/api/snapshot.jpg",
        "/api/preview.mjpg",
    ]
    for path in paths:
        assert client.get(path).status_code == 401, path
    assert client.put("/api/profile", json=LINES).status_code == 401
    assert client.put("/api/settings", json={"timezone": "UTC", "units": "mph"}).status_code == 401


def test_login_sets_a_strict_http_only_cookie_and_me_works(client: TestClient) -> None:
    response = client.post("/api/auth/login", json={"username": "admin", "password": PASSWORD})
    assert response.status_code == 204
    cookie = response.headers["set-cookie"].lower()
    assert COOKIE_NAME in cookie and "httponly" in cookie and "samesite=strict" in cookie
    assert "secure" not in cookie  # plain HTTP on the LAN
    assert client.get("/api/auth/me").json() == {"username": "admin"}


def test_the_cookie_is_secure_behind_https(client: TestClient) -> None:
    response = client.post(
        "/api/auth/login",
        json={"username": "admin", "password": PASSWORD},
        headers={"x-forwarded-proto": "https"},
    )
    assert "secure" in response.headers["set-cookie"].lower()


def test_wrong_password_and_unknown_user_look_the_same(client: TestClient) -> None:
    wrong = client.post("/api/auth/login", json={"username": "admin", "password": "nope" * 4})
    unknown = client.post("/api/auth/login", json={"username": "nobody", "password": PASSWORD})
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == unknown.json()
    assert COOKIE_NAME not in wrong.headers.get("set-cookie", "")


def test_logout_ends_the_session(logged_in: TestClient) -> None:
    assert logged_in.post("/api/auth/logout").status_code == 204
    assert logged_in.get("/api/auth/me").status_code == 401


def test_a_forged_or_tampered_cookie_is_refused(client: TestClient) -> None:
    client.cookies.set(COOKIE_NAME, "garbage")
    assert client.get("/api/auth/me").status_code == 401


def test_a_session_for_a_deleted_user_stops_working(logged_in: TestClient, store: Store) -> None:
    with store._session() as session:
        session.execute(delete(User))
    assert logged_in.get("/api/auth/me").status_code == 401


def test_repeated_failures_are_throttled_then_cleared_by_success(
    tmp_path: Path, store: Store, vision: FakeVision
) -> None:
    with make_client(tmp_path, store, vision, login_attempts=3) as c:
        bad = {"username": "admin", "password": "wrong password!"}
        assert [c.post("/api/auth/login", json=bad).status_code for _ in range(3)] == [401] * 3
        blocked = c.post("/api/auth/login", json=bad)
        assert blocked.status_code == 429 and int(blocked.headers["retry-after"]) >= 1
        # Even the right password is refused while blocked.
        good = {"username": "admin", "password": PASSWORD}
        assert c.post("/api/auth/login", json=good).status_code == 429


def test_the_throttle_follows_the_username_across_addresses_when_proxied(
    tmp_path: Path, store: Store, vision: FakeVision
) -> None:
    with make_client(tmp_path, store, vision, login_attempts=2, trust_proxy_headers=True) as c:
        bad = {"username": "admin", "password": "wrong password!"}
        for ip in ("1.1.1.1", "2.2.2.2"):
            assert (
                c.post("/api/auth/login", json=bad, headers={"cf-connecting-ip": ip}).status_code
                == 401
            )
        other_ip = c.post("/api/auth/login", json=bad, headers={"cf-connecting-ip": "3.3.3.3"})
        assert other_ip.status_code == 429  # same username, new address


def test_forwarded_addresses_are_ignored_unless_trusted(
    tmp_path: Path, store: Store, vision: FakeVision
) -> None:
    with make_client(tmp_path, store, vision, login_attempts=2) as c:
        bad = {"username": "someone", "password": "wrong password!"}
        for i in range(2):
            c.post("/api/auth/login", json=bad, headers={"x-forwarded-for": f"9.9.9.{i}"})
        # Spoofed headers did not give a fresh address: the real one is blocked.
        other_user = {"username": "else", "password": "wrong password!"}
        response = c.post(
            "/api/auth/login", json=other_user, headers={"x-forwarded-for": "8.8.8.8"}
        )
        assert response.status_code == 429


# --- cross-cutting ---------------------------------------------------------------------


def test_cross_origin_writes_are_refused_even_when_logged_in(logged_in: TestClient) -> None:
    evil = {"origin": "http://evil.example"}
    assert logged_in.put("/api/profile", json=LINES, headers=evil).status_code == 403
    assert logged_in.post("/api/auth/logout", headers=evil).status_code == 403
    same = {"origin": "http://testserver"}
    assert logged_in.put("/api/profile", json=LINES, headers=same).status_code == 200


def test_security_headers_and_no_store_on_api_responses(client: TestClient) -> None:
    response = client.get("/api/health")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
    assert response.headers["cache-control"] == "no-store"


def test_without_a_built_dashboard_the_root_says_so(client: TestClient) -> None:
    assert "dashboard has not been built" in client.get("/").json()["detail"]


def test_the_dashboard_directory_is_served_and_never_shadows_the_api(
    tmp_path: Path, store: Store, vision: FakeVision
) -> None:
    site = tmp_path / "site"
    site.mkdir()
    (site / "index.html").write_text("<h1>dash</h1>")
    config = ApiSettings(data_dir=tmp_path, dashboard_dir=site)
    transport = httpx.MockTransport(vision.handler)
    app = create_app(config, store=store, vision=VisionClient("http://v", transport=transport))
    with TestClient(app) as c:
        assert "<h1>dash</h1>" in c.get("/").text
        assert c.get("/api/health").json() == {"status": "ok"}


# --- status -----------------------------------------------------------------------------------


def test_status_reports_the_vision_service_disk_and_events(
    logged_in: TestClient, store: Store
) -> None:
    count(store, "north", 1, "car", 1_700_000_000_000)
    body = logged_in.get("/api/status").json()
    assert body["vision"]["status"] == "ok" and body["vision"]["fps"] == 29.9
    assert body["vision"]["detector"] == "yolov8s_h8l" and body["vision"]["profile_version"] == 3
    assert body["events_total"] == 1 and body["disk"]["total_gb"] > 0


def test_status_shows_an_unreachable_vision_service_without_failing(
    logged_in: TestClient, vision: FakeVision
) -> None:
    vision.up = False
    response = logged_in.get("/api/status")
    assert response.status_code == 200 and response.json()["vision"]["status"] == "unreachable"


# --- statistics -------------------------------------------------------------------------------


def test_today_counts_by_line_direction_and_class(logged_in: TestClient, store: Store) -> None:
    logged_in.put("/api/settings", json={"timezone": "UTC", "units": "mph"})
    logged_in.put("/api/profile", json=LINES)
    status = logged_in.get("/api/status").json()
    now = status["now_ms"]
    count(store, "north", 1, "car", now)
    count(store, "north", 1, "truck", now)
    count(store, "north", -1, "car", now)
    today = logged_in.get("/api/stats/today").json()
    assert today["total"] == 3 and today["timezone"] == "UTC"
    (line,) = today["lines"]
    assert line["name"] == "Northbound" and line["forward"] == 2 and line["reverse"] == 1
    assert line["by_class"] == {"car": 2, "truck": 1}
    assert len(today["hourly"]) >= 1


def test_series_buckets_and_validates_the_range(logged_in: TestClient, store: Store) -> None:
    logged_in.put("/api/settings", json={"timezone": "UTC", "units": "mph"})
    base = 1_700_000_000_000 // 3_600_000 * 3_600_000  # the start of an hour
    count(store, "north", 1, "car", base + 5 * 60_000)
    count(store, "north", 1, "car", base + 7 * 60_000)
    count(store, "north", 1, "car", base + 3_600_000 + 60_000)
    rows = logged_in.get(
        "/api/stats/series", params={"from_ms": base, "to_ms": base + 2 * 3_600_000}
    ).json()
    assert [r["total"] for r in rows] == [2, 1]
    backwards = logged_in.get("/api/stats/series", params={"from_ms": 10, "to_ms": 5})
    assert backwards.status_code == 422
    too_long = logged_in.get(
        "/api/stats/series", params={"from_ms": 0, "to_ms": 10 * 86400_000, "bucket": "minute"}
    )
    assert too_long.status_code == 422
    negative = logged_in.get("/api/stats/series", params={"from_ms": -1, "to_ms": 5})
    assert negative.status_code == 422


# --- events -----------------------------------------------------------------------------------


def test_events_are_newest_first_and_page_with_before_id(
    logged_in: TestClient, store: Store
) -> None:
    for i in range(5):
        count(store, "north", 1, "car", 1_700_000_000_000 + i * 1000)
    page = logged_in.get("/api/events", params={"limit": 2}).json()
    assert [e["t"] for e in page] == [1_700_000_004_000, 1_700_000_003_000]
    assert page[0]["line"] == "north" and page[0]["label"] == "car" and page[0]["direction"] == 1
    older = logged_in.get("/api/events", params={"limit": 10, "before_id": page[-1]["id"]}).json()
    assert len(older) == 3 and older[0]["t"] == 1_700_000_002_000
    assert logged_in.get("/api/events", params={"limit": 0}).status_code == 422
    assert logged_in.get("/api/events", params={"limit": 501}).status_code == 422
    assert logged_in.get("/api/events", params={"type": "stop"}).json() == []


# --- profile and settings ---------------------------------------------------------------------


def test_profile_starts_empty_then_saves_and_tells_the_vision_service(
    logged_in: TestClient, vision: FakeVision
) -> None:
    assert logged_in.get("/api/profile").json() == {"version": 0, "document": {"lines": []}}
    saved = logged_in.put("/api/profile", json=LINES).json()
    assert saved == {"version": 1, "vision_notified": True} and vision.reloads == 1
    assert logged_in.get("/api/profile").json() == {"version": 1, "document": LINES}
    assert logged_in.put("/api/profile", json=LINES).json()["version"] == 2


def test_a_profile_is_saved_even_when_the_vision_service_is_down(
    logged_in: TestClient, vision: FakeVision
) -> None:
    vision.up = False
    saved = logged_in.put("/api/profile", json=LINES).json()
    assert saved == {"version": 1, "vision_notified": False}


@pytest.mark.parametrize(
    "document",
    [
        {},
        {"lines": "north"},
        {"lines": [{"id": "x", "name": "x", "a": [0, 0], "b": [2, 2]}]},
        {"lines": [{"id": "x", "name": "x", "a": [0, 0], "b": [1, 1], "classes": ["unicorn"]}]},
    ],
)
def test_an_unusable_profile_is_rejected_and_not_stored(
    logged_in: TestClient, vision: FakeVision, document: dict[str, object]
) -> None:
    assert logged_in.put("/api/profile", json=document).status_code == 422
    assert logged_in.get("/api/profile").json()["version"] == 0 and vision.reloads == 0


def test_settings_default_save_and_validate(logged_in: TestClient) -> None:
    assert logged_in.get("/api/settings").json()["units"] == "mph"
    saved = logged_in.put("/api/settings", json={"timezone": "Europe/Paris", "units": "kmh"})
    assert saved.json() == {"timezone": "Europe/Paris", "units": "kmh"}
    assert logged_in.get("/api/settings").json() == {"timezone": "Europe/Paris", "units": "kmh"}
    bad = logged_in.put("/api/settings", json={"timezone": "Mars/Olympus", "units": "mph"})
    assert bad.status_code == 422
    assert (
        logged_in.put("/api/settings", json={"timezone": "UTC", "units": "furlongs"}).status_code
        == 422
    )


# --- relays -----------------------------------------------------------------------------------


def test_snapshot_and_preview_are_relayed(logged_in: TestClient) -> None:
    snapshot = logged_in.get("/api/snapshot.jpg")
    assert snapshot.headers["content-type"] == "image/jpeg" and snapshot.content.startswith(
        b"\xff\xd8"
    )
    preview = logged_in.get("/api/preview.mjpg")
    assert preview.headers["content-type"].startswith("multipart/x-mixed-replace")
    assert preview.content == b"--frame\r\n"


def test_relays_answer_503_when_the_camera_is_unreachable(
    logged_in: TestClient, vision: FakeVision
) -> None:
    vision.up = False
    assert logged_in.get("/api/snapshot.jpg").status_code == 503
    assert logged_in.get("/api/preview.mjpg").status_code == 503


def test_the_live_socket_refuses_visitors_who_are_not_logged_in(client: TestClient) -> None:
    from starlette.websockets import WebSocketDisconnect

    with pytest.raises(WebSocketDisconnect) as refused, client.websocket_connect("/api/live"):
        pass
    assert refused.value.code == 1008


def test_the_live_socket_refuses_a_foreign_origin(logged_in: TestClient) -> None:
    from starlette.websockets import WebSocketDisconnect

    with (
        pytest.raises(WebSocketDisconnect) as refused,
        logged_in.websocket_connect("/api/live", headers={"origin": "http://evil.example"}),
    ):
        pass
    assert refused.value.code == 1008


def test_the_live_socket_says_offline_when_the_vision_service_is_down(
    logged_in: TestClient,
) -> None:
    # Nothing listens on this port, so the relay reports "offline" and keeps retrying.
    logged_in.app.state.ctx.vision.base_url = "http://127.0.0.1:1"  # type: ignore[attr-defined]
    with logged_in.websocket_connect("/api/live") as socket:
        assert socket.receive_json() == {"type": "offline"}


# --- against a real vision service ------------------------------------------------------------


def test_the_api_works_against_a_real_vision_server(tmp_path: Path, store: Store) -> None:
    """Both services as real servers: login, status, snapshot, reload and the live socket."""
    source = ScriptedSource(car_crossing(30))
    service = VisionService(source, ScriptedDetector(source), store)
    service.run(StopRequest())
    vision_server = ServerThread(create_vision_app(service, live_hz=50), port=18742)
    config = ApiSettings(data_dir=tmp_path, dashboard_dir=None, vision_url="http://127.0.0.1:18742")
    api_server = ServerThread(create_app(config, store=store), port=18743)
    vision_server.start()
    api_server.start()
    try:
        base = "http://127.0.0.1:18743"
        with httpx.Client(base_url=base, timeout=5) as c:
            login = {"username": "admin", "password": PASSWORD}
            assert c.post("/api/auth/login", json=login).status_code == 204
            assert c.get("/api/status").json()["vision"]["status"] == "ok"
            assert c.get("/api/snapshot.jpg").status_code == 200
            assert c.put("/api/profile", json=LINES).json()["vision_notified"] is True
            assert service._reload.is_set()
            cookie = f"{COOKIE_NAME}={c.cookies[COOKIE_NAME]}"
        with connect(
            "ws://127.0.0.1:18743/api/live", additional_headers={"Cookie": cookie}
        ) as socket:
            message = json.loads(socket.recv(timeout=5))
        assert message["type"] == "state" and message["camera"]["ok"]
    finally:
        api_server.stop()
        vision_server.stop()
