"""Backup and restore of the database and the files beside it (plan.md D29).

A backup is one ``.tar.gz``, readable by its owner only (it holds the password hash, the key that
signs the sessions and the email provider's key): a ``manifest.json``, a consistent copy of the
database, the session key, the mail key if there is one, the reference picture and, if asked for,
the evidence. The copy is made with SQLite's ``VACUUM INTO``,
which reads one snapshot of the database without stopping the services that write to it; copying
the files of a database that is in use could catch it half way between two writes.

A restore checks the archive and the database in it before it touches anything, sets what it
replaces aside instead of deleting it, and puts the old files back if the restored database does
not open.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import tarfile
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

from trafficpi.core.paths import ensure_private_dir, evidence_dir, reference_file
from trafficpi.db.session import known_schema
from trafficpi.db.store import open_store

FORMAT = 1  # of the archive; a restore refuses a newer one
DATABASE = "trafficpi.db"
SECRET = "secret.key"
MAIL_KEY = "mail.key"  # the email provider's key (plan.md D32), if one was set
REFERENCE = reference_file(Path()).name
EVIDENCE = evidence_dir(Path()).name
MANIFEST = "manifest.json"
PREFIX = "trafficpi-backup-"
SUFFIX = ".tar.gz"

# Every table is one or the other, on purpose: `--config-only` drops the first and keeps the
# second, and a test fails when a table is added that is in neither. (What a person made of a stop
# event, confirmed or false alarm, lives in `events` and so goes with the history. The daily email's
# recipients are configuration; what was sent, and to whom it got, is history.)
HISTORY_TABLES = (
    "events",
    "minute_counts",
    "trails",
    "health_samples",
    "mail_sends",
    "mail_deliveries",
)
CONFIG_TABLES = ("cameras", "users", "settings", "profiles", "mail_recipients")

_PERMITTED_FILES = {MANIFEST, DATABASE, SECRET, MAIL_KEY, REFERENCE}
_ROOM_FACTOR = 2  # a backup or a restore needs about twice the size of what it holds


class BackupError(RuntimeError):
    """A backup or a restore that cannot go ahead; the message says why, in words."""


@dataclass(frozen=True, slots=True)
class RestoreReport:
    schema: str  # the database schema the backup was made with
    created: str
    rows: dict[str, int]
    config_only: bool
    with_evidence: bool
    set_aside: Path | None  # where what was replaced went, if anything was


# --- making a backup ----------------------------------------------------------------------------


def create_backup(
    data_dir: Path,
    to_dir: Path | None = None,
    *,
    config_only: bool = False,
    with_evidence: bool = False,
    keep: int | None = None,
    now: datetime | None = None,
) -> Path:
    """Write a backup of ``data_dir`` into ``to_dir`` (default: ``backups`` inside it); returns
    its path. ``keep`` limits how many backups stay in the folder, oldest going first."""
    database = data_dir / DATABASE
    if not database.is_file():
        raise BackupError(f"no database in {data_dir}: there is nothing to back up")
    to_dir = to_dir if to_dir is not None else data_dir / "backups"
    ensure_private_dir(to_dir)
    _require_room(
        to_dir, _size_of(database) + (_size_of(evidence_dir(data_dir)) if with_evidence else 0)
    )
    moment = (now or datetime.now(UTC)).astimezone(UTC)
    final = _free_name(to_dir, moment)
    partial = to_dir / f".{final.name}.partial"
    try:
        with tempfile.TemporaryDirectory(dir=to_dir, prefix=".work-") as work:
            copy = Path(work) / DATABASE
            _copy_database(database, copy, config_only=config_only)
            schema, rows = _describe(copy)
            manifest: dict[str, Any] = {
                "format": FORMAT,
                "created": moment.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "schema": schema,
                "config_only": config_only,
                "with_evidence": with_evidence,
                "rows": rows,
            }
            with tarfile.open(partial, "w:gz") as tar:
                _add_bytes(tar, MANIFEST, json.dumps(manifest, indent=1).encode())
                _add_file(tar, copy, DATABASE)
                for name in (SECRET, MAIL_KEY, REFERENCE):
                    if (data_dir / name).is_file():
                        _add_file(tar, data_dir / name, name)
                if with_evidence:
                    for path in _files_under(evidence_dir(data_dir)):
                        _add_file(
                            tar,
                            path,
                            f"{EVIDENCE}/{path.relative_to(evidence_dir(data_dir)).as_posix()}",
                        )
            partial.chmod(0o600)
            partial.replace(final)
    finally:
        partial.unlink(missing_ok=True)
    if keep is not None and keep > 0:
        _prune(to_dir, keep)
    return final


def _copy_database(source: Path, target: Path, *, config_only: bool) -> None:
    """A consistent copy of the database at one moment, made while others read and write it."""
    connection = sqlite3.connect(source, timeout=30)
    try:
        connection.execute("VACUUM INTO ?", (str(target),))
    finally:
        connection.close()
    if config_only:
        copy = sqlite3.connect(target)
        try:
            for table in HISTORY_TABLES:
                copy.execute(f'DELETE FROM "{table}"')  # names are ours, not input
            copy.commit()
            copy.execute("VACUUM")  # the space the history took goes back
        finally:
            copy.close()


def _describe(database: Path) -> tuple[str, dict[str, int]]:
    """The schema revision of a database file and how many rows each table holds."""
    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    try:
        row = connection.execute("SELECT version_num FROM alembic_version").fetchone()
        tables = [
            name
            for (name,) in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        ]
        counts = {
            t: connection.execute(f'SELECT count(*) FROM "{t}"').fetchone()[0] for t in tables
        }
    finally:
        connection.close()
    return (row[0] if row else ""), counts


def _free_name(folder: Path, moment: datetime) -> Path:
    base = f"{PREFIX}{moment:%Y%m%d-%H%M%S}"
    name = folder / f"{base}{SUFFIX}"
    n = 2
    while name.exists():
        name = folder / f"{base}_{n}{SUFFIX}"  # "_" sorts after "." so the order stays right
        n += 1
    return name


def _prune(folder: Path, keep: int) -> None:
    backups = sorted(
        (p for p in folder.glob(f"{PREFIX}*{SUFFIX}") if p.is_file()),
        key=lambda p: (p.stat().st_mtime_ns, p.name),
    )
    for old in backups[: max(0, len(backups) - keep)]:
        old.unlink()


def _size_of(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    return sum(p.stat().st_size for p in _files_under(path))


def _files_under(folder: Path) -> Iterator[Path]:
    if folder.is_dir():
        yield from sorted(p for p in folder.rglob("*") if p.is_file() and not p.is_symlink())


def _require_room(folder: Path, needed: int) -> None:
    free = shutil.disk_usage(folder).free
    if free < _ROOM_FACTOR * needed:
        raise BackupError(
            f"not enough room in {folder}: {free // 10**6} MB free, and this needs about "
            f"{_ROOM_FACTOR * needed // 10**6} MB"
        )


def _add_bytes(tar: tarfile.TarFile, name: str, data: bytes) -> None:
    import io

    info = tarfile.TarInfo(name)
    info.size = len(data)
    info.mode = 0o600
    info.mtime = int(datetime.now(UTC).timestamp())
    tar.addfile(info, io.BytesIO(data))


def _add_file(tar: tarfile.TarFile, path: Path, name: str) -> None:
    info = tar.gettarinfo(str(path), arcname=name)
    info.uid = info.gid = 0
    info.uname = info.gname = ""
    info.mode = 0o600
    with path.open("rb") as handle:
        tar.addfile(info, handle)


# --- restoring ----------------------------------------------------------------------------------


def holders_of(database: Path) -> list[tuple[int, str]]:
    """The processes (id and command) that have the database open, as far as this user can see.

    A restore must not run under a service that has the old database open: it would go on writing
    to files that are no longer the database. This asks the kernel rather than systemd, so it also
    catches a demo run by hand, and ignores services that use another data directory.
    """
    wanted = {
        str(path.resolve()) for path in (database, Path(f"{database}-wal"), Path(f"{database}-shm"))
    }
    found: list[tuple[int, str]] = []
    try:
        processes = [p for p in Path("/proc").iterdir() if p.name.isdigit()]
    except OSError:
        return found  # not Linux: nothing to ask
    for process in processes:
        if not _has_open(process, wanted):
            continue
        try:
            command = (
                (process / "cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace")
            )
        except OSError:  # it ended in the meantime
            command = ""
        found.append((int(process.name), command.strip()[:100]))
    return found


def _has_open(process: Path, wanted: set[str]) -> bool:
    """Whether a process (a /proc entry) has one of the files open. A descriptor can close between
    the listing and the look at it (the listing's own does), so each is looked at on its own."""
    try:
        descriptors = list((process / "fd").iterdir())
    except OSError:  # one that belongs to someone else, or has just ended
        return False
    for descriptor in descriptors:
        try:
            if str(descriptor.readlink()) in wanted:
                return True
        except OSError:
            continue
    return False


def restore_backup(archive: Path, data_dir: Path, *, now: datetime | None = None) -> RestoreReport:
    """Replace the files in ``data_dir`` with those in a backup. Nothing is changed if the archive
    or the database in it is not sound; what is replaced is set aside, not deleted."""
    if not archive.is_file():
        raise BackupError(f"no such file: {archive}")
    holders = holders_of(data_dir / DATABASE)
    if holders:
        users = ", ".join(f"{command or 'a process'} (pid {pid})" for pid, command in holders)
        raise BackupError(
            f"the database is in use by {users}; stop the services first: "
            "sudo systemctl stop trafficpi-vision trafficpi-api"
        )
    ensure_private_dir(data_dir)
    work = Path(tempfile.mkdtemp(dir=data_dir, prefix=".restore-"))
    try:
        manifest = _unpack(archive, work, data_dir)
        _check_database(work / DATABASE)
        return _put_in_place(work, manifest, data_dir, now)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _unpack(archive: Path, work: Path, data_dir: Path) -> dict[str, Any]:
    """Check every member of the archive, and only then write any of it into ``work``."""
    try:
        with tarfile.open(archive, "r:*") as tar:
            members = tar.getmembers()
            for member in members:
                _check_member(member)
            names = {m.name for m in members if m.isfile()}
            if MANIFEST not in names:
                raise BackupError(f"{archive.name} has no {MANIFEST}: it is not a TrafficPi backup")
            if DATABASE not in names:
                raise BackupError(f"{archive.name} holds no database")
            _require_room(data_dir, sum(m.size for m in members if m.isfile()))
            for member in members:
                target = work / member.name
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                source = tar.extractfile(member)
                if source is None:
                    raise BackupError(f"{member.name} in the archive cannot be read")
                with source, target.open("wb") as out:
                    shutil.copyfileobj(source, out)
    except (tarfile.TarError, OSError, EOFError) as exc:
        raise BackupError(f"{archive.name} is not a TrafficPi backup ({exc})") from exc
    try:
        manifest = json.loads((work / MANIFEST).read_text(encoding="utf-8"))
    except (ValueError, OSError) as exc:
        raise BackupError(f"the {MANIFEST} in the backup cannot be read ({exc})") from exc
    if not isinstance(manifest, dict) or not isinstance(manifest.get("format"), int):
        raise BackupError(f"the {MANIFEST} in the backup is not one of ours")
    if manifest["format"] > FORMAT:
        raise BackupError(
            f"this backup was made by a newer TrafficPi (archive format {manifest['format']}); "
            "update TrafficPi before restoring it"
        )
    return manifest


def _check_member(member: tarfile.TarInfo) -> None:
    """A backup holds a few plain files and, if it has evidence, that folder: nothing else, and
    nothing that points out of the folder it is unpacked into."""
    name = member.name
    path = PurePosixPath(name)
    clean = (
        name == str(path)
        and not path.is_absolute()
        and ".." not in path.parts
        and "\\" not in name
        and name not in ("", ".")
    )
    inside_evidence = clean and path.parts[0] == EVIDENCE
    if member.isdir():
        allowed = inside_evidence
    elif member.isfile():
        allowed = clean and (name in _PERMITTED_FILES or (inside_evidence and len(path.parts) > 1))
    else:  # links, devices, fifos
        allowed = False
    if not allowed:
        raise BackupError(f"the archive holds {name!r}, which a TrafficPi backup does not")


def _check_database(database: Path) -> None:
    try:
        connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
        try:
            verdict = connection.execute("PRAGMA integrity_check").fetchall()
            row = connection.execute("SELECT version_num FROM alembic_version").fetchone()
        finally:
            connection.close()
    except sqlite3.DatabaseError as exc:
        raise BackupError(f"the database in the backup cannot be read ({exc})") from exc
    if verdict != [("ok",)]:
        raise BackupError(f"the database in the backup is damaged ({verdict[0][0]})")
    schema = row[0] if row else ""
    if not known_schema(schema):
        raise BackupError(
            f"the backup was made by a newer TrafficPi (database schema {schema!r}); update "
            "TrafficPi before restoring it"
        )


def _put_in_place(
    work: Path, manifest: dict[str, Any], data_dir: Path, now: datetime | None
) -> RestoreReport:
    moment = (now or datetime.now(UTC)).astimezone(UTC)
    with_evidence = bool(manifest.get("with_evidence")) and (work / EVIDENCE).is_dir()
    incoming = [n for n in (DATABASE, SECRET, MAIL_KEY, REFERENCE) if (work / n).exists()]
    if with_evidence:
        incoming.append(EVIDENCE)
    wal_and_shm = [f"{DATABASE}-wal", f"{DATABASE}-shm"]
    # What the old database left beside it belongs to the old database, never to the new one.
    replaced = [*incoming, *wal_and_shm]
    aside = data_dir / f"before-restore-{moment:%Y%m%d-%H%M%S}"
    set_aside: list[str] = []
    installed: list[str] = []
    try:
        for name in replaced:
            if (data_dir / name).exists():
                ensure_private_dir(aside)
                (data_dir / name).replace(aside / name)
                set_aside.append(name)
        for name in incoming:
            (work / name).replace(data_dir / name)
            installed.append(name)
            _make_private(data_dir / name)
        try:
            open_store(data_dir).close()  # the restored database must open (and is migrated)
        except Exception as exc:
            raise BackupError(
                f"the restored database would not open ({exc}); the old files are back in place"
            ) from exc
    except BaseException:
        _roll_back(data_dir, aside, installed, set_aside)
        raise
    return RestoreReport(
        schema=str(manifest.get("schema", "")),
        created=str(manifest.get("created", "")),
        rows={str(k): int(v) for k, v in dict(manifest.get("rows", {})).items()},
        config_only=bool(manifest.get("config_only")),
        with_evidence=with_evidence,
        set_aside=aside if set_aside else None,
    )


def _roll_back(data_dir: Path, aside: Path, installed: list[str], set_aside: list[str]) -> None:
    """Take out what a failed restore put in, and put back what it had set aside."""
    leftovers = [f"{DATABASE}-wal", f"{DATABASE}-shm"] if DATABASE in installed else []
    for name in [*installed, *leftovers]:  # the failed database's own WAL files go with it
        path = data_dir / name
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
        else:
            path.unlink(missing_ok=True)
    for name in set_aside:
        (aside / name).replace(data_dir / name)
    if aside.is_dir() and not any(aside.iterdir()):
        aside.rmdir()


def _make_private(path: Path) -> None:
    if path.is_dir():
        for folder in [path, *(p for p in path.rglob("*") if p.is_dir())]:
            folder.chmod(0o700)
        for file in (p for p in path.rglob("*") if p.is_file()):
            file.chmod(0o600)
    else:
        path.chmod(0o600)
