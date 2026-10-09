from pathlib import Path

import pytest

from trafficpi.api.auth import (
    Busy,
    HashingSlots,
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


def test_asking_about_a_client_remembers_nothing() -> None:
    throttle = LoginThrottle(8, 600, clock=lambda: 0.0)
    for i in range(1_000):
        throttle.blocked(f"ip:10.0.0.{i}", f"user:name-{i}")
        throttle.retry_after_s(f"user:name-{i}")
    assert len(throttle) == 0  # it used to hold two entries per question, for ever


def test_failures_that_have_aged_out_are_forgotten() -> None:
    now = [0.0]
    throttle = LoginThrottle(3, 600, clock=lambda: now[0])
    for i in range(50):
        throttle.record_failure(f"user:name-{i}")
    assert len(throttle) == 50
    now[0] = 601
    throttle.record_failure("user:later")  # the next failure sweeps what has expired
    assert len(throttle) == 1


def test_a_name_that_is_asked_about_after_its_failures_expired_is_dropped() -> None:
    now = [0.0]
    throttle = LoginThrottle(3, 600, clock=lambda: now[0])
    throttle.record_failure("user:once")
    now[0] = 601
    assert not throttle.blocked("user:once")
    assert len(throttle) == 0


def test_the_throttle_has_a_size_limit_and_keeps_a_lockout_over_one_off_failures() -> None:
    throttle = LoginThrottle(3, 600, max_keys=20, clock=lambda: 0.0)
    for _ in range(3):
        throttle.record_failure("user:admin")  # locked out
    for i in range(500):
        throttle.record_failure(f"user:junk-{i}")  # one failure each, 500 different names
    assert len(throttle) <= 20
    assert throttle.blocked("user:admin")  # junk did not push the real lockout out


def test_only_a_few_hashes_run_at_once_and_the_next_is_turned_away() -> None:
    slots = HashingSlots(2)
    with slots.take(), slots.take(), pytest.raises(Busy), slots.take():
        pass
    with slots.take():  # a slot that was given back can be taken again
        pass


def test_recording_a_failure_says_which_keys_have_just_reached_the_limit() -> None:
    throttle = LoginThrottle(3, 600, clock=lambda: 0.0)
    assert throttle.record_failure("ip:1", "user:a") == ()
    assert throttle.record_failure("ip:1", "user:a") == ()
    assert throttle.record_failure("ip:1", "user:a") == ("ip:1", "user:a")
    # A key that is already past the limit has not "just" reached it, and a fresh one has not.
    assert throttle.record_failure("ip:2", "user:a") == ()
