"""Passwords, sessions and login throttling."""

from __future__ import annotations

import hashlib
import os
import secrets
import threading
import time
from collections import deque
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import NamedTuple

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from trafficpi.core.paths import ensure_private_dir

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


def normalize_username(name: str) -> str:
    """Usernames are not case-sensitive: phone keyboards capitalise the first letter."""
    return name.strip().lower()


def password_stamp(password_hash: str) -> str:
    """A short fingerprint of a password hash.

    Sessions carry it, so setting a new password ends every session made with the old one.
    """
    return hashlib.sha256(password_hash.encode()).hexdigest()[:16]


def load_or_create_secret(path: Path) -> str:
    """The key that signs session cookies, created (readable by the owner only) on first use."""
    if path.exists():
        return path.read_text(encoding="utf-8").strip()
    ensure_private_dir(path.parent)
    secret = secrets.token_urlsafe(48)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as file:
        file.write(secret + "\n")
    return secret


class Session(NamedTuple):
    username: str
    stamp: str  # the password fingerprint (:func:`password_stamp`) the session was made with


class SessionSigner:
    """Signed, expiring session tokens. Nothing is stored on the server."""

    def __init__(self, secret: str, max_age_s: int) -> None:
        self._serializer = URLSafeTimedSerializer(secret, salt="trafficpi-session")
        self.max_age_s = max_age_s

    def issue(self, username: str, stamp: str) -> str:
        return self._serializer.dumps({"u": username, "s": stamp})

    def read(self, token: str | None) -> Session | None:
        """The session in a valid, unexpired token, else None."""
        if not token:
            return None
        try:
            data = self._serializer.loads(token, max_age=self.max_age_s)
        except (BadSignature, SignatureExpired):
            return None
        if not isinstance(data, dict):
            return None
        username, stamp = data.get("u"), data.get("s")
        if not isinstance(username, str) or not isinstance(stamp, str):
            return None
        return Session(username, stamp)


class LoginThrottle:
    """Remembers recent failed logins so that guessing passwords is slow.

    A failure counts against both the client address and the username. Behind a tunnel every
    client can arrive from the same address, which is why the username is limited as well.

    Only failures are remembered, and only for the window. Asking about a client records nothing
    (it once did, so a flood of made-up names grew this without limit), keys whose failures have
    all aged out are dropped, and there are never more than ``max_keys`` of them: when it is full
    the keys with the fewest failures go first, so a real lockout outlives one-off junk.
    """

    def __init__(
        self,
        attempts: int,
        window_s: float,
        clock=time.monotonic,  # type: ignore[no-untyped-def]
        *,
        max_keys: int = 4096,
    ) -> None:
        self._attempts = attempts
        self._window = window_s
        self._clock = clock
        self._max_keys = max_keys
        self._failures: dict[str, deque[float]] = {}
        self._next_sweep = 0.0
        self._lock = threading.Lock()  # logins are handled in a pool of threads

    def __len__(self) -> int:
        with self._lock:
            return len(self._failures)

    def _recent(self, key: str) -> deque[float] | None:
        """The key's failures within the window; None (and the key is forgotten) if none are."""
        failures = self._failures.get(key)
        if failures is None:
            return None
        cutoff = self._clock() - self._window
        while failures and failures[0] < cutoff:
            failures.popleft()
        if not failures:
            del self._failures[key]
            return None
        return failures

    def _count(self, key: str) -> int:
        recent = self._recent(key)
        return len(recent) if recent else 0

    def blocked(self, *keys: str) -> bool:
        with self._lock:
            return any(self._count(key) >= self._attempts for key in keys)

    def retry_after_s(self, *keys: str) -> int:
        with self._lock:
            waits = []
            for key in keys:
                recent = self._recent(key)
                if recent and len(recent) >= self._attempts:
                    waits.append(self._window - (self._clock() - recent[0]))
            return max(1, int(max(waits, default=0)) + 1)

    def record_failure(self, *keys: str) -> tuple[str, ...]:
        """Note a failure against each key; returns the keys that have just reached the limit."""
        with self._lock:
            now = self._clock()
            reached = []
            for key in keys:
                failures = self._failures.setdefault(key, deque())
                failures.append(now)
                if len(self._recent(key) or ()) == self._attempts:
                    reached.append(key)
            if now >= self._next_sweep or len(self._failures) > self._max_keys:
                self._sweep(now)
            return tuple(reached)

    def _sweep(self, now: float) -> None:
        """Drop the keys whose failures have all expired; if still over the limit, make room."""
        cutoff = now - self._window
        for key in [k for k, failures in self._failures.items() if failures[-1] < cutoff]:
            del self._failures[key]
        self._next_sweep = now + self._window / 4
        if len(self._failures) > self._max_keys:
            # Make room for a tenth of the limit at once, so that this does not run on every
            # failure: the fewest failures go first, the least recent among those.
            excess = len(self._failures) - int(self._max_keys * 0.9)
            ranked = sorted(self._failures.items(), key=lambda item: (len(item[1]), item[1][-1]))
            for key, _ in ranked[:excess]:
                del self._failures[key]

    def clear(self, *keys: str) -> None:
        with self._lock:
            for key in keys:
                self._failures.pop(key, None)


class Busy(Exception):
    """Every password-hashing slot is taken."""


class HashingSlots:
    """Limits how many password hashes run at once; asking for one more is refused at once.

    A hash takes 64 MiB and about 0.15 s of a core (measured on the Pi 5). Logins are handled in
    a pool of up to 40 threads, so without a limit a burst of them, right or wrong, holds
    gigabytes that the vision service needs, and each waiting thread would be one the rest of the
    API cannot use. Refusing the surplus keeps both in check.
    """

    def __init__(self, slots: int = 2) -> None:
        self._free = threading.BoundedSemaphore(slots)

    @contextmanager
    def take(self) -> Iterator[None]:
        if not self._free.acquire(blocking=False):
            raise Busy
        try:
            yield
        finally:
            self._free.release()
