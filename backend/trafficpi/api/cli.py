"""Command-line entry point for the dashboard and API service: ``trafficpi-api``."""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import logging
import socket
import sys
from pathlib import Path
from types import FrameType

import uvicorn
from fastapi import FastAPI
from starlette.concurrency import run_in_threadpool

from trafficpi.api.app import create_app
from trafficpi.api.settings import ApiSettings, normalize_origin
from trafficpi.core.notify import notify, watchdog_period
from trafficpi.core.paths import DATA_DIR_HELP

log = logging.getLogger("trafficpi.api")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="trafficpi-api", description="Serve the TrafficPi dashboard and API."
    )
    parser.add_argument(
        "--host",
        default="0.0.0.0",
        help="address to listen on (default: all interfaces, so the dashboard works on the LAN; "
        "use 127.0.0.1 behind a tunnel)",
    )
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--data-dir", type=Path, help=DATA_DIR_HELP)
    parser.add_argument(
        "--vision-url", help="the vision service (default: $TRAFFICPI_VISION_URL, else :8001)"
    )
    parser.add_argument("--dashboard-dir", type=Path, help="the built dashboard (dashboard/out)")
    parser.add_argument(
        "--allow-origin",
        action="append",
        default=[],
        metavar="ORIGIN",
        help="also accept requests from a page at this origin, for example http://127.0.0.1:3100, "
        "the dashboard's development server. Development only: a listed origin can act as a "
        "logged-in user. May be repeated.",
    )
    parser.add_argument(
        "--trust-proxy-headers",
        action="store_true",
        help="take the client address from CF-Connecting-IP / X-Forwarded-For; only behind a "
        "trusted proxy such as a Cloudflare Tunnel, and only for connections from 127.0.0.1 and "
        "::1 (cloudflared runs on this machine) or from an address given with --trusted-proxy",
    )
    parser.add_argument(
        "--trusted-proxy",
        action="append",
        default=[],
        metavar="ADDRESS",
        help="also believe the proxy headers of connections from this address (with "
        "--trust-proxy-headers); for a proxy on another machine. May be repeated.",
    )
    return parser


class Server(uvicorn.Server):
    """A uvicorn server that tells the app when shutdown starts, and tells systemd it is alive.

    Browsers keep the preview stream and the live socket open for as long as the dashboard is
    showing; the app ends them on this signal instead of making shutdown wait for them.

    Under systemd (Type=notify, WatchdogSec=) it says READY=1 once it listens, and while it still
    answers it sends WATCHDOG=1 (plan.md D27): a server whose event loop is blocked, or whose
    thread pool is used up so that no request that touches the database can run, goes quiet and
    is restarted. By hand none of this sends anything.
    """

    def __init__(self, config: uvicorn.Config, app: FastAPI) -> None:
        super().__init__(config)
        self._app = app
        self._heartbeat: asyncio.Task[None] | None = None

    def handle_exit(self, sig: int, frame: FrameType | None) -> None:
        self._app.state.ctx.stopping.set()
        super().handle_exit(sig, frame)

    async def startup(self, sockets: list[socket.socket] | None = None) -> None:
        await super().startup(sockets=sockets)
        if self.started:
            notify("READY=1")
            period = watchdog_period()
            if period is not None:
                self._heartbeat = asyncio.create_task(self._keep_alive(period))

    async def shutdown(self, sockets: list[socket.socket] | None = None) -> None:
        if self._heartbeat is not None:
            self._heartbeat.cancel()
        await super().shutdown(sockets=sockets)

    async def _keep_alive(self, period: float) -> None:
        """Send WATCHDOG=1 every ``period`` seconds, if a worker thread can still be had."""
        while True:
            await asyncio.sleep(period)
            try:
                # What any blocking endpoint needs first: the loop is running (this ran), and a
                # thread of the pool takes a job and gives it back.
                await asyncio.wait_for(run_in_threadpool(lambda: None), timeout=period)
            except TimeoutError:
                log.error("no worker thread answers; not telling the watchdog")
                continue
            notify("WATCHDOG=1")


def settings_from(args: argparse.Namespace) -> ApiSettings:
    overrides = {
        "data_dir": args.data_dir,
        "vision_url": args.vision_url,
        "dashboard_dir": args.dashboard_dir,
        "trust_proxy_headers": True if args.trust_proxy_headers else None,
        "trusted_proxies": (*ApiSettings().trusted_proxies, *args.trusted_proxy) or None,
        "allowed_origins": tuple(normalize_origin(o) for o in args.allow_origin) or None,
    }
    return dataclasses.replace(
        ApiSettings(), **{k: v for k, v in overrides.items() if v is not None}
    )


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )
    logging.getLogger("alembic").setLevel(logging.WARNING)  # it reports every start-up check
    logging.getLogger("httpx").setLevel(logging.WARNING)  # and httpx every call to the camera
    settings = settings_from(args)
    app = create_app(settings)
    log.info("data in %s; vision service at %s", settings.data_dir.resolve(), settings.vision_url)
    if app.state.ctx.store.user_count() == 0:
        log.warning("no user exists yet; create one with `trafficpi-admin set-password`")
    if settings.dashboard_dir is None or not settings.dashboard_dir.is_dir():
        log.warning("no built dashboard found; only the API is served")
    config = uvicorn.Config(
        app,
        host=args.host,
        port=args.port,
        log_level="info",
        access_log=False,
        server_header=False,
        # Our own setting decides whom to believe about the client address (see ApiSettings).
        proxy_headers=False,
        # Open streams end when shutdown starts (see Server); this only catches stragglers.
        timeout_graceful_shutdown=3,
    )
    Server(config, app).run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
