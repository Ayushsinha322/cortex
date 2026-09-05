"""Terminal handoff.

The graph runs in a window, but the *work* happens in the terminal you launched
from.  The UI posts an action, it lands on this queue, and the main thread runs
it in the foreground so nvim/nano/less inherit your real TTY.  Quit the editor
and you are back at the graph, still live.
"""

from __future__ import annotations

import os
import queue
import shlex
import shutil
import subprocess
import sys
import threading

# Editors that live in the terminal -- run in the foreground, on our TTY.
TUI_EDITORS = [
    ("nvim", "Neovim", ["nvim"]),
    ("vim", "Vim", ["vim"]),
    ("nano", "nano", ["nano"]),
    ("micro", "micro", ["micro"]),
    ("hx", "Helix", ["hx"]),
    ("helix", "Helix", ["helix"]),
    ("kak", "Kakoune", ["kak"]),
    ("emacs", "Emacs", ["emacs", "-nw"]),
    ("vi", "vi", ["vi"]),
]

# Editors that open their own window -- spawn detached, don't block.
GUI_EDITORS = [
    ("code", "VS Code", ["code"]),
    ("codium", "VSCodium", ["codium"]),
    ("zed", "Zed", ["zed"]),
    ("subl", "Sublime Text", ["subl"]),
    ("gedit", "gedit", ["gedit"]),
    ("kate", "Kate", ["kate"]),
    ("mousepad", "Mousepad", ["mousepad"]),
]

# The editor named by $VISUAL / $EDITOR is offered first, under this id.  It is
# resolved at call time rather than at import, so changing the variable and
# relaunching is enough.
ENV_EDITOR_ID = "env"

# What `open ⧉` and `reveal` hand a path to, per platform.
OPENER = ["open"] if sys.platform == "darwin" else ["xdg-open"]

# How each editor is told to jump to a line. Anything not listed is opened at
# the top of the file rather than guessed at: an unknown flag would stop the
# editor from opening at all, which is a worse failure than losing the line.
LINE_STYLE = {
    "nvim": "plus", "vim": "plus", "vi": "plus", "nano": "plus",
    "micro": "plus", "emacs": "plus", "kak": "plus", "gedit": "plus",
    "hx": "colon", "helix": "colon", "zed": "colon", "subl": "colon",
    "code": "goto", "codium": "goto",
    "kate": "kate", "mousepad": "mousepad",
}

C = {
    "dim": "\033[2m", "b": "\033[1m", "off": "\033[0m",
    "blue": "\033[38;5;39m", "green": "\033[38;5;42m",
    "amber": "\033[38;5;214m", "red": "\033[38;5;203m",
    "grey": "\033[38;5;245m",
}


def _known_label(binary: str) -> tuple[str, bool] | None:
    """(label, is_gui) for a binary we already know about, else None."""
    for group, gui in ((TUI_EDITORS, False), (GUI_EDITORS, True)):
        for _eid, label, argv in group:
            if argv[0] == binary:
                return label, gui
    return None


def env_editor() -> dict | None:
    """Whatever $VISUAL / $EDITOR names, if we can actually run it.

    Honouring this is table stakes for a terminal tool: someone who has set
    `EDITOR=hx` should not be handed Neovim because our list happens to start
    there.  The value is a command line, so `EDITOR="emacs -nw"` works too.
    """
    for var in ("VISUAL", "EDITOR"):
        raw = (os.environ.get(var) or "").strip()
        if not raw:
            continue
        try:
            argv = shlex.split(raw)
        except ValueError:                       # unbalanced quotes
            continue
        if not argv or not shutil.which(argv[0]):
            continue
        binary = os.path.basename(argv[0])
        known = _known_label(binary)
        label, gui = known if known else (binary, False)
        return {"id": ENV_EDITOR_ID, "label": label, "gui": gui,
                "env": True, "argv": argv, "var": var, "binary": binary}
    return None


def available_editors() -> list[dict]:
    """Editors we can offer, best first. $VISUAL / $EDITOR always leads."""
    found: list[dict] = []
    seen_labels: set[str] = set()
    seen_binaries: set[str] = set()

    chosen = env_editor()
    if chosen:
        seen_labels.add(chosen["label"])
        seen_binaries.add(chosen["binary"])
        found.append({"id": chosen["id"], "label": chosen["label"],
                      "gui": chosen["gui"], "env": True})

    for group, gui in ((TUI_EDITORS, False), (GUI_EDITORS, True)):
        for eid, label, argv in group:
            if label in seen_labels or argv[0] in seen_binaries:
                continue
            if shutil.which(argv[0]):
                seen_labels.add(label)
                seen_binaries.add(argv[0])
                found.append({"id": eid, "label": label, "gui": gui,
                              "env": False})
    return found


def _editor_argv(eid: str) -> list[str] | None:
    if eid == ENV_EDITOR_ID:
        chosen = env_editor()
        return list(chosen["argv"]) if chosen else None
    for group in (TUI_EDITORS, GUI_EDITORS):
        for cand, _label, argv in group:
            if cand == eid and shutil.which(argv[0]):
                return list(argv)
    return None


def _is_gui(eid: str) -> bool:
    if eid == ENV_EDITOR_ID:
        chosen = env_editor()
        return bool(chosen and chosen["gui"])
    return any(cand == eid for cand, _l, _a in GUI_EDITORS)


def open_at_line(binary: str, argv: list[str], path: str,
                 line: int | None) -> list[str]:
    """Finish an editor command line, jumping to `line` when we know how."""
    style = LINE_STYLE.get(os.path.basename(binary))
    if not line or line < 1 or not style:
        return argv + [path]
    if style == "plus":
        return argv + [f"+{line}", path]
    if style == "colon":
        return argv + [f"{path}:{line}"]
    if style == "goto":
        return argv + ["-g", f"{path}:{line}"]
    if style == "kate":
        return argv + ["-l", str(line), path]
    if style == "mousepad":
        return argv + [f"--line={line}", path]
    return argv + [path]


def pager_argv(path: str, line: int | None = None) -> list[str]:
    """Best available read-only viewer for a path."""
    ext = os.path.splitext(path)[1].lower()
    if ext == ".pdf" and shutil.which("pdftotext"):
        return ["sh", "-c", f'pdftotext -layout "$1" - | ${{PAGER:-less}} -R', "sh", path]
    for name in ("bat", "batcat"):
        if shutil.which(name):
            argv = [name, "--style=numbers,header", "--paging=always"]
            if line and line > 0:
                argv += [f"--highlight-line={line}", f"--line-range={max(1, line - 20)}:"]
            return argv + [path]
    if shutil.which("less"):
        return ["less", "-R"] + ([f"+{line}"] if line and line > 0 else []) + [path]
    return ["cat", path]


class ActionRunner:
    """Queue of terminal actions, drained on the main thread."""

    VALID = {"edit", "read", "shell", "open", "reveal"}

    def __init__(self) -> None:
        self.q: queue.Queue[dict] = queue.Queue()
        self.stop = threading.Event()

    # called from the HTTP thread
    def submit(self, kind: str, path: str, editor: str | None = None,
               line=None) -> dict:
        if kind not in self.VALID:
            return {"ok": False, "error": f"unknown action {kind!r}"}
        if not os.path.exists(path):
            return {"ok": False, "error": "path no longer exists"}
        if kind == "edit":
            if not editor:
                return {"ok": False, "error": "no editor given"}
            if _editor_argv(editor) is None:
                return {"ok": False, "error": f"{editor} is not installed"}
        try:
            line = int(line) if line is not None else None
        except (TypeError, ValueError):
            line = None
        if line is not None and not 1 <= line <= 10_000_000:
            line = None
        self.q.put({"kind": kind, "path": path, "editor": editor, "line": line})
        gui = kind == "open" or (kind == "edit" and _is_gui(editor or ""))
        return {"ok": True, "terminal": not gui}

    # main thread
    def run_forever(self) -> None:
        while not self.stop.is_set():
            try:
                job = self.q.get(timeout=0.4)
            except queue.Empty:
                continue
            try:
                self._run(job)
            except Exception as exc:                    # never die on one action
                print(f"{C['red']}  action failed: {exc}{C['off']}")

    def _run(self, job: dict) -> None:
        kind, path, editor = job["kind"], job["path"], job.get("editor")
        line = job.get("line")
        short = path.replace(os.path.expanduser("~"), "~", 1)
        is_dir = os.path.isdir(path)
        cwd = path if is_dir else os.path.dirname(path)

        if kind == "open":
            self._detach(OPENER + [path])
            print(f"{C['grey']}  opened externally  {short}{C['off']}")
            return
        if kind == "reveal":
            self._detach(OPENER + [cwd])
            print(f"{C['grey']}  revealed  {cwd.replace(os.path.expanduser('~'), '~', 1)}{C['off']}")
            return

        if kind == "edit":
            argv = _editor_argv(editor)
            if argv is None:
                print(f"{C['red']}  {editor} is not installed{C['off']}")
                return
            name = os.path.basename(argv[0])
            argv = open_at_line(argv[0], argv, path, line)
            where = f"{short}" + (f":{line}" if line else "")
            if _is_gui(editor):
                self._detach(argv)
                print(f"{C['grey']}  handed to {name}  {where}{C['off']}")
                return
            self._foreground(argv, f"{name} {where}")
            return

        if kind == "read":
            if is_dir:
                self._foreground(
                    ["sh", "-c", 'ls -lAh --color=always "$1" | ${PAGER:-less} -R',
                     "sh", path],
                    f"list {short}")
            else:
                self._foreground(pager_argv(path, line),
                                 f"read {short}" + (f":{line}" if line else ""))
            return

        if kind == "shell":
            shell = os.environ.get("SHELL", "/bin/bash")
            self._foreground([shell], f"shell in {cwd.replace(os.path.expanduser('~'), '~', 1)}",
                             cwd=cwd)

    # -- process helpers ---------------------------------------------------

    def _foreground(self, argv: list[str], label: str, cwd: str | None = None) -> None:
        print(f"\n{C['blue']}┌─ {C['b']}{label}{C['off']}"
              f"{C['dim']}  (quit to return to the graph){C['off']}")
        sys.stdout.flush()
        try:
            subprocess.call(argv, cwd=cwd)
        except FileNotFoundError:
            print(f"{C['red']}  {argv[0]} not found{C['off']}")
            return
        except KeyboardInterrupt:
            pass
        print(f"{C['blue']}└─ {C['off']}{C['dim']}back at the graph{C['off']}\n")
        sys.stdout.flush()

    def _detach(self, argv: list[str]) -> None:
        try:
            subprocess.Popen(argv, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, start_new_session=True)
        except FileNotFoundError:
            print(f"{C['red']}  {argv[0]} not found{C['off']}")
