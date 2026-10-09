"""The built dashboard, and the Content-Security-Policy each of its pages is sent with (D31).

Next.js writes three inline ``<script>`` elements into every exported page (its start-up code and
the page's data), so a policy that allows scripts from the page itself has to allow inline ones too,
and then it would also allow a script that someone managed to inject. Instead the policy of a page
lists the hash of each inline script that page really contains: the browser runs those and no
others. The hashes are worked out from the file that is being sent (and again when the file
changes), so a rebuilt dashboard needs no restart and no list to keep up.
"""

from __future__ import annotations

import base64
import hashlib
import os
from collections.abc import Iterable
from functools import lru_cache
from html.parser import HTMLParser
from pathlib import Path

from starlette.concurrency import run_in_threadpool
from starlette.responses import FileResponse, Response
from starlette.staticfiles import PathLike, StaticFiles
from starlette.types import Scope


def content_security_policy(script_hashes: Iterable[str] = ()) -> str:
    """The policy for a response; ``script_hashes`` are the inline scripts a page may run."""
    scripts = " ".join(["'self'", *script_hashes])
    return "; ".join(
        [
            "default-src 'self'",
            f"script-src {scripts}",
            # Styles stay open: React sets style attributes, and the not-found page has a <style>.
            "style-src 'self' 'unsafe-inline'",
            "img-src 'self' data: blob:",
            "media-src 'self'",
            "connect-src 'self'",
            "font-src 'self'",
            "object-src 'none'",
            "base-uri 'self'",
            "form-action 'self'",
            "frame-ancestors 'none'",
        ]
    )


class _InlineScripts(HTMLParser):
    """Collects the text of every ``<script>`` that has no ``src``, as the browser reads it."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.scripts: list[str] = []
        self._current: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "script" and all(name != "src" for name, _ in attrs):
            self._current = []

    def handle_data(self, data: str) -> None:
        if self._current is not None:
            self._current.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "script" and self._current is not None:
            self.scripts.append("".join(self._current))
            self._current = None


def script_source(text: str) -> str:
    """A CSP source expression for one script: the SHA-256 of its exact text."""
    digest = hashlib.sha256(text.encode()).digest()
    return f"'sha256-{base64.b64encode(digest).decode()}'"


@lru_cache(maxsize=64)
def _inline_script_hashes(path: str, mtime_ns: int, size: int) -> tuple[str, ...]:
    """The hashes for one version of one file (its time and size are part of the cache key)."""
    parser = _InlineScripts()
    parser.feed(Path(path).read_text(encoding="utf-8", errors="replace"))
    parser.close()
    return tuple(dict.fromkeys(script_source(text) for text in parser.scripts if text))


def _set_policy(response: Response, path: str | Path, stat: os.stat_result) -> None:
    """Put the Content-Security-Policy that fits an HTML file on the response that sends it."""
    if str(path).endswith(".html"):
        hashes = _inline_script_hashes(str(path), stat.st_mtime_ns, stat.st_size)
        response.headers["Content-Security-Policy"] = content_security_policy(hashes)


class DashboardFiles(StaticFiles):
    """The exported dashboard, every HTML page with a policy that fits its own inline scripts.

    The policy goes on the "304 Not Modified" that answers a reload as well as on the page: a
    browser merges the headers of a 304 into the page it has kept, so a 304 with the plain policy
    (no inline script allowed) left the reloaded page blank.
    """

    def file_response(
        self, full_path: PathLike, stat_result: os.stat_result, scope: Scope, status_code: int = 200
    ) -> Response:
        response = super().file_response(full_path, stat_result, scope, status_code)
        _set_policy(
            response, str(full_path), stat_result
        )  # a 200, or the 304 that answers a reload
        return response

    async def get_response(self, path: str, scope: Scope) -> Response:
        response = await super().get_response(path, scope)
        # The not-found page does not come through file_response, so look at what comes back.
        if isinstance(response, FileResponse) and response.status_code == 404:
            stat = response.stat_result or Path(response.path).stat()
            await run_in_threadpool(_set_policy, response, str(response.path), stat)
        return response
