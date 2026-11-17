"""Themes of your own, and themes you already have.

The themes that come with cortex are in the page (ui/themes.js). This finds
the rest: files in ~/.config/cortex/themes, and -- because a terminal theme
is a background, a foreground and sixteen colours, which is all a cortex
theme needs -- the themes Ghostty has, if Ghostty is installed. Hundreds of
themes people already know by name are then a list to pick from, and each
one colours the graph and the panes as well as the terminal.

Two kinds of file are read. JSON is a cortex theme:

    { "name": "Dusk", "bg": "#1b1430", "fg": "#e8e0ff", "accent": "#ff7ad9" }

`bg` and `fg` are all it needs. Anything else is a terminal theme in the
form Ghostty writes them, `key = value` a line:

    background = #1b1430
    foreground = #e8e0ff
    palette = 4=#82cfff

Nothing here decides what a colour may be: every theme is checked again in
the page before it is used, where one that would leave text unreadable is
turned away. This only reads, and only so much -- a folder of ten thousand
files is not ten thousand themes.
"""

from __future__ import annotations

import json
import os
import re

from . import places

MINE = os.path.join(places.CONFIG_DIR, "themes")
GHOSTTY = (os.path.join(places.HOME, ".config", "ghostty", "themes"),
           "/usr/share/ghostty/themes", "/usr/local/share/ghostty/themes",
           "/opt/homebrew/share/ghostty/themes",
           "/Applications/Ghostty.app/Contents/Resources/ghostty/themes")
MAX_THEMES = 600
MAX_BYTES = 16 * 1024
HEX = re.compile(r"^#?([0-9a-fA-F]{6})$")


def _hex(value: str) -> str | None:
    m = HEX.match(value.strip())
    return "#" + m.group(1).lower() if m else None


def parse_terminal(text: str, name: str) -> dict | None:
    """A terminal theme, in Ghostty's `key = value` form, as a cortex theme.
    None when it has no background and foreground to make one from."""
    colours, palette = {}, {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = (part.strip() for part in line.split("=", 1))
        if key == "palette" and "=" in value:
            index, colour = value.split("=", 1)
            if index.strip().isdigit() and 0 <= int(index) < 16 and _hex(colour):
                palette[int(index)] = _hex(colour)
        elif key in ("background", "foreground", "cursor-color", "selection-background"):
            if _hex(value):
                colours[key] = _hex(value)
    if "background" not in colours or "foreground" not in colours:
        return None
    theme = {"id": name, "name": name, "bg": colours["background"],
             "fg": colours["foreground"]}
    if len(palette) == 16:
        theme["term"] = [palette[i] for i in range(16)]
        theme["accent"] = palette[4]            # blue: what a terminal theme calls its accent
    return theme


def _read(path: str) -> dict | None:
    try:
        if os.path.getsize(path) > MAX_BYTES:
            return None
        with open(path, "r", encoding="utf-8") as fh:
            text = fh.read()
    except (OSError, UnicodeDecodeError):
        return None
    name = os.path.splitext(os.path.basename(path))[0] if path.endswith(".json") \
        else os.path.basename(path)
    if path.endswith(".json"):
        try:
            data = json.loads(text)
        except ValueError:
            return None
        if not isinstance(data, dict):
            return None
        data.setdefault("name", name)
        data["id"] = name
        return data
    return parse_terminal(text, name)


def available(mine: str | None = None, ghostty=None) -> list[dict]:
    """Every theme found, yours first, each marked with where it came from.
    A theme of yours hides one of the same name from elsewhere."""
    found: dict[str, dict] = {}
    for folder, source in [(mine or MINE, "yours")] + \
            [(g, "ghostty") for g in (GHOSTTY if ghostty is None else ghostty)]:
        try:
            names = sorted(os.listdir(folder), key=str.lower)
        except OSError:
            continue
        for name in names:
            if len(found) >= MAX_THEMES:
                return list(found.values())
            path = os.path.join(folder, name)
            if name.startswith(".") or not os.path.isfile(path):
                continue
            theme = _read(path)
            if theme is None or theme["id"] in found:
                continue
            theme["source"] = source
            found[theme["id"]] = theme
    return list(found.values())


def find(theme_id, mine: str | None = None, ghostty=None) -> dict | None:
    """One theme by name, without reading every file there is."""
    if not isinstance(theme_id, str) or "/" in theme_id or theme_id.startswith("."):
        return None
    for folder, source in [(mine or MINE, "yours")] + \
            [(g, "ghostty") for g in (GHOSTTY if ghostty is None else ghostty)]:
        for name in (theme_id + ".json", theme_id):
            path = os.path.join(folder, name)
            if os.path.isfile(path):
                theme = _read(path)
                if theme is not None:
                    theme["source"] = source
                    return theme
    return None
