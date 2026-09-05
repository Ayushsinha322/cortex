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


class Watcher:
    """Directory mtimes, compared each time someone asks."""

    def __init__(self, cap: int = WATCH_CAP) -> None:
        self.cap = cap
        self._seen: OrderedDict[str, float] = OrderedDict()
        self._lock = threading.Lock()

    def note(self, path: str) -> None:
        """Start watching a directory the UI just read."""
        stamp = _mtime(path)
        with self._lock:
            self._seen[path] = stamp
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
            watching = list(self._seen.items())

        changed = []
        gone = []
        for path, before in watching:
            now = _mtime(path)
            if now == before:
                continue
            changed.append(path)
            if now is None:
                gone.append(path)

        if changed:
            with self._lock:
                for path in changed:
                    if path in self._seen:
                        self._seen[path] = _mtime(path)
                for path in gone:
                    self._seen.pop(path, None)
        return changed

    def __len__(self) -> int:
        with self._lock:
            return len(self._seen)


def _mtime(path: str) -> float | None:
    """A directory's modification time, or None when it is not there.

    st_mtime alone misses a file being replaced within the same second on
    filesystems with coarse timestamps, so the entry count rides along with it.
    """
    try:
        st = os.stat(path)
    except OSError:
        return None
    try:
        with os.scandir(path) as it:
            count = sum(1 for _ in it)
    except OSError:
        count = -1
    return st.st_mtime + count * 1e-6
