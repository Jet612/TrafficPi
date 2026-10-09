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
