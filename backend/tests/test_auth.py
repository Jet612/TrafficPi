from pathlib import Path

import pytest

from trafficpi.api.auth import (
    LoginThrottle,
    Session,
    SessionSigner,
    hash_password,
    load_or_create_secret,
    needs_rehash,
    normalize_username,
    password_stamp,
    verify_password,
)


def test_password_hashing() -> None:
    hashed = hash_password("correct horse battery")
    assert hashed.startswith("$argon2id$") and "correct horse" not in hashed
    assert verify_password(hashed, "correct horse battery")
    assert not verify_password(hashed, "correct horse batter")
    assert not verify_password("not a hash", "whatever-it-is")
    assert not needs_rehash(hashed)
    assert hash_password("correct horse battery") != hashed  # salted


def test_short_passwords_are_refused() -> None:
    with pytest.raises(ValueError, match="at least"):
        hash_password("short")


def test_the_secret_is_created_once_and_kept_private(tmp_path: Path) -> None:
    path = tmp_path / "sub" / "secret.key"
    first = load_or_create_secret(path)
    assert len(first) >= 48 and path.stat().st_mode & 0o777 == 0o600
    assert load_or_create_secret(path) == first


def test_session_tokens_round_trip_and_reject_tampering() -> None:
    signer = SessionSigner("s3cret", max_age_s=3600)
    token = signer.issue("admin", "stamp")
    assert signer.read(token) == Session("admin", "stamp")
    # Change a character of the payload. (The last character of a base64 signature carries
    # padding bits, so altering it can leave the decoded bytes, and the token, valid.)
    tampered = ("A" if token[2] != "A" else "B").join((token[:2], token[3:]))
    assert signer.read(tampered) is None
    assert signer.read("garbage") is None and signer.read(None) is None and signer.read("") is None
    assert SessionSigner("other-secret", 3600).read(token) is None


def test_a_signed_token_without_the_expected_fields_is_refused() -> None:
    from itsdangerous import URLSafeTimedSerializer

    forge = URLSafeTimedSerializer("s3cret", salt="trafficpi-session").dumps
    signer = SessionSigner("s3cret", 3600)
    assert signer.read(forge({"u": "admin"})) is None  # no stamp: made before stamps existed
    assert signer.read(forge({"u": 7, "s": "x"})) is None
    assert signer.read(forge(["admin", "x"])) is None


def test_the_password_stamp_changes_with_the_hash_and_hides_it() -> None:
    first, second = hash_password("correct horse battery"), hash_password("correct horse battery")
    assert password_stamp(first) == password_stamp(first) != password_stamp(second)
    assert len(password_stamp(first)) == 16 and password_stamp(first) not in first


def test_usernames_ignore_case_and_surrounding_spaces() -> None:
    assert normalize_username("  Admin ") == "admin"


def test_session_tokens_expire() -> None:
    token = SessionSigner("s3cret", max_age_s=3600).issue("admin", "stamp")
    assert SessionSigner("s3cret", max_age_s=-1).read(token) is None


def test_the_throttle_blocks_after_enough_failures_and_recovers() -> None:
    now = [0.0]
    throttle = LoginThrottle(3, 600, clock=lambda: now[0])
    keys = ("ip:1.2.3.4", "user:admin")
    for _ in range(2):
        throttle.record_failure(*keys)
    assert not throttle.blocked(*keys)
    throttle.record_failure(*keys)
    assert throttle.blocked(*keys) and throttle.retry_after_s(*keys) == 601
    assert not throttle.blocked("ip:9.9.9.9", "user:other")  # unrelated clients are unaffected
    assert throttle.blocked("ip:9.9.9.9", "user:admin")  # but the username is protected too
    now[0] = 601
    assert not throttle.blocked(*keys)


def test_a_success_clears_the_failures() -> None:
    throttle = LoginThrottle(2, 600, clock=lambda: 0.0)
    throttle.record_failure("ip:1", "user:a")
    throttle.record_failure("ip:1", "user:a")
    assert throttle.blocked("ip:1")
    throttle.clear("ip:1", "user:a")
    assert not throttle.blocked("ip:1", "user:a")
