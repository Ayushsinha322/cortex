#!/usr/bin/env python3
"""Tests for the change feed behind watch mode. Standard library only.

Run: python3 tests/test_activity.py

The same behaviour is checked against both ways of watching: inotify, where
the kernel has it (Linux), and polling, which works everywhere. Every test
builds its own throwaway folder; nothing here touches the real home directory.
Waits are short polls with a deadline, never fixed sleeps, so a slow machine
makes them slower rather than wrong.
"""

import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cortex import activity
from cortex.activity import Activity
from cortex.scanner import Scanner

LINUX = sys.platform.startswith("linux")


def write(path, text=""):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return path


def age(path, seconds=3600):
    """Push a file's timestamps into the past, so a rewrite now cannot share them."""
    then = time.time() - seconds
    os.utime(path, (then, then))


def has(kind, path):
    return lambda events: any(e["kind"] == kind and e["path"] == path for e in events)


def kinds(events, path):
    return [e["kind"] for e in events if e["path"] == path]


class Watching:
    """Shared by both engines. `engine` is set by the classes below."""

    engine = None

    def setUp(self):
        self.root = os.path.realpath(tempfile.mkdtemp(prefix="cortex-activity-"))
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.old = write(f"{self.root}/notes/old.md", "aaaa\n")
        age(self.old)
        write(f"{self.root}/.gitignore", "*.log\n")
        self.seq = 0
        self.markers = 0

    def start(self, scanner=None, **kw):
        scanner = scanner or Scanner(self.root)
        feed = Activity(scanner, engine=self.engine, **kw)
        self.addCleanup(feed.stop)
        if feed.engine != self.engine:
            self.skipTest(f"{self.engine} is not available here")
        feed.start()
        self.assertTrue(feed.ready.wait(5), "the first look never finished")
        if scanner.root == self.root:
            # the poll engine only looks into folders the page has opened
            feed.note_dir(f"{self.root}/notes")
        return feed

    def until(self, feed, done, timeout=3.0):
        """Events from here on, gathered until `done(events)` holds."""
        events = []
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            got = feed.since(self.seq, wait=0.1)
            self.seq = got["seq"]
            events += got["events"]
            if done(events):
                return events
        self.fail(f"gave up waiting; saw {events}")

    def settle(self, feed):
        """Everything still on its way: a marker is written and waited for.

        The kernel reports in order, so once the marker is through, so is
        anything before it. A poll may find things in either order within one
        look, so it is given a few more looks as well.
        """
        self.markers += 1
        marker = write(f"{feed.root}/zz-marker-{self.markers}.md", "x")
        events = self.until(feed, has("created", marker))
        if feed.engine == "poll":
            deadline = time.monotonic() + 4 * activity.POLL_EVERY
            while time.monotonic() < deadline:
                got = feed.since(self.seq, wait=activity.POLL_EVERY)
                self.seq = got["seq"]
                events += got["events"]
        return events

    # -- the basic kinds ---------------------------------------------------

    def test_a_new_file_is_reported_as_created(self):
        feed = self.start()
        path = write(f"{self.root}/notes/new.md", "hello\n")
        events = self.until(feed, has("created", path))
        event = [e for e in events if e["path"] == path][0]
        self.assertFalse(event["dir"])
        self.assertNotIn("from", event)
        self.assertAlmostEqual(event["t"], time.time(), delta=5)

    def test_an_edit_in_place_of_the_same_size_is_noticed(self):
        feed = self.start()
        with open(self.old, "r+", encoding="utf-8") as fh:
            fh.write("bbbb\n")
        self.assertEqual(os.path.getsize(self.old), 5)
        self.until(feed, has("modified", self.old))

    def test_a_deleted_file_is_noticed(self):
        feed = self.start()
        os.unlink(self.old)
        events = self.until(feed, has("deleted", self.old))
        self.assertFalse([e for e in events if e["path"] == self.old][0]["dir"])

    def test_a_new_folder_and_the_file_inside_it_are_both_reported(self):
        feed = self.start()
        folder = f"{self.root}/fresh"
        os.mkdir(folder)
        first = write(f"{folder}/first.md", "1\n")
        events = self.until(feed, lambda ev: has("created", folder)(ev)
                            and has("created", first)(ev))
        self.assertTrue([e for e in events if e["path"] == folder][0]["dir"])
        # and the new folder goes on being watched
        later = write(f"{folder}/later.md", "2\n")
        self.until(feed, has("created", later))

    # -- what is left out --------------------------------------------------

    def test_ignored_paths_are_not_reported(self):
        feed = self.start()
        hidden = [
            write(f"{self.root}/node_modules/dep/index.js", "x"),
            write(f"{self.root}/debug.log", "x"),           # *.log is gitignored
            write(f"{self.root}/.secret", "x"),
            write(f"{self.root}/notes/cache.pyc", "x"),
        ]
        kept = write(f"{self.root}/kept.md", "x")
        events = self.until(feed, has("created", kept)) + self.settle(feed)
        paths = {e["path"] for e in events}
        for path in hidden + [f"{self.root}/node_modules"]:
            self.assertNotIn(path, paths)

    @unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0,
                     "root reads every folder regardless")
    def test_an_unreadable_folder_is_passed_over(self):
        locked = f"{self.root}/locked"
        os.makedirs(f"{locked}/inner")
        os.chmod(locked, 0)
        self.addCleanup(os.chmod, locked, 0o755)
        feed = self.start()
        feed.note_dir(locked)
        path = write(f"{self.root}/notes/still.md", "x")
        self.until(feed, has("created", path))

    def test_a_symlink_back_up_the_tree_is_not_followed(self):
        os.symlink(self.root, f"{self.root}/notes/loop")
        feed = self.start()
        feed.note_dir(f"{self.root}/notes/loop")      # a second name for the root
        path = write(f"{self.root}/notes/loop/via-link.md", "x")
        events = self.until(feed, has("created", f"{self.root}/via-link.md"))
        events += self.settle(feed)
        self.assertFalse([e for e in events if "/loop/" in e["path"]])
        self.assertEqual(os.path.realpath(path), f"{self.root}/via-link.md")

    def test_cortexs_own_half_written_saves_are_never_reported(self):
        # shown hidden files, so it is the rule about cortex's own files that
        # keeps the temporary copy out, not the rule about dotfiles
        feed = self.start(Scanner(self.root, show_hidden=True))
        feed.note_dir(f"{self.root}/notes")
        temp = write(f"{self.root}/notes/.cortex-abc123.save", "saved\n")
        os.replace(temp, self.old)              # how cortex saves a file
        visible = write(f"{self.root}/.visible", "x")
        events = self.until(feed, has("created", visible)) + self.settle(feed)
        for e in events:
            self.assertNotIn(".cortex-", e["path"])
            self.assertNotIn(".cortex-", e.get("from", ""))
        self.assertTrue(kinds(events, self.old))     # the save itself is seen

    # -- coalescing ----------------------------------------------------------

    def test_rapid_writes_to_one_file_are_one_event(self):
        feed = self.start()
        for i in range(20):
            with open(self.old, "a", encoding="utf-8") as fh:
                fh.write(f"{i}\n")
        events = self.until(feed, has("modified", self.old)) + self.settle(feed)
        self.assertEqual(kinds(events, self.old), ["modified"])

    def test_a_write_just_after_a_create_is_part_of_the_create(self):
        feed = self.start()
        path = f"{self.root}/notes/born.md"
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("one\n")
        with open(path, "a", encoding="utf-8") as fh:
            fh.write("two\n")
        events = self.until(feed, has("created", path)) + self.settle(feed)
        self.assertEqual(kinds(events, path), ["created"])

    def test_a_delete_is_never_swallowed(self):
        feed = self.start()
        path = write(f"{self.root}/notes/brief.md", "x")
        os.unlink(path)
        # a poll may never see something this short-lived at all; what it
        # must never do is report the create and not the delete
        events = self.settle(feed)
        seen = kinds(events, path)
        self.assertIn(seen, ([], ["created", "deleted"]))

    # -- since() ---------------------------------------------------------------

    def test_nothing_happened_yet_is_seq_zero_and_no_events(self):
        feed = self.start()
        got = feed.since(0)
        self.assertEqual(got, {"seq": 0, "events": [], "engine": self.engine,
                               "dropped": False})

    def test_since_returns_only_newer_events(self):
        feed = self.start()
        first = write(f"{self.root}/one.md", "1")
        self.until(feed, has("created", first))
        mark = self.seq
        second = write(f"{self.root}/two.md", "2")
        self.until(feed, has("created", second))
        got = feed.since(mark)
        self.assertEqual([e["path"] for e in got["events"]], [second])
        self.assertTrue(all(e["seq"] > mark for e in got["events"]))
        self.assertEqual(got["seq"], got["events"][-1]["seq"])
        self.assertFalse(got["dropped"])
        self.assertEqual(feed.since(got["seq"])["events"], [])

    def test_a_waiting_since_returns_as_soon_as_something_happens(self):
        feed = self.start()
        mark = feed.since(0)["seq"]
        path = f"{self.root}/soon.md"
        timer = threading.Timer(0.1, write, (path, "x"))
        timer.start()
        self.addCleanup(timer.join)
        began = time.monotonic()
        got = feed.since(mark, wait=2)
        self.assertLess(time.monotonic() - began, 1.5)
        self.assertTrue(got["events"])

    def test_a_waiting_since_gives_up_empty_when_nothing_happens(self):
        feed = self.start()
        mark = feed.since(0)["seq"]
        began = time.monotonic()
        got = feed.since(mark, wait=0.3)
        took = time.monotonic() - began
        self.assertGreaterEqual(took, 0.28)
        self.assertLess(took, 1.5)
        self.assertEqual(got["events"], [])
        self.assertEqual(got["seq"], mark)

    def test_a_client_too_far_behind_is_told_it_missed_some(self):
        feed = self.start(cap=3)
        for i in range(6):
            write(f"{self.root}/n{i}.md", "x")
        deadline = time.monotonic() + 3
        while feed.since(0)["seq"] < 6 and time.monotonic() < deadline:
            time.sleep(0.02)
        got = feed.since(0)
        self.assertEqual(got["seq"], 6)
        self.assertTrue(got["dropped"])
        self.assertEqual(len(got["events"]), 3)
        recent = feed.since(got["seq"] - 2)
        self.assertFalse(recent["dropped"])
        self.assertEqual(len(recent["events"]), 2)

    def test_a_client_from_before_a_restart_is_told_to_start_over(self):
        feed = self.start()
        got = feed.since(500, wait=5)          # and is not kept waiting for it
        self.assertTrue(got["dropped"])
        self.assertEqual(got["seq"], 0)

    # -- stopping ---------------------------------------------------------------

    def test_stop_is_prompt_and_can_be_called_twice(self):
        feed = self.start()
        mark = feed.since(0)["seq"]
        waiter = threading.Thread(target=feed.since, args=(mark, 10))
        waiter.start()
        began = time.monotonic()
        feed.stop()
        self.assertLess(time.monotonic() - began, 0.5)
        feed.stop()
        waiter.join(1)
        self.assertFalse(waiter.is_alive(), "a long poll outlived stop()")
        self.assertFalse(feed._thread.is_alive())
        feed.start()                            # a stopped feed stays stopped
        self.assertFalse(feed._thread.is_alive())


@unittest.skipUnless(LINUX, "inotify is Linux only")
class TestInotify(Watching, unittest.TestCase):
    engine = "inotify"

    def test_a_rename_is_one_moved_event(self):
        feed = self.start()
        target = f"{self.root}/notes/renamed.md"
        os.rename(self.old, target)
        events = self.until(feed, has("moved", target))
        moved = [e for e in events if e["path"] == target][0]
        self.assertEqual(moved["from"], self.old)
        self.assertEqual(kinds(events, self.old), [])

    def test_a_renamed_folder_is_watched_under_its_new_name(self):
        os.mkdir(f"{self.root}/before")
        feed = self.start()
        os.rename(f"{self.root}/before", f"{self.root}/after")
        self.until(feed, has("moved", f"{self.root}/after"))
        inside = write(f"{self.root}/after/inside.md", "x")
        self.until(feed, has("created", inside))

    def test_a_folder_moved_out_of_the_root_is_gone_and_no_longer_watched(self):
        outside = os.path.realpath(tempfile.mkdtemp(prefix="cortex-outside-"))
        self.addCleanup(shutil.rmtree, outside, ignore_errors=True)
        write(f"{self.root}/box/item.md", "x")
        feed = self.start()
        os.rename(f"{self.root}/box", f"{outside}/box")
        events = self.until(feed, has("deleted", f"{self.root}/box"))
        write(f"{outside}/box/after.md", "x")
        write(f"{outside}/box/item.md", "changed")
        events += self.settle(feed)
        self.assertFalse([e for e in events if e["path"].startswith(outside)])
        self.assertEqual(kinds(events, f"{self.root}/box/after.md"), [])
        self.assertEqual(kinds(events, f"{self.root}/box/item.md"), [])

    def test_a_file_moved_out_of_the_root_is_deleted(self):
        outside = os.path.realpath(tempfile.mkdtemp(prefix="cortex-outside-"))
        self.addCleanup(shutil.rmtree, outside, ignore_errors=True)
        feed = self.start()
        os.rename(self.old, f"{outside}/old.md")
        self.until(feed, has("deleted", self.old))

    def test_the_watch_cap_leaves_the_deepest_folders_out(self):
        top = f"{self.root}/capped"
        os.makedirs(f"{top}/one/two/three")
        feed = self.start(Scanner(top), max_watches=2)    # top and one
        missed = write(f"{top}/one/two/missed.md", "x")
        seen = write(f"{top}/one/seen.md", "x")
        events = self.until(feed, has("created", seen)) + self.settle(feed)
        self.assertEqual(kinds(events, missed), [])

    def test_note_dir_watches_a_folder_the_cap_left_out(self):
        top = f"{self.root}/capped"
        os.makedirs(f"{top}/one/two/three")
        feed = self.start(Scanner(top), max_watches=2)
        feed.note_dir(f"{top}/one/two")
        path = write(f"{top}/one/two/now-seen.md", "x")
        self.until(feed, has("created", path))
        # only the folder the page opened, not everything under it
        deeper = write(f"{top}/one/two/three/still-missed.md", "x")
        events = self.settle(feed)
        self.assertEqual(kinds(events, deeper), [])

    def test_a_new_folder_past_the_cap_is_an_overflow(self):
        top = f"{self.root}/capped"
        os.makedirs(top)
        feed = self.start(Scanner(top), max_watches=1)
        os.mkdir(f"{top}/new")
        events = self.until(feed, has("overflow", top))
        self.assertIn("created", kinds(events, f"{top}/new"))

    def test_a_kernel_overflow_is_passed_on_and_the_tree_looked_at_again(self):
        # A real overflow needs more than 16384 events queued unread, which
        # depends on how fast this machine is; so the event is handed to the
        # watcher directly, with no thread running to race it.
        feed = Activity(Scanner(self.root), engine="inotify")
        self.addCleanup(feed.stop)
        if feed.engine != "inotify":
            self.skipTest("inotify is not available here")
        watcher = feed._watcher
        watcher._walk(self.root, announce=False)
        os.mkdir(f"{self.root}/made-while-deaf")
        watcher._event(-1, activity.IN_Q_OVERFLOW, 0, "")
        events = feed.since(0)["events"]
        self.assertEqual([(e["kind"], e["path"]) for e in events],
                         [("overflow", self.root)])
        self.assertIn(f"{self.root}/made-while-deaf", watcher._wds)

    def test_note_dir_ignores_folders_outside_the_root_or_left_out(self):
        feed = self.start()
        outside = os.path.realpath(tempfile.mkdtemp(prefix="cortex-outside-"))
        self.addCleanup(shutil.rmtree, outside, ignore_errors=True)
        os.makedirs(f"{self.root}/node_modules/pkg")
        for folder in (outside, f"{self.root}/node_modules/pkg", "relative/path",
                       f"{self.root}/no/such/folder"):
            feed.note_dir(folder)
        write(f"{outside}/x.md", "x")
        write(f"{self.root}/node_modules/pkg/x.js", "x")
        events = self.settle(feed)
        self.assertEqual([e["path"] for e in events if "marker" not in e["path"]], [])

    def test_stop_releases_the_inotify_descriptor(self):
        feed = self.start()
        fd = feed._watcher.fd
        os.fstat(fd)
        feed.stop()
        with self.assertRaises(OSError):
            os.fstat(fd)

    def test_stop_without_start_releases_it_too(self):
        feed = Activity(Scanner(self.root), engine="inotify")
        if feed.engine != "inotify":
            self.skipTest("inotify is not available here")
        fd = feed._watcher.fd
        feed.stop()
        with self.assertRaises(OSError):
            os.fstat(fd)


class TestPoll(Watching, unittest.TestCase):
    engine = "poll"

    def setUp(self):
        super().setUp()
        # a look every 50ms rather than every 1.5s, or these would take minutes
        patch = mock.patch.object(activity, "POLL_EVERY", 0.05)
        patch.start()
        self.addCleanup(patch.stop)

    def test_a_rename_is_a_delete_and_a_create(self):
        feed = self.start()
        target = f"{self.root}/notes/renamed.md"
        os.rename(self.old, target)
        events = self.until(feed, lambda ev: has("deleted", self.old)(ev)
                            and has("created", target)(ev))
        self.assertFalse([e for e in events if e["kind"] == "moved"])

    def test_a_folder_is_looked_into_once_the_page_opens_it(self):
        later = write(f"{self.root}/later/a.md", "aaaa")
        age(later)
        feed = self.start()
        write(later, "bbbb")
        events = self.settle(feed)
        self.assertEqual(kinds(events, later), [])
        feed.note_dir(f"{self.root}/later")
        write(later, "cccc")
        self.until(feed, has("modified", later))

    def test_a_huge_folder_is_stat_ed_a_slice_at_a_time(self):
        for i in range(30):
            age(write(f"{self.root}/notes/f{i:02}.md", "aaaa"))
        with mock.patch.object(activity, "STAT_BUDGET", 8):
            feed = self.start()
            listing = feed._watcher._folders[f"{self.root}/notes"]
            # every file is reached in the end, however small the budget...
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                if all(stamp for _, stamp in listing.entries.values()):
                    break
                time.sleep(0.02)
            self.assertEqual(len(listing.entries), 31)
            self.assertTrue(all(stamp for _, stamp in listing.entries.values()))
            # ...and comes round again, so an edit to any of them is seen
            for i in (0, 15, 29):
                path = write(f"{self.root}/notes/f{i:02}.md", "bbbb")
                self.until(feed, has("modified", path))


class TestChoosing(unittest.TestCase):
    def setUp(self):
        self.root = os.path.realpath(tempfile.mkdtemp(prefix="cortex-activity-"))
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)

    def test_an_unknown_engine_is_refused(self):
        with self.assertRaises(ValueError):
            Activity(Scanner(self.root), engine="fsevents")

    def test_the_engine_suits_the_platform(self):
        feed = Activity(Scanner(self.root))
        self.addCleanup(feed.stop)
        self.assertIn(feed.engine, ("inotify", "poll"))
        if not LINUX:
            self.assertEqual(feed.engine, "poll")

    def test_inotify_asked_for_where_there_is_none_falls_back_to_polling(self):
        with mock.patch.object(activity, "_Inotify", side_effect=OSError(24, "no")):
            feed = Activity(Scanner(self.root), engine="inotify")
        self.addCleanup(feed.stop)
        self.assertEqual(feed.engine, "poll")
        self.assertEqual(feed.since(0)["engine"], "poll")


if __name__ == "__main__":
    unittest.main(verbosity=2, buffer=True)
