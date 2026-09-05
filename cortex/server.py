"""Local HTTP API for the graph window.

Bound to 127.0.0.1 and gated by a per-run token, because this API can launch
editors and shells.  Every path argument is checked to be genuinely inside the
scanned root before anything touches it.
"""

from __future__ import annotations

import html
import json
import mimetypes
import os
import posixpath
import secrets
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs, unquote

from . import reader

UI_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ui")

# The page renders markdown out of files on your disk, so a note in a cloned
# repository is untrusted input.  The renderer escapes HTML, and this is the
# second lock: nothing may execute except our own scripts, and the only inline
# script is the config blob, which carries a fresh nonce each time the page is
# served.  Remote images are still allowed, because notes legitimately use them.
CSP = ("default-src 'none'; "
       "script-src 'self' 'nonce-{nonce}'; "
       "style-src 'self'; "
       "img-src 'self' data: https:; "
       "media-src 'self'; "
       "frame-src 'self'; "
       "connect-src 'self'; "
       "base-uri 'none'; "
       "form-action 'none'; "
       "frame-ancestors 'none'")

STATIC_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".svg": "image/svg+xml",
}


class Context:
    """Everything the handler needs, injected once at startup."""

    def __init__(self, scanner, links, runner, token, title, ui_config=None):
        self.scanner = scanner
        self.links = links
        self.runner = runner
        self.token = token
        self.title = title
        self.ui_config = ui_config or {}


class Handler(BaseHTTPRequestHandler):
    ctx: Context = None            # set by serve()
    server_version = "cortex"
    sys_version = ""

    # -- plumbing ----------------------------------------------------------

    def log_message(self, fmt, *args):      # keep the terminal clean
        pass

    def _send(self, code, body=b"", ctype="application/json", extra=None):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, payload, code=200):
        self._send(code, json.dumps(payload), "application/json")

    def _authed(self, params) -> bool:
        given = (params.get("t") or [None])[0]
        return given == self.ctx.token

    def _safe_path(self, params) -> str | None:
        raw = (params.get("path") or [None])[0]
        if not raw:
            return None
        path = os.path.realpath(unquote(raw))
        return path if self.ctx.scanner.inside(path) else None

    # -- routes ------------------------------------------------------------

    def do_GET(self):
        url = urlparse(self.path)
        params = parse_qs(url.query)
        route = url.path

        if route in ("/", "/index.html"):
            return self._index()
        if route.startswith("/static/"):
            return self._static(route[len("/static/"):])

        if not self._authed(params):
            return self._json({"error": "bad token"}, 403)

        if route == "/api/root":
            sc = self.ctx.scanner
            return self._json(sc.node(sc.root))

        if route == "/api/children":
            path = self._safe_path(params)
            if not path:
                return self._json({"error": "bad path"}, 400)
            return self._json(self.ctx.scanner.children(path))

        if route == "/api/search":
            term = (params.get("q") or [""])[0]
            hits = self.ctx.scanner.search(term)
            return self._json({"results": hits, "count": len(hits)})

        if route == "/api/preview":
            path = self._safe_path(params)
            if not path or not os.path.isfile(path):
                return self._json({"kind": "error", "message": "not a file"}, 400)
            return self._json(reader.preview(path))

        if route == "/api/raw":
            return self._raw(params)

        if route == "/api/links":
            return self._json(self.ctx.links.snapshot())

        if route == "/api/editors":
            from .actions import available_editors
            return self._json({"editors": available_editors()})

        return self._json({"error": "not found"}, 404)

    def do_POST(self):
        url = urlparse(self.path)
        params = parse_qs(url.query)
        if not self._authed(params):
            return self._json({"error": "bad token"}, 403)
        if url.path != "/api/action":
            return self._json({"error": "not found"}, 404)

        try:
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, TypeError):
            return self._json({"ok": False, "error": "bad body"}, 400)

        path = os.path.realpath(body.get("path") or "")
        if not self.ctx.scanner.inside(path):
            return self._json({"ok": False, "error": "path outside root"}, 400)

        result = self.ctx.runner.submit(body.get("kind", ""), path,
                                        body.get("editor"))
        return self._json(result)

    # -- helpers -----------------------------------------------------------

    def _index(self):
        try:
            with open(os.path.join(UI_DIR, "index.html"), "r",
                      encoding="utf-8") as fh:
                page = fh.read()
        except OSError:
            return self._send(500, b"ui missing", "text/plain")
        cfg = {"token": self.ctx.token, "root": self.ctx.scanner.root,
               "title": self.ctx.title}
        cfg.update(self.ctx.ui_config)
        nonce = secrets.token_urlsafe(16)
        page = (page.replace("__CONFIG__", json.dumps(cfg))
                    .replace("__NONCE__", nonce)
                    .replace("__TITLE__", html.escape(self.ctx.title)))
        return self._send(200, page, "text/html; charset=utf-8",
                          {"Content-Security-Policy": CSP.format(nonce=nonce),
                           "Referrer-Policy": "no-referrer"})

    def _static(self, rel):
        rel = posixpath.normpath("/" + rel).lstrip("/")
        full = os.path.join(UI_DIR, rel)
        if not os.path.realpath(full).startswith(os.path.realpath(UI_DIR)):
            return self._send(403, b"no", "text/plain")
        ext = os.path.splitext(full)[1].lower()
        try:
            with open(full, "rb") as fh:
                body = fh.read()
        except OSError:
            return self._send(404, b"not found", "text/plain")
        ctype = STATIC_TYPES.get(ext) or mimetypes.guess_type(full)[0] \
            or "application/octet-stream"
        return self._send(200, body, ctype)

    def _raw(self, params):
        path = self._safe_path(params)
        if not path or not os.path.isfile(path):
            return self._send(404, b"not found", "text/plain")
        try:
            size = os.path.getsize(path)
        except OSError:
            return self._send(404, b"not found", "text/plain")
        ctype = reader.mime_for(path)
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(size))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Disposition",
                         f'inline; filename="{os.path.basename(path)}"')
        self.end_headers()
        if self.command == "HEAD":
            return
        try:
            for chunk in reader.stream(path):
                self.wfile.write(chunk)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass


def serve(ctx: Context, port: int = 0) -> ThreadingHTTPServer:
    Handler.ctx = ctx
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    httpd.daemon_threads = True
    return httpd
