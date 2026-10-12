"""What is filling the disk.

The graph sizes a file by how long it is. Someone whose drive is full needs
something else: which folders the space went to. That includes the folders
the graph leaves out on purpose -- node_modules, .git, build output, caches --
because those are usually where it went. So this counts everything that is
really on the disk, the way `du -x` does:

  * blocks allocated, not the length a file claims, so a sparse file is small
    and a folder of ten thousand tiny files is as big as it really is;
  * a symlink is the link itself, never what it points at;
  * a file with several hard links is counted once, where it is first seen;
  * another filesystem mounted inside is shown, but not counted.

Sizing a big folder takes a while, and the window must not hang meanwhile. So
`get` answers within a budget with what it knows so far, and one background
thread keeps going: asking again later gets more, and in the end the whole
answer. Totals are remembered per directory, so sizing a parent after looking
at its children only reads what has not been seen yet, and a change costs
reading again the one folder it happened in and adding up the ones above it.
"""

from __future__ import annotations

import os
import stat
import threading
import time
from collections import OrderedDict

BUDGET = 0.4            # seconds `get` may wait for the walk before answering
WANTED_CAP = 64         # folders asked about and still unfinished; oldest dropped


def _bytes(st: os.stat_result) -> int:
    """Bytes the disk gave this entry, which is what freeing it would return."""
    blocks = getattr(st, "st_blocks", None)
    return blocks * 512 if blocks is not None else st.st_size


class _Dir:
    """One directory: its own listing, and once complete, its whole subtree."""

    __slots__ = ("ino", "mtime", "own", "files", "subs", "claimed", "deferred",
                 "total", "count", "complete", "stale", "cursor")

    def __init__(self, ino=None, mtime=None, own=0, files=0, subs=(),
                 claimed=(), deferred=()) -> None:
        self.ino = ino              # (ino, mtime) say whether the folder on
        self.mtime = mtime          # disk is still the one that was read
        self.own = own              # itself and the files directly in it
        self.files = files
        self.subs = subs            # names of subfolders on the same disk
        self.claimed = claimed      # hard-linked files counted here
        self.deferred = deferred    # hard-linked files counted somewhere else
        self.total = 0              # with everything beneath, once complete
        self.count = 0
        self.complete = False
        self.stale = False          # the listing itself must be read again
        self.cursor = 0             # subfolders before this one are complete


class DiskUsage:
    """Bytes on disk per folder, worked out in the background and remembered.

    Thread-safe: `get` is called from the server's request threads while the
    worker fills in totals. One lock covers every remembered directory; disk
    reads happen outside it.
    """

    def __init__(self, inside=None, budget: float = BUDGET) -> None:
        self.inside = inside
        self.budget = max(0.0, float(budget))
        self._nodes: dict[str, _Dir] = {}
        # A hard-linked file belongs to the first folder that counted it, and
        # every other folder holding a link counts it as nothing -- what `du`
        # does across one run, kept here across all of them, since totals are
        # reused from one question to the next. When the owner lets go (it
        # was re-read without the file, or it is gone), the folders that
        # deferred to it are read again, and one of them takes it over.
        self._owner: dict[tuple, str] = {}
        self._deferrers: dict[tuple, set] = {}
        self._wanted: OrderedDict[str, bool] = OrderedDict()
        self._lock = threading.Lock()
        self._cond = threading.Condition(self._lock)
        self._thread: threading.Thread | None = None
        self._stopped = False
        self._scanning: str | None = None   # the folder being read right now
        self._rescan = False                # it changed while being read

    # -- asking -------------------------------------------------------------

    def get(self, path: str) -> dict:
        """The size of a folder and of each thing directly in it.

        Waits up to the budget for the answer to be complete; when it is not,
        `complete` is False, the totals are what has been counted so far, and
        the counting goes on without anyone waiting for it.
        """
        if self.inside is not None and not self.inside(path):
            raise ValueError(f"outside the folder: {path}")
        key = os.path.normpath(path)
        try:
            st = os.lstat(key)
        except OSError:
            st = None
        if st is None or not stat.S_ISDIR(st.st_mode):
            return {"path": path, "bytes": 0, "files": 0, "complete": True,
                    "children": []}

        deadline = time.monotonic() + self.budget
        with self._cond:
            node = self._nodes.get(key)
            if node is not None and (node.ino, node.mtime) != (st.st_ino, st.st_mtime_ns):
                # something was added, removed or renamed in it since it was
                # read, and nobody said so: read it again
                self._invalidate(key)
            if not self._done(key) and not self._stopped:
                self._want(key)
                while not self._done(key) and not self._stopped:
                    left = deadline - time.monotonic()
                    if left <= 0:
                        break
                    self._cond.wait(left)

        entries = _listing(key)
        with self._lock:
            return self._answer(path, key, st, entries)

    def invalidate(self, path: str) -> None:
        """Something under `path` changed: its folder is read again, and the
        totals of every folder above it are added up again."""
        with self._lock:
            if self._scanning is not None:
                self._rescan = True     # it may have read the old contents
            self._invalidate(os.path.normpath(path))

    def stop(self) -> None:
        with self._cond:
            self._stopped = True
            self._cond.notify_all()
            thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2.0)

    # -- answering (with the lock held) --------------------------------------

    def _done(self, key: str) -> bool:
        node = self._nodes.get(key)
        return node is not None and node.complete

    def _answer(self, path: str, key: str, st: os.stat_result, entries) -> dict:
        node = self._nodes.get(key)
        own, files = _bytes(st), 0
        below, below_files = 0, 0
        seen: set = set()
        children = []
        for name, est in entries:
            child = os.path.join(key, name)
            if stat.S_ISDIR(est.st_mode):
                if est.st_dev != st.st_dev:     # a mount: listed, never counted
                    size, count, complete = 0, 0, True
                else:
                    size, count, complete = self._sized(child)
                below += size
                below_files += count
                children.append({"name": name, "path": child, "bytes": size,
                                 "dir": True, "complete": complete})
                continue
            size = _bytes(est)
            if est.st_nlink > 1:
                link = (est.st_dev, est.st_ino)
                owner = self._owner.get(link)
                if link in seen or (owner is not None and owner != key):
                    size = 0                    # counted once, elsewhere
                seen.add(link)
            own += size
            files += 1
            children.append({"name": name, "path": child, "bytes": size,
                             "dir": False, "complete": True})
        children.sort(key=lambda c: (-c["bytes"], c["name"]))
        if node is not None and node.complete:
            total, count, complete = node.total, node.count, True
        else:
            total, count, complete = own + below, files + below_files, False
        return {"path": path, "bytes": total, "files": count,
                "complete": complete, "children": children}

    def _sized(self, path: str) -> tuple[int, int, bool]:
        """(bytes, files, complete) for a folder; a lower bound until complete."""
        node = self._nodes.get(path)
        if node is None:
            return 0, 0, False
        if node.complete:
            return node.total, node.count, True
        total = count = 0
        todo = [path]
        while todo:
            p = todo.pop()
            node = self._nodes.get(p)
            if node is None:
                continue
            if node.complete:
                total += node.total
                count += node.count
                continue
            total += node.own
            count += node.files
            todo.extend(os.path.join(p, name) for name in node.subs)
        return total, count, False

    # -- forgetting (with the lock held) --------------------------------------

    def _invalidate(self, path: str) -> None:
        # A file, or a folder never read, lives in its parent's listing; go up
        # to the nearest folder that was read, and read that again.
        while path not in self._nodes:
            parent = os.path.dirname(path)
            if parent == path:
                return
            path = parent
        node = self._nodes[path]
        node.stale = True
        node.complete = False
        node.cursor = 0
        self._unsum_above(path)

    def _unsum_above(self, path: str) -> None:
        """Every folder above `path` has a total that included it."""
        while True:
            parent = os.path.dirname(path)
            if parent == path:
                return
            path = parent
            node = self._nodes.get(path)
            if node is not None:
                node.complete = False
                node.cursor = 0

    def _drop(self, path: str) -> None:
        """Forget a folder that is gone or was replaced, and all beneath it."""
        freed: set = set()
        todo = [path]
        while todo:
            p = todo.pop()
            node = self._nodes.pop(p, None)
            if node is None:
                continue
            freed |= self._release(p, node)
            todo.extend(os.path.join(p, name) for name in node.subs)
        self._unsum_above(path)
        self._reassign(freed)

    def _release(self, path: str, node: _Dir) -> set:
        """Let go of a folder's hard links. Returns the ones it owned."""
        for link in node.deferred:
            waiting = self._deferrers.get(link)
            if waiting is not None:
                waiting.discard(path)
                if not waiting:
                    del self._deferrers[link]
        freed = set()
        for link in node.claimed:
            if self._owner.get(link) == path:
                del self._owner[link]
                freed.add(link)
        return freed

    def _reassign(self, freed: set) -> None:
        for link in freed:
            if link not in self._owner:
                for path in self._deferrers.pop(link, ()):
                    self._invalidate(path)

    # -- the worker -----------------------------------------------------------

    def _want(self, key: str) -> None:
        self._wanted.pop(key, None)
        self._wanted[key] = True        # the newest question is answered first
        while len(self._wanted) > WANTED_CAP:
            self._wanted.popitem(last=False)
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name="cortex-du",
                                            daemon=True)
            self._thread.start()
        self._cond.notify_all()

    def _run(self) -> None:
        while True:
            with self._cond:
                while not self._wanted and not self._stopped:
                    self._cond.wait()   # idle: nothing is being asked about
                if self._stopped:
                    return
                target = next(reversed(self._wanted))
            self._walk(target)

    def _walk(self, target: str) -> None:
        """Size `target`, depth first, until it is complete or someone asks
        about another folder. Everything learned stays in the cache, so a walk
        that is set aside loses nothing when it is taken up again."""
        stack = [target]
        while stack:
            with self._cond:
                if self._stopped or next(reversed(self._wanted), None) != target:
                    return
                path = stack[-1]
                node = self._nodes.get(path)
                if node is not None and not node.stale:
                    if node.complete:
                        stack.pop()
                    else:
                        nxt = self._next_child(path, node)
                        if nxt is not None:
                            stack.append(nxt)
                        else:
                            self._finish(path, node)
                    continue
                self._scanning = path
                self._rescan = False
            found = _scan(path)
            with self._cond:
                self._install(path, found)
                if self._rescan:
                    self._invalidate(path)
                self._scanning = None
        with self._cond:
            self._wanted.pop(target, None)
            self._cond.notify_all()

    def _next_child(self, path: str, node: _Dir) -> str | None:
        while node.cursor < len(node.subs):
            child = os.path.join(path, node.subs[node.cursor])
            if not self._done(child):
                return child
            node.cursor += 1
        return None

    def _finish(self, path: str, node: _Dir) -> None:
        total, count = node.own, node.files
        for name in node.subs:
            child = self._nodes.get(os.path.join(path, name))
            if child is None or not child.complete:
                node.cursor = 0         # something moved under us; go round again
                return
            total += child.total
            count += child.count
        node.total, node.count, node.complete = total, count, True
        if path in self._wanted:
            self._cond.notify_all()

    def _install(self, path: str, found) -> None:
        """Put a fresh reading of one folder in place of the old one."""
        old = self._nodes.get(path)
        freed = self._release(path, old) if old is not None else set()
        if found is None:               # gone, or no longer a folder
            node = _Dir()
            node.complete = True
            self._nodes[path] = node
            for name in (old.subs if old is not None else ()):
                self._drop(os.path.join(path, name))
            self._reassign(freed)
            return

        ino, mtime, own, files, subs, links = found
        claimed, deferred, seen = [], [], set()
        for link, size in links:
            if link in seen:
                continue                # two names in one folder: one file
            seen.add(link)
            owner = self._owner.get(link)
            if owner is None or owner == path:
                self._owner[link] = path
                claimed.append(link)
                own += size
            else:
                deferred.append(link)
                self._deferrers.setdefault(link, set()).add(path)
        names = tuple(name for name, _, _ in subs)
        self._nodes[path] = _Dir(ino, mtime, own, files, names,
                                 tuple(claimed), tuple(deferred))

        # Subfolders read before: one that is gone or was replaced is
        # forgotten; one whose own listing changed is read again; the rest
        # keep their totals, which is what makes a re-read cheap.
        if old is not None:
            current = set(names)
            for name in old.subs:
                if name not in current:
                    self._drop(os.path.join(path, name))
        for name, sub_ino, sub_mtime in subs:
            child_path = os.path.join(path, name)
            child = self._nodes.get(child_path)
            if child is None:
                continue
            if child.ino != sub_ino:
                self._drop(child_path)
            elif child.mtime != sub_mtime and not child.stale:
                child.stale = True
                child.complete = False
                child.cursor = 0
        self._reassign(freed)


def _scan(path: str):
    """One folder's own listing: (ino, mtime, bytes, files, subfolders, links).

    Never raises. A folder that cannot be listed is the size of itself; an
    entry that cannot be looked at is left out. Hard-linked files are handed
    back separately, for the cache to decide which folder counts them.
    """
    try:
        st = os.lstat(path)
    except OSError:
        return None
    if not stat.S_ISDIR(st.st_mode):
        return None
    own, files, subs, links = _bytes(st), 0, [], []
    try:
        with os.scandir(path) as entries:
            for entry in entries:
                try:
                    est = entry.stat(follow_symlinks=False)
                except OSError:
                    continue
                if stat.S_ISDIR(est.st_mode):
                    # like du -x: another filesystem is not this one's space
                    if est.st_dev == st.st_dev:
                        subs.append((entry.name, est.st_ino, est.st_mtime_ns))
                    continue
                files += 1
                if est.st_nlink > 1:
                    links.append(((est.st_dev, est.st_ino), _bytes(est)))
                else:
                    own += _bytes(est)
    except OSError:
        pass                            # unreadable, or failed partway: keep what we got
    return st.st_ino, st.st_mtime_ns, own, files, subs, links


def _listing(path: str) -> list:
    """[(name, lstat)] for each entry directly in `path`; [] if unreadable."""
    out = []
    try:
        with os.scandir(path) as entries:
            for entry in entries:
                try:
                    out.append((entry.name, entry.stat(follow_symlinks=False)))
                except OSError:
                    continue
    except OSError:
        pass
    return out
