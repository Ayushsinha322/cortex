"""The folder's past, as git remembers it.

The graph shows a folder as it is today. Time travel replays how it got there:
files appearing, links forming, folders filling up, one commit at a time. git
already holds every one of those moments, so this asks it, once, for the
commits that touched the folder in view and what each of them changed, and the
page does the replaying.

A long history is cut to its most recent commits. The page still has to start
from the right picture rather than an empty folder, so the answer carries
`base`: the files that were already there before the first commit it lists.

Everything is reported as absolute paths under the folder in view, never
above it. A repository usually starts higher up than the folder someone opened,
and a file moved in from outside is, as far as this folder can tell, a file
that appeared -- so that is what it is called.

Nothing in here raises. A folder outside any repository, a machine without
git, or git failing outright all come back as an empty history, which the page
can show as "no history" without knowing which it was.
"""

from __future__ import annotations

import contextlib
import os
import re
import shutil
import subprocess
import tempfile
import threading

GIT = "git"
TIMEOUT = 15.0              # seconds, for each git command
LIMIT = 2000                # commits, by default
MAX_CHANGES = 150000        # file changes in one answer; past this, the page stalls
CHUNK = 1 << 16

# One header per commit. Every field is NUL-separated, and the first starts
# with \x01: a status letter never does, and a header only ever turns up
# where a status letter could, so nothing a filename contains can be mistaken
# for the start of the next commit. %aN rather than %an so .mailmap applies.
FORMAT = "%x01%H%x00%P%x00%at%x00%aN%x00%s"
HEADER = b"\x01"

SHA = re.compile(rb"[0-9a-f]{40}(?:[0-9a-f]{24})?")    # SHA-1, or SHA-256

# What git exports to hooks and aliases. Inherited by us, they would point
# every command here at that repository instead of the one above the folder.
INHERITED = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR",
             "GIT_PREFIX", "GIT_NAMESPACE")


class _Failed(Exception):
    """git could not answer. The message is short enough to show the user."""


class _Late(_Failed):
    """git ran out of time; whatever arrived before then is still good."""


def _empty(error: str | None = None) -> dict:
    blank = {"repo": None, "head": None, "commits": [], "base": [],
             "truncated": False}
    if error:
        blank["error"] = error
    return blank


def timeline(path: str, inside=None, limit: int = LIMIT) -> dict:
    """Every commit that changed something under `path`, oldest first.

    {"repo", "head", "truncated", "base": [path], "commits": [{"sha", "t",
    "author", "subject", "changes": [[status, path, from_path], ...]}]}, with
    status one of A, M, D and R, and from_path the old name of an R.
    """
    try:
        return _timeline(path, inside, limit)
    except _Failed as exc:
        return _empty(str(exc))
    except Exception as exc:        # a bug here must not take the window down
        return _empty(f"could not read the history ({type(exc).__name__})")


def _timeline(path: str, inside, limit: int) -> dict:
    git = shutil.which(GIT)
    if not git:
        return _empty()
    limit = max(1, int(limit))

    top = _ask(git, path, ["rev-parse", "--show-toplevel"])
    if top is None:
        return _empty()             # not in a repository; nothing to say
    repo = os.path.realpath(_line(top))
    # Where the folder sits inside the repository, as git spells it. Asked of
    # git rather than worked out from the two paths, which can disagree on a
    # case-insensitive disk or across a symlink.
    prefix = _ask(git, path, ["rev-parse", "--show-prefix"])
    if prefix is None:
        raise _Failed("git could not place this folder in its repository")
    prefix = _line(prefix)
    head = _ask(git, repo, ["rev-parse", "-q", "--verify", "HEAD^{commit}"])
    if head is None:                # a repository with no commits yet
        return {"repo": repo, "head": None, "commits": [], "base": [],
                "truncated": False}

    place = _Placer(path, prefix, inside)
    spec = ["--", prefix.rstrip("/") or "."]
    base_cmd = [git, "-C", repo, "--no-pager", "--literal-pathspecs",
                "-c", "log.showSignature=false", "-c", "log.follow=false"]

    # Newest first, and turned around at the end, rather than --reverse: git
    # can then be stopped the moment the cap is reached, and a history that
    # runs out of time still keeps its recent end, which is the end that
    # matters. One commit more than asked for says whether there are more.
    # The main line, with each merge as one step carrying what it brought in:
    # a branch's commits interleaved by date would show files arriving and
    # leaving that never existed together on any one checkout, and skipping
    # merges lost whatever was only resolved in them.
    argv = base_cmd + ["log", "-n", str(limit + 1), "--first-parent", "-m", "-M",
                       "--name-status", "-z", "--format=" + FORMAT] + spec
    commits: list[dict] = []
    oldest = None                   # the parents of the oldest commit kept
    seen = total = 0
    truncated = False
    try:
        with contextlib.closing(_tokens(argv)) as tokens:
            for sha, parents, when, author, subject, raw, cut in _parse(tokens):
                seen += 1
                if seen > limit:
                    truncated = True
                    break
                changes = [c for c in map(place.change, raw) if c]
                if total + len(changes) > MAX_CHANGES:
                    truncated = True
                    if oldest is not None:
                        break
                    changes = changes[:MAX_CHANGES]     # one enormous commit
                truncated = truncated or cut
                total += len(changes)
                oldest = parents
                # a commit with nothing in view would be a step of the slider
                # where nothing happens
                if changes:
                    commits.append({"sha": sha, "t": when, "author": author,
                                    "subject": subject, "changes": changes})
    except _Late:
        if oldest is None:
            raise _Failed("git took too long to read the history") from None
        truncated = True
    commits.reverse()

    base: list[str] = []
    if oldest:                      # no parent means it was the very first commit
        argv = base_cmd + ["ls-tree", "-r", "-z", "--name-only", oldest[0]] + spec
        try:
            with contextlib.closing(_tokens(argv)) as tokens:
                for token in tokens:
                    full = place(token)
                    if full is None:
                        continue
                    if len(base) >= MAX_CHANGES:
                        truncated = True
                        break
                    base.append(full)
        except _Late:
            truncated = True
    return {"repo": repo, "head": _line(head), "truncated": truncated,
            "base": base, "commits": commits}


class _Placer:
    """Turns git's repository-relative names into absolute paths in view.

    Paths are joined onto `path` itself, as the caller spelled it, so they
    match the paths the rest of cortex builds. `inside` is asked about each
    path once: a file touched by a thousand commits is one realpath, not a
    thousand.
    """

    def __init__(self, path: str, prefix: str, inside) -> None:
        self.path = path
        self.prefix = prefix
        self.inside = inside
        self.known: dict[str, bool] = {}

    def __call__(self, raw: bytes | None) -> str | None:
        if raw is None:
            return None
        rel = os.fsdecode(raw)      # a name that is not UTF-8 still round-trips
        if not rel.startswith(self.prefix):
            return None
        full = os.path.join(self.path, rel[len(self.prefix):])
        ok = self.known.get(full)
        if ok is None:
            ok = self.inside is None or bool(self.inside(full))
            self.known[full] = ok
        return full if ok else None

    def change(self, raw: tuple) -> list | None:
        """[status, path, from] for one change, or None if none of it is in view.

        A copy is a new file, wherever its contents came from. A rename with
        one side out of view is a file arriving or leaving. git's own pathspec
        usually settles that already, since it never pairs a rename across
        the boundary; `inside` can still cut one in two.
        """
        status, new, old = raw
        new, old = self(new), self(old)
        if status == "R":
            if new and old:
                return ["R", new, old]
            if new:
                return ["A", new, None]
            return ["D", old, None] if old else None
        if not new:
            return None
        if status in ("A", "C"):
            return ["A", new, None]
        if status == "D":
            return ["D", new, None]
        return ["M", new, None]     # M, a type change, and anything stranger


def _parse(tokens):
    """Commits out of `git log -z --name-status --format=FORMAT`, newest first.

    Each is (sha, [parents], author time, author, subject, changes, cut), with
    changes as (status letter, name, old name or None) in git's own bytes and
    cut True when there were more than MAX_CHANGES of them. A commit is
    yielded once the next one starts, so it is only ever yielded whole.

    The stream is NUL-separated fields: a header's five, then per change a
    status and one name, or two for a rename or copy. git puts a newline in
    front of the first status of each commit. Names are taken by position,
    never looked at, which is what lets them contain anything at all.
    """
    tokens = iter(tokens)

    def field() -> bytes:
        token = next(tokens, None)
        if token is None:
            raise _Failed("git's answer ended partway through a commit")
        return token

    token = next(tokens, None)
    while token is not None:
        sha = token[1:]
        if not token.startswith(HEADER) or not SHA.fullmatch(sha):
            raise _Failed("could not make sense of git's answer")
        parents, when, author, subject = field(), field(), field(), field()
        try:
            when = int(when)
        except ValueError:
            raise _Failed("could not make sense of git's answer") from None
        changes: list[tuple] = []
        cut = False
        token = next(tokens, None)
        while token is not None and not token.startswith(HEADER):
            status = token.lstrip(b"\n")[:1].decode("ascii", "replace")
            if not status.isupper():
                raise _Failed("could not make sense of git's answer")
            old = field() if status in ("R", "C") else None
            new = field()
            if len(changes) < MAX_CHANGES:
                changes.append((status, new, old))
            else:
                cut = True
            token = next(tokens, None)
        yield (sha.decode("ascii"), parents.decode("ascii").split(), when,
               author.decode("utf-8", "replace"),
               subject.decode("utf-8", "replace"), changes, cut)


def _tokens(argv: list[str]):
    """The NUL-separated fields of a command's output, as they arrive.

    Read as a stream so that whoever is reading can stop early: closing the
    generator stops git too. A command that outlives TIMEOUT is killed and
    _Late raised once what it did say has been handed over; a field it was
    still in the middle of writing is dropped.
    """
    with tempfile.TemporaryFile() as err:
        # stderr to a file, not a pipe: git warns once per commit when rename
        # detection gives up, and an unread pipe would fill and hang it
        try:
            proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL,
                                    stdout=subprocess.PIPE, stderr=err, env=_env())
        except OSError as exc:
            raise _Failed(f"could not run git: {exc.strerror or exc}") from None
        late = threading.Event()

        def expire() -> None:
            late.set()
            proc.kill()

        timer = threading.Timer(TIMEOUT, expire)
        timer.daemon = True
        timer.start()
        try:
            rest = b""
            while True:
                chunk = proc.stdout.read1(CHUNK)
                if not chunk:
                    break
                fields = (rest + chunk).split(b"\0")
                rest = fields.pop()
                yield from fields
            code = proc.wait()
            if code != 0 and late.is_set():
                raise _Late("git took too long")
            if code != 0:
                err.seek(0)
                said = err.read().decode("utf-8", "replace").strip().splitlines()
                raise _Failed("git could not read the history"
                              + (f": {said[-1]}" if said else ""))
        finally:
            timer.cancel()
            if proc.poll() is None:
                proc.kill()
            proc.stdout.close()
            proc.wait()


def _ask(git: str, cwd: str, args: list[str]) -> bytes | None:
    """The output of a quick git command, or None when git says no."""
    try:
        proc = subprocess.run([git, "-C", cwd] + args, stdin=subprocess.DEVNULL,
                              capture_output=True, timeout=TIMEOUT, env=_env())
    except subprocess.TimeoutExpired:
        raise _Failed("git took too long") from None
    except OSError as exc:
        raise _Failed(f"could not run git: {exc.strerror or exc}") from None
    return proc.stdout if proc.returncode == 0 else None


def _line(out: bytes) -> str:
    # exactly one newline off the end: a folder's name may end in another
    return os.fsdecode(out[:-1] if out.endswith(b"\n") else out)


def _env() -> dict:
    env = dict(os.environ)
    for name in INHERITED:
        env.pop(name, None)
    return env
