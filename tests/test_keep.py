#!/usr/bin/env python3
"""cortex going on with its window closed, and being found again.

Three parts: when cortex decides to stay (Lifecycle), how it knows something
is running (Terminals.activity), and -- with the real program, a stand-in
browser and a home directory of its own -- closing the window on a program
that is running, running `cortex` again, and stopping it.

Run: python3 tests/test_keep.py   (or tests/run)
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from urllib.request import Request, urlopen

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from cortex import instances, terminals
from cortex.lifecycle import Lifecycle
from cortex.terminals import Terminals

CORTEX = os.path.join(os.path.dirname(HERE), "cortex.py")
SH = ["/bin/sh"]


def load(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def text(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def until(test, seconds=15.0, what="it"):
    deadline = time.time() + seconds
    while time.time() < deadline:
        got = test()
        if got:
            return got
        time.sleep(0.03)
    raise AssertionError(f"timed out waiting for {what}")


# ------------------------------------------------------------- when to stay

class TestStaying(unittest.TestCase):
    def setUp(self):
        self.now = 100.0
        self.state = None
        self.told = []
        self.life = Lifecycle(grace=5, clock=lambda: self.now, keep=lambda: self.state,
                              linger=3600, tell=self.told.append)

    def close(self):
        self.life.hello("a")
        self.life.bye("a")
        self.now += 6

    def test_with_nothing_running_the_window_closing_ends_it_as_before(self):
        self.close()
        self.assertTrue(self.life.should_exit())
        self.assertEqual(self.told, [])

    def test_a_shell_sitting_at_its_prompt_is_not_a_reason_to_stay(self):
        self.state = "idle"
        self.close()
        self.assertTrue(self.life.should_exit())

    def test_something_running_keeps_it_and_says_so_once(self):
        self.state = "busy"
        self.close()
        self.assertFalse(self.life.should_exit())
        self.assertTrue(self.life.held)
        self.now += 10 ** 5
        self.assertFalse(self.life.should_exit())
        self.assertEqual(self.told, ["held"])

    def test_what_finished_is_kept_to_come_back_to_for_a_while(self):
        self.state = "busy"
        self.close()
        self.assertFalse(self.life.should_exit())
        self.state = "idle"                       # the test run is over
        self.now += 60
        self.assertFalse(self.life.should_exit())
        self.assertEqual(self.told, ["held", "finished"])
        self.now += 3500
        self.assertFalse(self.life.should_exit())
        self.now += 200                           # an hour since it was last busy
        self.assertTrue(self.life.should_exit())
        self.assertEqual(self.told, ["held", "finished"])

    def test_when_everything_it_stayed_for_has_gone_so_does_it(self):
        self.state = "busy"
        self.close()
        self.assertFalse(self.life.should_exit())
        self.state = None                         # the terminal was closed by its program
        self.assertTrue(self.life.should_exit())

    def test_a_window_opening_again_is_someone_back(self):
        self.state = "busy"
        self.close()
        self.assertFalse(self.life.should_exit())
        self.life.hello("b")
        self.assertFalse(self.life.should_exit())
        self.assertFalse(self.life.held)
        # and closing that one, with it still running, holds it again
        self.life.bye("b")
        self.now += 6
        self.assertFalse(self.life.should_exit())
        self.assertEqual(self.told, ["held", "held"])

    def test_a_window_that_says_it_closed_is_not_waited_on(self):
        self.life.hello("a")
        self.life.closed()                        # no goodbye from the page, no grace
        self.assertTrue(self.life.should_exit())

    def test_being_told_to_quit_ends_it_whatever_is_running(self):
        self.state = "busy"
        self.close()
        self.assertFalse(self.life.should_exit())
        self.life.quit()
        self.assertTrue(self.life.should_exit())

    def test_asked_not_to_keep_anything_it_does_not(self):
        life = Lifecycle(grace=5, clock=lambda: self.now)
        life.hello("a")
        life.bye("a")
        self.now += 6
        self.assertTrue(life.should_exit())


# ------------------------------------------------- knowing something is running

@unittest.skipUnless(terminals.supported(), "no pseudo-terminals here")
class TestActivity(unittest.TestCase):
    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="cortex-keep-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.terms = Terminals()
        self.addCleanup(self.terms.stop_all)

    def test_a_shell_at_its_prompt_is_open_but_not_running_anything(self):
        self.assertEqual(self.terms.activity(), (0, 0))
        got = self.terms.open(SH, self.tmp, "test")
        self.assertTrue(got["ok"], got)
        until(lambda: self.terms.activity() == (0, 1), what="an idle shell")

    def test_a_program_in_the_foreground_is_and_stops_being_when_it_ends(self):
        got = self.terms.open(SH, self.tmp, "test")
        until(lambda: self.terms.activity() == (0, 1), what="an idle shell")
        self.terms.write(got["id"], "sleep 1.5\r")
        until(lambda: self.terms.activity() == (1, 1), what="the sleep to be running")
        until(lambda: self.terms.activity() == (0, 1), what="the sleep to end")

    def test_an_agents_own_interface_counts_for_as_long_as_it_is_open(self):
        got = self.terms.open(["/bin/sh", "-c", "sleep 30"], self.tmp, "agent", kind="agent")
        self.assertTrue(got["ok"], got)
        until(lambda: self.terms.activity() == (1, 1), what="the agent's terminal")
        self.terms.close(got["id"])
        until(lambda: self.terms.activity() == (0, 0), what="it to close")


# ------------------------------------------------------------ being found again

class TestInstances(unittest.TestCase):
    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="cortex-run-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.was = os.environ.get("XDG_RUNTIME_DIR")
        os.environ["XDG_RUNTIME_DIR"] = self.tmp
        self.addCleanup(self.restore)
        self.asked = instances.ask
        self.addCleanup(lambda: setattr(instances, "ask", self.asked))

    def restore(self):
        instances.withdraw()
        if self.was is None:
            os.environ.pop("XDG_RUNTIME_DIR", None)
        else:
            os.environ["XDG_RUNTIME_DIR"] = self.was

    def entry(self, pid, root, port=4000):
        path = os.path.join(instances.folder(), f"{pid}.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"pid": pid, "root": root, "port": port, "token": "tok"}, fh)
        return path

    def test_the_file_is_the_users_alone_and_goes_when_cortex_does(self):
        instances.announce("/a/b", 4321, "secret")
        path = os.path.join(instances.folder(), f"{os.getpid()}.json")
        self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
        self.assertEqual(os.stat(instances.folder()).st_mode & 0o777, 0o700)
        self.assertEqual(load(path)["root"], "/a/b")
        instances.moved("/c")
        self.assertEqual(load(path)["root"], "/c")
        instances.withdraw()
        self.assertFalse(os.path.exists(path))

    def test_one_that_is_running_and_answers_is_found_by_its_folder(self):
        proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        self.addCleanup(lambda: (proc.kill(), proc.wait()))
        self.entry(proc.pid, "/proj")
        instances.ask = lambda entry, *a, **k: {"pid": entry["pid"], "terminals": 2}
        self.assertEqual(instances.find("/proj")["status"]["terminals"], 2)
        self.assertIsNone(instances.find("/other"))
        self.assertEqual(instances.url(instances.find("/proj")), "http://127.0.0.1:4000/?t=tok")

    def test_one_that_died_is_not_found_and_its_file_is_cleared(self):
        proc = subprocess.Popen([sys.executable, "-c", "pass"])
        proc.wait()
        path = self.entry(proc.pid, "/proj")
        self.assertIsNone(instances.find("/proj"))
        self.assertFalse(os.path.exists(path))

    def test_a_port_that_now_belongs_to_something_else_is_not_taken_for_it(self):
        proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        self.addCleanup(lambda: (proc.kill(), proc.wait()))
        self.entry(proc.pid, "/proj")
        instances.ask = lambda entry, *a, **k: {"pid": 1}        # somebody else answered
        self.assertIsNone(instances.find("/proj"))
        instances.ask = lambda entry, *a, **k: None               # nobody did
        self.assertIsNone(instances.find("/proj"))


# ------------------------------------------------------------ the real program

@unittest.skipUnless(terminals.supported(), "no pseudo-terminals here")
class TestTheRealThing(unittest.TestCase):
    def setUp(self):
        self.home = os.path.realpath(tempfile.mkdtemp(prefix="cortex-keep-home-"))
        self.addCleanup(shutil.rmtree, self.home, ignore_errors=True)
        self.root = f"{self.home}/proj"
        os.makedirs(self.root)
        os.makedirs(f"{self.home}/run", mode=0o700)
        self.browser = f"{self.home}/browser"
        with open(self.browser, "w") as fh:
            fh.write(f'#!/bin/sh\necho opened >> "{self.home}/opened"\n')
        os.chmod(self.browser, 0o755)
        # what cortex would have put on the desktop is written down instead:
        # a test is not a reason to show anyone a notification
        os.makedirs(f"{self.home}/bin")
        for name in ("notify-send", "osascript"):
            with open(f"{self.home}/bin/{name}", "w") as fh:
                fh.write(f'#!/bin/sh\necho "$@" >> "{self.home}/notified"\n')
            os.chmod(f"{self.home}/bin/{name}", 0o755)
        self.env = dict(os.environ, HOME=self.home, XDG_DATA_HOME=f"{self.home}/.local/share",
                        XDG_RUNTIME_DIR=f"{self.home}/run", SHELL="/bin/sh",
                        PATH=f"{self.home}/bin{os.pathsep}{os.environ.get('PATH', '')}")
        self.env.pop("CORTEX_SHELL", None)

    def cortex(self, *args, wait=False):
        argv = [sys.executable, CORTEX, *args]
        if wait:
            return subprocess.run(argv, env=self.env, stdin=subprocess.DEVNULL,
                                  capture_output=True, text=True, timeout=60)
        proc = subprocess.Popen(argv, env=self.env, stdin=subprocess.DEVNULL,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(lambda: (proc.poll() is None and proc.kill(), proc.wait()))
        return proc

    def start(self):
        proc = self.cortex("-w", "app", "-b", self.browser, "--no-links", "--no-watch", self.root)
        folder = f"{self.home}/run/cortex"
        path = f"{folder}/{proc.pid}.json"
        until(lambda: os.path.exists(path) or proc.poll() is not None, 30, "cortex to start")
        self.assertIsNone(proc.poll(), "cortex stopped before it was up")
        entry = load(path)
        return proc, entry

    def api(self, entry, route, body=None):
        req = Request(f"http://127.0.0.1:{entry['port']}{route}"
                      + ("&" if "?" in route else "?") + f"t={entry['token']}",
                      data=None if body is None else json.dumps(body).encode(),
                      headers={"Content-Type": "application/json"} if body is not None else {})
        with urlopen(req, timeout=10) as res:
            return json.loads(res.read().decode())

    def test_closing_the_window_on_nothing_ends_it(self):
        proc, entry = self.start()
        self.api(entry, "/api/hello?c=w1", {})
        got = self.api(entry, "/api/quit", {"closed": True})
        self.assertEqual(got, {"ok": True, "kept": False})
        proc.wait(timeout=15)
        self.assertFalse(os.path.exists(f"{self.home}/run/cortex/{proc.pid}.json"))

    def test_closing_it_on_a_running_program_keeps_it_and_cortex_finds_it_again(self):
        proc, entry = self.start()
        self.api(entry, "/api/hello?c=w1", {})
        term = self.api(entry, "/api/term", {"op": "open", "cwd": self.root})
        self.assertTrue(term["ok"], term)
        until(lambda: self.api(entry, "/api/status")["terminals"] == 1, what="the terminal")
        self.api(entry, "/api/term", {"op": "input", "id": term["id"], "data": "sleep 600\r"})
        until(lambda: self.api(entry, "/api/status")["running"] == 1, what="the sleep")

        # the window closes
        got = self.api(entry, "/api/quit", {"closed": True})
        self.assertEqual(got, {"ok": True, "kept": True})
        until(lambda: self.api(entry, "/api/status")["held"], what="cortex to stay for it")
        self.assertIsNone(proc.poll())
        # and the person is told it is still there, once
        until(lambda: os.path.exists(f"{self.home}/notified"), what="the notification")
        self.assertIn("cortex is still running", text(f"{self.home}/notified"))
        self.assertIn("1 terminal (1 running something)", text(f"{self.home}/notified"))

        # `cortex --running` knows of it
        listed = self.cortex("--running", wait=True)
        self.assertIn("proj", listed.stdout)
        self.assertIn("1 terminal (1 running something), no window open", listed.stdout)

        # `cortex` for the same folder opens a window on it rather than starting another
        opened_before = text(f"{self.home}/opened").count("opened")
        again = self.cortex("-w", "tab", "-b", self.browser, "--no-links", self.root, wait=True)
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertIn("already running", again.stdout)
        self.assertEqual(text(f"{self.home}/opened").count("opened"), opened_before + 1)
        self.assertEqual(len([n for n in os.listdir(f"{self.home}/run/cortex")
                              if n.endswith(".json")]), 1)

        # the page it opens is told so, and the terminal is still there, still running
        page = urlopen(f"http://127.0.0.1:{entry['port']}/?t={entry['token']}", timeout=10).read().decode()
        cfg = json.loads(page.split("window.CORTEX = ")[1].split(";</script>")[0])
        self.assertTrue(cfg["kept"])
        self.assertTrue(cfg["keeps"])
        self.api(entry, "/api/hello?c=w2", {})
        status = self.api(entry, "/api/status")
        self.assertEqual((status["terminals"], status["running"], status["windows"]), (1, 1, 1))
        until(lambda: not self.api(entry, "/api/status")["held"], what="it to see someone is back")

        # said from a terminal, heard by the window: a file to show, something to be told
        with open(f"{self.root}/app.py", "w") as fh:
            fh.write("one\ntwo\nthree\n")
        start = self.api(entry, "/api/inbox?since=-1")["seq"]
        shown = self.cortex("--show", f"{self.root}/app.py:3", wait=True)
        self.assertEqual(shown.returncode, 0, shown.stderr)
        self.assertIn("shown in", shown.stdout)
        said = subprocess.run([sys.executable, CORTEX, "--notify", "the build is green"],
                              env=self.env, cwd=self.root, capture_output=True, text=True, timeout=60)
        self.assertEqual(said.returncode, 0, said.stderr)
        heard = self.api(entry, f"/api/inbox?since={start}&wait=5")["items"]
        self.assertEqual([(i["kind"], i.get("path"), i.get("line"), i.get("text")) for i in heard],
                         [("open", f"{self.root}/app.py", 3, None),
                          ("notify", None, None, "the build is green")])
        until(lambda: "the build is green" in text(f"{self.home}/notified"), what="it on the desktop")
        outside = self.cortex("--show", "/etc/hostname", wait=True)
        self.assertEqual(outside.returncode, 1)
        self.assertIn("no cortex is running for", outside.stderr)

        # --new starts another all the same, and --stop ends the ones for this folder
        other = self.cortex("-w", "app", "-b", self.browser, "--no-links", "--no-watch",
                            "--new", self.root)
        until(lambda: len([n for n in os.listdir(f"{self.home}/run/cortex")
                           if n.endswith(".json")]) == 2, 30, "a second cortex")
        stopped = self.cortex("--stop", self.root, wait=True)
        self.assertEqual(stopped.stdout.count("stopped"), 2, stopped.stdout)
        proc.wait(timeout=15)
        other.wait(timeout=15)
        self.assertEqual([n for n in os.listdir(f"{self.home}/run/cortex")
                          if n.endswith(".json")], [])

    def test_asked_not_to_it_ends_with_its_window_whatever_is_running(self):
        proc = self.cortex("-w", "app", "-b", self.browser, "--no-links", "--no-watch",
                           "--no-keep", self.root)
        path = f"{self.home}/run/cortex/{proc.pid}.json"
        until(lambda: os.path.exists(path) or proc.poll() is not None, 30, "cortex to start")
        entry = load(path)
        self.api(entry, "/api/hello?c=w1", {})
        term = self.api(entry, "/api/term", {"op": "open", "cwd": self.root})
        self.api(entry, "/api/term", {"op": "input", "id": term["id"], "data": "sleep 600\r"})
        until(lambda: self.api(entry, "/api/status")["running"] == 1, what="the sleep")
        self.api(entry, "/api/quit", {"closed": True})
        proc.wait(timeout=15)


if __name__ == "__main__":
    unittest.main(verbosity=1)
