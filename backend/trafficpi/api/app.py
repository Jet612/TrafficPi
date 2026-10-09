"""The API service's web application."""

from __future__ import annotations

import asyncio
import shutil
import threading
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Annotated, Any, Literal
from urllib.parse import urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import websockets
from fastapi import Body, Depends, FastAPI, HTTPException, Query, Request, Response, WebSocket
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from trafficpi.api import analytics, stats, trail_views
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
    ReviewRequest,
    SeriesPoint,
    Settings,
    StatusResponse,
    StopOut,
    StopStats,
    StopSummary,
    StopTotals,
    TodaySummary,
    TrafficStats,
    TrailDay,
    TrailPaths,
    VisionStatus,
)
from trafficpi.api.settings import ApiSettings, normalize_origin
from trafficpi.api.vision_client import VisionClient, VisionUnavailable
from trafficpi.core.clock import WallClock, resolve_timezone
from trafficpi.core.evidence_settings import EvidenceSettings
from trafficpi.core.paths import evidence_dir
from trafficpi.core.profile import (
    Profile,
    ProfileError,
    describe_errors,
    empty_profile,
    parse_profile,
)
from trafficpi.core.trail_settings import TrailSettings
from trafficpi.db.store import STOP_OUTCOMES, EventRow, Store, open_store
from trafficpi.vision.trails import trail_classes

UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
WHERE_FROM = {"body", "query", "path", "header", "cookie"}  # the first part of a FastAPI error path
SERIES_LIMITS_S = {"minute": 2 * 86400, "hour": 62 * 86400, "day": 1100 * 86400}
MAX_STOP_RANGE_S = 366 * 86400
EVIDENCE_CACHE = "private, max-age=86400"  # a picture or a clip is never changed once it is written
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


def _evidence_file(data_dir: Path, relative: str | None) -> Path | None:
    """The file that an event's evidence path names: None unless it is a real file that lies in
    the evidence folder (the path is read from the database; nothing it says is trusted)."""
    if not relative:
        return None
    folder = evidence_dir(data_dir).resolve()
    path = (data_dir / relative).resolve()
    return path if path.is_relative_to(folder) and path.is_file() else None


def _stop_out(event: EventRow, data_dir: Path) -> StopOut:
    details = event.details or {}
    has_snapshot = _evidence_file(data_dir, event.evidence_path) is not None
    has_clip = _evidence_file(data_dir, details.get("clip")) is not None
    return StopOut(
        id=event.id,
        t=event.occurred_at,
        sign=event.line_id or "",
        label=event.class_label,
        outcome=details.get("outcome", "indeterminate"),
        hold_s=details.get("hold_s", 0.0),
        min_speed=details.get("min_speed", -1.0),
        observed_s=details.get("observed_s", 0.0),
        review=event.review,  # type: ignore[arg-type]
        reviewed_at=event.reviewed_at,
        snapshot_url=f"/api/stops/{event.id}/snapshot.jpg" if has_snapshot else None,
        clip_url=f"/api/stops/{event.id}/clip.mp4" if has_clip else None,
    )


def _check_stop_range(from_ms: int, to_ms: int) -> None:
    if to_ms <= from_ms:
        raise HTTPException(status_code=422, detail="to_ms must be after from_ms")
    if to_ms - from_ms > MAX_STOP_RANGE_S * 1000:
        raise HTTPException(status_code=422, detail="ask for at most a year of stops at a time")


def _outcomes(names: str | None) -> list[str] | None:
    """The outcomes asked for in ``?outcome=rolling,did_not_stop`` (None: any), or a refusal."""
    if names is None:
        return None
    asked = [name.strip() for name in names.split(",")]
    for name in asked:
        if name not in STOP_OUTCOMES:
            raise HTTPException(
                status_code=422,
                detail=f"unknown outcome {name!r}; use {', '.join(STOP_OUTCOMES)}",
            )
    return list(dict.fromkeys(asked))


def _trail_kinds(kinds: str | None) -> list[str] | None:
    """The kinds asked for in ``?kinds=car,truck`` (None: every kind), or a refusal and why."""
    if kinds is None:
        return None
    names = [name.strip() for name in kinds.split(",")]
    known = sorted(trail_classes(people=True))
    if not kinds.strip() or any(not name for name in names):
        raise HTTPException(status_code=422, detail=f"name at least one kind: {', '.join(known)}")
    for name in names:
        if name not in known:
            raise HTTPException(
                status_code=422, detail=f"unknown kind {name!r}; use {', '.join(known)}"
            )
    return list(dict.fromkeys(names))


def _timezone(store: Store) -> ZoneInfo:
    return resolve_timezone(store.get_setting("timezone"))


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

    @app.exception_handler(trail_views.BadTrailQuery)
    async def unanswerable_trail_query(
        _request: Request, exc: trail_views.BadTrailQuery
    ) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=422)

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

    @app.get("/api/stats/stops", response_model=StopStats)
    def stats_stops(
        user: User,
        days: Annotated[int, Query(ge=0, le=analytics.MAX_DAYS)] = 7,
        sign: Annotated[str | None, Query(max_length=64)] = None,
    ) -> dict[str, Any]:
        """How vehicles did at the stop signs over the last ``days`` local days (today included),
        and over the same number of days before; ``days=0`` is everything since the first pass."""
        tz = _timezone(ctx.store)
        period = analytics.period_for(
            ctx.clock.now_ms(), tz, days, ctx.store.first_stop_ms(sign=sign)
        )
        rows = ctx.store.stop_rows(period.start_ms, period.end_ms, sign=sign)
        previous = (
            None
            if period.previous_start_ms is None
            else ctx.store.stop_rows(period.previous_start_ms, period.start_ms, sign=sign)
        )
        units = ctx.store.get_setting("units", "mph")
        return {
            **analytics.stop_stats(rows, previous, tz, period, sign=sign),
            "units": "kmh" if units == "kmh" else "mph",
        }

    @app.get("/api/stats/traffic", response_model=TrafficStats)
    def stats_traffic(
        user: User,
        days: Annotated[int, Query(ge=0, le=analytics.MAX_DAYS)] = 7,
    ) -> dict[str, Any]:
        """Vehicles counted over the last ``days`` local days (today included); ``days=0`` is
        everything since the first count."""
        tz = _timezone(ctx.store)
        first_s = ctx.store.first_count_s()
        period = analytics.period_for(
            ctx.clock.now_ms(), tz, days, None if first_s is None else first_s * 1000
        )
        rows = ctx.store.minute_counts(period.start_ms // 1000, period.end_ms // 1000)
        return analytics.traffic_stats(rows, tz, period, line_names())

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

    # --- stops (plan.md D23, M5) -----------------------------------------------------------

    @app.get("/api/stops", response_model=list[StopOut])
    def stops(
        user: User,
        from_ms: Annotated[int, Query(ge=0)],
        to_ms: Annotated[int, Query(ge=0)],
        outcome: str | None = None,
        review: Literal["unreviewed", "confirmed", "false_positive"] | None = None,
        sign: str | None = None,
        limit: Annotated[int, Query(ge=1, le=500)] = 50,
        before_id: int | None = None,
    ) -> list[StopOut]:
        """What vehicles did at stop signs in a range, newest first; ``before_id`` pages on."""
        _check_stop_range(from_ms, to_ms)
        found = ctx.store.stops_between(
            from_ms,
            to_ms,
            outcomes=_outcomes(outcome),
            review=review,
            sign=sign,
            limit=limit,
            before_id=before_id,
        )
        return [_stop_out(event, settings.data_dir) for event in found]

    @app.get("/api/stops/summary", response_model=StopSummary)
    def stops_summary(
        user: User,
        from_ms: Annotated[int, Query(ge=0)],
        to_ms: Annotated[int, Query(ge=0)],
    ) -> StopSummary:
        """How many stop events had each outcome in a range, and how many have been looked at."""
        _check_stop_range(from_ms, to_ms)
        totals = ctx.store.stop_summary(from_ms, to_ms)
        return StopSummary(
            from_ms=from_ms,
            to_ms=to_ms,
            outcomes=[StopTotals(outcome=name, **counts) for name, counts in totals.items()],  # type: ignore[arg-type]
        )

    @app.put("/api/stops/{event_id}/review", response_model=StopOut)
    def review_stop(event_id: int, body: ReviewRequest, user: User) -> StopOut:
        """Say what a person made of a stop event: confirmed, a false positive, or null to undo."""
        reviewed = ctx.store.review_stop(event_id, body.state, now_ms=ctx.clock.now_ms())
        if reviewed is None:
            raise HTTPException(status_code=404, detail="no such stop event")
        return _stop_out(reviewed, settings.data_dir)

    def evidence_response(event_id: int, kind: Literal["snapshot", "clip"]) -> FileResponse:
        event = ctx.store.stop_event(event_id)
        if event is not None:
            relative = (
                event.evidence_path if kind == "snapshot" else (event.details or {}).get("clip")
            )
            path = _evidence_file(settings.data_dir, relative)
        else:
            path = None
        if path is None:
            raise HTTPException(status_code=404, detail=f"no {kind} is kept for that stop")
        return FileResponse(
            path,
            media_type="image/jpeg" if kind == "snapshot" else "video/mp4",
            filename=f"stop-{event_id}{path.suffix}",
            content_disposition_type="inline",  # shown where it is opened; "save as" keeps the name
            headers={"Cache-Control": EVIDENCE_CACHE},
        )

    @app.get("/api/stops/{event_id}/snapshot.jpg")
    def stop_snapshot(event_id: int, user: User) -> FileResponse:
        """The picture of the moment a vehicle crossed the stop line (when one is kept)."""
        return evidence_response(event_id, "snapshot")

    @app.get("/api/stops/{event_id}/clip.mp4")
    def stop_clip(event_id: int, user: User) -> FileResponse:
        """The clip around that moment (when one is kept); it can be played and seeked in."""
        return evidence_response(event_id, "clip")

    def reference_taken_at() -> int | None:
        try:
            return int(settings.reference_path.stat().st_mtime * 1000)
        except OSError:
            return None

    # --- trails (plan.md M4b) --------------------------------------------------------------

    @app.get("/api/trails/day", response_model=TrailDay)
    def trails_day(user: User, day: Annotated[date | None, Query()] = None) -> TrailDay:
        """A local day (today if none is given) by the hour: when the trails began, by kind."""
        tz = _timezone(ctx.store)
        chosen = day or datetime.fromtimestamp(ctx.clock.now_ms() / 1000, tz).date()
        return TrailDay(**trail_views.day_by_the_hour(ctx.store, tz, chosen))

    @app.get("/api/trails", response_model=TrailPaths)
    def trails(
        request: Request,
        user: User,
        from_ms: int,
        to_ms: int,
        kinds: str | None = None,
        limit: int = trail_views.DEFAULT_LIMIT,
    ) -> Response:
        """The paths of the trails that began in a range, compressed if the browser accepts that."""
        payload = trail_views.paths(
            ctx.store, from_ms, to_ms, kinds=_trail_kinds(kinds), limit=limit
        )
        body, headers = trail_views.encode_json(
            payload, "gzip" in request.headers.get("accept-encoding", "")
        )
        return Response(body, media_type="application/json", headers=headers)

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
        trails = ctx.store.get_trail_settings()
        evidence = ctx.store.get_evidence_settings()
        return Settings(
            timezone=str(_timezone(ctx.store)),
            units="kmh" if units == "kmh" else "mph",
            trails_enabled=trails.enabled,
            trails_people=trails.people,
            trails_keep_days=trails.keep_days,
            evidence_enabled=evidence.enabled,
            evidence_keep_days=evidence.keep_days,
            evidence_max_mb=evidence.max_mb,
            evidence_rolling_clip_hours=evidence.rolling_clip_hours,
            evidence_did_not_stop_clip_hours=evidence.did_not_stop_clip_hours,
        )

    @app.put("/api/settings", response_model=Settings)
    async def put_settings(user: User, body: Settings) -> Settings:
        try:
            ZoneInfo(body.timezone)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise HTTPException(
                status_code=422, detail=f"unknown time zone {body.timezone!r}"
            ) from exc
        ctx.store.set_setting("timezone", body.timezone)
        ctx.store.set_setting("units", body.units)
        ctx.store.set_trail_settings(
            TrailSettings(
                enabled=body.trails_enabled,
                people=body.trails_people,
                keep_days=body.trails_keep_days,
            )
        )
        ctx.store.set_evidence_settings(
            EvidenceSettings(
                enabled=body.evidence_enabled,
                keep_days=body.evidence_keep_days,
                max_mb=body.evidence_max_mb,
                rolling_clip_hours=body.evidence_rolling_clip_hours,
                did_not_stop_clip_hours=body.evidence_did_not_stop_clip_hours,
            )
        )
        await ctx.vision.reload()  # the vision service reads these settings when told to
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
