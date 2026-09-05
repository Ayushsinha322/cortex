"""Remembering where the nodes ended up.

A force-directed graph settles somewhere different every run, so a project you
open every day looks different every day and you lose the spatial memory that
makes a map useful at all.  Positions are saved per mapped folder and restored
on the next launch, along with the camera, so it opens where you left it.

This is a cache, not data: deleting it costs one re-settle.
"""

from __future__ import annotations

import hashlib
import json
import os

CACHE_DIR = os.path.join(os.path.expanduser("~"), ".cache", "cortex", "layouts")
MAX_NODES = 4000          # beyond this the file is bigger than the benefit


def _key(root: str) -> str:
    digest = hashlib.sha256(root.encode("utf-8", "replace")).hexdigest()[:20]
    return os.path.join(CACHE_DIR, digest + ".json")


def load(root: str) -> dict:
    """{"positions": {path: [x, y]}, "cam": {...}} for one mapped folder."""
    try:
        with open(_key(root), "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {"positions": {}, "cam": None}
    if not isinstance(data, dict) or data.get("root") != root:
        return {"positions": {}, "cam": None}
    positions = data.get("positions")
    cam = data.get("cam")
    return {
        "positions": positions if isinstance(positions, dict) else {},
        "cam": cam if isinstance(cam, dict) else None,
    }


def save(root: str, positions: dict, cam=None) -> bool:
    """Write the layout for `root`. False when it could not be written."""
    if not isinstance(positions, dict):
        return False
    trimmed = {}
    for path, xy in positions.items():
        if len(trimmed) >= MAX_NODES:
            break
        if (isinstance(path, str) and isinstance(xy, (list, tuple))
                and len(xy) == 2 and all(_finite(v) for v in xy)):
            trimmed[path] = [round(float(xy[0]), 1), round(float(xy[1]), 1)]

    payload = {"root": root, "positions": trimmed}
    if isinstance(cam, dict) and all(_finite(cam.get(k)) for k in "sxy"):
        payload["cam"] = {k: round(float(cam[k]), 3) for k in "sxy"}

    path = _key(root)
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        # written beside the target and renamed, so a crash mid-write cannot
        # leave a half-file that fails to parse on the next launch
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, separators=(",", ":"))
        os.replace(tmp, path)
        return True
    except OSError:
        return False


def forget(root: str) -> bool:
    try:
        os.remove(_key(root))
        return True
    except OSError:
        return False


def _finite(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) \
        and value == value and abs(value) < 1e9
