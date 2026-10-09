"""Passwords, sessions and login throttling."""

from __future__ import annotations

import os
import secrets
import time
from collections import defaultdict, deque
from pathlib import Path

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

COOKIE_NAME = "trafficpi_session"
MIN_PASSWORD_LENGTH = 10

_hasher = PasswordHasher()  # argon2id with the library's current recommended parameters


def hash_password(password: str) -> str:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"password must be at least {MIN_PASSWORD_LENGTH} characters")
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerificationError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    """True when the hash was made with weaker parameters than are current."""
    return _hasher.check_needs_rehash(password_hash)


def load_or_create_secret(path: Path) -> str:
    """The key that signs session cookies, created (readable by the owner only) on first use."""
    if path.exists():
        return path.read_text(encoding="utf-8").strip()
    path.parent.mkdir(parents=True, exist_ok=True)
    secret = secrets.token_urlsafe(48)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as file:
        file.write(secret + "\n")
    return secret


class SessionSigner:
    """Signed, expiring session tokens. Nothing is stored on the server."""

    def __init__(self, secret: str, max_age_s: int) -> None:
        self._serializer = URLSafeTimedSerializer(secret, salt="trafficpi-session")
        self.max_age_s = max_age_s

    def issue(self, username: str) -> str:
        return self._serializer.dumps({"u": username})

    def read(self, token: str | None) -> str | None:
        """The username in a valid, unexpired token, else None."""
        if not token:
            return None
        try:
            data = self._serializer.loads(token, max_age=self.max_age_s)
        except (BadSignature, SignatureExpired):
            return None
        username = data.get("u") if isinstance(data, dict) else None
        return username if isinstance(username, str) else None


class LoginThrottle:
    """Remembers recent failed logins so that guessing passwords is slow.

    A failure counts against both the client address and the username. Behind a tunnel every
    client can arrive from the same address, which is why the username is limited as well.
    """

    def __init__(self, attempts: int, window_s: float, clock=time.monotonic) -> None:  # type: ignore[no-untyped-def]
        self._attempts = attempts
        self._window = window_s
        self._clock = clock
        self._failures: dict[str, deque[float]] = defaultdict(deque)

    def _recent(self, key: str) -> deque[float]:
        failures = self._failures[key]
        cutoff = self._clock() - self._window
        while failures and failures[0] < cutoff:
            failures.popleft()
        return failures

    def blocked(self, *keys: str) -> bool:
        return any(len(self._recent(key)) >= self._attempts for key in keys)

    def retry_after_s(self, *keys: str) -> int:
        waits = [
            self._window - (self._clock() - self._recent(key)[0])
            for key in keys
            if len(self._recent(key)) >= self._attempts
        ]
        return max(1, int(max(waits, default=0)) + 1)

    def record_failure(self, *keys: str) -> None:
        now = self._clock()
        for key in keys:
            self._recent(key).append(now)

    def clear(self, *keys: str) -> None:
        for key in keys:
            self._failures.pop(key, None)
