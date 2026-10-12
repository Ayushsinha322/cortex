"""Local HTTP API for the graph window.

Bound to 127.0.0.1 and gated by a per-run token, because this API can launch
editors and shells and write files.  Every path argument is checked to be
genuinely inside the scanned root before anything touches it.
"""

from __future__ import annotations

import html
import json
import logging
import mimetypes
import os
import posixpath
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs, quote

from . import (desktop, dismissed, files, gitstatus, grep, journal, layout, places,
               reader, snapshot, tours, views)
from . import history
from .activity import Activity
from .du import DiskUsage

UI_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ui")
log = logging.getLogger("cortex")

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

# The names this server may be reached by.  A browser puts the hostname it
# resolved into the Host header, so a page on some other site whose DNS has
# been pointed at 127.0.0.1 -- DNS rebinding -- still announces its own name
# here and is turned away before it can read the token out of the page.  The
# port is not checked, so an SSH tunnel to a different local port still works.
LOCAL_HOSTS = {"127.0.0.1", "localhost", "[::1]"}

# Served with every file from disk except PDFs. Shown inside the page, a
# file's own policy does nothing; opened on its own -- an SVG in a new tab --
# it is a document on this origin, where the API is, and an SVG can carry
# script. The sandbox gives it an origin of its own. PDFs are left alone
# because the browser's viewer will not run sandboxed, and their scripts are
# the viewer's to contain anyway.
RAW_CSP = ("default-src 'none'; img-src 'self' data:; media-src 'self'; "
           "style-src 'unsafe-inline'; sandbox")

# A layout for the biggest graph we will draw, with room to spare.  Also
# comfortably more than the biggest file the editor will save.
MAX_BODY = 6 * 1024 * 1024

STATIC_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".svg": "image/svg+xml",
}

# Sent by the page when it closes, from navigator.sendBeacon, which cannot set
# headers or wait for an answer; everything it needs is in the query string.
BEACONS = {"/api/hello", "/api/bye"}


class Context:
    """Everything the handler needs, injected once at startup.

    The mapped folder can change while running -- the window can open a
    different one -- so the per-folder parts (scanner, links, watcher, title)
    are swapped together by reopen(), using the factory cli.py hands in.
    """

    def __init__(self, scanner, links, runner, token, title, ui_config=None,
                 watcher=None, remember_layout=True, lifecycle=None,
                 read_only=False, reopen=None, bootstrap=None):
        self.scanner = scanner
        self.links = links
        self.runner = runner
        self.token = token
        self.title = title
        self.ui_config = ui_config or {}
        self.watcher = watcher
        self.remember_layout = remember_layout
        self.lifecycle = lifecycle
        self.read_only = read_only
        self._reopen = reopen
        self.lock = threading.Lock()
        # one switch at a time: two at once could leave the runner opening
        # editors in the folder that lost the race
        self._switching = threading.Lock()
        self.projects_lock = threading.Lock()
        # the private page the browser was handed instead of the address; it
        # has done its job once the real page has been asked for
        self.bootstrap = bootstrap
        # Live mode's feed of changes, started the first time live is turned
        # on: until then nobody pays for it, in watches or in a walk of the
        # tree. None for good when cortex was told not to watch.
        self.watching = watcher is not None
        self.activity = None
        self._activity_lock = threading.Lock()
        self.du = None                     # disk usage, counted on first use

    def disk(self):
        with self._activity_lock:
            if self.du is None:
                self.du = DiskUsage(inside=self.scanner.inside)
            return self.du

    def feed(self):
        with self._activity_lock:
            if self.activity is None and self.watching:
                self.activity = Activity(self.scanner)
                self.activity.start()
                # the folders the window opened before live was turned on
                known = self.watcher.folders() if self.watcher is not None else []
                feed = self.activity
                threading.Thread(target=lambda: [feed.note_dir(d) for d in known],
                                 daemon=True).start()
            return self.activity

    def stop_feed(self) -> None:
        with self._activity_lock:
            feed, self.activity = self.activity, None
            du, self.du = self.du, None
        if feed is not None:
            feed.stop()
        if du is not None:
            du.stop()

    @property
    def can_reopen(self) -> bool:
        return self._reopen is not None

    def reopen(self, root: str) -> None:
        with self._switching:
            world = self._reopen(root)
            with self.lock:
                old = self.links
                self.scanner = world["scanner"]
                self.links = world["links"]
                self.watcher = world.get("watcher")
                self.title = world["title"]
                self.ui_config.update(world.get("ui") or {})
                self.runner.home = root
        if old is not None and old is not self.links:
            old.stop()
        self.stop_feed()                   # it was watching the old folder

    def forget_bootstrap(self) -> None:
        path, self.bootstrap = self.bootstrap, None
        if path:
            try:
                os.remove(path)
            except OSError:
                pass


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

    def _host_ok(self) -> bool:
        host = (self.headers.get("Host") or "").strip().lower()
        if host.startswith("["):
            name = host.split("]", 1)[0] + "]"
        else:
            name = host.split(":", 1)[0]
        return name in LOCAL_HOSTS

    def _origin_ok(self) -> bool:
        """A browser says where a POST comes from. It must be from here.

        No Origin at all is a program, not a page, and still needs the token.
        "null" is a sandboxed document -- a file from disk opened on its own --
        and is refused like any other site.
        """
        origin = self.headers.get("Origin")
        if origin is None:
            return True
        try:
            host = urlparse(origin).hostname
        except ValueError:
            return False
        return host in ("127.0.0.1", "localhost", "::1")

    def _authed(self, params) -> bool:
        given = (params.get("t") or [None])[0]
        return given is not None and secrets.compare_digest(
            given.encode(), self.ctx.token.encode())

    def _safe_path(self, params) -> str | None:
        # parse_qs has already decoded the value; decoding it a second time
        # turned a file literally named "a%20b.md" into "a b.md"
        raw = (params.get("path") or [None])[0]
        return self._inside(raw)

    def _inside(self, raw) -> str | None:
        """The real path, if it is inside the root, following symlinks."""
        if not isinstance(raw, str) or not raw:
            return None
        path = os.path.realpath(raw)
        return path if self.ctx.scanner.inside(path) else None

    def _entry(self, raw) -> str | None:
        """The path itself, *not* following a final symlink, if it is inside.

        Renaming or trashing a symlink has to act on the link. Resolving the
        whole path, the way reading does, would rename or trash whatever the
        link points at instead.
        """
        if not isinstance(raw, str) or not raw.startswith("/"):
            return None
        path = os.path.normpath(raw)
        name = os.path.basename(path)
        parent = os.path.realpath(os.path.dirname(path))
        if not name or not self.ctx.scanner.inside(parent):
            return None
        entry = os.path.join(parent, name)
        return None if entry == self.ctx.scanner.root else entry

    # -- dispatch ----------------------------------------------------------

    def do_GET(self):
        self._guarded(self._get)

    def do_HEAD(self):
        self._guarded(self._get)

    def do_POST(self):
        self._guarded(self._post)

    def _guarded(self, route):
        """Every request, with nothing allowed to escape onto the terminal.

        An exception here used to reach socketserver, which prints a traceback
        to stderr -- the terminal, where an editor may be running in the
        foreground. It goes to the log instead, and the page gets an answer.
        """
        if not self._host_ok():
            return self._send(403, b"cortex answers to 127.0.0.1 only",
                              "text/plain; charset=utf-8")
        try:
            route()
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            pass                                  # the page went away mid-answer
        except Exception:
            log.exception("%s %s failed", self.command, self.path)
            try:
                self._json({"ok": False, "error": "cortex hit an internal error; "
                                                  "see ~/.cache/cortex/cortex.log"}, 500)
            except Exception:
                pass

    # -- reads -------------------------------------------------------------

    def _get(self):
        url = urlparse(self.path)
        params = parse_qs(url.query)
        route = url.path

        if route.startswith("/static/"):
            return self._static(route[len("/static/"):])

        if not self._authed(params):
            if route in ("/", "/index.html"):
                return self._send(403, "This is cortex. Open the link it printed "
                                       "when it started, or start it again.",
                                  "text/plain; charset=utf-8")
            return self._json({"error": "bad token"}, 403)

        if route in ("/", "/index.html"):
            return self._index()

        if route == "/api/root":
            sc = self.ctx.scanner
            return self._json(sc.node(sc.root))

        if route == "/api/children":
            path = self._safe_path(params)
            if not path:
                return self._json({"error": "bad path"}, 400)
            kids = self.ctx.scanner.children(path)
            # `is not None`, not a truth test: a Watcher has __len__, so an
            # empty one is falsy and would never get its first directory.
            if self.ctx.watcher is not None:
                self.ctx.watcher.note(path)
            feed = self.ctx.activity
            if feed is not None:
                # it may wait a moment for its own thread; this answer need not
                threading.Thread(target=feed.note_dir, args=(path,), daemon=True).start()
            return self._json(kids)

        if route == "/api/pulse":
            root = self.ctx.scanner.root
            if self.ctx.watcher is None:
                return self._json({"changed": [], "reindexed": False, "root": root})
            changed = self.ctx.watcher.poll()
            reindexed = False
            if changed:
                # Parsed ignore rules are only stale if a .gitignore moved.
                # Dropping them for any change meant re-reading every one of
                # them on the next expansion, for nothing.
                if any(os.path.isfile(os.path.join(d, ".gitignore"))
                       for d in changed):
                    self.ctx.scanner.forget_ignores()
                if self.ctx.du is not None:
                    for d in changed:
                        self.ctx.du.invalidate(d)
                reindexed = self.ctx.links.maybe_rebuild()
            return self._json({"changed": changed, "reindexed": reindexed,
                               "root": root})

        if route == "/api/search":
            term = (params.get("q") or [""])[0]
            hits = self.ctx.scanner.search(term)
            return self._json({"results": hits, "count": len(hits)})

        if route == "/api/grep":
            term = (params.get("q") or [""])[0]
            return self._json(grep.search(self.ctx.scanner, term))

        if route == "/api/preview":
            path = self._safe_path(params)
            if not path or not os.path.isfile(path):
                return self._json({"kind": "error", "message": "not a file"}, 400)
            return self._json(reader.preview(path))

        if route == "/api/source":
            path = self._safe_path(params)
            if not path:
                return self._json({"ok": False, "error": "bad path"}, 400)
            try:
                return self._json({"ok": True, **files.read_source(path)})
            except files.FileError as exc:
                return self._json(exc.reply(), exc.status)

        if route == "/api/raw":
            return self._raw(params)

        if route == "/api/git":
            raw = (params.get("path") or [None])[0]
            path = self._safe_path(params) if raw else self.ctx.scanner.root
            if not path:
                return self._json({"error": "bad path"}, 400)
            return self._json(gitstatus.read(path, self.ctx.scanner.inside))

        if route == "/api/layout":
            if not self.ctx.remember_layout:
                return self._json({"positions": {}, "cam": None})
            return self._json(layout.load(self.ctx.scanner.root))

        if route == "/api/links":
            snap = self.ctx.links.snapshot()
            if snap.get("suggest"):
                snap["suggest"] = dismissed.without(self.ctx.scanner.root, snap["suggest"])
            return self._json(snap)

        if route == "/api/editors":
            return self._json({"editors": self.ctx.runner.editors(),
                               "terminal": self.ctx.runner.mode})

        if route == "/api/places":
            return self._json(self._places())

        if route == "/api/dirs":
            return self._json(places.complete((params.get("path") or [""])[0]))

        if route == "/api/activity":
            feed = self.ctx.feed()
            if feed is None:
                return self._json({"off": True, "seq": 0, "events": [], "engine": None})
            try:
                since = int((params.get("since") or ["0"])[0])
                wait = min(25.0, max(0.0, float((params.get("wait") or ["0"])[0])))
            except ValueError:
                return self._json({"error": "bad since"}, 400)
            got = feed.since(since, wait)
            du = self.ctx.du
            if du is not None:
                # a file grown in place leaves its folder's timestamp alone
                for ev in got.get("events") or []:
                    du.invalidate(ev.get("path") or "")
            return self._json(got)

        if route == "/api/history":
            path = self._safe_path(params)
            if not path or not os.path.isdir(path):
                return self._json({"error": "bad path"}, 400)
            return self._json(history.timeline(path, self.ctx.scanner.inside))

        if route == "/api/du":
            path = self._safe_path(params)
            if not path or not os.path.isdir(path):
                return self._json({"error": "bad path"}, 400)
            try:
                got = self.ctx.disk().get(path)
            except ValueError:
                return self._json({"error": "bad path"}, 400)
            kids = got.get("children") or []
            if len(kids) > 2000:
                # the biggest are what the window shows; a folder of a hundred
                # thousand files is told how many more there are
                got = {**got, "children": kids[:2000], "more": len(kids) - 2000}
            return self._json(got)

        if route == "/api/views":
            return self._json({"views": views.load(self.ctx.scanner.root)})

        if route == "/api/tours":
            sc = self.ctx.scanner
            return self._json({"tours": tours.list_all(sc.root, sc.inside)})

        if route == "/api/tour":
            path = self._tour_path((params.get("path") or [None])[0])
            if not path or not os.path.isfile(path):
                return self._json({"ok": False, "error": "no such tour"}, 404)
            sc = self.ctx.scanner
            return self._json({"ok": True, **tours.read(path, sc.root, sc.inside)})

        return self._json({"error": "not found"}, 404)

    # -- writes ------------------------------------------------------------

    def _post(self):
        url = urlparse(self.path)
        params = parse_qs(url.query)
        if not self._authed(params):
            return self._json({"error": "bad token"}, 403)
        if not self._origin_ok():
            return self._json({"error": "not from this window"}, 403)

        if url.path in BEACONS:
            client = (params.get("c") or [""])[0][:64]
            life = self.ctx.lifecycle
            if life is not None and client:
                (life.hello if url.path == "/api/hello" else life.bye)(client)
            return self._json({"ok": True})

        routes = {
            "/api/action": self._action,
            "/api/layout": self._save_layout,
            "/api/save": self._save_file,
            "/api/fs": self._file_op,
            "/api/open": self._open_folder,
            "/api/project": self._project,
            "/api/launcher": self._launcher,
            "/api/quit": self._quit,
            "/api/nodes": self._nodes,
            "/api/views": self._views,
            "/api/tours": self._tours,
            "/api/daily": self._daily,
            "/api/capture": self._capture,
            "/api/snapshot": self._snapshot,
            "/api/dismiss": self._dismiss,
        }
        handler = routes.get(url.path)
        if handler is None:
            return self._json({"error": "not found"}, 404)
        # JSON only: a browser will not send that cross-origin without asking
        # first, and nothing here answers the asking
        ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if ctype != "application/json":
            return self._json({"ok": False, "error": "send JSON"}, 415)

        try:
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BODY:
                return self._json({"ok": False, "error": "body too large"}, 413)
            body = json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, TypeError):
            return self._json({"ok": False, "error": "bad body"}, 400)
        if not isinstance(body, dict):
            return self._json({"ok": False, "error": "bad body"}, 400)
        return handler(body)

    def _action(self, body):
        path = self._inside(body.get("path"))
        if not path:
            return self._json({"ok": False, "error": "path outside root"}, 400)
        result = self.ctx.runner.submit(body.get("kind", ""), path,
                                        body.get("editor"), body.get("line"))
        return self._json(result)

    def _save_layout(self, body):
        if not self.ctx.remember_layout:
            return self._json({"ok": False, "error": "not remembering"})
        # A page saves its layout as it closes, and when the window has just
        # opened another folder that save arrives after the switch. Filed
        # under the new folder it would overwrite that folder's own layout.
        claimed = body.get("root")
        if claimed is not None and claimed != self.ctx.scanner.root:
            return self._json({"ok": False, "error": "that folder is not open"})
        ok = layout.save(self.ctx.scanner.root, body.get("positions") or {},
                         body.get("cam"))
        return self._json({"ok": ok})

    def _writable(self):
        if self.ctx.read_only:
            self._json({"ok": False, "error": "cortex was started read-only"}, 403)
            return False
        return True

    def _save_file(self, body):
        if not self._writable():
            return
        raw = body.get("path")
        path = self._inside(raw)
        if not path and isinstance(raw, str) and body.get("force"):
            # saving a file that was deleted while open puts it back, if the
            # folder it was in is still inside the root
            entry = self._entry(raw)
            path = entry if entry and not os.path.lexists(entry) else None
        if not path:
            return self._json({"ok": False, "error": "bad path"}, 400)
        if self.ctx.du is not None:
            self.ctx.du.invalidate(path)
        try:
            return self._json(files.save(path, body.get("text"),
                                         body.get("version"), bool(body.get("force"))))
        except files.FileError as exc:
            return self._json(exc.reply(), exc.status)

    def _file_op(self, body):
        if not self._writable():
            return
        op = body.get("op")
        try:
            if op in ("new", "mkdir"):
                folder = self._inside(body.get("path"))
                if not folder:
                    return self._json({"ok": False, "error": "bad path"}, 400)
                result = files.create(folder, body.get("name"),
                                      "dir" if op == "mkdir" else "file",
                                      parents=bool(body.get("parents")),
                                      inside=self.ctx.scanner.inside)
            elif op in ("rename", "trash"):
                path = self._entry(body.get("path"))
                if not path:
                    return self._json({"ok": False, "error": "bad path"}, 400)
                result = (files.rename(path, body.get("name")) if op == "rename"
                          else files.trash(path))
            else:
                return self._json({"ok": False, "error": f"unknown operation {op!r}"})
        except files.FileError as exc:
            return self._json(exc.reply(), exc.status)
        return self._json(result)

    def _open_folder(self, body):
        if not self.ctx.can_reopen:
            return self._json({"ok": False, "error": "this cortex cannot switch folders"})
        raw = body.get("path")
        if not isinstance(raw, str) or not raw.strip():
            return self._json({"ok": False, "error": "which folder?"})
        root = os.path.realpath(os.path.expanduser(raw.strip()))
        if not os.path.isdir(root):
            return self._json({"ok": False, "error": f"not a folder: {raw.strip()}"})
        if not os.access(root, os.R_OK | os.X_OK):
            return self._json({"ok": False, "error": "cortex is not allowed to read that folder"})
        self.ctx.reopen(root)
        places.remember(root)
        return self._json({"ok": True, "root": root})

    def _project(self, body):
        op = body.get("op")
        name = places.clean_project_name(body.get("name"))
        if not name:
            return self._json({"ok": False, "error": "give it a name"})
        if op not in ("save", "forget"):
            return self._json({"ok": False, "error": f"unknown operation {op!r}"})
        with self.ctx.projects_lock:
            projects = places.load_projects()
            if op == "save":
                projects[name] = self.ctx.scanner.root
            else:
                projects.pop(name, None)
            try:
                places.save_projects(projects)
            except OSError as exc:
                return self._json({"ok": False, "error": f"could not save: {exc}"})
            desktop.refresh(projects)
        return self._json({"ok": True, **self._places()})

    def _launcher(self, body):
        op = body.get("op")
        if op == "install":
            result = desktop.install(places.load_projects())
        elif op == "remove":
            result = desktop.remove()
        else:
            return self._json({"ok": False, "error": f"unknown operation {op!r}"})
        return self._json({**result, "launcher": desktop.status()})

    def _nodes(self, body):
        """Nodes, with their folder chains, for paths anywhere under the root.

        What the tour, the path finder, the change feed and the history use to
        put a file on the graph without opening every folder on the way.
        """
        paths = body.get("paths")
        if not isinstance(paths, list):
            return self._json({"ok": False, "error": "which paths?"}, 400)
        hits, cache = [], {}
        for raw in paths[:5000]:
            hit = self.ctx.scanner.lineage(raw, cache)
            if hit is not None:
                hits.append(hit)
        return self._json({"ok": True, "hits": hits})

    def _views(self, body):
        sc, op = self.ctx.scanner, body.get("op")
        try:
            if op == "save":
                result = views.save(sc.root, body.get("view"), sc.inside)
            elif op == "forget":
                result = views.forget(sc.root, body.get("name"))
            elif op == "move":
                result = views.move(sc.root, body.get("name"), body.get("to", 0))
            else:
                return self._json({"ok": False, "error": f"unknown operation {op!r}"})
        except (ValueError, TypeError) as exc:
            return self._json({"ok": False, "error": str(exc)})
        except OSError as exc:
            return self._json({"ok": False, "error": f"could not save: {exc.strerror or exc}"})
        return self._json({"ok": True, "views": result})

    def _tour_path(self, raw) -> str | None:
        """A tour file: a .md directly inside the root's .tours folder."""
        if not isinstance(raw, str):
            return None
        path = os.path.normpath(raw)
        where = tours.folder(self.ctx.scanner.root)
        if os.path.dirname(path) != where or not path.endswith(".md"):
            return None
        # .tours, or the file in it, could be a symlink leading out of the root
        sc = self.ctx.scanner
        return path if sc.inside(where) and sc.inside(path) else None

    def _tours(self, body):
        if not self._writable():
            return
        sc, op = self.ctx.scanner, body.get("op")
        if not sc.inside(tours.folder(sc.root)):
            return self._json({"ok": False, "error": "the .tours folder leads "
                                                     "outside this folder"}, 400)
        try:
            if op == "save":
                existing = self._tour_path(body.get("path")) if body.get("path") else None
                if body.get("path") and not existing:
                    return self._json({"ok": False, "error": "bad tour path"}, 400)
                path = tours.write(sc.root, body.get("title") or "",
                                   body.get("intro") or "", body.get("stops") or [],
                                   sc.inside, existing)
                return self._json({"ok": True, "path": path})
            if op == "delete":
                path = self._tour_path(body.get("path"))
                if not path or not os.path.isfile(path):
                    return self._json({"ok": False, "error": "no such tour"}, 404)
                return self._json(files.trash(path))
        except files.FileError as exc:
            return self._json(exc.reply(), exc.status)
        except (OSError, ValueError) as exc:
            return self._json({"ok": False, "error": f"could not save the tour: {exc}"})
        return self._json({"ok": False, "error": f"unknown operation {op!r}"})

    def _daily(self, body):
        if not self._writable():
            return
        try:
            sc = self.ctx.scanner
            return self._json(journal.open_today(sc.root, inside=sc.inside))
        except files.FileError as exc:
            return self._json(exc.reply(), exc.status)

    def _capture(self, body):
        if not self._writable():
            return
        try:
            sc = self.ctx.scanner
            return self._json(journal.capture(sc.root, body.get("text"), inside=sc.inside))
        except files.FileError as exc:
            return self._json(exc.reply(), exc.status)

    def _snapshot(self, body):
        """The window as one HTML file, sent back as a download."""
        sc = self.ctx.scanner
        root = self._inside(body.get("root")) or sc.root
        if not os.path.isdir(root):
            return self._json({"ok": False, "error": "not a folder"}, 400)
        page, summary = snapshot.build(sc, self.ctx.links, root, body)
        name = (os.path.basename(root) or "cortex").replace('"', "")
        plain = name.encode("ascii", "replace").decode().replace("?", "_")
        return self._send(200, page, "text/html; charset=utf-8", {
            "Content-Disposition": f'attachment; filename="{plain}-map.html"; '
                                   f"filename*=UTF-8''{quote(name)}-map.html",
            "X-Cortex-Summary": json.dumps(summary),
        })

    def _dismiss(self, body):
        a, b = body.get("a"), body.get("b")
        if not (self._inside(a) and self._inside(b)):
            return self._json({"ok": False, "error": "bad path"}, 400)
        try:
            dismissed.add(self.ctx.scanner.root, a, b)
        except OSError as exc:
            return self._json({"ok": False, "error": f"could not save: {exc.strerror or exc}"})
        return self._json({"ok": True})

    def _quit(self, body):
        if self.ctx.lifecycle is None:
            return self._json({"ok": False, "error": "nothing to quit"})
        # recorded before answering: once the answer is out, the page may ask
        # whether cortex is stopping, and it must already be
        self.ctx.lifecycle.quit("quit from the window")
        self._json({"ok": True})

    # -- helpers -----------------------------------------------------------

    def _places(self) -> dict:
        root = self.ctx.scanner.root
        projects = places.load_projects()
        named = {path for path in projects.values()}
        return {
            "root": root,
            "home": places.HOME,
            "parent": os.path.dirname(root) if root != "/" else None,
            "projects": [{"name": n, "path": p, "missing": not os.path.isdir(p)}
                         for n, p in sorted(projects.items(), key=lambda kv: kv[0].lower())],
            "recent": [p for p in places.load_recent()
                       if p not in named and os.path.isdir(p)][:places.RECENT_MAX],
            "launcher": desktop.status(),
            "canSwitch": self.ctx.can_reopen,
        }

    def _index(self):
        try:
            with open(os.path.join(UI_DIR, "index.html"), "r",
                      encoding="utf-8") as fh:
                page = fh.read()
        except OSError:
            return self._send(500, b"ui missing", "text/plain")
        if self.ctx.lifecycle is not None:
            self.ctx.lifecycle.touch()         # a reload is not a close
        self.ctx.forget_bootstrap()
        cfg = {"token": self.ctx.token, "root": self.ctx.scanner.root,
               "title": self.ctx.title, "home": places.HOME,
               "readOnly": self.ctx.read_only,
               "terminal": self.ctx.runner.mode,
               "canQuit": self.ctx.lifecycle is not None}
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
        if not os.path.realpath(full).startswith(os.path.realpath(UI_DIR) + os.sep):
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
        # Headers are latin-1, and a name like 日本.pdf raised partway through
        # the reply. The plain name is an ASCII stand-in; filename* is the real
        # one, percent-encoded the way RFC 6266 lets a header carry UTF-8.
        name = os.path.basename(path)
        plain = name.encode("ascii", "replace").decode().replace('"', "'")
        self.send_header("Content-Disposition",
                         f"inline; filename=\"{plain}\"; "
                         f"filename*=UTF-8''{quote(name)}")
        self.send_header("X-Content-Type-Options", "nosniff")
        if ctype != "application/pdf":
            self.send_header("Content-Security-Policy", RAW_CSP)
        self.end_headers()
        if self.command == "HEAD":
            return
        try:
            for chunk in reader.stream(path):
                self.wfile.write(chunk)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        # The default prints a traceback to stderr, which is the terminal --
        # and while an editor runs in the foreground, the middle of someone's
        # editing session. A browser dropping a connection is routine.
        log.debug("connection from %s ended badly", client_address, exc_info=True)


def serve(ctx: Context, port: int = 0) -> ThreadingHTTPServer:
    Handler.ctx = ctx
    return Server(("127.0.0.1", port), Handler)
