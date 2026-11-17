"""How the window was left: the theme, the width of the pane, the rail.

The window is a page on a port that is different every time cortex starts,
so nothing the browser remembers for a page is still there the next time.
What should outlast a launch is kept here instead, in
~/.config/cortex/prefs.json: plain JSON, a few keys, safe to edit or delete.

Only keys named below are kept, each checked for the kind of thing it is. A
page can write here, and a file a page can write is not somewhere to accept
whatever it sends.
"""

from __future__ import annotations

import os
import re
import threading

from . import places

FILE = os.path.join(places.CONFIG_DIR, "prefs.json")
HEX = re.compile(r"^#[0-9a-fA-F]{6}$")
NAME = re.compile(r"^[^\x00-\x1f/\\]{1,80}$")
MAX_ACCENTS = 200

_lock = threading.Lock()


def _theme(v):
    return v if isinstance(v, str) and NAME.match(v) else None


def _accents(v):
    """{folder: colour}: a colour of its own for each folder that has one."""
    if not isinstance(v, dict):
        return None
    out = {k: c.lower() for k, c in v.items()
           if isinstance(k, str) and k.startswith("/") and len(k) < 4096
           and isinstance(c, str) and HEX.match(c)}
    return dict(list(out.items())[-MAX_ACCENTS:])


# key -> what it has to be; a checker returns the value to keep, or None
KEYS = {
    "theme": _theme,                          # the theme, or the dark one of a pair
    "light": _theme,                          # the light one of the pair
    "follow": lambda v: v if isinstance(v, bool) else None,   # follow the system
    "accents": _accents,
    "panel": lambda v: int(v) if isinstance(v, (int, float)) and not isinstance(v, bool)
    and 200 <= v <= 6000 else None,
    "rail": lambda v: v if v in ("open", "shut") else None,
    # what the window opens on: the workspace (terminals, agents, pages) with
    # the graph a key away, or the graph with the workspace beside it
    "layout": lambda v: v if v in ("work", "graph") else None,
    "preview": lambda v: v if isinstance(v, str) and len(v) < 2048
    and re.match(r"^https?://", v) else None,
}


def load() -> dict:
    data = places._read_json(FILE, dict)
    out = {}
    for key, check in KEYS.items():
        if key in data:
            value = check(data[key])
            if value is not None:
                out[key] = value
    return out


def update(changes) -> dict:
    """Keep what in `changes` is a known key with a sound value. Returns
    everything as it now stands."""
    if not isinstance(changes, dict):
        return load()
    with _lock:
        now = load()
        for key, value in changes.items():
            check = KEYS.get(key)
            if check is None:
                continue
            kept = check(value)
            if kept is None:
                now.pop(key, None)             # an unsound value is a way to clear one
            else:
                now[key] = kept
        try:
            places._write_json(FILE, now)
        except OSError:
            pass
        return now
