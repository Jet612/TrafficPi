"""Talking to the vision service on localhost."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import httpx


class VisionUnavailable(Exception):
    """The vision service did not answer."""


class VisionClient:
    def __init__(
        self,
        base_url: str,
        *,
        timeout: float = 2.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(
            base_url=self.base_url, timeout=timeout, transport=transport
        )

    @property
    def ws_url(self) -> str:
        return self.base_url.replace("http", "ws", 1) + "/live"

    async def _get_json(self, path: str) -> dict[str, Any]:
        try:
            response = await self._client.get(path)
            response.raise_for_status()
            data = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise VisionUnavailable(str(exc)) from exc
        return data

    async def health(self) -> dict[str, Any]:
        return await self._get_json("/health")

    async def state(self) -> dict[str, Any]:
        return await self._get_json("/state")

    async def reload(self) -> bool:
        """Ask the vision service to reload its profile; False if it could not be reached."""
        try:
            return (await self._client.post("/reload")).is_success
        except httpx.HTTPError:
            return False

    async def snapshot(self) -> bytes:
        try:
            response = await self._client.get("/snapshot.jpg", timeout=10.0)
        except httpx.HTTPError as exc:
            raise VisionUnavailable(str(exc)) from exc
        if response.status_code != 200:
            raise VisionUnavailable(f"vision service answered {response.status_code}")
        return response.content

    async def open_preview(self) -> tuple[str, AsyncIterator[bytes]]:
        """Start the MJPEG stream: its content type and an iterator over the raw bytes."""
        request = self._client.build_request(
            "GET", "/preview.mjpg", timeout=httpx.Timeout(5.0, read=None)
        )
        try:
            upstream = await self._client.send(request, stream=True)
        except httpx.HTTPError as exc:
            raise VisionUnavailable(str(exc)) from exc
        if upstream.status_code != 200:
            await upstream.aclose()
            raise VisionUnavailable(f"vision service answered {upstream.status_code}")

        async def chunks() -> AsyncIterator[bytes]:
            try:
                async for chunk in upstream.aiter_raw():
                    yield chunk
            except httpx.HTTPError:
                return
            finally:
                await upstream.aclose()

        return upstream.headers.get("content-type", "multipart/x-mixed-replace"), chunks()

    async def close(self) -> None:
        await self._client.aclose()
