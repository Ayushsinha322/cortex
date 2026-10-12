#!/usr/bin/env python3
"""Tests for counting what is on the disk. Standard library only.

Run: python3 tests/test_du.py   (or tests/run)

Every test sizes its own throwaway tree. The answers are checked against a
second count made the slow and obvious way, rather than against byte counts
written into the test, because how many blocks a file takes depends on the
filesystem the temporary directory happens to be on.
"""

import os
import random
import shutil
import stat
import sys
import tempfile
import threading
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cortex import du
from cortex.du import DiskUsage
from cortex.scanner import Scanner

KB = 1024


def temp_dir(test, prefix="cortex-du-"):
    path = os.path.realpath(tempfile.mkdtemp(prefix=prefix))
    test.addCleanup(shutil.rmtree, path, ignore_errors=True)
    return path


def write(path, size, seed=0):
    """A file of `size` bytes that no filesystem can compress away."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(random.Random(seed).randbytes(size))
    return path


def blocks(st):
    return st.st_blocks * 512 if hasattr(st, "st_blocks") else st.st_size


def on_disk(root):
    """(bytes, files) as `du` would count them: every directory, every
    non-directory, hard links once, symlinks as themselves."""
    seen = set()
    total = files = 0
    for dirpath, dirnames, filenames in os.walk(root):
        total += blocks(os.lstat(dirpath))
        for name in dirnames + filenames:
            st = os.lstat(os.path.join(dirpath, name))
            if stat.S_ISDIR(st.st_mode):
                continue                    # counted when walked into
            files += 1
            if st.st_nlink > 1:
                if (st.st_dev, st.st_ino) in seen:
                    continue
                seen.add((st.st_dev, st.st_ino))
            total += blocks(st)
    return total, files


class Sizing(unittest.TestCase):
    def setUp(self):
        self.root = temp_dir(self)
        self.du = DiskUsage(budget=10)      # long enough to always finish here
        self.addCleanup(self.du.stop)

    def finished(self, path, du=None, within=20.0):
        """Ask until the answer is complete."""
        du = du or self.du
        deadline = time.monotonic() + within
        got = du.get(path)
        while not got["complete"]:
            self.assertLess(time.monotonic(), deadline, "the walk never finished")
            time.sleep(0.01)
            got = du.get(path)
        return got

    def child(self, got, name):
        (match,) = [c for c in got["children"] if c["name"] == name]
        return match


class TestSizes(Sizing):
    def test_sizes_add_up_for_a_small_tree(self):
        r = self.root
        write(f"{r}/notes/a.md", 3 * KB, 1)
        write(f"{r}/notes/deep/b.md", 40 * KB, 2)
        write(f"{r}/app/main.py", 9 * KB, 3)
        write(f"{r}/top.bin", 100 * KB, 4)
        os.makedirs(f"{r}/empty")

        got = self.finished(r)
        self.assertEqual((got["bytes"], got["files"]), on_disk(r))
        self.assertEqual(got["path"], r)
        self.assertEqual({c["name"] for c in got["children"]},
                         {"notes", "app", "top.bin", "empty"})
        # the folder itself, plus everything directly in it
        self.assertEqual(blocks(os.lstat(r)) + sum(c["bytes"] for c in got["children"]),
                         got["bytes"])
        self.assertTrue(all(c["complete"] for c in got["children"]))
        notes = self.child(got, "notes")
        self.assertTrue(notes["dir"])
        self.assertEqual(notes["path"], f"{r}/notes")
        self.assertEqual(notes["bytes"], on_disk(f"{r}/notes")[0])
        self.assertFalse(self.child(got, "top.bin")["dir"])

        sub = self.finished(f"{r}/notes")
        self.assertEqual((sub["bytes"], sub["files"]), on_disk(f"{r}/notes"))

    def test_hidden_and_ignored_folders_are_counted(self):
        r = self.root
        write(f"{r}/.git/objects/pack/pack-1.pack", 64 * KB, 1)
        write(f"{r}/node_modules/left-pad/index.js", 32 * KB, 2)
        write(f"{r}/__pycache__/x.cpython-39.pyc", 8 * KB, 3)
        write(f"{r}/.DS_Store", 4 * KB, 4)
        write(f"{r}/readme.md", 1 * KB, 5)

        got = self.finished(r)
        self.assertEqual((got["bytes"], got["files"]), on_disk(r))
        names = {c["name"] for c in got["children"]}
        self.assertTrue({".git", "node_modules", "__pycache__", ".DS_Store"} <= names)
        self.assertGreaterEqual(self.child(got, ".git")["bytes"], 64 * KB)
        self.assertGreaterEqual(self.child(got, "node_modules")["bytes"], 32 * KB)

    def test_a_symlink_is_counted_as_itself_not_what_it_points_at(self):
        outside = temp_dir(self)
        big = write(f"{outside}/big.bin", 1024 * KB, 1)
        write(f"{outside}/folder/also-big.bin", 1024 * KB, 2)
        os.symlink(big, f"{self.root}/link-to-file")
        os.symlink(f"{outside}/folder", f"{self.root}/link-to-folder")

        got = self.finished(self.root)
        self.assertEqual((got["bytes"], got["files"]), on_disk(self.root))
        self.assertLess(got["bytes"], 64 * KB)
        self.assertEqual(got["files"], 2)
        link = self.child(got, "link-to-folder")
        self.assertFalse(link["dir"])           # a link, not a folder to open
        self.assertLess(link["bytes"], 64 * KB)

    def test_a_hard_linked_file_is_counted_once(self):
        r = self.root
        first = write(f"{r}/a/big.bin", 256 * KB, 1)
        os.link(first, f"{r}/a/same-folder.bin")
        os.makedirs(f"{r}/b")
        os.link(first, f"{r}/b/other-folder.bin")

        got = self.finished(r)
        self.assertEqual((got["bytes"], got["files"]), on_disk(r))
        self.assertEqual(got["files"], 3)       # three names...
        self.assertLess(got["bytes"], 2 * 256 * KB)     # ...one file's worth
        a, b = self.child(got, "a"), self.child(got, "b")
        self.assertLess(a["bytes"] + b["bytes"], 2 * 256 * KB)

    def test_when_the_folder_that_counted_a_hard_link_goes_the_other_link_counts_it(self):
        r = self.root
        first = write(f"{r}/old-backup/big.bin", 256 * KB, 1)
        os.makedirs(f"{r}/new-backup")
        os.link(first, f"{r}/new-backup/big.bin")
        # sized on its own first, so it is the one that counts the file
        # whatever order the directory lists them in
        self.finished(f"{r}/old-backup")
        before = self.finished(r)
        self.assertLess(self.child(before, "new-backup")["bytes"], 256 * KB)

        shutil.rmtree(f"{r}/old-backup")
        self.du.invalidate(r)
        got = self.finished(r)
        self.assertEqual((got["bytes"], got["files"]), on_disk(r))
        self.assertGreaterEqual(self.child(got, "new-backup")["bytes"], 256 * KB)

    def test_children_come_largest_first(self):
        r = self.root
        write(f"{r}/small.txt", 1 * KB, 1)
        write(f"{r}/large.bin", 200 * KB, 2)
        write(f"{r}/medium/inside.bin", 50 * KB, 3)
        write(f"{r}/tiny-but-named-first.txt", 10, 4)

        got = self.finished(r)
        sizes = [c["bytes"] for c in got["children"]]
        self.assertEqual(sizes, sorted(sizes, reverse=True))
        self.assertEqual([c["name"] for c in got["children"]][:2], ["large.bin", "medium"])


class TestPartialAnswers(Sizing):
    def test_a_slow_answer_comes_in_parts_then_completes(self):
        r = self.root
        for d in range(40):
            os.makedirs(f"{r}/d{d:02}/nested")
            for f in range(50):
                with open(f"{r}/d{d:02}/f{f}", "wb") as fh:
                    fh.write(b"x" * (f + 1))
                with open(f"{r}/d{d:02}/nested/g{f}", "wb") as fh:
                    fh.write(b"y")
        hasty = DiskUsage(budget=0)
        self.addCleanup(hasty.stop)

        first = hasty.get(r)
        self.assertFalse(first["complete"])
        answers = [first]
        deadline = time.monotonic() + 30
        while not answers[-1]["complete"]:
            self.assertLess(time.monotonic(), deadline, "the walk never finished")
            time.sleep(0.005)
            answers.append(hasty.get(r))

        totals = [a["bytes"] for a in answers]
        self.assertEqual(totals, sorted(totals), "a later answer said less")
        self.assertEqual((answers[-1]["bytes"], answers[-1]["files"]), on_disk(r))
        self.assertEqual(answers[-1]["files"], 4000)
        self.assertTrue(all(c["complete"] for c in answers[-1]["children"]))

    def test_sizing_a_parent_reuses_what_was_learned_about_its_children(self):
        r = self.root
        write(f"{r}/sub/deeper/a.bin", 8 * KB, 1)
        write(f"{r}/sub/b.bin", 8 * KB, 2)
        write(f"{r}/other/c.bin", 8 * KB, 3)
        self.finished(f"{r}/sub")

        read = []
        real = du._scan
        def counting(path):
            read.append(path)
            return real(path)
        old, du._scan = du._scan, counting
        self.addCleanup(setattr, du, "_scan", old)

        got = self.finished(r)
        self.assertEqual(got["bytes"], on_disk(r)[0])
        self.assertEqual(sorted(read), [r, f"{r}/other"])

    def test_many_requests_at_once_agree(self):
        r = self.root
        for i in range(20):
            write(f"{r}/d{i}/f.bin", (i + 1) * KB, i)
        want = on_disk(r)
        results, errors = [], []

        def ask():
            try:
                results.append(self.finished(r))
            except BaseException as exc:    # surfaced below, on the main thread
                errors.append(exc)

        threads = [threading.Thread(target=ask) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(30)
        self.assertEqual(errors, [])
        self.assertEqual([(g["bytes"], g["files"]) for g in results], [want] * 8)


class TestChanges(Sizing):
    def test_invalidate_makes_a_change_visible(self):
        r = self.root
        write(f"{r}/sub/a.bin", 16 * KB, 1)
        write(f"{r}/b.bin", 16 * KB, 2)
        before = self.finished(r)

        write(f"{r}/sub/new.bin", 512 * KB, 3)
        # nothing in the folder asked about changed, so the total is remembered
        self.assertEqual(self.du.get(r)["bytes"], before["bytes"])

        self.du.invalidate(f"{r}/sub")
        after = self.finished(r)
        self.assertEqual((after["bytes"], after["files"]), on_disk(r))
        self.assertGreaterEqual(after["bytes"] - before["bytes"], 512 * KB)

    def test_a_folder_whose_listing_changed_is_reread_when_asked_about(self):
        r = self.root
        write(f"{r}/a.bin", 8 * KB, 1)
        # an old timestamp, so the new file is sure to move it on a disk
        # whose clock only ticks every few milliseconds
        os.utime(r, ns=(10**18, 10**18))
        before = self.finished(r)
        write(f"{r}/b.bin", 256 * KB, 2)
        after = self.finished(r)
        self.assertEqual((after["bytes"], after["files"]), on_disk(r))
        self.assertGreater(after["bytes"], before["bytes"])

    def test_invalidating_a_file_rereads_the_folder_it_is_in(self):
        r = self.root
        target = write(f"{r}/sub/grows.bin", 4 * KB, 1)
        before = self.finished(r)
        write(target, 300 * KB, 2)
        self.du.invalidate(target)
        after = self.finished(r)
        self.assertEqual((after["bytes"], after["files"]), on_disk(r))
        self.assertGreater(after["bytes"], before["bytes"])

    def test_a_deleted_folder_drops_out(self):
        r = self.root
        write(f"{r}/keep/a.bin", 8 * KB, 1)
        write(f"{r}/gone/deep/b.bin", 200 * KB, 2)
        self.finished(r)
        shutil.rmtree(f"{r}/gone")
        self.du.invalidate(r)
        got = self.finished(r)
        self.assertEqual((got["bytes"], got["files"]), on_disk(r))
        self.assertEqual([c["name"] for c in got["children"]], ["keep"])


class TestBoundaries(Sizing):
    def test_a_path_outside_the_folder_is_refused(self):
        guarded = DiskUsage(inside=Scanner(self.root).inside)
        self.addCleanup(guarded.stop)
        elsewhere = temp_dir(self)
        with self.assertRaises(ValueError):
            guarded.get(elsewhere)
        with self.assertRaises(ValueError):
            guarded.get(os.path.join(self.root, "..", os.path.basename(elsewhere)))
        self.assertTrue(guarded.get(self.root)["complete"])

    def test_something_that_is_not_a_folder_is_empty_and_complete(self):
        f = write(f"{self.root}/file.bin", 8 * KB)
        for path in (f, f"{self.root}/missing"):
            self.assertEqual(self.du.get(path), {"path": path, "bytes": 0, "files": 0,
                                                 "complete": True, "children": []})

    def test_an_unreadable_folder_counts_what_could_be_read(self):
        if not hasattr(os, "geteuid") or os.geteuid() == 0:
            self.skipTest("root can read everything")
        r = self.root
        write(f"{r}/open/a.bin", 16 * KB, 1)
        write(f"{r}/locked/secret.bin", 64 * KB, 2)
        os.chmod(f"{r}/locked", 0)
        self.addCleanup(os.chmod, f"{r}/locked", 0o700)

        got = self.finished(r)
        locked = self.child(got, "locked")
        self.assertTrue(locked["dir"])
        self.assertTrue(locked["complete"])
        self.assertLess(locked["bytes"], 64 * KB)   # only the folder itself
        self.assertGreaterEqual(self.child(got, "open")["bytes"], 16 * KB)
        self.assertEqual(self.finished(f"{r}/locked")["children"], [])

    def test_after_stop_it_still_answers_without_waiting(self):
        write(f"{self.root}/a/b.bin", 8 * KB)
        self.du.stop()
        started = time.monotonic()
        got = self.du.get(self.root)
        self.assertLess(time.monotonic() - started, 1.0)
        self.assertFalse(got["complete"])
        self.assertEqual([c["name"] for c in got["children"]], ["a"])


if __name__ == "__main__":
    unittest.main(verbosity=2, buffer=True)
