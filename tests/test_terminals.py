#!/usr/bin/env python3
"""Tests for the terminals in the window: real programs on real
pseudo-terminals, read back through the feed the window reads.
Standard library only.

Run: python3 tests/test_terminals.py   (or tests/run)

Every terminal here runs /bin/sh in a throwaway folder. Waits are polls with
a deadline, never sleeps, so a slow machine makes them slower, not wrong.
"""

import base64
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from cortex import terminals
from cortex.actions import ActionRunner
from cortex.lifecycle import Lifecycle
from cortex.links import LinkIndex
from cortex.scanner import Scanner
from cortex.server import Context, serve
from cortex.terminals import Terminals
from cortex.watch import Watcher

SH = ["/bin/sh"]


def until(test, seconds=10.0, what="it"):
    deadline = time.time() + seconds
    while time.time() < deadline:
        got = test()
        if got:
            return got
        time.sleep(0.02)
    raise AssertionError(f"timed out waiting for {what}")


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="cortex-term-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        os.makedirs(f"{self.tmp}/sub")
        self.terms = Terminals()
        self.addCleanup(self.terms.stop_all)
        self.seen = {}                 # terminal id -> everything it has printed
        self.since = 0

    def open(self, argv=SH, cwd=None, **kw):
        got = self.terms.open(argv, cwd or self.tmp, "test", **kw)
        self.assertTrue(got["ok"], got)
        return got["id"]

    def read(self, wait=0.2):
        got = self.terms.poll(self.since, wait)
        self.since = got["seq"]
        for _seq, tid, data in got["chunks"]:
            self.seen[tid] = self.seen.get(tid, b"") + base64.b64decode(data)
        return got

    def output(self, tid):
        return self.seen.get(tid, b"").decode("utf-8", "replace")

    def expect(self, tid, text, what=None):
        def there():
            self.read()
            return text in self.output(tid)
        until(there, what=what or f"{text!r} in the output")


@unittest.skipUnless(terminals.supported(), "no pseudo-terminals here")
class TestTerminals(Base):
    def test_what_is_typed_is_run_and_what_is_printed_comes_back(self):
        tid = self.open()
        self.assertTrue(self.terms.write(tid, "echo he''llo\n")["ok"])
        self.expect(tid, "hello")

    def test_it_starts_in_the_folder_it_was_given(self):
        tid = self.open(cwd=f"{self.tmp}/sub")
        self.terms.write(tid, "pwd\n")
        self.expect(tid, f"{self.tmp}/sub")

    def test_it_is_a_terminal_with_colours_and_a_size(self):
        tid = self.open(cols=91, rows=17)
        self.terms.write(tid, "test -t 0 && echo is-a-tty; echo $TERM $COLORTERM; stty size\n")
        self.expect(tid, "is-a-tty")
        self.expect(tid, "xterm-256color truecolor")
        self.expect(tid, "17 91")
        self.terms.resize(tid, 120, 40)
        self.terms.write(tid, "stty size\n")
        self.expect(tid, "40 120")
        self.assertFalse(self.terms.resize(tid, "wide", 4)["ok"])

    def test_ctrl_c_reaches_what_is_running(self):
        # only true if the pty is the program's controlling terminal
        tid = self.open()
        self.terms.write(tid, "echo st''arted; sleep 300\n")
        self.expect(tid, "started")
        # the shell prints that a moment before the sleep is in the
        # foreground; nobody's finger is that fast, and a test's must not be
        time.sleep(0.4)
        self.terms.write(tid, "\x03")
        # an interrupt throws away whatever was typed just before it landed,
        # as on any terminal; so wait for it to land
        self.expect(tid, "^C")
        time.sleep(0.1)
        self.terms.write(tid, "echo st''atus-$?\n")
        self.expect(tid, "status-130", "ctrl-c to interrupt the sleep")

    def test_a_program_that_ends_says_so_with_its_status(self):
        tid = self.open()
        self.terms.write(tid, "exit 7\n")
        ended = until(lambda: next((t for t in self.read()["terms"]
                                    if t["id"] == tid and not t["alive"]), None),
                      what="the shell to exit")
        self.assertEqual(ended["code"], 7)
        self.assertFalse(self.terms.write(tid, "echo late\n")["ok"])

    def test_a_program_that_cannot_be_started_says_why_in_the_tab(self):
        tid = self.open(argv=["no-such-program-anywhere"])
        self.expect(tid, "could not start no-such-program-anywhere")
        until(lambda: not self.read()["terms"][0]["alive"], what="it to end")

    def test_closing_a_tab_ends_what_was_running_in_it(self):
        tid = self.open()
        self.terms.write(tid, "echo p''id-$$\n")
        self.expect(tid, "pid-")
        pid = int(self.output(tid).split("pid-")[-1].split()[0])
        self.assertTrue(self.terms.close(tid)["ok"])

        def gone():
            try:
                os.kill(pid, 0)
                return False
            except ProcessLookupError:
                return True
        until(gone, what="the shell to be gone")
        self.assertEqual(self.terms.poll(0)["terms"], [])
        self.assertFalse(self.terms.close(tid)["ok"])

    def test_a_window_that_reloads_gets_the_screen_back(self):
        tid = self.open()
        self.terms.write(tid, "echo be''fore-reload\n")
        self.expect(tid, "before-reload")
        got = self.terms.replay(tid)
        self.assertIn("before-reload", base64.b64decode(got["data"]).decode())
        # and knows where in the feed that was true, so nothing is read twice
        self.assertEqual(self.terms.poll(got["seq"])["chunks"], [])
        self.assertIsNone(self.terms.replay("nope"))

    def test_the_feed_carries_every_terminal_and_waits_when_quiet(self):
        a, b = self.open(), self.open()
        self.terms.write(a, "echo from-a\n")
        self.terms.write(b, "echo from-b\n")
        self.expect(a, "from-a")
        self.expect(b, "from-b")
        self.assertNotIn("from-b", self.output(a))
        # once both prompts are back there is nothing more to say
        until(lambda: not self.read(0.3)["chunks"], what="the shells to go quiet")
        began = time.time()
        quiet = self.terms.poll(self.since, wait=0.15)
        self.assertEqual(quiet["chunks"], [])
        self.assertGreaterEqual(time.time() - began, 0.14)

    def test_a_window_too_far_behind_is_told_to_start_over(self):
        old = terminals.LOG_BYTES
        terminals.LOG_BYTES = 2000
        self.addCleanup(setattr, terminals, "LOG_BYTES", old)
        tid = self.open()
        self.terms.write(tid, "i=0; while [ $i -lt 200 ]; do echo line-$i-padding-padding; "
                              "i=$((i+1)); done; echo fin''ished\n")
        until(lambda: b"finished" in base64.b64decode(self.terms.replay(tid)["data"]),
              what="the loop to finish")
        got = self.terms.poll(1)
        self.assertTrue(got["lost"])
        self.assertEqual(got["chunks"], [])
        self.assertFalse(self.terms.poll(got["seq"])["lost"])

    def test_there_is_a_limit_and_bad_input_is_refused(self):
        old = terminals.MAX_TERMINALS
        terminals.MAX_TERMINALS = 2
        self.addCleanup(setattr, terminals, "MAX_TERMINALS", old)
        a = self.open()
        self.open()
        self.assertFalse(self.terms.open(SH, self.tmp, "x")["ok"])
        self.terms.close(a)
        self.assertTrue(self.terms.open(SH, self.tmp, "x")["ok"])     # a closed one frees a place
        self.assertFalse(self.terms.open(SH, f"{self.tmp}/gone", "x")["ok"])
        self.assertFalse(self.terms.write("nope", "x")["ok"])
        self.assertFalse(self.terms.write(self.terms.poll(0)["terms"][0]["id"],
                                          {"not": "text"})["ok"])

    def test_an_agents_interface_is_known_to_be_open(self):
        tid = self.open(kind="agent", owner="s1")
        self.assertTrue(self.terms.holds("s1"))
        self.assertFalse(self.terms.holds("s2"))
        self.terms.close_owned("s1")
        self.assertFalse(self.terms.holds("s1"))
        self.assertIsNone(self.terms.replay(tid))


@unittest.skipUnless(terminals.supported(), "no pseudo-terminals here")
class TestOverHTTP(Base):
    read_only = False

    def setUp(self):
        super().setUp()
        scanner = Scanner(self.tmp)
        self.ctx = Context(scanner, LinkIndex(scanner), ActionRunner(tty=True), "tok",
                           title="t", watcher=Watcher(), lifecycle=Lifecycle(grace=0),
                           read_only=self.read_only)
        self.httpd = serve(self.ctx, 0)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, args=(0.05,), daemon=True).start()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)
        self.addCleanup(lambda: self.ctx.terminals and self.ctx.terminals.stop_all())

    def getj(self, path):
        try:
            with urlopen(f"http://127.0.0.1:{self.port}{path}", timeout=5) as r:
                return r.status, json.loads(r.read())
        except HTTPError as exc:
            return exc.code, json.loads(exc.read() or b"null")

    def post(self, payload):
        req = Request(f"http://127.0.0.1:{self.port}/api/term?t=tok",
                      data=json.dumps(payload).encode(),
                      headers={"content-type": "application/json"})
        try:
            with urlopen(req, timeout=10) as r:
                return r.status, json.loads(r.read())
        except HTTPError as exc:
            return exc.code, json.loads(exc.read() or b"null")

    def test_open_type_read_and_close_through_the_api(self):
        status, got = self.post({"op": "open", "cwd": f"{self.tmp}/sub", "cols": 80, "rows": 24})
        self.assertEqual((status, got["ok"]), (200, True))
        tid = got["id"]
        self.assertEqual(got["term"]["cwd"], f"{self.tmp}/sub")
        self.post({"op": "input", "id": tid, "data": "echo over-ht''tp\n"})

        def printed():
            _, feed = self.getj("/api/terms?t=tok&since=0")
            raw = b"".join(base64.b64decode(c[2]) for c in feed["chunks"] if c[1] == tid)
            return b"over-http" in raw
        until(printed, what="the echo to come back")
        self.assertEqual(self.getj(f"/api/term/replay?t=tok&id={tid}")[0], 200)
        self.assertTrue(self.post({"op": "resize", "id": tid, "cols": 100, "rows": 30})[1]["ok"])
        self.assertTrue(self.post({"op": "close", "id": tid})[1]["ok"])
        self.assertEqual(self.getj("/api/terms?t=tok&since=0")[1]["terms"], [])

    def test_what_is_refused(self):
        self.assertEqual(self.getj("/api/terms")[0], 403)                       # no token
        self.assertEqual(self.post({"op": "open", "cwd": "/etc"})[0], 400)      # outside the root
        self.assertEqual(self.post({"op": "open", "cols": "wide"})[0], 400)
        self.assertEqual(self.getj("/api/term/replay?t=tok&id=nope")[0], 404)
        self.assertFalse(self.post({"op": "input", "id": "nope", "data": "x"})[1]["ok"])
        self.assertFalse(self.post({"op": "open", "agent": "nope"})[1]["ok"])
        self.assertFalse(self.post({"op": "dance"})[1]["ok"])
        # the page may ask for a shell, never name the program
        _, got = self.post({"op": "open", "argv": ["/bin/echo", "owned"], "cols": 80, "rows": 24})
        tid = got["id"]
        self.post({"op": "input", "id": tid, "data": "echo $0\n"})
        self.assertNotEqual(got["term"]["title"], "echo")


@unittest.skipUnless(terminals.supported(), "no pseudo-terminals here")
class TestReadOnlyOverHTTP(TestOverHTTP):
    read_only = True

    def test_open_type_read_and_close_through_the_api(self):
        status, got = self.getj("/api/terms?t=tok&since=0")
        self.assertEqual((status, got["off"]), (200, True))
        self.assertEqual(self.post({"op": "open"})[0], 403)
        self.assertIsNone(self.ctx.terminals)

    test_what_is_refused = None


if __name__ == "__main__":
    unittest.main(verbosity=1)
