"""Terminal handoff.

The graph runs in a window, but the *work* happens in the terminal you launched
from.  The UI posts an action, it lands on this queue, and the main thread runs
it in the foreground so nvim/nano/less inherit your real TTY.  Quit the editor
and you are back at the graph, still live.
"""

from __future__ import annotations

import os
import queue
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

C = {
    "dim": "\033[2m", "b": "\033[1m", "off": "\033[0m",
    "blue": "\033[38;5;39m", "green": "\033[38;5;42m",
    "amber": "\033[38;5;214m", "red": "\033[38;5;203m",
    "grey": "\033[38;5;245m",
}


def available_editors() -> list[dict]:
    found = []
    seen_labels = set()
    for group, gui in ((TUI_EDITORS, False), (GUI_EDITORS, True)):
        for eid, label, argv in group:
            if shutil.which(argv[0]) and label not in seen_labels:
                seen_labels.add(label)
                found.append({"id": eid, "label": label, "gui": gui})
    return found


def _editor_argv(eid: str) -> list[str] | None:
    for group in (TUI_EDITORS, GUI_EDITORS):
        for cand, _label, argv in group:
            if cand == eid and shutil.which(argv[0]):
                return list(argv)
    return None


def _is_gui(eid: str) -> bool:
    return any(cand == eid for cand, _l, _a in GUI_EDITORS)


def pager_argv(path: str) -> list[str]:
    """Best available read-only viewer for a path."""
    ext = os.path.splitext(path)[1].lower()
    if ext == ".pdf" and shutil.which("pdftotext"):
        return ["sh", "-c", f'pdftotext -layout "$1" - | ${{PAGER:-less}} -R', "sh", path]
    for name in ("bat", "batcat"):
        if shutil.which(name):
            return [name, "--style=numbers,header", "--paging=always", path]
    if shutil.which("less"):
        return ["less", "-R", path]
    return ["cat", path]


class ActionRunner:
    """Queue of terminal actions, drained on the main thread."""

    VALID = {"edit", "read", "shell", "open", "reveal"}

    def __init__(self) -> None:
        self.q: queue.Queue[dict] = queue.Queue()
        self.stop = threading.Event()

    # called from the HTTP thread
    def submit(self, kind: str, path: str, editor: str | None = None) -> dict:
        if kind not in self.VALID:
            return {"ok": False, "error": f"unknown action {kind!r}"}
        if not os.path.exists(path):
            return {"ok": False, "error": "path no longer exists"}
        if kind == "edit":
            if not editor:
                return {"ok": False, "error": "no editor given"}
            if _editor_argv(editor) is None:
                return {"ok": False, "error": f"{editor} is not installed"}
        self.q.put({"kind": kind, "path": path, "editor": editor})
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
        short = path.replace(os.path.expanduser("~"), "~", 1)
        is_dir = os.path.isdir(path)
        cwd = path if is_dir else os.path.dirname(path)

        if kind == "open":
            self._detach(["xdg-open", path])
            print(f"{C['grey']}  opened externally  {short}{C['off']}")
            return
        if kind == "reveal":
            self._detach(["xdg-open", cwd])
            print(f"{C['grey']}  revealed  {cwd.replace(os.path.expanduser('~'), '~', 1)}{C['off']}")
            return

        if kind == "edit":
            argv = _editor_argv(editor) + [path]
            if _is_gui(editor):
                self._detach(argv)
                print(f"{C['grey']}  handed to {editor}  {short}{C['off']}")
                return
            self._foreground(argv, f"{editor} {short}")
            return

        if kind == "read":
            if is_dir:
                self._foreground(
                    ["sh", "-c", 'ls -lAh --color=always "$1" | ${PAGER:-less} -R',
                     "sh", path],
                    f"list {short}")
            else:
                self._foreground(pager_argv(path), f"read {short}")
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
