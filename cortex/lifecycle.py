"""Knowing when the window has gone.

Launched from a terminal, cortex had Ctrl-C. Launched from an app menu it has
nothing, so it has to notice for itself that nobody is looking any more.

Each page says hello when it loads and goodbye when it is closed. Once the last
one has said goodbye and a few seconds have passed with no new hello, it is
time to go -- a reload is a goodbye followed at once by a hello, and the grace
period is what keeps a reload from looking like a close.

There is deliberately no timeout on silence. A browser may throttle or freeze a
minimised window for as long as it likes, and a cortex that quit because a
window was merely minimised would be found dead when someone came back to it.
So a browser that crashes without saying goodbye leaves cortex running; idle,
it does no work.

Going is not always the right thing to do when the window has gone. An agent
half way through a task, a test run in a terminal: closing the window on
those used to end them. Now cortex is asked first whether anything is still
running (`keep`), and if it is, stays -- held -- until someone opens a window
on it again. Work that finishes with nobody there is kept to be come back to,
for a while, and then cortex goes after all.
"""

from __future__ import annotations

import threading
import time

GRACE = 6.0              # seconds between the last goodbye and quitting
LINGER = 12 * 3600.0     # how long finished work waits for someone to come back
MAX_CLIENTS = 64         # windows tracked at once; more is someone misbehaving


class Lifecycle:
    def __init__(self, auto_exit: bool = True, grace: float = GRACE,
                 clock=time.monotonic, keep=None, linger: float = LINGER,
                 tell=None) -> None:
        self.auto_exit = auto_exit
        self.grace = grace
        # keep() says what is running with no window to see it: "busy" (an
        # agent working, a program in a terminal), "idle" (terminals open,
        # nothing running in them) or None. tell(what) is told "held" when
        # cortex stays for it and "finished" when what it stayed for is done.
        self.keep = keep
        self.linger = linger
        self.tell = tell
        self.held = False
        self._last_busy = 0.0
        self._told_finished = False
        self.reason: str | None = None
        self._clock = clock
        self._clients: set[str] = set()
        self._quiet_since: float | None = None
        self._quit = threading.Event()
        self._lock = threading.Lock()

    def hello(self, client: str) -> None:
        with self._lock:
            if len(self._clients) < MAX_CLIENTS:
                self._clients.add(client)
            self._quiet_since = None

    def closed(self) -> None:
        """The last window has closed, and whoever closed it says so. Not a
        reload, so there is nothing to wait for: decide now."""
        with self._lock:
            self._clients.clear()
            self._quiet_since = self._clock() - self.grace

    def bye(self, client: str) -> None:
        with self._lock:
            self._clients.discard(client)
            if not self._clients:
                self._quiet_since = self._clock()

    def touch(self) -> None:
        """A page is loading. It will say hello in a moment; wait for it."""
        with self._lock:
            if self._quiet_since is not None:
                self._quiet_since = self._clock()

    def quit(self, reason: str = "asked to quit") -> None:
        self.reason = reason
        self._quit.set()

    @property
    def clients(self) -> int:
        with self._lock:
            return len(self._clients)

    def should_exit(self) -> bool:
        if self._quit.is_set():
            return True
        if not self.auto_exit:
            return False
        with self._lock:
            if self._clients or self._quiet_since is None:
                self.held = False               # someone is looking
                self._told_finished = False
                return False
            if self._clock() - self._quiet_since < self.grace:
                return False
        state = self.keep() if self.keep is not None else None
        now = self._clock()
        if state == "busy":
            self._last_busy = now
            self._told_finished = False
            if not self.held:
                self.held = True
                self._say("held")
            return False
        if self.held and state == "idle":
            # what it stayed for is done; the result waits to be come back to
            if not self._told_finished:
                self._told_finished = True
                self._say("finished")
            if now - self._last_busy < self.linger:
                return False
        self.reason = "the window was closed"
        return True

    def _say(self, what: str) -> None:
        if self.tell is not None:
            try:
                self.tell(what)
            except Exception:
                pass
