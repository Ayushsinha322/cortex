#!/usr/bin/env python3
"""Tests for what the window remembers between launches, and for the themes
found on disk. Standard library only.

Run: python3 tests/test_prefs.py   (or tests/run)

Everything is pointed at a throwaway folder first; nothing here reads or
writes the real ~/.config/cortex.
"""

import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from cortex import prefs, themes
from cortex.actions import ActionRunner
from cortex.lifecycle import Lifecycle
from cortex.links import LinkIndex
from cortex.scanner import Scanner
from cortex.server import Context, serve
from cortex.watch import Watcher

HARBOR = """# a terminal theme
background = #0f1a24
foreground = d5e3ee
cursor-color = #7fd1c7
font-size = 14
""" + "".join(f"palette = {i}=#{i:x}{i:x}aa{i:x}{i:x}\n" for i in range(16))


def write(path, text=""):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return path


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="cortex-prefs-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        old = (prefs.FILE, themes.MINE, themes.GHOSTTY)
        prefs.FILE = f"{self.tmp}/cfg/prefs.json"
        themes.MINE = f"{self.tmp}/cfg/themes"
        themes.GHOSTTY = (f"{self.tmp}/ghostty",)
        self.addCleanup(lambda: (setattr(prefs, "FILE", old[0]), setattr(themes, "MINE", old[1]),
                                 setattr(themes, "GHOSTTY", old[2])))


class TestPrefs(Base):
    def test_what_is_kept_is_there_the_next_time(self):
        self.assertEqual(prefs.load(), {})
        prefs.update({"theme": "oled", "panel": 640.4, "rail": "shut", "follow": True})
        self.assertEqual(prefs.load(), {"theme": "oled", "panel": 640, "rail": "shut",
                                        "follow": True})
        prefs.update({"light": "paper"})
        self.assertEqual(prefs.load()["theme"], "oled")         # one change leaves the rest

    def test_only_known_keys_of_the_right_kind(self):
        got = prefs.update({"theme": "x" * 200, "panel": "wide", "rail": "ajar", "follow": "yes",
                            "shell": "/bin/sh", "__proto__": {"a": 1}, "preview": "file:///etc/passwd",
                            "light": "../../etc/passwd"})
        self.assertEqual(got, {})
        self.assertEqual(prefs.update(["not", "a", "dict"]), {})
        got = prefs.update({"preview": "http://localhost:3000/", "panel": 99999})
        self.assertEqual(got, {"preview": "http://localhost:3000/"})
        got = prefs.update({"layout": "sideways"})
        self.assertNotIn("layout", got)
        self.assertEqual(prefs.update({"layout": "work"})["layout"], "work")

    def test_a_colour_for_each_folder_that_has_one(self):
        got = prefs.update({"accents": {"/home/u/a": "#FB923C", "/home/u/b": "red",
                                        "relative": "#000000", "/home/u/c": "#00000"}})
        self.assertEqual(got["accents"], {"/home/u/a": "#fb923c"})
        self.assertEqual(prefs.update({"accents": {}})["accents"], {})

    def test_a_value_that_is_not_sound_clears_the_one_there(self):
        prefs.update({"theme": "oled"})
        self.assertNotIn("theme", prefs.update({"theme": None}))

    def test_a_file_someone_broke_is_started_over(self):
        write(prefs.FILE, "{ not json")
        self.assertEqual(prefs.load(), {})
        write(prefs.FILE, json.dumps({"theme": "oled", "panel": True, "junk": 1}))
        self.assertEqual(prefs.load(), {"theme": "oled"})


class TestThemesOnDisk(Base):
    def test_a_terminal_theme_is_read_as_a_whole_theme(self):
        got = themes.parse_terminal(HARBOR, "Harbor")
        self.assertEqual((got["bg"], got["fg"], got["name"]), ("#0f1a24", "#d5e3ee", "Harbor"))
        self.assertEqual(len(got["term"]), 16)
        self.assertEqual(got["term"][3], "#33aa33")
        self.assertEqual(got["accent"], got["term"][4])

    def test_one_without_a_background_is_not_one(self):
        self.assertIsNone(themes.parse_terminal("foreground = #ffffff\n", "x"))
        self.assertIsNone(themes.parse_terminal("just some text\n", "x"))
        short = themes.parse_terminal("background = #000000\nforeground = #ffffff\n"
                                      "palette = 1=#ff0000\npalette = 99=#00ff00\n", "x")
        self.assertNotIn("term", short)          # a palette of one colour is not a palette

    def test_yours_first_then_what_ghostty_has(self):
        write(f"{themes.MINE}/Dusk.json", json.dumps({"bg": "#1b1430", "fg": "#e8e0ff"}))
        write(f"{themes.MINE}/Harbor", HARBOR)
        write(f"{themes.MINE}/.hidden", HARBOR)
        write(f"{themes.MINE}/notes.json", "[1, 2]")
        write(f"{self.tmp}/ghostty/Harbor", HARBOR.replace("0f1a24", "000000"))
        write(f"{self.tmp}/ghostty/Catppuccin Mocha", HARBOR)
        got = themes.available()
        self.assertEqual([(t["id"], t["source"]) for t in got],
                         [("Dusk", "yours"), ("Harbor", "yours"), ("Catppuccin Mocha", "ghostty")])
        self.assertEqual(got[0]["name"], "Dusk")
        self.assertEqual(got[1]["bg"], "#0f1a24")        # yours hides the one of the same name

    def test_one_by_name_and_nothing_by_a_path(self):
        write(f"{themes.MINE}/Dusk.json", json.dumps({"bg": "#1b1430", "fg": "#e8e0ff"}))
        write(f"{self.tmp}/ghostty/Harbor", HARBOR)
        self.assertEqual(themes.find("Dusk")["source"], "yours")
        self.assertEqual(themes.find("Harbor")["source"], "ghostty")
        self.assertIsNone(themes.find("Nope"))
        self.assertIsNone(themes.find("../prefs"))
        self.assertIsNone(themes.find(None))

    def test_a_file_too_big_to_be_a_theme_is_passed_over(self):
        write(f"{themes.MINE}/Huge", HARBOR + "#" * 20000)
        self.assertEqual(themes.available(), [])
        self.assertEqual(themes.available(mine=f"{self.tmp}/no-such-folder"), [])


class TestOverHTTP(Base):
    def setUp(self):
        super().setUp()
        root = f"{self.tmp}/proj"
        write(f"{root}/a.md", "# a\n")
        scanner = Scanner(root)
        self.ctx = Context(scanner, LinkIndex(scanner), ActionRunner(tty=True), "tok",
                           title="t", watcher=Watcher(), lifecycle=Lifecycle(grace=0),
                           read_only=True)
        self.httpd = serve(self.ctx, 0)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, args=(0.05,), daemon=True).start()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)

    def get(self, path):
        with urlopen(f"http://127.0.0.1:{self.port}{path}", timeout=5) as r:
            return r.read().decode()

    def post(self, payload):
        req = Request(f"http://127.0.0.1:{self.port}/api/prefs?t=tok",
                      data=json.dumps(payload).encode(),
                      headers={"content-type": "application/json"})
        try:
            with urlopen(req, timeout=5) as r:
                return r.status, json.loads(r.read())
        except HTTPError as exc:
            return exc.code, json.loads(exc.read() or b"null")

    def test_the_page_is_handed_how_it_was_left_and_its_theme(self):
        write(f"{themes.MINE}/Harbor", HARBOR)
        # kept even though cortex is read-only: it is not a change to anyone's files
        status, got = self.post({"set": {"theme": "Harbor", "panel": 700, "evil": "x"}})
        self.assertEqual((status, got["prefs"]), (200, {"theme": "Harbor", "panel": 700}))
        page = self.get("/?t=tok")
        cfg = json.loads(page.split("window.CORTEX = ")[1].split(";</script>")[0])
        # ...and, with nothing said about it, the window opens on the workspace
        self.assertEqual(cfg["prefs"], {"theme": "Harbor", "panel": 700, "layout": "work"})
        self.assertEqual([t["id"] for t in cfg["themes"]], ["Harbor"])
        # whoever left it on the graph gets the graph
        status, got = self.post({"set": {"layout": "graph"}})
        self.assertEqual((status, got["prefs"]["layout"]), (200, "graph"))
        page = self.get("/?t=tok")
        cfg = json.loads(page.split("window.CORTEX = ")[1].split(";</script>")[0])
        self.assertEqual(cfg["prefs"]["layout"], "graph")
        self.assertEqual(cfg["themes"][0]["bg"], "#0f1a24")
        self.assertIn('<script src="/static/themes.js"></script>', page)

    def test_the_list_of_themes_and_what_is_refused(self):
        write(f"{themes.MINE}/Dusk.json", json.dumps({"bg": "#1b1430", "fg": "#e8e0ff"}))
        got = json.loads(self.get("/api/themes?t=tok"))
        self.assertEqual([t["id"] for t in got["themes"]], ["Dusk"])
        with self.assertRaises(HTTPError) as refused:
            self.get("/api/themes")
        self.assertEqual(refused.exception.code, 403)
        self.assertEqual(self.post({"set": "theme=oled"})[1]["prefs"], {})


if __name__ == "__main__":
    unittest.main(verbosity=1)
