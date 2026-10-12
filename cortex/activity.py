"""Seeing the folder change while it changes.

`watch` answers "has anything in the folders I have open changed?" by
comparing directory timestamps. That is enough to refresh a listing, but not to
show what happened: a file edited in place does not touch its directory's
timestamp at all, and a timestamp that did move does not say which file moved
it. Watch mode needs more than that. It lights each file up as it is written,
leaving a fading trail, so someone can watch an agent, a build or a `git pull`
work its way through a project.

So this keeps an ordered feed of file-level changes -- created, modified,
deleted, moved -- that the page reads with a long poll: "everything after the
last number I saw, and wait a while if there is nothing yet".

On Linux the kernel says what changed (inotify), and an idle cortex sleeps
until it does. Elsewhere, or when the kernel will not give us inotify, the
root and the folders the page has opened are looked at every second and a
half instead: slower to notice, and blind to folders nobody has opened, but it
still sees an edit that leaves the directory untouched.

Either way only what the graph would show is reported -- nothing under
node_modules, nothing gitignored, none of cortex's own half-written saves --
and a burst of writes to one file is one event, not hundreds.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import errno
import os
import select
import struct
import sys
import threading
import time
from collections import OrderedDict, deque

CAP = 2000               # events held; a page further behind than this starts over
MAX_WATCHES = 4096       # inotify watches, given out shallowest folder first
COALESCE = 1.0           # seconds in which more writes to one file add nothing
LONGEST_WAIT = 30.0      # seconds a long poll may hold a request open
POLL_EVERY = 1.5         # seconds between looks, without inotify
STAT_BUDGET = 5000       # files stat-ed per look, so one huge folder cannot stall the rest
OPEN_FOLDERS = 600       # folders watched because the page opened them; the oldest go
NOTE_WAIT = 0.25         # seconds note_dir waits for the watcher to take a folder on
MOVE_PAIR = 0.05         # seconds the first half of a rename waits for its second
MEMO = 20000             # remembered verdicts on what the graph shows, before starting over

# cortex's own saves are written beside the file under this prefix and renamed
# over it; the half-written copy is not something that happened to the folder
PRIVATE = ".cortex-"

# What a later event for the same path is swallowed by, when it comes within
# COALESCE of the last one reported. A write just after a create is part of the
# create; one creation seen twice -- by its own event and by the look into a
# folder that has just appeared -- is one creation. A move is never swallowed,
# and neither is anything that follows a delete.
SWALLOWED_BY = {
    "modified": ("created", "modified", "moved"),
    "created": ("created",),
    "deleted": ("deleted",),
    "overflow": ("overflow",),
    "moved": (),
}

# from <sys/inotify.h>
IN_MODIFY = 0x00000002
IN_CLOSE_WRITE = 0x00000008
IN_MOVED_FROM = 0x00000040
IN_MOVED_TO = 0x00000080
IN_CREATE = 0x00000100
IN_DELETE = 0x00000200
IN_DELETE_SELF = 0x00000400
IN_MOVE_SELF = 0x00000800
IN_Q_OVERFLOW = 0x00004000
IN_IGNORED = 0x00008000
IN_ONLYDIR = 0x01000000
IN_DONT_FOLLOW = 0x02000000
IN_EXCL_UNLINK = 0x04000000
IN_ISDIR = 0x40000000

WATCH_MASK = (IN_MODIFY | IN_CLOSE_WRITE | IN_MOVED_FROM | IN_MOVED_TO
              | IN_CREATE | IN_DELETE | IN_DELETE_SELF | IN_MOVE_SELF
              | IN_ONLYDIR | IN_DONT_FOLLOW | IN_EXCL_UNLINK)

HEADER = struct.Struct("iIII")   # wd, mask, cookie, len; the name follows
READ_SIZE = 64 * 1024


class Activity:
    """An ordered feed of changes to the files under `scanner.root`.

    `since` may be called from any number of threads at once. Everything else
    about watching happens on one background thread, which is why `note_dir`
    hands its folder over rather than watching it itself. `ready` is set once
    the first full look has been taken; changes made before that may go
    unreported. A stopped Activity stays stopped.
    """

    def __init__(self, scanner, cap: int = CAP, max_watches: int = MAX_WATCHES,
                 engine: str | None = None) -> None:
        if engine not in (None, "inotify", "poll"):
            raise ValueError(f"no such engine: {engine!r}")
        self.scanner = scanner
        self.root: str = scanner.root
        self.max_watches = max_watches
        self.ready = threading.Event()
        self._events: deque[dict] = deque(maxlen=max(1, cap))
        self._seq = 0
        self._recent: OrderedDict[str, tuple] = OrderedDict()
        self._changed = threading.Condition()
        self._notes: deque = deque()
        self._stopping = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._dirs: dict[str, bool] = {}
        self._files: dict[str, bool] = {}

        self._watcher = None
        if engine == "inotify" or (engine is None and sys.platform.startswith("linux")):
            try:
                self._watcher = _Inotify(self)
            except (OSError, AttributeError):
                # no inotify in this libc, or none left to give: every user has
                # a small limit on instances, and other programs use them up
                self._watcher = None
        if self._watcher is None:
            self._watcher = _Poll(self)
        self.engine: str = self._watcher.name

    # -- the public side ------------------------------------------------------

    def start(self) -> None:
        with self._lock:
            if self._thread is not None or self._stopping.is_set():
                return
            self._thread = threading.Thread(target=self._run, daemon=True,
                                            name="cortex-activity")
            self._thread.start()

    def stop(self) -> None:
        with self._lock:
            if self._stopping.is_set():
                return
            self._stopping.set()
            thread = self._thread
        self._watcher.wake()
        with self._changed:
            self._changed.notify_all()     # nobody should sit out a long poll for nothing
        if thread is None:
            self._watcher.close()
        elif thread is not threading.current_thread():
            thread.join(timeout=1.0)

    def note_dir(self, path: str) -> None:
        """The page just listed this folder, so make sure it is being watched."""
        if not isinstance(path, str) or self._stopping.is_set():
            return
        done = threading.Event()
        self._notes.append((os.path.normpath(path), done))
        self._watcher.wake()
        thread = self._thread
        # Usually the folder is taken on within a millisecond, and waiting for
        # that means a change made right after the listing is not missed. In
        # the middle of a flood of events it can take longer, and the page's
        # request should not be held up for it.
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            done.wait(NOTE_WAIT)

    def since(self, seq: int, wait: float = 0.0) -> dict:
        """Events after `seq`, oldest first, waiting up to `wait` seconds for one.

        `dropped` means the caller is too far behind (or ahead, from before a
        restart) for the events it missed to be told, and should refresh.
        """
        seq = max(0, int(seq))
        wait = float(wait or 0)
        wait = min(wait, LONGEST_WAIT) if wait > 0 else 0.0     # NaN is not > 0
        deadline = time.monotonic() + wait
        with self._changed:
            while self._seq == seq and not self._stopping.is_set():
                left = deadline - time.monotonic()
                if left <= 0:
                    break
                self._changed.wait(left)
            held = self._events
            oldest = held[0]["seq"] if held else self._seq + 1
            events = [dict(e) for e in held if e["seq"] > seq] if seq < self._seq else []
            return {"seq": self._seq, "events": events, "engine": self.engine,
                    "dropped": seq + 1 < oldest or seq > self._seq}

    # -- for the watcher thread -----------------------------------------------

    def _run(self) -> None:
        try:
            self._watcher.run()
        finally:
            self.ready.set()
            # closed here rather than in stop(): if this thread is stuck on a
            # slow disk, a descriptor closed under it could be handed to
            # something else by the time it reads, and read as if it were ours
            self._watcher.close()

    def _report(self, kind: str, path: str, is_dir: bool, origin: str | None = None) -> None:
        now = time.monotonic()
        with self._changed:
            last = self._recent.get(path)
            if last is not None and now - last[0] < COALESCE and last[1] in SWALLOWED_BY[kind]:
                return
            self._recent[path] = (now, kind)
            self._recent.move_to_end(path)
            while self._recent:
                when, _ = next(iter(self._recent.values()))
                if now - when < COALESCE:
                    break
                self._recent.popitem(last=False)
            self._seq += 1
            event = {"seq": self._seq, "t": time.time(), "kind": kind,
                     "path": path, "dir": is_dir}
            if origin is not None:
                event["from"] = origin
            self._events.append(event)
            self._changed.notify_all()

    def _take_notes(self) -> None:
        while self._notes:
            try:
                folder, done = self._notes.popleft()
            except IndexError:
                return
            try:
                if self._noteworthy(folder):
                    self._watcher.note(folder)
            finally:
                done.set()

    def _noteworthy(self, folder: str) -> bool:
        # The feed names folders by the path the graph knows them by. One
        # reached through a symlink has another name too, and inotify would
        # hand back the watch it already has under that other name. isdir()
        # goes first because it shrugs off a name no folder could have, one
        # with a NUL in it, where reading its .gitignore would raise.
        return (os.path.isabs(folder) and os.path.isdir(folder)
                and os.path.realpath(folder) == folder and self._dir_shown(folder))

    # -- what the graph shows ---------------------------------------------------

    def _shown(self, path: str, is_dir: bool) -> bool:
        if is_dir:
            return self._dir_shown(path)
        parent, name = os.path.split(path)
        if not self._dir_shown(parent):
            return False
        verdict = self._files.get(path)
        if verdict is None:
            verdict = not (name.startswith(PRIVATE)
                           or self.scanner.skip(name, False)
                           or self.scanner.gitignored(path, False))
            if len(self._files) >= MEMO:
                self._files.clear()
            self._files[path] = verdict
        return verdict

    def _dir_shown(self, path: str) -> bool:
        """Whether every folder from the root down to `path` is in the graph.

        Remembered per folder, because a burst of events lands in a handful of
        folders, and asking .gitignore about each of them each time adds up.
        """
        undecided = []
        folder = path
        while True:
            verdict = self._dirs.get(folder)
            if verdict is not None:
                break
            if folder == self.root:
                verdict = True
                break
            if not _under(folder, self.root):
                verdict = False
                break
            undecided.append(folder)
            folder = os.path.dirname(folder)
        if len(self._dirs) + len(undecided) > MEMO:
            self._dirs.clear()
        for folder in reversed(undecided):
            if verdict:
                name = os.path.basename(folder)
                verdict = not (name.startswith(PRIVATE)
                               or self.scanner.skip(name, True)
                               or self.scanner.gitignored(folder, True))
            self._dirs[folder] = verdict
        return verdict

    def _ignores_changed(self) -> None:
        """A .gitignore was touched, so every verdict may be wrong now."""
        self._dirs.clear()
        self._files.clear()
        self.scanner.forget_ignores()


# -- inotify ----------------------------------------------------------------

_LIBC = None


def _libc():
    global _LIBC
    if _LIBC is None:
        libc = ctypes.CDLL(ctypes.util.find_library("c") or "libc.so.6", use_errno=True)
        libc.inotify_init1.argtypes = [ctypes.c_int]
        libc.inotify_init1.restype = ctypes.c_int
        libc.inotify_add_watch.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_uint32]
        libc.inotify_add_watch.restype = ctypes.c_int
        libc.inotify_rm_watch.argtypes = [ctypes.c_int, ctypes.c_int]
        libc.inotify_rm_watch.restype = ctypes.c_int
        _LIBC = libc
    return _LIBC


class _Inotify:
    """Changes as the Linux kernel reports them.

    One watch per folder, not per file, and each folder's watch says which
    entry in it changed. The kernel names folders by the descriptor it gave
    out, so this keeps the path each one stands for, and moves those paths
    along when a watched folder is renamed: otherwise a folder moved out of
    the root would go on reporting its changes under its old name.
    """

    name = "inotify"

    def __init__(self, feed: Activity) -> None:
        libc = _libc()
        self._add = libc.inotify_add_watch
        self._rm = libc.inotify_rm_watch
        fd = libc.inotify_init1(os.O_NONBLOCK | os.O_CLOEXEC)
        if fd < 0:
            code = ctypes.get_errno()
            raise OSError(code, os.strerror(code))
        try:
            # a pipe to wake the thread with, so it can sleep without a
            # timeout and still stop at once
            self._wake_r, self._wake_w = os.pipe()
        except OSError:
            os.close(fd)
            raise
        os.set_blocking(self._wake_r, False)
        os.set_blocking(self._wake_w, False)
        self.fd = fd
        self.feed = feed
        self.root = feed.root
        self._paths: dict[int, str] = {}                # watch -> folder
        self._wds: dict[str, int] = {}                  # folder -> watch
        self._noted: OrderedDict[str, int] = OrderedDict()   # past the cap, for the page
        self._moves: OrderedDict[int, tuple] = OrderedDict()  # cookie -> first half
        self._full = False
        self._closed = False
        self._fd_lock = threading.Lock()

    def run(self) -> None:
        feed = self.feed
        self._walk(self.root, announce=False)
        feed.ready.set()
        poller = select.poll()
        poller.register(self.fd, select.POLLIN)
        poller.register(self._wake_r, select.POLLIN)
        while not feed._stopping.is_set():
            # With a rename half-told, wait only briefly for the rest of it;
            # otherwise there is nothing to do until the kernel or the page
            # says so, and no reason to wake up.
            poller.poll(int(MOVE_PAIR * 1000) if self._moves else None)
            if feed._stopping.is_set():
                break
            try:
                os.read(self._wake_r, 4096)
            except OSError:
                pass
            feed._take_notes()
            self._read()
            self._settle_moves(time.monotonic())

    def wake(self) -> None:
        with self._fd_lock:
            if self._closed:
                return
            try:
                os.write(self._wake_w, b"\0")
            except OSError:
                pass                    # already full of wake-ups, which will do

    def close(self) -> None:
        with self._fd_lock:
            if self._closed:
                return
            self._closed = True
            for fd in (self.fd, self._wake_r, self._wake_w):
                try:
                    os.close(fd)        # closing the instance drops every watch
                except OSError:
                    pass
        self._paths.clear()
        self._wds.clear()
        self._noted.clear()

    def note(self, folder: str) -> None:
        if folder in self._wds:
            if folder in self._noted:
                self._noted.move_to_end(folder)
            return
        # the page is looking at it, so it is watched even past the cap; the
        # folders watched that way are few, and the oldest are let go
        if not self._watch(folder):
            self._watch(folder, noted=True)

    # -- reading ---------------------------------------------------------------

    def _read(self) -> None:
        # one buffer per wake-up: under a flood, stop() and note_dir still get
        # a turn between buffers
        try:
            data = os.read(self.fd, READ_SIZE)
        except OSError:
            return                      # nothing waiting after all
        at = 0
        while at + HEADER.size <= len(data):
            wd, mask, cookie, length = HEADER.unpack_from(data, at)
            start = at + HEADER.size
            at = start + length
            self._event(wd, mask, cookie, os.fsdecode(data[start:at].rstrip(b"\0")))

    def _event(self, wd: int, mask: int, cookie: int, name: str) -> None:
        feed = self.feed
        if mask & IN_Q_OVERFLOW:
            feed._report("overflow", self.root, True)
            self._settle_moves(None)            # their other halves may be lost
            self._walk(self.root, announce=False)   # folders made while we were deaf
            return
        if mask & IN_IGNORED:
            self._forget(wd)
            return
        folder = self._paths.get(wd)
        if folder is None:
            return                              # from a watch already let go
        if not name:
            if mask & (IN_DELETE_SELF | IN_MOVE_SELF):
                self._lost(folder)
            return

        path = os.path.join(folder, name)
        is_dir = bool(mask & IN_ISDIR)
        if name == ".gitignore":
            feed._ignores_changed()
        if self._moves and not mask & IN_MOVED_TO:
            self._settle_path(path)
        if mask & IN_MOVED_TO:
            first = self._moves.pop(cookie, None)
            self._moved(first[0] if first else None, path, is_dir)
        elif mask & IN_MOVED_FROM:
            self._moves[cookie] = (path, is_dir, time.monotonic())
        elif mask & IN_CREATE:
            if feed._shown(path, is_dir):
                feed._report("created", path, is_dir)
                if is_dir:
                    self._walk(path, announce=True)
        elif mask & IN_DELETE:
            if is_dir:
                # a folder must be empty to be removed, so only its own watch
                # is left; let it go now, in case a new folder takes its name
                # before the kernel gets round to saying the old one is gone
                self._release(path)
            if feed._shown(path, is_dir):
                feed._report("deleted", path, is_dir)
        elif mask & (IN_MODIFY | IN_CLOSE_WRITE) and not is_dir:
            if feed._shown(path, False):
                feed._report("modified", path, False)

    def _moved(self, src: str | None, dst: str | None, is_dir: bool) -> None:
        """One rename, with either end missing when it was outside our watch."""
        feed = self.feed
        was = src is not None and feed._shown(src, is_dir)
        now = dst is not None and feed._shown(dst, is_dir)
        if was and now:
            feed._report("moved", dst, is_dir, src)
        elif was:
            feed._report("deleted", src, is_dir)
        elif now:
            # From outside the graph. If it landed on an existing file -- the
            # usual way to save safely is to write a hidden copy and rename it
            # over the original -- the kernel does not say so, and this
            # "created" is really an edit.
            feed._report("created", dst, is_dir)
        if not is_dir:
            return
        if src is not None:
            if now:
                self._rehome(src, dst)
            else:
                self._unwatch(src)
        if now and dst not in self._wds:
            self._walk(dst, announce=not was)

    def _lost(self, folder: str) -> None:
        """A watched folder says it was deleted or moved away."""
        for cookie, (path, _, _) in list(self._moves.items()):
            if path == folder:
                # its parent saw it leave, and nothing we watch saw it arrive
                del self._moves[cookie]
                self._moved(folder, None, True)
                return
        if folder != self.root and os.path.dirname(folder) in self._wds:
            return                              # its parent has told us already
        self._unwatch(folder)
        if self.feed._shown(folder, True):
            self.feed._report("deleted", folder, True)

    def _settle_moves(self, now: float | None) -> None:
        """First halves of renames that have waited long enough: they left."""
        while self._moves:
            cookie, (path, is_dir, when) = next(iter(self._moves.items()))
            if now is not None and now - when < MOVE_PAIR:
                return
            del self._moves[cookie]
            self._moved(path, None, is_dir)

    def _settle_path(self, path: str) -> None:
        # something new happened at a path that is half-way through leaving;
        # its leaving has to be told first, or the feed ends with it gone
        for cookie, (src, is_dir, _) in list(self._moves.items()):
            if src == path:
                del self._moves[cookie]
                self._moved(src, None, is_dir)

    # -- the watches -------------------------------------------------------------

    def _walk(self, top: str, announce: bool) -> None:
        """Watch `top` and the folders under it, shallowest first.

        Each folder is watched before it is listed, so whatever is made in it
        afterwards the kernel reports, and whatever was made before, the
        listing finds. When `announce` is set the listing is reported too:
        a folder that has just appeared was filled before we could watch it.
        """
        feed = self.feed
        queue = deque([top])
        while queue and not feed._stopping.is_set():
            folder = queue.popleft()
            if not self._watch(folder):
                if not self._capped():
                    continue                    # unreadable, or gone already
                if announce:
                    feed._report("overflow", self.root, True)
                return
            if feed._notes:
                feed._take_notes()
            try:
                with os.scandir(folder) as it:
                    entries = [(e.name, _is_dir(e)) for e in it]
            except OSError:
                continue
            for name, is_dir in entries:
                path = os.path.join(folder, name)
                if not feed._shown(path, is_dir):
                    continue
                if announce:
                    feed._report("created", path, is_dir)
                if is_dir:
                    queue.append(path)

    def _capped(self) -> bool:
        return self._full or len(self._wds) - len(self._noted) >= self.feed.max_watches

    def _watch(self, folder: str, noted: bool = False) -> bool:
        if folder in self._wds:
            return True
        if not noted and self._capped():
            return False
        wd = self._add(self.fd, os.fsencode(folder), WATCH_MASK)
        if wd < 0:
            if ctypes.get_errno() == errno.ENOSPC:
                self._full = True               # the kernel's own limit on watches
            return False                        # EACCES, or gone: nothing to watch
        old = self._paths.get(wd)
        if old is not None and old != folder:
            # the kernel already watches this folder under its old name: it
            # was moved somewhere we were not looking, and has come back
            self._wds.pop(old, None)
            self._noted.pop(old, None)
        self._paths[wd] = folder
        self._wds[folder] = wd
        if noted:
            self._noted[folder] = wd
            while len(self._noted) > OPEN_FOLDERS:
                self._release(next(iter(self._noted)))
        return True

    def _release(self, folder: str) -> None:
        wd = self._wds.pop(folder, None)
        self._noted.pop(folder, None)
        if wd is not None:
            self._paths.pop(wd, None)
            self._rm(self.fd, wd)

    def _unwatch(self, top: str) -> None:
        for folder in [f for f in self._wds if _under(f, top)]:
            self._release(folder)

    def _rehome(self, src: str, dst: str) -> None:
        for folder in [f for f in self._wds if _under(f, src)]:
            wd = self._wds.pop(folder)
            moved = dst + folder[len(src):]
            self._wds[moved] = wd
            self._paths[wd] = moved
            if folder in self._noted:
                self._noted[moved] = self._noted.pop(folder)

    def _forget(self, wd: int) -> None:
        """The kernel let a watch go: the folder is gone, or we asked."""
        folder = self._paths.pop(wd, None)
        if folder is not None and self._wds.get(folder) == wd:
            del self._wds[folder]
            self._noted.pop(folder, None)
        self._full = False


# -- polling ----------------------------------------------------------------

class _Listing:
    """One folder as last seen: name -> (is_dir, (mtime_ns, size, inode) or None)."""

    __slots__ = ("path", "entries", "fresh", "announce", "turn", "ignores")

    def __init__(self, path: str, announce: bool = False) -> None:
        self.path = path
        self.entries: dict[str, tuple] = {}
        self.fresh = True           # not looked at yet
        self.announce = announce    # report what the first look finds, as new
        self.turn = 0               # where the next look starts stat-ing a big folder
        self.ignores = None         # the stamp of its .gitignore


class _Poll:
    """Changes found by looking, where the kernel will not say.

    The root and each folder the page has opened are listed, and their files
    stat-ed, every POLL_EVERY seconds. A file's (mtime, size, inode) is what
    gives away an edit: the inode for a file replaced by renaming another over
    it, the size for a write landing in the same tick of a coarse clock. There
    is no telling a rename from a delete and a create here, so it is reported
    as those two.
    """

    name = "poll"

    def __init__(self, feed: Activity) -> None:
        self.feed = feed
        self.root = feed.root
        self._root = _Listing(feed.root)
        self._root_gone = False
        self._folders: OrderedDict[str, _Listing] = OrderedDict()
        self._turn = 0
        self._wake = threading.Event()

    def run(self) -> None:
        feed = self.feed
        self._look(self._root, STAT_BUDGET, None)
        feed.ready.set()
        due = time.monotonic() + POLL_EVERY
        while not feed._stopping.is_set():
            self._wake.wait(max(0.0, due - time.monotonic()))
            self._wake.clear()
            if feed._stopping.is_set():
                break
            feed._take_notes()
            if time.monotonic() >= due:
                self._tick()
                due = time.monotonic() + POLL_EVERY

    def wake(self) -> None:
        self._wake.set()

    def close(self) -> None:
        self._folders.clear()

    def note(self, folder: str) -> None:
        if folder == self.root:
            return
        if folder in self._folders:
            self._folders.move_to_end(folder)
            return
        # the first look is the baseline, what the page has just been shown
        self._look(self._adopt(folder, announce=False), STAT_BUDGET, None)

    def _adopt(self, folder: str, announce: bool) -> _Listing:
        listing = _Listing(folder, announce)
        self._folders[folder] = listing
        while len(self._folders) > OPEN_FOLDERS:
            self._folders.popitem(last=False)
        return listing

    def _tick(self) -> None:
        # Round the folders in turn, from wherever the last tick ran out of
        # budget, so a few huge folders cannot keep the rest from being seen.
        order = [self._root] + list(self._folders.values())
        start = self._turn % len(order)
        budget = STAT_BUDGET
        for i in range(len(order)):
            if budget <= 0 or self.feed._stopping.is_set():
                return
            self._turn = start + i + 1
            queue = deque([order[(start + i) % len(order)]])
            while queue and budget > 0:
                budget -= self._look(queue.popleft(), budget, queue)

    def _look(self, listing: _Listing, allowance: int, found: deque | None) -> int:
        """Compare a folder with how it was; the number of stats it took.

        New folders found are adopted and put on `found`, to be looked into
        straight away, since whatever is in them is new too.
        """
        feed = self.feed
        folder = listing.path
        seen: dict[str, tuple] = {}
        ignores = None
        cost = 1
        try:
            with os.scandir(folder) as it:
                for e in it:
                    if e.name == ".gitignore":
                        ignores = _stamp(e)
                        cost += 1
                    is_dir = _is_dir(e)
                    old = listing.entries.get(e.name)
                    if old is not None and old[0] == is_dir:
                        seen[e.name] = old
                    elif feed._shown(os.path.join(folder, e.name), is_dir):
                        seen[e.name] = (is_dir, None)
        except (FileNotFoundError, NotADirectoryError):
            self._vanished(listing)
            return cost
        except OSError:
            return cost                         # unreadable for now; next time

        if ignores != listing.ignores:
            if not listing.fresh:
                feed._ignores_changed()
            listing.ignores = ignores
        if folder == self.root:
            self._root_gone = False

        if not (listing.fresh and not listing.announce):
            before = listing.entries
            for name, (is_dir, _) in before.items():
                now = seen.get(name)
                if now is None or now[0] != is_dir:
                    feed._report("deleted", os.path.join(folder, name), is_dir)
            for name, (is_dir, _) in seen.items():
                old = before.get(name)
                if old is None or old[0] != is_dir:
                    path = os.path.join(folder, name)
                    feed._report("created", path, is_dir)
                    if is_dir and found is not None:
                        found.append(self._adopt(path, announce=True))

        for name in self._to_stat(listing, seen, max(1, allowance)):
            path = os.path.join(folder, name)
            try:
                st = os.lstat(path)
            except OSError:
                continue                        # gone since the listing; next time
            cost += 1
            stamp = (st.st_mtime_ns, st.st_size, st.st_ino)
            old = seen[name][1]
            if old is not None and old != stamp:
                feed._report("modified", path, False)
            seen[name] = (False, stamp)

        listing.entries = seen
        listing.fresh = False
        return cost

    def _to_stat(self, listing: _Listing, seen: dict, room: int) -> list:
        """The files to stat this time: all of them, if the budget allows.

        Otherwise files never stat-ed come first, since until they are their
        edits cannot be told, then the next slice of the rest, carrying on
        from where the last look stopped.
        """
        files = [n for n, (is_dir, _) in seen.items() if not is_dir]
        if len(files) <= room:
            return files
        unknown = [n for n in files if seen[n][1] is None]
        if len(unknown) >= room:
            return unknown[:room]
        known = [n for n in files if seen[n][1] is not None]
        take = room - len(unknown)
        at = listing.turn % len(known)
        listing.turn = at + take
        return unknown + (known[at:] + known[:at])[:take]

    def _vanished(self, listing: _Listing) -> None:
        feed = self.feed
        folder = listing.path
        if folder == self.root:
            if not self._root_gone:
                self._root_gone = True
                feed._report("deleted", folder, True)
            listing.entries = {}
            return
        self._folders.pop(folder, None)
        parent = os.path.dirname(folder)
        if parent != self.root and parent not in self._folders:
            # nothing else is looking at where it was, so this is the only
            # word of it there will be
            feed._report("deleted", folder, True)


# -- helpers ----------------------------------------------------------------

def _under(path: str, top: str) -> bool:
    return path == top or path.startswith(top.rstrip(os.sep) + os.sep)


def _is_dir(entry: os.DirEntry) -> bool:
    try:
        return entry.is_dir(follow_symlinks=False)
    except OSError:
        return False


def _stamp(entry: os.DirEntry) -> tuple | None:
    try:
        st = entry.stat(follow_symlinks=False)
    except OSError:
        return None
    return (st.st_mtime_ns, st.st_size, st.st_ino)
