"""Today's note, and getting a thought into it without opening anything.

The daily note is the habit most note-takers already have: one file per day,
made on first use. cortex keeps them in a `daily` folder at the top of the
folder you have open -- or in whichever of `Daily`, `journal` or `Journal`
already exists there, since that is where your old ones are.

Quick capture is the half-second version: from the app menu, a small window
takes one line, adds it to today's note with the time in front, and goes
away. No graph, no folder to choose, no switching windows.
"""

from __future__ import annotations

import datetime
import os
import threading

from . import files

FOLDERS = ("daily", "Daily", "journal", "Journal", "journals")
DEFAULT_FOLDER = "daily"
MAX_CAPTURE = 20_000

# A capture appends; two at once must not interleave or lose one.
_LOCK = threading.Lock()


def folder(root: str) -> str:
    for name in FOLDERS:
        path = os.path.join(root, name)
        if os.path.isdir(path):
            return path
    return os.path.join(root, DEFAULT_FOLDER)


def _check(path: str, inside) -> None:
    # a daily/ that is a symlink -- from a cloned repository, say -- must not
    # become a way to write somewhere else
    if inside is not None and not inside(path):
        raise files.FileError("today's note would be outside this folder")


def heading(day: datetime.date) -> str:
    # "Thursday, 9 October 2026" -- strftime's day is zero-padded everywhere,
    # and %-d is not portable, so the number is put in by hand
    return f"# {day.strftime('%A')}, {day.day} {day.strftime('%B %Y')}"


def today_path(root: str, day: datetime.date | None = None) -> str:
    day = day or datetime.date.today()
    return os.path.join(folder(root), f"{day.isoformat()}.md")


def open_today(root: str, day: datetime.date | None = None, inside=None) -> dict:
    """Today's note, made if it is not there yet. {path, created}."""
    day = day or datetime.date.today()
    path = today_path(root, day)
    _check(os.path.dirname(path), inside)
    _check(path, inside)
    with _LOCK:
        if os.path.isfile(path):
            return {"ok": True, "path": path, "created": False}
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            files._create(path, (heading(day) + "\n\n").encode("utf-8"))
        except FileExistsError:
            return {"ok": True, "path": path, "created": False}
        except OSError as exc:
            raise files.FileError(f"could not make today's note: "
                                  f"{exc.strerror or exc}") from None
    return {"ok": True, "path": path, "created": True}


def capture(root: str, text, now: datetime.datetime | None = None,
            inside=None) -> dict:
    """Add a line -- or a few -- to today's note, stamped with the time."""
    if not isinstance(text, str) or not text.strip():
        raise files.FileError("nothing to add")
    text = text.strip()[:MAX_CAPTURE]
    now = now or datetime.datetime.now()
    made = open_today(root, now.date(), inside)
    path = made["path"]
    lines = text.splitlines()
    entry = f"- {now.strftime('%H:%M')} {lines[0]}\n" + "".join(
        f"  {line}\n" if line.strip() else "\n" for line in lines[1:])
    with _LOCK:
        try:
            with open(path, "rb") as fh:
                fh.seek(0, os.SEEK_END)
                size = fh.tell()
                if size:
                    fh.seek(-1, os.SEEK_END)
                    ends_clean = fh.read(1) == b"\n"
                else:
                    ends_clean = True
            # O_NOFOLLOW: a symlink put at today's name since the check above
            # is refused, not followed
            fd = os.open(path, os.O_WRONLY | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0))
            with os.fdopen(fd, "ab") as fh:
                fh.write((("" if ends_clean else "\n") + entry).encode("utf-8"))
        except OSError as exc:
            raise files.FileError(f"could not add it: {exc.strerror or exc}") from None
    return {"ok": True, "path": path, "created": made["created"]}
