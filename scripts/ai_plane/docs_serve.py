"""Serve the built reader over loopback HTTP.

Opening `.ai/_site/index.html` as a `file:` URL looks like it should work and mostly does, until it
does not: browsers apply a distinct, stricter policy to file origins, so parts of a static
application silently fail there and the reader cannot be validated at all. Every adopter hits this,
and the workaround -- start a throwaway HTTP server by hand -- is the same three commands every
time.

Loopback only, by construction. The bind address is not configurable: a documentation reader
containing a repository's task history, receipts, and source excerpts must not be one flag away
from being published on a shared network.
"""

from __future__ import annotations

import contextlib
import socket
import threading
import webbrowser
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import scripts.ai_plane.constants as constants

LOOPBACK = "127.0.0.1"
PREFERRED_PORTS = (8787, 8788, 8789, 8790, 8791)


class _QuietHandler(SimpleHTTPRequestHandler):
    """A static handler that does not narrate every asset request."""

    def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A003 - stdlib signature
        return

    def end_headers(self) -> None:
        # The reader is a local, single-user surface; caching it defeats the rebuild-and-refresh
        # loop that is the whole reason to serve it.
        self.send_header("Cache-Control", "no-store")
        super().end_headers()


def choose_port(preferred: tuple[int, ...] = PREFERRED_PORTS) -> int:
    """A free loopback port: a known one when available, otherwise whatever the OS hands out.

    Failing because 8787 happens to be busy would make the command useless on exactly the machines
    that run several projects at once.
    """
    for port in preferred:
        with contextlib.closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as probe:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                probe.bind((LOOPBACK, port))
            except OSError:
                continue
            return port
    with contextlib.closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as probe:
        probe.bind((LOOPBACK, 0))
        return int(probe.getsockname()[1])


def build_server(site_dir: Path, port: int) -> ThreadingHTTPServer:
    handler = partial(_QuietHandler, directory=str(site_dir))
    return ThreadingHTTPServer((LOOPBACK, port), handler)


def cmd_docs_serve(
    args: Any = None,
    *,
    root: Path | None = None,
    serve_forever: bool = True,
) -> dict[str, Any]:
    """Serve `.ai/_site/` and print the URL. Returns the binding for tests and callers."""
    root = root if root is not None else constants.ROOT
    site_dir = root / ".ai" / "_site"
    if not (site_dir / "index.html").is_file():
        raise SystemExit(
            "No built reader at .ai/_site/index.html. Run `python scripts/ai_cli.py docs build` "
            "first."
        )
    port = choose_port()
    server = build_server(site_dir, port)
    url = f"http://{LOOPBACK}:{port}/"
    print(f"Reader served from {site_dir} at {url}")
    print("Loopback only. Press Ctrl+C to stop.")
    if getattr(args, "open_browser", False):
        threading.Timer(0.4, webbrowser.open, args=(url,)).start()
    if not serve_forever:
        server.server_close()
        return {"url": url, "port": port, "directory": str(site_dir)}
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        server.server_close()
    return {"url": url, "port": port, "directory": str(site_dir)}


def add_docs_serve_parser(docs_sub: Any) -> None:
    parser = docs_sub.add_parser(
        "serve",
        help="Serve the built reader over loopback HTTP (a file: URL cannot validate it)",
    )
    parser.add_argument("--open", dest="open_browser", action="store_true",
                        help="Open the printed URL in the default browser")
