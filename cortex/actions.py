"""Terminal handoff.

The graph runs in a window, but the *work* happens in the terminal you launched
from.  The UI posts an action, it lands on this queue, and the main thread runs
it in the foreground so nvim/nano/less inherit your real TTY.  Quit the editor
and you are back at the graph, still live.

Launched from an app menu there is no terminal to return to. Windowed editors
are unaffected, and a terminal editor is given a terminal window of its own,
which closes when the editor quits.
"""

from __future__ import annotations

import os
import queue
import shlex
import shutil
import subprocess
import sys
import threading
from urllib.parse import quote

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

# How each terminal is told "run this command". They do not agree, and a wrong
# guess opens an empty terminal, so only programs whose syntax is known are
# here. x-terminal-emulator is Debian's configured default, and its policy
# requires it to take -e.
TERMINALS = {
    "x-terminal-emulator": ["-e"],
    "gnome-terminal": ["--"],
    "kgx": ["--"],
    "konsole": ["-e"],
    "xfce4-terminal": ["-x"],
    "mate-terminal": ["-x"],
    "terminator": ["-x"],
    "kitty": [],
    "foot": [],
    "alacritty": ["-e"],
    "wezterm": ["start", "--"],
    "xterm": ["-e"],
    "urxvt": ["-e"],
    "st": ["-e"],
}
# The desktop's own terminal is the one people expect to see, so it goes ahead
# of whichever the distribution happened to make the default.
DESKTOP_TERMINALS = {
    "KDE": ["konsole"], "GNOME": ["gnome-terminal", "kgx"],
    "XFCE": ["xfce4-terminal"], "MATE": ["mate-terminal"],
    "X-CINNAMON": ["gnome-terminal"], "UNITY": ["gnome-terminal"],
}
# Run inside the terminal window: start in the right folder, and if the program
# fails, keep the window open long enough to read why instead of flashing an
# error and vanishing.
HOLD = ('cd "$1" 2>/dev/null; shift; "$@"; s=$?; '
        'if [ "$s" -ne 0 ]; then '
        'printf "\\n[%s exited with status %s - press enter to close]" "$1" "$s"; '
        'read -r _; fi')

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


def available_editors(tty: bool = True, terminal: bool = True) -> list[dict]:
    """Editors we can offer, best first. $VISUAL / $EDITOR always leads.

    Without a terminal of our own, windowed editors come next: someone who
    launched cortex from an app menu is better served by one than by a terminal
    window. Without even a terminal window to open, terminal editors are not
    offered at all, because choosing one could only fail.
    """
    found: list[dict] = []
    seen_labels: set[str] = set()
    seen_binaries: set[str] = set()

    usable = tty or terminal
    chosen = env_editor()
    if chosen and (chosen["gui"] or usable):
        seen_labels.add(chosen["label"])
        seen_binaries.add(chosen["binary"])
        found.append({"id": chosen["id"], "label": chosen["label"],
                      "gui": chosen["gui"], "env": True})

    groups = [(TUI_EDITORS, False), (GUI_EDITORS, True)]
    if not tty:
        groups.reverse()
    for group, gui in groups:
        if not gui and not usable:
            continue
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


def has_tty() -> bool:
    """True when there is a terminal to hand programs to."""
    try:
        return sys.stdin.isatty() and sys.stdout.isatty()
    except (AttributeError, ValueError):      # closed, or None under some launchers
        return False


def find_terminal() -> list[str] | None:
    """The command that opens a terminal window running the rest of argv."""
    if sys.platform == "darwin":
        return ["osascript"] if shutil.which("osascript") else None

    chosen = (os.environ.get("TERMINAL") or "").strip()
    if chosen:
        try:
            argv = shlex.split(chosen)
        except ValueError:
            argv = []
        if argv and shutil.which(argv[0]):
            return argv + TERMINALS.get(os.path.basename(argv[0]), ["-e"])

    desktops = (os.environ.get("XDG_CURRENT_DESKTOP") or "").upper().split(":")
    order = [t for d in desktops for t in DESKTOP_TERMINALS.get(d, [])]
    order += [t for t in TERMINALS if t not in order]
    for name in order:
        exe = shutil.which(name)
        if exe:
            return [exe] + TERMINALS[name]
    return None


def terminal_argv(terminal: list[str], argv: list[str], cwd: str,
                  hold: bool = True) -> list[str]:
    """`argv`, run in a new terminal window that starts in `cwd`."""
    if os.path.basename(terminal[0]) == "osascript":
        line = f"cd {shlex.quote(cwd)} && clear && {shlex.join(argv)}; exit"
        script = ["on run argv", 'tell application "Terminal"', "activate",
                  "do script (item 1 of argv)", "end tell", "end run"]
        out = list(terminal)
        for row in script:
            out += ["-e", row]
        return out + [line]
    if hold:
        return terminal + ["sh", "-c", HOLD, "cortex", cwd] + argv
    return terminal + ["sh", "-c", 'cd "$1" 2>/dev/null; shift; exec "$@"',
                       "cortex", cwd] + argv


class ActionRunner:
    """Queue of terminal actions, drained on the main thread."""

    VALID = {"edit", "read", "shell", "open", "reveal"}

    def __init__(self, tty: bool | None = None,
                 terminal: list[str] | None = None) -> None:
        self.q: queue.Queue[dict] = queue.Queue()
        self.stop = threading.Event()
        self.tty = has_tty() if tty is None else tty
        self.terminal = terminal if terminal is not None or self.tty \
            else find_terminal()
        # where a terminal window opens for an editor: the project, the way
        # `cd project && nvim file` would have it. Set by whoever maps a folder.
        self.home: str | None = None

    @property
    def mode(self) -> str | None:
        """Where terminal programs end up: "here", a "window", or nowhere."""
        if self.tty:
            return "here"
        return "window" if self.terminal else None

    def editors(self) -> list[dict]:
        return available_editors(tty=self.tty, terminal=bool(self.terminal))

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
        gui = kind in ("open", "reveal") or (kind == "edit" and _is_gui(editor or ""))
        if not gui and self.mode is None:
            return {"ok": False,
                    "error": "there is no terminal to run that in - pick a "
                             "windowed editor, or edit it here"}
        try:
            line = int(line) if line is not None else None
        except (TypeError, ValueError):
            line = None
        if line is not None and not 1 <= line <= 10_000_000:
            line = None
        self.q.put({"kind": kind, "path": path, "editor": editor, "line": line})
        how = "app" if gui else self.mode
        return {"ok": True, "terminal": how == "here", "how": how}

    # main thread
    def run_forever(self, until=None) -> None:
        """Run actions as they arrive, until stopped or `until()` says so."""
        while not self.stop.is_set():
            if until is not None and until():
                return
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
            self._reveal(path, is_dir)
            print(f"{C['grey']}  revealed  {short}{C['off']}")
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
            self._foreground(argv, f"{name} {where}", start=self.home or cwd)
            return

        if kind == "read":
            if is_dir:
                # BSD ls has no --color; it colours with -G when told to
                listing = ('CLICOLOR_FORCE=1 ls -lAhG "$1"' if sys.platform == "darwin"
                           else 'ls -lAh --color=always "$1"')
                self._foreground(
                    ["sh", "-c", listing + ' | ${PAGER:-less} -R', "sh", path],
                    f"list {short}", start=cwd)
            else:
                self._foreground(pager_argv(path, line),
                                 f"read {short}" + (f":{line}" if line else ""),
                                 start=cwd)
            return

        if kind == "shell":
            shell = os.environ.get("SHELL", "/bin/bash")
            self._foreground([shell], f"shell in {cwd.replace(os.path.expanduser('~'), '~', 1)}",
                             cwd=cwd, hold=False)

    # -- process helpers ---------------------------------------------------

    def _reveal(self, path: str, is_dir: bool) -> None:
        """Show it in the file manager, selected where the platform allows."""
        if sys.platform == "darwin":
            self._detach(["open", "-R", path])
            return
        # FileManager1 is how Dolphin, Nautilus, Nemo and Thunar are asked to
        # open a folder with one item selected; xdg-open can only open the folder
        if shutil.which("dbus-send"):
            try:
                done = subprocess.run(
                    ["dbus-send", "--session", "--print-reply",
                     "--dest=org.freedesktop.FileManager1", "--type=method_call",
                     "/org/freedesktop/FileManager1",
                     "org.freedesktop.FileManager1.ShowItems",
                     f"array:string:file://{quote(path)}", "string:"],
                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL, timeout=4)
                if done.returncode == 0:
                    return
            except (OSError, subprocess.SubprocessError):
                pass
        self._detach(OPENER + [path if is_dir else os.path.dirname(path)])

    def _foreground(self, argv: list[str], label: str, cwd: str | None = None,
                    hold: bool = True, start: str | None = None) -> None:
        """Run in the terminal we were launched from, or failing that, a new one.

        `cwd` is where the program runs in either case; `start` is only where a
        new terminal window opens, since one has no folder of its own to inherit.
        """
        if not self.tty:
            if not self.terminal:
                print(f"{C['red']}  no terminal to run {label} in{C['off']}")
                return
            where = cwd or start or os.path.expanduser("~")
            self._detach(terminal_argv(self.terminal, argv, where, hold), cwd=where)
            print(f"{C['grey']}  {label}  (in a terminal window){C['off']}")
            return
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

    def _detach(self, argv: list[str], cwd: str | None = None) -> None:
        try:
            subprocess.Popen(argv, stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             start_new_session=True, cwd=cwd)
        except OSError as exc:
            print(f"{C['red']}  could not start {argv[0]}: {exc}{C['off']}")
