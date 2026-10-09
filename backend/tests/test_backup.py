"""Backup and restore of the database and the files beside it (plan.md D29)."""

import io
import json
import os
import sqlite3
import stat
import tarfile
import threading
from datetime import UTC, datetime
from pathlib import Path

import pytest

from tests.fakes import finished_trail
from trafficpi.api import backup
from trafficpi.db.models import Base
from trafficpi.db.session import head_revision
from trafficpi.db.store import Store, open_store
from trafficpi.vision.rules import CountEvent, StopEvent

BASE_MS = 1_760_000_000_000
NOW = datetime(2026, 10, 9, 18, 30, 5, tzinfo=UTC)
REAL_HOLDERS_OF = backup.holders_of  # the fixture below replaces it in every test


def populate(data: Path) -> Store:
    """A data directory as the services leave one: a database with everything in it, the session
    key, the reference picture and a little evidence."""
    store = open_store(data)
    store.create_user("admin", "$argon2id$v=19$m=65536,t=3,p=4$not-a-real-hash-for-a-test")
    store.set_setting("timezone", "America/New_York")
    store.set_setting("units", "mph")
    store.set_profile(
        {"lines": [{"id": "main", "name": "Main", "a": [0.2, 0.5], "b": [0.8, 0.5]}]},
        now_ms=BASE_MS,
    )
    for i in range(30):
        store.record_count(CountEvent(0, i, 2, "car", "main", 1), BASE_MS + i * 1_000)
    stop_id = store.record_stop(
        StopEvent(0, 7, 2, "car", "elm", "rolling", hold_s=0.4, min_speed=0.2, observed_s=3.0),  # type: ignore[arg-type]
        BASE_MS + 40_000,
    )
    store.review_stop(stop_id, "confirmed", now_ms=BASE_MS + 50_000)
    store.add_trails([finished_trail(BASE_MS + i * 60_000, track_id=i) for i in range(20)])
    (data / "secret.key").write_text("the-session-key\n")
    (data / "secret.key").chmod(0o600)
    (data / "mail.key").write_text("re_the_mail_key_12345\n")
    (data / "mail.key").chmod(0o600)
    (data / "reference.jpg").write_bytes(b"\xff\xd8 the reference picture \xff\xd9")
    day = data / "evidence" / "2026-10-09"
    day.mkdir(parents=True)
    (day / "stop-1.jpg").write_bytes(b"\xff\xd8 a pass \xff\xd9")
    (day / "stop-1.mp4").write_bytes(b"\x00\x00\x00\x18ftypmp42 a clip")
    return store


def rows(database: Path) -> dict[str, int]:
    """How many rows each table of a database file has."""
    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    try:
        names = [
            r[0] for r in connection.execute("select name from sqlite_master where type='table'")
        ]
        return {n: connection.execute(f'select count(*) from "{n}"').fetchone()[0] for n in names}
    finally:
        connection.close()


def unpack(archive: Path, into: Path) -> Path:
    with tarfile.open(archive) as tar:
        tar.extractall(into, filter="data")
    return into


def repack(archive: Path, edit) -> Path:  # type: ignore[no-untyped-def]
    """The same archive after ``edit(members)`` has changed its {name: bytes} (None removes)."""
    with tarfile.open(archive) as tar:
        members = {m.name: tar.extractfile(m).read() for m in tar.getmembers() if m.isfile()}  # type: ignore[union-attr]
    edit(members)
    out = archive.with_name("repacked.tar.gz")
    with tarfile.open(out, "w:gz") as tar:
        for name, data in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return out


@pytest.fixture(autouse=True)
def nothing_holds_the_database(monkeypatch: pytest.MonkeyPatch) -> None:
    """The tests that restore must not be stopped by this machine's own services."""
    monkeypatch.setattr(backup, "holders_of", lambda path: [])


@pytest.fixture
def data(tmp_path: Path) -> Path:
    folder = tmp_path / "data"
    populate(folder).close()
    return folder


# --- what is in a backup --------------------------------------------------------------------------


def test_every_table_is_either_history_or_configuration() -> None:
    """A table added later has to be placed on purpose, or --config-only would quietly keep or
    drop it."""
    placed = set(backup.HISTORY_TABLES) | set(backup.CONFIG_TABLES)
    assert placed == set(Base.metadata.tables)
    assert not set(backup.HISTORY_TABLES) & set(backup.CONFIG_TABLES)


def test_a_backup_holds_the_database_and_the_files_beside_it(tmp_path: Path, data: Path) -> None:
    archive = backup.create_backup(data, tmp_path / "out", now=NOW)
    assert archive.name == "trafficpi-backup-20261009-183005.tar.gz"
    assert stat.S_IMODE(archive.stat().st_mode) == 0o600  # it holds the password hash
    assert stat.S_IMODE(archive.parent.stat().st_mode) == 0o700
    with tarfile.open(archive) as tar:
        assert sorted(tar.getnames()) == [
            "mail.key",
            "manifest.json",
            "reference.jpg",
            "secret.key",
            "trafficpi.db",
        ]
        manifest = json.load(tar.extractfile("manifest.json"))  # type: ignore[arg-type]
    assert manifest["format"] == 1 and manifest["created"] == "2026-10-09T18:30:05Z"
    assert manifest["schema"] == head_revision() and manifest["config_only"] is False
    assert manifest["with_evidence"] is False
    assert manifest["rows"]["events"] == 31 and manifest["rows"]["trails"] == 20
    unpacked = unpack(archive, tmp_path / "look")
    assert rows(unpacked / "trafficpi.db") == rows(data / "trafficpi.db")
    assert (unpacked / "secret.key").read_text() == "the-session-key\n"
    assert (unpacked / "reference.jpg").read_bytes() == (data / "reference.jpg").read_bytes()


def test_the_default_place_is_a_backups_folder_in_the_data_directory(data: Path) -> None:
    archive = backup.create_backup(data, now=NOW)
    assert archive.parent == data / "backups" and archive.is_file()


def test_evidence_comes_along_only_when_asked_for(tmp_path: Path, data: Path) -> None:
    plain = backup.create_backup(data, tmp_path / "a", now=NOW)
    with tarfile.open(plain) as tar:
        assert not any(n.startswith("evidence") for n in tar.getnames())
    full = backup.create_backup(data, tmp_path / "b", with_evidence=True, now=NOW)
    with tarfile.open(full) as tar:
        names = tar.getnames()
        assert (
            "evidence/2026-10-09/stop-1.jpg" in names and "evidence/2026-10-09/stop-1.mp4" in names
        )
        assert json.load(tar.extractfile("manifest.json"))["with_evidence"] is True  # type: ignore[arg-type]


def test_config_only_leaves_the_history_out_and_keeps_everything_else(
    tmp_path: Path, data: Path
) -> None:
    archive = backup.create_backup(data, tmp_path / "out", config_only=True, now=NOW)
    copy = unpack(archive, tmp_path / "look") / "trafficpi.db"
    counts = rows(copy)
    assert counts["events"] == counts["minute_counts"] == counts["trails"] == 0
    assert counts["users"] == 1 and counts["profiles"] >= 1 and counts["settings"] >= 2
    assert counts["cameras"] == 1
    with tarfile.open(archive) as tar:
        assert json.load(tar.extractfile("manifest.json"))["config_only"] is True  # type: ignore[arg-type]
    assert rows(data / "trafficpi.db")["events"] == 31  # the live database was not touched


def test_the_mail_key_comes_with_a_config_only_backup_as_it_is_not_history(
    tmp_path: Path, data: Path
) -> None:
    archive = backup.create_backup(data, tmp_path / "out", config_only=True, now=NOW)
    with tarfile.open(archive) as tar:
        assert "mail.key" in tar.getnames()


def test_a_data_directory_without_a_mail_key_makes_a_backup_without_one(tmp_path: Path) -> None:
    folder = tmp_path / "data"
    populate(folder).close()
    (folder / "mail.key").unlink()
    with tarfile.open(backup.create_backup(folder, tmp_path / "out", now=NOW)) as tar:
        assert "mail.key" not in tar.getnames()


def test_restoring_a_backup_from_before_the_email_keeps_the_key_that_is_there(
    tmp_path: Path, data: Path
) -> None:
    (data / "mail.key").unlink()
    archive = backup.create_backup(data, tmp_path / "out", now=NOW)
    target = tmp_path / "target"
    open_store(target).close()
    (target / "mail.key").write_text("re_a_key_set_since_12345\n")
    backup.restore_backup(archive, target, now=NOW)
    assert (target / "mail.key").read_text() == "re_a_key_set_since_12345\n"


def test_a_backup_taken_while_the_services_write_is_a_consistent_snapshot(
    tmp_path: Path, data: Path
) -> None:
    """Every count is an event and a minute's total, written together: in a copy that is a
    snapshot the two always agree. Copying the files of a database in use would not give that."""
    store = open_store(data)
    stop = threading.Event()
    written = 0

    def keep_counting() -> None:
        nonlocal written
        while not stop.is_set():
            store.record_count(
                CountEvent(0, written, 2, "car", "main", 1), BASE_MS + 100_000 + written * 500
            )
            written += 1

    writer = threading.Thread(target=keep_counting)
    writer.start()
    try:
        archives = [backup.create_backup(data, tmp_path / f"out{i}", now=NOW) for i in range(4)]
    finally:
        stop.set()
        writer.join()
        store.close()
    assert written > 0
    for i, archive in enumerate(archives):
        copy = unpack(archive, tmp_path / f"look{i}") / "trafficpi.db"
        connection = sqlite3.connect(f"file:{copy}?mode=ro", uri=True)
        try:
            assert connection.execute("pragma integrity_check").fetchone() == ("ok",)
            events = connection.execute(
                "select count(*) from events where type='count'"
            ).fetchone()[0]
            total = connection.execute(
                "select coalesce(sum(count), 0) from minute_counts"
            ).fetchone()[0]
            assert events == total, f"copy {i}: {events} events but {total} counted"
        finally:
            connection.close()


def test_the_newest_few_are_kept_and_nothing_else_is_touched(tmp_path: Path, data: Path) -> None:
    out = tmp_path / "out"
    out.mkdir()
    (out / "notes.txt").write_text("not a backup")
    for hour in range(5):
        backup.create_backup(data, out, keep=3, now=NOW.replace(hour=hour))
    kept = sorted(p.name for p in out.glob("trafficpi-backup-*.tar.gz"))
    assert kept == [
        "trafficpi-backup-20261009-023005.tar.gz",
        "trafficpi-backup-20261009-033005.tar.gz",
        "trafficpi-backup-20261009-043005.tar.gz",
    ]
    assert (out / "notes.txt").read_text() == "not a backup"
    assert not list(out.glob(".*"))  # no half-made files left behind


def test_two_backups_in_the_same_second_do_not_overwrite_each_other(
    tmp_path: Path, data: Path
) -> None:
    first = backup.create_backup(data, tmp_path / "out", now=NOW)
    second = backup.create_backup(data, tmp_path / "out", now=NOW)
    assert first != second and first.is_file() and second.is_file()


def test_there_is_nothing_to_back_up_without_a_database(tmp_path: Path) -> None:
    with pytest.raises(backup.BackupError, match="no database"):
        backup.create_backup(tmp_path / "empty", tmp_path / "out")


def test_a_backup_that_cannot_be_finished_leaves_nothing_half_made(
    tmp_path: Path, data: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out = tmp_path / "out"

    def fails(*args: object, **kwargs: object) -> None:
        raise OSError("the card is full")

    monkeypatch.setattr(Path, "replace", fails)  # the last step: the finished file gets its name
    with pytest.raises(OSError):
        backup.create_backup(data, out, now=NOW)
    assert list(out.iterdir()) == []


# --- restoring ------------------------------------------------------------------------------------


def test_a_restore_brings_back_everything_that_was_backed_up(tmp_path: Path, data: Path) -> None:
    archive = backup.create_backup(data, tmp_path / "out", with_evidence=True, now=NOW)
    fresh = tmp_path / "new-card" / "data"
    report = backup.restore_backup(archive, fresh, now=NOW)
    assert report.schema == head_revision() and report.rows["events"] == 31
    assert report.set_aside is None
    restored = open_store(fresh)
    assert restored.user_count() == 1
    assert restored.get_setting("timezone") == "America/New_York"
    assert restored.get_profile() is not None
    assert restored.event_count() == 31 and restored.trail_count() == 20
    restored.close()
    assert (fresh / "secret.key").read_text() == "the-session-key\n"  # sessions survive
    assert (fresh / "reference.jpg").read_bytes() == (data / "reference.jpg").read_bytes()
    assert (fresh / "evidence" / "2026-10-09" / "stop-1.mp4").exists()
    assert stat.S_IMODE(fresh.stat().st_mode) == 0o700
    assert stat.S_IMODE((fresh / "secret.key").stat().st_mode) == 0o600
    assert (fresh / "mail.key").read_text() == "re_the_mail_key_12345\n"  # the email still works
    assert stat.S_IMODE((fresh / "mail.key").stat().st_mode) == 0o600
    assert rows(fresh / "trafficpi.db") == rows(data / "trafficpi.db")


def test_a_review_of_a_stop_is_part_of_the_history_that_comes_back(
    tmp_path: Path, data: Path
) -> None:
    archive = backup.create_backup(data, tmp_path / "out", now=NOW)
    fresh = tmp_path / "fresh"
    backup.restore_backup(archive, fresh, now=NOW)
    connection = sqlite3.connect(fresh / "trafficpi.db")
    try:
        assert connection.execute("select review from events where type='stop'").fetchone() == (
            "confirmed",
        )
    finally:
        connection.close()


def test_a_restore_sets_aside_what_it_replaces_instead_of_deleting_it(
    tmp_path: Path, data: Path
) -> None:
    archive = backup.create_backup(data, tmp_path / "out", now=NOW)
    target = tmp_path / "target"
    other = open_store(target)
    other.create_user("someone-else", "$argon2id$other")
    other.close()
    (target / "secret.key").write_text("the-other-key\n")
    report = backup.restore_backup(archive, target, now=NOW)
    aside = report.set_aside
    assert aside == target / "before-restore-20261009-183005"
    assert aside is not None
    old = rows(aside / "trafficpi.db")
    assert old["events"] == 0 and old["users"] == 1
    assert (aside / "secret.key").read_text() == "the-other-key\n"
    assert (target / "secret.key").read_text() == "the-session-key\n"
    assert stat.S_IMODE(aside.stat().st_mode) == 0o700


def test_evidence_is_replaced_only_if_the_backup_has_it(tmp_path: Path, data: Path) -> None:
    without = backup.create_backup(data, tmp_path / "a", now=NOW)
    target = tmp_path / "target"
    populate(target).close()
    (target / "evidence" / "2026-10-09" / "stop-1.jpg").write_bytes(b"keep me")
    backup.restore_backup(without, target, now=NOW)
    assert (target / "evidence" / "2026-10-09" / "stop-1.jpg").read_bytes() == b"keep me"


def test_a_restore_refuses_while_the_database_is_open_elsewhere(
    tmp_path: Path, data: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive = backup.create_backup(data, tmp_path / "out", now=NOW)
    target = tmp_path / "target"
    populate(target).close()
    before = rows(target / "trafficpi.db")
    monkeypatch.setattr(
        backup, "holders_of", lambda path: [(4242, "trafficpi-vision run"), (4243, "trafficpi-api")]
    )
    with pytest.raises(
        backup.BackupError, match=r"in use.*trafficpi-vision run \(pid 4242\).*systemctl stop"
    ):
        backup.restore_backup(archive, target, now=NOW)
    assert rows(target / "trafficpi.db") == before and not list(target.glob("before-restore-*"))


def test_this_process_holding_the_database_open_counts_as_using_it(tmp_path: Path) -> None:
    folder = tmp_path / "data"
    store = open_store(folder)
    try:
        holders = REAL_HOLDERS_OF(folder / "trafficpi.db")
        assert any(pid == os.getpid() for pid, _ in holders)
    finally:
        store.close()
    assert REAL_HOLDERS_OF(folder / "trafficpi.db") == []


def test_a_database_that_does_not_exist_has_no_holders(tmp_path: Path) -> None:
    assert REAL_HOLDERS_OF(tmp_path / "nowhere" / "trafficpi.db") == []


def test_a_backup_from_a_newer_version_is_refused_and_nothing_changes(
    tmp_path: Path, data: Path
) -> None:
    archive = backup.create_backup(data, tmp_path / "out", now=NOW)
    unpacked = unpack(archive, tmp_path / "look")
    connection = sqlite3.connect(unpacked / "trafficpi.db")
    connection.execute("update alembic_version set version_num = '9999'")
    connection.commit()
    connection.close()
    newer = repack(
        archive, lambda m: m.update({"trafficpi.db": (unpacked / "trafficpi.db").read_bytes()})
    )
    target = tmp_path / "target"
    populate(target).close()
    before = rows(target / "trafficpi.db")
    with pytest.raises(backup.BackupError, match="newer"):
        backup.restore_backup(newer, target, now=NOW)
    assert rows(target / "trafficpi.db") == before and not list(target.glob("before-restore-*"))


def test_an_older_schema_is_restored_and_brought_up_to_date(tmp_path: Path) -> None:
    """A backup made before the latest migration restores, and the database is migrated at once."""
    from alembic import command

    from trafficpi.db.session import _alembic_config, create_db_engine

    old = tmp_path / "old"
    old.mkdir()
    engine = create_db_engine(old / "trafficpi.db")
    with engine.begin() as connection:
        command.upgrade(_alembic_config(connection=connection), "0002")  # before the reviews
    old_store = Store(engine)
    old_store.ensure_camera()
    old_store.create_user("admin", "$argon2id$an-old-hash")
    old_store.close()
    archive = backup.create_backup(old, tmp_path / "out", now=NOW)
    target = tmp_path / "target"
    report = backup.restore_backup(archive, target, now=NOW)
    assert report.schema == "0002"
    connection = sqlite3.connect(target / "trafficpi.db")
    try:
        assert connection.execute("select version_num from alembic_version").fetchone() == (
            head_revision(),
        )
        assert connection.execute("select count(*) from users").fetchone() == (1,)
    finally:
        connection.close()


@pytest.mark.parametrize(
    "damage",
    [
        pytest.param(lambda m: m.pop("trafficpi.db"), id="no database"),
        pytest.param(lambda m: m.pop("manifest.json"), id="no manifest"),
        pytest.param(lambda m: m.update({"manifest.json": b"{not json"}), id="manifest unreadable"),
        pytest.param(
            lambda m: m.update({"manifest.json": json.dumps({"format": 2}).encode()}),
            id="made by a newer format",
        ),
        pytest.param(
            lambda m: m.update({"trafficpi.db": b"SQLite format 3\0" + b"\xff" * 4000}),
            id="garbage database",
        ),
        pytest.param(
            lambda m: m.update({"trafficpi.db": b"this is not a database at all"}),
            id="not a database",
        ),
        pytest.param(lambda m: m.update({"../escape.txt": b"x"}), id="path leaving the folder"),
        pytest.param(lambda m: m.update({"/tmp/absolute.txt": b"x"}), id="absolute path"),
        pytest.param(
            lambda m: m.update({"evidence/../../up.txt": b"x"}), id="evidence path leaving it"
        ),
        pytest.param(lambda m: m.update({"notes.txt": b"x"}), id="a file that does not belong"),
    ],
)
def test_a_damaged_or_hostile_archive_is_refused_and_nothing_changes(
    tmp_path: Path, data: Path, damage
) -> None:  # type: ignore[no-untyped-def]
    archive = backup.create_backup(data, tmp_path / "out", now=NOW)
    bad = repack(archive, damage)
    target = tmp_path / "target"
    populate(target).close()
    before = rows(target / "trafficpi.db")
    with pytest.raises(backup.BackupError):
        backup.restore_backup(bad, target, now=NOW)
    assert rows(target / "trafficpi.db") == before
    assert not list(target.glob("before-restore-*")) and not list(target.glob(".restore-*"))
    assert not (tmp_path / "escape.txt").exists() and not Path("/tmp/absolute.txt").exists()


def test_a_link_in_an_archive_is_refused(tmp_path: Path, data: Path) -> None:
    archive = backup.create_backup(data, tmp_path / "out", now=NOW)
    evil = tmp_path / "evil.tar.gz"
    with tarfile.open(archive) as source, tarfile.open(evil, "w:gz") as tar:
        for member in source.getmembers():
            tar.addfile(member, source.extractfile(member) if member.isfile() else None)
        link = tarfile.TarInfo("secret.key.link")
        link.type = tarfile.SYMTYPE
        link.linkname = "/etc/passwd"
        tar.addfile(link)
    target = tmp_path / "target"
    with pytest.raises(backup.BackupError):
        backup.restore_backup(evil, target, now=NOW)
    assert not (target / "secret.key.link").exists()


def test_something_that_is_not_an_archive_is_refused(tmp_path: Path) -> None:
    junk = tmp_path / "junk.tar.gz"
    junk.write_bytes(b"not a tar file")
    with pytest.raises(backup.BackupError, match="not a TrafficPi backup"):
        backup.restore_backup(junk, tmp_path / "target")
    with pytest.raises(backup.BackupError, match="no such file"):
        backup.restore_backup(tmp_path / "missing.tar.gz", tmp_path / "target")


def test_a_failure_after_the_files_are_in_place_puts_the_old_ones_back(
    tmp_path: Path, data: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive = backup.create_backup(data, tmp_path / "out", now=NOW)
    target = tmp_path / "target"
    populate(target).close()
    before = rows(target / "trafficpi.db")
    key = (target / "secret.key").read_text()

    def cannot_open(path: Path) -> Store:
        raise RuntimeError("the restored database would not open")

    monkeypatch.setattr(backup, "open_store", cannot_open)
    with pytest.raises(backup.BackupError, match="would not open"):
        backup.restore_backup(archive, target, now=NOW)
    assert rows(target / "trafficpi.db") == before
    assert (target / "secret.key").read_text() == key


def test_the_restored_files_log_in_as_before(tmp_path: Path) -> None:
    """The point of a backup: a new card, the same password and the same sessions."""
    from fastapi.testclient import TestClient

    from trafficpi.api.app import create_app
    from trafficpi.api.auth import hash_password
    from trafficpi.api.settings import ApiSettings

    original = tmp_path / "original"
    store = open_store(original)
    store.create_user("admin", hash_password("a long enough password"))
    store.close()
    # the old machine's API runs (which makes the session key) and someone signs in
    with TestClient(create_app(ApiSettings(data_dir=original, dashboard_dir=None))) as c:
        assert (
            c.post(
                "/api/auth/login", json={"username": "admin", "password": "a long enough password"}
            ).status_code
            == 204
        )
        cookie = c.cookies["trafficpi_session"]
    archive = backup.create_backup(original, tmp_path / "out", now=NOW)
    fresh = tmp_path / "fresh"
    backup.restore_backup(archive, fresh, now=NOW)
    with TestClient(create_app(ApiSettings(data_dir=fresh, dashboard_dir=None))) as c:
        c.cookies.set("trafficpi_session", cookie)
        # same key, same user, same hash
        assert c.get("/api/auth/me").json()["username"] == "admin"


def test_without_a_given_time_a_backup_is_named_by_the_utc_clock(
    data: Path, tmp_path: Path
) -> None:
    archive = backup.create_backup(data, tmp_path / "out")
    stamp = archive.name.removeprefix("trafficpi-backup-").removesuffix(".tar.gz")
    parsed = datetime.strptime(stamp, "%Y%m%d-%H%M%S").replace(tzinfo=UTC)
    assert abs((datetime.now(UTC) - parsed).total_seconds()) < 120
