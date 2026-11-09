#!/usr/bin/env python3
"""Serve a built static demo on this machine, to try it before it is published.

    python3 scripts/serve_static_demo.py SITE_DIR [--port 18200] [--host 0.0.0.0]

It answers on this machine only, unless ``--host 0.0.0.0`` lets other devices on the network in.

Python's own ``http.server`` cannot answer a request for part of a file, which a browser needs to
seek in a video and to loop it smoothly; this one can, as every static host does.
"""

from __future__ import annotations

import argparse
import functools
import re
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

RANGE = re.compile(r"bytes=(\d*)-(\d*)$")


class RangeHandler(SimpleHTTPRequestHandler):
    def send_head(self):  # type: ignore[override]
        match = RANGE.match(self.headers.get("Range", ""))
        path = Path(self.translate_path(self.path))
        if match is None or not path.is_file():
            return super().send_head()
        size = path.stat().st_size
        first, last = match.groups()
        if first == "":  # the last N bytes
            start, end = max(0, size - int(last or 0)), size - 1
        else:
            start, end = int(first), min(int(last) if last else size - 1, size - 1)
        if start > end or start >= size:
            self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
            self.send_header("Content-Range", f"bytes */{size}")
            self.end_headers()
            return None
        file = path.open("rb")
        file.seek(start)
        self.send_response(HTTPStatus.PARTIAL_CONTENT)
        self.send_header("Content-Type", self.guess_type(path))
        self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("Content-Length", str(end - start + 1))
        self.send_header("Accept-Ranges", "bytes")
        self.end_headers()
        self._remaining = end - start + 1
        return file

    def copyfile(self, source, outputfile):  # type: ignore[override]
        remaining = getattr(self, "_remaining", None)
        if remaining is None:
            return super().copyfile(source, outputfile)
        while remaining > 0:
            chunk = source.read(min(64 * 1024, remaining))
            if not chunk:
                break
            outputfile.write(chunk)
            remaining -= len(chunk)
        self._remaining = None


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("site", help="the folder build_static_demo.sh wrote")
    parser.add_argument("--port", type=int, default=18200)
    parser.add_argument("--host", default="127.0.0.1", help="0.0.0.0 for the whole network")
    args = parser.parse_args()
    handler = functools.partial(RangeHandler, directory=args.site)
    with ThreadingHTTPServer((args.host, args.port), handler) as server:
        print(
            f"serving {args.site} at http://{args.host}:{args.port}/ (Ctrl-C stops it)", flush=True
        )
        server.serve_forever()


if __name__ == "__main__":
    main()
