"""The daily email's API: the card in Settings, the key, tests and the preview."""

import logging
import stat
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from tests.test_mail_service import FakeMailer
from trafficpi.api.app import create_app
from trafficpi.api.auth import hash_password
from trafficpi.api.settings import ApiSettings
from trafficpi.api.vision_client import VisionClient
from trafficpi.db import create_db_engine, upgrade_to_head
from trafficpi.db.store import Store
from trafficpi.mail.mailer import SendOutcome

PASSWORD = "correct horse battery"
KEY = "re_SECRETsecret123456789"
SCHEDULE = {
    "enabled": True,
    "time": "20:00",
    "days": [0, 1, 2, 3, 4, 5, 6],
    "sender_name": "TrafficPi",
    "sender_address": "report@mail.example.com",
    "link_url": "",
}


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
    s.set_setting("timezone", "America/New_York")
    return s


@pytest.fixture
def mailer() -> FakeMailer:
    return FakeMailer()


def vision(_request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"ok": True})


def make_app(store: Store, tmp_path: Path, mailer: FakeMailer, **over: object):  # type: ignore[no-untyped-def]
    return create_app(
        ApiSettings(data_dir=tmp_path, dashboard_dir=None),
        store=store,
        vision=VisionClient("http://vision", transport=httpx.MockTransport(vision)),
        mailer=mailer,
        **over,  # type: ignore[arg-type]
    )


@pytest.fixture
def anonymous(store: Store, tmp_path: Path, mailer: FakeMailer) -> Iterator[TestClient]:
    with TestClient(make_app(store, tmp_path, mailer), base_url="http://testserver") as c:
        yield c


@pytest.fixture
def client(anonymous: TestClient) -> TestClient:
    login = anonymous.post("/api/auth/login", json={"username": "admin", "password": PASSWORD})
    assert login.status_code == 204
    return anonymous


def configure(client: TestClient, **over: object) -> dict[str, object]:
    response = client.put("/api/mail/schedule", json={**SCHEDULE, **over})
    assert response.status_code == 200, response.text
    return response.json()  # type: ignore[no-any-return]


def add(client: TestClient, address: str = "owner@example.com") -> int:
    response = client.post("/api/mail/recipients", json={"address": address})
    assert response.status_code == 200, response.text
    return next(r["id"] for r in response.json()["recipients"] if r["address"] == address.lower())


# --- who may ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("GET", "/api/mail", None),
        ("PUT", "/api/mail/schedule", SCHEDULE),
        ("POST", "/api/mail/recipients", {"address": "a@example.com"}),
        ("DELETE", "/api/mail/recipients/1", None),
        ("POST", "/api/mail/recipients/1/test", None),
        ("PUT", "/api/mail/key", {"key": KEY}),
        ("DELETE", "/api/mail/key", None),
        ("GET", "/api/mail/preview", None),
    ],
)
def test_every_part_of_the_email_needs_a_login(
    anonymous: TestClient, method: str, path: str, body: object
) -> None:
    response = anonymous.request(method, path, json=body)
    assert response.status_code == 401


def test_a_request_from_another_site_is_refused_like_every_other_change(client: TestClient) -> None:
    response = client.put(
        "/api/mail/schedule", json=SCHEDULE, headers={"Origin": "https://evil.example.com"}
    )
    assert response.status_code == 403


# --- the state --------------------------------------------------------------------------------


def test_a_fresh_install_has_the_email_off_with_nobody_to_send_it_to(client: TestClient) -> None:
    state = client.get("/api/mail").json()
    assert state["schedule"] == {
        "enabled": False,
        "time": "20:00",
        "days": [0, 1, 2, 3, 4, 5, 6],
        "sender_name": "TrafficPi",
        "sender_address": "",
        "link_url": "",
    }
    assert state["recipients"] == [] and state["recent"] == []
    assert state["key_set"] is False and state["transport"] == "resend"
    assert state["ready"] is False and state["next_at"] is None
    assert any("recipient" in p.lower() for p in state["problems"])
    assert state["timezone"] == "America/New_York" and state["max_recipients"] == 20


def test_the_state_says_what_is_missing_until_nothing_is(
    client: TestClient, tmp_path: Path, mailer: FakeMailer
) -> None:
    mailer.is_ready = False
    configure(client)
    add(client)
    state = client.get("/api/mail").json()
    assert state["problems"] == ["No API key is set."] and state["ready"] is False
    mailer.is_ready = True
    state = client.get("/api/mail").json()
    assert state["problems"] == [] and state["ready"] is True and state["next_at"] is not None


# --- the schedule -----------------------------------------------------------------------------


def test_the_schedule_is_saved_and_the_new_state_comes_back(client: TestClient) -> None:
    saved = configure(
        client,
        time="07:30",
        days=[0, 2, 4],
        sender_name="Elm Street cam",
        link_url="https://traffic.example.com",
    )
    assert saved["schedule"] == {
        "enabled": True,
        "time": "07:30",
        "days": [0, 2, 4],
        "sender_name": "Elm Street cam",
        "sender_address": "report@mail.example.com",
        "link_url": "https://traffic.example.com",
    }
    assert client.get("/api/mail").json()["schedule"] == saved["schedule"]


@pytest.mark.parametrize(
    ("member", "value", "words"),
    [
        ("time", "8pm", "HH:MM"),
        ("time", "25:00", "HH:MM"),
        ("days", [], "day"),
        ("days", [7], "day"),
        ("days", [1, 1], "day"),
        ("sender_address", "", "address"),
        ("sender_address", "not an address", "address"),
        ("sender_address", "Name <a@b.io>", "address"),
        ("sender_name", "Jet <jet@x.com>", "name"),
        ("link_url", "javascript:alert(1)", "link"),
        ("link_url", "example.com", "link"),
    ],
)
def test_a_schedule_that_cannot_work_is_refused_in_words_and_changes_nothing(
    client: TestClient, member: str, value: object, words: str
) -> None:
    before = client.get("/api/mail").json()["schedule"]
    response = client.put("/api/mail/schedule", json={**SCHEDULE, member: value})
    assert response.status_code == 422
    assert words in response.json()["detail"]
    assert client.get("/api/mail").json()["schedule"] == before


@pytest.mark.parametrize("value", ["yes", "true", 1, 0, None])
def test_the_switch_must_be_true_or_false(client: TestClient, value: object) -> None:
    assert client.put("/api/mail/schedule", json={**SCHEDULE, "enabled": value}).status_code == 422


@pytest.mark.parametrize("member", list(SCHEDULE))
def test_every_member_of_the_schedule_must_be_given(client: TestClient, member: str) -> None:
    body = {k: v for k, v in SCHEDULE.items() if k != member}
    assert client.put("/api/mail/schedule", json=body).status_code == 422


def test_the_email_can_be_switched_off_without_a_sender_address(client: TestClient) -> None:
    saved = configure(client, enabled=False, sender_address="")
    assert saved["schedule"]["enabled"] is False  # type: ignore[index]


# --- recipients -------------------------------------------------------------------------------


def test_a_recipient_is_added_checked_and_listed_with_no_result_yet(client: TestClient) -> None:
    state = client.post("/api/mail/recipients", json={"address": "  Owner@Example.COM "}).json()
    (recipient,) = state["recipients"]
    assert recipient["address"] == "owner@example.com"
    assert recipient["last_status"] is None and recipient["last_at"] is None
    assert isinstance(recipient["added_at"], int) and recipient["id"] > 0


def test_the_same_address_twice_is_one_recipient(client: TestClient) -> None:
    add(client)
    state = client.post("/api/mail/recipients", json={"address": "OWNER@example.com"}).json()
    assert len(state["recipients"]) == 1


@pytest.mark.parametrize(
    "address", ["", "plain", "a@b", "Name <a@b.io>", "a@b.io, c@d.io", 5, None]
)
def test_an_address_that_is_not_one_is_refused_in_words(
    client: TestClient, address: object
) -> None:
    response = client.post("/api/mail/recipients", json={"address": address})
    assert response.status_code == 422
    assert client.get("/api/mail").json()["recipients"] == []


def test_the_list_holds_twenty_and_says_so_when_it_is_full(client: TestClient) -> None:
    for i in range(20):
        add(client, f"person{i}@example.com")
    response = client.post("/api/mail/recipients", json={"address": "one-too-many@example.com"})
    assert response.status_code == 409 and "20" in response.json()["detail"]
    assert len(client.get("/api/mail").json()["recipients"]) == 20


def test_a_recipient_is_removed_and_a_second_removal_is_a_404(client: TestClient) -> None:
    recipient = add(client)
    state = client.delete(f"/api/mail/recipients/{recipient}").json()
    assert state["recipients"] == []
    assert client.delete(f"/api/mail/recipients/{recipient}").status_code == 404


# --- the key ----------------------------------------------------------------------------------


def test_the_key_is_saved_in_a_private_file_and_the_state_only_says_one_is_set(
    client: TestClient, tmp_path: Path
) -> None:
    state = client.put("/api/mail/key", json={"key": f"  {KEY} \n"}).json()
    assert state["key_set"] is True
    path = tmp_path / "mail.key"
    assert path.read_text().strip() == KEY
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert client.delete("/api/mail/key").json()["key_set"] is False
    assert not path.exists()


def test_the_key_is_in_no_answer_the_api_gives(
    client: TestClient, mailer: FakeMailer, tmp_path: Path
) -> None:
    configure(client)
    recipient = add(client)
    answers = [
        client.put("/api/mail/key", json={"key": KEY}),
        client.get("/api/mail"),
        client.get("/api/mail/preview"),
        client.post(f"/api/mail/recipients/{recipient}/test"),
        client.get("/api/settings"),
        client.get("/api/health"),
    ]
    for answer in answers:
        assert KEY not in answer.text and "SECRETsecret" not in answer.text, answer.request.url
        assert "SECRETsecret" not in str(answer.headers)


def test_the_key_is_in_no_log_line(
    client: TestClient, mailer: FakeMailer, caplog: pytest.LogCaptureFixture
) -> None:
    configure(client)
    recipient = add(client)
    with caplog.at_level(logging.DEBUG):
        client.put("/api/mail/key", json={"key": KEY})
        client.post(f"/api/mail/recipients/{recipient}/test")
        client.put("/api/mail/key", json={"key": "short"})  # refused: and not repeated either
        client.delete("/api/mail/key")
    assert "SECRETsecret" not in caplog.text and KEY not in caplog.text


@pytest.mark.parametrize("typed", ["", "short", "has a space in it", "x" * 400, "re_\nsecond"])
def test_a_key_that_cannot_be_one_is_refused_without_repeating_it(
    client: TestClient, typed: str, tmp_path: Path
) -> None:
    response = client.put("/api/mail/key", json={"key": typed})
    assert response.status_code == 422
    if typed.strip():
        assert typed.strip() not in response.text
    assert not (tmp_path / "mail.key").exists()


@pytest.mark.parametrize("typed", [5, None, ["re_abcdefghij"], {"a": 1}])
def test_a_key_that_is_not_text_is_refused(client: TestClient, typed: object) -> None:
    assert client.put("/api/mail/key", json={"key": typed}).status_code == 422


# --- tests ------------------------------------------------------------------------------------


def test_a_test_mail_goes_to_the_recipient_and_the_result_shows_next_to_them(
    client: TestClient, mailer: FakeMailer
) -> None:
    configure(client)
    recipient = add(client)
    answer = client.post(f"/api/mail/recipients/{recipient}/test")
    assert answer.status_code == 200 and answer.json() == {"ok": True, "detail": ""}
    (message,) = mailer.sent
    assert message.to == "owner@example.com" and message.subject.startswith("[Test] ")
    (listed,) = client.get("/api/mail").json()["recipients"]
    assert (listed["last_kind"], listed["last_status"], listed["last_detail"]) == (
        "test",
        "sent",
        None,
    )


def test_a_test_that_fails_says_why_and_so_does_the_list(
    client: TestClient, mailer: FakeMailer
) -> None:
    configure(client)
    recipient = add(client)
    mailer.script["owner@example.com"] = [
        SendOutcome("failed", "Resend refused it (403 validation_error): domain is not verified")
    ]
    answer = client.post(f"/api/mail/recipients/{recipient}/test")
    assert answer.status_code == 200
    assert answer.json()["ok"] is False and "domain is not verified" in answer.json()["detail"]
    (listed,) = client.get("/api/mail").json()["recipients"]
    assert listed["last_status"] == "failed" and "domain is not verified" in listed["last_detail"]


def test_a_test_for_a_recipient_that_is_not_there_is_a_404(client: TestClient) -> None:
    configure(client)
    assert client.post("/api/mail/recipients/999/test").status_code == 404


def test_a_test_needs_a_sender_address_that_has_been_saved(
    client: TestClient, mailer: FakeMailer
) -> None:
    recipient = add(client)
    answer = client.post(f"/api/mail/recipients/{recipient}/test")
    assert answer.status_code == 409 and "sender" in answer.json()["detail"]
    assert mailer.attempts == []


def test_tests_in_quick_succession_are_turned_away_with_a_time_to_wait(
    client: TestClient, mailer: FakeMailer
) -> None:
    configure(client)
    recipient = add(client)
    assert client.post(f"/api/mail/recipients/{recipient}/test").status_code == 200
    again = client.post(f"/api/mail/recipients/{recipient}/test")
    assert again.status_code == 429
    assert 1 <= int(again.headers["retry-after"]) <= 5
    assert len(mailer.attempts) == 1


def test_the_preview_is_the_mail_as_it_would_be_made_now_and_sends_nothing(
    client: TestClient, mailer: FakeMailer
) -> None:
    configure(client)
    preview = client.get("/api/mail/preview").json()
    assert preview["subject"].startswith("[Test] ")
    assert "daily report" in preview["text"] and "<html" in preview["html"]
    assert mailer.attempts == []


# --- the file sender (the demo, the browser test) ---------------------------------------------


def test_with_the_file_sender_no_key_is_needed_and_a_test_writes_a_file(
    store: Store, tmp_path: Path
) -> None:
    app = create_app(
        ApiSettings(data_dir=tmp_path, dashboard_dir=None, mail_transport="file"),
        store=store,
        vision=VisionClient("http://vision", transport=httpx.MockTransport(vision)),
    )
    with TestClient(app, base_url="http://testserver") as c:
        c.post("/api/auth/login", json={"username": "admin", "password": PASSWORD})
        configure(c)
        recipient = add(c)
        state = c.get("/api/mail").json()
        assert state["transport"] == "file" and state["key_set"] is False
        assert state["problems"] == [] and state["ready"] is True
        assert c.post(f"/api/mail/recipients/{recipient}/test").json()["ok"] is True
    assert len(list((tmp_path / "outbox").glob("*.html"))) == 1


def test_the_sender_is_chosen_by_a_setting_that_only_knows_two_names(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert ApiSettings().mail_transport == "resend"
    monkeypatch.setenv("TRAFFICPI_MAIL_TRANSPORT", "file")
    assert ApiSettings().mail_transport == "file"
    monkeypatch.setenv("TRAFFICPI_MAIL_TRANSPORT", "carrier-pigeon")
    assert ApiSettings().mail_transport == "resend"


# --- the scheduler thread ---------------------------------------------------------------------


def threads_named(prefix: str) -> list[threading.Thread]:
    return [t for t in threading.enumerate() if t.name.startswith(prefix)]


def test_no_scheduler_runs_unless_it_is_asked_for(
    store: Store, tmp_path: Path, mailer: FakeMailer
) -> None:
    with TestClient(make_app(store, tmp_path, mailer)):
        assert threads_named("mail-scheduler") == []


def test_the_scheduler_looks_at_the_email_again_and_again_and_stops_with_the_app(
    store: Store, tmp_path: Path, mailer: FakeMailer
) -> None:
    app = make_app(store, tmp_path, mailer, mail_tick_s=0.02)
    looks = 0

    def count() -> object:
        nonlocal looks
        looks += 1
        return None

    app.state.ctx.mail_service.tick = count
    with TestClient(app):
        deadline = time.monotonic() + 5
        while looks < 3 and time.monotonic() < deadline:
            time.sleep(0.01)
        assert looks >= 3
        assert len(threads_named("mail-scheduler")) == 1
    stopped_at = looks
    time.sleep(0.2)
    assert looks == stopped_at and threads_named("mail-scheduler") == []


def test_a_look_that_fails_does_not_stop_the_scheduler(
    store: Store, tmp_path: Path, mailer: FakeMailer, caplog: pytest.LogCaptureFixture
) -> None:
    app = make_app(store, tmp_path, mailer, mail_tick_s=0.02)
    looks = 0

    def fail() -> object:
        nonlocal looks
        looks += 1
        raise RuntimeError("a look went wrong")

    app.state.ctx.mail_service.tick = fail
    with caplog.at_level(logging.ERROR), TestClient(app):
        deadline = time.monotonic() + 5
        while looks < 3 and time.monotonic() < deadline:
            time.sleep(0.01)
    assert looks >= 3
    assert "a look went wrong" in caplog.text
