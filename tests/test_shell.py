#!/usr/bin/env python3
"""Tests for finding and starting cortex's own window. Standard library only.

Run: python3 tests/test_shell.py   (or tests/run)

No Electron is run, and none need be installed: a folder is laid out the way
`npm install` leaves one, with a script standing in for the program, and what
that script was started with is read back.
"""

import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from cortex import cli, shell


def write(path, text=""):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return path


class TestShell(unittest.TestCase):
    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="cortex-shell-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.app = f"{self.tmp}/desktop"
        self.seen = f"{self.tmp}/seen.json"
        write(f"{self.app}/main.js", "// the window\n")
        write(f"{self.app}/node_modules/electron/path.txt", "electron")
        self.exe = write(f"{self.app}/node_modules/electron/dist/electron",
                         "#!/usr/bin/env python3\n"
                         "import json, os, sys\n"
                         f"json.dump({{'argv': sys.argv[1:], 'url': os.environ.get('CORTEX_URL')}},"
                         f" open({self.seen!r}, 'w'))\n")
        os.chmod(self.exe, 0o755)
        env = mock.patch.dict(os.environ)
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("CORTEX_NO_SHELL", None)

    def test_it_is_found_where_npm_leaves_it(self):
        self.assertEqual(shell.find([f"{self.tmp}/nowhere", self.app]), [self.exe, self.app])

    def test_it_is_not_there_until_it_is_whole(self):
        self.assertIsNone(shell.find([f"{self.tmp}/nowhere"]))
        os.chmod(self.exe, 0o644)                       # there, but not a program
        self.assertIsNone(shell.find([self.app]))
        os.chmod(self.exe, 0o755)
        os.remove(f"{self.app}/node_modules/electron/path.txt")     # npm install not finished
        self.assertIsNone(shell.find([self.app]))
        write(f"{self.app}/node_modules/electron/path.txt", "electron")
        os.remove(f"{self.app}/main.js")                # electron, but not cortex's window
        self.assertIsNone(shell.find([self.app]))

    def test_it_can_be_told_to_stay_out_of_it(self):
        os.environ["CORTEX_NO_SHELL"] = "1"
        self.assertIsNone(shell.find([self.app]))

    def test_the_address_goes_in_the_environment_never_on_the_command_line(self):
        url = "http://127.0.0.1:41000/?t=secret-token"
        self.assertTrue(shell.launch(url, [self.exe, self.app]))
        deadline = time.time() + 10
        while not os.path.exists(self.seen) and time.time() < deadline:
            time.sleep(0.02)
        time.sleep(0.05)
        with open(self.seen, encoding="utf-8") as fh:
            got = json.load(fh)
        self.assertEqual(got["url"], url)
        self.assertEqual(got["argv"], [self.app])
        self.assertNotIn("secret-token", " ".join(got["argv"]))

    def test_no_window_is_not_an_error(self):
        self.assertFalse(shell.launch("http://127.0.0.1:1/?t=x", []))
        self.assertFalse(shell.launch("http://127.0.0.1:1/?t=x", [f"{self.tmp}/missing", self.app]))

    def test_the_cli_opens_it_when_asked_and_says_so(self):
        with mock.patch.object(shell, "launch", return_value=True) as launch:
            self.assertEqual(cli.launch_window("http://127.0.0.1:1/?t=x", "desktop", None),
                             "cortex desktop")
        launch.assert_called_once_with("http://127.0.0.1:1/?t=x")

    def test_without_it_the_cli_falls_back_to_a_browsers_window(self):
        opened = []
        fake = write(f"{self.tmp}/bin/google-chrome", "#!/bin/sh\n")
        os.chmod(fake, 0o755)
        with mock.patch.object(shell, "launch", return_value=False), \
                mock.patch.object(cli.shutil, "which",
                                  side_effect=lambda n: fake if n == "google-chrome" else None), \
                mock.patch.object(cli.subprocess, "Popen",
                                  side_effect=lambda argv, **kw: opened.append(argv)), \
                mock.patch.object(cli, "CACHE", f"{self.tmp}/cache"):
            got = cli.launch_window("http://127.0.0.1:1/?t=x", "desktop", None)
        self.assertEqual(got, "google-chrome")
        self.assertTrue(any(a.startswith("--app=") for a in opened[0]), opened)

    def test_the_default_window_is_its_own_once_set_up(self):
        parser = cli.build_parser()
        self.assertIsNone(parser.parse_args([]).window)          # decided at startup
        self.assertEqual(parser.parse_args(["-w", "desktop"]).window, "desktop")
        self.assertEqual(parser.parse_args(["-w", "app"]).window, "app")


if __name__ == "__main__":
    unittest.main(verbosity=1)
