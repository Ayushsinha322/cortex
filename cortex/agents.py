"""Coding agents, run from the window and shown on the map.

An agent here is a program that already exists on the machine -- Claude Code,
Codex, anything that takes a task on its command line -- started by cortex in
a folder, with what it says and does sent to the window as it happens. cortex
adds three things a terminal tab does not:

  Each one works in a copy. In a git repository an agent gets a worktree of
  its own (see worktrees.py), so several can work on one project at once and
  none of it lands in your folder until you put it there.

  It asks you, where you are. When an agent wants to run a command it has not
  been allowed to, the question comes to the window with the command in it --
  and to the desktop, if the window is not what you are looking at -- instead
  of waiting unseen in a tab.

  It is on the graph. Every file an agent reads or writes is reported against
  the file's place in your project, so the window can draw each agent beside
  what it is changing, and say when two of them are changing the same file
  before either is merged.

How much of that an agent gets depends on how it can be spoken to. Claude Code
holds a conversation over stdin and stdout, and asks permission through it.
Codex runs a turn per process and reports what it did as it goes. Anything
else is run once per message and its output shown as it prints; what it
changed is read back out of git afterwards.

Nothing here invents a command line. An agent is one of the programs listed
below, or one the person has written into ~/.config/cortex/agents.json; the
window can choose between them but cannot name a program of its own.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import tempfile
import threading
import time
import uuid
from collections import deque

from . import worktrees
from .places import CONFIG_DIR
from .worktrees import GitError

AGENTS_FILE = os.path.join(CONFIG_DIR, "agents.json")
STATE_FILE = os.path.join(worktrees.DATA_DIR, "agents.json")

# Programs whose command lines are known. "speaks" is how cortex talks to
# one: claude and codex have machine-readable modes; "plain" is run once per
# message, with {prompt} replaced by the message, and its output shown.
KNOWN = (
    {"id": "claude", "label": "Claude Code", "bin": "claude", "speaks": "claude"},
    {"id": "codex", "label": "Codex", "bin": "codex", "speaks": "codex"},
    {"id": "opencode", "label": "OpenCode", "bin": "opencode", "speaks": "plain",
     "argv": ["opencode", "run", "{prompt}"]},
    {"id": "gemini", "label": "Gemini", "bin": "gemini", "speaks": "plain",
     "argv": ["gemini", "-p", "{prompt}"]},
    {"id": "crush", "label": "Crush", "bin": "crush", "speaks": "plain",
     "argv": ["crush", "run", "--quiet", "{prompt}"]},
)

# What an agent may do without asking, in the words the window uses.
#   ask    every change and every command is asked about
#   edits  it may edit files; commands are asked about
#   plan   it may read and think, and change nothing
#   auto   the agent's own judgement of what is safe
PERMS = ("ask", "edits", "plan", "auto")
CLAUDE_MODE = {"ask": "manual", "edits": "acceptEdits", "plan": "plan", "auto": "auto"}
CODEX_SANDBOX = {"ask": "workspace-write", "edits": "workspace-write",
                 "plan": "read-only", "auto": "workspace-write"}

MAX_EVENTS = 4000            # kept per agent in memory; the oldest go
SAVED_EVENTS = 400           # kept per agent across a restart
MAX_SESSIONS = 40
MAX_PROMPT = 100_000         # characters; a plain agent takes it as one argument
SAY_CAP, RESULT_CAP, TITLE_CAP = 40_000, 6_000, 400
FILES_CAP = 3000             # files remembered per agent
COLORS = 8                   # the window has this many agent colours
FOCUS_FRESH = 32.0           # seconds a "the window has focus" stays believed
STATS_EVERY = 2.0            # seconds between looks at what git says changed
KILL_AFTER = 3.0             # seconds between asking a process to stop and making it

WRITES = {"Edit", "MultiEdit", "Write", "NotebookEdit"}
READS = {"Read", "NotebookRead"}
ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[@-_]")


def _clip(text, cap: int) -> str:
    text = text if isinstance(text, str) else json.dumps(text, ensure_ascii=False)
    return text if len(text) <= cap else text[:cap] + f"\n… {len(text) - cap} more characters"


def available(config: str | None = None) -> list[dict]:
    """The agents that can be started here: installed, or written into the
    config file. Best known first; one the person defined replaces a built-in
    of the same name."""
    found: dict[str, dict] = {}
    for a in KNOWN:
        exe = shutil.which(a["bin"])
        if exe:
            found[a["id"]] = {**a, "exe": exe}
    try:
        with open(config or AGENTS_FILE, "r", encoding="utf-8") as fh:
            custom = json.load(fh)
    except (OSError, ValueError):
        custom = {}
    if isinstance(custom, dict):
        for name, spec in custom.items():
            argv = spec.get("argv") if isinstance(spec, dict) else None
            if (not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", str(name))
                    or not isinstance(argv, list) or not argv
                    or not all(isinstance(x, str) for x in argv)):
                continue
            exe = shutil.which(argv[0])
            if exe:
                found[name] = {"id": name, "label": str(spec.get("label") or name)[:40],
                               "bin": argv[0], "speaks": "plain", "argv": argv,
                               "exe": exe, "custom": True}
    return list(found.values())


def name_for(prompt: str, cap: int = 48) -> str:
    """What to call a task: the start of what was asked, cut at a word."""
    first = (prompt.strip().splitlines() or [""])[0].strip()
    first = re.split(r"(?<=[.!?])\s", first, maxsplit=1)[0].rstrip(".")
    if len(first) > cap:
        first = first[:cap].rsplit(" ", 1)[0].rstrip(",;:(") + "…"
    return first or "task"


def public(agent: dict) -> dict:
    """What the window is told about an agent: not where it is on disk."""
    return {"id": agent["id"], "label": agent["label"], "speaks": agent["speaks"],
            # only a conversation can be asked a follow-up, or ask for leave
            "converses": agent["speaks"] in ("claude", "codex"),
            "asks": agent["speaks"] == "claude"}


# ----------------------------------------------------------------- reading
# What each kind of agent says, turned into the few kinds of event the window
# draws. These are plain functions of a line, so they can be tested without
# an agent.

def describe_tool(name: str, given: dict) -> dict:
    """A tool call in one line: what it is, and the file it concerns, if any."""
    given = given if isinstance(given, dict) else {}
    out = {"name": name, "title": "", "detail": ""}
    path = given.get("file_path") or given.get("notebook_path")
    if name in WRITES or name in READS:
        out["path"] = str(path or "")
        out["write"] = name in WRITES
        if name == "Edit":
            old, new = str(given.get("old_string") or ""), str(given.get("new_string") or "")
            out["detail"] = _clip("\n".join(["- " + ln for ln in old.splitlines()]
                                            + ["+ " + ln for ln in new.splitlines()]), 2000)
        elif name == "Write":
            out["detail"] = _clip(str(given.get("content") or ""), 2000)
    elif name == "Bash":
        out["title"] = _clip(str(given.get("command") or ""), TITLE_CAP)
        out["detail"] = _clip(str(given.get("description") or ""), 300)
    elif name in ("Grep", "Glob"):
        out["title"] = _clip(str(given.get("pattern") or ""), TITLE_CAP)
    elif name in ("WebFetch", "WebSearch"):
        out["title"] = _clip(str(given.get("url") or given.get("query") or ""), TITLE_CAP)
    elif name in ("Task", "Agent"):
        out["title"] = _clip(str(given.get("description") or ""), TITLE_CAP)
    else:
        out["title"] = _clip(given, 200) if given else ""
    return out


def _result_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [c.get("text", "") if isinstance(c, dict) and c.get("type") == "text"
                 else "[image]" if isinstance(c, dict) and c.get("type") == "image" else ""
                 for c in content]
        return "\n".join(p for p in parts if p)
    return ""


def read_claude(obj: dict) -> list[dict]:
    """One line of `claude --output-format stream-json`, as events."""
    kind = obj.get("type")
    out: list[dict] = []
    if kind == "system" and obj.get("subtype") == "init":
        out.append({"k": "sid", "sid": obj.get("session_id")})
    elif kind == "assistant":
        for block in (obj.get("message") or {}).get("content") or []:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text" and (block.get("text") or "").strip():
                out.append({"k": "say", "text": _clip(block["text"], SAY_CAP)})
            elif block.get("type") == "tool_use":
                out.append({"k": "tool", "id": block.get("id"),
                            **describe_tool(block.get("name") or "tool", block.get("input"))})
    elif kind == "user":
        for block in (obj.get("message") or {}).get("content") or []:
            if isinstance(block, dict) and block.get("type") == "tool_result":
                out.append({"k": "done", "id": block.get("tool_use_id"),
                            "ok": not block.get("is_error"),
                            "text": _clip(_result_text(block.get("content")), RESULT_CAP)})
    elif kind == "control_request":
        req = obj.get("request") or {}
        if req.get("subtype") == "can_use_tool":
            out.append({"k": "ask", "id": obj.get("request_id"), "input": req.get("input"),
                        **describe_tool(req.get("tool_name") or "tool", req.get("input"))})
    elif kind == "rate_limit_event":
        windows = (obj.get("rate_limit_info") or {}).get("unifiedWindows")
        if isinstance(windows, dict):
            seen = {name: {"used": w.get("utilization"), "resets": w.get("resetsAt")}
                    for name, w in windows.items()
                    if isinstance(w, dict) and isinstance(w.get("utilization"), (int, float))}
            if seen:
                out.append({"k": "usage", "windows": seen})
    elif kind == "result":
        out.append({"k": "turn", "ok": not obj.get("is_error"),
                    "cost": obj.get("total_cost_usd"), "ms": obj.get("duration_ms"),
                    "text": "" if not obj.get("is_error")
                    else _clip(str(obj.get("result") or obj.get("subtype") or ""), 600)})
    return out


def read_codex(obj: dict) -> list[dict]:
    """One line of `codex exec --json`, as events."""
    kind = obj.get("type")
    out: list[dict] = []
    if kind == "thread.started":
        out.append({"k": "sid", "sid": obj.get("thread_id")})
    elif kind in ("item.started", "item.completed"):
        item = obj.get("item") or {}
        what, done = item.get("type"), kind == "item.completed"
        if what == "agent_message" and done and (item.get("text") or "").strip():
            out.append({"k": "say", "text": _clip(item["text"], SAY_CAP)})
        elif what == "command_execution":
            if not done:
                out.append({"k": "tool", "id": item.get("id"), "name": "Bash",
                            "title": _clip(str(item.get("command") or ""), TITLE_CAP),
                            "detail": ""})
            else:
                out.append({"k": "done", "id": item.get("id"),
                            "ok": item.get("exit_code") in (0, None)
                            and item.get("status") != "failed",
                            "text": _clip(str(item.get("aggregated_output") or ""), RESULT_CAP)})
        elif what == "file_change" and done:
            for change in item.get("changes") or []:
                if isinstance(change, dict) and change.get("path"):
                    out.append({"k": "tool", "id": None, "name": "Edit", "title": "",
                                "detail": str(change.get("kind") or ""),
                                "path": str(change["path"]), "write": True})
        elif what in ("mcp_tool_call", "web_search") and not done:
            out.append({"k": "tool", "id": item.get("id"),
                        "name": str(item.get("tool") or what),
                        "title": _clip(str(item.get("query") or item.get("server") or ""),
                                       TITLE_CAP), "detail": ""})
        elif what == "error" and done:
            out.append({"k": "note", "warn": True,
                        "text": _clip(str(item.get("message") or ""), 600)})
    elif kind == "turn.completed":
        out.append({"k": "turn", "ok": True, "cost": None, "ms": None, "text": ""})
    elif kind == "turn.failed":
        out.append({"k": "turn", "ok": False, "cost": None, "ms": None,
                    "text": _clip(str((obj.get("error") or {}).get("message") or ""), 600)})
    elif kind == "error":
        out.append({"k": "note", "warn": True, "text": _clip(str(obj.get("message") or ""), 600)})
    return out


# ----------------------------------------------------------------- sessions

class Session:
    """One agent, on one task. Everything here is guarded by the pool's lock."""

    def __init__(self, agent: dict, title: str, folder: str) -> None:
        self.id = uuid.uuid4().hex[:8]
        self.agent = agent
        self.title = title
        self.folder = folder            # the folder it was asked to work on
        self.origin = folder            # what its paths are reported against
        self.tree: str | None = None    # its worktree, when it has one
        self.cwd = folder               # where the process runs
        self.repo: str | None = None
        self.branch: str | None = None
        self.base: str | None = None    # what its changes are measured from
        self.applied: str | None = None  # its copy as it stood when last applied
        self.mode = "here"
        self.perm = "ask"
        self.state = "starting"         # working | waiting | idle | failed
        self.ended: str | None = None   # applied | kept: no more can be said to it
        self.events: deque = deque(maxlen=MAX_EVENTS)
        self.seq = 0
        self.unread = False
        self.ask: dict | None = None
        self.allow: set[str] = set()    # what it need not ask about again
        self.files: dict[str, str] = {}
        self.recent: deque = deque(maxlen=16)
        self.stats: dict | None = None
        self.cost: float | None = None
        self.sid: str | None = None     # the agent's own name for the conversation
        self.group: str | None = None   # agents started together on one task
        self.color = 0
        self.started = self.updated = time.time()
        self.last = ""
        self.proc: subprocess.Popen | None = None
        self.turn_open = False
        self.stats_at = 0.0
        self.stats_busy = False
        self.tools: dict[str, dict] = {}   # tool calls waiting on their result
        self.stopped = False               # the turn is ending because it was told to

    def home(self, path: str) -> str:
        """Where a file the agent names lives in the project proper.

        An agent in a worktree reads and writes copies; the graph draws the
        originals. A path inside the worktree is that file's path in the
        repository it was copied from; any other path is itself.
        """
        full = os.path.normpath(path if os.path.isabs(path)
                                else os.path.join(self.cwd, path))
        if self.tree and (full == self.tree or full.startswith(self.tree + os.sep)):
            return os.path.normpath(os.path.join(self.repo or self.origin,
                                                 os.path.relpath(full, self.tree)))
        return full

    def summary(self) -> dict:
        return {
            "id": self.id, "agent": self.agent["id"], "label": self.agent["label"],
            "converses": self.agent["speaks"] in ("claude", "codex"),
            # whether it can be told something while it is in the middle of a turn
            "live": self.agent["speaks"] == "claude",
            "title": self.title, "folder": self.folder, "origin": self.origin,
            "tree": self.tree, "branch": self.branch, "mode": self.mode,
            "perm": self.perm, "state": self.state, "ended": self.ended,
            "seq": self.seq, "unread": self.unread,
            # that it asked, and what for -- not the whole of what it would write
            "ask": ({k: v for k, v in self.ask.items() if k != "_input"}
                    if self.ask else None),
            "files": dict(self.files), "recent": list(self.recent),
            "stats": self.stats, "cost": self.cost, "group": self.group,
            "color": self.color, "started": self.started, "updated": self.updated,
            "last": self.last, "review": self.base is not None,
            "resumable": bool(self.sid) and self.agent["speaks"] in ("claude", "codex"),
        }

    def saved(self) -> dict:
        keep = ("id", "title", "folder", "origin", "tree", "cwd", "repo", "branch",
                "base", "applied", "mode", "perm", "ended", "seq", "unread", "files", "stats",
                "cost", "sid", "group", "color", "started", "updated", "last")
        out = {k: getattr(self, k) for k in keep}
        out["agent"] = self.agent["id"]
        out["state"] = self.state if self.state in ("idle", "failed") else "idle"
        out["events"] = list(self.events)[-SAVED_EVENTS:]
        return out


class Pool:
    """Every agent cortex has started, and the one feed the window reads.

    `state` is a long poll, like the change feed: "everything, if anything is
    newer than the number I have; wait a while if not". A session's own
    events are asked for separately, by the window that has it open.
    """

    def __init__(self, notify=None, config: str | None = None,
                 state_file: str | None = None, tree_root: str | None = None) -> None:
        self._lock = threading.RLock()
        self._changed = threading.Condition(self._lock)
        self._sessions: dict[str, Session] = {}
        self._rev = 1
        self._notify = notify
        self._config = config
        self._state_file = state_file        # None: nothing is kept across a restart
        self._tree_root = tree_root          # where worktrees go; tests set it
        self._focused_at = 0.0
        self._colors = 0
        self._stopping = False
        self._listed: list[dict] | None = None
        self._listed_at = 0.0
        self._usage: dict[str, dict] = {}
        self._load()

    # -- what the window reads ---------------------------------------------

    def agents(self) -> list[dict]:
        """Which agents can be started. Looked up afresh every few seconds, so
        one installed while cortex runs turns up without a restart."""
        now = time.monotonic()
        with self._lock:
            if self._listed is None or now - self._listed_at > 8.0:
                self._listed = [public(a) for a in available(self._config)]
                self._listed_at = now
            return list(self._listed)

    def state(self, since: int = 0, wait: float = 0.0, focused: bool = False) -> dict:
        deadline = time.monotonic() + max(0.0, wait)
        with self._changed:
            if focused:
                self._focused_at = time.monotonic()
            while self._rev <= since and not self._stopping:
                left = deadline - time.monotonic()
                if left <= 0:
                    break
                self._changed.wait(left)
            sessions = [s.summary() for s in self._sessions.values()]
            rev, usage = self._rev, dict(self._usage)
        writers: dict[str, list[str]] = {}
        for s in sessions:
            if s["ended"]:
                continue
            for path, how in s["files"].items():
                if how == "w":
                    writers.setdefault(path, []).append(s["id"])
        conflicts = [{"path": p, "sessions": ids}
                     for p, ids in sorted(writers.items()) if len(ids) > 1]
        return {"rev": rev, "sessions": sessions, "conflicts": conflicts,
                "agents": self.agents(), "git": worktrees.available(), "usage": usage}

    def events(self, sid: str, after: int = 0) -> dict | None:
        with self._lock:
            s = self._sessions.get(sid)
            if s is None:
                return None
            return {"id": sid, "seq": s.seq,
                    "events": [e for e in s.events if e["seq"] > after]}

    def get(self, sid) -> Session | None:
        with self._lock:
            return self._sessions.get(sid) if isinstance(sid, str) else None

    # -- starting ------------------------------------------------------------

    def start(self, agent_id, prompt, folder: str, mode: str = "copy",
              perm: str | None = None, title: str | None = None,
              group: str | None = None) -> dict:
        prompt = prompt.strip() if isinstance(prompt, str) else ""
        if not prompt:
            return {"ok": False, "error": "say what it should do"}
        if len(prompt) > MAX_PROMPT:
            return {"ok": False, "error": "that is too long to hand to an agent in one go"}
        agent = next((a for a in available(self._config) if a["id"] == agent_id), None)
        if agent is None:
            return {"ok": False, "error": f"{agent_id} is not installed"}
        if mode not in ("copy", "here"):
            return {"ok": False, "error": f"unknown mode {mode!r}"}
        if perm is None:
            # in a copy an edit costs nothing until it is applied; in your
            # own folder it is a change to your files
            perm = "edits" if mode == "copy" else "ask"
        if perm not in PERMS:
            return {"ok": False, "error": f"unknown permission {perm!r}"}
        with self._lock:
            if len(self._sessions) >= MAX_SESSIONS:
                return {"ok": False, "error": "that is a great many agents - "
                                              "forget some finished ones first"}

        s = Session(agent, name_for(title or prompt), folder)
        s.mode, s.perm, s.group = mode, perm, group
        repo = worktrees.repo_of(folder)
        try:
            if mode == "copy":
                if repo is None:
                    return {"ok": False, "error": "this folder is not in a git repository, "
                                                  "so there is nothing to copy - let it "
                                                  "work right here instead"}
                made = worktrees.create(repo, s.title, self._tree_root)
                s.repo, s.tree, s.branch, s.base = repo, made["tree"], made["branch"], made["base"]
                s.origin = repo
                # the same folder, in the copy
                s.cwd = os.path.normpath(os.path.join(made["tree"],
                                                      os.path.relpath(folder, repo)))
                if not os.path.isdir(s.cwd):
                    s.cwd = made["tree"]
            elif repo is not None:
                s.repo, s.origin = repo, repo
                s.branch = worktrees.branch_of(repo)
                # what was already changed when it began is not its doing
                s.base = worktrees.picture(repo)
        except GitError as exc:
            return {"ok": False, "error": str(exc)}

        with self._lock:
            s.color = self._colors % COLORS
            self._colors += 1
            self._sessions[s.id] = s
            self._emit(s, {"k": "user", "text": prompt})
        self._run_turn(s, prompt)
        self._save()
        return {"ok": True, "id": s.id}

    # -- talking to it -------------------------------------------------------

    def send(self, sid, text) -> dict:
        text = text.strip() if isinstance(text, str) else ""
        s = self.get(sid)
        if s is None:
            return {"ok": False, "error": "no such agent"}
        if not text:
            return {"ok": False, "error": "say something"}
        if len(text) > MAX_PROMPT:
            return {"ok": False, "error": "that is too long to send in one go"}
        with self._lock:
            if s.ended:
                return {"ok": False, "error": "this one is finished: its work was " + s.ended}
            if s.state in ("working", "waiting") and s.agent["speaks"] != "claude":
                return {"ok": False, "error": "it is still working - wait, or stop it first"}
            if s.agent["speaks"] == "gone":
                return {"ok": False, "error": f"{s.agent['label']} is no longer installed"}
            if s.cwd and not os.path.isdir(s.cwd):
                return {"ok": False, "error": "the folder it was working in is gone"}
            self._emit(s, {"k": "user", "text": text})
            s.unread = False
        self._run_turn(s, text)
        return {"ok": True}

    def answer(self, sid, request, allow: bool, always: bool = False,
               why: str | None = None) -> dict:
        s = self.get(sid)
        if s is None:
            return {"ok": False, "error": "no such agent"}
        with self._lock:
            ask = s.ask
            if ask is None or ask["id"] != request:
                return {"ok": False, "error": "that question has already been answered"}
            if allow and always:
                s.allow.add(self._allow_key(ask))
            self._settle(s, ask, bool(allow), why)
        return {"ok": True}

    def stop(self, sid) -> dict:
        s = self.get(sid)
        if s is None:
            return {"ok": False, "error": "no such agent"}
        with self._lock:
            proc = s.proc
            if proc is None or s.state not in ("working", "waiting", "starting"):
                return {"ok": True, "was": s.state}
            s.stopped = True
            if s.ask is not None:
                self._settle(s, s.ask, False, "The person stopped the agent.")
            polite = s.agent["speaks"] == "claude"
            if polite:
                # ask it to give up the turn; the conversation stays open
                self._write(s, {"type": "control_request", "request_id": uuid.uuid4().hex,
                                "request": {"subtype": "interrupt"}})
        # not under the lock: the window reads through it, and a process can
        # take its time dying
        if polite:
            timer = threading.Timer(KILL_AFTER, self._kill_if_busy, (s, proc))
            timer.daemon = True
            timer.start()
        else:
            self._kill(proc)
        return {"ok": True}

    def seen(self, sid) -> dict:
        s = self.get(sid)
        if s is None:
            return {"ok": False, "error": "no such agent"}
        with self._lock:
            if s.unread:
                s.unread = False
                self._bump()
        return {"ok": True}

    # -- what it did -----------------------------------------------------------

    def review(self, sid, path: str | None = None) -> dict:
        s = self.get(sid)
        if s is None:
            return {"ok": False, "error": "no such agent"}
        if s.base is None:
            return {"ok": False, "error": "this folder is not in a git repository, "
                                          "so there is no before to compare with"}
        where = s.tree or s.repo
        if not where or not os.path.isdir(where):
            return {"ok": False, "error": "its copy of the project is gone"}
        try:
            if path:
                if not isinstance(path, str) or os.path.isabs(path) \
                        or ".." in path.split("/"):
                    return {"ok": False, "error": "bad path"}
                return {"ok": True, **worktrees.patch(where, s.base, path)}
            got = worktrees.changes(where, s.base)
        except GitError as exc:
            return {"ok": False, "error": str(exc)}
        for f in got["files"]:
            f["home"] = os.path.join(s.repo or s.origin, f["path"])
        return {"ok": True, "branch": s.branch, "mode": s.mode, **got}

    def apply(self, sid) -> dict:
        """Put a copy's changes into the folder it was copied from."""
        s = self.get(sid)
        if s is None:
            return {"ok": False, "error": "no such agent"}
        if not s.tree or not s.repo or s.base is None:
            return {"ok": False, "error": "it worked in your folder: its changes "
                                          "are already there"}
        if self._busy(s):
            return {"ok": False, "error": "it is still working - wait, or stop it first"}
        try:
            got = worktrees.apply_to(s.repo, s.tree, s.base)
        except GitError as exc:
            return {"ok": False, "error": str(exc)}
        if got.get("ok"):
            try:
                # so that forgetting it later knows this much is not lost
                s.applied = worktrees.picture(s.tree)
            except GitError:
                s.applied = None
            with self._lock:
                self._emit(s, {"k": "note", "text": f"applied to your folder: "
                               f"{got['files']} file{'' if got['files'] == 1 else 's'}, "
                               "uncommitted, for you to look over"})
                s.unread = False
            self._save()
        return got

    def keep(self, sid) -> dict:
        """Commit a copy's work on its branch, and let the copy go."""
        s = self.get(sid)
        if s is None:
            return {"ok": False, "error": "no such agent"}
        if not s.tree or not s.repo:
            return {"ok": False, "error": "it worked in your folder: there is no "
                                          "branch of its own to keep"}
        if self._busy(s):
            return {"ok": False, "error": "it is still working - wait, or stop it first"}
        try:
            worktrees.commit_all(s.tree, s.title)
            commits = worktrees.ahead(s.tree, s.base) if s.base else 0
            self._end_process(s)
            worktrees.remove(s.repo, s.tree)
        except GitError as exc:
            return {"ok": False, "error": str(exc)}
        with self._lock:
            s.tree, s.ended, s.state, s.unread = None, "kept", "idle", False
            self._emit(s, {"k": "note", "text": f"kept on the branch {s.branch}"
                           + (f" ({commits} commit{'' if commits == 1 else 's'})"
                              if commits else " (nothing had changed)")})
        self._save()
        return {"ok": True, "branch": s.branch, "commits": commits}

    def forget(self, sid, discard: bool = False) -> dict:
        """Take an agent off the list. A copy with work in it is only thrown
        away when that was asked for in so many words."""
        s = self.get(sid)
        if s is None:
            return {"ok": False, "error": "no such agent"}
        if s.tree and s.repo and not discard:
            try:
                dirty = bool(worktrees.changes(s.tree, s.base)["files"]) if s.base else False
                if dirty and s.applied:
                    # all of it is in your folder already, unless it has
                    # carried on since
                    dirty = worktrees.picture(s.tree) != s.applied
            except GitError:
                dirty = True
            if dirty:
                return {"ok": False, "needs": "discard",
                        "error": "it has changes that are nowhere else"}
        self._end_process(s)
        if s.tree and s.repo:
            try:
                worktrees.remove(s.repo, s.tree, s.branch)
            except GitError as exc:
                return {"ok": False, "error": str(exc)}
        with self._lock:
            self._sessions.pop(s.id, None)
            self._bump()
        self._save()
        return {"ok": True}

    def handoff(self, sid, takeover: bool = False) -> dict:
        """What to run in a terminal for this agent: a shell in its folder,
        or the agent's own interface carrying on the same conversation.

        {"ok", "argv", "cwd", "title", "kind"}. cortex lets go of the
        conversation first: two programs writing one is how they are lost.
        """
        s = self.get(sid)
        if s is None:
            return {"ok": False, "error": "no such agent"}
        if not os.path.isdir(s.cwd):
            return {"ok": False, "error": "the folder it was working in is gone"}
        if not takeover:
            shell = os.environ.get("SHELL") or "/bin/sh"
            return {"ok": True, "argv": [shell], "cwd": s.cwd, "kind": "shell",
                    "title": s.title}
        if s.ended:
            return {"ok": False, "error": "this one is finished: its work was " + s.ended}
        if s.agent["speaks"] not in ("claude", "codex"):
            return {"ok": False, "error": "this agent has no interface of its own to open"}
        if self._busy(s):
            return {"ok": False, "error": "it is still working - wait, or stop it first"}
        self._end_process(s)
        argv = [s.agent["exe"]]
        if s.sid:
            argv += ["--resume", s.sid] if s.agent["speaks"] == "claude" else ["resume", s.sid]
        with self._lock:
            self._emit(s, {"k": "note", "text": f"opened in {s.agent['label']} itself; "
                           "a message here picks the conversation up afterwards"})
        return {"ok": True, "argv": argv, "cwd": s.cwd, "kind": "agent",
                "title": f"{s.agent['label']} · {s.title}"}

    def stop_all(self) -> None:
        """cortex is quitting: nothing it started is left running."""
        with self._changed:
            self._stopping = True
            sessions = list(self._sessions.values())
            self._changed.notify_all()
        for s in sessions:
            self._end_process(s, wait=1.5)
        self._save()

    def busy(self) -> int:
        with self._lock:
            return sum(1 for s in self._sessions.values() if self._busy(s))

    # -- running ---------------------------------------------------------------

    @staticmethod
    def _busy(s: Session) -> bool:
        return s.state in ("starting", "working", "waiting")

    def _bump(self) -> None:
        self._rev += 1
        self._changed.notify_all()

    def _emit(self, s: Session, event: dict) -> dict:
        """Add to an agent's record. Caller holds the lock."""
        s.seq += 1
        event = {**event, "seq": s.seq, "t": time.time()}
        s.events.append(event)
        s.updated = event["t"]
        if event["k"] in ("say", "note") and event.get("text"):
            s.last = event["text"].strip().splitlines()[-1][:160] if event["text"].strip() else s.last
        self._bump()
        return event

    def _argv(self, s: Session, prompt: str) -> tuple[list[str], bool]:
        """The command for this turn, and whether it is spoken to on stdin."""
        a = s.agent
        if a["speaks"] == "claude":
            argv = [a["exe"], "-p", "--output-format", "stream-json",
                    "--input-format", "stream-json", "--verbose",
                    "--permission-prompt-tool", "stdio",
                    "--permission-mode", CLAUDE_MODE[s.perm]]
            return argv + (["--resume", s.sid] if s.sid else []), True
        if a["speaks"] == "codex":
            if s.sid:
                return [a["exe"], "exec", "resume", "--json", "--skip-git-repo-check",
                        s.sid, prompt], False
            return [a["exe"], "exec", "--json", "--skip-git-repo-check",
                    "-s", CODEX_SANDBOX[s.perm], prompt], False
        return [a["exe"]] + [part.replace("{prompt}", prompt) for part in a["argv"][1:]], False

    def _run_turn(self, s: Session, prompt: str) -> None:
        with self._lock:
            alive = s.proc is not None and s.proc.poll() is None
            if alive and s.agent["speaks"] == "claude":
                s.state, s.turn_open, s.stopped = "working", True, False
                self._write(s, {"type": "user",
                                "message": {"role": "user", "content": prompt}})
                self._bump()
                return
            argv, spoken = self._argv(s, prompt)
            env = dict(os.environ)
            env.update({"NO_COLOR": "1", "CORTEX_AGENT": s.id})
            try:
                proc = subprocess.Popen(
                    argv, cwd=s.cwd, env=env,
                    stdin=subprocess.PIPE if spoken else subprocess.DEVNULL,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    start_new_session=True)
            except OSError as exc:
                s.state, s.unread = "failed", True
                self._emit(s, {"k": "note", "warn": True,
                               "text": f"could not start {s.agent['label']}: "
                                       f"{exc.strerror or exc}"})
                return
            s.proc, s.state, s.turn_open, s.stopped = proc, "working", True, False
            self._bump()
            if spoken:
                self._write(s, {"type": "user",
                                "message": {"role": "user", "content": prompt}})
        tail: deque = deque(maxlen=12)
        threading.Thread(target=self._drain, args=(proc, tail), daemon=True,
                         name=f"cortex-agent-err-{s.id}").start()
        threading.Thread(target=self._listen, args=(s, proc, tail), daemon=True,
                         name=f"cortex-agent-{s.id}").start()

    @staticmethod
    def _drain(proc: subprocess.Popen, tail: deque) -> None:
        """stderr is read so the agent cannot block on it, and its last lines
        are kept to say why, if the agent dies."""
        try:
            for raw in proc.stderr:
                line = ANSI.sub("", raw.decode("utf-8", "replace")).strip()
                if line:
                    tail.append(line[:400])
        except (OSError, ValueError):
            pass
        finally:
            proc.stderr.close()

    def _write(self, s: Session, obj: dict) -> bool:
        proc = s.proc
        if proc is None or proc.stdin is None:
            return False
        try:
            proc.stdin.write((json.dumps(obj) + "\n").encode("utf-8"))
            proc.stdin.flush()
            return True
        except (OSError, ValueError):
            return False

    def _listen(self, s: Session, proc: subprocess.Popen, tail: deque) -> None:
        speaks = s.agent["speaks"]
        try:
            for raw in proc.stdout:
                line = raw.decode("utf-8", "replace").rstrip("\r\n")
                if not line.strip():
                    continue
                if speaks == "plain":
                    found = [{"k": "out", "text": _clip(ANSI.sub("", line), 4000)}]
                else:
                    try:
                        obj = json.loads(line)
                    except ValueError:
                        found = [{"k": "out", "text": _clip(ANSI.sub("", line), 4000)}]
                    else:
                        if not isinstance(obj, dict):
                            continue
                        found = (read_claude if speaks == "claude" else read_codex)(obj)
                for event in found:
                    self._take(s, event)
        except (OSError, ValueError):
            pass
        finally:
            proc.stdout.close()
        code = proc.wait()
        if proc.stdin is not None:
            try:
                proc.stdin.close()
            except (OSError, ValueError):
                pass
        with self._lock:
            # A turn-per-process agent may already have been sent its next
            # message: the turn now open is that process's, not this one's
            # to end.
            mine = s.proc is proc
            if mine:
                s.proc = None
            if mine and s.turn_open and not self._stopping:
                # it went without saying the turn was over
                why = "; ".join(list(tail)[-3:])
                if code == 0:
                    self._finish(s, True, "")
                else:
                    self._finish(s, False, why or f"{s.agent['label']} exited with status {code}")
        self._refresh(s, force=True)
        self._save()

    def _take(self, s: Session, event: dict) -> None:
        """One thing the agent said or did, into its record."""
        kind = event["k"]
        refresh = False
        with self._lock:
            if kind == "sid":
                if event.get("sid"):
                    s.sid = event["sid"]
                return
            if kind == "usage":
                # how much of the account's allowance is gone: about the
                # agent, not this task, so it is kept for all of them
                self._usage[s.agent["id"]] = {"label": s.agent["label"],
                                              "windows": event["windows"],
                                              "at": time.time()}
                self._bump()
                return
            if kind == "tool":
                self._touch(s, event)
                if event.get("id"):
                    s.tools[event["id"]] = event
                self._emit(s, event)
            elif kind == "done":
                call = s.tools.pop(event.get("id"), None)
                if call and call.get("write") and event.get("ok") and call.get("path"):
                    self._wrote(s, call["path"])
                refresh = bool(call and (call.get("write") or call.get("name") == "Bash"))
                self._emit(s, event)
            elif kind == "ask":
                # the call itself was already recorded when the agent made it
                if event.get("path"):
                    event["path"] = s.home(event["path"])
                given = event.pop("input", None)
                if self._allow_key(event) in s.allow:
                    self._respond(s, {**event, "_input": given}, True, None)
                    return
                s.ask = {**event, "_input": given}
                s.state, s.unread = "waiting", True
                self._emit(s, event)
                self._attention(s, f"{s.agent['label']} needs you — {s.title}",
                                self._ask_line(event))
            elif kind == "turn":
                if event.get("cost") is not None:
                    s.cost = event["cost"]
                self._finish(s, bool(event.get("ok")), event.get("text") or "")
                refresh = True
            else:
                self._emit(s, event)
        if refresh:
            self._refresh(s, force=kind == "turn")
            if kind == "turn":
                self._save()

    def _touch(self, s: Session, event: dict) -> None:
        """Note the file a tool call concerns, against its place in the project."""
        path = event.get("path")
        if not path:
            return
        home = s.home(path)
        event["path"] = home
        if home not in s.files and len(s.files) < FILES_CAP:
            s.files[home] = "r"
        s.recent.append([home, "w" if event.get("write") else "r", time.time()])
        # a write is counted when it has happened -- one that was refused
        # changed nothing -- unless the agent only reports them afterwards
        if event.get("write") and not event.get("id"):
            self._wrote(s, home)

    @staticmethod
    def _wrote(s: Session, home: str) -> None:
        if home in s.files or len(s.files) < FILES_CAP:
            s.files[home] = "w"

    @staticmethod
    def _allow_key(ask: dict) -> str:
        """What "do not ask again" covers: this command exactly, or this tool."""
        if ask.get("name") == "Bash":
            return "Bash:" + (ask.get("title") or "")
        return str(ask.get("name"))

    @staticmethod
    def _ask_line(ask: dict) -> str:
        if ask.get("name") == "Bash":
            return "wants to run: " + (ask.get("title") or "a command")[:200]
        if ask.get("path"):
            verb = "change" if ask.get("write") else "read"
            return f"wants to {verb} {os.path.basename(ask['path'])}"
        return f"wants to use {ask.get('name')}" + (f": {ask['title'][:160]}" if ask.get("title") else "")

    def _respond(self, s: Session, ask: dict, allow: bool, why: str | None) -> None:
        given = ask.get("_input")
        if allow:
            answer = {"behavior": "allow", "updatedInput": given if isinstance(given, dict) else {}}
        else:
            answer = {"behavior": "deny",
                      "message": (why or "").strip() or "The person declined this."}
        self._write(s, {"type": "control_response",
                        "response": {"subtype": "success", "request_id": ask["id"],
                                     "response": answer}})

    def _settle(self, s: Session, ask: dict, allow: bool, why: str | None) -> None:
        """A question answered. Caller holds the lock."""
        self._respond(s, ask, allow, why)
        s.ask = None
        s.state = "working" if s.proc is not None else s.state
        s.unread = False
        self._emit(s, {"k": "answered", "id": ask["id"], "allow": allow})

    def _finish(self, s: Session, ok: bool, text: str) -> None:
        """The end of a turn. Caller holds the lock."""
        if not s.turn_open:
            return
        s.turn_open = False
        s.ask = None
        s.tools.clear()
        if s.stopped:
            # stopped on request: not a failure, and not news to whoever asked
            s.stopped, s.state = False, "idle"
            self._emit(s, {"k": "turn", "ok": True, "text": "stopped", "cost": s.cost})
            return
        s.state = "idle" if ok else "failed"
        s.unread = True
        self._emit(s, {"k": "turn", "ok": ok, "text": text, "cost": s.cost})
        if ok:
            self._attention(s, f"{s.agent['label']} finished — {s.title}", s.last)
        else:
            self._attention(s, f"{s.agent['label']} stopped — {s.title}", text or s.last)

    def _attention(self, s: Session, title: str, body: str) -> None:
        """Tell the desktop, unless the window is what is being looked at."""
        if self._notify is None or self._stopping:
            return
        if time.monotonic() - self._focused_at < FOCUS_FRESH:
            return
        try:
            self._notify(title, (body or "")[:240])
        except Exception:                      # a notification is never worth a crash
            pass

    def _refresh(self, s: Session, force: bool = False) -> None:
        """Ask git what the agent has changed so far. Not under the lock: it
        runs git, and the window must not wait on that."""
        with self._lock:
            now = time.monotonic()
            if s.base is None or s.stats_busy or s.ended:
                return
            if not force and now - s.stats_at < STATS_EVERY:
                return
            s.stats_busy, s.stats_at = True, now
            where, base = s.tree or s.repo, s.base
        got = None
        try:
            if where and os.path.isdir(where):
                got = worktrees.changes(where, base)
        except GitError:
            got = None
        with self._lock:
            s.stats_busy = False
            if got is None:
                return
            s.stats = {"files": len(got["files"]) + got["more"],
                       "added": got["added"], "removed": got["removed"]}
            # git is the authority on what was written: a command can change
            # files without any tool having named them
            root = s.repo or s.origin
            written = {os.path.join(root, f["path"]) for f in got["files"]}
            for path in written:
                s.files[path] = "w"
            for path, how in list(s.files.items()):
                if how == "w" and path not in written and not got["more"]:
                    s.files[path] = "r"         # changed, then changed back
            self._bump()

    def _kill(self, proc: subprocess.Popen) -> None:
        """Stop a process and whatever it started."""
        for sig in (signal.SIGTERM, signal.SIGKILL):
            if proc.poll() is not None:
                return
            try:
                os.killpg(proc.pid, sig)
            except (OSError, ProcessLookupError):
                try:
                    proc.send_signal(sig)
                except (OSError, ProcessLookupError):
                    return
            try:
                proc.wait(KILL_AFTER)
            except subprocess.TimeoutExpired:
                continue

    def _kill_if_busy(self, s: Session, proc: subprocess.Popen) -> None:
        with self._lock:
            still = s.proc is proc and s.turn_open
        if still:
            self._kill(proc)

    def _end_process(self, s: Session, wait: float = KILL_AFTER) -> None:
        with self._lock:
            proc = s.proc
            if proc is None:
                return
            try:
                if proc.stdin is not None:
                    proc.stdin.close()         # a conversation ends when stdin does
            except (OSError, ValueError):
                pass
        try:
            proc.wait(0.4 if wait else 0)
        except subprocess.TimeoutExpired:
            self._kill(proc)

    # -- across a restart ------------------------------------------------------

    def _save(self) -> None:
        if not self._state_file:
            return
        with self._lock:
            data = {"colors": self._colors,
                    "sessions": [s.saved() for s in self._sessions.values()]}
        if not data["sessions"] and not os.path.exists(self._state_file):
            return                       # nothing to remember, and nothing to forget
        folder = os.path.dirname(self._state_file)
        try:
            os.makedirs(folder, exist_ok=True)
            # yours alone: it holds what you asked for and what was said back
            fd, tmp = tempfile.mkstemp(prefix=".cortex-", suffix=".tmp", dir=folder)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(data, fh)
            os.chmod(tmp, 0o600)
            os.replace(tmp, self._state_file)
        except OSError:
            pass

    def _load(self) -> None:
        if not self._state_file:
            return
        try:
            with open(self._state_file, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            return
        if not isinstance(data, dict):
            return
        known = {a["id"]: a for a in available(self._config)}
        self._colors = int(data.get("colors") or 0)
        for raw in data.get("sessions") or []:
            try:
                agent = known.get(raw["agent"]) or {
                    "id": raw["agent"], "label": raw["agent"], "speaks": "gone",
                    "bin": raw["agent"], "exe": raw["agent"]}
                s = Session(agent, str(raw["title"]), str(raw["folder"]))
                for key in ("id", "origin", "tree", "cwd", "repo", "branch", "base",
                            "applied", "mode", "perm", "ended", "seq", "unread", "files", "stats",
                            "cost", "sid", "group", "color", "started", "updated", "last"):
                    setattr(s, key, raw.get(key))
                s.state = raw.get("state") if raw.get("state") in ("idle", "failed") else "idle"
                s.files = s.files if isinstance(s.files, dict) else {}
                s.events.extend(e for e in raw.get("events") or [] if isinstance(e, dict))
                if s.tree and not os.path.isdir(s.tree):
                    s.tree = None              # the copy was removed behind our back
                    s.ended = s.ended or "gone"
            except (KeyError, TypeError, ValueError):
                continue
            self._sessions[s.id] = s
