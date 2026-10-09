"""The security pass (plan.md D31): what a client that is not logged in, or a hostile one, can do.

Each test here was written to fail on the code as it was before the pass, and passes on the code
after it (the commit messages say which gap each one closes).
"""

import asyncio
import base64
import hashlib
import json
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx2 import Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from trafficpi.api.app import create_app
from trafficpi.api.auth import hash_password
from trafficpi.api.limits import BodyLimit
from trafficpi.api.settings import ApiSettings
from trafficpi.api.vision_client import VisionClient
from trafficpi.db import create_db_engine, upgrade_to_head
from trafficpi.db.store import Store

PASSWORD = "correct horse battery"
LOGIN = {"username": "admin", "password": PASSWORD}


@pytest.fixture(scope="module")
def password_hash() -> str:
    return hash_password(PASSWORD)  # hashing is slow on purpose: once per module


@pytest.fixture
def store(password_hash: str) -> Store:
    engine = create_db_engine(":memory:")
    upgrade_to_head(engine)
    s = Store(engine)
    s.ensure_camera()
    s.create_user("admin", password_hash)
    return s


def make_app(tmp_path: Path, store: Store, **settings: object) -> FastAPI:
    vision = VisionClient(
        "http://vision", transport=httpx.MockTransport(lambda request: httpx.Response(404))
    )
    settings.setdefault("dashboard_dir", None)
    config = ApiSettings(data_dir=tmp_path, **settings)  # type: ignore[arg-type]
    return create_app(config, store=store, vision=vision)


@pytest.fixture
def app(tmp_path: Path, store: Store) -> FastAPI:
    return make_app(tmp_path, store)


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app, base_url="http://testserver") as c:
        yield c


@pytest.fixture
def logged_in(client: TestClient) -> TestClient:
    assert client.post("/api/auth/login", json=LOGIN).status_code == 204
    return client


# --- a request body has a size limit ------------------------------------------------------------


def test_a_login_with_a_huge_body_is_refused_not_read(client: TestClient) -> None:
    padding = "x" * (2 * 1024 * 1024)
    response = client.post("/api/auth/login", json={**LOGIN, "padding": padding})
    assert response.status_code == 413
    assert "too large" in response.json()["detail"]


def test_a_chunked_body_is_refused_too(client: TestClient) -> None:
    def chunks() -> Iterator[bytes]:  # no Content-Length: the client streams it
        for _ in range(2_000):
            yield b"x" * 1024

    response = client.post(
        "/api/auth/login", content=chunks(), headers={"content-type": "application/json"}
    )
    assert response.status_code == 413


def test_an_ordinary_login_is_nowhere_near_the_limit(client: TestClient) -> None:
    assert client.post("/api/auth/login", json=LOGIN).status_code == 204


def test_a_profile_may_be_larger_than_other_bodies_but_not_without_end(
    logged_in: TestClient,
) -> None:
    # 100 KB: far more than a login, and a profile is allowed to be this big (the answer is the
    # profile's own, not the size limit's).
    big_name = "n" * (100 * 1024)
    response = logged_in.put("/api/profile", json={"lines": [{"id": "a", "name": big_name}]})
    assert response.status_code != 413
    huge = logged_in.put("/api/profile", json={"padding": "x" * (3 * 1024 * 1024)})
    assert huge.status_code == 413
    check = logged_in.post("/api/profile/check", json={"padding": "x" * (3 * 1024 * 1024)})
    assert check.status_code == 413


def test_the_other_endpoints_that_take_a_body_have_the_small_limit(logged_in: TestClient) -> None:
    settings = {"timezone": "UTC", "units": "mph", "padding": "x" * (200 * 1024)}
    assert logged_in.put("/api/settings", json=settings).status_code == 413
    review = {"state": "confirmed", "padding": "x" * (200 * 1024)}
    assert logged_in.put("/api/stops/1/review", json=review).status_code == 413


# --- logins cannot use up the machine -----------------------------------------------------------


def test_a_burst_of_logins_cannot_hash_more_than_two_at_once(
    client: TestClient, app: FastAPI
) -> None:
    ctx = app.state.ctx
    with ctx.hashing.take(), ctx.hashing.take():  # two hashes are running
        busy = client.post("/api/auth/login", json=LOGIN)
        assert busy.status_code == 503 and int(busy.headers["retry-after"]) >= 1
        assert len(ctx.throttle) == 0  # being turned away is not a failed login
    assert client.post("/api/auth/login", json=LOGIN).status_code == 204


def test_many_logins_for_many_names_leave_the_throttle_small(tmp_path: Path, store: Store) -> None:
    app = make_app(tmp_path, store)
    with TestClient(app) as c:
        for i in range(200):
            c.post(
                "/api/auth/login", json={"username": f"nobody-{i}", "password": "wrong password!"}
            )
        assert len(app.state.ctx.throttle) <= 4_096


# --- a client cannot make up its address ----------------------------------------------------------


def wrong_logins(client: TestClient, headers_per_try: list[dict[str, str]]) -> list[int]:
    """One failed login per entry, each under a name of its own, so that only the client's address
    (and not the name) can ever block one."""
    return [
        client.post(
            "/api/auth/login",
            json={"username": f"name-{i}", "password": "wrong password!"},
            headers=headers,
        ).status_code
        for i, headers in enumerate(headers_per_try)
    ]


def test_by_default_only_this_machine_is_believed_about_addresses() -> None:
    assert ApiSettings().trusted_proxies == ("127.0.0.1", "::1")
    assert ApiSettings().trust_proxy_headers is False


def test_proxy_headers_from_a_stranger_do_not_give_a_fresh_address(
    tmp_path: Path, store: Store
) -> None:
    """With the headers switched on, a client on the LAN (not the proxy) still cannot invent an
    address for each try: the connection's own address is what counts."""
    app = make_app(tmp_path, store, login_attempts=3, trust_proxy_headers=True)
    with TestClient(app) as c:  # the test client connects from "testclient": not a trusted proxy
        tries = [
            {"cf-connecting-ip": f"7.7.7.{i}", "x-forwarded-for": f"8.8.8.{i}"} for i in range(5)
        ]
        assert wrong_logins(c, tries) == [401, 401, 401, 429, 429]  # one address, many claims


def test_proxy_headers_from_the_proxy_are_believed(tmp_path: Path, store: Store) -> None:
    app = make_app(
        tmp_path, store, login_attempts=2, trust_proxy_headers=True, trusted_proxies=("testclient",)
    )
    with TestClient(app) as c:
        tries = [{"cf-connecting-ip": ip} for ip in ("1.1.1.1", "1.1.1.1", "1.1.1.1", "2.2.2.2")]
        assert wrong_logins(c, tries) == [401, 401, 429, 401]  # a second address starts afresh


def test_the_address_the_proxy_saw_beats_one_the_client_wrote_in_x_forwarded_for(
    tmp_path: Path, store: Store
) -> None:
    """A client can send its own X-Forwarded-For, and the proxy appends the real address after
    it: the last entry is the one to believe, not the first."""
    app = make_app(
        tmp_path, store, login_attempts=2, trust_proxy_headers=True, trusted_proxies=("testclient",)
    )
    with TestClient(app) as c:
        tries = [{"x-forwarded-for": f"9.9.9.{i}, 5.5.5.5"} for i in range(3)]
        assert wrong_logins(c, tries) == [401, 401, 429]  # always 5.5.5.5, whatever came before it


def test_cloudflares_header_beats_x_forwarded_for(tmp_path: Path, store: Store) -> None:
    app = make_app(
        tmp_path, store, login_attempts=2, trust_proxy_headers=True, trusted_proxies=("testclient",)
    )
    with TestClient(app) as c:
        tries = [
            {"cf-connecting-ip": "4.4.4.4", "x-forwarded-for": f"6.6.6.{i}, 3.3.3.{i}"}
            for i in range(3)
        ]
        assert wrong_logins(c, tries) == [401, 401, 429]  # 4.4.4.4 every time


def test_a_proxy_header_that_is_not_an_address_is_ignored(tmp_path: Path, store: Store) -> None:
    app = make_app(
        tmp_path, store, login_attempts=2, trust_proxy_headers=True, trusted_proxies=("testclient",)
    )
    with TestClient(app) as c:
        junk = [{"cf-connecting-ip": "not an address " + "x" * i} for i in range(3)]
        assert wrong_logins(c, junk) == [401, 401, 429]  # judged by the connection, not the junk
        assert len(app.state.ctx.throttle) <= 6  # and the junk was not kept as a key


# --- what is logged about logins ------------------------------------------------------------------


def logged(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.name == "trafficpi.api"]


def test_a_failed_login_for_a_real_user_is_logged_with_the_address_but_not_the_password(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level("INFO", logger="trafficpi.api")
    wrong = "this is not the password"
    client.post("/api/auth/login", json={"username": "Admin", "password": wrong})
    messages = logged(caplog)
    assert any("failed login" in m and "'admin'" in m and "testclient" in m for m in messages)
    assert wrong not in caplog.text and PASSWORD not in caplog.text


def test_the_name_of_a_user_who_does_not_exist_is_not_logged(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    """People paste a password into the user name field now and then: it must not reach the log."""
    caplog.set_level("INFO", logger="trafficpi.api")
    client.post("/api/auth/login", json={"username": "hunter2-my-secret", "password": "x" * 12})
    messages = logged(caplog)
    assert any("failed login" in m and "unknown user" in m for m in messages)
    assert "hunter2-my-secret" not in caplog.text


def test_a_lockout_is_logged_once_when_it_starts_not_for_every_refused_try(
    tmp_path: Path, store: Store, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level("INFO", logger="trafficpi.api")
    app = make_app(tmp_path, store, login_attempts=3)
    with TestClient(app) as c:
        bad = {"username": "admin", "password": "wrong password!"}
        for _ in range(8):
            c.post("/api/auth/login", json=bad)
    messages = logged(caplog)
    assert sum(m.startswith("failed login for") for m in messages) == 3  # refused ones are not
    lockouts = [m for m in messages if "locked out" in m]
    assert len(lockouts) == 1 and "'admin'" in lockouts[0] and "testclient" in lockouts[0]


def test_a_successful_login_is_logged(client: TestClient, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level("INFO", logger="trafficpi.api")
    client.post("/api/auth/login", json=LOGIN)
    assert any("login" in m and "'admin'" in m and "testclient" in m for m in logged(caplog))
    assert PASSWORD not in caplog.text


def test_a_login_turned_away_for_want_of_a_hashing_slot_is_logged_but_not_every_time(
    client: TestClient, app: FastAPI, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level("INFO", logger="trafficpi.api")
    ctx = app.state.ctx
    with ctx.hashing.take(), ctx.hashing.take():
        for _ in range(5):
            assert client.post("/api/auth/login", json=LOGIN).status_code == 503
    assert sum("busy" in m for m in logged(caplog)) == 1


# --- the Content-Security-Policy and the other headers ------------------------------------------


def script_hash(text: str) -> str:
    return "'sha256-" + base64.b64encode(hashlib.sha256(text.encode()).digest()).decode() + "'"


INDEX_SCRIPTS = ["self.__next_f=[]", 'self.__next_f.push([1,"a < b && c"])\n']
HISTORY_SCRIPT = "history_only()"


@pytest.fixture
def site(tmp_path: Path) -> Path:
    """A little dashboard: pages with inline scripts, as Next's static export writes them."""
    root = tmp_path / "site"
    (root / "history").mkdir(parents=True)
    (root / "index.html").write_text(
        "<!DOCTYPE html><html><head>"
        f'<script>{INDEX_SCRIPTS[0]}</script><script src="/_next/a.js"></script>'
        f'</head><body><script>{INDEX_SCRIPTS[1]}</script><script type="application/json">'
        "</script></body></html>"
    )
    (root / "history" / "index.html").write_text(
        f"<html><body><script>{HISTORY_SCRIPT}</script></body></html>"
    )
    (root / "404.html").write_text(
        "<html><head><style>.a{color:red}</style></head><body><script>four04()</script></body></html>"
    )
    (root / "app.js").write_text("console.log(1)")
    (tmp_path / "secret.txt").write_text("not for the web")
    return root


@pytest.fixture
def site_client(tmp_path: Path, store: Store, site: Path) -> Iterator[TestClient]:
    app = make_app(tmp_path, store, dashboard_dir=site)
    with TestClient(app, base_url="http://testserver") as c:
        yield c


def policy_of(response: Response) -> dict[str, list[str]]:
    parts = [p.strip() for p in response.headers["content-security-policy"].split(";") if p.strip()]
    return {name: rest for name, *rest in (p.split() for p in parts)}


def test_a_page_is_sent_with_the_hashes_of_its_own_inline_scripts_and_no_unsafe_inline(
    site_client: TestClient,
) -> None:
    scripts = policy_of(site_client.get("/"))["script-src"]
    assert scripts[0] == "'self'"
    assert set(scripts[1:]) == {script_hash(s) for s in INDEX_SCRIPTS}  # not the empty JSON block
    assert "'unsafe-inline'" not in scripts


def test_another_page_gets_its_own_hashes_and_not_the_first_pages(site_client: TestClient) -> None:
    scripts = policy_of(site_client.get("/history/"))["script-src"]
    assert set(scripts[1:]) == {script_hash(HISTORY_SCRIPT)}


def test_the_not_found_page_has_a_policy_for_its_scripts_too(site_client: TestClient) -> None:
    response = site_client.get("/no/such/page/")
    assert response.status_code == 404 and "four04()" in response.text
    assert script_hash("four04()") in policy_of(response)["script-src"]


def test_a_rebuilt_page_gets_a_new_policy_without_restarting_the_server(
    site_client: TestClient, site: Path
) -> None:
    assert script_hash("replaced()") not in policy_of(site_client.get("/history/"))["script-src"]
    (site / "history" / "index.html").write_text("<script>replaced()</script>" + " " * 7)
    scripts = policy_of(site_client.get("/history/"))["script-src"]
    assert script_hash("replaced()") in scripts and script_hash(HISTORY_SCRIPT) not in scripts


def test_a_page_that_is_revalidated_keeps_its_own_policy(site_client: TestClient) -> None:
    """A reload asks the server whether the page has changed, and the answer is a 304 with no body.
    A browser merges the headers of that answer into the page it has kept, so a 304 that carried the
    default policy (which allows no inline script) made the reloaded page blank. A browser found this
    on the first reload; a test that only ever fetches each page once could not."""
    first = site_client.get("/history/")
    assert first.status_code == 200
    expected = policy_of(first)["script-src"]
    again = site_client.get("/history/", headers={"If-None-Match": first.headers["etag"]})
    assert again.status_code == 304
    assert policy_of(again)["script-src"] == expected
    by_date = site_client.get(
        "/history/", headers={"If-Modified-Since": first.headers["last-modified"]}
    )
    assert by_date.status_code == 304 and policy_of(by_date)["script-src"] == expected


def test_files_that_are_not_pages_and_the_api_allow_no_inline_script_at_all(
    site_client: TestClient,
) -> None:
    for path in ("/app.js", "/api/health"):
        assert policy_of(site_client.get(path))["script-src"] == ["'self'"], path


def test_the_policy_locks_the_things_the_default_does_not_cover(site_client: TestClient) -> None:
    policy = policy_of(site_client.get("/"))
    assert policy["object-src"] == ["'none'"]
    assert policy["base-uri"] == ["'self'"]
    assert policy["form-action"] == ["'self'"]
    assert policy["frame-ancestors"] == ["'none'"]
    assert policy["default-src"] == ["'self'"]
    assert policy["connect-src"] == ["'self'"]


def test_more_headers_on_everything(site_client: TestClient) -> None:
    for path in ("/", "/api/health", "/app.js"):
        headers = site_client.get(path).headers
        assert headers["cross-origin-opener-policy"] == "same-origin", path
        assert headers["cross-origin-resource-policy"] == "same-origin", path
        assert "camera=()" in headers["permissions-policy"], path
        assert "microphone=()" in headers["permissions-policy"], path
        assert headers["x-content-type-options"] == "nosniff", path
        assert headers["x-frame-options"] == "DENY", path


def test_hsts_is_sent_only_when_the_request_came_by_https(site_client: TestClient) -> None:
    assert "strict-transport-security" not in site_client.get("/api/health").headers
    via_proxy = site_client.get("/api/health", headers={"x-forwarded-proto": "https"})
    assert via_proxy.headers["strict-transport-security"].startswith("max-age=")
    assert "includesubdomains" not in via_proxy.headers["strict-transport-security"].lower()
    cloudflare = site_client.get("/api/health", headers={"cf-visitor": '{"scheme":"https"}'})
    assert "strict-transport-security" in cloudflare.headers


def test_a_request_cannot_reach_a_file_outside_the_dashboard(site_client: TestClient) -> None:
    for path in (
        "/../secret.txt",
        "/%2e%2e/secret.txt",
        "/..%2fsecret.txt",
        "/history/../../secret.txt",
        "/%2e%2e%2f%2e%2e%2fetc/passwd",
        "//etc/passwd",
        "/history/%00",
    ):
        response = site_client.get(path)
        assert "not for the web" not in response.text and "root:" not in response.text, path
        assert response.status_code in (200, 307, 308, 400, 404), (path, response.status_code)
        if response.status_code == 200:  # the dashboard's own page, nothing outside it
            assert "<script" in response.text, path


# --- the body limit, as an ASGI layer ---------------------------------------------------------


async def run_asgi(
    app: ASGIApp,
    *,
    method: str = "POST",
    path: str = "/api/auth/login",
    body_chunks: list[bytes],
    declare_length: bool,
    scope_type: str = "http",
) -> list[Message]:
    """Drive an ASGI app by hand: the chunks are handed out one at a time, and what is left shows
    how much of the body was read."""
    headers = [(b"content-type", b"application/json")]
    if declare_length:
        headers.append((b"content-length", str(sum(map(len, body_chunks))).encode()))
    scope: Scope = {"type": scope_type, "method": method, "path": path, "headers": headers}
    pending = list(body_chunks)
    sent: list[Message] = []

    async def receive() -> Message:
        if not pending:
            return {"type": "http.disconnect"}
        return {"type": "http.request", "body": pending.pop(0), "more_body": bool(pending)}

    async def send(message: Message) -> None:
        sent.append(message)

    await app(scope, receive, send)
    sent.append({"type": "test.unread", "chunks": len(pending)})
    return sent


def status_of(messages: list[Message]) -> int | None:
    return next((m["status"] for m in messages if m["type"] == "http.response.start"), None)


def unread(messages: list[Message]) -> int:
    return next(m["chunks"] for m in messages if m["type"] == "test.unread")


async def reads_everything_then_answers(scope: Scope, receive: Receive, send: Send) -> None:
    more = True
    while more:
        more = (await receive()).get("more_body", False)
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": b"read it all"})


async def turns_a_failed_read_into_its_own_400(scope: Scope, receive: Receive, send: Send) -> None:
    """What FastAPI does: any exception while reading the body becomes an answer of its own."""
    try:
        more = True
        while more:
            more = (await receive()).get("more_body", False)
    except Exception:
        await send({"type": "http.response.start", "status": 400, "headers": []})
        await send({"type": "http.response.body", "body": b"There was an error parsing the body"})
        return
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": b"ok"})


def test_a_streamed_body_is_cut_off_soon_after_the_limit() -> None:
    limit = BodyLimit(reads_everything_then_answers, default=10 * 1024)
    chunks = [b"x" * 1024] * 1_000  # a megabyte that nobody declared
    sent = asyncio.run(run_asgi(limit, body_chunks=chunks, declare_length=False))
    assert status_of(sent) == 413
    assert unread(sent) >= 980  # 11 chunks went over the limit; the other ~990 were never taken


def test_the_refusal_replaces_the_answer_an_application_makes_of_a_failed_read() -> None:
    limit = BodyLimit(turns_a_failed_read_into_its_own_400, default=10 * 1024)
    sent = asyncio.run(run_asgi(limit, body_chunks=[b"x" * 1024] * 50, declare_length=False))
    assert status_of(sent) == 413
    bodies = [m["body"] for m in sent if m["type"] == "http.response.body"]
    assert bodies == [json.dumps({"detail": "request body too large"}).encode()]  # one answer


def test_a_declared_length_over_the_limit_is_refused_without_reading_or_calling_anything() -> None:
    called = []

    async def app(scope: Scope, receive: Receive, send: Send) -> None:
        called.append(scope)

    limit = BodyLimit(app, default=10 * 1024)
    sent = asyncio.run(run_asgi(limit, body_chunks=[b"x" * 1024] * 50, declare_length=True))
    assert status_of(sent) == 413 and not called and unread(sent) == 50


def test_a_body_inside_the_limit_passes_untouched() -> None:
    limit = BodyLimit(reads_everything_then_answers, default=10 * 1024)
    sent = asyncio.run(run_asgi(limit, body_chunks=[b"x" * 1024] * 10, declare_length=True))
    assert status_of(sent) == 200 and unread(sent) == 0


def test_a_route_can_have_a_larger_limit_and_only_that_route() -> None:
    limit = BodyLimit(
        reads_everything_then_answers, default=1024, larger={("PUT", "/api/profile"): 100 * 1024}
    )
    big = [b"x" * 1024] * 50
    assert (
        status_of(
            asyncio.run(
                run_asgi(
                    limit, method="PUT", path="/api/profile", body_chunks=big, declare_length=True
                )
            )
        )
        == 200
    )
    assert (
        status_of(
            asyncio.run(
                run_asgi(
                    limit, method="POST", path="/api/profile", body_chunks=big, declare_length=True
                )
            )
        )
        == 413
    )
    assert (
        status_of(
            asyncio.run(
                run_asgi(
                    limit, method="PUT", path="/api/settings", body_chunks=big, declare_length=True
                )
            )
        )
        == 413
    )


def test_sockets_and_other_scopes_are_not_limited() -> None:
    seen = []

    async def app(scope: Scope, receive: Receive, send: Send) -> None:
        seen.append(scope["type"])

    limit = BodyLimit(app, default=1)
    for kind in ("websocket", "lifespan"):
        asyncio.run(run_asgi(limit, body_chunks=[b"x" * 100], declare_length=True, scope_type=kind))
    assert seen == ["websocket", "lifespan"]
