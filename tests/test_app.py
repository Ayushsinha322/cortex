#!/usr/bin/env python3
"""Tests for the parts that let cortex be used without a terminal.

Editing and file operations from the window, opening other folders, knowing
when the window has closed, giving terminal editors a terminal window, and
putting cortex in the app menu. Standard library only.

Run: python3 tests/test_app.py   (or tests/run)

Like test_cortex.py, every test works in its own throwaway directory. Anything
that would write under the home directory -- the trash, the launcher, the
recent-folder list -- is pointed somewhere temporary first.
"""

import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.client import HTTPConnection
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

from cortex import actions, desktop, files, places, reader
from cortex.actions import (ActionRunner, available_editors, find_terminal,
                            terminal_argv)
from cortex.lifecycle import Lifecycle
from cortex.links import LinkIndex
from cortex.scanner import Scanner
from cortex.server import Context, serve
from cortex.watch import Watcher
from test_cortex import Tree, write


def temp_dir(test, prefix="cortex-app-"):
    path = os.path.realpath(tempfile.mkdtemp(prefix=prefix))
    test.addCleanup(shutil.rmtree, path, ignore_errors=True)
    return path


def patch(test, obj, name, value):
    old = getattr(obj, name)
    setattr(obj, name, value)
    test.addCleanup(setattr, obj, name, old)


def patch_env(test, **values):
    for key, value in values.items():
        old = os.environ.get(key)
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value
        test.addCleanup(
            lambda k=key, v=old: os.environ.__setitem__(k, v) if v is not None
            else os.environ.pop(k, None))


def read(path, mode="r"):
    with open(path, mode) as fh:
        return fh.read()


# ---------------------------------------------------------------- editing

class TestReadForEditing(unittest.TestCase):
    def setUp(self):
        self.dir = temp_dir(self)

    def test_the_whole_file_comes_back_with_a_version(self):
        p = write(f"{self.dir}/a.md", "# A\n\nbody\n")
        got = files.read_source(p)
        self.assertEqual(got["text"], "# A\n\nbody\n")
        self.assertEqual(got["eol"], "lf")
        self.assertFalse(got["bom"])
        self.assertEqual(got["version"], files.version(p))
        self.assertIsInstance(got["version"], str)

    def test_more_than_the_preview_shows(self):
        # the reader stops at LINE_LIMIT lines; the editor must not, or a save
        # would cut the file short
        lines = "\n".join(f"line {i}" for i in range(reader.LINE_LIMIT + 50))
        p = write(f"{self.dir}/long.txt", lines)
        self.assertEqual(files.read_source(p)["text"], lines)

    def test_windows_line_endings_arrive_as_newlines(self):
        p = f"{self.dir}/w.txt"
        with open(p, "wb") as fh:
            fh.write(b"one\r\ntwo\r\n")
        got = files.read_source(p)
        self.assertEqual(got["text"], "one\ntwo\n")
        self.assertEqual(got["eol"], "crlf")

    def test_a_byte_order_mark_is_noticed_and_hidden(self):
        p = f"{self.dir}/b.txt"
        with open(p, "wb") as fh:
            fh.write(b"\xef\xbb\xbfhello")
        got = files.read_source(p)
        self.assertEqual(got["text"], "hello")
        self.assertTrue(got["bom"])

    def test_old_mac_line_endings_arrive_as_newlines(self):
        # a textarea turns a lone \r into \n as it reads it back, so the
        # editor must be given \n, or it looks edited the moment it opens
        p = f"{self.dir}/mac.txt"
        with open(p, "wb") as fh:
            fh.write(b"one\rtwo\r")
        got = files.read_source(p)
        self.assertEqual(got["text"], "one\ntwo\n")
        self.assertEqual(got["eol"], "cr")

    def test_mixed_line_endings_are_reported(self):
        p = f"{self.dir}/mixed.txt"
        with open(p, "wb") as fh:
            fh.write(b"a\r\nb\nc\nd\n")
        got = files.read_source(p)
        self.assertTrue(got["mixed"])
        self.assertEqual(got["eol"], "lf")
        with open(p, "wb") as fh:
            fh.write(b"a\nb\n")
        self.assertFalse(files.read_source(p)["mixed"])

    def test_a_binary_file_is_refused(self):
        p = f"{self.dir}/x.bin"
        with open(p, "wb") as fh:
            fh.write(b"\x00\x01\x02")
        with self.assertRaises(files.FileError):
            files.read_source(p)

    def test_text_that_is_not_utf8_is_refused_rather_than_mangled(self):
        p = f"{self.dir}/latin.txt"
        with open(p, "wb") as fh:
            fh.write("café".encode("latin-1"))
        with self.assertRaises(files.FileError) as cm:
            files.read_source(p)
        self.assertIn("UTF-8", cm.exception.message)

    def test_a_huge_file_is_refused(self):
        p = f"{self.dir}/big.txt"
        with open(p, "wb") as fh:
            fh.write(b"a" * (files.EDIT_LIMIT + 1))
        with self.assertRaises(files.FileError) as cm:
            files.read_source(p)
        self.assertIn("too large", cm.exception.message)


class TestSave(unittest.TestCase):
    def setUp(self):
        self.dir = temp_dir(self)
        self.path = write(f"{self.dir}/note.md", "old\n")

    def test_a_save_writes_the_text(self):
        got = files.save(self.path, "new\n", files.version(self.path))
        self.assertTrue(got["ok"])
        self.assertEqual(read(self.path), "new\n")
        self.assertEqual(got["version"], files.version(self.path))

    def test_a_save_over_a_change_it_did_not_see_is_refused(self):
        seen = files.version(self.path)
        time.sleep(0.01)
        write(self.path, "someone else wrote this\n")
        with self.assertRaises(files.FileError) as cm:
            files.save(self.path, "mine\n", seen)
        self.assertEqual(cm.exception.status, 409)
        self.assertTrue(cm.exception.extra["conflict"])
        self.assertEqual(read(self.path), "someone else wrote this\n")

    def test_force_overwrites_anyway(self):
        write(self.path, "theirs\n")
        files.save(self.path, "mine\n", "0:0", force=True)
        self.assertEqual(read(self.path), "mine\n")

    def test_line_endings_are_kept(self):
        with open(self.path, "wb") as fh:
            fh.write(b"a\r\nb\r\n")
        files.save(self.path, "a\nb\nc\n", files.version(self.path))
        self.assertEqual(read(self.path, "rb"), b"a\r\nb\r\nc\r\n")

    def test_a_byte_order_mark_is_kept(self):
        with open(self.path, "wb") as fh:
            fh.write(b"\xef\xbb\xbfold")
        files.save(self.path, "new", files.version(self.path))
        self.assertEqual(read(self.path, "rb"), b"\xef\xbb\xbfnew")

    def test_the_file_keeps_its_permissions(self):
        os.chmod(self.path, 0o640)
        files.save(self.path, "x", files.version(self.path))
        self.assertEqual(stat.S_IMODE(os.stat(self.path).st_mode), 0o640)

    def test_nothing_is_left_behind_beside_it(self):
        files.save(self.path, "x", files.version(self.path))
        self.assertEqual(os.listdir(self.dir), ["note.md"])

    def test_saving_through_a_symlink_keeps_the_link(self):
        link = f"{self.dir}/link.md"
        os.symlink(self.path, link)
        real = os.path.realpath(link)
        files.save(real, "via link\n", files.version(real))
        self.assertTrue(os.path.islink(link))
        self.assertEqual(read(link), "via link\n")

    def test_a_file_deleted_while_open_says_so(self):
        os.remove(self.path)
        with self.assertRaises(files.FileError) as cm:
            files.save(self.path, "x", "1:1")
        self.assertTrue(cm.exception.extra.get("gone"))

    def test_and_can_be_put_back_on_purpose(self):
        os.remove(self.path)
        files.save(self.path, "back\n", None, force=True)
        self.assertEqual(read(self.path), "back\n")

    def test_text_that_is_not_a_string_is_refused(self):
        with self.assertRaises(files.FileError):
            files.save(self.path, None, files.version(self.path))

    def test_old_mac_line_endings_are_kept(self):
        with open(self.path, "wb") as fh:
            fh.write(b"a\rb\r")
        files.save(self.path, "a\nb\nc\n", files.version(self.path))
        self.assertEqual(read(self.path, "rb"), b"a\rb\rc\r")

    def test_a_hard_linked_file_stays_one_file(self):
        other = f"{self.dir}/other-name.md"
        os.link(self.path, other)
        files.save(self.path, "both see this\n", files.version(self.path))
        self.assertEqual(read(other), "both see this\n")
        self.assertTrue(os.path.samefile(self.path, other))

    def test_a_file_made_read_only_since_opening_is_not_overwritten(self):
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            self.skipTest("root can write anything")
        seen = files.version(self.path)
        os.chmod(self.path, 0o444)
        self.addCleanup(os.chmod, self.path, 0o644)
        with self.assertRaises(files.FileError) as cm:
            files.save(self.path, "x", seen)
        self.assertIn("read-only", cm.exception.message)
        self.assertEqual(read(self.path), "old\n")

    def test_extended_attributes_are_kept(self):
        if not hasattr(os, "setxattr"):
            self.skipTest("no extended attributes here")
        try:
            os.setxattr(self.path, "user.cortex-test", b"kept")
        except OSError:
            self.skipTest("this filesystem takes no user attributes")
        files.save(self.path, "x", files.version(self.path))
        self.assertEqual(os.getxattr(self.path, "user.cortex-test"), b"kept")

    def test_a_long_name_in_a_wide_script_can_be_saved(self):
        long = write(f"{self.dir}/{'日' * 80}.md", "old")       # 243 bytes
        files.save(long, "new", files.version(long))
        self.assertEqual(read(long), "new")

    def test_a_file_replaced_with_the_same_size_and_time_is_still_noticed(self):
        seen = files.version(self.path)
        st = os.stat(self.path)
        swap = write(f"{self.dir}/swap", "NEW\n")              # same size as old
        os.utime(swap, ns=(st.st_atime_ns, st.st_mtime_ns))
        os.replace(swap, self.path)
        with self.assertRaises(files.FileError) as cm:
            files.save(self.path, "mine", seen)
        self.assertEqual(cm.exception.status, 409)

    def test_a_rename_by_cortex_does_not_make_a_draft_stale(self):
        seen = files.version(self.path)
        moved = files.rename(self.path, "moved.md")["path"]
        self.assertEqual(files.version(moved), seen)


# ------------------------------------------------------- new, rename, trash

class TestFileOperations(unittest.TestCase):
    def setUp(self):
        self.dir = temp_dir(self)

    def test_a_new_note_gets_md_and_a_heading(self):
        got = files.create(self.dir, "ideas")
        self.assertEqual(got["path"], f"{self.dir}/ideas.md")
        self.assertEqual(read(got["path"]), "# ideas\n\n")

    def test_a_name_with_an_extension_is_left_alone(self):
        got = files.create(self.dir, "run.sh")
        self.assertEqual(got["path"], f"{self.dir}/run.sh")
        self.assertEqual(read(got["path"]), "")

    def test_a_dotfile_is_not_given_an_extension(self):
        self.assertEqual(files.create(self.dir, ".env")["path"], f"{self.dir}/.env")

    def test_a_new_folder(self):
        got = files.create(self.dir, "archive", "dir")
        self.assertTrue(os.path.isdir(got["path"]))

    def test_nothing_is_overwritten_by_new(self):
        write(f"{self.dir}/a.md", "keep")
        with self.assertRaises(files.FileError) as cm:
            files.create(self.dir, "a.md")
        self.assertIn("already exists", cm.exception.message)
        self.assertEqual(read(f"{self.dir}/a.md"), "keep")

    def test_names_that_are_paths_are_refused(self):
        for bad in ("", "  ", ".", "..", "a/b", "../escape", "x\x00y", "é" * 200):
            with self.subTest(name=bad), self.assertRaises(files.FileError):
                files.create(self.dir, bad)

    def test_rename(self):
        old = write(f"{self.dir}/a.md", "x")
        got = files.rename(old, "b.md")
        self.assertEqual(got["path"], f"{self.dir}/b.md")
        self.assertFalse(os.path.exists(old))

    def test_rename_never_lands_on_something_else(self):
        a = write(f"{self.dir}/a.md", "a")
        write(f"{self.dir}/b.md", "b")
        with self.assertRaises(files.FileError):
            files.rename(a, "b.md")
        self.assertEqual(read(f"{self.dir}/b.md"), "b")

    def test_renaming_only_the_case_is_allowed(self):
        a = write(f"{self.dir}/note.md", "x")
        got = files.rename(a, "Note.md")
        self.assertEqual(os.listdir(self.dir), ["Note.md"])
        self.assertEqual(got["path"], f"{self.dir}/Note.md")

    def test_a_link_renamed_onto_the_file_it_points_at_is_refused(self):
        # samefile() would call these the same, and the rename would replace
        # the file with a link to itself
        target = write(f"{self.dir}/notes.md", "precious")
        os.symlink("notes.md", f"{self.dir}/latest.md")
        with self.assertRaises(files.FileError):
            files.rename(f"{self.dir}/latest.md", "notes.md")
        self.assertFalse(os.path.islink(target))
        self.assertEqual(read(target), "precious")

    def test_one_hard_link_renamed_onto_another_is_refused(self):
        a = write(f"{self.dir}/a.md", "x")
        os.link(a, f"{self.dir}/b.md")
        with self.assertRaises(files.FileError):
            files.rename(a, "b.md")
        self.assertEqual(sorted(os.listdir(self.dir)), ["a.md", "b.md"])


@unittest.skipIf(sys.platform == "darwin", "the macOS trash belongs to Finder")
class TestTrash(unittest.TestCase):
    def setUp(self):
        self.dir = temp_dir(self)
        self.data = temp_dir(self, "cortex-xdg-")
        patch_env(self, XDG_DATA_HOME=self.data)

    def test_a_file_goes_to_the_trash_with_a_note_of_where_it_was(self):
        p = write(f"{self.dir}/old note.md", "x")
        got = files.trash(p)
        self.assertFalse(os.path.exists(p))
        self.assertEqual(got["trashed"], f"{self.data}/Trash/files/old note.md")
        self.assertEqual(read(got["trashed"]), "x")
        info = read(f"{self.data}/Trash/info/old note.md.trashinfo")
        self.assertIn("[Trash Info]", info)
        self.assertIn(f"Path={quote(p)}", info)
        self.assertIn("DeletionDate=", info)

    def test_a_second_one_with_the_same_name_does_not_replace_the_first(self):
        first = files.trash(write(f"{self.dir}/a.md", "one"))["trashed"]
        second = files.trash(write(f"{self.dir}/a.md", "two"))["trashed"]
        self.assertNotEqual(first, second)
        self.assertEqual(read(first), "one")
        self.assertEqual(read(second), "two")
        self.assertTrue(os.path.isfile(
            f"{self.data}/Trash/info/{os.path.basename(second)}.trashinfo"))

    def test_a_folder_goes_whole(self):
        write(f"{self.dir}/box/inner.md", "x")
        got = files.trash(f"{self.dir}/box")
        self.assertTrue(os.path.isfile(f"{got['trashed']}/inner.md"))

    def test_a_name_too_long_for_its_trash_record_still_goes(self):
        name = "n" * 250 + ".md"
        got = files.trash(write(f"{self.dir}/{name}", "x"))
        self.assertTrue(os.path.isfile(got["trashed"]))
        trashed = os.path.basename(got["trashed"])
        self.assertTrue(trashed.endswith(".md"))
        info = read(f"{self.data}/Trash/info/{trashed}.trashinfo")
        self.assertIn(quote(name), info)          # restored under its full name

    def test_the_trash_is_private(self):
        files.trash(write(f"{self.dir}/a.md"))
        mode = stat.S_IMODE(os.stat(f"{self.data}/Trash/files").st_mode)
        self.assertEqual(mode, 0o700)

    def test_something_already_gone_is_an_error_not_a_crash(self):
        with self.assertRaises(files.FileError):
            files.trash(f"{self.dir}/never-was.md")


# ------------------------------------------------------------- the server

class Served(Tree):
    """A running server over the fixture tree, with the home-directory
    writers pointed into the test's own temp space."""

    read_only = False

    def setUp(self):
        super().setUp()
        self.state = temp_dir(self, "cortex-state-")
        patch(self, places, "RECENT_FILE", f"{self.state}/recent.json")
        patch(self, places, "PROJECTS_FILE", f"{self.state}/projects.json")
        patch_env(self, XDG_DATA_HOME=f"{self.state}/data")
        self.runner = ActionRunner(tty=True)
        self.life = Lifecycle(grace=0)
        self.reopened = []

        def world(folder):
            self.reopened.append(folder)
            sc = Scanner(folder)
            return {"scanner": sc, "links": LinkIndex(sc), "watcher": Watcher(),
                    "title": os.path.basename(folder), "ui": {"autoExpand": {"depth": 1}}}

        self.links = LinkIndex(self.scanner)
        self.ctx = Context(self.scanner, self.links, self.runner, "tok", title="t",
                           ui_config={"autoExpand": {"depth": 0}},
                           watcher=Watcher(), lifecycle=self.life,
                           read_only=self.read_only, reopen=world)
        self.httpd = serve(self.ctx, 0)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, args=(0.05,),
                         daemon=True).start()
        self.addCleanup(self.httpd.shutdown)

    def get(self, path, host=None):
        req = Request(f"http://127.0.0.1:{self.port}{path}")
        if host:
            req.add_header("Host", host)
        try:
            with urlopen(req, timeout=5) as r:
                return r.status, r.read()
        except HTTPError as exc:
            return exc.code, exc.read()

    def getj(self, path):
        status, body = self.get(path)
        return status, json.loads(body or b"null")

    def post(self, path, payload):
        req = Request(f"http://127.0.0.1:{self.port}{path}",
                      data=json.dumps(payload).encode(),
                      headers={"content-type": "application/json"})
        try:
            with urlopen(req, timeout=5) as r:
                return r.status, json.loads(r.read() or b"null")
        except HTTPError as exc:
            return exc.code, json.loads(exc.read() or b"null")


class TestHardening(Served):
    def test_the_page_is_not_served_without_the_token(self):
        status, body = self.get("/")
        self.assertEqual(status, 403)
        self.assertNotIn(b"tok", body)

    def test_a_page_reached_under_another_name_is_refused(self):
        # DNS rebinding: evil.example resolves to 127.0.0.1, and its page asks
        # for ours. The browser still sends evil.example as the host.
        status, body = self.get("/?t=tok", host=f"evil.example:{self.port}")
        self.assertEqual(status, 403)
        self.assertNotIn(b"window.CORTEX", body)
        status, _ = self.get("/api/root?t=tok", host="evil.example")
        self.assertEqual(status, 403)

    def test_localhost_works_on_any_port_for_a_tunnel(self):
        status, _ = self.get("/api/root?t=tok", host="localhost:8080")
        self.assertEqual(status, 200)

    def test_a_request_with_no_host_is_refused(self):
        conn = HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.putrequest("GET", "/api/root?t=tok", skip_host=True)
        conn.endheaders()
        self.assertEqual(conn.getresponse().status, 403)
        conn.close()

    def test_a_file_with_a_percent_escape_in_its_name_is_the_one_read(self):
        literal = write(f"{self.root}/a%20b.md", "the literal one")
        write(f"{self.root}/a b.md", "the decoded one")
        status, body = self.getj(f"/api/preview?t=tok&path={quote(literal, safe='')}")
        self.assertEqual(status, 200)
        self.assertEqual(body["text"], "the literal one")

    def test_a_file_whose_name_is_not_latin1_can_be_served(self):
        p = write(f"{self.root}/日本.md", "hello")
        status, body = self.get(f"/api/raw?t=tok&path={quote(p)}")
        self.assertEqual(status, 200)
        self.assertEqual(body, b"hello")

    def test_an_internal_error_is_an_answer_not_a_dropped_connection(self):
        def boom(_path):
            raise RuntimeError("kaboom")
        patch(self, reader, "preview", boom)
        status, body = self.getj(
            f"/api/preview?t=tok&path={quote(self.root + '/notes/index.md')}")
        self.assertEqual(status, 500)
        self.assertFalse(body["ok"])
        self.assertNotIn("kaboom", body["error"])

    def test_an_action_with_no_path_is_refused(self):
        status, body = self.post("/api/action?t=tok", {"kind": "read", "path": ""})
        self.assertEqual(status, 400)
        self.assertEqual(self.runner.q.qsize(), 0)

    def test_the_page_tells_the_window_what_it_can_do(self):
        _, page = self.get("/?t=tok")
        cfg = json.loads(page.decode().split("window.CORTEX = ")[1].split(";</script>")[0])
        self.assertEqual(cfg["terminal"], "here")
        self.assertFalse(cfg["readOnly"])
        self.assertTrue(cfg["canQuit"])

    def test_the_icon_is_served(self):
        status, body = self.get("/static/icon.svg")
        self.assertEqual(status, 200)
        self.assertIn(b"<svg", body)

    def test_a_file_from_disk_cannot_run_as_this_origin(self):
        # an SVG from a cloned repository, opened on its own in a tab, would
        # otherwise be a page on this origin, next to the API
        svg = write(f"{self.root}/pic.svg", '<svg xmlns="http://www.w3.org/2000/svg">'
                    '<script>alert(1)</script></svg>')
        with urlopen(f"http://127.0.0.1:{self.port}/api/raw?t=tok&path={quote(svg)}",
                     timeout=5) as r:
            policy = r.headers["Content-Security-Policy"] or ""
            self.assertIn("sandbox", policy)
            self.assertIn("default-src 'none'", policy)
            self.assertEqual(r.headers["X-Content-Type-Options"], "nosniff")
        pdf = write(f"{self.root}/doc.pdf", "%PDF-1.4")
        with urlopen(f"http://127.0.0.1:{self.port}/api/raw?t=tok&path={quote(pdf)}",
                     timeout=5) as r:
            # the browser's PDF viewer will not run sandboxed
            self.assertIsNone(r.headers["Content-Security-Policy"])

    def test_a_write_from_another_origin_is_refused(self):
        p = f"{self.root}/notes/index.md"
        before = read(p)
        for origin in ("http://evil.example", "null", "http://127.0.0.1.evil.example"):
            req = Request(f"http://127.0.0.1:{self.port}/api/save?t=tok",
                          data=json.dumps({"path": p, "text": "x", "force": True}).encode(),
                          headers={"content-type": "application/json", "Origin": origin})
            with self.assertRaises(HTTPError) as cm:
                urlopen(req, timeout=5)
            self.assertEqual(cm.exception.code, 403, origin)
        self.assertEqual(read(p), before)
        req = Request(f"http://127.0.0.1:{self.port}/api/save?t=tok",
                      data=json.dumps({"path": p, "text": "ours", "force": True}).encode(),
                      headers={"content-type": "application/json",
                               "Origin": f"http://127.0.0.1:{self.port}"})
        with urlopen(req, timeout=5) as r:
            self.assertEqual(r.status, 200)

    def test_a_write_that_is_not_json_is_refused(self):
        # a page can send text/plain anywhere without asking first; JSON it
        # cannot, so requiring JSON keeps other pages' forms out
        p = f"{self.root}/notes/index.md"
        before = read(p)
        req = Request(f"http://127.0.0.1:{self.port}/api/save?t=tok",
                      data=json.dumps({"path": p, "text": "x", "force": True}).encode(),
                      headers={"content-type": "text/plain"})
        with self.assertRaises(HTTPError) as cm:
            urlopen(req, timeout=5)
        self.assertEqual(cm.exception.code, 415)
        self.assertEqual(read(p), before)


class TestEditingOverHTTP(Served):
    def test_load_edit_save_round_trip(self):
        p = f"{self.root}/notes/index.md"
        status, src = self.getj(f"/api/source?t=tok&path={quote(p)}")
        self.assertEqual(status, 200)
        status, got = self.post("/api/save?t=tok", {
            "path": p, "text": src["text"] + "more\n", "version": src["version"]})
        self.assertEqual(status, 200, got)
        self.assertTrue(read(p).endswith("more\n"))

    def test_a_stale_save_is_a_409_with_the_new_version(self):
        p = f"{self.root}/notes/other.md"
        _, src = self.getj(f"/api/source?t=tok&path={quote(p)}")
        time.sleep(0.01)
        write(p, "changed underneath\n")
        status, got = self.post("/api/save?t=tok", {
            "path": p, "text": "mine", "version": src["version"]})
        self.assertEqual(status, 409)
        self.assertTrue(got["conflict"])
        self.assertEqual(got["version"], files.version(p))
        self.assertEqual(read(p), "changed underneath\n")

    def test_nothing_outside_the_root_can_be_written(self):
        outside = temp_dir(self, "cortex-outside-")
        victim = write(f"{outside}/victim.txt", "safe")
        for path in (victim, f"{self.root}/../{os.path.basename(outside)}/victim.txt"):
            status, got = self.post("/api/save?t=tok", {
                "path": path, "text": "pwned", "force": True})
            self.assertEqual(status, 400, path)
        self.assertEqual(read(victim), "safe")

    def test_nor_through_a_symlink_pointing_out(self):
        outside = temp_dir(self, "cortex-outside-")
        victim = write(f"{outside}/victim.txt", "safe")
        os.symlink(victim, f"{self.root}/sneaky.txt")
        status, _ = self.post("/api/save?t=tok", {
            "path": f"{self.root}/sneaky.txt", "text": "pwned", "force": True})
        self.assertEqual(status, 400)
        self.assertEqual(read(victim), "safe")

    def test_a_deleted_file_can_be_saved_back_into_its_folder(self):
        p = f"{self.root}/notes/other.md"
        os.remove(p)
        status, got = self.post("/api/save?t=tok", {"path": p, "text": "back", "force": True})
        self.assertEqual(status, 200, got)
        self.assertEqual(read(p), "back")

    def test_saving_needs_the_token(self):
        status, _ = self.post("/api/save", {"path": f"{self.root}/notes/index.md",
                                            "text": "x", "force": True})
        self.assertEqual(status, 403)


class TestFileOpsOverHTTP(Served):
    def test_new_rename_and_trash(self):
        status, made = self.post("/api/fs?t=tok", {
            "op": "new", "path": f"{self.root}/notes", "name": "fresh"})
        self.assertEqual(status, 200, made)
        self.assertTrue(os.path.isfile(f"{self.root}/notes/fresh.md"))

        status, moved = self.post("/api/fs?t=tok", {
            "op": "rename", "path": made["path"], "name": "renamed.md"})
        self.assertEqual(moved["path"], f"{self.root}/notes/renamed.md")

        if sys.platform != "darwin":
            status, gone = self.post("/api/fs?t=tok", {"op": "trash", "path": moved["path"]})
            self.assertTrue(gone["ok"], gone)
            self.assertFalse(os.path.exists(moved["path"]))

    def test_mkdir(self):
        _, got = self.post("/api/fs?t=tok", {"op": "mkdir", "path": self.root, "name": "box"})
        self.assertTrue(os.path.isdir(got["path"]))

    @unittest.skipIf(sys.platform == "darwin", "the macOS trash belongs to Finder")
    def test_trashing_a_symlink_trashes_the_link_not_its_target(self):
        target = f"{self.root}/notes/index.md"
        os.symlink(target, f"{self.root}/shortcut.md")
        _, got = self.post("/api/fs?t=tok", {"op": "trash", "path": f"{self.root}/shortcut.md"})
        self.assertTrue(got["ok"], got)
        self.assertFalse(os.path.lexists(f"{self.root}/shortcut.md"))
        self.assertTrue(os.path.isfile(target))

    def test_renaming_a_symlink_renames_the_link(self):
        os.symlink(f"{self.root}/notes/index.md", f"{self.root}/shortcut.md")
        self.post("/api/fs?t=tok", {"op": "rename", "path": f"{self.root}/shortcut.md",
                                    "name": "moved.md"})
        self.assertTrue(os.path.islink(f"{self.root}/moved.md"))
        self.assertTrue(os.path.isfile(f"{self.root}/notes/index.md"))

    def test_the_root_itself_cannot_be_renamed_or_trashed(self):
        for op in ("rename", "trash"):
            status, _ = self.post("/api/fs?t=tok", {"op": op, "path": self.root, "name": "x"})
            self.assertEqual(status, 400, op)
        self.assertTrue(os.path.isdir(self.root))

    def test_nothing_outside_the_root_can_be_touched(self):
        outside = temp_dir(self, "cortex-outside-")
        victim = write(f"{outside}/victim.txt", "safe")
        for body in ({"op": "trash", "path": victim},
                     {"op": "rename", "path": victim, "name": "x"},
                     {"op": "new", "path": outside, "name": "planted"},
                     {"op": "trash", "path": f"{self.root}/../{os.path.basename(outside)}"}):
            status, _ = self.post("/api/fs?t=tok", body)
            self.assertEqual(status, 400, body)
        self.assertEqual(os.listdir(outside), ["victim.txt"])

    def test_a_name_cannot_climb_out(self):
        status, got = self.post("/api/fs?t=tok", {
            "op": "new", "path": self.root, "name": "../escaped"})
        self.assertFalse(got["ok"])
        self.assertFalse(os.path.exists(os.path.join(os.path.dirname(self.root), "escaped.md")))

    def test_an_unknown_operation_is_refused(self):
        _, got = self.post("/api/fs?t=tok", {"op": "chmod", "path": self.root})
        self.assertFalse(got["ok"])


class TestReadOnly(Served):
    read_only = True

    def test_nothing_can_be_written(self):
        p = f"{self.root}/notes/index.md"
        before = read(p)
        status, got = self.post("/api/save?t=tok", {"path": p, "text": "x", "force": True})
        self.assertEqual(status, 403)
        self.assertIn("read-only", got["error"])
        for body in ({"op": "new", "path": self.root, "name": "n"},
                     {"op": "trash", "path": p}):
            status, _ = self.post("/api/fs?t=tok", body)
            self.assertEqual(status, 403)
        self.assertEqual(read(p), before)
        self.assertFalse(os.path.exists(f"{self.root}/n.md"))

    def test_but_it_can_still_be_read(self):
        status, _ = self.getj(f"/api/source?t=tok&path={quote(self.root + '/notes/index.md')}")
        self.assertEqual(status, 200)


class TestFoldersOverHTTP(Served):
    def test_places_describes_where_we_are(self):
        _, got = self.getj("/api/places?t=tok")
        self.assertEqual(got["root"], self.root)
        self.assertEqual(got["parent"], os.path.dirname(self.root))
        self.assertTrue(got["canSwitch"])
        self.assertIn("installed", got["launcher"])

    def test_opening_another_folder_moves_the_root(self):
        other = temp_dir(self, "cortex-other-")
        write(f"{other}/elsewhere.md", "x")
        status, got = self.post("/api/open?t=tok", {"path": other})
        self.assertEqual(status, 200, got)
        self.assertEqual(self.reopened, [other])
        _, root = self.getj("/api/root?t=tok")
        self.assertEqual(root["id"], other)
        # the boundary moved with it
        status, _ = self.getj(f"/api/children?t=tok&path={quote(self.root)}")
        self.assertEqual(status, 400)
        self.assertEqual(places.load_recent()[0], other)

    def test_the_old_index_is_abandoned(self):
        other = temp_dir(self, "cortex-other-")
        self.post("/api/open?t=tok", {"path": other})
        self.assertTrue(self.links._stop.is_set())

    def test_terminal_windows_move_with_the_folder(self):
        other = temp_dir(self, "cortex-other-")
        self.post("/api/open?t=tok", {"path": other})
        self.assertEqual(self.runner.home, other)

    def test_opening_something_that_is_not_a_folder_changes_nothing(self):
        for path in (f"{self.root}/notes/index.md", "/definitely/not/here", ""):
            _, got = self.post("/api/open?t=tok", {"path": path})
            self.assertFalse(got["ok"], path)
        self.assertEqual(self.reopened, [])

    def test_a_layout_saved_for_the_old_folder_is_not_filed_under_the_new(self):
        _, got = self.post("/api/layout?t=tok", {"root": "/somewhere/else",
                                                 "positions": {"/a": [1, 2]}})
        self.assertFalse(got["ok"])

    def test_typing_a_path_offers_folders(self):
        _, got = self.getj(f"/api/dirs?t=tok&path={quote(self.root + '/no')}")
        self.assertIn(f"{self.root}/notes", got["dirs"])
        self.assertIn(f"{self.root}/node_modules", got["dirs"])

    def test_projects_can_be_saved_and_forgotten(self):
        _, got = self.post("/api/project?t=tok", {"op": "save", "name": "fixture"})
        self.assertTrue(got["ok"])
        self.assertEqual(places.load_projects(), {"fixture": self.root})
        self.assertEqual(got["projects"][0]["name"], "fixture")
        _, got = self.post("/api/project?t=tok", {"op": "forget", "name": "fixture"})
        self.assertEqual(places.load_projects(), {})

    def test_a_project_needs_a_name(self):
        _, got = self.post("/api/project?t=tok", {"op": "save", "name": "  "})
        self.assertFalse(got["ok"])


class TestLifecycleOverHTTP(Served):
    def beacon(self, route, client="w1"):
        # what navigator.sendBeacon sends: a text/plain POST, no JSON
        req = Request(f"http://127.0.0.1:{self.port}{route}?t=tok&c={client}",
                      data=b"bye", headers={"content-type": "text/plain"})
        with urlopen(req, timeout=5) as r:
            return r.status

    def test_hello_and_goodbye_reach_the_lifecycle(self):
        self.assertEqual(self.beacon("/api/hello"), 200)
        self.assertEqual(self.life.clients, 1)
        self.assertFalse(self.life.should_exit())
        self.beacon("/api/bye")
        self.assertTrue(self.life.should_exit())

    def test_a_goodbye_needs_the_token(self):
        self.beacon("/api/hello")
        req = Request(f"http://127.0.0.1:{self.port}/api/bye?c=w1", data=b"")
        with self.assertRaises(HTTPError):
            urlopen(req, timeout=5)
        self.assertEqual(self.life.clients, 1)

    def test_quit(self):
        status, got = self.post("/api/quit?t=tok", {})
        self.assertTrue(got["ok"])
        self.assertTrue(self.life.should_exit())
        self.assertEqual(self.life.reason, "quit from the window")


# -------------------------------------------------------------- lifecycle

class TestLifecycle(unittest.TestCase):
    def setUp(self):
        self.now = 100.0
        self.life = Lifecycle(grace=5, clock=lambda: self.now)

    def test_with_no_window_yet_it_waits(self):
        self.now += 1000
        self.assertFalse(self.life.should_exit())

    def test_the_last_window_closing_ends_it_after_the_grace_period(self):
        self.life.hello("a")
        self.life.bye("a")
        self.assertFalse(self.life.should_exit())
        self.now += 6
        self.assertTrue(self.life.should_exit())
        self.assertEqual(self.life.reason, "the window was closed")

    def test_a_reload_is_not_a_close(self):
        self.life.hello("a")
        self.life.bye("a")
        self.now += 1
        self.life.hello("b")
        self.now += 60
        self.assertFalse(self.life.should_exit())

    def test_a_page_still_loading_holds_it_open(self):
        self.life.hello("a")
        self.life.bye("a")
        self.now += 4
        self.life.touch()
        self.now += 4
        self.assertFalse(self.life.should_exit())

    def test_one_of_two_windows_closing_is_not_the_end(self):
        self.life.hello("a")
        self.life.hello("b")
        self.life.bye("a")
        self.now += 60
        self.assertFalse(self.life.should_exit())

    def test_silence_alone_never_ends_it(self):
        # a minimised window can be frozen by the browser indefinitely
        self.life.hello("a")
        self.now += 10 ** 6
        self.assertFalse(self.life.should_exit())

    def test_without_auto_exit_only_quit_ends_it(self):
        life = Lifecycle(auto_exit=False, grace=0, clock=lambda: self.now)
        life.hello("a")
        life.bye("a")
        self.now += 60
        self.assertFalse(life.should_exit())
        life.quit()
        self.assertTrue(life.should_exit())


# ---------------------------------------------------- terminal-less actions

class TestNoTerminal(Tree):
    def test_with_a_terminal_window_a_terminal_editor_still_works(self):
        runner = ActionRunner(tty=False, terminal=["xterm", "-e"])
        got = runner.submit("read", f"{self.root}/notes/index.md")
        self.assertTrue(got["ok"])
        self.assertFalse(got["terminal"])
        self.assertEqual(got["how"], "window")

    def test_with_no_terminal_at_all_a_terminal_program_is_refused(self):
        runner = ActionRunner(tty=False, terminal=[])
        runner.terminal = None
        got = runner.submit("read", f"{self.root}/notes/index.md")
        self.assertFalse(got["ok"])
        self.assertIn("no terminal", got["error"])
        self.assertEqual(runner.q.qsize(), 0)

    def test_but_handing_a_file_to_the_desktop_still_works(self):
        runner = ActionRunner(tty=False, terminal=[])
        runner.terminal = None
        for kind in ("open", "reveal"):
            self.assertTrue(runner.submit(kind, f"{self.root}/notes/index.md")["ok"])

    def test_a_foreground_program_gets_a_terminal_window(self):
        runner = ActionRunner(tty=False, terminal=["xterm", "-e"])
        runner.home = self.root
        launched = []
        patch(self, runner, "_detach", lambda argv, cwd=None: launched.append((argv, cwd)))
        runner._foreground(["nvim", "x.md"], "nvim x.md", start=self.root)
        argv, cwd = launched[0]
        self.assertEqual(argv[:2], ["xterm", "-e"])
        self.assertEqual(argv[-2:], ["nvim", "x.md"])
        self.assertIn(self.root, argv)
        self.assertEqual(cwd, self.root)

    def test_the_terminal_window_runs_in_the_folder_it_was_given(self):
        argv = terminal_argv(["xterm", "-e"], ["pwd"], self.root)
        out = subprocess.run(argv[2:], capture_output=True, text=True, timeout=10)
        self.assertEqual(out.stdout.strip(), self.root)

    def test_a_failing_program_holds_the_window_open_to_be_read(self):
        argv = terminal_argv(["xterm", "-e"], ["sh", "-c", "exit 3"], self.root)
        out = subprocess.run(argv[2:], input="\n", capture_output=True, text=True,
                             timeout=10)
        self.assertIn("exited with status 3", out.stdout)

    def test_on_macos_terminal_app_is_asked_through_an_argument(self):
        argv = terminal_argv(["osascript"], ["nvim", "it's.md"], "/tmp/a b")
        self.assertEqual(argv[0], "osascript")
        line = argv[-1]
        self.assertIn("cd '/tmp/a b'", line)
        self.assertIn("'it'\"'\"'s.md'", line)
        # the command is never spliced into the AppleScript itself
        self.assertNotIn("nvim", " ".join(argv[:-1]))

    def test_terminal_is_honoured(self):
        bindir = temp_dir(self, "cortex-bin-")
        fake = f"{bindir}/myterm"
        write(fake, "#!/bin/sh\n")
        os.chmod(fake, 0o755)
        patch_env(self, TERMINAL="myterm", PATH=bindir + os.pathsep + os.environ["PATH"])
        if sys.platform == "darwin":
            self.skipTest("macOS always uses Terminal.app")
        self.assertEqual(find_terminal(), ["myterm", "-e"])

    def test_the_desktops_own_terminal_comes_first(self):
        bindir = temp_dir(self, "cortex-bin-")
        for name in ("xterm", "konsole"):
            write(f"{bindir}/{name}", "#!/bin/sh\n")
            os.chmod(f"{bindir}/{name}", 0o755)
        patch_env(self, TERMINAL=None, PATH=bindir, XDG_CURRENT_DESKTOP="KDE")
        if sys.platform == "darwin":
            self.skipTest("macOS always uses Terminal.app")
        self.assertEqual(find_terminal(), [f"{bindir}/konsole", "-e"])

    def test_without_a_terminal_windowed_editors_come_first(self):
        bindir = temp_dir(self, "cortex-bin-")
        for name in ("nvim", "codium"):
            write(f"{bindir}/{name}", "#!/bin/sh\n")
            os.chmod(f"{bindir}/{name}", 0o755)
        patch_env(self, PATH=bindir, EDITOR=None, VISUAL=None)
        here = [e["id"] for e in available_editors(tty=True)]
        launched = [e["id"] for e in available_editors(tty=False, terminal=True)]
        stranded = [e["id"] for e in available_editors(tty=False, terminal=False)]
        self.assertEqual(here, ["nvim", "codium"])
        self.assertEqual(launched, ["codium", "nvim"])
        self.assertEqual(stranded, ["codium"])

    def test_run_forever_stops_when_told(self):
        runner = ActionRunner(tty=True)
        done = threading.Event()
        t = threading.Thread(target=lambda: (runner.run_forever(until=done.is_set)))
        t.start()
        done.set()
        t.join(3)
        self.assertFalse(t.is_alive())


# --------------------------------------------------------------- the menu

class TestLauncher(unittest.TestCase):
    def setUp(self):
        self.home = temp_dir(self, "cortex-home-")
        patch(self, desktop, "HOME", self.home)
        patch_env(self, XDG_DATA_HOME=f"{self.home}/.local/share")

    @unittest.skipUnless(sys.platform.startswith("linux"), "a .desktop file is Linux")
    def test_install_writes_an_entry_a_script_and_an_icon(self):
        env = {"PATH": "/opt/x/bin:/usr/bin", "EDITOR": "hx", "VISUAL": "code --wait"}
        got = desktop.install({"notes": "/n"}, env=env)
        self.assertTrue(got["ok"], got)
        p = desktop.paths()
        entry = read(p["entry"])
        self.assertIn("Terminal=false", entry)
        self.assertIn(f"Exec={p['script']}", entry)
        self.assertIn(f"Icon={p['icon']}", entry)
        self.assertIn("StartupWMClass=Cortex", entry)
        self.assertIn("[Desktop Action project-0]", entry)
        self.assertIn("Name=notes", entry)
        self.assertTrue(os.access(p["script"], os.X_OK))
        self.assertIn(b"<svg", read(p["icon"], "rb"))
        self.assertTrue(desktop.installed())

        script = read(p["script"])
        self.assertIn("--launcher", script.splitlines()[-1])
        self.assertEqual(subprocess.run(["sh", "-n", p["script"]]).returncode, 0)
        # run everything but the exec, as the app menu would, and look at what
        # the editor and the path come out as
        setup = "\n".join(script.splitlines()[:-1])
        out = subprocess.run(
            [shutil.which("sh"), "-c",
             setup + '\nprintf "%s|%s|%s" "$PATH" "$EDITOR" "$VISUAL"'],
            env={"PATH": "/session/bin"}, capture_output=True, text=True).stdout
        self.assertEqual(out, "/opt/x/bin:/usr/bin:/session/bin|hx|code --wait")

    @unittest.skipUnless(sys.platform.startswith("linux"), "a .desktop file is Linux")
    def test_remove_takes_it_all_away(self):
        desktop.install({})
        self.assertTrue(desktop.remove()["ok"])
        for path in desktop.paths().values():
            self.assertFalse(os.path.exists(path), path)
        self.assertFalse(desktop.installed())

    @unittest.skipUnless(sys.platform.startswith("linux"), "a .desktop file is Linux")
    def test_someone_elses_entry_is_never_replaced_or_removed(self):
        entry = desktop.paths()["entry"]
        write(entry, "[Desktop Entry]\nName=a different cortex\n")
        self.assertFalse(desktop.install({})["ok"])
        desktop.remove()
        self.assertIn("a different cortex", read(entry))

    def test_the_launcher_runs_this_cortex(self):
        argv = desktop.launch_command()
        self.assertEqual(argv[0], sys.executable)
        # this suite runs from a clone, so the clone's entry point it is
        self.assertTrue(argv[-1].endswith("cortex.py"), argv)

    def test_exec_quoting_follows_the_desktop_entry_spec(self):
        q = desktop._exec_arg
        self.assertEqual(q("/home/u/.local/share/cortex/launch"),
                         "/home/u/.local/share/cortex/launch")
        self.assertEqual(q("/home/my user/x"), '"/home/my user/x"')
        self.assertEqual(q('say "hi"'), '"say \\\\"hi\\\\""')
        self.assertEqual(q("100%"), "100%%")
        self.assertEqual(q("cost $5"), '"cost \\\\$5"')

    def test_on_macos_it_is_a_bundle_that_stays_out_of_the_dock(self):
        patch(self, desktop.sys, "platform", "darwin")
        got = desktop.install({})
        self.assertTrue(got["ok"], got)
        app = f"{self.home}/Applications/Cortex.app"
        import plistlib
        with open(f"{app}/Contents/Info.plist", "rb") as fh:
            info = plistlib.load(fh)
        self.assertEqual(info["CFBundleIdentifier"], desktop.BUNDLE_ID)
        self.assertEqual(info["CFBundleExecutable"], "cortex")
        self.assertTrue(info["LSUIElement"])
        self.assertTrue(os.access(f"{app}/Contents/MacOS/cortex", os.X_OK))
        self.assertTrue(desktop.installed())
        desktop.remove()
        self.assertFalse(os.path.exists(app))

    def test_on_macos_someone_elses_app_is_left_alone(self):
        patch(self, desktop.sys, "platform", "darwin")
        os.makedirs(f"{self.home}/Applications/Cortex.app/Contents")
        self.assertFalse(desktop.install({})["ok"])
        desktop.remove()
        self.assertTrue(os.path.isdir(f"{self.home}/Applications/Cortex.app"))


# ----------------------------------------------------------- recent folders

class TestPlaces(unittest.TestCase):
    def setUp(self):
        self.dir = temp_dir(self)
        patch(self, places, "RECENT_FILE", f"{self.dir}/state/recent.json")
        patch(self, places, "PROJECTS_FILE", f"{self.dir}/config/projects.json")

    def test_recent_folders_are_most_recent_first_without_repeats(self):
        for path in ("/a", "/b", "/a", "/c"):
            places.remember(path)
        self.assertEqual(places.load_recent(), ["/c", "/a", "/b"])

    def test_the_list_is_capped(self):
        for i in range(places.RECENT_MAX + 5):
            places.remember(f"/p{i}")
        self.assertEqual(len(places.load_recent()), places.RECENT_MAX)

    def test_writers_at_the_same_moment_never_leave_a_broken_file(self):
        threads = [threading.Thread(target=places.remember, args=(f"/p{i}",))
                   for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        with open(places.RECENT_FILE, encoding="utf-8") as fh:
            self.assertIsInstance(json.load(fh), list)
        self.assertEqual([n for n in os.listdir(os.path.dirname(places.RECENT_FILE))
                          if n != "recent.json"], [])

    def test_the_last_folder_still_there_is_the_one_reopened(self):
        real = temp_dir(self)
        places.remember(real)
        places.remember("/gone/now")
        self.assertEqual(places.last_root(), real)

    def test_rubbish_in_the_file_is_not_fatal(self):
        os.makedirs(os.path.dirname(places.RECENT_FILE))
        write(places.RECENT_FILE, "{not json")
        self.assertEqual(places.load_recent(), [])
        write(places.RECENT_FILE, '[1, "/ok", null]')
        self.assertEqual(places.load_recent(), ["/ok"])

    def test_completion_offers_folders_only(self):
        os.makedirs(f"{self.dir}/work/alpha")
        os.makedirs(f"{self.dir}/work/.hidden")
        write(f"{self.dir}/work/afile.md")
        got = places.complete(f"{self.dir}/work/")
        self.assertEqual(got["dirs"], [f"{self.dir}/work/alpha"])
        self.assertEqual(places.complete(f"{self.dir}/work/al")["dirs"],
                         [f"{self.dir}/work/alpha"])
        self.assertEqual(places.complete(f"{self.dir}/work/.h")["dirs"],
                         [f"{self.dir}/work/.hidden"])

    def test_completion_of_nowhere_is_empty_not_an_error(self):
        self.assertEqual(places.complete("/no/such/place/x")["dirs"], [])

    def test_home_is_shortened_but_a_lookalike_is_not(self):
        self.assertEqual(places.short(places.HOME + "/notes"), "~/notes")
        self.assertEqual(places.short(places.HOME + "x/notes"), places.HOME + "x/notes")


# ------------------------------------------------- the whole thing, launched

class TestLaunchedFromTheMenu(unittest.TestCase):
    """cortex started the way the app-menu entry starts it: no terminal, no
    folder named, a browser that only writes down the address it was given."""

    def test_it_reopens_the_last_folder_logs_quietly_and_quits_when_asked(self):
        home = temp_dir(self, "cortex-home-")
        last = temp_dir(self, "cortex-last-")
        write(f"{last}/hello.md", "# hi\n")
        os.makedirs(f"{home}/.cache/cortex")
        write(f"{home}/.cache/cortex/recent.json", json.dumps([last]))
        browser = f"{home}/browser"
        write(browser, f'#!/bin/sh\nfor a; do echo "$a"; done > "{home}/opened"\n')
        os.chmod(browser, 0o755)

        env = dict(os.environ, HOME=home, XDG_DATA_HOME=f"{home}/.local/share")
        proc = subprocess.Popen(
            [sys.executable, os.path.join(os.path.dirname(HERE), "cortex.py"),
             "--launcher", "-w", "tab", "-b", browser, "--no-links"],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, env=env)
        self.addCleanup(lambda: proc.poll() is None and proc.kill())

        handed = None
        for _ in range(100):
            if os.path.exists(f"{home}/opened"):
                handed = next((ln for ln in read(f"{home}/opened").split()
                               if ln.startswith("file://")), None)
                if handed:
                    break
            time.sleep(0.05)
        self.assertIsNotNone(handed, "the browser was never handed the page")

        # the token is in a file only this user can read, never on the
        # browser's command line, where any user on the machine could see it
        forward = handed[len("file://"):]
        self.assertEqual(stat.S_IMODE(os.stat(forward).st_mode), 0o600)
        url = read(forward).split('url=')[1].split('"')[0].replace("&amp;", "&")
        base, token = url.split("/?t=")
        self.assertNotIn(token, read(f"{home}/opened"))
        self.assertEqual(stat.S_IMODE(os.stat(f"{home}/.cache/cortex/cortex.log").st_mode),
                         0o600)

        with urlopen(f"{base}/?t={token}", timeout=5) as r:
            self.assertIn(b"window.CORTEX", r.read())
        self.assertFalse(os.path.exists(forward), "the forwarding page outlived its use")
        with urlopen(f"{base}/api/root?t={token}", timeout=5) as r:
            self.assertEqual(json.load(r)["id"], last)

        req = Request(f"{base}/api/quit?t={token}", data=b"{}",
                      headers={"content-type": "application/json"})
        urlopen(req, timeout=5).read()
        out, err = proc.communicate(timeout=10)
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(out, b"")              # nothing printed: there is no terminal
        self.assertEqual(err, b"")
        log = read(f"{home}/.cache/cortex/cortex.log")
        self.assertIn("mapping", log)
        self.assertIn("quit from the window", log)
        self.assertNotIn("\033[", log)          # no colour codes in a file


if __name__ == "__main__":
    unittest.main(verbosity=2, buffer=True)
