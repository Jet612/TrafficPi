"""The daily email's endpoints (plan.md D32): the card in Settings, the key, tests, the preview.

Every one needs the login (D10, D31), changes are subject to the same-origin check as every other,
and bodies to the same size limit. The provider's key is write-only: it is stored in a file of its
own (``mail.key``), and no answer, error or log line ever holds it. The blocking work (the database,
and above all a test mail's call to the provider) runs in the thread pool, as the endpoints are
plain ``def``s.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from trafficpi.api.schemas import (
    MailAddressBody,
    MailKeyBody,
    MailPreview,
    MailRecipientOut,
    MailScheduleBody,
    MailSendOut,
    MailState,
    MailTestResult,
)
from trafficpi.api.settings import ApiSettings
from trafficpi.core.clock import resolve_timezone
from trafficpi.db.mail_store import MailLimitReached
from trafficpi.mail.mailer import FileMailer, Mailer, ResendMailer
from trafficpi.mail.service import MailNotReady, TooManyTests
from trafficpi.mail.settings import (
    MAX_RECIPIENTS,
    MailSchedule,
    clean_address,
    clean_key,
    clear_key,
    read_key,
    write_key,
)

if TYPE_CHECKING:
    from trafficpi.api.app import ApiContext

log = logging.getLogger("trafficpi.api")

RECENT_MAILS = 7  # how many scheduled mails the state lists


def make_mailer(settings: ApiSettings) -> Mailer:
    """The sender the settings call for: Resend, or the folder (``--mail-transport file``)."""
    if settings.mail_transport == "file":
        return FileMailer(settings.outbox_path)
    return ResendMailer(settings.mail_key_path)


def _state(ctx: ApiContext) -> MailState:
    status = ctx.mail_service.status()
    schedule = ctx.mail.get_schedule()
    return MailState(
        schedule=MailScheduleBody(
            enabled=schedule.enabled,
            time=schedule.time,
            days=list(schedule.days),
            sender_name=schedule.sender_name,
            sender_address=schedule.sender_address,
            link_url=schedule.link_url,
        ),
        recipients=[
            MailRecipientOut.model_validate(r, from_attributes=True) for r in ctx.mail.recipients()
        ],
        key_set=read_key(ctx.settings.mail_key_path) is not None,
        transport="file" if ctx.settings.mail_transport == "file" else "resend",
        ready=status.ready,
        problems=status.problems,
        next_at=status.next_at,
        timezone=str(resolve_timezone(ctx.store.get_setting("timezone"))),
        recent=[
            MailSendOut.model_validate(s, from_attributes=True)
            for s in ctx.mail.sends(RECENT_MAILS)
        ],
        max_recipients=MAX_RECIPIENTS,
    )


def build_router(
    get_ctx: Callable[[Request], ApiContext], require_user: Callable[..., str]
) -> APIRouter:
    router = APIRouter(prefix="/api/mail", dependencies=[Depends(require_user)])

    def ctx_of(request: Request) -> ApiContext:
        return get_ctx(request)

    @router.get("", response_model=MailState)
    def get_state(request: Request) -> MailState:
        return _state(ctx_of(request))

    @router.put("/schedule", response_model=MailState)
    def put_schedule(body: MailScheduleBody, request: Request) -> MailState:
        ctx = ctx_of(request)
        try:
            schedule = MailSchedule(
                enabled=body.enabled,
                time=body.time,
                days=tuple(body.days),
                sender_name=body.sender_name,
                sender_address=body.sender_address,
                link_url=body.link_url,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        # What is kept is the cleaned form (an address in lower case, no spaces round a name).
        ctx.mail.set_schedule(schedule, ctx.clock.now_ms())
        return _state(ctx)

    @router.post("/recipients", response_model=MailState)
    def add_recipient(body: MailAddressBody, request: Request) -> MailState:
        ctx = ctx_of(request)
        try:
            address = clean_address(body.address)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        try:
            ctx.mail.add_recipient(address, ctx.clock.now_ms())
        except MailLimitReached:
            raise HTTPException(
                status_code=409,
                detail=f"The list is full: at most {MAX_RECIPIENTS} recipients. Remove one first.",
            ) from None
        return _state(ctx)

    @router.delete("/recipients/{recipient_id}", response_model=MailState)
    def remove_recipient(recipient_id: int, request: Request) -> MailState:
        ctx = ctx_of(request)
        if not ctx.mail.remove_recipient(recipient_id):
            raise HTTPException(status_code=404, detail="no such recipient")
        return _state(ctx)

    @router.post("/recipients/{recipient_id}/test", response_model=MailTestResult)
    def send_test(recipient_id: int, request: Request) -> MailTestResult | JSONResponse:
        ctx = ctx_of(request)
        try:
            outcome = ctx.mail_service.send_test(recipient_id)
        except LookupError:
            raise HTTPException(status_code=404, detail="no such recipient") from None
        except MailNotReady as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from None
        except TooManyTests as exc:
            wait = max(1, round(exc.retry_after_s))
            return JSONResponse(
                {"detail": f"Tests are limited; try again in {wait} s."},
                status_code=429,
                headers={"Retry-After": str(wait)},
            )
        return MailTestResult(ok=outcome.kind == "sent", detail=outcome.detail)

    @router.put("/key", response_model=MailState)
    def put_key(body: MailKeyBody, request: Request) -> MailState:
        ctx = ctx_of(request)
        try:
            key = clean_key(body.key.get_secret_value())
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None  # never the typed text
        write_key(ctx.settings.mail_key_path, key)
        log.info("the email provider's key was set")
        return _state(ctx)

    @router.delete("/key", response_model=MailState)
    def delete_key(request: Request) -> MailState:
        ctx = ctx_of(request)
        clear_key(ctx.settings.mail_key_path)
        log.info("the email provider's key was removed")
        return _state(ctx)

    @router.get("/preview", response_model=MailPreview)
    def preview(request: Request) -> MailPreview:
        digest = ctx_of(request).mail_service.preview()
        return MailPreview(subject=digest.subject, text=digest.text, html=digest.html)

    return router
