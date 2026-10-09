"""The API service's web application."""

from __future__ import annotations

import asyncio
import shutil
import threading
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Annotated, Any, Literal
from urllib.parse import urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import websockets
from fastapi import Body, Depends, FastAPI, HTTPException, Query, Request, Response, WebSocket
from fastapi.exceptions import RequestValidationError
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
    normalize_username,
    password_stamp,
    verify_password,
)
from trafficpi.api.schemas import (
    AuthState,
    DiskStatus,
    EventOut,
    LoginRequest,
    Me,
    ProfileCheck,
    ProfileOut,
    ProfileSaved,
    ReferenceSaved,
    SeriesPoint,
    Settings,
    StatusResponse,
    TodaySummary,
    VisionStatus,
)
from trafficpi.api.settings import ApiSettings, normalize_origin
from trafficpi.api.vision_client import VisionClient, VisionUnavailable
from trafficpi.core.clock import WallClock, system_timezone_name
from trafficpi.core.profile import (
    Profile,
    ProfileError,
    describe_errors,
    empty_profile,
    parse_profile,
)
from trafficpi.db.store import Store, open_store

UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
WHERE_FROM = {"body", "query", "path", "header", "cookie"}  # the first part of a FastAPI error path
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
    # Set when the server starts shutting down, so open streams end and shutdown need not wait.
    stopping: threading.Event = field(default_factory=threading.Event)


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


def _same_origin(origin: str | None, host: str | None, allowed: tuple[str, ...] = ()) -> bool:
    """Whether a request's Origin (if it sent one) is this very site, or one that is allowed."""
    if origin is None or normalize_origin(origin) in allowed:
        return True
    return host is not None and urlparse(origin).netloc == host


def _error_path(location: tuple[str | int, ...]) -> tuple[str | int, ...]:
    """Where a request went wrong, without the part of the request: lines[0].a, not body.lines..."""
    return tuple(location[1:]) if location and location[0] in WHERE_FROM else tuple(location)


def _timezone(store: Store) -> ZoneInfo:
    name = store.get_setting("timezone") or system_timezone_name()
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        return ZoneInfo("UTC")


def _logged_in_user(ctx: ApiContext, cookie: str | None) -> str | None:
    """The user a session cookie belongs to, if it is genuine, unexpired and still current."""
    session = ctx.signer.read(cookie)
    user = ctx.store.get_user(session.username) if session else None
    if session is None or user is None or session.stamp != password_stamp(user.password_hash):
        return None
    return user.username


def require_user(request: Request) -> str:
    username = _logged_in_user(_ctx(request), request.cookies.get(COOKIE_NAME))
    if username is None:
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
        store = open_store(settings.data_dir)
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

    # The schema is not served: it is generated offline (trafficpi.api.openapi) for the dashboard.
    app = FastAPI(
        title="TrafficPi", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan
    )
    app.state.ctx = ctx

    @app.exception_handler(RequestValidationError)
    async def unusable_request(_request: Request, exc: RequestValidationError) -> JSONResponse:
        # One readable sentence instead of FastAPI's list: "lines[0].a: must be inside the
        # picture ..." says what to fix, and the dashboard shows it as it is.
        errors = [{**error, "loc": _error_path(error["loc"])} for error in exc.errors()]
        return JSONResponse({"detail": describe_errors(errors)}, status_code=422)

    # --- cross-cutting -------------------------------------------------------------------------

    @app.middleware("http")
    async def protect(request: Request, call_next):  # type: ignore[no-untyped-def]
        if request.method in UNSAFE_METHODS and not _same_origin(
            request.headers.get("origin"), request.headers.get("host"), settings.allowed_origins
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
        username = normalize_username(body.username)
        keys = (
            f"ip:{_client_address(request, settings.trust_proxy_headers)}",
            f"user:{username}",
        )
        if ctx.throttle.blocked(*keys):
            raise HTTPException(
                status_code=429,
                detail="too many failed logins; try again later",
                headers={"Retry-After": str(ctx.throttle.retry_after_s(*keys))},
            )
        user = ctx.store.get_user(username)
        verified = verify_password(user.password_hash if user else ctx.dummy_hash, body.password)
        if user is None or not verified:
            ctx.throttle.record_failure(*keys)
            raise HTTPException(status_code=401, detail="invalid username or password")
        ctx.throttle.clear(*keys)
        password_hash = user.password_hash
        if needs_rehash(password_hash):
            password_hash = hash_password(body.password)
            ctx.store.set_password_hash(user.username, password_hash)
        response = Response(status_code=204)
        secure = (
            request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"
        )
        response.set_cookie(
            COOKIE_NAME,
            ctx.signer.issue(user.username, password_stamp(password_hash)),
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
        stored = ctx.store.get_profile()
        if stored is None:
            return {}
        try:
            return {line.id: line.name for line in parse_profile(stored[0]).lines}
        except ProfileError:
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

    def reference_taken_at() -> int | None:
        try:
            return int(settings.reference_path.stat().st_mtime * 1000)
        except OSError:
            return None

    @app.get("/api/profile", response_model=ProfileOut)
    def get_profile(user: User) -> ProfileOut:
        stored = ctx.store.get_profile()
        profile, version, problem = empty_profile(), 0, None
        if stored is not None:
            document, version = stored
            try:
                profile = parse_profile(document)
            except ProfileError as exc:
                problem = str(exc)
        return ProfileOut(
            version=version, profile=profile, problem=problem, reference_at=reference_taken_at()
        )

    @app.put("/api/profile", response_model=ProfileSaved)
    async def put_profile(user: User, profile: Profile) -> ProfileSaved:
        version = ctx.store.set_profile(profile.model_dump(mode="json"), now_ms=ctx.clock.now_ms())
        return ProfileSaved(version=version, vision_notified=await ctx.vision.reload())

    @app.post("/api/profile/check", response_model=ProfileCheck)
    def check_profile(user: User, document: Annotated[Any, Body()] = None) -> ProfileCheck:
        """Whether a document (a profile file someone is importing) is usable; nothing is saved."""
        try:
            return ProfileCheck(ok=True, problem=None, profile=parse_profile(document))
        except ProfileError as exc:
            return ProfileCheck(ok=False, problem=str(exc), profile=None)

    @app.get("/api/profile/reference.jpg")
    def get_reference(user: User) -> Response:
        try:
            picture = settings.reference_path.read_bytes()
        except OSError as exc:
            raise HTTPException(status_code=404, detail="no reference picture yet") from exc
        return Response(picture, media_type="image/jpeg")

    @app.post("/api/profile/reference", response_model=ReferenceSaved)
    async def set_reference(user: User) -> ReferenceSaved:
        """Keep the camera's current view as the picture the profile is drawn on."""
        try:
            picture = await ctx.vision.snapshot()
        except VisionUnavailable as exc:
            raise HTTPException(status_code=503, detail="camera unavailable") from exc
        if not picture.startswith(b"\xff\xd8"):
            raise HTTPException(status_code=502, detail="the camera did not return a picture")
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        temporary = settings.reference_path.with_suffix(".jpg.tmp")
        temporary.write_bytes(picture)
        temporary.replace(settings.reference_path)  # readers never see half a picture
        return ReferenceSaved(reference_at=reference_taken_at() or ctx.clock.now_ms())

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

        async def until_shutdown() -> AsyncIterator[bytes]:
            try:
                async for chunk in chunks:
                    if ctx.stopping.is_set():
                        return
                    yield chunk
            finally:
                await chunks.aclose()  # type: ignore[attr-defined]

        return StreamingResponse(until_shutdown(), media_type=content_type)

    @app.websocket("/api/live")
    async def live(websocket: WebSocket) -> None:
        allowed = _logged_in_user(ctx, websocket.cookies.get(COOKIE_NAME)) is not None
        if not allowed or not _same_origin(
            websocket.headers.get("origin"), websocket.headers.get("host"), settings.allowed_origins
        ):
            await websocket.close(code=1008)
            return
        await websocket.accept()

        async def relay() -> None:
            while not ctx.stopping.is_set():
                try:
                    async with websockets.connect(ctx.vision.ws_url, open_timeout=2) as upstream:
                        async for message in upstream:
                            if ctx.stopping.is_set():
                                return
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
            # Not gather: if this handler is itself being cancelled, gather would replace that
            # cancellation with a bare CancelledError, which the cancel scope that sent it (the
            # test client's, for one) does not recognise as its own and so cannot absorb.
            finished, _ = await asyncio.wait(tasks)
            for task in finished:
                if not task.cancelled():
                    task.exception()  # looked at, so that asyncio does not report it as unretrieved

    # --- the dashboard (last, so it never shadows an API route) -------------------------------

    if settings.dashboard_dir is not None and settings.dashboard_dir.is_dir():
        app.mount("/", StaticFiles(directory=settings.dashboard_dir, html=True), name="dashboard")
    else:

        @app.get("/", include_in_schema=False)
        def no_dashboard() -> dict[str, str]:
            return {"detail": "the dashboard has not been built; the API is at /api"}

    return app
