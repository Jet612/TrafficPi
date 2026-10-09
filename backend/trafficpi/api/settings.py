"""Where the API service finds its files and its neighbours."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _default_dashboard() -> Path | None:
    """The built dashboard in a source checkout (``dashboard/out``), if it exists."""
    candidate = Path(__file__).resolve().parents[3] / "dashboard" / "out"
    return candidate if candidate.is_dir() else None


@dataclass(frozen=True, slots=True)
class ApiSettings:
    data_dir: Path = field(
        default_factory=lambda: Path(os.environ.get("TRAFFICPI_DATA_DIR", "data"))
    )
    vision_url: str = field(
        default_factory=lambda: os.environ.get("TRAFFICPI_VISION_URL", "http://127.0.0.1:8001")
    )
    dashboard_dir: Path | None = field(
        default_factory=lambda: (
            Path(os.environ["TRAFFICPI_DASHBOARD_DIR"])
            if "TRAFFICPI_DASHBOARD_DIR" in os.environ
            else _default_dashboard()
        )
    )
    session_max_age_s: int = 7 * 24 * 3600
    # Behind a tunnel every request arrives from localhost; with this on, the client address comes
    # from CF-Connecting-IP / X-Forwarded-For. Leave it off unless a trusted proxy sets them.
    trust_proxy_headers: bool = False
    # Failed logins allowed per window, per client address and per username.
    login_attempts: int = 8
    login_window_s: int = 600

    @property
    def db_path(self) -> Path:
        return self.data_dir / "trafficpi.db"

    @property
    def secret_path(self) -> Path:
        return self.data_dir / "secret.key"
