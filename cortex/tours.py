"""Tours: a walk through a project, with something said at each stop.

Someone who knows a codebase records a path through it -- start here, this
calls that, the interesting part is on line 120 -- and a newcomer presses
play and is walked through it, the graph flying from stop to stop.

A tour is an ordinary markdown file in the project's .tours folder, so it is
committed with the code, reviewed in pull requests, and reads properly on
GitHub, where every stop is a link that works:

    # Getting around the server

    Where requests come in and where they go.

    1. [cortex/cli.py](../cortex/cli.py)
       Everything starts here.

    2. [cortex/server.py, line 120](../cortex/server.py#L120)
       The routes.

Parsing is forgiving, because people will edit these by hand: any numbered
list item that starts with a link is a stop, and whatever is indented under
it is what is said there.
"""

from __future__ import annotations

import os
import re
import tempfile
from urllib.parse import quote, unquote

TOUR_DIR = ".tours"
MAX_STOPS = 200
MAX_TOURS = 200
MAX_BYTES = 512 * 1024

RE_STOP = re.compile(r"^\s{0,3}\d{1,4}[.)]\s+\[([^\]]*)\]\((<[^>]*>|[^)\s]*)\)\s*(.*)$")
RE_TITLE = re.compile(r"^#\s+(.+?)\s*#*\s*$")
RE_LINE = re.compile(r"#L(\d{1,7})(?:-L?\d+)?$")


def folder(root: str) -> str:
    return os.path.join(root, TOUR_DIR)


def slug(title: str) -> str:
    s = re.sub(r"[^\w]+", "-", title.lower(), flags=re.UNICODE).strip("-_")
    return (s or "tour")[:60].rstrip("-")


# -- reading ----------------------------------------------------------------

def parse(text: str, here: str, root: str, inside) -> dict:
    """A tour file's title, introduction and stops.

    `here` is the folder the file is in, which its links are relative to.
    Stops pointing outside the root are dropped; stops whose file has gone
    are kept and marked, so the tour still plays and says what is missing.
    """
    title, intro, stops = None, [], []
    current = None
    for raw in text.splitlines():
        if title is None and not stops:
            m = RE_TITLE.match(raw)
            if m:
                title = m.group(1)
                continue
        # under a stop, anything indented is what is said there -- even a line
        # that looks like a stop of its own, "1. [the docs](https://...)"
        if current is not None and (raw.startswith((" ", "\t")) or not raw.strip()):
            current["note"].append(raw[3:] if raw.startswith("   ") else raw.strip())
            continue
        m = RE_STOP.match(raw)
        if m:
            target = m.group(2).strip("<>")
            line = None
            hit = RE_LINE.search(target)
            if hit:
                line = int(hit.group(1))
            target = unquote(target.split("#", 1)[0])
            if not target or re.match(r"^[a-z][a-z0-9+.-]*:", target, re.I):
                current = None                      # a web link, not a stop
                continue
            path = os.path.normpath(target if target.startswith("/")
                                    else os.path.join(here, target))
            if not inside(path) and not inside(os.path.dirname(path)):
                current = None
                continue
            current = {"path": path, "line": line,
                       "label": m.group(1).strip(),
                       "note": [m.group(3)] if m.group(3).strip() else [],
                       "missing": not os.path.lexists(path)}
            stops.append(current)
            if len(stops) >= MAX_STOPS:
                break
            continue
        current = None
        if not stops and raw.strip():
            intro.append(raw)
    for stop in stops:
        # blank lines at either end go; a note's own indentation -- a code
        # block on its first line -- stays
        stop["note"] = "\n".join(stop["note"]).strip("\n").rstrip()
    return {"title": title or "Untitled tour", "intro": "\n".join(intro).strip(),
            "stops": stops}


def read(path: str, root: str, inside) -> dict:
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        text = fh.read(MAX_BYTES)
    tour = parse(text, os.path.dirname(path), root, inside)
    tour["path"] = path
    return tour


def list_all(root: str, inside) -> list[dict]:
    """Every tour in the project, by title, without the text of each stop."""
    where = folder(root)
    out = []
    try:
        names = sorted(n for n in os.listdir(where) if n.lower().endswith(".md"))
    except OSError:
        return []
    for name in names[:MAX_TOURS]:
        path = os.path.join(where, name)
        if not os.path.isfile(path) or not inside(path):
            continue
        try:
            tour = read(path, root, inside)
        except OSError:
            continue
        out.append({"path": path, "title": tour["title"],
                    "intro": tour["intro"].split("\n")[0][:200],
                    "stops": len(tour["stops"])})
    out.sort(key=lambda t: t["title"].lower())
    return out


# -- writing ----------------------------------------------------------------

def render(title: str, intro: str, stops: list[dict], here: str, root: str) -> str:
    """The markdown for a tour, with links relative to where it is kept."""
    out = [f"# {' '.join(title.split()) or 'Untitled tour'}", ""]
    if intro.strip():
        out += [intro.strip(), ""]
    for i, stop in enumerate(stops, 1):
        rel = os.path.relpath(stop["path"], here)
        shown = os.path.relpath(stop["path"], root)
        line = stop.get("line")
        target = quote(rel) + (f"#L{line}" if line else "")
        label = shown + (f", line {line}" if line else "")
        label = label.replace("[", "(").replace("]", ")")
        out.append(f"{i}. [{label}]({target})")
        for note_line in (stop.get("note") or "").strip("\n").rstrip().splitlines():
            out.append(("   " + note_line) if note_line.strip() else "")
        out.append("")
    return "\n".join(out).rstrip() + "\n"


def clean_stops(stops, inside) -> list[dict]:
    out = []
    for stop in stops if isinstance(stops, list) else []:
        if not isinstance(stop, dict):
            continue
        path = stop.get("path")
        if not isinstance(path, str) or not inside(path):
            continue
        line = stop.get("line")
        line = line if isinstance(line, int) and not isinstance(line, bool) \
            and 0 < line < 10_000_000 else None
        note = stop.get("note") if isinstance(stop.get("note"), str) else ""
        out.append({"path": path, "line": line, "note": note[:20_000]})
        if len(out) >= MAX_STOPS:
            break
    return out


def write(root: str, title: str, intro: str, stops: list[dict], inside,
          path: str | None = None) -> str:
    """Save a tour. A new one gets a file named after its title; an existing
    one (`path`) is rewritten where it is."""
    where = folder(root)
    title = " ".join((title or "").split())[:120] or "Untitled tour"
    if path is None:
        os.makedirs(where, exist_ok=True)
        base = slug(title)
        path = os.path.join(where, base + ".md")
        n = 2
        while os.path.lexists(path):
            path = os.path.join(where, f"{base}-{n}.md")
            n += 1
    elif os.path.dirname(path) != where or not path.endswith(".md"):
        raise ValueError("a tour lives in the project's .tours folder")
    text = render(title, intro or "", clean_stops(stops, inside), where, root)
    fd, tmp = tempfile.mkstemp(prefix=".cortex-", suffix=".save", dir=where)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return path
