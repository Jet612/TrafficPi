"""When stop signs are judged: the endpoints behind the Stop-sign hours card.

Three of them. ``GET`` gives what is saved and what it means now; ``PUT`` saves a whole new
setting and tells the vision service to read it (it applies at once, with no restart); ``POST
/check`` says what a setting that is not saved yet would mean now, so that the card can show the
sun's times for a place as it is typed. Every one needs the login, and a change is subject to the
same-origin check and the body-size limit like every other.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Request

from trafficpi.api.schemas import (
    StopHoursConfig,
    StopHoursDay,
    StopHoursRange,
    StopHoursSaved,
    StopHoursState,
    StopHoursStatus,
)
from trafficpi.core.clock import resolve_timezone
from trafficpi.core.stop_hours import (
    StopHours,
    TimeRange,
    daylight_day,
    describe_hours,
    format_clock,
    judging_at,
    next_change_ms,
    parse_clock,
)

if TYPE_CHECKING:
    from trafficpi.api.app import ApiContext


def to_config(hours: StopHours) -> StopHoursConfig:
    return StopHoursConfig(
        daylight=hours.daylight,
        latitude=hours.latitude,
        longitude=hours.longitude,
        before_sunrise_min=hours.before_sunrise_min,
        after_sunset_min=hours.after_sunset_min,
        clock=hours.clock,
        ranges=[
            StopHoursRange(start=format_clock(r.start), end=format_clock(r.end))
            for r in hours.ranges
        ],
        days=sorted(hours.days),
    )


def to_hours(config: StopHoursConfig) -> StopHours:
    """The setting a body asks for; one that makes no sense is a 422 saying why in a sentence."""
    try:
        return StopHours(
            daylight=config.daylight,
            latitude=config.latitude,
            longitude=config.longitude,
            before_sunrise_min=config.before_sunrise_min,
            after_sunset_min=config.after_sunset_min,
            clock=config.clock,
            ranges=tuple(
                TimeRange(parse_clock(r.start), parse_clock(r.end)) for r in config.ranges
            ),
            days=frozenset(config.days),
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def status_of(hours: StopHours, zone: ZoneInfo, now_ms: int) -> StopHoursStatus:
    day = daylight_day(hours, now_ms, zone)
    return StopHoursStatus(
        now=now_ms,
        timezone=str(zone),
        limited=hours.limited,
        judging=judging_at(hours, now_ms, zone),
        next_change=next_change_ms(hours, now_ms, zone),
        description=describe_hours(hours),
        day=None
        if day is None
        else StopHoursDay(
            date=day.date,
            sunrise=day.sunrise_ms,
            sunset=day.sunset_ms,
            start=day.start_ms,
            end=day.end_ms,
            polar=day.polar,
            noon_offset_min=day.noon_offset_min,
        ),
    )


def _state(ctx: ApiContext) -> StopHoursState:
    hours = ctx.store.get_stop_hours()
    zone = resolve_timezone(ctx.store.get_setting("timezone"))
    return StopHoursState(
        config=to_config(hours), status=status_of(hours, zone, ctx.clock.now_ms())
    )


def build_router(
    get_ctx: Callable[[Request], ApiContext], require_user: Callable[..., str]
) -> APIRouter:
    router = APIRouter(prefix="/api/stops/hours", dependencies=[Depends(require_user)])

    @router.get("", response_model=StopHoursState)
    def get_state(request: Request) -> StopHoursState:
        return _state(get_ctx(request))

    @router.put("", response_model=StopHoursSaved)
    async def put_state(body: StopHoursConfig, request: Request) -> StopHoursSaved:
        ctx = get_ctx(request)
        hours = to_hours(body)  # all of it is checked before any of it is saved
        ctx.store.set_stop_hours(hours)
        notified = await ctx.vision.reload()  # the vision service reads the hours when told to
        state = _state(ctx)
        return StopHoursSaved(config=state.config, status=state.status, vision_notified=notified)

    @router.post("/check", response_model=StopHoursStatus)
    def check(body: StopHoursConfig, request: Request) -> StopHoursStatus:
        ctx = get_ctx(request)
        zone = resolve_timezone(ctx.store.get_setting("timezone"))
        return status_of(to_hours(body), zone, ctx.clock.now_ms())

    return router
