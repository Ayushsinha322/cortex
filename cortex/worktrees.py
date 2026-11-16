"""A copy of the project for each agent, and what it did there.

Two agents let loose on one folder overwrite each other, and one agent let
loose on the folder you are working in overwrites you. Git already has the
answer: a worktree is a second checkout of the same repository on a branch of
its own, sharing the history and nothing else. Each agent gets one, so they
can all work at once, and nothing reaches your folder until you say so.

This file makes the worktrees, and answers the three questions that matter
afterwards: what did it change, what does the change look like, and can it be
put into my folder. All of them are answered against the state the agent
started from -- for a worktree, the commit it was made at; for an agent
working in place, a picture of the folder taken as it began, so that what you
had already changed is not counted as its work.

Nothing here touches the index you stage into. Looking at "everything in the
folder, untracked files too" is done through a throwaway copy of the index.
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import re
import shutil
import subprocess
import tempfile

TIMEOUT = 30.0
BRANCH_PREFIX = "cortex/"
DATA_DIR = os.path.join(os.path.expanduser("~"), ".local", "share", "cortex")
MAX_PATCH = 400 * 1024        # bytes of one file's diff the window is sent
MAX_FILES = 2000              # changed files listed; a vendored tree can be more

# What running a program leaves behind, which is never the agent's work. A
# project with a .gitignore already says so; one without would otherwise have
# its review -- and then your folder -- filled with caches from the agent
# having run the tests. Kept short on purpose: anything that might be source
# is left for git, and the project's own .gitignore, to decide.
LEFTOVERS = ("__pycache__", "node_modules", ".venv", "venv", ".mypy_cache",
             ".pytest_cache", ".ruff_cache", ".tox")
LEFTOVER_FILES = ("*.pyc", ".DS_Store")
EVERYTHING = (["--", "."]
              + [f":(exclude,glob)**/{name}/**" for name in LEFTOVERS]
              + [f":(exclude,glob)**/{name}" for name in LEFTOVER_FILES])


class GitError(Exception):
    """Git said no, or was not there to ask. The message is for a person."""


def available() -> bool:
    return shutil.which("git") is not None


def _git(cwd: str, *args: str, env: dict | None = None, stdin: bytes | None = None,
         check: bool = True, timeout: float = TIMEOUT) -> subprocess.CompletedProcess:
    full = dict(os.environ)
    # never stop to ask for a password, and say things in one language
    full.update({"GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C"})
    if env:
        full.update(env)
    try:
        done = subprocess.run(["git", "-C", cwd, *args], input=stdin,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              env=full, timeout=timeout)
    except FileNotFoundError:
        raise GitError("git is not installed") from None
    except subprocess.TimeoutExpired:
        raise GitError(f"git {args[0]} took too long") from None
    except OSError as exc:
        raise GitError(f"could not run git: {exc.strerror or exc}") from None
    if check and done.returncode != 0:
        raise GitError(_reason(done.stderr) or f"git {args[0]} failed")
    return done


def _reason(stderr: bytes) -> str:
    """The lines of git's complaint worth showing: the first few, without the
    prefixes it puts in front of them."""
    lines = [re.sub(r"^(fatal|error|warning): ", "", ln.strip())
             for ln in stderr.decode("utf-8", "replace").splitlines() if ln.strip()]
    return "; ".join(lines[:4])


def _text(done: subprocess.CompletedProcess) -> str:
    return done.stdout.decode("utf-8", "replace")


def repo_of(path: str) -> str | None:
    """The top of the repository `path` is in, or None when it is in none."""
    if not available() or not os.path.isdir(path):
        return None
    try:
        done = _git(path, "rev-parse", "--show-toplevel", check=False, timeout=5.0)
    except GitError:
        return None
    top = _text(done).strip()
    return os.path.realpath(top) if done.returncode == 0 and top else None


def head(repo: str) -> str | None:
    """The commit the repository is on, or None for one with no commits yet."""
    try:
        done = _git(repo, "rev-parse", "--verify", "--quiet", "HEAD", check=False)
    except GitError:
        return None
    sha = _text(done).strip()
    return sha if done.returncode == 0 and sha else None


def branch_of(tree: str) -> str | None:
    try:
        done = _git(tree, "symbolic-ref", "--quiet", "--short", "HEAD", check=False)
    except GitError:
        return None
    name = _text(done).strip()
    return name if done.returncode == 0 and name else None


def slug(title: str, fallback: str = "task") -> str:
    """A branch-safe name out of whatever the task was called."""
    words = re.sub(r"[^a-z0-9]+", "-", (title or "").lower()).strip("-")
    words = "-".join(words.split("-")[:6])[:40].strip("-")
    return words or fallback


def home_for(repo: str) -> str:
    """Where this repository's worktrees are kept.

    Outside the repository, so they are not files in the project -- nothing to
    ignore, nothing for a search to find twice -- and under the data
    directory rather than the cache, because work in progress is not a cache.
    """
    tag = hashlib.sha1(repo.encode("utf-8", "surrogateescape")).hexdigest()[:8]
    name = re.sub(r"[^A-Za-z0-9._-]+", "-", os.path.basename(repo)) or "repo"
    return os.path.join(DATA_DIR, "worktrees", f"{name}-{tag}")


def create(repo: str, title: str, root: str | None = None) -> dict:
    """A new worktree of `repo` on a new branch, at the commit it is on now.

    Returns {"tree", "branch", "base"}. What you have not committed stays in
    your folder: the agent starts from the last commit, which is the only
    state of the project that has a name.
    """
    base = head(repo)
    if base is None:
        raise GitError("this repository has no commits yet, so there is "
                       "nothing to make a copy of - commit once, or let the "
                       "agent work right here")
    root = root or home_for(repo)
    os.makedirs(root, exist_ok=True)
    stem = slug(title)
    for n in range(1, 200):
        name = stem if n == 1 else f"{stem}-{n}"
        tree, branch = os.path.join(root, name), BRANCH_PREFIX + name
        taken = _git(repo, "show-ref", "--verify", "--quiet",
                     f"refs/heads/{branch}", check=False).returncode == 0
        if not taken and not os.path.lexists(tree):
            break
    else:
        raise GitError("could not find a free name for the branch")
    _git(repo, "worktree", "add", "-b", branch, tree, base, timeout=120.0)
    return {"tree": os.path.realpath(tree), "branch": branch, "base": base}


@contextlib.contextmanager
def _everything(tree: str):
    """An environment in which the index holds the folder exactly as it is.

    A copy of the real index with every change and every untracked file added
    to it, so `git diff --cached` against any commit shows the whole
    difference -- and the index you stage into is never touched. Starting from
    the real one means only what changed has to be read again.
    """
    fd, tmp = tempfile.mkstemp(prefix="cortex-index-")
    os.close(fd)
    try:
        real = _text(_git(tree, "rev-parse", "--git-path", "index")).strip()
        real = real if os.path.isabs(real) else os.path.join(tree, real)
        try:
            # with its timestamp: git trusts an entry older than its index
            # without reading the file, so a copy that looked newer than it
            # is would hide an edit made in the same second as the checkout
            shutil.copy2(real, tmp)
        except OSError:
            os.remove(tmp)             # no index yet: git makes an empty one
        env = {"GIT_INDEX_FILE": tmp}
        _git(tree, "add", "-A", *EVERYTHING, env=env, timeout=120.0)
        yield env
    finally:
        for leftover in (tmp, tmp + ".lock"):
            try:
                os.remove(leftover)
            except OSError:
                pass


def picture(tree: str) -> str:
    """The folder as it is this moment, as a tree git can compare against."""
    with _everything(tree) as env:
        return _text(_git(tree, "write-tree", env=env)).strip()


def changes(tree: str, base: str) -> dict:
    """What is different in `tree` from `base`: every file, with line counts.

    {"files": [{"path", "status", "added", "removed", "binary"}], "added",
    "removed", "more"}. Paths are relative to the top of the repository;
    status is "new", "changed" or "deleted".
    """
    with _everything(tree) as env:
        names = _text(_git(tree, "diff", "--cached", "--no-renames", "--name-status",
                           "-z", base, env=env)).split("\0")
        counts = _text(_git(tree, "diff", "--cached", "--no-renames", "--numstat",
                            "-z", base, env=env)).split("\0")
    status = {}
    for i in range(0, len(names) - 1, 2):
        status[names[i + 1]] = {"A": "new", "D": "deleted"}.get(names[i][:1], "changed")
    files, added, removed = [], 0, 0
    for row in counts:
        parts = row.split("\t", 2)
        if len(parts) != 3:
            continue
        binary = parts[0] == "-"
        a = 0 if binary else int(parts[0])
        r = 0 if binary else int(parts[1])
        added, removed = added + a, removed + r
        files.append({"path": parts[2], "status": status.get(parts[2], "changed"),
                      "added": a, "removed": r, "binary": binary})
    files.sort(key=lambda f: f["path"])
    more = max(0, len(files) - MAX_FILES)
    return {"files": files[:MAX_FILES], "added": added, "removed": removed,
            "more": more}


def patch(tree: str, base: str, path: str) -> dict:
    """One file's difference from `base`, as a unified diff."""
    with _everything(tree) as env:
        raw = _git(tree, "diff", "--cached", "--no-renames", base, "--", path,
                   env=env).stdout
    cut = len(raw) > MAX_PATCH
    return {"path": path, "patch": raw[:MAX_PATCH].decode("utf-8", "replace"),
            "truncated": cut}


def apply_to(repo: str, tree: str, base: str) -> dict:
    """Put what changed in `tree` into the files of `repo`, uncommitted.

    The change arrives the way your own edits do -- in the working files, for
    you to look over and commit -- and only if all of it fits: it is tried
    first, and when any part would not apply to the folder as it is now,
    nothing is changed and git's reason is passed on.
    """
    with _everything(tree) as env:
        diff = _git(tree, "diff", "--cached", "--binary", "--no-renames", base,
                    env=env, timeout=120.0).stdout
    if not diff.strip():
        return {"ok": True, "files": 0}
    tried = _git(repo, "apply", "--check", "--whitespace=nowarn", "-",
                 stdin=diff, check=False, timeout=120.0)
    if tried.returncode != 0:
        return {"ok": False, "conflict": True,
                "error": "it does not fit on top of your folder as it is now: "
                         + (_reason(tried.stderr) or "git would not apply it")}
    _git(repo, "apply", "--whitespace=nowarn", "-", stdin=diff, timeout=120.0)
    count = sum(1 for line in diff.split(b"\n") if line.startswith(b"diff --git "))
    return {"ok": True, "files": count}


def commit_all(tree: str, message: str) -> dict:
    """Commit everything in the worktree onto its branch.

    Returns {"branch", "commits"}: how many commits the branch now has that
    the commit it started from does not.
    """
    _git(tree, "add", "-A", *EVERYTHING, timeout=120.0)
    dirty = _git(tree, "diff", "--cached", "--quiet", check=False).returncode != 0
    if dirty:
        who = []
        if not _text(_git(tree, "config", "user.name", check=False)).strip():
            # a machine nobody has told git who they are on: the commit still
            # has to be made by someone, and it is cortex doing the asking
            who = ["-c", "user.name=cortex", "-c", "user.email=cortex@localhost"]
        # the repository's own commit hooks run, as they would for you: a
        # check someone set up is not cortex's to skip
        _git(tree, *who, "commit", "-m", message or "agent work", timeout=120.0)
    return {"branch": branch_of(tree), "dirty": dirty}


def ahead(tree: str, base: str) -> int:
    try:
        return int(_text(_git(tree, "rev-list", "--count", f"{base}..HEAD")).strip())
    except (GitError, ValueError):
        return 0


def remove(repo: str, tree: str, branch: str | None = None) -> None:
    """Take a worktree away, and its branch too when one is named.

    Only ever a folder this module made: the caller passes the tree it was
    given by create(), and anything that is not a worktree of `repo` is left
    alone.
    """
    listed = _text(_git(repo, "worktree", "list", "--porcelain", check=False))
    known = {os.path.realpath(line[9:]) for line in listed.splitlines()
             if line.startswith("worktree ")}
    if os.path.realpath(tree) in known and os.path.realpath(tree) != os.path.realpath(repo):
        _git(repo, "worktree", "remove", "--force", tree, check=False, timeout=120.0)
        shutil.rmtree(tree, ignore_errors=True)
        _git(repo, "worktree", "prune", check=False)
    if branch and branch.startswith(BRANCH_PREFIX):
        _git(repo, "branch", "-D", branch, check=False)
