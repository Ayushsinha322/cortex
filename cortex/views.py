"""Named views: a way of looking at a folder, kept under a name.

A view is everything that makes the graph look the way it does right now --
which folder it is narrowed to, which kinds of file are hidden, which folders
are open, where the camera is -- so that "frontend" or "thesis" is one key
away instead of a minute of clicking. Keys 1 to 9 are the first nine.

Views belong to the folder they were made in, and are kept in
~/.config/cortex/views/, one file per mapped folder, because they are
something you made and would miss, unlike the layout cache.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile

from .places import CONFIG_DIR

VIEWS_DIR = os.path.join(CONFIG_DIR, "views")
MAX_VIEWS = 30
MAX_EXPANDED = 600          # folders a view reopens; beyond this it is a layout
NAME_MAX = 60


def _file(root: str) -> str:
    digest = hashlib.sha256(root.encode("utf-8", "replace")).hexdigest()[:20]
    return os.path.join(VIEWS_DIR, digest + ".json")


def load(root: str) -> list[dict]:
    try:
        with open(_file(root), "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return []
    if not isinstance(data, dict) or data.get("root") != root:
        return []
    return [v for v in data.get("views") or [] if isinstance(v, dict) and v.get("name")]


def _store(root: str, views: list[dict]) -> None:
    os.makedirs(VIEWS_DIR, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".views-", suffix=".tmp", dir=VIEWS_DIR)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump({"root": root, "views": views}, fh, indent=1)
        os.replace(tmp, _file(root))
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def clean(view, inside) -> dict | None:
    """What a view may hold, and nothing else. None if it is not a view."""
    if not isinstance(view, dict):
        return None
    name = view.get("name")
    if not isinstance(name, str) or not name.strip():
        return None
    name = " ".join(name.split())[:NAME_MAX]

    def path(p):
        return p if isinstance(p, str) and inside(p) else None

    def number(v):
        return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) \
            and v == v and abs(v) < 1e9 else None

    cam = view.get("cam")
    cam = ({k: number(cam.get(k)) for k in "sxy"}
           if isinstance(cam, dict) and all(number(cam.get(k)) is not None for k in "sxy")
           else None)
    groups = [g for g in view.get("hidden") or [] if isinstance(g, str)][:20]
    expanded = [p for p in (path(p) for p in view.get("expanded") or []) if p]
    return {
        "name": name,
        "scope": path(view.get("scope")),
        "hidden": groups,
        "solo": view.get("solo") if isinstance(view.get("solo"), str) else None,
        "semantic": view.get("semantic") is not False,
        "labels": view.get("labels") is not False,
        "expanded": expanded[:MAX_EXPANDED],
        "selected": path(view.get("selected")),
        "cam": cam,
    }


def save(root: str, view: dict, inside) -> list[dict]:
    """Add a view, or replace the one with the same name (any case)."""
    view = clean(view, inside)
    if view is None:
        raise ValueError("a view needs a name")
    views = [v for v in load(root) if v["name"].lower() != view["name"].lower()]
    # replacing keeps the old slot, so its number key does not move
    old = [v["name"].lower() for v in load(root)]
    slot = old.index(view["name"].lower()) if view["name"].lower() in old else len(views)
    views.insert(min(slot, len(views)), view)
    views = views[:MAX_VIEWS]
    _store(root, views)
    return views


def forget(root: str, name: str) -> list[dict]:
    views = [v for v in load(root)
             if not isinstance(name, str) or v["name"].lower() != name.lower()]
    _store(root, views)
    return views


def move(root: str, name: str, to: int) -> list[dict]:
    """Give a view another slot, so a different number key opens it."""
    views = load(root)
    found = [v for v in views if v["name"].lower() == str(name).lower()]
    if not found:
        return views
    rest = [v for v in views if v is not found[0]]
    rest.insert(max(0, min(int(to), len(rest))), found[0])
    _store(root, rest)
    return rest
