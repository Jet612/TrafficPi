"""What the status page says about the machine around the services: how much
the data directory holds, when the last backup was made, and what the installer's memory test said.

Only plain facts read from the files, never anything from the network, so that the status page
costs the Pi nothing worth noticing; the sizes, which mean walking the evidence folder, are worked
out at most once in a while (:class:`TtlCache`).
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import TypedDict

from trafficpi.core.paths import evidence_dir


class Usage(TypedDict):
    database_mb: float
    evidence_mb: float
    backups_mb: float
    evidence_files: int
    backup_files: int


class BackupFound(TypedDict):
    at_ms: int
    size_mb: float
    count: int


class MemtestFound(TypedDict):
    at_ms: int
    mb: int
    ok: bool


MB = 1_000_000  # as the disk and the evidence limit count them (a thousand to the gigabyte)
BACKUP_PATTERN = "trafficpi-backup-*.tar.gz"


def _tree(folder: Path) -> tuple[int, int]:
    """The bytes and the files under a folder, without following links out of it."""
    total = files = 0
    pending = [folder]
    while pending:
        try:
            for entry in pending.pop().iterdir():
                if entry.is_symlink():
                    continue
                if entry.is_dir():
                    pending.append(entry)
                elif entry.is_file():
                    total += entry.stat().st_size
                    files += 1
        except OSError:  # a folder that is not there, or one that goes in the middle of this
            continue
    return total, files


def _mb(size: int) -> float:
    return round(size / MB, 1)


def data_usage(data_dir: Path) -> Usage:
    """What the database, the evidence and the backups take, in megabytes, and how many files."""
    database = sum(
        (data_dir / name).stat().st_size
        for name in ("trafficpi.db", "trafficpi.db-wal", "trafficpi.db-shm")
        if (data_dir / name).is_file()
    )
    evidence, evidence_files = _tree(evidence_dir(data_dir))
    backups = [p for p in (data_dir / "backups").glob(BACKUP_PATTERN) if p.is_file()]
    return {
        "database_mb": _mb(database),
        "evidence_mb": _mb(evidence),
        "backups_mb": _mb(sum(p.stat().st_size for p in backups)),
        "evidence_files": evidence_files,
        "backup_files": len(backups),
    }


def newest_backup(data_dir: Path) -> BackupFound | None:
    """The newest backup in the usual folder: when it was made, how big it is, how many there are.

    (A backup written somewhere else with ``--backup-to`` is not seen from here.)
    """
    found = [p for p in (data_dir / "backups").glob(BACKUP_PATTERN) if p.is_file()]
    if not found:
        return None
    newest = max(found, key=lambda p: p.stat().st_mtime_ns)
    return {
        "at_ms": newest.stat().st_mtime_ns // 1_000_000,
        "size_mb": _mb(newest.stat().st_size),
        "count": len(found),
    }


def memtest_result(data_dir: Path) -> MemtestFound | None:
    """What the installer's memory test found (``memtest.json``), or None if it did not run or
    its file cannot be read."""
    try:
        document = json.loads((data_dir / "memtest.json").read_text(encoding="utf-8"))
        if not (
            isinstance(document, dict)
            and isinstance(document.get("at"), str)
            and isinstance(document.get("mb"), int)
            and not isinstance(document.get("mb"), bool)
            and isinstance(document.get("ok"), bool)
        ):
            return None
        at = datetime.fromisoformat(document["at"].replace("Z", "+00:00")).astimezone(UTC)
    except (OSError, ValueError):
        return None
    return {"at_ms": int(at.timestamp() * 1000), "mb": document["mb"], "ok": document["ok"]}


class TtlCache:
    """Remembers the sizes of the data directory for a while: the status page asks every few
    seconds, and walking thousands of evidence files that often would be a waste."""

    def __init__(self, ttl_s: float = 30.0, clock: Callable[[], float] = time.monotonic) -> None:
        self._ttl = ttl_s
        self._clock = clock
        self._at = float("-inf")
        self._value: Usage | None = None

    def usage(self, data_dir: Path) -> Usage:
        now = self._clock()
        if self._value is None or now - self._at >= self._ttl:
            self._value = data_usage(data_dir)
            self._at = now
        return self._value
