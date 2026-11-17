"""Terminals inside the window.

An agent has an interface of its own, and so does a shell, and both are made
for a terminal. Until now cortex could only hand them to one: the terminal it
was started in, or a window it asked the desktop to open. This gives the
window terminals of its own -- real ones, each a program on a pseudo-terminal
-- so the agent's own interface, a shell in its folder, or a test runner can
sit in a tab beside the graph.

The server's half is small: start the program on a pty, pass what it prints
to the window, pass what is typed to it, and tell it when the tab changes
size. What the bytes mean -- the colours, the cursor, the screen being
cleared -- is the window's business (ui/terminal.js).

The window reads every terminal through one long poll, not one each: a
browser allows a page only a handful of connections to one server, and a
poll per tab would use them all up and leave none for anything else.

Nothing the window sends chooses the program. The caller here names it -- the
person's shell, or an agent's command line built by agents.py -- and the
window can only ask for one of those.
"""

from __future__ import annotations

import base64
import fcntl
import json
import os
import signal
import struct
import subprocess
import sys
import termios
import threading
import time
import uuid
from collections import deque

MAX_TERMINALS = 32
MAX_LAYOUTS = 32                 # arrangements of panes remembered, one per tab
MAX_LAYOUT = 16 * 1024           # bytes of one, as JSON
LOG_BYTES = 4 * 1024 * 1024      # held for the long poll, across all terminals
REPLAY_BYTES = 256 * 1024        # held per terminal, for a window that reloads
MAX_INPUT = 1024 * 1024          # one write; a paste can be long, not endless
READ = 65536
KILL_AFTER = 1.5

# Run in a fresh interpreter between cortex and the program. A terminal's
# program has to be the leader of its own session with the pty as its
# controlling terminal, or ctrl-c reaches nothing and the shell refuses job
# control. That takes a few calls between fork and exec, which are not safe
# to make in a process with other threads running -- and cortex always has
# other threads. So cortex starts this instead, which has none.
SHIM = r"""
import fcntl, os, sys, termios
tty, cwd = sys.argv[1], sys.argv[2]
fd = os.open(tty, os.O_RDWR)
try:
    fcntl.ioctl(fd, termios.TIOCSCTTY, 0)
except OSError:
    pass
for target in (0, 1, 2):
    os.dup2(fd, target)
if fd > 2:
    os.close(fd)
try:
    os.chdir(cwd)
except OSError:
    pass
try:
    os.execvp(sys.argv[3], sys.argv[3:])
except OSError as exc:
    os.write(2, ("could not start %s: %s\r\n" % (sys.argv[3], exc.strerror)).encode())
    os._exit(127)
"""


def supported() -> bool:
    return hasattr(os, "openpty") and sys.platform != "win32"


def default_shell() -> list[str]:
    shell = os.environ.get("SHELL") or "/bin/sh"
    return [shell if os.path.isfile(shell) else "/bin/sh"]


class Terminal:
    def __init__(self, tid: str, title: str, cwd: str, kind: str, owner: str | None) -> None:
        self.id = tid
        self.mux = tid                  # the tab it is a pane of; its own, until told
        self.title = title
        self.cwd = cwd
        self.kind = kind                # "shell", or "agent" for an agent's own interface
        self.owner = owner              # the agent it belongs to, if any
        self.master = -1
        self.proc: subprocess.Popen | None = None
        self.alive = True
        self.code: int | None = None
        self.cols, self.rows = 100, 30
        self.replay = bytearray()
        self.started = time.time()

    def summary(self) -> dict:
        return {"id": self.id, "mux": self.mux, "title": self.title, "cwd": self.cwd,
                "kind": self.kind,
                "owner": self.owner, "alive": self.alive, "code": self.code,
                "cols": self.cols, "rows": self.rows, "started": self.started}


class Terminals:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._changed = threading.Condition(self._lock)
        self._terms: dict[str, Terminal] = {}
        self._log: deque = deque()      # (seq, terminal id, bytes), oldest first
        self._log_bytes = 0
        self._seq = 0
        self._first = 1                 # the oldest seq still in the log
        self._stopping = False
        # How each tab's panes are arranged. The window decides that and only
        # the window reads it; it is kept here so that a window which reloads
        # finds its panes where it left them.
        self._layouts: dict[str, object] = {}

    # -- starting and stopping ------------------------------------------------

    def open(self, argv: list[str], cwd: str, title: str, kind: str = "shell",
             owner: str | None = None, cols: int = 100, rows: int = 30,
             env: dict | None = None, mux: str | None = None) -> dict:
        if not supported():
            return {"ok": False, "error": "this system has no terminals to give"}
        if not os.path.isdir(cwd):
            return {"ok": False, "error": "that folder no longer exists"}
        with self._lock:
            if sum(1 for t in self._terms.values() if t.alive) >= MAX_TERMINALS:
                return {"ok": False, "error": "that is a great many terminals - "
                                              "close some first"}
        term = Terminal(uuid.uuid4().hex[:8], title, cwd, kind, owner)
        if isinstance(mux, str) and 0 < len(mux) <= 64:
            term.mux = mux
        term.cols, term.rows = _clamp(cols, 20, 400), _clamp(rows, 5, 200)
        try:
            master, slave = os.openpty()
        except OSError as exc:
            return {"ok": False, "error": f"could not open a terminal: {exc.strerror or exc}"}
        full = dict(os.environ)
        full.update({"TERM": "xterm-256color", "COLORTERM": "truecolor",
                     "CORTEX_TERMINAL": term.id})
        full.pop("NO_COLOR", None)
        if env:
            full.update(env)
        try:
            _resize(master, term.cols, term.rows)
            term.proc = subprocess.Popen(
                [sys.executable, "-c", SHIM, os.ttyname(slave), cwd, *argv],
                stdin=slave, stdout=slave, stderr=slave, env=full, cwd=cwd,
                start_new_session=True, close_fds=True)
        except OSError as exc:
            os.close(master)
            os.close(slave)
            return {"ok": False, "error": f"could not start {argv[0]}: {exc.strerror or exc}"}
        os.close(slave)
        term.master = master
        with self._changed:
            self._terms[term.id] = term
            self._changed.notify_all()
        threading.Thread(target=self._read, args=(term,), daemon=True,
                         name=f"cortex-term-{term.id}").start()
        return {"ok": True, "id": term.id, "term": term.summary()}

    def close(self, tid) -> dict:
        """End a terminal's program and take it off the list."""
        with self._changed:
            term = self._terms.pop(tid, None) if isinstance(tid, str) else None
            self._changed.notify_all()
        if term is None:
            return {"ok": False, "error": "no such terminal"}
        self._end(term)
        return {"ok": True}

    def where(self, tid) -> str | None:
        """The folder a terminal's program is in right now, for a pane opened
        beside it -- or the one it started in, where the system will not say."""
        term = self._get(tid)
        if term is None:
            return None
        if term.alive and term.proc is not None:
            try:
                here = os.readlink(f"/proc/{term.proc.pid}/cwd")
                if os.path.isdir(here):
                    return here
            except OSError:
                pass
        return term.cwd if os.path.isdir(term.cwd) else None

    def regroup(self, tid, mux) -> dict:
        """Make a terminal a pane of another tab: a pane broken out into a
        tab of its own, or brought into one."""
        term = self._get(tid)
        if term is None:
            return {"ok": False, "error": "no such terminal"}
        if not isinstance(mux, str) or not 0 < len(mux) <= 64:
            return {"ok": False, "error": "which tab?"}
        with self._changed:
            term.mux = mux
            self._changed.notify_all()
        return {"ok": True, "term": term.summary()}

    def arrange(self, mux, layout) -> dict:
        """Remember how a tab's panes are arranged; None forgets it."""
        if not isinstance(mux, str) or not 0 < len(mux) <= 64:
            return {"ok": False, "error": "which tab?"}
        with self._lock:
            if layout is None:
                self._layouts.pop(mux, None)
                return {"ok": True}
            try:
                size = len(json.dumps(layout))
            except (TypeError, ValueError):
                return {"ok": False, "error": "bad layout"}
            if size > MAX_LAYOUT or not isinstance(layout, dict):
                return {"ok": False, "error": "bad layout"}
            self._layouts.pop(mux, None)
            self._layouts[mux] = layout
            while len(self._layouts) > MAX_LAYOUTS:
                self._layouts.pop(next(iter(self._layouts)))
        return {"ok": True}

    def close_owned(self, owner: str) -> None:
        """An agent is going: so is its interface, if it is open in a tab."""
        with self._lock:
            mine = [t.id for t in self._terms.values() if t.owner == owner]
        for tid in mine:
            self.close(tid)

    def activity(self) -> tuple[int, int]:
        """(terminals with something running in them, terminals open).

        A shell sitting at its prompt is not running anything; one with a
        program in the foreground is, and the terminal itself knows which --
        the foreground group is the shell's own only while it waits. An
        agent's own interface counts for as long as it is open."""
        busy = alive = 0
        with self._lock:
            terms = [t for t in self._terms.values() if t.alive and t.proc is not None]
        for term in terms:
            alive += 1
            if term.kind != "shell":
                busy += 1
                continue
            try:
                if os.tcgetpgrp(term.master) != term.proc.pid:
                    busy += 1
            except OSError:
                pass
        return busy, alive

    def stop_all(self) -> None:
        with self._changed:
            self._stopping = True
            terms = list(self._terms.values())
            self._terms.clear()
            self._changed.notify_all()
        for term in terms:
            self._end(term, wait=0.5)

    def _end(self, term: Terminal, wait: float = KILL_AFTER) -> None:
        proc = term.proc
        if proc is not None and proc.poll() is None:
            # what a real terminal does when its window is closed
            for sig in (signal.SIGHUP, signal.SIGKILL):
                try:
                    os.killpg(proc.pid, sig)
                except (OSError, ProcessLookupError):
                    break
                try:
                    proc.wait(wait)
                    break
                except subprocess.TimeoutExpired:
                    continue
        fd, term.master = term.master, -1
        if fd >= 0:
            try:
                os.close(fd)
            except OSError:
                pass

    # -- what is typed, and the size of the tab -------------------------------

    def write(self, tid, data) -> dict:
        term = self._get(tid)
        if term is None or not term.alive:
            return {"ok": False, "error": "that terminal has ended"}
        if not isinstance(data, str) or len(data) > MAX_INPUT:
            return {"ok": False, "error": "bad input"}
        raw = data.encode("utf-8", "replace")
        try:
            while raw:
                raw = raw[os.write(term.master, raw):]
        except OSError:
            return {"ok": False, "error": "that terminal has ended"}
        return {"ok": True}

    def resize(self, tid, cols, rows) -> dict:
        term = self._get(tid)
        if term is None:
            return {"ok": False, "error": "no such terminal"}
        try:
            term.cols, term.rows = _clamp(int(cols), 20, 400), _clamp(int(rows), 5, 200)
        except (TypeError, ValueError):
            return {"ok": False, "error": "bad size"}
        if term.alive and term.master >= 0:
            try:
                _resize(term.master, term.cols, term.rows)
            except OSError:
                pass
        return {"ok": True}

    def _get(self, tid) -> Terminal | None:
        with self._lock:
            return self._terms.get(tid) if isinstance(tid, str) else None

    # -- what they print -------------------------------------------------------

    def _read(self, term: Terminal) -> None:
        fd = term.master
        while True:
            try:
                data = os.read(fd, READ)
            except OSError:
                data = b""              # the program closed its end: it has gone
            if not data:
                break
            with self._changed:
                self._add(term, data)
        code = term.proc.wait() if term.proc is not None else None
        with self._changed:
            term.alive, term.code = False, code
            # a line of its own, so the window hears of the end the way it
            # hears of everything else
            self._add(term, b"")

    def _add(self, term: Terminal, data: bytes) -> None:
        """Caller holds the lock."""
        self._seq += 1
        self._log.append((self._seq, term.id, data))
        self._log_bytes += len(data)
        while self._log_bytes > LOG_BYTES and len(self._log) > 1:
            _seq, _tid, old = self._log.popleft()
            self._log_bytes -= len(old)
            self._first = self._log[0][0]
        term.replay += data
        if len(term.replay) > REPLAY_BYTES:
            # cut at a line, so what is left does not begin mid-sequence
            cut = len(term.replay) - REPLAY_BYTES
            at = term.replay.find(b"\n", cut)
            del term.replay[:(at + 1) if at != -1 else cut]
        self._changed.notify_all()

    def poll(self, since: int = 0, wait: float = 0.0) -> dict:
        """Everything printed after `since`, by every terminal.

        {"seq", "chunks": [[seq, terminal id, base64], ...], "terms": [...]}, and
        "lost" when `since` is further back than is still held: the window
        then asks each terminal for its replay and starts again from there.
        """
        deadline = time.monotonic() + max(0.0, wait)
        with self._changed:
            while self._seq <= since and not self._stopping:
                left = deadline - time.monotonic()
                if left <= 0:
                    break
                self._changed.wait(left)
            lost = bool(self._log) and since + 1 < self._first and since < self._seq
            chunks = [] if lost else [
                [seq, tid, base64.b64encode(data).decode("ascii")]
                for seq, tid, data in self._log if seq > since and data]
            tabs = {t.mux for t in self._terms.values()}
            return {"seq": self._seq, "chunks": chunks, "lost": lost,
                    "terms": [t.summary() for t in self._terms.values()],
                    "layouts": {m: v for m, v in self._layouts.items() if m in tabs}}

    def replay(self, tid) -> dict | None:
        """What a terminal has printed, and the point in the feed it is true at."""
        with self._lock:
            term = self._terms.get(tid) if isinstance(tid, str) else None
            if term is None:
                return None
            return {"id": tid, "seq": self._seq, "term": term.summary(),
                    "data": base64.b64encode(bytes(term.replay)).decode("ascii")}

    def count(self) -> int:
        with self._lock:
            return sum(1 for t in self._terms.values() if t.alive)

    def holds(self, owner: str) -> bool:
        """Whether an agent's own interface is running in a tab right now."""
        with self._lock:
            return any(t.alive and t.owner == owner and t.kind == "agent"
                       for t in self._terms.values())


def _clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, int(value)))


def _resize(fd: int, cols: int, rows: int) -> None:
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
