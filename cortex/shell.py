"""The window of its own.

cortex's page is shown in a browser window with the tabs taken off, which
costs nothing to install and cannot show another site inside itself. There
is a second window it can be shown in -- desktop/, the same page in Chromium
by way of Electron -- which can: its tabs are real browser tabs. This finds
that window if it has been set up, and starts it.

It is optional, and cortex without it is cortex as it always was. Setting it
up is `npm install` in the desktop folder, once.
"""

from __future__ import annotations

import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
# beside the package in a checkout; or where an installer would put it
PLACES = (os.path.join(os.path.dirname(HERE), "desktop"),
          os.path.join(os.path.expanduser("~"), ".local", "share", "cortex", "desktop"))


def find(places=PLACES) -> list[str] | None:
    """The command that opens the window, or None when it is not set up."""
    if os.environ.get("CORTEX_NO_SHELL"):
        return None
    for app in places:
        base = os.path.join(app, "node_modules", "electron")
        if not os.path.isfile(os.path.join(app, "main.js")):
            continue
        try:
            # electron's own note of where its program is, per platform
            with open(os.path.join(base, "path.txt"), "r", encoding="utf-8") as fh:
                exe = os.path.join(base, "dist", fh.read().strip())
        except OSError:
            continue
        if os.path.isfile(exe) and os.access(exe, os.X_OK):
            return [exe, app]
    return None


def launch(url: str, argv: list[str] | None = None) -> bool:
    """Open the window on `url`. False when there is none, or it would not start.

    The address goes in the environment, not on the command line: it carries
    the token, and a command line can be read by every user on the machine.
    """
    argv = argv if argv is not None else find()
    if not argv:
        return False
    env = dict(os.environ)
    env["CORTEX_URL"] = url
    try:
        subprocess.Popen(argv, env=env, stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True)
    except OSError:
        return False
    return True
