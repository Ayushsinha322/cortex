"""What git thinks of the files in the graph.

The graph already glows for files touched in the last week, which is a guess at
"what am I working on".  Git knows the answer exactly, so ask it: a node can
show that it is modified, staged, untracked or conflicted, and a folder can
show that something under it is.

Only the repository containing the folder being asked about is consulted.  A
home directory full of repositories gets nothing until you narrow to one, which
is the same moment the rest of the graph starts paying off.
"""

from __future__ import annotations

import os
import shutil
import subprocess

TIMEOUT = 5.0
MAX_ENTRIES = 5000          # a repository mid-rebase can report a great many

MODIFIED = "modified"
STAGED = "staged"
UNTRACKED = "untracked"
CONFLICT = "conflict"
INSIDE = "inside"           # a directory with one of the above beneath it

# Which state wins when a path could be several. A conflict is the one you
# most need to see; "inside" is only ever a fallback for a folder.
RANK = {CONFLICT: 4, MODIFIED: 3, STAGED: 2, UNTRACKED: 1, INSIDE: 0}


def repo_root(path: str) -> str | None:
    """The repository `path` belongs to, or None when it is not in one."""
    cur = os.path.realpath(path)
    for _ in range(40):
        if os.path.exists(os.path.join(cur, ".git")):
            return cur
        parent = os.path.dirname(cur)
        if parent == cur:
            return None
        cur = parent
    return None


def _classify(code: str) -> str:
    x, y = (code + "  ")[0], (code + "  ")[1]
    if x == "?" or y == "?":
        return UNTRACKED
    if x == "U" or y == "U" or (x == "A" and y == "A") or (x == "D" and y == "D"):
        return CONFLICT
    if y != " ":
        return MODIFIED         # changed in the working tree
    return STAGED               # changed, and already added


def _parse(payload: str, repo: str, limit: int = MAX_ENTRIES) -> dict:
    """`git status --porcelain -z` into {absolute path: state}.

    NUL separated because a filename may contain anything at all, including
    spaces, quotes and newlines. A rename entry carries its old path as an
    extra field, which is skipped rather than reported as a second change.
    """
    fields = payload.split("\0")
    states: dict[str, str] = {}
    i = 0
    while i < len(fields) and len(states) < limit:
        entry = fields[i]
        i += 1
        if len(entry) < 4:
            continue
        code, rel = entry[:2], entry[3:]
        if code[0] in "RC":         # rename/copy: the next field is the source
            i += 1
        if not rel:
            continue
        states[os.path.join(repo, rel.rstrip("/"))] = _classify(code)
    return states


def _roll_up(states: dict, repo: str) -> dict:
    """Mark every directory above a change, so a shut folder still says so."""
    out = dict(states)
    for path in list(states):
        cur = os.path.dirname(path)
        while cur.startswith(repo) and cur != repo:
            if cur in out:
                break               # its own state is more specific than ours
            out[cur] = INSIDE
            cur = os.path.dirname(cur)
    return out


def read(path: str, inside=None) -> dict:
    """{"repo": ..., "branch": ..., "states": {path: state}} for one folder.

    `inside` is the boundary test.  A repository usually starts above the
    folder being mapped, so without it this would report filenames from
    outside the folder the user pointed cortex at.
    """
    blank = {"repo": None, "branch": None, "states": {}}
    repo = repo_root(path)
    if not repo or not shutil.which("git"):
        return blank
    try:
        proc = subprocess.run(
            ["git", "-C", repo, "--no-optional-locks", "status",
             "--porcelain", "-z", "--untracked-files=normal"],
            capture_output=True, text=True, timeout=TIMEOUT, errors="replace")
    except (OSError, subprocess.SubprocessError):
        return blank
    if proc.returncode != 0:
        return blank

    states = _roll_up(_parse(proc.stdout, repo), repo)
    if inside is not None:
        states = {p: st for p, st in states.items() if inside(p)}
    return {"repo": repo, "branch": _branch(repo), "states": states}


def _branch(repo: str) -> str | None:
    """The branch name, or the short commit when HEAD is detached.

    `rev-parse --abbrev-ref HEAD` is the obvious command and the wrong one: on
    a repository with no commits yet it answers "HEAD" rather than the branch
    you are on, which is exactly the moment a new project is being mapped.
    """
    for argv, label in ((["symbolic-ref", "--short", "HEAD"], True),
                        (["rev-parse", "--short", "HEAD"], False)):
        try:
            proc = subprocess.run(["git", "-C", repo] + argv, capture_output=True,
                                  text=True, timeout=TIMEOUT, errors="replace")
        except (OSError, subprocess.SubprocessError):
            return None
        name = proc.stdout.strip()
        if proc.returncode == 0 and name:
            return name if label else f"detached at {name}"
    return None
