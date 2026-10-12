"""Suggested links someone said no to.

The index suggests notes that seem to be about the same thing. When it is
wrong, dismissing a suggestion should make it stay gone -- across relaunches,
and across rebuilds of the index -- so the pairs are kept here, per mapped
folder, in ~/.cache/cortex/dismissed/. Losing the file brings the
suggestions back; nothing worse.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import threading

DIR = os.path.join(os.path.expanduser("~"), ".cache", "cortex", "dismissed")
MAX_PAIRS = 5000
_LOCK = threading.Lock()


def _file(root: str) -> str:
    digest = hashlib.sha256(root.encode("utf-8", "replace")).hexdigest()[:20]
    return os.path.join(DIR, digest + ".json")


def load(root: str) -> set[tuple[str, str]]:
    try:
        with open(_file(root), "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return set()
    if not isinstance(data, dict) or data.get("root") != root:
        return set()
    return {(a, b) for a, b in (data.get("pairs") or [])
            if isinstance(a, str) and isinstance(b, str)}


def add(root: str, a: str, b: str) -> None:
    pair = (a, b) if a < b else (b, a)
    with _LOCK:
        pairs = load(root)
        pairs.add(pair)
        os.makedirs(DIR, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".dismissed-", suffix=".tmp", dir=DIR)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump({"root": root, "pairs": sorted(pairs)[-MAX_PAIRS:]}, fh)
            os.replace(tmp, _file(root))
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise


def without(root: str, suggest: list) -> list:
    """The suggestions, less the ones dismissed here."""
    gone = load(root)
    if not gone:
        return suggest
    return [s for s in suggest if (min(s[0], s[1]), max(s[0], s[1])) not in gone]
