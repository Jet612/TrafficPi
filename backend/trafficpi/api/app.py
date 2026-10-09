"""The API service's web application."""

from __future__ import annotations

import asyncio
import shutil
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Annotated, Any, Literal
from urllib.parse import urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import websockets
from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response, WebSocket
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from trafficpi.api import stats
from trafficpi.api.auth import (
    COOKIE_NAME,
    LoginThrottle,
    SessionSigner,
    hash_password,
    load_or_create_secret,
    needs_rehash,
    verify_password,
)
from trafficpi.api.schemas import (
    AuthState,
    DiskStatus,
    EventOut,
    LoginRequest,
    Me,
    ProfileOut,
    ProfileSaved,
    SeriesPoint,
    Settings,
    StatusResponse,
    TodaySummary,
    VisionStatus,
)
from trafficpi.api.settings import ApiSettings
from trafficpi.api.vision_client import VisionClient, VisionUnavailable
from trafficpi.core.clock import WallClock, system_timezone_name
from trafficpi.db import create_db_engine, upgrade_to_head
from trafficpi.db.store import Store
from trafficpi.vision.lines import parse_counting_lines

UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
SERIES_LIMITS_S = {"minute": 2 * 86400, "hour": 62 * 86400, "day": 1100 * 86400}
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "same-origin",
    # The static dashboard needs inline scripts for hydration; tightening this is an M8 item.
    "Content-Security-Policy": (
        "default-src 'self'; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline'; "
        "script-src 'self' 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'"
    ),
}


@dataclass(slots=True)
class ApiContext:
    settings: ApiSettings
    store: Store
    signer: SessionSigner
    throttle: LoginThrottle
    vision: VisionClient
    clock: WallClock
    dummy_hash: str  # verified against when a username does not exist, so timing does not tell


def _ctx(request: Request) -> ApiContext:
    return request.app.state.ctx  # type: ignore[no-any-return]


def _client_address(request: Request, trust_proxy: bool) -> str:
    if trust_proxy:
        forwarded = request.headers.get("cf-connecting-ip") or request.headers.get(
            "x-forwarded-for"
        )
        if forwarded:
            return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _same_origin(origin: str | None, host: str | None) -> bool:
    """Whether a request's Origin (if it sent one) is this very site."""
    return origin is None or (host is not None and urlparse(origin).netloc == host)


def _timezone(store: Store) -> ZoneInfo:
    name = store.get_setting("timezone") or system_timezone_name()
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        return ZoneInfo("UTC")


def require_user(request: Request) -> str:
    ctx = _ctx(request)
    username = ctx.signer.read(request.cookies.get(COOKIE_NAME))
    if username is None or ctx.store.get_user(username) is None:
        raise HTTPException(status_code=401, detail="not logged in")
    return username


User = Annotated[str, Depends(require_user)]


def create_app(
    settings: ApiSettings | None = None,
    *,
    store: Store | None = None,
    vision: VisionClient | None = None,
) -> FastAPI:
    settings = settings or ApiSettings()
    if store is None:
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        engine = create_db_engine(settings.db_path)
        upgrade_to_head(engine)
        store = Store(engine)
        store.ensure_camera()
    ctx = ApiContext(
        settings=settings,
        store=store,
        signer=SessionSigner(
            load_or_create_secret(settings.secret_path), settings.session_max_age_s
        ),
        throttle=LoginThrottle(settings.login_attempts, settings.login_window_s),
        vision=vision or VisionClient(settings.vision_url),
        clock=WallClock(),
        dummy_hash=hash_password("a password that nobody uses"),
    )

    @asynccontextmanager
    async def lifespan(_app: FastAPI):  # type: ignore[no-untyped-def]
        yield
        await ctx.vision.close()

    app = FastAPI(title="TrafficPi", docs_url=None, redoc_url=None, lifespan=lifespan)
    app.state.ctx = ctx

    # --- cross-cutting -------------------------------------------------------------------------

    @app.middleware("http")
    async def protect(request: Request, call_next):  # type: ignore[no-untyped-def]
        if request.method in UNSAFE_METHODS and not _same_origin(
            request.headers.get("origin"), request.headers.get("host")
        ):
            return JSONResponse({"detail": "cross-origin request refused"}, status_code=403)
        response = await call_next(request)
        for name, value in SECURITY_HEADERS.items():
            response.headers.setdefault(name, value)
        if request.url.path.startswith("/api/"):
            response.headers.setdefault("Cache-Control", "no-store")
        return response

    # --- public ----------------------------------------------------------------------------------

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/auth/state", response_model=AuthState)
    def auth_state() -> AuthState:
        return AuthState(configured=ctx.store.user_count() > 0)

    @app.post("/api/auth/login", status_code=204)
    def login(body: LoginRequest, request: Request) -> Response:
        keys = (
            f"ip:{_client_address(request, settings.trust_proxy_headers)}",
            f"user:{body.username.lower()}",
        )
        if ctx.throttle.blocked(*keys):
            raise HTTPException(
                status_code=429,
                detail="too many failed logins; try again later",
                headers={"Retry-After": str(ctx.throttle.retry_after_s(*keys))},
            )
        user = ctx.store.get_user(body.username)
        verified = verify_password(user.password_hash if user else ctx.dummy_hash, body.password)
        if user is None or not verified:
            ctx.throttle.record_failure(*keys)
            raise HTTPException(status_code=401, detail="invalid username or password")
        ctx.throttle.clear(*keys)
        if needs_rehash(user.password_hash):
            ctx.store.set_password_hash(user.username, hash_password(body.password))
        response = Response(status_code=204)
        secure = (
            request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"
        )
        response.set_cookie(
            COOKIE_NAME,
            ctx.signer.issue(user.username),
            max_age=settings.session_max_age_s,
            httponly=True,
            samesite="strict",
            secure=secure,
            path="/",
        )
        return response

    @app.post("/api/auth/logout", status_code=204)
    def logout() -> Response:
        response = Response(status_code=204)
        response.delete_cookie(COOKIE_NAME, path="/")
        return response

    # --- authenticated ---------------------------------------------------------------------------

    @app.get("/api/auth/me", response_model=Me)
    def me(user: User) -> Me:
        return Me(username=user)

    @app.get("/api/status", response_model=StatusResponse)
    async def status(user: User) -> StatusResponse:
        try:
            state = await ctx.vision.state()
            camera = state.get("camera", {})
            vision_status = VisionStatus(
                status="ok" if camera.get("ok") else "camera-offline",
                fps=state.get("fps"),
                uptime_s=state.get("uptime_s"),
                camera_error=camera.get("error"),
                profile_version=state.get("profile_version"),
                detector=state.get("detector"),
                system=state.get("system", {}),
            )
        except VisionUnavailable:
            vision_status = VisionStatus(status="unreachable")
        usage = shutil.disk_usage(settings.data_dir if settings.data_dir.exists() else ".")
        return StatusResponse(
            vision=vision_status,
            disk=DiskStatus(
                free_gb=round(usage.free / 1e9, 1),
                total_gb=round(usage.total / 1e9, 1),
                used_percent=round(100 * usage.used / usage.total, 1),
            ),
            events_total=ctx.store.event_count(),
            now_ms=ctx.clock.now_ms(),
            timezone=str(_timezone(ctx.store)),
        )

    def line_names() -> dict[str, str]:
        profile = ctx.store.get_profile()
        if profile is None:
            return {}
        try:
            return {line.id: line.name for line in parse_counting_lines(profile[0])}
        except ValueError:
            return {}

    @app.get("/api/stats/today", response_model=TodaySummary)
    def stats_today(user: User) -> dict[str, Any]:
        return stats.today_summary(
            ctx.store, _timezone(ctx.store), ctx.clock.now_ms(), line_names()
        )

    @app.get("/api/stats/series", response_model=list[SeriesPoint])
    def stats_series(
        user: User,
        from_ms: Annotated[int, Query(ge=0)],
        to_ms: Annotated[int, Query(ge=0)],
        bucket: Literal["minute", "hour", "day"] = "hour",
    ) -> list[dict[str, Any]]:
        if to_ms <= from_ms:
            raise HTTPException(status_code=422, detail="to_ms must be after from_ms")
        if (to_ms - from_ms) / 1000 > SERIES_LIMITS_S[bucket]:
            raise HTTPException(status_code=422, detail=f"range too long for {bucket} buckets")
        start_s, end_s = from_ms // 1000, to_ms // 1000
        rows = ctx.store.minute_counts(start_s, end_s)
        return stats.series(rows, _timezone(ctx.store), start_s, end_s, bucket)

    @app.get("/api/events", response_model=list[EventOut])
    def events(
        user: User,
        limit: Annotated[int, Query(ge=1, le=500)] = 50,
        before_id: int | None = None,
        type: str | None = None,
    ) -> list[EventOut]:
        return [
            EventOut(
                id=e.id,
                t=e.occurred_at,
                type=e.type,
                track=e.track_id,
                label=e.class_label,
                line=e.line_id,
                direction=e.direction,
            )
            for e in ctx.store.recent_events(limit=limit, before_id=before_id, type=type)
        ]

    @app.get("/api/profile", response_model=ProfileOut)
    def get_profile(user: User) -> ProfileOut:
        profile = ctx.store.get_profile()
        return (
            ProfileOut(version=0, document={"lines": []})
            if profile is None
            else ProfileOut(version=profile[1], document=profile[0])
        )

    @app.put("/api/profile", response_model=ProfileSaved)
    async def put_profile(user: User, document: dict[str, Any]) -> ProfileSaved:
        try:
            parse_counting_lines(document)  # reject what the vision service could not use
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        version = ctx.store.set_profile(document, now_ms=ctx.clock.now_ms())
        return ProfileSaved(version=version, vision_notified=await ctx.vision.reload())

    @app.get("/api/settings", response_model=Settings)
    def get_settings(user: User) -> Settings:
        units = ctx.store.get_setting("units", "mph")
        return Settings(
            timezone=str(_timezone(ctx.store)), units="kmh" if units == "kmh" else "mph"
        )

    @app.put("/api/settings", response_model=Settings)
    def put_settings(user: User, body: Settings) -> Settings:
        try:
            ZoneInfo(body.timezone)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise HTTPException(
                status_code=422, detail=f"unknown time zone {body.timezone!r}"
            ) from exc
        ctx.store.set_setting("timezone", body.timezone)
        ctx.store.set_setting("units", body.units)
        return body

    # --- the camera, relayed from the vision service -------------------------------------------

    @app.get("/api/snapshot.jpg")
    async def snapshot(user: User) -> Response:
        try:
            return Response(await ctx.vision.snapshot(), media_type="image/jpeg")
        except VisionUnavailable as exc:
            raise HTTPException(status_code=503, detail="camera unavailable") from exc

    @app.get("/api/preview.mjpg")
    async def preview(user: User) -> StreamingResponse:
        try:
            content_type, chunks = await ctx.vision.open_preview()
        except VisionUnavailable as exc:
            raise HTTPException(status_code=503, detail="camera unavailable") from exc
        return StreamingResponse(chunks, media_type=content_type)

    @app.websocket("/api/live")
    async def live(websocket: WebSocket) -> None:
        username = ctx.signer.read(websocket.cookies.get(COOKIE_NAME))
        allowed = username is not None and ctx.store.get_user(username) is not None
        if not allowed or not _same_origin(
            websocket.headers.get("origin"), websocket.headers.get("host")
        ):
            await websocket.close(code=1008)
            return
        await websocket.accept()

        async def relay() -> None:
            while True:
                try:
                    async with websockets.connect(ctx.vision.ws_url, open_timeout=2) as upstream:
                        async for message in upstream:
                            await websocket.send_text(
                                message if isinstance(message, str) else message.decode()
                            )
                except (OSError, TimeoutError, websockets.WebSocketException):
                    pass
                await websocket.send_json({"type": "offline"})
                await asyncio.sleep(1.0)

        async def watch_client() -> None:
            while True:  # returns when the browser disconnects
                message = await websocket.receive()
                if message["type"] == "websocket.disconnect":
                    return

        tasks = [asyncio.create_task(relay()), asyncio.create_task(watch_client())]
        try:
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    # --- the dashboard (last, so it never shadows an API route) -------------------------------

    if settings.dashboard_dir is not None and settings.dashboard_dir.is_dir():
        app.mount("/", StaticFiles(directory=settings.dashboard_dir, html=True), name="dashboard")
    else:

        @app.get("/")
        def no_dashboard() -> dict[str, str]:
            return {"detail": "the dashboard has not been built; the API is at /api"}

    return app
