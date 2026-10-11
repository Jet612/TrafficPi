"""Where the API service finds its files and its neighbours."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from trafficpi.core.paths import default_data_dir, reference_file


def _default_dashboard() -> Path | None:
    """The built dashboard in a source checkout (``dashboard/out``), if it exists."""
    candidate = Path(__file__).resolve().parents[3] / "dashboard" / "out"
    return candidate if candidate.is_dir() else None


def _env_list(name: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in os.environ.get(name, "").split(",") if part.strip())


def normalize_origin(origin: str) -> str:
    """How origins are compared: no trailing slash, any case."""
    return origin.strip().rstrip("/").lower()


@dataclass(frozen=True, slots=True)
class ApiSettings:
    data_dir: Path = field(default_factory=default_data_dir)
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
    # from CF-Connecting-IP / X-Forwarded-For, but only when the connection itself comes from one
    # of `trusted_proxies` (a client on the LAN could write those headers too). Leave it off unless
    # a trusted proxy sets them. `--trust-proxy-headers` or TRAFFICPI_TRUST_PROXY_HEADERS=1.
    trust_proxy_headers: bool = field(
        default_factory=lambda: os.environ.get("TRAFFICPI_TRUST_PROXY_HEADERS") == "1"
    )
    # Whose word about the client address is taken: cloudflared runs on this machine, so the
    # loopback addresses; `--trusted-proxy` or TRAFFICPI_TRUSTED_PROXIES add others.
    trusted_proxies: tuple[str, ...] = field(
        default_factory=lambda: ("127.0.0.1", "::1", *_env_list("TRAFFICPI_TRUSTED_PROXIES"))
    )
    # Page origins, besides the API's own, whose requests are accepted (the Origin check refuses
    # all others). For development only: the dashboard's dev server runs on another port, so
    # `--allow-origin http://127.0.0.1:3100`. Anything listed here can act as a logged-in user.
    allowed_origins: tuple[str, ...] = field(
        default_factory=lambda: tuple(
            normalize_origin(o) for o in _env_list("TRAFFICPI_ALLOWED_ORIGINS")
        )
    )
    # Failed logins allowed per window, per client address and per username.
    login_attempts: int = 8
    login_window_s: int = 600
    # Password hashes that may run at once (each takes 64 MiB); a login that finds all slots taken
    # is turned away with a 503 instead of waiting.
    hashing_slots: int = 2

    # How the daily email is sent: "resend", the real thing, or "file", which writes
    # each mail into ``outbox`` instead, for the demo, the browser test and for trying it out.
    # `--mail-transport` or TRAFFICPI_MAIL_TRANSPORT; anything else is "resend".
    mail_transport: str = field(
        default_factory=lambda: (
            "file" if os.environ.get("TRAFFICPI_MAIL_TRANSPORT") == "file" else "resend"
        )
    )

    @property
    def db_path(self) -> Path:
        return self.data_dir / "trafficpi.db"

    @property
    def secret_path(self) -> Path:
        return self.data_dir / "secret.key"

    @property
    def reference_path(self) -> Path:
        """The camera's reference picture: what the lines and zones were drawn on."""
        return reference_file(self.data_dir)

    @property
    def mail_key_path(self) -> Path:
        """The email provider's key: a file of its own, readable by its owner only."""
        return self.data_dir / "mail.key"

    @property
    def outbox_path(self) -> Path:
        """Where the file sender writes its mails."""
        return self.data_dir / "outbox"
