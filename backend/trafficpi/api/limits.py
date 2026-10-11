"""Size limits for what a client may send."""

from __future__ import annotations

import json
from collections.abc import Mapping

from starlette.types import ASGIApp, Message, Receive, Scope, Send

DEFAULT_BODY_BYTES = 16 * 1024
PROFILE_BODY_BYTES = 1024 * 1024  # a profile with every zone and stop sign it may hold is far less
REFUSAL = json.dumps({"detail": "request body too large"}).encode()


class BodyTooLarge(Exception):
    """A request body went over its limit while it was being read."""


class BodyLimit:
    """Refuses a request whose body is larger than its limit with a 413, before it is all read.

    Starlette reads a JSON body whole into memory, and neither it nor uvicorn limits its size, so a
    client that was not even logged in could send gigabytes to ``/api/auth/login``. A declared
    length is judged before a byte is read. A body with no declared length (chunked) is counted as
    it arrives and the read is cut off the moment it goes over.

    FastAPI turns any exception while reading a body into a 400, so when the limit was passed this
    replaces whatever the application answers with the 413.
    """

    def __init__(
        self,
        app: ASGIApp,
        *,
        default: int = DEFAULT_BODY_BYTES,
        larger: Mapping[tuple[str, str], int] | None = None,
    ) -> None:
        self.app = app
        self.default = default
        self.larger = dict(larger or {})

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        limit = self.larger.get((scope["method"], scope["path"]), self.default)
        declared = _declared_length(scope)
        if declared is not None and declared > limit:
            await _refuse(send)
            return

        received = 0
        exceeded = False
        refused = False  # the 413 has been sent in place of the application's answer

        async def counted_receive() -> Message:
            nonlocal received, exceeded
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    exceeded = True
                    raise BodyTooLarge
            return message

        async def guarded_send(message: Message) -> None:
            nonlocal refused
            if refused:
                return  # the rest of an answer that has been replaced
            if exceeded and message["type"] == "http.response.start":
                refused = True
                await _refuse(send)
                return
            await send(message)

        try:
            await self.app(scope, counted_receive, guarded_send)
        except BodyTooLarge:
            if not refused:
                await _refuse(send)


def _declared_length(scope: Scope) -> int | None:
    for name, value in scope["headers"]:
        if name == b"content-length":
            try:
                return int(value)
            except ValueError:
                return None  # the server has already refused a length that is not a number
    return None


async def _refuse(send: Send) -> None:
    await send(
        {
            "type": "http.response.start",
            "status": 413,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(REFUSAL)).encode()),
                (b"cache-control", b"no-store"),
                (b"x-content-type-options", b"nosniff"),
                (b"connection", b"close"),  # what is left of the body is not read
            ],
        }
    )
    await send({"type": "http.response.body", "body": REFUSAL})
