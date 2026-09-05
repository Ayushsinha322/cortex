"""Noticing that the disk changed while you are looking at it.

The graph is otherwise a photograph taken at launch: create a file in your
editor and it is not there until you relaunch.

There is no background thread here.  Directories are remembered as the UI asks
for them, and the poll that asks "what changed?" is the same call that does the
stat-ing, so an idle cortex with no window attached does no work at all.
"""

from __future__ import annotations

import os
import threading
from collections import OrderedDict

WATCH_CAP = 500          # directories remembered; the oldest fall off the end
DEEP_EVERY = 4           # polls between full directory listings


class Watcher:
    """Directory mtimes, compared each time someone asks.

    Two signals, at two rates. A directory's timestamp is one stat and catches
    almost everything, so it is checked every poll. It is not sufficient on its
    own: a change landing in the same filesystem timestamp granule as our last
    look is invisible to it *for good*, not merely late. The entry count closes
    that hole and costs a full directory listing, so it is checked every
    DEEP_EVERY polls instead -- which bounds a missed change to a few seconds
    rather than forever, at a quarter of the I/O of counting every time.
    """

    def __init__(self, cap: int = WATCH_CAP) -> None:
        self.cap = cap
        self._seen: OrderedDict[str, tuple] = OrderedDict()
        self._ticks = 0
        self._lock = threading.Lock()

    def note(self, path: str) -> None:
        """Start watching a directory the UI just read."""
        entry = (_stamp(path), _count(path))
        with self._lock:
            self._seen[path] = entry
            self._seen.move_to_end(path)
            while len(self._seen) > self.cap:
                self._seen.popitem(last=False)

    def forget(self, path: str) -> None:
        with self._lock:
            self._seen.pop(path, None)

    def poll(self) -> list[str]:
        """Directories whose contents changed since the last time we looked.

        A directory that has been deleted counts as changed once and is then
        dropped, so a removed folder is reported to the UI exactly once.
        """
        with self._lock:
            self._ticks += 1
            deep = self._ticks % DEEP_EVERY == 0
            watching = list(self._seen.items())

        changed: list[str] = []
        gone: list[str] = []
        fresh: dict[str, tuple] = {}

        for path, (stamp, count) in watching:
            now_stamp = _stamp(path)
            now_count = _count(path) if (deep or now_stamp != stamp) else count
            if now_stamp == stamp and now_count == count:
                continue
            changed.append(path)
            fresh[path] = (now_stamp, now_count)
            if now_stamp is None:
                gone.append(path)

        if changed:
            with self._lock:
                for path, entry in fresh.items():
                    if path in self._seen:
                        self._seen[path] = entry
                for path in gone:
                    self._seen.pop(path, None)
        return changed

    def __len__(self) -> int:
        with self._lock:
            return len(self._seen)


def _stamp(path: str) -> int | None:
    """A directory's modification time in nanoseconds, or None if it is gone."""
    try:
        st = os.stat(path)
    except OSError:
        return None
    return st.st_mtime_ns ^ (st.st_ctime_ns << 1)


def _count(path: str) -> int:
    """How many entries the directory holds. -1 when it cannot be read."""
    try:
        with os.scandir(path) as it:
            return sum(1 for _ in it)
    except OSError:
        return -1
