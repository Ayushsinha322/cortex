#!/usr/bin/env python3
"""Tests for named views, tours, the daily note and quick capture, sharing a
map as one file, dismissed suggestions, and putting any file on the graph.

Run: python3 tests/test_features.py   (or tests/run)

Every test works in its own throwaway directory, with the config and cache
folders pointed there too.
"""

import datetime
import json
import os
import re
import sys
import tempfile
import unittest
from urllib.parse import quote

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

from cortex import desktop, dismissed, files, journal, snapshot, tours, views
from cortex import cli
from cortex.links import LinkIndex
from cortex.scanner import Scanner
from test_app import Served, patch, read, temp_dir
from test_cortex import Tree, write


# ------------------------------------------------------------------ views

class TestViews(unittest.TestCase):
    def setUp(self):
        self.dir = temp_dir(self)
        patch(self, views, "VIEWS_DIR", f"{self.dir}/views")
        self.root = "/p"
        self.inside = lambda p: isinstance(p, str) and (p == "/p" or p.startswith("/p/"))

    def view(self, name, **extra):
        v = {"name": name, "scope": "/p/web", "hidden": ["media"], "solo": None,
             "semantic": True, "labels": False, "expanded": ["/p/web", "/p/web/src"],
             "selected": "/p/web/a.js", "cam": {"s": 1.5, "x": 10, "y": -4}}
        v.update(extra)
        return v

    def test_a_view_comes_back_as_it_was_saved(self):
        views.save(self.root, self.view("frontend"), self.inside)
        got = views.load(self.root)[0]
        self.assertEqual(got["name"], "frontend")
        self.assertEqual(got["scope"], "/p/web")
        self.assertEqual(got["hidden"], ["media"])
        self.assertFalse(got["labels"])
        self.assertEqual(got["cam"], {"s": 1.5, "x": 10.0, "y": -4.0})

    def test_saving_the_same_name_replaces_it_in_its_slot(self):
        views.save(self.root, self.view("a"), self.inside)
        views.save(self.root, self.view("b"), self.inside)
        views.save(self.root, self.view("A", labels=True), self.inside)
        names = [v["name"] for v in views.load(self.root)]
        self.assertEqual(names, ["A", "b"])

    def test_paths_outside_the_folder_are_dropped(self):
        views.save(self.root, self.view("x", scope="/etc", expanded=["/etc", "/p/ok"],
                                        selected="/etc/passwd"), self.inside)
        got = views.load(self.root)[0]
        self.assertIsNone(got["scope"])
        self.assertEqual(got["expanded"], ["/p/ok"])
        self.assertIsNone(got["selected"])

    def test_a_view_needs_a_name(self):
        with self.assertRaises(ValueError):
            views.save(self.root, self.view("  "), self.inside)

    def test_a_camera_that_is_not_a_camera_is_left_out(self):
        views.save(self.root, self.view("x", cam={"s": "big"}), self.inside)
        self.assertIsNone(views.load(self.root)[0]["cam"])

    def test_each_folder_has_its_own_views(self):
        views.save(self.root, self.view("a"), self.inside)
        self.assertEqual(views.load("/other"), [])

    def test_forget_and_move(self):
        for n in "abc":
            views.save(self.root, self.view(n), self.inside)
        views.move(self.root, "c", 0)
        self.assertEqual([v["name"] for v in views.load(self.root)], ["c", "a", "b"])
        views.forget(self.root, "a")
        self.assertEqual([v["name"] for v in views.load(self.root)], ["c", "b"])


# ------------------------------------------------------------------ tours

class TestTours(unittest.TestCase):
    def setUp(self):
        self.root = temp_dir(self)
        write(f"{self.root}/cortex/cli.py", "x = 1\n")
        write(f"{self.root}/cortex/server.py", "y = 2\n")
        write(f"{self.root}/my notes/a b.md", "# a\n")
        self.inside = lambda p: p == self.root or p.startswith(self.root + "/")

    def test_a_tour_round_trips_through_markdown(self):
        stops = [{"path": f"{self.root}/cortex/cli.py", "note": "Start here.\n\nThen *this*."},
                 {"path": f"{self.root}/cortex/server.py", "line": 120, "note": "The routes."},
                 {"path": f"{self.root}/my notes/a b.md", "note": ""}]
        path = tours.write(self.root, "Getting around", "Where things are.", stops, self.inside)
        self.assertEqual(path, f"{self.root}/.tours/getting-around.md")
        tour = tours.read(path, self.root, self.inside)
        self.assertEqual(tour["title"], "Getting around")
        self.assertEqual(tour["intro"], "Where things are.")
        self.assertEqual([s["path"] for s in tour["stops"]], [s["path"] for s in stops])
        self.assertEqual(tour["stops"][0]["note"], "Start here.\n\nThen *this*.")
        self.assertEqual(tour["stops"][1]["line"], 120)
        self.assertFalse(any(s["missing"] for s in tour["stops"]))

    def test_the_file_reads_properly_on_github(self):
        path = tours.write(self.root, "T", "", [
            {"path": f"{self.root}/cortex/server.py", "line": 7, "note": "n"},
            {"path": f"{self.root}/my notes/a b.md", "note": ""}], self.inside)
        text = read(path)
        self.assertIn("1. [cortex/server.py, line 7](../cortex/server.py#L7)", text)
        # a space in a link target must be encoded, or the link is not a link
        self.assertIn("(../my%20notes/a%20b.md)", text)

    def test_a_hand_written_tour_is_understood(self):
        os.makedirs(f"{self.root}/.tours")
        write(f"{self.root}/.tours/hand.md", "# By hand\n\nIntro line.\n\n"
              "1) [the cli](../cortex/cli.py) — first\n   more about it\n\n"
              "2. [a website](https://example.com) not a stop\n"
              "3. [server](<../cortex/server.py>)\n"
              "4. [escape](../../etc/passwd)\n")
        tour = tours.read(f"{self.root}/.tours/hand.md", self.root, self.inside)
        self.assertEqual(tour["title"], "By hand")
        self.assertEqual([os.path.basename(s["path"]) for s in tour["stops"]],
                         ["cli.py", "server.py"])
        self.assertEqual(tour["stops"][0]["note"], "— first\nmore about it")

    def test_a_note_can_hold_lists_links_and_code(self):
        note = ("Read these first:\n\n1. [the docs](https://example.com)\n"
                "2. then this\n\n    code_block()\n\n1. [a.md](a.md) is mentioned, not a stop\n\n"
                "More after.")
        stops = [{"path": f"{self.root}/cortex/cli.py", "note": "    starts with code\nthen text"},
                 {"path": f"{self.root}/cortex/server.py", "note": note}]
        path = tours.write(self.root, "T", "", stops, self.inside)
        got = tours.read(path, self.root, self.inside)["stops"]
        self.assertEqual(len(got), 2)
        self.assertEqual(got[0]["note"], "    starts with code\nthen text")
        self.assertEqual(got[1]["note"], note)

    def test_a_stop_whose_file_has_gone_is_kept_and_marked(self):
        path = tours.write(self.root, "T", "", [{"path": f"{self.root}/cortex/cli.py"}], self.inside)
        os.remove(f"{self.root}/cortex/cli.py")
        self.assertTrue(tours.read(path, self.root, self.inside)["stops"][0]["missing"])

    def test_two_tours_with_one_title_do_not_overwrite(self):
        a = tours.write(self.root, "Same", "", [], self.inside)
        b = tours.write(self.root, "Same", "", [], self.inside)
        self.assertNotEqual(a, b)

    def test_listing(self):
        tours.write(self.root, "Zeta", "", [{"path": f"{self.root}/cortex/cli.py"}], self.inside)
        tours.write(self.root, "alpha", "first line\nsecond", [], self.inside)
        got = tours.list_all(self.root, self.inside)
        self.assertEqual([t["title"] for t in got], ["alpha", "Zeta"])
        self.assertEqual(got[0]["intro"], "first line")
        self.assertEqual(got[1]["stops"], 1)

    def test_an_existing_tour_is_rewritten_where_it_is(self):
        path = tours.write(self.root, "T", "", [], self.inside)
        again = tours.write(self.root, "Renamed", "", [], self.inside, path)
        self.assertEqual(again, path)
        self.assertEqual(tours.read(path, self.root, self.inside)["title"], "Renamed")


# ------------------------------------------------------------ daily notes

class TestJournal(unittest.TestCase):
    def setUp(self):
        self.root = temp_dir(self)
        self.day = datetime.date(2026, 10, 9)

    def test_todays_note_is_made_once(self):
        first = journal.open_today(self.root, self.day)
        self.assertTrue(first["created"])
        self.assertEqual(first["path"], f"{self.root}/daily/2026-10-09.md")
        self.assertEqual(read(first["path"]), "# Friday, 9 October 2026\n\n")
        self.assertFalse(journal.open_today(self.root, self.day)["created"])

    def test_an_existing_journal_folder_is_used(self):
        os.makedirs(f"{self.root}/Journal")
        self.assertEqual(journal.open_today(self.root, self.day)["path"],
                         f"{self.root}/Journal/2026-10-09.md")

    def test_a_capture_is_added_with_the_time(self):
        at = datetime.datetime(2026, 10, 9, 9, 5)
        journal.capture(self.root, "  call the bank  ", at)
        journal.capture(self.root, "two lines\nof thought", at.replace(hour=14))
        text = read(f"{self.root}/daily/2026-10-09.md")
        self.assertTrue(text.endswith("- 09:05 call the bank\n- 14:05 two lines\n  of thought\n"),
                        repr(text))

    def test_a_capture_after_a_note_with_no_final_newline_starts_a_line(self):
        path = journal.open_today(self.root, self.day)["path"]
        with open(path, "a") as fh:
            fh.write("typed without a newline")
        journal.capture(self.root, "x", datetime.datetime(2026, 10, 9, 8, 0))
        self.assertTrue(read(path).endswith("typed without a newline\n- 08:00 x\n"))

    def test_nothing_to_capture_is_refused(self):
        with self.assertRaises(files.FileError):
            journal.capture(self.root, "   ")


# ------------------------------------------------------------ lineage

class TestLineage(Tree):
    def test_a_file_on_disk_comes_with_its_folders(self):
        hit = self.scanner.lineage(f"{self.root}/notes/sub/deep.md")
        self.assertEqual([a["name"] for a in hit["ancestors"]], ["notes", "sub"])
        self.assertNotIn("missing", hit["node"])

    def test_a_file_that_is_gone_comes_back_marked(self):
        hit = self.scanner.lineage(f"{self.root}/old/gone/file.md")
        self.assertTrue(hit["node"]["missing"])
        self.assertEqual(hit["node"]["group"], "note")
        self.assertTrue(all(a["dir"] and a["missing"] for a in hit["ancestors"]))

    def test_nothing_outside_the_root(self):
        self.assertIsNone(self.scanner.lineage("/etc/passwd"))
        self.assertIsNone(self.scanner.lineage(f"{self.root}/../x"))
        self.assertIsNone(self.scanner.lineage("relative/path"))

    def test_not_through_a_symlink_leading_out(self):
        outside = temp_dir(self)
        write(f"{outside}/secret.md")
        os.symlink(outside, f"{self.root}/door")
        self.assertIsNone(self.scanner.lineage(f"{self.root}/door/secret.md"))


class TestNestedCreate(unittest.TestCase):
    def setUp(self):
        self.root = temp_dir(self)
        self.inside = lambda p: os.path.realpath(p).startswith(self.root)

    def test_a_note_in_folders_that_do_not_exist_yet(self):
        got = files.create(self.root, "projects/2026/Roadmap", parents=True, inside=self.inside)
        self.assertEqual(got["path"], f"{self.root}/projects/2026/Roadmap.md")
        self.assertTrue(os.path.isfile(got["path"]))

    def test_without_parents_a_slash_is_still_refused(self):
        with self.assertRaises(files.FileError):
            files.create(self.root, "projects/Roadmap")

    def test_no_climbing_out(self):
        for bad in ("../escape/x", "a/../../x", "a/./../x"):
            with self.subTest(bad=bad), self.assertRaises(files.FileError):
                files.create(self.root, bad, parents=True, inside=self.inside)
        self.assertEqual(os.listdir(os.path.dirname(self.root)).count("escape"), 0)

    def test_not_through_a_symlinked_folder_leading_out(self):
        outside = temp_dir(self)
        os.symlink(outside, f"{self.root}/door")
        with self.assertRaises(files.FileError):
            files.create(self.root, "door/planted", parents=True, inside=self.inside)
        self.assertEqual(os.listdir(outside), [])


class TestPythonPackageImports(unittest.TestCase):
    """Inside a package, files import each other by name: `from . import x`."""

    def setUp(self):
        self.root = temp_dir(self)
        r = self.root
        write(f"{r}/pkg/__init__.py", "")
        write(f"{r}/pkg/cli.py", "from . import server, grep as g\nfrom .util import helper\n")
        write(f"{r}/pkg/server.py", "from . import (\n    reader,\n    layout,\n)\n")
        write(f"{r}/pkg/reader.py", "")
        write(f"{r}/pkg/layout.py", "")
        write(f"{r}/pkg/grep.py", "")
        write(f"{r}/pkg/util.py", "def helper(): pass\n")
        write(f"{r}/pkg/sub/__init__.py", "")
        write(f"{r}/pkg/sub/mod.py", "")
        write(f"{r}/pkg/main.py", "from .sub import mod\nfrom .sub import nothing_here\n")
        idx = LinkIndex(Scanner(self.root))
        idx._build()
        self.edges = {(os.path.relpath(a, r), os.path.relpath(b, r))
                      for a, b, kind in idx.snapshot()["edges"] if kind == "code"}

    def test_from_dot_import_names_the_modules(self):
        self.assertIn(("pkg/cli.py", "pkg/server.py"), self.edges)
        self.assertIn(("pkg/cli.py", "pkg/grep.py"), self.edges)

    def test_a_parenthesised_list_across_lines(self):
        self.assertIn(("pkg/server.py", "pkg/reader.py"), self.edges)
        self.assertIn(("pkg/server.py", "pkg/layout.py"), self.edges)

    def test_a_name_that_is_not_a_module_links_to_its_module(self):
        self.assertIn(("pkg/cli.py", "pkg/util.py"), self.edges)

    def test_a_submodule_beats_its_package(self):
        self.assertIn(("pkg/main.py", "pkg/sub/mod.py"), self.edges)

    def test_so_a_path_runs_through_the_package(self):
        # cli -> server -> reader: the chain the path finder follows
        self.assertTrue({("pkg/cli.py", "pkg/server.py"),
                         ("pkg/server.py", "pkg/reader.py")} <= self.edges)


class TestNothingLeavesThroughASymlink(Served):
    """Folders that already exist are used as they are -- so one that is a
    symlink, from a cloned repository say, must not lead the write out."""

    def setUp(self):
        super().setUp()
        self.outside = temp_dir(self, "cortex-outside-")

    def test_a_daily_folder_leading_out_is_not_written_into(self):
        os.symlink(self.outside, f"{self.root}/daily")
        for route, body in (("/api/daily", {}), ("/api/capture", {"text": "x"})):
            status, got = self.post(f"{route}?t=tok", body)
            self.assertFalse(got["ok"], route)
        self.assertEqual(os.listdir(self.outside), [])

    def test_todays_note_leading_out_is_not_appended_to(self):
        import datetime
        victim = write(f"{self.outside}/victim.rc", "safe\n")
        os.makedirs(f"{self.root}/daily")
        os.symlink(victim, f"{self.root}/daily/{datetime.date.today().isoformat()}.md")
        _, got = self.post("/api/capture?t=tok", {"text": "pwned"})
        self.assertFalse(got["ok"])
        self.assertEqual(read(victim), "safe\n")

    def test_a_tour_file_leading_out_is_not_read(self):
        secret = write(f"{self.outside}/secret.md", "# TOP SECRET\n")
        os.makedirs(f"{self.root}/.tours")
        os.symlink(secret, f"{self.root}/.tours/x.md")
        status, body = self.get(f"/api/tour?t=tok&path={quote(self.root + '/.tours/x.md')}")
        self.assertEqual(status, 404)
        self.assertNotIn(b"TOP SECRET", body)


class TestSnapshotInjection(Tree):
    def page(self, title_root=None, **request):
        links = LinkIndex(self.scanner)
        links._build()
        root = title_root or self.root
        page, _ = snapshot.build(self.scanner, links, root, request)
        return page.decode("utf-8")

    def test_file_text_cannot_climb_out_through_the_title(self):
        odd = f"{self.root}/docs\\"
        os.makedirs(odd)
        evil = write(f"{odd}/n.md", '__TITLE__"}}};alert(1)//')
        page = self.page(odd, contents=True, files=[evil], folders=[odd])
        # the data is still one well-formed JSON value, holding the text as it
        # is: nothing was substituted into it, and nothing got out of it
        data = json.loads(page.split("window.SNAPSHOT = ")[1].split(";</script>")[0])
        self.assertEqual(data["previews"][evil]["text"], '__TITLE__"}}};alert(1)//')
        cfg = json.loads(page.split("window.CORTEX = ")[1].split(";\n")[0])
        self.assertEqual(cfg["title"], "docs\\")

    def test_a_comment_opener_in_a_file_does_not_unbalance_the_page(self):
        evil = write(f"{self.root}/notes/evil.md", "<!--<script>alert(1)</script>")
        page = self.page(contents=True, files=[evil])
        data = page.split("window.SNAPSHOT = ")[1].split(";</script>")[0]
        self.assertNotIn("<", data)

    def test_unwritten_notes_elsewhere_are_not_shared(self):
        write(f"{self.root}/private/diary.md", "see [[Divorce lawyer]] #secret-tag-xyz")
        write(f"{self.root}/notes/plan.md", "see [[Roadmap]]")
        page = self.page(f"{self.root}/notes", folders=[f"{self.root}/notes"])
        self.assertNotIn("Divorce lawyer", page)
        self.assertNotIn("secret-tag-xyz", page)
        self.assertIn("Roadmap", page)


class TestEncodedLinks(unittest.TestCase):
    def test_a_percent_encoded_relative_link_resolves(self):
        root = temp_dir(self)
        write(f"{root}/a.md", "See also [my note](my%20note.md) and [r](r%281%29.md)")
        write(f"{root}/my note.md")
        write(f"{root}/r(1).md")
        idx = LinkIndex(Scanner(root))
        idx._build()
        targets = {os.path.basename(b) for a, b, k in idx.snapshot()["edges"] if k == "note"}
        self.assertEqual(targets, {"my note.md", "r(1).md"})


class TestDismissed(unittest.TestCase):
    def setUp(self):
        patch(self, dismissed, "DIR", temp_dir(self))

    def test_a_dismissed_pair_stays_gone_either_way_round(self):
        suggest = [["/r/a.md", "/r/b.md", 0.5, ["x"]], ["/r/a.md", "/r/c.md", 0.4, ["y"]]]
        dismissed.add("/r", "/r/b.md", "/r/a.md")
        self.assertEqual(dismissed.without("/r", suggest), [suggest[1]])
        self.assertEqual(dismissed.without("/elsewhere", suggest), suggest)


# --------------------------------------------------------------- snapshot

class TestSnapshot(Tree):
    def build(self, **request):
        links = LinkIndex(self.scanner)
        links._build()
        req = {"folders": [f"{self.root}/notes", f"{self.root}/app"],
               "files": [f"{self.root}/notes/index.md", f"{self.root}/app/main.py"],
               "positions": {f"{self.root}/notes": [1, 2]}, "cam": {"s": 1, "x": 0, "y": 0}}
        req.update(request)
        page, summary = snapshot.build(self.scanner, links, self.root, req)
        return page.decode("utf-8"), summary

    def data(self, page):
        return json.loads(page.split("window.SNAPSHOT = ")[1].split(";</script>")[0]
                          .replace("<\\/", "</"))

    def test_it_is_one_file_with_nothing_fetched_from_anywhere(self):
        page, _ = self.build()
        self.assertNotIn('src="/static/', page)
        self.assertNotIn('href="/static/', page)
        self.assertIn("window.SNAPSHOT", page)
        self.assertIn("window.CORTEX", page)

    def test_without_contents_no_text_goes_in(self):
        page, _ = self.build()
        self.assertNotIn("see [[deep]]", page)
        prev = self.data(page)["previews"][f"{self.root}/notes/index.md"]
        self.assertEqual(prev["kind"], "error")

    def test_with_contents_the_text_goes_in(self):
        page, summary = self.build(contents=True)
        prev = self.data(page)["previews"][f"{self.root}/notes/index.md"]
        self.assertEqual(prev["kind"], "markdown")
        self.assertIn("[[deep]]", prev["text"])
        self.assertEqual(summary["files"], 2)

    def test_nothing_outside_the_view_goes_in(self):
        outside = temp_dir(self)
        write(f"{outside}/secret.md", "TOP SECRET")
        page, _ = self.build(contents=True, files=[f"{outside}/secret.md"],
                             folders=[outside])
        self.assertNotIn("TOP SECRET", page)

    def test_a_note_cannot_end_the_script_it_is_carried_in(self):
        write(f"{self.root}/notes/evil.md", "</script><script>alert(1)</script>")
        page, _ = self.build(contents=True, files=[f"{self.root}/notes/evil.md"])
        self.assertNotIn("</script><script>alert(1)", page)
        self.assertIn("alert(1)", page)

    def test_it_is_read_only_and_says_when_it_was_taken(self):
        page, _ = self.build()
        cfg = json.loads(page.split("window.CORTEX = ")[1].split(";\n")[0])
        self.assertTrue(cfg["readOnly"])
        self.assertIsNone(cfg["terminal"])
        self.assertIn("taken", cfg["snapshot"])

    def test_the_way_it_looked_rides_along(self):
        page, _ = self.build(view={"expanded": [f"{self.root}/notes", "/etc"],
                                   "hidden": ["media"], "labels": False})
        cfg = json.loads(page.split("window.CORTEX = ")[1].split(";\n")[0])
        self.assertEqual(cfg["view"]["expanded"], [f"{self.root}/notes"])
        self.assertEqual(cfg["view"]["hidden"], ["media"])
        self.assertFalse(cfg["view"]["labels"])

    def test_the_layout_rides_along(self):
        page, _ = self.build()
        self.assertEqual(self.data(page)["layout"]["positions"][f"{self.root}/notes"], [1, 2])


# ---------------------------------------------------------------- the API

class TestFeatureRoutes(Served):
    def setUp(self):
        super().setUp()
        patch(self, views, "VIEWS_DIR", f"{self.state}/views")
        patch(self, dismissed, "DIR", f"{self.state}/dismissed")

    def test_nodes_for_paths_anywhere(self):
        status, got = self.post("/api/nodes?t=tok", {"paths": [
            f"{self.root}/notes/sub/deep.md", f"{self.root}/gone.md", "/etc/passwd"]})
        self.assertEqual(status, 200)
        ids = [h["node"]["id"] for h in got["hits"]]
        self.assertEqual(ids, [f"{self.root}/notes/sub/deep.md", f"{self.root}/gone.md"])
        self.assertTrue(got["hits"][1]["node"]["missing"])

    def test_views_over_http(self):
        _, got = self.post("/api/views?t=tok", {"op": "save", "view": {
            "name": "notes", "scope": f"{self.root}/notes", "expanded": [f"{self.root}/notes"]}})
        self.assertTrue(got["ok"])
        _, listed = self.getj("/api/views?t=tok")
        self.assertEqual(listed["views"][0]["scope"], f"{self.root}/notes")

    def test_tours_over_http(self):
        _, saved = self.post("/api/tours?t=tok", {"op": "save", "title": "Intro", "stops": [
            {"path": f"{self.root}/notes/index.md", "note": "hello"},
            {"path": "/etc/passwd", "note": "nope"}]})
        self.assertTrue(saved["ok"], saved)
        _, listed = self.getj("/api/tours?t=tok")
        self.assertEqual(listed["tours"][0]["stops"], 1)
        _, tour = self.getj(f"/api/tour?t=tok&path={quote(saved['path'])}")
        self.assertEqual(tour["stops"][0]["note"], "hello")

    def test_a_tour_must_live_in_the_tours_folder(self):
        status, _ = self.getj(f"/api/tour?t=tok&path={quote(self.root + '/notes/index.md')}")
        self.assertEqual(status, 404)
        _, got = self.post("/api/tours?t=tok", {"op": "save", "title": "x", "stops": [],
                                                "path": f"{self.root}/notes/index.md"})
        self.assertFalse(got["ok"])
        self.assertEqual(read(f"{self.root}/notes/index.md").startswith("# Index"), True)

    def test_daily_and_capture(self):
        _, today = self.post("/api/daily?t=tok", {})
        self.assertTrue(os.path.isfile(today["path"]))
        _, cap = self.post("/api/capture?t=tok", {"text": "a thought"})
        self.assertEqual(cap["path"], today["path"])
        self.assertIn("a thought", read(today["path"]))

    def test_a_ghost_note_can_be_made_in_new_folders(self):
        _, got = self.post("/api/fs?t=tok", {"op": "new", "path": self.root,
                                             "name": "plans/Roadmap", "parents": True})
        self.assertTrue(got["ok"], got)
        self.assertTrue(os.path.isfile(f"{self.root}/plans/Roadmap.md"))

    def test_a_shared_map_comes_back_as_a_download(self):
        req_body = json.dumps({"files": [f"{self.root}/notes/index.md"]}).encode()
        from urllib.request import Request, urlopen
        req = Request(f"http://127.0.0.1:{self.port}/api/snapshot?t=tok", data=req_body,
                      headers={"content-type": "application/json"})
        with urlopen(req, timeout=10) as r:
            self.assertIn("attachment", r.headers["Content-Disposition"])
            summary = json.loads(r.headers["X-Cortex-Summary"])
            page = r.read()
        self.assertGreater(summary["bytes"], 10000)
        self.assertIn(b"window.SNAPSHOT", page)

    def test_a_dismissed_suggestion_is_not_suggested_again(self):
        self.links.suggest = []
        a, b = f"{self.root}/notes/index.md", f"{self.root}/notes/other.md"
        original = self.links.snapshot
        patch(self, self.links, "snapshot",
              lambda: {**original(), "suggest": [[a, b, 0.5, ["x"]]]})
        _, before = self.getj("/api/links?t=tok")
        self.assertEqual(len(before["suggest"]), 1)
        self.post("/api/dismiss?t=tok", {"a": b, "b": a})
        _, after = self.getj("/api/links?t=tok")
        self.assertEqual(after["suggest"], [])

    def test_writes_are_refused_read_only(self):
        self.ctx.read_only = True
        for route, body in (("/api/daily", {}), ("/api/capture", {"text": "x"}),
                            ("/api/tours", {"op": "save", "title": "t", "stops": []})):
            status, _ = self.post(f"{route}?t=tok", body)
            self.assertEqual(status, 403, route)


class TestLiveFeedRoute(Served):
    def tearDown(self):
        self.ctx.stop_feed()

    def test_a_change_arrives_through_the_long_poll(self):
        import threading, time
        _, first = self.getj("/api/activity?t=tok&since=0&wait=0")
        self.assertIn(first["engine"], ("inotify", "poll"))
        self.ctx.activity.ready.wait(5)
        self.getj(f"/api/children?t=tok&path={quote(self.root + '/notes')}")
        time.sleep(0.3)
        threading.Timer(0.3, lambda: write(f"{self.root}/notes/brand-new.md", "hi")).start()
        started = time.monotonic()
        seen = []
        while time.monotonic() - started < 8 and not seen:
            _, got = self.getj(f"/api/activity?t=tok&since={first['seq']}&wait=4")
            seen = [e for e in got["events"] if e["path"].endswith("brand-new.md")]
        self.assertTrue(seen, "the new note never came through")
        self.assertEqual(seen[0]["kind"], "created")

    def test_switching_folders_stops_the_old_feed(self):
        self.getj("/api/activity?t=tok&since=0&wait=0")
        old = self.ctx.activity
        other = temp_dir(self)
        self.post("/api/open?t=tok", {"path": other})
        self.assertIsNone(self.ctx.activity)
        self.assertIsNot(self.ctx.feed(), old)

    def test_with_watching_off_live_says_so(self):
        self.ctx.watching = False
        _, got = self.getj("/api/activity?t=tok&since=0&wait=0")
        self.assertTrue(got["off"])

    def test_since_must_be_a_number(self):
        status, _ = self.getj("/api/activity?t=tok&since=soon")
        self.assertEqual(status, 400)


class TestHistoryAndDiskRoutes(Served):
    def tearDown(self):
        self.ctx.stop_feed()

    def test_history_outside_a_repository_is_an_answer(self):
        status, got = self.getj(f"/api/history?t=tok&path={quote(self.root)}")
        self.assertEqual(status, 200)
        self.assertIsNone(got["repo"])

    def test_history_of_a_real_repository(self):
        import shutil, subprocess
        if not shutil.which("git"):
            self.skipTest("git is not installed")
        env = dict(os.environ, GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_NOSYSTEM="1",
                   GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t",
                   GIT_COMMITTER_EMAIL="t@t")
        run = lambda *a: subprocess.run(["git", "-C", self.root, *a], env=env,
                                        capture_output=True, check=True)
        run("init", "-q")
        run("add", "notes")
        run("commit", "-qm", "the notes")
        _, got = self.getj(f"/api/history?t=tok&path={quote(self.root)}")
        self.assertEqual(got["repo"], self.root)
        self.assertEqual(got["commits"][0]["subject"], "the notes")
        self.assertIn(["A", f"{self.root}/notes/index.md", None], got["commits"][0]["changes"])

    def test_history_refuses_outside_the_root(self):
        status, _ = self.getj("/api/history?t=tok&path=/etc")
        self.assertEqual(status, 400)

    def test_disk_usage_counts_what_the_graph_hides(self):
        import time
        deadline = time.monotonic() + 5
        while True:
            _, got = self.getj(f"/api/du?t=tok&path={quote(self.root)}")
            if got["complete"] or time.monotonic() > deadline:
                break
            time.sleep(0.05)
        names = {k["name"] for k in got["children"]}
        self.assertIn("node_modules", names)
        self.assertIn(".hidden", names)
        self.assertGreater(got["bytes"], 0)

    def test_disk_usage_refuses_outside_the_root(self):
        status, _ = self.getj("/api/du?t=tok&path=/etc")
        self.assertEqual(status, 400)


class TestQuickNoteLauncher(unittest.TestCase):
    def test_the_menu_entry_offers_a_quick_note_first(self):
        entry = desktop.desktop_entry("/x/launch", "/x/icon.svg", {"notes": "/n"})
        self.assertIn("Actions=capture;project-0;", entry)
        self.assertIn("[Desktop Action capture]\nName=Quick note\nExec=/x/launch --capture", entry)

    def test_capture_parses(self):
        self.assertTrue(cli.build_parser().parse_args(["--capture"]).capture)


if __name__ == "__main__":
    unittest.main(verbosity=2, buffer=True)
