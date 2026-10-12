"""Changing files, from the window.

cortex began as a reader, and reading is still all it does until someone asks
for more: nothing here runs unless they press save, or pick new, rename or
trash. When they do, the reader's rule still holds -- every path is proved to
be inside the mapped folder before it is touched, which the server does before
calling in here -- and two more are added:

  * A save never lands on top of a change it did not see. The page sends back
    the version it loaded, and if the disk has moved on since, the save is
    refused and the page asks what to do.
  * Nothing is ever deleted. "Delete" moves to the desktop's trash, where it
    can be put back.
"""

from __future__ import annotations

import codecs
import os
import secrets
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unicodedata
from urllib.parse import quote

EDIT_LIMIT = 2 * 1024 * 1024     # bytes; a textarea is not the tool beyond this
NAME_MAX = 255                   # bytes, the limit nearly every filesystem has

# Created by "new note" when the name given has no extension: a note is what
# someone making a file from a graph of notes almost always means.
DEFAULT_EXT = ".md"

# One save at a time, and something for shutdown to wait on: a write cut off by
# the process exiting is the one way a save could leave a file torn.
WRITE_LOCK = threading.Lock()

ENDINGS = {"\r\n": "crlf", "\r": "cr", "\n": "lf"}


class FileError(Exception):
    """A refusal the page should show as-is. `extra` rides along in the reply."""

    def __init__(self, message: str, status: int = 400, **extra) -> None:
        super().__init__(message)
        self.message = message
        self.status = status
        self.extra = extra

    def reply(self) -> dict:
        return {"ok": False, "error": self.message, **self.extra}


# -- versions ---------------------------------------------------------------

def _stamp(st: os.stat_result) -> str:
    """What the page hands back to prove it saw the latest copy.

    A string, because nanosecond timestamps do not survive a JavaScript number.
    The inode changes when a file is replaced wholesale -- how most editors,
    rsync and tar write -- even when they put the old timestamp back; the size
    catches what lands in the same tick of a coarse clock. The change time is
    left out on purpose: renaming a file changes it, and a draft should not
    fall out of date because cortex itself renamed the file under it.
    """
    return f"{st.st_ino}:{st.st_mtime_ns}:{st.st_size}"


def version(path: str) -> str | None:
    try:
        return _stamp(os.stat(path))
    except OSError:
        return None


# -- reading for editing ------------------------------------------------------

def _decode(raw: bytes) -> tuple[str, bool]:
    """(text, had_bom), refusing anything that would not survive a save."""
    if b"\x00" in raw[:8192]:
        raise FileError("this is a binary file, so it cannot be edited here")
    bom = raw.startswith(codecs.BOM_UTF8)
    try:
        return raw[len(codecs.BOM_UTF8) if bom else 0:].decode("utf-8"), bom
    except UnicodeDecodeError:
        # the reader falls back to latin-1 to show such a file, but writing it
        # back as UTF-8 would quietly rewrite every accented character in it
        raise FileError("this file is not UTF-8 text, so saving it here "
                        "could change it; use your editor") from None


def _endings(text: str) -> tuple[str, bool]:
    """(the line ending a file mostly uses, whether it uses more than one)."""
    crlf = text.count("\r\n")
    counts = {"\r\n": crlf, "\r": text.count("\r") - crlf,
              "\n": text.count("\n") - crlf}
    used = [e for e, n in counts.items() if n]
    # ties, and a file with no line breaks at all, go to \n
    eol = max(counts, key=lambda e: (counts[e], e == "\n"))
    return eol, len(used) > 1


def _lf(text: str) -> str:
    # A textarea turns every \r\n and every lone \r into \n as it is read
    # back, so that is the form the editor is given; anything else would look
    # edited the moment it opened.
    return text.replace("\r\n", "\n").replace("\r", "\n")


def read_source(path: str) -> dict:
    """The whole file, as text the page can edit and send back."""
    if not os.path.isfile(path):
        raise FileError("not a file")
    try:
        with open(path, "rb") as fh:
            # the version is taken before the read: a write landing mid-read
            # then shows up as a conflict at save time, rather than as a
            # version that vouches for text it does not match
            st = os.fstat(fh.fileno())
            if st.st_size > EDIT_LIMIT:
                raise FileError("too large to edit here (over 2 MB); use your editor")
            raw = fh.read(EDIT_LIMIT + 1)
    except OSError as exc:
        raise FileError(f"could not read it: {exc.strerror or exc}") from None
    if len(raw) > EDIT_LIMIT:
        raise FileError("too large to edit here (over 2 MB); use your editor")
    text, bom = _decode(raw)
    eol, mixed = _endings(text)
    return {"text": _lf(text), "version": _stamp(st), "eol": ENDINGS[eol],
            "mixed": mixed, "bom": bom, "writable": os.access(path, os.W_OK)}


# -- writing ----------------------------------------------------------------

def save(path: str, text, base: str | None = None, force: bool = False) -> dict:
    """Write `text` over `path`, unless the file changed since `base`."""
    if not isinstance(text, str):
        raise FileError("nothing to save")
    with WRITE_LOCK:
        exists = os.path.lexists(path)
        if exists and not os.path.isfile(path):
            raise FileError("not a file")
        if not exists and not force:
            raise FileError("the file is gone from the disk", 409, gone=True)

        st = None
        eol, bom = "\n", False
        if exists:
            try:
                st = os.stat(path)
            except OSError as exc:
                raise FileError(f"could not read it: {exc.strerror or exc}") from None
            if not force and base != _stamp(st):
                raise FileError("it changed on disk since you opened it", 409,
                                conflict=True, version=_stamp(st))
            if not os.access(path, os.W_OK):
                raise FileError("it is read-only on disk now, so it was not saved")
            try:
                with open(path, "rb") as fh:
                    head = fh.read(EDIT_LIMIT + 1)
                bom = head.startswith(codecs.BOM_UTF8)
                eol = _endings(head.decode("utf-8", "replace"))[0]
            except OSError:
                pass

        body = _lf(text)
        if eol != "\n":
            body = body.replace("\n", eol)
        data = (codecs.BOM_UTF8 if bom else b"") + body.encode("utf-8")
        if len(data) > EDIT_LIMIT * 2:
            raise FileError("too large to save from here")

        try:
            if exists:
                _write_over(path, data, st)
            else:
                _create(path, data)
        except OSError as exc:
            raise FileError(f"could not save: {exc.strerror or exc}") from None
        return {"ok": True, "version": version(path), "size": len(data)}


def _write_over(path: str, data: bytes, st: os.stat_result) -> None:
    """Swap in a new copy when that keeps the file what it was; else write in place.

    A swap -- write beside, rename over -- means a crash or a full disk
    mid-save leaves the old file whole. But the copy is a new file, and some
    things belong to the old one: other hard links to it, an owner that is not
    us, and extended attributes, which hold ACLs, SELinux labels and macOS
    Finder tags. When any of those would be lost, or cannot be known (macOS
    has no way to list attributes from the standard library), the file is
    written in place, as most editors write: it stays the same file, at the
    cost of the old contents not surviving a crash mid-write.
    """
    if (st.st_nlink == 1 and hasattr(os, "listxattr")
            and hasattr(os, "getuid") and st.st_uid == os.getuid()):
        try:
            fd, tmp = tempfile.mkstemp(prefix=".cortex-", suffix=".save",
                                       dir=os.path.dirname(path))
        except OSError:
            fd = None                    # cannot write beside it; in place, then
        if fd is not None:
            try:
                with os.fdopen(fd, "wb") as fh:
                    fh.write(data)
                    fh.flush()
                    os.fsync(fh.fileno())
            except BaseException:
                _unlink(tmp)
                raise                    # the old file is untouched; say why
            if _carry_over(path, tmp, st):
                try:
                    os.replace(tmp, path)
                    return
                except BaseException:
                    _unlink(tmp)
                    raise
            _unlink(tmp)
    with open(path, "r+b") as fh:
        fh.write(data)
        fh.truncate()
        fh.flush()
        os.fsync(fh.fileno())


def _carry_over(src: str, dst: str, st: os.stat_result) -> bool:
    """Give the new copy everything the old file had. False if that failed."""
    try:
        os.chmod(dst, stat.S_IMODE(st.st_mode))
        if os.stat(dst).st_gid != st.st_gid:
            os.chown(dst, -1, st.st_gid)
        for name in os.listxattr(src):
            os.setxattr(dst, name, os.getxattr(src, name))
        return True
    except OSError:
        return False


def _unlink(path: str) -> None:
    try:
        os.unlink(path)
    except OSError:
        pass


def _create(path: str, data: bytes) -> None:
    # O_EXCL: never clobber something that appeared in the meantime, and never
    # follow a symlink someone left at this name
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o666)
    with os.fdopen(fd, "wb") as fh:
        fh.write(data)


def quiesce(timeout: float = 10.0) -> None:
    """Let a save in progress finish, and start no more. For shutdown."""
    WRITE_LOCK.acquire(timeout=timeout)


# -- names ------------------------------------------------------------------

def clean_name(name) -> str:
    """A single path component, or a refusal saying what is wrong with it."""
    if not isinstance(name, str):
        raise FileError("give it a name")
    name = name.strip()
    if not name or name in (".", ".."):
        raise FileError("give it a name")
    if "/" in name or "\x00" in name:
        raise FileError("a name cannot contain /")
    if len(name.encode("utf-8", "replace")) > NAME_MAX:
        raise FileError("that name is too long")
    return name


def create(folder: str, name, kind: str = "file") -> dict:
    """A new, empty note (or file) or folder inside `folder`."""
    if not os.path.isdir(folder):
        raise FileError("not a folder")
    name = clean_name(name)
    if kind == "file" and not os.path.splitext(name)[1] and not name.startswith("."):
        name += DEFAULT_EXT
        if len(name.encode("utf-8")) > NAME_MAX:
            raise FileError("that name is too long")
    path = os.path.join(folder, name)
    if os.path.lexists(path):
        raise FileError(f"{name} already exists here")
    try:
        if kind == "dir":
            os.mkdir(path)
        else:
            stem = os.path.splitext(name)[0]
            seed = f"# {stem}\n\n" if name.lower().endswith(DEFAULT_EXT) else ""
            _create(path, seed.encode("utf-8"))
    except FileExistsError:
        raise FileError(f"{name} already exists here") from None
    except OSError as exc:
        raise FileError(f"could not create it: {exc.strerror or exc}") from None
    return {"ok": True, "path": path}


def rename(path: str, name) -> dict:
    """Give a file or folder a new name, in the same folder."""
    name = clean_name(name)
    if not os.path.lexists(path):
        raise FileError("it is gone from the disk")
    target = os.path.join(os.path.dirname(path), name)
    if target == path:
        return {"ok": True, "path": path}
    # rename() replaces whatever is at the target, so the target must be free,
    # or be this very entry under another spelling, as "a.md" and "A.md" are
    # on a case-insensitive disk
    if os.path.lexists(target) and not _same_entry(path, target):
        raise FileError(f"{name} already exists here")
    try:
        os.rename(path, target)
    except OSError as exc:
        raise FileError(f"could not rename it: {exc.strerror or exc}") from None
    return {"ok": True, "path": target}


def _fold(name: str) -> str:
    return unicodedata.normalize("NFC", name).casefold()


def _same_entry(a: str, b: str) -> bool:
    """True only for one directory entry spelled two ways.

    Not samefile(): that follows symlinks, so a link and the file it points
    at would count as the same, and renaming the link onto the file would
    replace the file with a link to itself. Nor is a second hard link to the
    same file the same entry: renaming one onto the other would drop a name.
    """
    if _fold(os.path.basename(a)) != _fold(os.path.basename(b)):
        return False
    try:
        sa, sb = os.lstat(a), os.lstat(b)
    except OSError:
        return False
    return (sa.st_dev, sa.st_ino) == (sb.st_dev, sb.st_ino)


def _fit(name: str, room: int) -> str:
    """`name`, cut down from the end of its stem to fit in `room` bytes."""
    if len(name.encode("utf-8")) <= room:
        return name
    stem, ext = os.path.splitext(name)
    if len(ext.encode("utf-8")) > room // 2:
        stem, ext = name, ""
    budget = room - len(ext.encode("utf-8"))
    return stem.encode("utf-8")[:budget].decode("utf-8", "ignore") + ext


# -- the trash --------------------------------------------------------------

def trash(path: str) -> dict:
    """Move to the desktop's trash. There is no permanent delete, on purpose."""
    if not os.path.lexists(path):
        raise FileError("it is already gone")
    if sys.platform == "darwin":
        # Finder resolves a symlink and would trash what it points at, which
        # may not even be inside the folder you opened
        return _trash_link_mac(path) if os.path.islink(path) else _trash_finder(path)
    return _trash_freedesktop(path)


def trash_dir() -> str:
    base = os.environ.get("XDG_DATA_HOME") or os.path.join(
        os.path.expanduser("~"), ".local", "share")
    return os.path.join(base, "Trash")


def _device(path: str) -> int | None:
    """The device a path lives on, or would once created."""
    while path and not os.path.exists(path):
        parent = os.path.dirname(path)
        if parent == path:
            return None
        path = parent
    try:
        return os.stat(path).st_dev
    except OSError:
        return None


def _trash_freedesktop(path: str) -> dict:
    """The freedesktop.org trash, which every Linux file manager reads.

    Each trashed item gets a .trashinfo beside it recording where it came
    from, so "restore" in the file manager puts it back. The info file is
    created first and exclusively, which is how the specification has two
    programs agree on a free name without a lock.
    """
    home_trash = trash_dir()
    if os.lstat(path).st_dev != _device(home_trash):
        # another drive has its own trash at its top; gio knows how to find it
        if shutil.which("gio"):
            return _run_trash(["gio", "trash", "--", path])
        raise FileError("this is on another drive, and there is no trash "
                        "cortex can move it to; delete it from your file manager")

    files = os.path.join(home_trash, "files")
    info = os.path.join(home_trash, "info")
    try:
        os.makedirs(files, mode=0o700, exist_ok=True)
        os.makedirs(info, mode=0o700, exist_ok=True)
    except OSError as exc:
        raise FileError(f"could not reach the trash: {exc.strerror or exc}") from None

    # the record keeps the full original name; the name in the trash only has
    # to be unique, and short enough to take ".trashinfo" and a counter
    stem, ext = os.path.splitext(
        _fit(os.path.basename(path.rstrip("/")), NAME_MAX - len(".trashinfo") - 6))
    record = ("[Trash Info]\n"
              f"Path={quote(path)}\n"
              f"DeletionDate={time.strftime('%Y-%m-%dT%H:%M:%S')}\n")
    for n in range(1, 1000):
        name = stem + ext if n == 1 else f"{stem}.{n}{ext}"
        info_path = os.path.join(info, name + ".trashinfo")
        try:
            fd = os.open(info_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            continue
        except OSError as exc:
            raise FileError(f"could not reach the trash: {exc.strerror or exc}") from None
        dest = os.path.join(files, name)
        if os.path.lexists(dest):          # an orphan from some other program
            os.close(fd)
            os.unlink(info_path)
            continue
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(record)
        try:
            os.rename(path, dest)
        except OSError as exc:
            os.unlink(info_path)
            raise FileError(f"could not move it to the trash: "
                            f"{exc.strerror or exc}") from None
        return {"ok": True, "trashed": dest}
    raise FileError("the trash has too many things with this name")


def _trash_finder(path: str) -> dict:
    # Finder's own delete, so Put Back works and each drive's trash is used.
    # The path travels as an argument, never spliced into the script.
    script = ["on run argv",
              'tell application "Finder" to delete (POSIX file (item 1 of argv) as alias)',
              "end run"]
    argv = ["osascript"]
    for line in script:
        argv += ["-e", line]
    return _run_trash(argv + [path])


def _trash_link_mac(path: str) -> dict:
    """A symlink is only a name, so it is moved into the trash by hand.

    macOS may refuse even to look inside ~/.Trash, so whether a name is free
    there cannot be checked; a random suffix makes asking unnecessary, since
    rename() would otherwise replace whatever already had the name.
    """
    stem, ext = os.path.splitext(_fit(os.path.basename(path), NAME_MAX - 8))
    dest = os.path.join(os.path.expanduser("~"), ".Trash",
                        f"{stem} {secrets.token_hex(3)}{ext}")
    try:
        os.rename(path, dest)
    except OSError as exc:
        raise FileError(f"could not move the link to the trash: "
                        f"{exc.strerror or exc}") from None
    return {"ok": True, "trashed": dest}


def _run_trash(argv: list[str]) -> dict:
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=60,
                              stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError) as exc:
        raise FileError(f"could not move it to the trash: {exc}") from None
    if proc.returncode != 0:
        why = (proc.stderr or proc.stdout or "").strip().splitlines()
        raise FileError("could not move it to the trash"
                        + (f": {why[-1]}" if why else ""))
    return {"ok": True}
