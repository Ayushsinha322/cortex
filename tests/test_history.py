#!/usr/bin/env python3
"""Tests for the folder's past, as cortex reads it from git. Standard library only.

Run: python3 tests/test_history.py   (or tests/run)

Every test builds its own repository in a throwaway directory. git's global
and system configuration are switched off for the length of each test, and
every commit gets a fixed author, committer and date, so nothing here reads
or changes the real home directory and every run sees the same history.
"""

import os
import random
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cortex import history
from cortex.scanner import Scanner

EMPTY = {"repo": None, "head": None, "commits": [], "base": [], "truncated": False}
START = 1700000000              # the first commit's date; each one after is a minute later


def patch(test, obj, name, value):
    old = getattr(obj, name)
    setattr(obj, name, value)
    test.addCleanup(setattr, obj, name, old)


def patch_env(test, **values):
    for key, value in values.items():
        old = os.environ.get(key)
        os.environ[key] = value
        test.addCleanup(_restore_env, key, old)


def _restore_env(key, old):
    if old is None:
        os.environ.pop(key, None)
    else:
        os.environ[key] = old


class Repo:
    """A throwaway repository with a clock that only moves when we commit."""

    def __init__(self, test, prefix="cortex-history-"):
        self.root = os.path.realpath(tempfile.mkdtemp(prefix=prefix))
        test.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.clock = START
        self.git("init", "-q")

    def git(self, *args):
        stamp = f"{self.clock} +0000"
        env = dict(os.environ, GIT_AUTHOR_DATE=stamp, GIT_COMMITTER_DATE=stamp)
        proc = subprocess.run(["git", "-C", self.root] + list(args), env=env,
                              capture_output=True, text=True)
        if proc.returncode != 0:
            raise AssertionError(f"git {' '.join(args)} failed: {proc.stderr}")
        return proc.stdout.strip()

    def path(self, rel):
        return os.path.join(self.root, rel)

    def write(self, rel, text="x\n"):
        full = self.path(rel)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
        return full

    def commit(self, message):
        self.git("add", "-A")
        self.git("commit", "-q", "--no-verify", "-m", message)
        sha = self.git("rev-parse", "HEAD")
        self.clock += 60
        return sha

    def at(self, n):
        """The date of the nth commit made, counting from zero."""
        return START + 60 * n


class GitTest(unittest.TestCase):
    """Real git, cut off from the machine's configuration."""

    def setUp(self):
        if not shutil.which("git"):
            self.skipTest("git not installed")
        here = os.path.realpath(tempfile.gettempdir())
        home = os.path.realpath(tempfile.mkdtemp(prefix="cortex-home-"))
        self.addCleanup(shutil.rmtree, home, ignore_errors=True)
        patch_env(self,
                  # git before 2.32 ignores GIT_CONFIG_GLOBAL and reads ~ instead
                  HOME=home, XDG_CONFIG_HOME=home,
                  GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1",
                  GIT_AUTHOR_NAME="Ada", GIT_AUTHOR_EMAIL="ada@example.com",
                  GIT_COMMITTER_NAME="Ada", GIT_COMMITTER_EMAIL="ada@example.com",
                  # never find a repository above the temporary directory
                  GIT_CEILING_DIRECTORIES=here)


class TestTimeline(GitTest):
    def test_adds_modifies_deletes_and_renames_come_back_in_order(self):
        repo = Repo(self)
        repo.write("a.md", "# A\n")
        repo.write("b.md", "# B\n")
        repo.write("notes/c.md", "# C\n" + "a line that stays put\n" * 20)
        first = repo.commit("start")
        repo.write("a.md", "# A, again\n")
        repo.commit("edit a")
        os.remove(repo.path("b.md"))
        repo.commit("drop b")
        repo.git("mv", "notes/c.md", "notes/d.md")
        last = repo.commit("rename c")

        got = history.timeline(repo.root)
        self.assertEqual(got["repo"], repo.root)
        self.assertEqual(got["head"], last)
        self.assertFalse(got["truncated"])
        self.assertEqual(got["base"], [])       # the root commit is in the list
        self.assertNotIn("error", got)
        self.assertEqual([c["subject"] for c in got["commits"]],
                         ["start", "edit a", "drop b", "rename c"])
        self.assertEqual(got["commits"][0]["sha"], first)
        self.assertEqual([c["t"] for c in got["commits"]],
                         [repo.at(0), repo.at(1), repo.at(2), repo.at(3)])
        self.assertEqual({c["author"] for c in got["commits"]}, {"Ada"})
        p = repo.path
        self.assertEqual([c["changes"] for c in got["commits"]], [
            [["A", p("a.md"), None], ["A", p("b.md"), None],
             ["A", p("notes/c.md"), None]],
            [["M", p("a.md"), None]],
            [["D", p("b.md"), None]],
            [["R", p("notes/d.md"), p("notes/c.md")]],
        ])

    def test_a_subfolder_sees_only_its_own_files(self):
        repo = Repo(self)
        body = "enough text for git to call the moved file the same file\n" * 10
        repo.write("top.md", body)
        repo.write("inner/x.md", body.upper())
        repo.write("inner/y.md", body.title())
        repo.commit("start")
        repo.write("top.md", body + "more\n")
        repo.commit("only outside")
        repo.git("mv", "top.md", "inner/top.md")
        repo.commit("move in")
        repo.git("mv", "inner/x.md", "x.md")
        repo.commit("move out")
        repo.git("mv", "inner/y.md", "inner/z.md")
        repo.commit("move within")

        inner = repo.path("inner")
        got = history.timeline(inner, Scanner(inner).inside)
        self.assertEqual(got["repo"], repo.root)
        self.assertEqual([c["subject"] for c in got["commits"]],
                         ["start", "move in", "move out", "move within"])
        p = repo.path
        self.assertEqual([c["changes"] for c in got["commits"]], [
            [["A", p("inner/x.md"), None], ["A", p("inner/y.md"), None]],
            [["A", p("inner/top.md"), None]],       # arrived from outside
            [["D", p("inner/x.md"), None]],         # left for outside
            [["R", p("inner/z.md"), p("inner/y.md")]],
        ])

    def test_a_rename_with_one_side_out_of_view_is_an_arrival_or_a_departure(self):
        # git's pathspec usually keeps such a pair apart already; `inside`
        # can still split one, and the page must never see the far side
        place = history._Placer("/r/in", "in/", None)
        self.assertEqual(place.change(("R", b"in/new.md", b"elsewhere/old.md")),
                         ["A", "/r/in/new.md", None])
        self.assertEqual(place.change(("R", b"elsewhere/new.md", b"in/old.md")),
                         ["D", "/r/in/old.md", None])
        self.assertIsNone(place.change(("R", b"a/new.md", b"b/old.md")))
        self.assertEqual(place.change(("R", b"in/new.md", b"in/old.md")),
                         ["R", "/r/in/new.md", "/r/in/old.md"])

    def test_a_copy_is_an_add_and_a_type_change_is_a_modify(self):
        place = history._Placer("/r", "", None)
        self.assertEqual(place.change(("C", b"copy.md", b"original.md")),
                         ["A", "/r/copy.md", None])
        self.assertEqual(place.change(("T", b"link", None)), ["M", "/r/link", None])

    def test_unusual_filenames_survive_the_round_trip(self):
        repo = Repo(self)
        names = ["with space.md", "quo\"te's.md", "ünïcødé ✓.md",
                 "new\nline.md", "tab\there.md", "R100", "\x01" + "f" * 40,
                 "back\\slash.md"]
        for i, name in enumerate(names):
            repo.write(name, f"file {i}\n" * 30)
        repo.commit("odd names")
        for name in names:
            repo.git("mv", name, "moved " + name)
        repo.commit("move them all")

        got = history.timeline(repo.root)
        self.assertNotIn("error", got)
        added, moved = (c["changes"] for c in got["commits"])
        self.assertEqual(sorted(added), sorted(["A", repo.path(n), None] for n in names))
        self.assertEqual(sorted(moved), sorted(
            ["R", repo.path("moved " + n), repo.path(n)] for n in names))

    def test_a_folder_with_glob_characters_is_taken_literally(self):
        repo = Repo(self)
        repo.write("a*/one.md")
        repo.write("ab/two.md")
        repo.write("[x]/three.md")
        repo.write("x/four.md")
        repo.commit("start")
        got = history.timeline(repo.path("a*"))
        self.assertEqual(got["commits"][0]["changes"], [["A", repo.path("a*/one.md"), None]])
        got = history.timeline(repo.path("[x]"))
        self.assertEqual(got["commits"][0]["changes"],
                         [["A", repo.path("[x]/three.md"), None]])

    def test_a_limit_keeps_the_newest_commits_and_says_so(self):
        repo = Repo(self)
        repo.write("a.md", "one\n")
        repo.commit("add a")
        repo.write("b.md", "two\n")
        repo.commit("add b")
        repo.write("a.md", "one, edited\n")
        repo.commit("edit a")
        os.remove(repo.path("b.md"))
        repo.commit("drop b")
        repo.write("c.md", "three\n")
        repo.commit("add c")

        got = history.timeline(repo.root, limit=2)
        self.assertTrue(got["truncated"])
        self.assertEqual([c["subject"] for c in got["commits"]], ["drop b", "add c"])
        # what was there when "edit a" had been made
        self.assertEqual(sorted(got["base"]), [repo.path("a.md"), repo.path("b.md")])

        got = history.timeline(repo.root, limit=5)
        self.assertFalse(got["truncated"])      # exactly the limit, and no more
        self.assertEqual(len(got["commits"]), 5)
        self.assertEqual(got["base"], [])

    def test_the_cap_on_changes_keeps_the_newest_commits(self):
        patch(self, history, "MAX_CHANGES", 3)
        repo = Repo(self)
        repo.write("a.md")
        repo.write("b.md")
        repo.commit("two files")
        repo.write("c.md")
        repo.write("d.md")
        repo.commit("two more")
        repo.write("a.md", "edited\n")
        repo.commit("one edit")

        got = history.timeline(repo.root)
        self.assertTrue(got["truncated"])
        self.assertEqual([c["subject"] for c in got["commits"]], ["two more", "one edit"])
        self.assertEqual(sorted(got["base"]), [repo.path("a.md"), repo.path("b.md")])

    def test_one_commit_bigger_than_the_cap_is_kept_but_cut(self):
        patch(self, history, "MAX_CHANGES", 3)
        repo = Repo(self)
        for name in "abcde":
            repo.write(f"{name}.md")
        repo.commit("five files")
        got = history.timeline(repo.root)
        self.assertTrue(got["truncated"])
        self.assertEqual(len(got["commits"]), 1)
        self.assertEqual(len(got["commits"][0]["changes"]), 3)

    def test_the_inside_test_filters_commits_and_base(self):
        repo = Repo(self)
        repo.write("open/a.md")
        repo.write("secret/b.md")
        repo.commit("start")
        repo.write("secret/b.md", "hidden edit\n")
        repo.commit("secret only")
        repo.write("open/a.md", "visible edit\n")
        repo.commit("open only")

        hidden = repo.path("secret")
        def inside(path):
            return not (path == hidden or path.startswith(hidden + os.sep))

        got = history.timeline(repo.root, inside)
        self.assertEqual([c["subject"] for c in got["commits"]], ["start", "open only"])
        everything = [c[1] for commit in got["commits"] for c in commit["changes"]]
        self.assertNotIn(repo.path("secret/b.md"), everything)

        got = history.timeline(repo.root, inside, limit=1)
        self.assertEqual(got["base"], [repo.path("open/a.md")])

    def test_a_merge_is_one_step_on_the_main_line(self):
        repo = Repo(self)
        repo.write("a.md")
        repo.commit("add a")
        trunk = repo.git("symbolic-ref", "--short", "HEAD")
        repo.git("checkout", "-q", "-b", "side")
        repo.write("b.md")
        repo.commit("add b")
        repo.git("checkout", "-q", trunk)
        repo.write("c.md")
        repo.commit("add c")
        repo.git("merge", "-q", "--no-ff", "--no-edit", "side")
        self.assertIn("Merge", repo.git("log", "-1", "--format=%s"))

        # the branch's own commits are not interleaved by date; the merge is
        # the step at which its files arrive on the main line
        got = history.timeline(repo.root, limit=5)
        subjects = [c["subject"] for c in got["commits"]]
        self.assertEqual(subjects[:2], ["add a", "add c"])
        self.assertIn("Merge", subjects[2])
        self.assertEqual(got["commits"][2]["changes"], [["A", repo.path("b.md"), None]])
        self.assertFalse(got["truncated"])

    def test_a_repository_with_no_commits_yet_has_a_root_and_nothing_else(self):
        repo = Repo(self)
        repo.write("draft.md")
        got = history.timeline(repo.root)
        self.assertEqual(got, dict(EMPTY, repo=repo.root))

    def test_a_folder_in_no_repository_has_no_history(self):
        folder = os.path.realpath(tempfile.mkdtemp(prefix="cortex-history-"))
        self.addCleanup(shutil.rmtree, folder, ignore_errors=True)
        with open(os.path.join(folder, "a.md"), "w") as fh:
            fh.write("x\n")
        self.assertEqual(history.timeline(folder), EMPTY)

    def test_a_folder_that_does_not_exist_has_no_history(self):
        self.assertEqual(history.timeline("/nonexistent/cortex/folder"), EMPTY)


class TestWithoutGit(unittest.TestCase):
    def test_no_git_on_the_path_means_no_history(self):
        patch(self, history.shutil, "which", lambda *a, **k: None)
        self.assertEqual(history.timeline(os.getcwd()), EMPTY)

    def test_a_git_that_is_not_there_by_name_means_no_history(self):
        patch(self, history, "GIT", "cortex-there-is-no-such-git")
        self.assertEqual(history.timeline(os.getcwd()), EMPTY)


class TestFailures(GitTest):
    """Whatever goes wrong, the caller gets the empty shape and a reason."""

    def setUp(self):
        super().setUp()
        self.repo = Repo(self)
        self.repo.write("a.md")
        self.repo.commit("start")

    def test_output_git_did_not_mean_to_give_is_a_reason_not_a_crash(self):
        patch(self, history, "FORMAT", "%H")
        got = history.timeline(self.repo.root)
        self.assertTrue(got.pop("error"))
        self.assertEqual(got, EMPTY)

    def test_an_inside_test_that_raises_is_a_reason_not_a_crash(self):
        def inside(path):
            raise RuntimeError("boom")
        got = history.timeline(self.repo.root, inside)
        self.assertTrue(got.pop("error"))
        self.assertEqual(got, EMPTY)

    def test_git_running_out_of_time_is_a_reason_not_a_hang(self):
        patch(self, history, "TIMEOUT", 0.0)
        started = time.monotonic()
        got = history.timeline(self.repo.root)
        self.assertLess(time.monotonic() - started, 5)
        self.assertTrue(got.pop("error"))
        self.assertEqual(got, EMPTY)


class TestParse(unittest.TestCase):
    """The -z stream, built by hand, including the awkward cases."""

    def stream(self, *commits):
        out = b""
        for sha, parents, changes in commits:
            out += b"\x01" + sha + b"\0" + parents + b"\0" + b"1700000000\0Ada\0subject\0"
            if changes:
                out += b"\n" + b"".join(part + b"\0" for part in changes)
        return out.split(b"\0")[:-1]

    def test_a_name_that_looks_like_a_header_is_still_a_name(self):
        sha1, sha2 = b"1" * 40, b"2" * 40
        lookalike = b"\x01" + b"3" * 40
        tokens = self.stream(
            (sha2, sha1, [b"A", lookalike, b"R100", b"old", b"new\nline"]),
            (sha1, b"", []))
        got = list(history._parse(tokens))
        self.assertEqual([c[0] for c in got], ["2" * 40, "1" * 40])
        self.assertEqual(got[0][1], ["1" * 40])
        self.assertEqual(got[0][5], [("A", lookalike, None),
                                     ("R", b"new\nline", b"old")])
        self.assertEqual(got[1][1], [])         # a root commit has no parents
        self.assertEqual(got[1][5], [])

    def test_an_empty_subject_does_not_shift_the_fields(self):
        tokens = (b"\x01" + b"a" * 40 + b"\0\0" + b"1700000000\0Ada\0\0\nM\0f.md\0").split(b"\0")[:-1]
        (got,) = list(history._parse(tokens))
        self.assertEqual(got[4], "")
        self.assertEqual(got[5], [("M", b"f.md", None)])

    def test_a_stream_that_stops_partway_is_refused(self):
        tokens = (b"\x01" + b"a" * 40 + b"\0\0" + b"1700000000\0Ada\0s\0\nR100\0old").split(b"\0")
        with self.assertRaises(history._Failed):
            list(history._parse(tokens))

    def test_a_large_random_set_of_names_parses_back_exactly(self):
        rng = random.Random(7)
        alphabet = [chr(c) for c in range(1, 128) if chr(c) != "/"] + ["é", "✓"]
        names = ["".join(rng.choice(alphabet) for _ in range(rng.randint(1, 12))).encode()
                 for _ in range(300)]
        parts, want = [], []
        for i, name in enumerate(names):
            if i % 3 == 0:
                parts += [b"R087", name, name + b".new"]
                want.append(("R", name + b".new", name))
            else:
                parts += [b"M", name]
                want.append(("M", name, None))
        (got,) = list(history._parse(self.stream((b"b" * 40, b"", parts))))
        self.assertEqual(got[5], want)


if __name__ == "__main__":
    unittest.main(verbosity=2, buffer=True)
