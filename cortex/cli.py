"""cortex - launch the graph window and serve the terminal handoff loop."""

from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
import subprocess
import sys
import threading
import webbrowser

from . import __version__
from .actions import ActionRunner, available_editors, C
from .links import LinkIndex
from .scanner import Scanner
from .watch import Watcher
from .server import Context, serve

VERSION = __version__

# Chromium-family browsers can open a real app window: no tabs, no URL bar.
APP_BROWSERS = ["google-chrome", "google-chrome-stable", "brave-browser",
                "chromium", "chromium-browser", "microsoft-edge", "vivaldi"]
PLAIN_BROWSERS = ["firefox", "librewolf"]

CACHE = os.path.join(os.path.expanduser("~"), ".cache", "cortex")
CONFIG_DIR = os.path.join(os.path.expanduser("~"), ".config", "cortex")
PROJECTS_FILE = os.path.join(CONFIG_DIR, "projects.json")

# Pointing cortex at a project should show the whole project, not one ring of
# top-level folders. A home directory is far too big for that, so it stays lazy.
PROJECT_DEPTH = 3
PROJECT_BUDGET = 700


def load_projects() -> dict:
    try:
        with open(PROJECTS_FILE, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_projects(projects: dict) -> None:
    os.makedirs(CONFIG_DIR, exist_ok=True)
    with open(PROJECTS_FILE, "w", encoding="utf-8") as fh:
        json.dump(projects, fh, indent=2, sort_keys=True)

BANNER = r"""
   ___ ___  ___ _____ _____  __
  / __/ _ \| _ \_   _| ____|\ \/ /   your filesystem as a brain
 | (_| (_) |   / | | | _|    >  <    v{v}
  \___\___/|_|_\ |_| |___|  /_/\_\
"""


def _short(path: str) -> str:
    home = os.path.expanduser("~")
    return path.replace(home, "~", 1) if path.startswith(home) else path


def launch_window(url: str, mode: str, browser: str | None) -> str | None:
    """Open the UI. Returns the command name used, or None if nothing opened."""
    if mode == "none":
        return None

    profile = os.path.join(CACHE, "window")
    os.makedirs(profile, exist_ok=True)

    candidates = [browser] if browser else []
    if mode == "app":
        candidates += APP_BROWSERS + PLAIN_BROWSERS
    else:
        candidates += PLAIN_BROWSERS + APP_BROWSERS

    for name in candidates:
        exe = shutil.which(name) if name else None
        if not exe:
            continue
        base = os.path.basename(exe)
        if mode == "app" and base not in PLAIN_BROWSERS:
            argv = [exe, f"--app={url}", f"--user-data-dir={profile}",
                    "--no-first-run", "--no-default-browser-check",
                    "--disable-features=Translate,MediaRouter",
                    "--window-size=1560,960", "--class=Cortex"]
        else:
            argv = [exe, "--new-window", url]
        try:
            subprocess.Popen(argv, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, start_new_session=True)
            return base
        except OSError:
            continue

    try:
        webbrowser.open(url)
        return "default browser"
    except Exception:
        return None


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="cortex",
        description="A force-directed brain graph of your files, "
                    "with terminal handoff to your editor.")
    p.add_argument("root", nargs="?", default=os.path.expanduser("~"),
                   help="directory to map (default: your home directory)")
    p.add_argument("-p", "--port", type=int, default=0,
                   help="port to bind (default: pick a free one)")
    p.add_argument("-w", "--window", choices=["app", "tab", "none"],
                   default="app",
                   help="app = chromeless window, tab = normal browser tab, "
                        "none = just print the URL")
    p.add_argument("-b", "--browser", default=None,
                   help="force a specific browser binary")
    p.add_argument("-a", "--hidden", action="store_true",
                   help="include dotfiles and dot-directories")
    p.add_argument("--ignore", default="",
                   help="comma-separated extra directory names to skip")
    p.add_argument("--no-gitignore", action="store_true",
                   help="do not read .gitignore files; show what git hides")
    p.add_argument("--no-links", action="store_true",
                   help="skip the semantic link index (faster start)")

    g = p.add_argument_group("projects")
    g.add_argument("-P", "--project", metavar="NAME",
                   help="open a saved project by name")
    g.add_argument("--save", metavar="NAME",
                   help="save the mapped directory under NAME, then open it")
    g.add_argument("--list", action="store_true",
                   help="list saved projects and exit")
    g.add_argument("--forget", metavar="NAME",
                   help="delete a saved project and exit")
    g.add_argument("-d", "--depth", type=int, default=None, metavar="N",
                   help="folder levels to open automatically on launch "
                        f"(default: {PROJECT_DEPTH} for a project, 0 for your "
                        "home directory)")
    g.add_argument("--max-nodes", type=int, default=PROJECT_BUDGET,
                   metavar="N", help="stop auto-opening after N nodes "
                                     f"(default: {PROJECT_BUDGET})")
    p.add_argument("-V", "--version", action="version",
                   version=f"cortex {VERSION}")
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    projects = load_projects()

    if args.list:
        if not projects:
            print(f"{C['dim']}no saved projects yet - "
                  f"try: cortex ~/myproject --save myproject{C['off']}")
            return 0
        width = max(len(n) for n in projects)
        for name in sorted(projects):
            path = projects[name]
            gone = "" if os.path.isdir(path) else f"  {C['red']}(missing){C['off']}"
            print(f"  {C['b']}{name.ljust(width)}{C['off']}  "
                  f"{C['dim']}{_short(path)}{C['off']}{gone}")
        return 0

    if args.forget:
        if args.forget not in projects:
            print(f"{C['red']}no such project: {args.forget}{C['off']}",
                  file=sys.stderr)
            return 2
        projects.pop(args.forget)
        save_projects(projects)
        print(f"forgot {C['b']}{args.forget}{C['off']}")
        return 0

    label = None
    if args.project:
        if args.project not in projects:
            known = ", ".join(sorted(projects)) or "none saved"
            print(f"{C['red']}no such project: {args.project}{C['off']}\n"
                  f"{C['dim']}known: {known}{C['off']}", file=sys.stderr)
            return 2
        target, label = projects[args.project], args.project
    else:
        target = args.root

    root = os.path.realpath(os.path.expanduser(target))
    if not os.path.isdir(root):
        print(f"{C['red']}not a directory: {target}{C['off']}", file=sys.stderr)
        return 2

    if args.save:
        projects[args.save] = root
        save_projects(projects)
        label = args.save

    home = os.path.realpath(os.path.expanduser("~"))
    depth = args.depth if args.depth is not None else (0 if root == home
                                                       else PROJECT_DEPTH)
    depth = max(0, depth)

    extra = {s.strip() for s in args.ignore.split(",") if s.strip()}
    scanner = Scanner(root, show_hidden=args.hidden, extra_ignores=extra,
                      use_gitignore=not args.no_gitignore)
    links = LinkIndex(scanner)
    runner = ActionRunner()
    token = secrets.token_urlsafe(24)

    watcher = None if args.no_watch else Watcher()
    ctx = Context(scanner, links, runner, token, title=_short(root),
                  ui_config={"autoExpand": {"depth": depth,
                                            "budget": max(0, args.max_nodes)},
                             "pulseMs": 0 if args.no_watch else 2500},
                  watcher=watcher)
    try:
        httpd = serve(ctx, args.port)
    except OSError as exc:
        print(f"{C['red']}could not bind port {args.port}: {exc}{C['off']}",
              file=sys.stderr)
        return 1

    port = httpd.server_address[1]
    url = f"http://127.0.0.1:{port}/?t={token}"

    threading.Thread(target=httpd.serve_forever, daemon=True,
                     name="cortex-http").start()
    if not args.no_links:
        links.start()

    editors = [e["label"] for e in available_editors()]
    print(f"{C['blue']}{BANNER.format(v=VERSION)}{C['off']}")
    print(f"  {C['b']}mapping{C['off']}   {_short(root)}"
          + (f"  {C['dim']}(project: {label}){C['off']}" if label else ""))
    if depth:
        print(f"  {C['b']}opening{C['off']}   {depth} level"
              f"{'' if depth == 1 else 's'} deep, up to {args.max_nodes} nodes")
    print(f"  {C['b']}editors{C['off']}   {', '.join(editors) or C['dim'] + 'none found' + C['off']}")
    print(f"  {C['b']}url{C['off']}       {C['dim']}{url}{C['off']}")

    opened = launch_window(url, args.window, args.browser)
    if opened:
        print(f"  {C['b']}window{C['off']}    opened with {opened}")
    elif args.window == "none":
        print(f"  {C['b']}window{C['off']}    {C['dim']}not opened (--window none){C['off']}")
    else:
        print(f"  {C['amber']}no browser found - open the url above yourself{C['off']}")

    print(f"\n{C['green']}  ready.{C['off']} "
          f"{C['dim']}click a node in the window; actions land here. "
          f"ctrl-c to quit.{C['off']}")

    try:
        runner.run_forever()
    except KeyboardInterrupt:
        pass
    finally:
        runner.stop.set()
        httpd.shutdown()
        print(f"\n{C['dim']}  cortex out.{C['off']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
