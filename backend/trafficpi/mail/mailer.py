"""Sending a mail (plan.md D32): the interface, the Resend sender, and a sender that writes files.

A ``Mailer`` takes one message for one recipient and says what became of it: ``sent``, ``retry``
(the network, a rate limit or the provider's own trouble: try again later) or ``failed`` (the
provider refused it for a reason that waiting will not change: a wrong key, an unverified domain,
a refused address, a used-up quota). Deciding when to try again is the scheduler's business
(``mail/service.py``); a mailer makes one attempt.

The key is read from its file at every send, so a new one works at once and nothing here keeps it
in memory. It is never part of an outcome, an error text or a ``repr``: whatever a server or a
network error says is cleaned, and the key (and its first characters) taken out, before it is kept.
"""

from __future__ import annotations

import hashlib
import itertools
import time
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path
from typing import Literal, Protocol

import httpx

from trafficpi.core.paths import ensure_private_dir
from trafficpi.mail.settings import read_key

MAX_DETAIL = 300  # characters of a reason that are kept
RESEND_URL = "https://api.resend.com"
TIMEOUT = httpx.Timeout(15.0, connect=8.0)  # short: a scheduler thread waits on every call
# 429s that waiting a few minutes will not cure: the plan's quota is used up.
QUOTA_ERRORS = ("daily_quota_exceeded", "monthly_quota_exceeded")
# 409s that are about the moment: the same request is being handled, or the thing is locked.
TRANSIENT_CONFLICTS = ("concurrent_idempotent_requests", "resource_locked")
MAX_RETRY_AFTER_S = 3600.0


def _user_agent() -> str:
    try:
        return f"TrafficPi/{metadata.version('trafficpi')}"
    except metadata.PackageNotFoundError:
        return "TrafficPi/0"


@dataclass(frozen=True, slots=True)
class MailMessage:
    """One mail for one recipient. With an idempotency key, sending it twice sends it once (the
    provider remembers a key for 24 hours), which is what makes a send after a crash safe."""

    sender: str  # "Name <address>" or an address
    to: str
    subject: str
    text: str
    html: str
    idempotency_key: str | None = None


@dataclass(frozen=True, slots=True)
class SendOutcome:
    kind: Literal["sent", "retry", "failed"]
    detail: str = ""  # a reason, in words, for the owner to read next to the recipient
    provider_id: str | None = None
    retry_after_s: float | None = None  # what the provider asked for, if it did


class Mailer(Protocol):
    name: str  # "resend" or "file"

    def ready(self) -> bool:
        """Whether a message could be sent now (a key is set, for the real sender)."""
        ...

    def send(self, message: MailMessage) -> SendOutcome: ...


def clean_detail(text: str, *secrets: str, limit: int = MAX_DETAIL) -> str:
    """A reason as one short plain line: control characters and runs of spaces collapsed, anything
    in ``secrets`` (and the first characters of each) taken out, cut to ``limit`` characters."""
    for secret in secrets:
        if len(secret) >= 12:
            text = text.replace(secret, "[key]").replace(secret[:8], "[key]")
        elif secret:
            text = text.replace(secret, "[key]")
    printable = "".join(c if c.isprintable() else " " for c in text)
    return " ".join(printable.split())[:limit]


def _retry_after(response: httpx.Response) -> float | None:
    try:
        seconds = float(response.headers.get("retry-after", ""))
    except ValueError:
        return None
    return seconds if 0 <= seconds <= MAX_RETRY_AFTER_S else None  # not nan, not inf, not negative


class ResendMailer:
    """Sends through Resend's HTTP API: one POST to ``/emails`` for each message."""

    name = "resend"

    def __init__(
        self,
        key_path: Path,
        *,
        base_url: str = RESEND_URL,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._key_path = key_path
        self._base_url = base_url.rstrip("/")
        self._transport = transport

    def __repr__(self) -> str:
        return f"ResendMailer(key_path={str(self._key_path)!r})"

    def ready(self) -> bool:
        return read_key(self._key_path) is not None

    def send(self, message: MailMessage) -> SendOutcome:
        key = read_key(self._key_path)
        if key is None:
            return SendOutcome(
                "failed", "No API key is set. Add the Resend key in Settings, under Daily email."
            )
        headers = {
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "User-Agent": _user_agent(),
        }
        if message.idempotency_key:
            headers["Idempotency-Key"] = message.idempotency_key
        body = {
            "from": message.sender,
            "to": [message.to],
            "subject": message.subject,
            "text": message.text,
            "html": message.html,
        }
        try:
            # Never follow a redirect: the key would go with the request to whatever it names.
            with httpx.Client(
                transport=self._transport, timeout=TIMEOUT, follow_redirects=False
            ) as client:
                response = client.post(f"{self._base_url}/emails", json=body, headers=headers)
        except httpx.TimeoutException as exc:
            return SendOutcome("retry", clean_detail(f"Resend did not answer in time ({exc})", key))
        except httpx.TransportError as exc:
            reason = f"Could not reach Resend ({type(exc).__name__}: {exc})"
            return SendOutcome("retry", clean_detail(reason, key))
        return self._outcome(response, key)

    def _outcome(self, response: httpx.Response, key: str) -> SendOutcome:
        status = response.status_code
        if 200 <= status < 300:
            try:
                found = response.json().get("id")
            except (ValueError, AttributeError):
                found = None
            return SendOutcome("sent", provider_id=found if isinstance(found, str) else None)
        name, text = "", ""
        try:
            data = response.json()
            if isinstance(data, dict):
                name = str(data.get("name") or "")
                text = str(data.get("message") or "")
        except ValueError:
            pass  # not JSON: a proxy's page, say; the status says enough
        what = f"{status} {name}" if name else str(status)
        detail = clean_detail(f"Resend refused it ({what}): {text}", key)
        if 300 <= status < 400:
            return SendOutcome(
                "failed", clean_detail(f"Resend answered with a redirect ({status})")
            )
        if status == 429:
            if name in QUOTA_ERRORS:
                return SendOutcome("failed", detail)
            return SendOutcome("retry", detail, retry_after_s=_retry_after(response))
        if status >= 500 or (status == 409 and name in TRANSIENT_CONFLICTS):
            return SendOutcome("retry", detail)
        return SendOutcome("failed", detail)


class FileMailer:
    """Writes each mail to a folder instead of sending it, for the demo, the browser test and for
    looking at a mail before the real sender is set up. Two files each: ``mail-<id>.html`` (open
    it in a browser) and ``mail-<id>.txt`` (the headers and the plain text). An address that starts
    with ``fail@`` is refused, so that what a failure looks like can be shown without a provider."""

    name = "file"

    def __init__(self, outbox: Path) -> None:
        self._outbox = outbox
        self._serial = itertools.count()

    def ready(self) -> bool:
        return True

    def send(self, message: MailMessage) -> SendOutcome:
        if message.to.startswith("fail@"):
            return SendOutcome(
                "failed",
                "The file sender refuses addresses that start with fail@, to show a failure.",
            )
        ensure_private_dir(self._outbox)
        if message.idempotency_key:
            stem = "mail-" + hashlib.sha256(message.idempotency_key.encode()).hexdigest()[:16]
        else:
            tag = hashlib.sha256(message.to.encode()).hexdigest()[:6]
            stem = f"mail-{time.time_ns()}-{next(self._serial)}-{tag}"
        html_path = self._outbox / f"{stem}.html"
        if message.idempotency_key and html_path.exists():
            return SendOutcome("sent", provider_id=stem)  # the same mail again: once is enough
        header = [f"From: {message.sender}", f"To: {message.to}", f"Subject: {message.subject}"]
        if message.idempotency_key:
            header.append(f"Idempotency-Key: {message.idempotency_key}")
        self._write(self._outbox / f"{stem}.txt", "\n".join(header) + "\n\n" + message.text)
        self._write(html_path, message.html)
        return SendOutcome("sent", provider_id=stem)

    @staticmethod
    def _write(path: Path, content: str) -> None:
        temporary = path.with_name(f".{path.name}.part")
        temporary.write_text(content, encoding="utf-8")
        temporary.chmod(0o600)
        temporary.replace(path)
