"""Folders you have opened, and the ones you have named.

Projects are the folders saved under a name, with `--save` or from the window.
They live in ~/.config/cortex/projects.json as plain JSON, so they can be
edited by hand. Recent folders are remembered as they are opened, so the window
can offer the last few without anyone typing a path. That list is a cache:
deleting it loses nothing but the convenience.
"""

from __future__ import annotations

import json
import os
import tempfile

HOME = os.path.expanduser("~")
CONFIG_DIR = os.path.join(HOME, ".config", "cortex")
PROJECTS_FILE = os.path.join(CONFIG_DIR, "projects.json")
RECENT_FILE = os.path.join(HOME, ".cache", "cortex", "recent.json")
RECENT_MAX = 12
COMPLETE_MAX = 40


def _read_json(path: str, kind: type):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, kind) else kind()
    except (OSError, ValueError):
        return kind()


def _write_json(path: str, data) -> None:
    folder = os.path.dirname(path)
    os.makedirs(folder, exist_ok=True)
    # beside the target and renamed, so a crash cannot leave half a file; the
    # temporary name is unique, so two writers cannot share one half-file
    fd, tmp = tempfile.mkstemp(prefix=".cortex-", suffix=".tmp", dir=folder)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, sort_keys=isinstance(data, dict))
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# -- projects ---------------------------------------------------------------

def load_projects() -> dict:
    data = _read_json(PROJECTS_FILE, dict)
    return {k: v for k, v in data.items()
            if isinstance(k, str) and isinstance(v, str)}


def save_projects(projects: dict) -> None:
    _write_json(PROJECTS_FILE, projects)


def clean_project_name(name) -> str | None:
    if not isinstance(name, str):
        return None
    name = name.strip()
    return name if 0 < len(name) <= 80 and "\n" not in name else None


# -- recent folders ---------------------------------------------------------

def load_recent() -> list[str]:
    return [p for p in _read_json(RECENT_FILE, list) if isinstance(p, str)]


def remember(root: str) -> None:
    """Put `root` at the front of the recent list. Never raises."""
    recent = [p for p in load_recent() if p != root]
    try:
        _write_json(RECENT_FILE, ([root] + recent)[:RECENT_MAX])
    except OSError:
        pass


def last_root() -> str | None:
    """The folder opened most recently that is still there."""
    for path in load_recent():
        if os.path.isdir(path):
            return path
    return None


# -- typing a path ----------------------------------------------------------

def complete(partial: str, limit: int = COMPLETE_MAX) -> dict:
    """Folders a half-typed path could be heading for.

    Only folder names are ever listed, never files and never anything inside
    them: this exists so a path can be typed, not so the disk can be browsed.
    A dot-folder is offered only once a dot has been typed, the way a shell
    completes them.
    """
    raw = (partial or "").strip() or "~/"
    text = os.path.expanduser(raw)
    if not os.path.isabs(text):
        text = os.path.join(HOME, text)
    if raw.endswith("/") or text.endswith("/"):
        base, stem = text, ""
    else:
        base, stem = os.path.dirname(text), os.path.basename(text)
    base = os.path.normpath(base) if base else "/"

    out: list[str] = []
    try:
        with os.scandir(base) as it:
            names = sorted((e.name for e in it if _is_dir(e)), key=str.lower)
    except OSError:
        names = []
    low = stem.lower()
    for name in names:
        if name.startswith(".") and not stem.startswith("."):
            continue
        if low and not name.lower().startswith(low):
            continue
        out.append(os.path.join(base, name))
        if len(out) >= limit:
            break
    return {"base": base, "dirs": out}


def _is_dir(entry) -> bool:
    try:
        return entry.is_dir()            # following links: a linked folder is a folder
    except OSError:
        return False


def short(path: str) -> str:
    """~/x for a path under the home directory. Not /home/userx for /home/user."""
    if path == HOME or path.startswith(HOME + os.sep):
        return "~" + path[len(HOME):]
    return path
