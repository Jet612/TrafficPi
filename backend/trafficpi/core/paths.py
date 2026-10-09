"""Where TrafficPi keeps what it makes while it runs: database, secret key, pictures, clips."""

from __future__ import annotations

import os
from pathlib import Path

DATA_DIR_ENV = "TRAFFICPI_DATA_DIR"
DATA_DIR_HELP = f"default: ${DATA_DIR_ENV}, else backend/data in the checkout"


def default_data_dir(source: Path | None = None) -> Path:
    """``$TRAFFICPI_DATA_DIR``, else ``data`` in the backend directory, else ``./data``.

    Where this points must not depend on the directory a command happens to be run from. It once
    did: a password set with ``trafficpi-admin`` from backend/ went into ``./data``, a database the
    systemd services (given another directory) never read. The backend directory is found from
    where this file is, which holds for a checkout; anywhere else (no ``pyproject.toml`` beside the
    package) the current directory is the fallback. ``source`` is the file to look from; only the
    tests pass it.
    """
    configured = os.environ.get(DATA_DIR_ENV)
    if configured:
        return Path(configured)
    backend = (source or Path(__file__)).resolve().parents[2]
    return backend / "data" if (backend / "pyproject.toml").is_file() else Path("data")


def ensure_private_dir(path: Path) -> None:
    """Create ``path`` if it is missing and keep it to its owner alone (mode 700).

    The data directory holds the password hash, the session key, the database and pictures of the
    street. Everything inside is as private as the folder is, so this one mode covers all of it,
    whatever mode each file was given. Only the owner of a directory can change its mode, so a
    directory that belongs to someone else is left as it is.
    """
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.stat().st_uid == os.getuid():
        path.chmod(0o700)


def evidence_dir(data_dir: Path) -> Path:
    """Where the pictures and clips of stop-sign passes are kept. Paths in the database are
    relative to the data directory, so they begin with this folder's name."""
    return data_dir / "evidence"


def reference_file(data_dir: Path) -> Path:
    """The picture the lines and zones were drawn on, kept when the owner saves it in the editor.
    The camera-drift check compares the live picture with it."""
    return data_dir / "reference.jpg"
