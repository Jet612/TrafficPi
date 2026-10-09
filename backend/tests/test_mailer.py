"""Sending a mail: what Resend is asked, how its answers are read, and the file sender."""

import json
import stat
from pathlib import Path

import httpx
import pytest

from trafficpi.mail.mailer import (
    FileMailer,
    MailMessage,
    ResendMailer,
    SendOutcome,
)
from trafficpi.mail.settings import write_key

KEY = "re_SECRETsecret123456789"


def message(**over: object) -> MailMessage:
    fields: dict[str, object] = {
        "sender": "TrafficPi <report@mail.example.com>",
        "to": "owner@example.com",
        "subject": "TrafficPi: 3 did not stop, 12 rolled through (Fri 9 Oct)",
        "text": "plain words",
        "html": "<p>html words</p>",
        "idempotency_key": "trafficpi-1760054400000-7",
        **over,
    }
    return MailMessage(**fields)  # type: ignore[arg-type]


class Server:
    """A stand-in for api.resend.com that remembers what it was asked and answers from a script."""

    def __init__(self, *responses: httpx.Response | Exception) -> None:
        self.responses = list(responses)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        answer = self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
        if isinstance(answer, Exception):
            raise answer
        return answer


def error(status: int, name: str, text: str, **headers: str) -> httpx.Response:
    return httpx.Response(
        status, json={"statusCode": status, "name": name, "message": text}, headers=headers
    )


@pytest.fixture
def key_file(tmp_path: Path) -> Path:
    path = tmp_path / "mail.key"
    write_key(path, KEY)
    return path


def mailer_for(server: Server, key_file: Path) -> ResendMailer:
    return ResendMailer(key_file, transport=httpx.MockTransport(server))


# --- what is asked ----------------------------------------------------------------------------


def test_one_message_is_one_post_to_the_emails_endpoint_with_the_key_and_the_fields(
    key_file: Path,
) -> None:
    server = Server(httpx.Response(200, json={"id": "49a3999c-0ce1-4ea6-ab68-afcd6dc2e794"}))
    outcome = mailer_for(server, key_file).send(message())
    assert outcome == SendOutcome("sent", provider_id="49a3999c-0ce1-4ea6-ab68-afcd6dc2e794")
    (request,) = server.requests
    assert request.method == "POST" and str(request.url) == "https://api.resend.com/emails"
    assert request.headers["authorization"] == f"Bearer {KEY}"
    assert request.headers["content-type"] == "application/json"
    assert request.headers["idempotency-key"] == "trafficpi-1760054400000-7"
    assert request.headers["user-agent"].startswith("TrafficPi/")
    assert json.loads(request.content) == {
        "from": "TrafficPi <report@mail.example.com>",
        "to": ["owner@example.com"],
        "subject": "TrafficPi: 3 did not stop, 12 rolled through (Fri 9 Oct)",
        "text": "plain words",
        "html": "<p>html words</p>",
    }


def test_a_message_without_an_idempotency_key_sends_no_idempotency_header(key_file: Path) -> None:
    server = Server(httpx.Response(200, json={"id": "x"}))
    mailer_for(server, key_file).send(message(idempotency_key=None))
    assert "idempotency-key" not in server.requests[0].headers


def test_the_key_is_read_at_each_send_so_a_new_one_works_at_once(
    tmp_path: Path, key_file: Path
) -> None:
    server = Server(httpx.Response(200, json={"id": "x"}))
    mailer = mailer_for(server, key_file)
    mailer.send(message())
    write_key(key_file, "re_the_new_key_987654321")
    mailer.send(message())
    assert [r.headers["authorization"] for r in server.requests] == [
        f"Bearer {KEY}",
        "Bearer re_the_new_key_987654321",
    ]


def test_without_a_key_nothing_is_sent_and_it_says_what_to_do(tmp_path: Path) -> None:
    server = Server(httpx.Response(200, json={"id": "x"}))
    mailer = mailer_for(server, tmp_path / "mail.key")
    assert mailer.ready() is False
    outcome = mailer.send(message())
    assert outcome.kind == "failed" and "key" in outcome.detail.lower()
    assert server.requests == []


def test_ready_follows_the_key_file(key_file: Path) -> None:
    mailer = mailer_for(Server(httpx.Response(200)), key_file)
    assert mailer.ready() is True
    key_file.unlink()
    assert mailer.ready() is False


def test_the_sender_says_which_it_is(key_file: Path) -> None:
    assert mailer_for(Server(httpx.Response(200)), key_file).name == "resend"


# --- how the answers are read -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("response", "kind"),
    [
        (httpx.Response(200, json={"id": "abc"}), "sent"),
        (httpx.Response(201, json={"id": "abc"}), "sent"),
        (httpx.Response(200, json={"unexpected": True}), "sent"),  # accepted is accepted
        (httpx.Response(200, content=b"not json"), "sent"),
        (error(500, "application_error", "Something went wrong"), "retry"),
        (error(503, "service_unavailable", "Try later"), "retry"),
        (httpx.Response(502, content=b"<html>Bad gateway</html>"), "retry"),
        (error(429, "rate_limit_exceeded", "Too many requests", **{"retry-after": "2"}), "retry"),
        (error(409, "concurrent_idempotent_requests", "Still being processed"), "retry"),
        (error(409, "resource_locked", "Locked"), "retry"),
        (error(401, "missing_api_key", "Missing API key"), "failed"),
        (error(403, "validation_error", "The mail.example.com domain is not verified."), "failed"),
        (error(403, "restricted_api_key", "The key is not active"), "failed"),
        (error(400, "invalid_idempotency_key", "Bad key"), "failed"),
        (error(409, "invalid_idempotent_request", "Same key, other body"), "failed"),
        (error(422, "missing_required_field", "Missing `from`"), "failed"),
        (error(429, "daily_quota_exceeded", "You have reached your daily quota"), "failed"),
        (error(429, "monthly_quota_exceeded", "You have reached your monthly quota"), "failed"),
        (httpx.Response(404, content=b"nothing here"), "failed"),
        (httpx.Response(301, headers={"location": "https://elsewhere.example.com/"}), "failed"),
    ],
)
def test_each_answer_is_sent_to_try_again_or_given_up(
    key_file: Path, response: httpx.Response, kind: str
) -> None:
    assert mailer_for(Server(response), key_file).send(message()).kind == kind


def test_a_refusal_carries_the_reason_resend_gave_so_the_owner_can_act_on_it(
    key_file: Path,
) -> None:
    unverified = error(403, "validation_error", "The mail.example.com domain is not verified.")
    outcome = mailer_for(Server(unverified), key_file).send(message())
    assert outcome.kind == "failed"
    assert "403" in outcome.detail and "domain is not verified" in outcome.detail
    testing = error(403, "validation_error", "You can only send testing emails to your own address")
    assert "your own address" in mailer_for(Server(testing), key_file).send(message()).detail


def test_a_quota_is_given_up_on_and_says_so_while_a_rate_limit_is_tried_again(
    key_file: Path,
) -> None:
    quota = mailer_for(
        Server(error(429, "daily_quota_exceeded", "You have exceeded your daily quota")), key_file
    ).send(message())
    assert quota.kind == "failed" and "quota" in quota.detail.lower()
    limited = mailer_for(
        Server(error(429, "rate_limit_exceeded", "Slow down", **{"retry-after": "3"})), key_file
    ).send(message())
    assert limited.kind == "retry" and limited.retry_after_s == 3.0


@pytest.mark.parametrize("header", ["soon", "-5", "", "1e999", "nan"])
def test_a_retry_after_that_is_not_a_sensible_number_is_ignored(
    key_file: Path, header: str
) -> None:
    response = error(429, "rate_limit_exceeded", "Slow down", **{"retry-after": header})
    outcome = mailer_for(Server(response), key_file).send(message())
    assert outcome.kind == "retry" and outcome.retry_after_s is None


@pytest.mark.parametrize(
    "failure",
    [
        httpx.ConnectTimeout("timed out"),
        httpx.ReadTimeout("timed out"),
        httpx.ConnectError("no route to host"),
        httpx.ReadError("connection reset"),
        httpx.RemoteProtocolError("server disconnected"),
    ],
)
def test_a_network_failure_is_tried_again_later(key_file: Path, failure: Exception) -> None:
    outcome = mailer_for(Server(failure), key_file).send(message())
    assert outcome.kind == "retry"
    assert "Resend" in outcome.detail


def test_what_a_server_says_is_cleaned_and_cut_before_it_is_kept(key_file: Path) -> None:
    rude = error(
        422, "validation_error", "line one\nline two\r\n\x00\x07" + "x" * 2000 + "<script>"
    )
    detail = mailer_for(Server(rude), key_file).send(message()).detail
    assert "\n" not in detail and "\r" not in detail and "\x00" not in detail
    assert len(detail) <= 300


def test_the_key_is_never_in_a_result_even_when_the_server_repeats_it(key_file: Path) -> None:
    echo = error(401, "invalid_api_key", f"API key {KEY} is invalid")
    outcome = mailer_for(Server(echo), key_file).send(message())
    assert KEY not in outcome.detail and "re_SECRET" not in outcome.detail
    assert "invalid" in outcome.detail


def test_the_key_is_never_in_what_a_sender_prints_about_itself(key_file: Path) -> None:
    mailer = mailer_for(Server(httpx.Response(200)), key_file)
    assert KEY not in repr(mailer) and KEY not in str(mailer)


def test_a_transport_error_text_that_holds_the_key_is_cleaned_too(key_file: Path) -> None:
    failure = httpx.ConnectError(f"cannot connect with Authorization: Bearer {KEY}")
    outcome = mailer_for(Server(failure), key_file).send(message())
    assert KEY not in outcome.detail


def test_a_redirect_is_not_followed_so_the_key_never_goes_to_another_host(key_file: Path) -> None:
    server = Server(httpx.Response(307, headers={"location": "https://evil.example.com/emails"}))
    outcome = mailer_for(server, key_file).send(message())
    assert outcome.kind == "failed"
    assert len(server.requests) == 1  # it did not go on to the other address


# --- the sender that writes files -------------------------------------------------------------


def test_the_file_sender_writes_the_mail_where_it_can_be_read_and_opens_in_a_browser(
    tmp_path: Path,
) -> None:
    outbox = tmp_path / "outbox"
    mailer = FileMailer(outbox)
    assert mailer.name == "file" and mailer.ready() is True
    outcome = mailer.send(message())
    assert outcome.kind == "sent" and outcome.provider_id
    htmls = sorted(outbox.glob("*.html"))
    texts = sorted(outbox.glob("*.txt"))
    assert len(htmls) == 1 and len(texts) == 1
    assert htmls[0].read_text() == "<p>html words</p>"
    header = texts[0].read_text()
    assert "To: owner@example.com" in header
    assert "From: TrafficPi <report@mail.example.com>" in header
    assert "Subject: TrafficPi: 3 did not stop" in header and "plain words" in header


def test_the_file_sender_does_not_write_a_mail_twice_for_the_same_key(tmp_path: Path) -> None:
    mailer = FileMailer(tmp_path / "outbox")
    first = mailer.send(message())
    second = mailer.send(message())
    assert first.kind == second.kind == "sent" and first.provider_id == second.provider_id
    assert len(list((tmp_path / "outbox").glob("*.html"))) == 1
    mailer.send(message(idempotency_key="another-key"))
    assert len(list((tmp_path / "outbox").glob("*.html"))) == 2
    mailer.send(message(idempotency_key=None))
    mailer.send(message(idempotency_key=None))
    assert len(list((tmp_path / "outbox").glob("*.html"))) == 4  # none to tell them apart


def test_the_file_sender_refuses_an_address_that_starts_with_fail_so_a_failure_can_be_shown(
    tmp_path: Path,
) -> None:
    mailer = FileMailer(tmp_path / "outbox")
    outcome = mailer.send(message(to="fail@example.com"))
    assert outcome.kind == "failed" and "fail@" in outcome.detail
    assert not (tmp_path / "outbox").exists() or not list((tmp_path / "outbox").glob("*.html"))


def test_the_outbox_is_private_and_its_names_cannot_climb_out_of_it(tmp_path: Path) -> None:
    outbox = tmp_path / "outbox"
    FileMailer(outbox).send(message(to="../../etc/passwd@example.com", idempotency_key="../../x"))
    assert stat.S_IMODE(outbox.stat().st_mode) == 0o700
    assert {p.parent for p in tmp_path.rglob("*.html")} == {outbox}
    assert not (tmp_path.parent / "x.html").exists()
