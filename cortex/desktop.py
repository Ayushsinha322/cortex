"""Putting cortex in the app menu, so it can be started without a terminal.

`cortex --install-launcher`, or the button in the window, writes:

  Linux   ~/.local/share/applications/cortex.desktop   the menu entry
          ~/.local/share/cortex/launch                  what it runs
          ~/.local/share/cortex/cortex.svg              its icon
  macOS   ~/Applications/Cortex.app                     a minimal app bundle

The launch script carries the PATH, $EDITOR and $VISUAL of whatever installed
it. An app menu does not read ~/.bashrc, so without them an editor installed
in ~/.local/bin or /opt/homebrew would not be found, and the editor someone
chose would quietly become whichever one came first.

Removing only ever touches files that say cortex wrote them.
"""

from __future__ import annotations

import os
import plistlib
import re
import shlex
import shutil
import subprocess
import sys

from . import __version__

HOME = os.path.expanduser("~")
ICON = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ui", "icon.svg")
BUNDLE_ID = "io.github.ayushsinha322.cortex"
MARKER = "X-Cortex-Launcher=1"
CARRIED = ("PATH", "EDITOR", "VISUAL", "TERMINAL")


def _data_home() -> str:
    return os.environ.get("XDG_DATA_HOME") or os.path.join(HOME, ".local", "share")


def paths() -> dict:
    if sys.platform == "darwin":
        app = os.path.join(HOME, "Applications", "Cortex.app")
        return {"entry": app, "script": os.path.join(app, "Contents", "MacOS", "cortex"),
                "icon": None}
    base = _data_home()
    return {"entry": os.path.join(base, "applications", "cortex.desktop"),
            "script": os.path.join(base, "cortex", "launch"),
            "icon": os.path.join(base, "cortex", "cortex.svg")}


def supported() -> bool:
    return sys.platform == "darwin" or sys.platform.startswith("linux")


def installed() -> bool:
    entry = paths()["entry"]
    if sys.platform == "darwin":
        return _ours_bundle(entry)
    return _ours_file(entry)


def status() -> dict:
    return {"supported": supported(), "installed": supported() and installed(),
            "where": _short(paths()["entry"])}


# -- what the launcher runs ---------------------------------------------------

def launch_command() -> list[str]:
    """How to start this very cortex again, from anywhere."""
    pkg = os.path.dirname(os.path.abspath(__file__))
    checkout = os.path.join(os.path.dirname(pkg), "cortex.py")
    if os.path.isfile(checkout):             # running from a clone
        return [sys.executable, checkout]
    return [sys.executable, "-m", "cortex"]


def script_text(env=None) -> str:
    env = os.environ if env is None else env
    lines = [
        "#!/bin/sh",
        "# Starts cortex from the app menu. Written by `cortex --install-launcher`,",
        "# which recorded the settings below from the shell it ran in, because an",
        "# app menu does not read your shell's startup files. Install again to",
        "# refresh them, or edit them here.",
    ]
    for var in CARRIED:
        value = env.get(var)
        if not value:
            continue
        if var == "PATH":
            lines.append(f'PATH={shlex.quote(value)}"${{PATH:+:$PATH}}"; export PATH')
        else:
            lines.append(f"{var}={shlex.quote(value)}; export {var}")
    lines.append(f'exec {shlex.join(launch_command())} --launcher "$@"')
    return "\n".join(lines) + "\n"


# -- the menu entry -----------------------------------------------------------

def _exec_arg(arg: str) -> str:
    """Quote one argument for a desktop entry's Exec key, per the spec."""
    arg = arg.replace("%", "%%")
    if re.fullmatch(r"[\w@%+=:,./-]+", arg):
        quoted = arg
    else:
        quoted = '"' + re.sub(r'(["`$\\])', r"\\\1", arg) + '"'
    return quoted.replace("\\", "\\\\")        # the string-level escape comes last


def _entry_value(text: str) -> str:
    return re.sub(r"[\r\n\t]", " ", text)


def desktop_entry(script: str, icon: str, projects: dict | None = None) -> str:
    """The .desktop file. Right-click on its icon offers a quick note, then
    each saved project."""
    names = sorted(projects or {})[:12]
    actions = ["capture"] + [f"project-{i}" for i in range(len(names))]
    out = [
        "[Desktop Entry]",
        "Type=Application",
        "Version=1.5",
        "Name=cortex",
        "GenericName=File Graph",
        "Comment=Your files as a graph you can read and edit",
        f"Exec={_exec_arg(script)}",
        f"Icon={icon}",
        "Terminal=false",
        "Categories=Utility;",
        "Keywords=graph;notes;files;markdown;wikilinks;brain;",
        # the window is a Chrome app window launched with --class=Cortex, and
        # this is how the dock knows that window belongs to this icon
        "StartupWMClass=Cortex",
        "StartupNotify=false",
        MARKER,
    ]
    out.append("Actions=" + ";".join(actions) + ";")
    out += ["", "[Desktop Action capture]", "Name=Quick note",
            f"Exec={_exec_arg(script)} --capture"]
    for action, name in zip(actions[1:], names):
        out += ["", f"[Desktop Action {action}]", f"Name={_entry_value(name)}",
                f"Exec={_exec_arg(script)} -P {_exec_arg(name)}"]
    return "\n".join(out) + "\n"


def _ours_file(path: str) -> bool:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return MARKER in fh.read()
    except OSError:
        return False


def _ours_bundle(app: str) -> bool:
    try:
        with open(os.path.join(app, "Contents", "Info.plist"), "rb") as fh:
            return plistlib.load(fh).get("CFBundleIdentifier") == BUNDLE_ID
    except (OSError, ValueError, plistlib.InvalidFileException):
        return False


def _write(path: str, text: str, mode: int = 0o644) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.chmod(tmp, mode)
    os.replace(tmp, path)


# -- install / remove ---------------------------------------------------------

def install(projects: dict | None = None, env=None) -> dict:
    if not supported():
        return {"ok": False, "error": "only Linux and macOS have a launcher"}
    p = paths()
    try:
        if sys.platform == "darwin":
            if os.path.exists(p["entry"]) and not _ours_bundle(p["entry"]):
                return {"ok": False, "error": f"{_short(p['entry'])} is "
                                              "something else; not replacing it"}
            plist = {
                "CFBundleName": "Cortex", "CFBundleDisplayName": "cortex",
                "CFBundleIdentifier": BUNDLE_ID, "CFBundleExecutable": "cortex",
                "CFBundlePackageType": "APPL", "CFBundleVersion": __version__,
                "CFBundleShortVersionString": __version__,
                # the window is the browser's; without this the script would
                # also sit in the dock as an app that never draws anything
                "LSUIElement": True,
            }
            os.makedirs(os.path.join(p["entry"], "Contents", "MacOS"), exist_ok=True)
            with open(os.path.join(p["entry"], "Contents", "Info.plist"), "wb") as fh:
                plistlib.dump(plist, fh)
            _write(p["script"], script_text(env), 0o755)
        else:
            if os.path.exists(p["entry"]) and not _ours_file(p["entry"]):
                return {"ok": False, "error": f"{_short(p['entry'])} is "
                                              "something else; not replacing it"}
            _write(p["script"], script_text(env), 0o755)
            os.makedirs(os.path.dirname(p["icon"]), exist_ok=True)
            shutil.copyfile(ICON, p["icon"])
            _write(p["entry"], desktop_entry(p["script"], p["icon"], projects))
    except OSError as exc:
        return {"ok": False, "error": f"could not write the launcher: {exc}"}
    return {"ok": True, "where": _short(p["entry"])}


def refresh(projects: dict) -> None:
    """Rewrite the menu entry's project list, if there is a menu entry."""
    if sys.platform.startswith("linux") and installed():
        p = paths()
        try:
            _write(p["entry"], desktop_entry(p["script"], p["icon"], projects))
        except OSError:
            pass


def remove() -> dict:
    p = paths()
    try:
        if sys.platform == "darwin":
            if _ours_bundle(p["entry"]):
                shutil.rmtree(p["entry"])
        else:
            if _ours_file(p["entry"]):
                os.remove(p["entry"])
            for extra in (p["script"], p["icon"]):
                if extra and os.path.isfile(extra):
                    os.remove(extra)
    except OSError as exc:
        return {"ok": False, "error": f"could not remove the launcher: {exc}"}
    return {"ok": True}


# -- telling someone something with no terminal to print to --------------------

def notify(title: str, message: str) -> None:
    """A desktop notification, best effort. Never raises."""
    if sys.platform == "darwin":
        argv = ["osascript", "-e", "on run argv",
                "-e", "display notification (item 2 of argv) with title (item 1 of argv)",
                "-e", "end run", title, message]
    elif shutil.which("notify-send"):
        argv = ["notify-send", "--app-name=cortex", title, message]
    else:
        return
    try:
        subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)
    except OSError:
        pass


def _short(path: str) -> str:
    return "~" + path[len(HOME):] if path == HOME or path.startswith(HOME + "/") else path
