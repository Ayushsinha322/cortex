"""A map you can send to someone: one HTML file, no cortex needed to open it.

The window is plain HTML, CSS and JavaScript that only ever talks to the
outside through fetch(). So a snapshot is the same page with its files
inlined and a small stand-in for the server (ui/snapshot.js) answering from
data frozen into the file: the folders that were open, the links, where every
node sat, and -- if you say so -- the text of the files you could see. The
online demo works the same way.

It is read-only by construction: there is no server behind it to write to.
What it contains is exactly what was asked for, and the window says so before
anything is written, because a file sent to someone is a file you cannot take
back.
"""

from __future__ import annotations

import base64
import datetime
import json
import os

from . import __version__, gitstatus, reader, tours, views

UI_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ui")

MAX_TOTAL = 24 * 1024 * 1024     # the whole file; past this, previews are left out
MAX_IMAGE = 400 * 1024           # an image is embedded only below this
MAX_FOLDERS = 3000
MAX_FILES = 6000
IMAGE_KINDS = {"image"}


def _read(name: str) -> str:
    with open(os.path.join(UI_DIR, name), "r", encoding="utf-8") as fh:
        return fh.read()


def _script(js: str) -> str:
    # inlined, a script ends at the first "</script" whatever surrounds it,
    # and "<!--" changes how the HTML parser reads what follows
    return "<script>" + js.replace("</script", "<\\/script").replace("<!--", "<\\!--") \
        + "</script>"


def _json(value) -> str:
    # every < escaped, not just "</": "<!--" inside a script changes how the
    # parser reads the rest of the page, and nothing in data needs a raw <
    return (json.dumps(value, separators=(",", ":"))
            .replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026"))


def build(scanner, links, root: str, request: dict) -> tuple[bytes, dict]:
    """The snapshot page, and a summary of what went into it.

    `root` is the folder the window was showing, which becomes the root of
    the snapshot. `request` is what the window sends: the open folders, the
    files on screen, positions and camera, and whether to include text.
    """
    inside = lambda p: isinstance(p, str) and (p == root or p.startswith(root + "/")) \
        and scanner.inside(p)

    folders = [root] + [p for p in request.get("folders") or [] if inside(p) and p != root]
    folders = list(dict.fromkeys(folders))[:MAX_FOLDERS]
    children = {}
    for folder in folders:
        if os.path.isdir(folder):
            children[folder] = scanner.children(folder)

    shown = [p for p in request.get("files") or [] if inside(p)][:MAX_FILES]
    with_text = bool(request.get("contents"))
    previews, raw, budget, left_out = {}, {}, MAX_TOTAL, 0
    for path in shown:
        if not os.path.isfile(path):
            continue
        if not with_text:
            previews[path] = {"kind": "error", "message":
                              "This snapshot holds the map, not the files."}
            continue
        p = reader.preview(path)
        if p.get("kind") in IMAGE_KINDS and p.get("size", MAX_IMAGE + 1) <= MAX_IMAGE \
                and budget > MAX_IMAGE:
            try:
                with open(path, "rb") as fh:
                    data = fh.read(MAX_IMAGE + 1)
                raw[path] = f"data:{reader.mime_for(path)};base64," + \
                    base64.b64encode(data).decode("ascii")
                budget -= len(raw[path])
            except OSError:
                pass
        elif p.get("kind") in ("pdf", "video", "audio", "image"):
            p = {"kind": "error", "message": "Not included in this snapshot."}
        cost = len(json.dumps(p))
        if cost > budget:
            left_out += 1
            p = {"kind": "error", "message": "Left out: the snapshot was full."}
        else:
            budget -= cost
        previews[path] = p

    snap = links.snapshot()
    known = set(children) | {k["id"] for kids in children.values() for k in kids}
    edges = [e for e in snap.get("edges") or []
             if inside(e[0]) and (inside(e[1]) or not e[1].startswith("/"))]
    suggest = [s for s in snap.get("suggest") or [] if inside(s[0]) and inside(s[1])]
    # the names of unwritten notes and tags only as far as the shared folder
    # uses them: a note elsewhere in the root is not part of what was shared
    named = {e[1] for e in edges}
    ghosts = {g: n for g, n in (snap.get("ghosts") or {}).items() if g in named}
    tag_names = [t for t in snap.get("tags") or [] if "tag:" + t in named]

    tour_list, tour_text = [], {}
    if with_text:
        for t in tours.list_all(scanner.root, scanner.inside):
            full = tours.read(t["path"], scanner.root, scanner.inside)
            full["stops"] = [s for s in full["stops"] if inside(s["path"])]
            if full["stops"]:
                tour_list.append(t)
                tour_text[t["path"]] = full

    positions = {k: v for k, v in (request.get("positions") or {}).items()
                 if isinstance(k, str) and (k in known or not k.startswith("/"))}
    title = request.get("title") if isinstance(request.get("title"), str) else \
        os.path.basename(root) or root
    today = datetime.date.today()
    taken = f"{today.day} {today.strftime('%B %Y')}"

    data = {
        "root": scanner.node(root),
        "children": children,
        "previews": previews,
        "raw": raw,
        "links": {"ready": True, "edges": edges, "suggest": suggest, "ghosts": ghosts,
                  "notes": snap.get("notes", 0), "sources": snap.get("sources", 0),
                  "tags": tag_names},
        "git": gitstatus.read(root, inside) if with_text else
               {"repo": None, "branch": None, "states": {}},
        "layout": {"positions": positions, "cam": request.get("cam")},
        "tours": tour_list,
        "tourText": tour_text,
    }
    config = {
        "token": "snapshot", "root": root, "title": title, "home": "",
        "readOnly": True, "terminal": None, "canQuit": False,
        "autoExpand": {"depth": 0, "budget": 0}, "pulseMs": 0,
        "rememberLayout": True,
        "snapshot": {"taken": taken, "version": __version__,
                     "contents": with_text, "files": len(previews) - left_out},
        # the way the window looked -- open folders, filters, camera -- which
        # the snapshot puts back as it opens
        "view": views.clean({**(request.get("view") or {}), "name": "shared"}, inside),
    }

    # the title goes in first, while the page is still only the template: put
    # in afterwards it would also be replaced inside the files' own text
    page = _read("index.html").replace("__TITLE__", _escape(title))
    icon = "data:image/svg+xml;base64," + base64.b64encode(
        _read("icon.svg").encode("utf-8")).decode("ascii")
    page = (page
            .replace('<link rel="icon" href="/static/icon.svg" type="image/svg+xml">',
                     f'<link rel="icon" href="{icon}" type="image/svg+xml">')
            .replace('<link rel="stylesheet" href="/static/style.css">',
                     "<style>" + _read("style.css") + "</style>")
            .replace('<script nonce="__NONCE__">window.CORTEX = __CONFIG__;</script>',
                     "<script>window.CORTEX = " + _json(config) + ";\n"
                     "window.SNAPSHOT = " + _json(data) + ";</script>")
            .replace('<script src="/static/markdown.js"></script>',
                     _script(_read("snapshot.js")) + "\n" + _script(_read("markdown.js")))
            .replace('<script src="/static/highlight.js"></script>',
                     _script(_read("highlight.js")))
            .replace('<script src="/static/app.js"></script>',
                     _script(_read("app.js"))))
    body = page.encode("utf-8")
    return body, {"bytes": len(body), "folders": len(children),
                  "files": len(previews), "left_out": left_out}


def _escape(text: str) -> str:
    return (text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                .replace('"', "&quot;"))
