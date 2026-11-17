"""Which cortex is already running, and for which folder.

cortex can go on working after its window has closed: an agent mid-task, a
test run in a terminal. For the next `cortex` to open a window onto that one,
rather than start another beside it, the one running has to be findable.

Each running cortex keeps one small file saying where it is: its folder, its
port, and the token its page is opened with. The token is what lets a page
in; so the file is the user's alone, in the runtime directory where the
system has one -- memory, not disk, and gone at logout -- and it is removed
when cortex stops. A file left by one that died is ignored and cleared away.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

_mine: str | None = None            # this process's own file


def folder() -> str:
    base = os.environ.get("XDG_RUNTIME_DIR")
    if not base or not os.path.isdir(base):
        base = os.path.join(os.path.expanduser("~"), ".cache", "cortex")
    path = os.path.join(base, "cortex-run") if base.endswith("cortex") \
        else os.path.join(base, "cortex")
    os.makedirs(path, mode=0o700, exist_ok=True)
    try:
        os.chmod(path, 0o700)
    except OSError:
        pass
    return path


def _write(path: str, data: dict) -> None:
    tmp = f"{path}.{os.getpid()}.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(data, fh)
    os.replace(tmp, path)


def announce(root: str, port: int, token: str, read_only: bool = False) -> None:
    """This cortex is up, here."""
    global _mine
    try:
        _mine = os.path.join(folder(), f"{os.getpid()}.json")
        _write(_mine, {"pid": os.getpid(), "root": root, "port": port,
                       "token": token, "readOnly": bool(read_only)})
    except OSError:
        _mine = None


def moved(root: str) -> None:
    """The window opened another folder: this cortex is that folder's now."""
    if not _mine:
        return
    try:
        with open(_mine, encoding="utf-8") as fh:
            data = json.load(fh)
        data["root"] = root
        _write(_mine, data)
    except (OSError, ValueError):
        pass


def withdraw() -> None:
    global _mine
    if _mine:
        try:
            os.remove(_mine)
        except OSError:
            pass
        _mine = None


def url(entry: dict) -> str:
    return f"http://127.0.0.1:{entry['port']}/?t={entry['token']}"


def ask(entry: dict, path: str = "/api/status", body: dict | None = None,
        timeout: float = 2.0) -> dict | None:
    """Ask a running cortex something. None if it does not answer as one."""
    try:
        req = urllib.request.Request(
            f"http://127.0.0.1:{int(entry['port'])}{path}?t={entry['token']}",
            data=None if body is None else json.dumps(body).encode(),
            headers={"Content-Type": "application/json"} if body is not None else {})
        with urllib.request.urlopen(req, timeout=timeout) as res:
            got = json.loads(res.read().decode("utf-8"))
        return got if isinstance(got, dict) else None
    except (OSError, ValueError, KeyError, TypeError, urllib.error.URLError):
        return None


def _alive(pid) -> bool:
    try:
        os.kill(int(pid), 0)
        return True
    except (OSError, ValueError, TypeError):
        return False


def running() -> list[dict]:
    """Every cortex that is up and answering, each with what it says of itself."""
    out = []
    try:
        names = sorted(os.listdir(folder()))
    except OSError:
        return out
    for name in names:
        if not name.endswith(".json"):
            continue
        path = os.path.join(folder(), name)
        try:
            with open(path, encoding="utf-8") as fh:
                entry = json.load(fh)
        except (OSError, ValueError):
            continue
        if not isinstance(entry, dict) or entry.get("pid") == os.getpid():
            continue
        status = ask(entry) if _alive(entry.get("pid")) else None
        if status is None or status.get("pid") != entry.get("pid"):
            # dead, or the port now belongs to something else
            if not _alive(entry.get("pid")):
                try:
                    os.remove(path)
                except OSError:
                    pass
            continue
        entry["status"] = status
        out.append(entry)
    return out


def covering(path: str) -> list[dict]:
    """The cortexes whose folder holds `path`, the nearest folder first."""
    hits = [e for e in running()
            if path == e.get("root") or path.startswith(str(e.get("root")).rstrip("/") + "/")]
    return sorted(hits, key=lambda e: -len(e["root"]))


def find(root: str) -> dict | None:
    """The cortex running for this folder, if there is one."""
    for entry in running():
        if entry.get("root") == root:
            return entry
    return None
