"""`.gitignore` support.

The built-in ignore list in `scanner` knows about caches and dependency trees
by name.  It cannot know that *this* project writes `generated/`, or that the
build lands in `out-x86_64/`.  The project already wrote that down, in the file
git reads, so read it too.

This implements the part of the format that appears in real repositories:
comments, negation with `!`, directory-only patterns, anchoring, character
classes, and `**`.  What it does not do is consult `.git/info/exclude` or a
global excludes file, both of which live outside the folder being mapped.
"""

from __future__ import annotations

import os
import re

MAX_PATTERNS = 2000          # a pathological ignore file should not stall a scan


def _translate(pattern: str) -> str:
    """One gitignore pattern to a regex matching a path relative to its base."""
    anchored = pattern.startswith("/") or "/" in pattern.rstrip("/")
    pattern = pattern.lstrip("/")

    out = []
    segments = pattern.split("/")
    for i, seg in enumerate(segments):
        if i:
            out.append("/")
        if seg == "**":
            # `a/**/b` spans any number of directories, including none
            out.append("(?:[^/]+/)*" if i + 1 < len(segments) else ".*")
            if i + 1 < len(segments):
                out.append("\x00")          # marker: swallow the following "/"
            continue
        out.append(_translate_segment(seg))

    body = "".join(out).replace("\x00/", "")
    prefix = "" if anchored else "(?:.*/)?"
    # Matching a directory also matches everything beneath it.
    return f"^{prefix}{body}(?:/.*)?$"


def _translate_segment(seg: str) -> str:
    out = []
    i = 0
    while i < len(seg):
        ch = seg[i]
        if ch == "*":
            out.append("[^/]*")
        elif ch == "?":
            out.append("[^/]")
        elif ch == "[":
            end = seg.find("]", i + 1)
            if end == -1:
                out.append(re.escape(ch))
            else:
                body = seg[i + 1:end]
                if body.startswith("!"):
                    body = "^" + body[1:]
                out.append("[" + body.replace("\\", "\\\\") + "]")
                i = end
        else:
            out.append(re.escape(ch))
        i += 1
    return "".join(out)


class IgnoreFile:
    """The rules from one `.gitignore`, matched against paths below it."""

    __slots__ = ("base", "rules")

    def __init__(self, base: str, text: str) -> None:
        self.base = base
        self.rules: list[tuple[re.Pattern, bool, bool]] = []
        for raw in text.splitlines()[:MAX_PATTERNS]:
            line = raw.rstrip()
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            if line.endswith("\\"):          # an escaped trailing space, rare
                line = line[:-1]
            negated = line.startswith("!")
            if negated:
                line = line[1:]
            elif line.startswith("\\"):      # \! or \# is a literal
                line = line[1:]
            dir_only = line.endswith("/")
            line = line.rstrip("/")
            if not line:
                continue
            try:
                self.rules.append((re.compile(_translate(line)), negated, dir_only))
            except re.error:
                continue                     # a pattern we cannot read is not fatal

    def verdict(self, rel: str, is_dir: bool) -> bool | None:
        """True to ignore, False to keep, None when this file has no opinion.

        Later rules win, which is what makes `!keep.me` after `*.me` work.
        """
        answer = None
        for rx, negated, dir_only in self.rules:
            if dir_only and not is_dir:
                continue
            if rx.match(rel):
                answer = not negated
        return answer


def load(directory: str) -> IgnoreFile | None:
    """Read `directory/.gitignore`, or None when there isn't one."""
    path = os.path.join(directory, ".gitignore")
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as fh:
            return IgnoreFile(directory, fh.read(256 * 1024))
    except OSError:
        return None


def decide(chain: list[IgnoreFile], path: str, is_dir: bool) -> bool:
    """Ask a root-to-leaf chain of ignore files whether to skip `path`.

    The deepest file with an opinion wins, matching git: a nested `.gitignore`
    is allowed to bring back something its parent excluded.
    """
    for ig in reversed(chain):
        rel = os.path.relpath(path, ig.base).replace(os.sep, "/")
        if rel.startswith(".."):
            continue
        verdict = ig.verdict(rel, is_dir)
        if verdict is not None:
            return verdict
    return False
