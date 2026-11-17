"""cortex - launch the graph window and serve the terminal handoff loop."""

from __future__ import annotations

import argparse
import html
import logging
import os
import secrets
import shutil
import signal
import subprocess
import sys
import threading
import time
import webbrowser
from urllib.request import pathname2url

from . import __version__, agents, desktop, files, instances, places, shell
from .actions import ActionRunner, C
from .lifecycle import Lifecycle
from .links import LinkIndex
from .places import CONFIG_DIR, PROJECTS_FILE, load_projects, save_projects
from .scanner import Scanner
from .watch import Watcher
from .server import Context, serve

VERSION = __version__

# Chromium-family browsers can open a real app window: no tabs, no URL bar.
APP_BROWSERS = ["google-chrome", "google-chrome-stable", "brave-browser",
                "chromium", "chromium-browser", "microsoft-edge", "vivaldi"]
PLAIN_BROWSERS = ["firefox", "librewolf"]

CACHE = os.path.join(os.path.expanduser("~"), ".cache", "cortex")
LOG_FILE = os.path.join(CACHE, "cortex.log")
LOG_MAX = 1024 * 1024

# Pointing cortex at a project should show the whole project, not one ring of
# top-level folders. A home directory is far too big for that, so it stays lazy.
PROJECT_DEPTH = 3
PROJECT_BUDGET = 700


BANNER = r"""
   ___ ___  ___ _____ _____  __
  / __/ _ \| _ \_   _| ____|\ \/ /   your filesystem as a brain
 | (_| (_) |   / | | | _|    >  <    v{v}
  \___\___/|_|_\ |_| |___|  /_/\_\
"""


_short = places.short


def start_log(launched: bool) -> None:
    """Somewhere for errors to go that is not the terminal.

    From a terminal, only the server's errors are logged, because printing
    them would land on top of an editor in the foreground. From an app menu
    there is no terminal at all, so everything cortex would have printed goes
    to the same file, in plain text.
    """
    try:
        private_dir()
        if os.path.getsize(LOG_FILE) > LOG_MAX:
            os.remove(LOG_FILE)
    except OSError:
        pass
    try:
        # yours alone: from the app menu it records the window's address
        os.close(os.open(LOG_FILE, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600))
        os.chmod(LOG_FILE, 0o600)
        handler = logging.FileHandler(LOG_FILE, encoding="utf-8")
    except OSError:
        return
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger = logging.getLogger("cortex")
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    if not launched:
        # piped or redirected, print() would hold the address back in a
        # buffer until exit, which is exactly when it stops being useful
        try:
            sys.stdout.reconfigure(line_buffering=True)
        except (AttributeError, ValueError):
            pass
        return
    if launched:
        for key in C:
            C[key] = ""
        try:
            out = open(LOG_FILE, "a", buffering=1, encoding="utf-8",
                       errors="replace")
        except OSError:
            out = open(os.devnull, "w")
        sys.stdout = sys.stderr = out


def fail(message: str, launched: bool, code: int = 2) -> int:
    """Report a reason not to start, wherever someone will see it."""
    print(f"{C['red']}{message}{C['off']}", file=sys.stderr)
    if launched:
        desktop.notify("cortex could not start", message)
    return code


# Handed to the browser in place of the address. A browser's command line can
# be read by every user on the machine (ps, /proc), and the address carries the
# token -- which can now write files. This page can be read only by you, and
# forwards to the address; it is deleted once the window has loaded.
FORWARD = """<!doctype html>
<meta charset="utf-8">
<meta name="referrer" content="no-referrer">
<meta http-equiv="refresh" content="0;url={url}">
<title>cortex</title>
<style>html{{background:#070910;color:#5c6880;font:13px sans-serif}}</style>
<p>Opening cortex&hellip; <a href="{url}" style="color:#7db1ff">continue</a></p>
"""


def private_dir() -> str:
    """~/.cache/cortex, readable by you alone: it holds the log, which can hold
    the window's address, and the browser profile the window uses."""
    os.makedirs(CACHE, exist_ok=True)
    try:
        os.chmod(CACHE, 0o700)
    except OSError:
        pass
    return CACHE


def write_forward(url: str) -> str | None:
    """A file only you can read, that sends the browser on to `url`."""
    try:
        path = os.path.join(private_dir(), f"open-{os.getpid()}.html")
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(FORWARD.format(url=html.escape(url, quote=True)))
        return path
    except OSError:
        return None


def confined(exe: str) -> bool:
    """A snap-packaged browser, which cannot read hidden folders in your home
    directory, and so cannot open the forwarding page."""
    if exe.startswith("/snap/") or os.path.realpath(exe).startswith("/snap/"):
        return True
    try:
        with open(exe, "rb") as fh:
            head = fh.read(4096)
    except OSError:
        return False
    # Ubuntu's /usr/bin/firefox and chromium-browser are scripts that run snaps
    return head.startswith(b"#!") and b"/snap/" in head


def launch_window(url: str, mode: str, browser: str | None,
                  forward: str | None = None, size: str = "1560,960") -> str | None:
    """Open the UI. Returns the command name used, or None if nothing opened.

    `forward` is a private file that leads to `url`, given to the browser in
    its place where the browser can read it.
    """
    if mode == "none":
        return None
    if mode == "desktop":
        # the window of its own, where it has been set up; a browser's otherwise
        if not browser and shell.launch(url):
            return "cortex desktop"
        mode = "app"

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
        target = url if not forward or confined(exe) \
            else "file://" + pathname2url(forward)
        if mode == "app" and base not in PLAIN_BROWSERS:
            argv = [exe, f"--app={target}", f"--user-data-dir={profile}",
                    "--no-first-run", "--no-default-browser-check",
                    "--disable-features=Translate,MediaRouter",
                    f"--window-size={size}", "--class=Cortex"]
        else:
            argv = [exe, "--new-window", target]
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


def describe(status: dict) -> str:
    """What a running cortex has going on, in a few words."""
    bits = []
    if status.get("agents"):
        n = status["agents"]
        bits.append(f"{n} agent{'' if n == 1 else 's'} working")
    if status.get("terminals"):
        n, busy = status["terminals"], status.get("running", 0)
        bits.append(f"{n} terminal{'' if n == 1 else 's'}"
                    + (f" ({busy} running something)" if busy else ""))
    if not bits:
        bits.append("nothing running")
    n = status.get("windows", 0)
    bits.append("no window open" if not n else f"{n} window{'' if n == 1 else 's'} open")
    return ", ".join(bits)


def status_of(ctx) -> dict:
    busy, alive = ctx.terminals.activity() if ctx.terminals is not None else (0, 0)
    return {"terminals": alive, "running": busy,
            "agents": ctx.agents.busy() if ctx.agents is not None else 0, "windows": 0}


def join(other: dict, args, launched: bool) -> int:
    """Open a window on the cortex that is already running for this folder."""
    url = instances.url(other)
    forward = write_forward(url)
    opened = launch_window(url, args.window, args.browser, forward)
    print(f"  {C['b']}cortex{C['off']}    already running for {_short(other['root'])}"
          f"  {C['dim']}({describe(other['status'])}){C['off']}")
    if opened:
        print(f"  {C['b']}window{C['off']}    opened with {opened}  "
              f"{C['dim']}(--new starts a separate one){C['off']}")
    else:
        print(f"  {C['amber']}no browser found - open this yourself:{C['off']} {url}")
        if launched:
            desktop.notify("cortex could not open a window", "No browser was found.")
    if forward:
        # the page that leads the browser there has done its work by now
        if opened and opened != "cortex desktop":
            time.sleep(6)
        try:
            os.remove(forward)
        except OSError:
            pass
    return 0 if opened else 1


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="cortex",
        description="A force-directed brain graph of your files, "
                    "with terminal handoff to your editor.")
    p.add_argument("root", nargs="?", default=None,
                   help="directory to map (default: your home directory)")
    p.add_argument("-p", "--port", type=int, default=0,
                   help="port to bind (default: pick a free one)")
    p.add_argument("-w", "--window", choices=["desktop", "app", "tab", "none"],
                   default=None,
                   help="desktop = cortex's own window, with real browser tabs "
                        "(the default once it is set up: npm install in desktop/), "
                        "app = a browser's window with no tabs or address bar, "
                        "tab = a normal browser tab, none = just print the URL")
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
    p.add_argument("--no-watch", action="store_true",
                   help="do not notice files changing while the window is open")
    p.add_argument("--no-layout", action="store_true",
                   help="do not remember where the nodes were last time")
    p.add_argument("--read-only", action="store_true",
                   help="never change a file: no agents, editing, new, rename "
                        "or trash")

    k = p.add_argument_group("running in the background")
    k.add_argument("--no-keep", action="store_true",
                   help="closing the window always ends cortex, and what is "
                        "running in it; by default an agent at work or a "
                        "program in a terminal keeps it going")
    k.add_argument("--new", action="store_true",
                   help="start a separate cortex even if one is already "
                        "running for this folder")
    k.add_argument("--running", action="store_true",
                   help="list the cortexes running, and what is going on in "
                        "each, and exit")
    k.add_argument("--show", metavar="FILE[:LINE]",
                   help="show a file, at a line if one is given, in the cortex "
                        "already running for its folder, and exit")
    k.add_argument("--notify", metavar="TEXT",
                   help="have the running cortex tell you something -- for "
                        "scripts, and for agents' hooks -- and exit")
    k.add_argument("--stop", action="store_true",
                   help="stop the cortex running for this folder (every one, "
                        "if no folder is named), ending what runs in it, and exit")

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

    d = p.add_argument_group("app menu")
    d.add_argument("--install-launcher", action="store_true",
                   help="add cortex to your app menu, so it starts without "
                        "a terminal, and exit")
    d.add_argument("--remove-launcher", action="store_true",
                   help="take it out of the app menu again, and exit")
    # what the app-menu entry runs: no terminal, so log to a file, and with no
    # folder named, reopen the last one rather than the whole home directory
    d.add_argument("--capture", action="store_true",
                   help="a small window that adds a line to today's note, "
                        "then closes")
    d.add_argument("--launcher", action="store_true", help=argparse.SUPPRESS)
    p.add_argument("-V", "--version", action="version",
                   version=f"cortex {VERSION}")
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    if args.window is None:
        # its own window when there is one; and the quick-note window is too
        # small a thing to start a second program for
        args.window = "desktop" if shell.find() and not args.capture \
            and not args.browser else "app"
    launched = args.launcher
    projects = load_projects()

    if args.install_launcher or args.remove_launcher:
        result = desktop.install(projects) if args.install_launcher else desktop.remove()
        if not result["ok"]:
            print(f"{C['red']}{result['error']}{C['off']}", file=sys.stderr)
            return 1
        if args.install_launcher:
            print(f"added to your app menu  {C['dim']}{result['where']}{C['off']}")
        else:
            print("removed from your app menu")
        return 0

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
        desktop.refresh(projects)
        print(f"forgot {C['b']}{args.forget}{C['off']}")
        return 0

    if args.running:
        found = instances.running()
        if not found:
            print(f"{C['dim']}no cortex is running{C['off']}")
        for entry in found:
            print(f"  {C['b']}{_short(entry['root'])}{C['off']}  "
                  f"{C['dim']}{describe(entry['status'])}{C['off']}")
        return 0

    if args.show:
        name, _, tail = args.show.rpartition(":")
        line = int(tail) if name and tail.isdigit() else None
        path = os.path.realpath(os.path.expanduser(name if line else args.show))
        if not os.path.exists(path):
            print(f"{C['red']}no such file: {args.show}{C['off']}", file=sys.stderr)
            return 2
        for entry in instances.covering(path):
            got = instances.ask(entry, "/api/tell", {"kind": "open", "path": path, "line": line})
            if got and got.get("ok"):
                print(f"shown in {C['b']}{_short(entry['root'])}{C['off']}"
                      + ("" if got.get("windows") else
                         f"  {C['dim']}(no window is open on it: run cortex){C['off']}"))
                return 0
        print(f"{C['red']}no cortex is running for {_short(path)}{C['off']}", file=sys.stderr)
        return 1

    if args.notify:
        here = os.path.realpath(os.getcwd())
        found = instances.covering(here) or instances.running()
        told = 0
        for entry in found[:1] if instances.covering(here) else found:
            got = instances.ask(entry, "/api/tell", {"kind": "notify", "text": args.notify})
            told += 1 if got and got.get("ok") else 0
        if not told:
            # nothing to pass it on: say it directly, so a hook is never silent
            desktop.notify("cortex", args.notify[:300])
        return 0

    if args.stop:
        only = os.path.realpath(os.path.expanduser(args.root)) if args.root else None
        found = [e for e in instances.running() if only is None or e["root"] == only]
        if not found:
            print(f"{C['dim']}no cortex is running"
                  + (f" for {_short(only)}" if only else "") + C["off"])
            return 0
        for entry in found:
            instances.ask(entry, "/api/quit", {})
            print(f"stopped {C['b']}{_short(entry['root'])}{C['off']}  "
                  f"{C['dim']}{describe(entry['status'])}{C['off']}")
        return 0

    start_log(launched)
    home = os.path.realpath(os.path.expanduser("~"))

    label = None
    if args.project:
        if args.project not in projects:
            known = ", ".join(sorted(projects)) or "none saved"
            return fail(f"no such project: {args.project}\n"
                        f"{C['dim']}known: {known}{C['off']}", launched)
        target, label = projects[args.project], args.project
    else:
        # from the app menu, with nothing named, pick up where you left off
        target = args.root or (places.last_root() if launched else None) or home

    root = os.path.realpath(os.path.expanduser(target))
    if not os.path.isdir(root):
        return fail(f"not a directory: {target}", launched)

    if args.save:
        projects[args.save] = root
        save_projects(projects)
        desktop.refresh(projects)
        label = args.save

    # One already running for this folder -- its window closed on an agent at
    # work, or still open elsewhere -- gets a window, rather than a second
    # cortex started beside it with none of its terminals.
    if not (args.new or args.capture or args.port or args.window == "none"):
        other = instances.find(root)
        if other and bool(other.get("readOnly")) == bool(args.read_only):
            return join(other, args, launched)

    extra = {s.strip() for s in args.ignore.split(",") if s.strip()}
    runner = ActionRunner()
    token = secrets.token_urlsafe(24)
    remember = not args.no_layout

    def depth_for(folder: str) -> int:
        if args.depth is not None:
            return max(0, args.depth)
        return 0 if folder == home else PROJECT_DEPTH

    def world(folder: str) -> dict:
        """Everything that belongs to one mapped folder, built fresh.

        Called once here, and again each time the window opens another folder.
        """
        scanner = Scanner(folder, show_hidden=args.hidden, extra_ignores=extra,
                          use_gitignore=not args.no_gitignore)
        links = LinkIndex(scanner)
        # a quick note needs no map of anything
        if not args.no_links and not args.capture:
            links.start()
        return {"scanner": scanner, "links": links,
                "watcher": None if args.no_watch else Watcher(),
                "title": _short(folder),
                "ui": {"autoExpand": {"depth": depth_for(folder),
                                      "budget": max(0, args.max_nodes)}}}

    first = world(root)
    runner.home = root
    depth = depth_for(root)
    # Closing the window ends cortex, unless nobody opened a window: then the
    # page may be in a tab somewhere else, or at the far end of an SSH tunnel,
    # and cortex has no way to know it has gone.
    def still_running():
        busy, alive = ctx.terminals.activity() if ctx.terminals is not None else (0, 0)
        if ctx.agents is not None:
            busy += ctx.agents.busy()
        return "busy" if busy else "idle" if alive else None

    def tell(what: str) -> None:
        where = ctx.title
        if what == "held":
            line = f"still running in {where}: {describe(status_of(ctx))}"
            desktop.notify("cortex is still running",
                           f"{describe(status_of(ctx)).capitalize()} kept going in {where}. "
                           "Open cortex to pick it up.")
        else:
            line = f"what was running in {where} has finished; open cortex to see it"
            desktop.notify("cortex: finished", f"What was running in {where} is done. "
                                               "Open cortex to see it.")
        print(f"  {C['dim']}{line}{C['off']}")
        logging.getLogger("cortex").info(line)

    lifecycle = Lifecycle(auto_exit=args.window != "none",
                          keep=None if args.no_keep else still_running, tell=tell)
    ctx = Context(first["scanner"], first["links"], runner, token,
                  title=first["title"],
                  ui_config={**first["ui"],
                             "pulseMs": 0 if args.no_watch else 2500,
                             "rememberLayout": remember,
                             "capture": bool(args.capture)},
                  watcher=first["watcher"], remember_layout=remember,
                  lifecycle=lifecycle, read_only=args.read_only, reopen=world)
    try:
        httpd = serve(ctx, args.port)
    except OSError as exc:
        first["links"].stop()
        return fail(f"could not bind port {args.port}: {exc}", launched, 1)

    port = httpd.server_address[1]
    url = f"http://127.0.0.1:{port}/?t={token}"
    places.remember(root)
    if not args.capture:
        instances.announce(root, port, token, args.read_only)
    if args.window != "none":
        ctx.bootstrap = write_forward(url)

    threading.Thread(target=httpd.serve_forever, daemon=True,
                     name="cortex-http").start()

    # Logging out, or the desktop shutting down, ends cortex with a signal
    # rather than a key. It should leave the same way either way: a save
    # finished, and no agent left running with nobody to answer it.
    def hung_up(*_):
        # The terminal cortex was started from has closed. That ends cortex --
        # unless it is keeping something going, which closing a terminal is
        # no more a reason to end than closing the window was. It lets go of
        # the terminal instead, so that nothing it prints later can fail.
        if lifecycle.keep is not None and lifecycle.keep() == "busy":
            try:
                null = os.open(os.devnull, os.O_RDWR)
                for fd in (0, 1, 2):
                    os.dup2(null, fd)
                os.close(null)
            except OSError:
                pass
            return
        lifecycle.quit("asked to stop")

    for sig, how in ((signal.SIGTERM, lambda *_: lifecycle.quit("asked to stop")),
                     (signal.SIGHUP, hung_up)):
        try:
            signal.signal(sig, how)
        except (OSError, ValueError):
            pass

    editors = [e["label"] for e in runner.editors()]
    print(f"{C['blue']}{BANNER.format(v=VERSION)}{C['off']}")
    print(f"  {C['b']}mapping{C['off']}   {_short(root)}"
          + (f"  {C['dim']}(project: {label}){C['off']}" if label else ""))
    if depth:
        print(f"  {C['b']}opening{C['off']}   {depth} level"
              f"{'' if depth == 1 else 's'} deep, up to {args.max_nodes} nodes")
    print(f"  {C['b']}editors{C['off']}   {', '.join(editors) or C['dim'] + 'none found' + C['off']}")
    if args.read_only:
        print(f"  {C['b']}files{C['off']}     read-only")
    else:
        found = [a["label"] for a in agents.available()]
        if found:
            print(f"  {C['b']}agents{C['off']}    {', '.join(found)}")
    print(f"  {C['b']}url{C['off']}       {C['dim']}{url}{C['off']}")

    opened = launch_window(url, args.window, args.browser, ctx.bootstrap,
                           "560,300" if args.capture else "1560,960")
    if opened:
        print(f"  {C['b']}window{C['off']}    opened with {opened}")
    elif args.window == "none":
        print(f"  {C['b']}window{C['off']}    {C['dim']}not opened (--window none){C['off']}")
    else:
        print(f"  {C['amber']}no browser found - open the url above yourself{C['off']}")
        if launched:
            desktop.notify("cortex could not open a window",
                           "No browser was found. The address is in "
                           "~/.cache/cortex/cortex.log")

    if runner.tty:
        print(f"\n{C['green']}  ready.{C['off']} "
              f"{C['dim']}click a node in the window; actions land here. "
              f"close the window or ctrl-c to quit.{C['off']}")
        if lifecycle.keep is not None:
            print(f"  {C['dim']}closing the window on an agent at work, or a program "
                  f"in a terminal, leaves cortex running for it.{C['off']}")
        if desktop.supported() and not desktop.installed():
            print(f"  {C['dim']}tip: cortex --install-launcher puts it in your "
                  f"app menu, no terminal needed{C['off']}")

    try:
        runner.run_forever(until=lifecycle.should_exit)
    except KeyboardInterrupt:
        pass
    finally:
        runner.stop.set()
        instances.withdraw()
        files.quiesce()             # a save in progress finishes, whole
        httpd.shutdown()
        if ctx.agents is not None:
            ctx.agents.stop_all()       # nothing cortex started outlives it
        if ctx.terminals is not None:
            ctx.terminals.stop_all()
        ctx.links.stop()
        ctx.stop_feed()
        ctx.forget_bootstrap()
        why = f" ({lifecycle.reason})" if lifecycle.reason else ""
        print(f"\n{C['dim']}  cortex out{why}.{C['off']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
