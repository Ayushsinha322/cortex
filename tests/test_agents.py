#!/usr/bin/env python3
"""Tests for agents: their worktrees, their conversations, and what the
window is told about both. Standard library only.

Run: python3 tests/test_agents.py   (or tests/run)

No real agent is run, and none need be installed. Each test puts stand-ins on
the PATH -- small scripts that speak the same lines Claude Code and Codex
speak, and do to the folder what they say they do -- so what is tested is
cortex's half of the conversation: what it starts, what it sends, what it
makes of the answers, and what ends up on disk. Every test works in its own
throwaway folders; nothing touches the real home directory.
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
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

from cortex import agents, desktop, worktrees
from cortex.actions import ActionRunner
from cortex.agents import Pool, read_claude, read_codex
from cortex.lifecycle import Lifecycle
from cortex.links import LinkIndex
from cortex.scanner import Scanner
from cortex.server import Context, serve
from cortex.watch import Watcher
from cortex.worktrees import GitError

GIT = shutil.which("git") is not None

# Speaks `claude -p --input-format stream-json --output-format stream-json`.
# What it does depends on words in the message: "edit" changes a.txt, "new"
# writes made.txt, "run" asks leave to run a command, "hang" waits to be
# interrupted, "boom" dies.
FAKE_CLAUDE = r'''#!/usr/bin/env python3
import json, os, sys
def out(o):
    sys.stdout.write(json.dumps(o) + "\n"); sys.stdout.flush()
with open(os.environ["FAKE_LOG"], "a") as fh:
    fh.write(json.dumps({"argv": sys.argv[1:], "cwd": os.getcwd()}) + "\n")
sid = sys.argv[sys.argv.index("--resume") + 1] if "--resume" in sys.argv else "sess-" + str(os.getpid())
n = 0
def tool(name, given):
    global n
    n += 1
    tid = "toolu_%d" % n
    out({"type": "assistant", "message": {"content": [
        {"type": "tool_use", "id": tid, "name": name, "input": given}]}})
    return tid
def result(tid, text, bad=False):
    out({"type": "user", "message": {"content": [
        {"type": "tool_result", "tool_use_id": tid, "content": text, "is_error": bad}]}})
def turn(bad=False):
    out({"type": "result", "subtype": "success", "is_error": bad,
         "total_cost_usd": 0.01, "duration_ms": 5, "session_id": sid})
for line in sys.stdin:
    msg = json.loads(line)
    if msg.get("type") != "user":
        continue
    text = msg["message"]["content"]
    out({"type": "system", "subtype": "init", "session_id": sid})
    if "boom" in text:
        sys.stderr.write("the model is unavailable\n"); sys.exit(3)
    if "read" in text:
        tid = tool("Read", {"file_path": os.path.join(os.getcwd(), "b.txt")})
        result(tid, "1\tbee")
    if "edit" in text:
        path = os.path.join(os.getcwd(), "a.txt")
        tid = tool("Edit", {"file_path": path, "old_string": "one", "new_string": "ONE"})
        with open(path) as fh: body = fh.read()
        with open(path, "w") as fh: fh.write(body.replace("one", "ONE"))
        result(tid, "updated")
    if "new" in text:
        path = os.path.join(os.getcwd(), "made.txt")
        tid = tool("Write", {"file_path": path, "content": "made\n"})
        with open(path, "w") as fh: fh.write("made\n")
        result(tid, "created")
    if "run" in text:
        for _ in range(2 if "twice" in text else 1):
            given = {"command": "touch ran.txt", "description": "leave a mark"}
            tid = tool("Bash", given)
            out({"type": "control_request", "request_id": "req-" + tid,
                 "request": {"subtype": "can_use_tool", "tool_name": "Bash",
                             "input": given, "tool_use_id": tid}})
            reply = json.loads(sys.stdin.readline())
            verdict = reply["response"]["response"]
            if verdict["behavior"] == "allow":
                open(os.path.join(os.getcwd(), "ran.txt"), "a").write("x")
                result(tid, "(no output)")
            else:
                result(tid, verdict.get("message", "denied"), bad=True)
    if "hang" in text:
        tool("Bash", {"command": "sleep 1000"})
        reply = json.loads(sys.stdin.readline())      # the interrupt
        out({"type": "control_response", "response": {"subtype": "success",
             "request_id": reply.get("request_id")}})
        turn(bad=True)
        continue
    out({"type": "assistant", "message": {"content": [
        {"type": "text", "text": "did: " + text}]}})
    turn()
'''

# Speaks `codex exec --json`: one turn per process.
FAKE_CODEX = r'''#!/usr/bin/env python3
import json, os, sys, time
def out(o):
    sys.stdout.write(json.dumps(o) + "\n"); sys.stdout.flush()
with open(os.environ["FAKE_LOG"], "a") as fh:
    fh.write(json.dumps({"argv": sys.argv[1:], "cwd": os.getcwd()}) + "\n")
prompt = sys.argv[-1]
out({"type": "thread.started", "thread_id": "thread-7"})
out({"type": "turn.started"})
if "fail" in prompt:
    out({"type": "error", "message": "no such model"})
    out({"type": "turn.failed", "error": {"message": "no such model"}})
    sys.exit(1)
if "sleep" in prompt:
    time.sleep(60)
out({"type": "item.started", "item": {"id": "item_1", "type": "command_execution",
     "command": "ls", "status": "in_progress"}})
out({"type": "item.completed", "item": {"id": "item_1", "type": "command_execution",
     "command": "ls", "aggregated_output": "a.txt\n", "exit_code": 0, "status": "completed"}})
with open("codex.txt", "w") as fh: fh.write("from codex\n")
out({"type": "item.completed", "item": {"id": "item_2", "type": "file_change",
     "changes": [{"path": os.path.join(os.getcwd(), "codex.txt"), "kind": "add"}]}})
out({"type": "item.completed", "item": {"id": "item_3", "type": "agent_message",
     "text": "wrote codex.txt"}})
out({"type": "turn.completed", "usage": {"input_tokens": 1, "output_tokens": 1}})
'''

# Speaks nothing in particular: prints, in colour, and changes a file.
FAKE_PLAIN = r'''#!/usr/bin/env python3
import sys
print("\x1b[32mworking on:\x1b[0m " + sys.argv[1])
open("plain.txt", "w").write("plain\n")
print("done")
'''


def write(path, text=""):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return path


def read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def git(cwd, *args):
    return subprocess.run(["git", "-C", cwd, *args], check=True,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE).stdout.decode()


def until(test, seconds=10.0, what="it"):
    """Wait for something to become true, with a deadline rather than a sleep."""
    deadline = time.time() + seconds
    while time.time() < deadline:
        got = test()
        if got:
            return got
        time.sleep(0.02)
    raise AssertionError(f"timed out waiting for {what}")


class Fixture(unittest.TestCase):
    """A repository with two files in it, stand-in agents on the PATH, and
    every place cortex would write under the home directory pointed away."""

    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="cortex-agents-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.repo = f"{self.tmp}/proj"
        write(f"{self.repo}/a.txt", "one\ntwo\n")
        write(f"{self.repo}/b.txt", "bee\n")
        write(f"{self.repo}/sub/c.txt", "sea\n")
        git(self.repo, "init", "-q", "-b", "main")
        git(self.repo, "config", "user.name", "Tester")
        git(self.repo, "config", "user.email", "t@example.com")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-qm", "start")

        self.bin = f"{self.tmp}/bin"
        for name, body in (("claude", FAKE_CLAUDE), ("codex", FAKE_CODEX),
                           ("fake-plain", FAKE_PLAIN)):
            path = write(f"{self.bin}/{name}", body)
            os.chmod(path, 0o755)
        self.log = f"{self.tmp}/launches.jsonl"
        old = {k: os.environ.get(k) for k in ("PATH", "FAKE_LOG")}
        os.environ["PATH"] = self.bin + os.pathsep + old["PATH"]
        os.environ["FAKE_LOG"] = self.log
        self.addCleanup(lambda: [os.environ.__setitem__(k, v) if v is not None
                                 else os.environ.pop(k, None) for k, v in old.items()])

        self.config = write(f"{self.tmp}/agents.json", json.dumps(
            {"plain": {"label": "Plain", "argv": ["fake-plain", "{prompt}"]}}))
        self.state_file = f"{self.tmp}/state/agents.json"
        self.trees = f"{self.tmp}/trees"
        self.notes = []
        self.pool = self.new_pool()

    def new_pool(self, **kw):
        pool = Pool(notify=lambda title, body: self.notes.append((title, body)),
                    config=self.config, state_file=self.state_file,
                    tree_root=self.trees, **kw)
        self.addCleanup(pool.stop_all)
        return pool

    def launches(self):
        try:
            with open(self.log) as fh:
                return [json.loads(line) for line in fh]
        except OSError:
            return []

    def start(self, prompt, agent="claude", **kw):
        got = self.pool.start(agent, prompt, kw.pop("folder", self.repo), **kw)
        self.assertTrue(got.get("ok"), got)
        return self.pool.get(got["id"])

    def settled(self, s, state="idle"):
        until(lambda: s.state == state and not s.stats_busy, what=f"{s.title} to be {state}")
        return s

    def kinds(self, s):
        return [e["k"] for e in self.pool.events(s.id)["events"]]


# ------------------------------------------------------------ the worktrees

@unittest.skipUnless(GIT, "git is not installed")
class TestWorktrees(Fixture):
    def test_a_name_for_a_branch_out_of_anything(self):
        self.assertEqual(worktrees.slug("Fix the Login bug!"), "fix-the-login-bug")
        self.assertEqual(worktrees.slug("  "), "task")
        self.assertEqual(worktrees.slug("日本語"), "task")
        self.assertLessEqual(len(worktrees.slug("word " * 40)), 40)

    def test_a_copy_is_a_branch_at_the_commit_you_are_on(self):
        made = worktrees.create(self.repo, "Fix login", self.trees)
        self.assertEqual(made["branch"], "cortex/fix-login")
        self.assertEqual(made["base"], git(self.repo, "rev-parse", "HEAD").strip())
        self.assertTrue(os.path.isfile(f"{made['tree']}/a.txt"))
        self.assertEqual(worktrees.branch_of(made["tree"]), "cortex/fix-login")
        # and the same name again gets a branch of its own
        again = worktrees.create(self.repo, "Fix login", self.trees)
        self.assertEqual(again["branch"], "cortex/fix-login-2")

    def test_a_repository_with_no_commits_says_so(self):
        empty = f"{self.tmp}/empty"
        os.makedirs(empty)
        git(empty, "init", "-q")
        with self.assertRaises(GitError) as raised:
            worktrees.create(empty, "x", self.trees)
        self.assertIn("no commits", str(raised.exception))

    def test_what_changed_counts_untracked_files_and_leaves_the_index_alone(self):
        made = worktrees.create(self.repo, "work", self.trees)
        tree = made["tree"]
        write(f"{tree}/a.txt", "ONE\ntwo\nthree\n")
        write(f"{tree}/fresh.txt", "new\n")
        os.remove(f"{tree}/b.txt")
        before = git(tree, "status", "--porcelain")
        got = worktrees.changes(tree, made["base"])
        by = {f["path"]: f for f in got["files"]}
        self.assertEqual(by["a.txt"]["status"], "changed")
        self.assertEqual((by["a.txt"]["added"], by["a.txt"]["removed"]), (2, 1))
        self.assertEqual(by["fresh.txt"]["status"], "new")
        self.assertEqual(by["b.txt"]["status"], "deleted")
        self.assertEqual(got["added"], 3)
        # nothing was staged by looking
        self.assertEqual(git(tree, "status", "--porcelain"), before)
        self.assertEqual(git(tree, "diff", "--cached", "--name-only"), "")

    def test_what_running_things_leaves_behind_is_not_the_agents_work(self):
        made = worktrees.create(self.repo, "work", self.trees)
        tree = made["tree"]
        write(f"{tree}/__pycache__/a.cpython-313.pyc", "junk")
        write(f"{tree}/sub/node_modules/dep/index.js", "junk")
        write(f"{tree}/sub/real.js", "work\n")
        self.assertEqual([f["path"] for f in worktrees.changes(tree, made["base"])["files"]],
                         ["sub/real.js"])
        worktrees.apply_to(self.repo, tree, made["base"])
        self.assertFalse(os.path.exists(f"{self.repo}/__pycache__"))
        worktrees.commit_all(tree, "work")
        self.assertEqual(git(tree, "show", "--name-only", "--format=", "HEAD").split(),
                         ["sub/real.js"])

    def test_one_files_patch(self):
        made = worktrees.create(self.repo, "work", self.trees)
        write(f"{made['tree']}/a.txt", "ONE\ntwo\n")
        got = worktrees.patch(made["tree"], made["base"], "a.txt")
        self.assertIn("-one", got["patch"])
        self.assertIn("+ONE", got["patch"])
        self.assertFalse(got["truncated"])

    def test_applying_puts_the_change_in_your_folder_uncommitted(self):
        made = worktrees.create(self.repo, "work", self.trees)
        write(f"{made['tree']}/a.txt", "ONE\ntwo\n")
        write(f"{made['tree']}/fresh.txt", "new\n")
        head = git(self.repo, "rev-parse", "HEAD")
        got = worktrees.apply_to(self.repo, made["tree"], made["base"])
        self.assertEqual(got, {"ok": True, "files": 2})
        self.assertEqual(read(f"{self.repo}/a.txt"), "ONE\ntwo\n")
        self.assertEqual(read(f"{self.repo}/fresh.txt"), "new\n")
        self.assertEqual(git(self.repo, "rev-parse", "HEAD"), head)      # no commit made
        self.assertEqual(git(self.repo, "diff", "--cached", "--name-only"), "")  # nor staged

    def test_a_change_that_does_not_fit_changes_nothing(self):
        made = worktrees.create(self.repo, "work", self.trees)
        write(f"{made['tree']}/a.txt", "ONE\ntwo\n")
        write(f"{made['tree']}/fresh.txt", "new\n")
        write(f"{self.repo}/a.txt", "uno\ntwo\n")         # you changed the same line
        got = worktrees.apply_to(self.repo, made["tree"], made["base"])
        self.assertFalse(got["ok"])
        self.assertTrue(got["conflict"])
        self.assertEqual(read(f"{self.repo}/a.txt"), "uno\ntwo\n")
        self.assertFalse(os.path.exists(f"{self.repo}/fresh.txt"))   # none of it, not half

    def test_keeping_commits_on_the_branch_and_removing_leaves_it(self):
        made = worktrees.create(self.repo, "work", self.trees)
        write(f"{made['tree']}/a.txt", "ONE\ntwo\n")
        worktrees.commit_all(made["tree"], "the work")
        self.assertEqual(worktrees.ahead(made["tree"], made["base"]), 1)
        worktrees.remove(self.repo, made["tree"])
        self.assertFalse(os.path.exists(made["tree"]))
        self.assertIn("cortex/work", git(self.repo, "branch", "--list"))
        self.assertEqual(git(self.repo, "show", "cortex/work:a.txt"), "ONE\ntwo\n")

    def test_discarding_takes_the_branch_too_but_never_your_checkout(self):
        made = worktrees.create(self.repo, "work", self.trees)
        worktrees.remove(self.repo, made["tree"], made["branch"])
        self.assertNotIn("cortex/work", git(self.repo, "branch", "--list"))
        # asked to remove the repository itself, or a branch it did not make
        worktrees.remove(self.repo, self.repo, "main")
        self.assertTrue(os.path.isfile(f"{self.repo}/a.txt"))
        self.assertIn("main", git(self.repo, "branch", "--list"))

    def test_a_picture_of_the_folder_leaves_your_own_changes_out_of_the_count(self):
        write(f"{self.repo}/b.txt", "yours, uncommitted\n")
        base = worktrees.picture(self.repo)
        self.assertEqual(worktrees.changes(self.repo, base)["files"], [])
        write(f"{self.repo}/a.txt", "ONE\ntwo\n")
        self.assertEqual([f["path"] for f in worktrees.changes(self.repo, base)["files"]],
                         ["a.txt"])


# ------------------------------------------------- what an agent's lines mean

class TestReading(unittest.TestCase):
    def test_claude_says_does_and_asks(self):
        say = read_claude({"type": "assistant", "message": {"content": [
            {"type": "thinking", "thinking": "hm"},
            {"type": "text", "text": "On it."},
            {"type": "tool_use", "id": "t1", "name": "Edit",
             "input": {"file_path": "/p/a.py", "old_string": "a", "new_string": "b"}}]}})
        self.assertEqual([e["k"] for e in say], ["say", "tool"])
        self.assertEqual(say[1]["path"], "/p/a.py")
        self.assertTrue(say[1]["write"])
        self.assertEqual(say[1]["detail"], "- a\n+ b")

        ask = read_claude({"type": "control_request", "request_id": "r1", "request": {
            "subtype": "can_use_tool", "tool_name": "Bash",
            "input": {"command": "npm test", "description": "run the tests"}}})
        self.assertEqual(ask[0]["k"], "ask")
        self.assertEqual((ask[0]["id"], ask[0]["title"]), ("r1", "npm test"))

        done = read_claude({"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "t1", "is_error": True,
             "content": [{"type": "text", "text": "no"}, {"type": "image"}]}]}})
        self.assertEqual(done, [{"k": "done", "id": "t1", "ok": False, "text": "no\n[image]"}])

        self.assertEqual(read_claude({"type": "system", "subtype": "init",
                                      "session_id": "s"}), [{"k": "sid", "sid": "s"}])
        end = read_claude({"type": "result", "is_error": False, "total_cost_usd": 0.5})
        self.assertEqual((end[0]["k"], end[0]["ok"], end[0]["cost"]), ("turn", True, 0.5))

    def test_claude_lines_that_are_not_news_are_nothing(self):
        for line in ({"type": "rate_limit_event"}, {"type": "system", "subtype": "hook_started"},
                     {"type": "control_response"}, {}, {"type": "assistant", "message": None}):
            self.assertEqual(read_claude(line), [])

    def test_codex_runs_changes_and_fails(self):
        self.assertEqual(read_codex({"type": "thread.started", "thread_id": "th"}),
                         [{"k": "sid", "sid": "th"}])
        started = read_codex({"type": "item.started", "item": {
            "id": "i1", "type": "command_execution", "command": "pytest"}})
        self.assertEqual((started[0]["k"], started[0]["name"], started[0]["title"]),
                         ("tool", "Bash", "pytest"))
        failed = read_codex({"type": "item.completed", "item": {
            "id": "i1", "type": "command_execution", "exit_code": 2,
            "aggregated_output": "1 failed"}})
        self.assertEqual((failed[0]["ok"], failed[0]["text"]), (False, "1 failed"))
        change = read_codex({"type": "item.completed", "item": {
            "type": "file_change", "changes": [{"path": "/p/x.py", "kind": "update"}]}})
        self.assertEqual((change[0]["path"], change[0]["write"]), ("/p/x.py", True))
        bad = read_codex({"type": "turn.failed", "error": {"message": "quota"}})
        self.assertEqual((bad[0]["ok"], bad[0]["text"]), (False, "quota"))
        # reasoning is the agent talking to itself
        self.assertEqual(read_codex({"type": "item.completed", "item": {
            "type": "reasoning", "text": "…"}}), [])


class TestNaming(unittest.TestCase):
    def test_a_task_is_called_by_how_it_starts(self):
        name = agents.name_for
        self.assertEqual(name("Fix the login bug. Then run the tests."), "Fix the login bug")
        self.assertEqual(name("edit"), "edit")
        self.assertEqual(name("\n  second line is the first with words\nthird"),
                         "second line is the first with words")
        long = name("Refactor the whole of the authentication module so that it no longer")
        self.assertLessEqual(len(long), 49)
        self.assertTrue(long.endswith("…"))
        self.assertNotIn("  ", long)
        self.assertEqual(name("   "), "task")


class TestWhichAgents(Fixture):
    def test_installed_ones_and_your_own_are_offered(self):
        ids = [a["id"] for a in self.pool.agents()]
        self.assertIn("claude", ids)
        self.assertIn("codex", ids)
        self.assertIn("plain", ids)
        plain = next(a for a in self.pool.agents() if a["id"] == "plain")
        self.assertEqual(plain, {"id": "plain", "label": "Plain", "speaks": "plain",
                                 "converses": False, "asks": False})

    def test_a_config_that_names_no_program_offers_nothing(self):
        write(self.config, json.dumps({
            "ghost": {"argv": ["no-such-program-anywhere", "{prompt}"]},
            "../x": {"argv": ["fake-plain"]}, "empty": {"argv": []}, "bad": "string"}))
        ids = [a["id"] for a in agents.available(self.config)]
        self.assertNotIn("ghost", ids)
        self.assertNotIn("../x", ids)
        self.assertNotIn("empty", ids)

    def test_an_agent_that_is_not_installed_cannot_be_started(self):
        got = self.pool.start("nonesuch", "do it", self.repo)
        self.assertFalse(got["ok"])
        self.assertIn("not installed", got["error"])
        self.assertFalse(self.pool.start("claude", "   ", self.repo)["ok"])


# ------------------------------------------------------------ a conversation

@unittest.skipUnless(GIT, "git is not installed")
class TestClaude(Fixture):
    def test_it_works_in_a_copy_and_reports_against_your_files(self):
        s = self.settled(self.start("read and edit the file"))
        self.assertEqual(self.kinds(s), ["user", "tool", "done", "tool", "done", "say", "turn"])
        self.assertEqual(s.mode, "copy")
        self.assertTrue(s.tree.startswith(self.trees))
        self.assertEqual(s.branch, "cortex/read-and-edit-the-file")
        # it changed its copy, and yours is as it was
        self.assertEqual(read(f"{s.tree}/a.txt"), "ONE\ntwo\n")
        self.assertEqual(read(f"{self.repo}/a.txt"), "one\ntwo\n")
        # but what it touched is reported as your files, which is what the graph draws
        self.assertEqual(s.files, {f"{self.repo}/b.txt": "r", f"{self.repo}/a.txt": "w"})
        tool = [e for e in self.pool.events(s.id)["events"] if e["k"] == "tool"][1]
        self.assertEqual(tool["path"], f"{self.repo}/a.txt")
        self.assertEqual(s.stats, {"files": 1, "added": 1, "removed": 1})
        self.assertEqual(s.cost, 0.01)
        self.assertTrue(s.unread)
        self.assertEqual(self.notes[-1][0], "Claude Code finished — read and edit the file")

    def test_it_is_started_the_way_its_permissions_say(self):
        self.settled(self.start("edit"))
        argv = self.launches()[0]["argv"]
        self.assertEqual(argv[argv.index("--permission-mode") + 1], "acceptEdits")
        self.assertIn("stream-json", argv)
        self.settled(self.start("edit again", mode="here"))
        argv = self.launches()[1]["argv"]
        self.assertEqual(argv[argv.index("--permission-mode") + 1], "manual")
        self.settled(self.start("think", perm="plan"))
        argv = self.launches()[2]["argv"]
        self.assertEqual(argv[argv.index("--permission-mode") + 1], "plan")
        self.assertFalse(self.pool.start("claude", "x", self.repo, perm="everything")["ok"])

    def test_it_runs_in_the_same_folder_of_the_copy(self):
        s = self.settled(self.start("look about", folder=f"{self.repo}/sub"))
        self.assertEqual(self.launches()[0]["cwd"], f"{s.tree}/sub")
        self.assertEqual(s.home(f"{s.tree}/sub/c.txt"), f"{self.repo}/sub/c.txt")
        self.assertEqual(s.home("c.txt"), f"{self.repo}/sub/c.txt")
        self.assertEqual(s.home("/etc/hosts"), "/etc/hosts")

    def test_a_question_waits_for_an_answer_and_says_what_it_is_about(self):
        s = self.start("run it")
        until(lambda: s.state == "waiting", what="the question")
        ask = self.pool.state()["sessions"][0]["ask"]
        self.assertEqual((ask["name"], ask["title"]), ("Bash", "touch ran.txt"))
        self.assertNotIn("_input", ask)
        self.assertEqual(self.notes[-1], ("Claude Code needs you — run it",
                                          "wants to run: touch ran.txt"))
        self.assertFalse(os.path.exists(f"{s.tree}/ran.txt"))    # not before it is allowed

        self.assertTrue(self.pool.answer(s.id, ask["id"], True)["ok"])
        self.settled(s)
        self.assertTrue(os.path.exists(f"{s.tree}/ran.txt"))
        self.assertIn("answered", self.kinds(s))
        # the same answer twice is refused
        self.assertFalse(self.pool.answer(s.id, ask["id"], True)["ok"])

    def test_no_means_no_and_the_agent_is_told_why(self):
        s = self.start("run it")
        ask = until(lambda: s.ask, what="the question")
        self.pool.answer(s.id, ask["id"], False, why="not on a friday")
        self.settled(s)
        self.assertFalse(os.path.exists(f"{s.tree}/ran.txt"))
        done = [e for e in self.pool.events(s.id)["events"] if e["k"] == "done"][0]
        self.assertEqual((done["ok"], done["text"]), (False, "not on a friday"))

    def test_always_is_asked_once(self):
        s = self.start("run it twice")
        ask = until(lambda: s.ask, what="the question")
        self.pool.answer(s.id, ask["id"], True, always=True)
        self.settled(s)
        self.assertEqual(self.kinds(s).count("ask"), 1)
        self.assertEqual(read(f"{s.tree}/ran.txt"), "xx")

    def test_no_notification_while_the_window_has_focus(self):
        self.pool.state(focused=True)
        self.settled(self.start("edit"))
        self.assertEqual(self.notes, [])

    def test_a_follow_up_goes_to_the_same_conversation(self):
        s = self.settled(self.start("edit"))
        self.assertTrue(self.pool.send(s.id, "new file please")["ok"])
        until(lambda: self.kinds(s).count("turn") == 2, what="the second turn")
        self.settled(s)
        self.assertEqual(len(self.launches()), 1)            # one process, two turns
        self.assertTrue(os.path.exists(f"{s.tree}/made.txt"))
        self.assertEqual(s.stats["files"], 2)
        self.assertFalse(self.pool.send(s.id, "  ")["ok"])
        self.assertFalse(self.pool.send("nope", "hello")["ok"])

    def test_it_is_picked_up_again_after_a_restart(self):
        s = self.settled(self.start("edit"))
        sid, conversation = s.id, s.sid
        self.pool.stop_all()
        self.assertEqual(stat.S_IMODE(os.stat(self.state_file).st_mode), 0o600)

        self.pool = self.new_pool()
        back = self.pool.get(sid)
        self.assertEqual((back.title, back.state, back.sid), ("edit", "idle", conversation))
        self.assertEqual(self.kinds(back), ["user", "tool", "done", "say", "turn"])
        self.assertEqual(back.files, {f"{self.repo}/a.txt": "w"})
        self.pool.send(sid, "new file")
        until(lambda: self.kinds(back).count("turn") == 2, what="the turn after the restart")
        argv = self.launches()[-1]["argv"]
        self.assertEqual(argv[argv.index("--resume") + 1], conversation)

    def test_stopping_is_not_failing(self):
        s = self.start("hang")
        until(lambda: "tool" in self.kinds(s), what="it to be busy")
        self.notes.clear()
        self.assertTrue(self.pool.stop(s.id)["ok"])
        self.settled(s)
        turn = self.pool.events(s.id)["events"][-1]
        self.assertEqual((turn["k"], turn["ok"], turn["text"]), ("turn", True, "stopped"))
        self.assertEqual(self.notes, [])             # you stopped it; you know

    def test_an_agent_that_dies_says_why(self):
        s = self.start("boom")
        self.settled(s, "failed")
        turn = self.pool.events(s.id)["events"][-1]
        self.assertFalse(turn["ok"])
        self.assertIn("the model is unavailable", turn["text"])
        self.assertEqual(self.notes[-1][0], "Claude Code stopped — boom")

    def test_two_agents_on_one_file_is_a_conflict_before_either_is_merged(self):
        a = self.settled(self.start("edit"))
        b = self.settled(self.start("edit too"))
        c = self.settled(self.start("new file"))
        got = self.pool.state()
        self.assertEqual(got["conflicts"],
                         [{"path": f"{self.repo}/a.txt", "sessions": [a.id, b.id]}])
        self.assertNotEqual(a.color, b.color)
        # one of them kept on its branch is no longer in the way
        self.pool.keep(b.id)
        self.assertEqual(self.pool.state()["conflicts"], [])
        self.assertIsNotNone(c)

    def test_the_feed_waits_and_wakes(self):
        rev = self.pool.state()["rev"]
        began = time.time()
        self.assertEqual(self.pool.state(rev, wait=0.15)["rev"], rev)
        self.assertGreaterEqual(time.time() - began, 0.14)
        got = {}
        waiter = threading.Thread(target=lambda: got.update(self.pool.state(rev, wait=8)))
        waiter.start()
        self.start("edit")
        waiter.join(5)
        self.assertGreater(got["rev"], rev)
        self.assertEqual(len(got["sessions"]), 1)

    def test_events_after_a_number(self):
        s = self.settled(self.start("edit"))
        every = self.pool.events(s.id)["events"]
        self.assertEqual([e["seq"] for e in every], list(range(1, len(every) + 1)))
        self.assertEqual(self.pool.events(s.id, after=every[-2]["seq"])["events"], every[-1:])
        self.assertIsNone(self.pool.events("nope"))


# ------------------------------------------------------------- what it made

@unittest.skipUnless(GIT, "git is not installed")
class TestReviewAndKeeping(Fixture):
    def test_review_lists_what_changed_and_shows_one_file(self):
        s = self.settled(self.start("edit and new"))
        got = self.pool.review(s.id)
        self.assertTrue(got["ok"])
        self.assertEqual([(f["path"], f["status"]) for f in got["files"]],
                         [("a.txt", "changed"), ("made.txt", "new")])
        self.assertEqual(got["files"][0]["home"], f"{self.repo}/a.txt")
        one = self.pool.review(s.id, "a.txt")
        self.assertIn("+ONE", one["patch"])
        for bad in ("/etc/passwd", "../outside", "sub/../../x"):
            self.assertFalse(self.pool.review(s.id, bad)["ok"], bad)

    def test_apply_puts_it_in_your_folder_and_leaves_the_agent_talkable(self):
        s = self.settled(self.start("edit and new"))
        got = self.pool.apply(s.id)
        self.assertEqual(got, {"ok": True, "files": 2})
        self.assertEqual(read(f"{self.repo}/a.txt"), "ONE\ntwo\n")
        self.assertTrue(os.path.exists(f"{self.repo}/made.txt"))
        self.assertIsNone(s.ended)
        self.assertTrue(self.pool.send(s.id, "read")["ok"])

    def test_apply_that_does_not_fit_says_so_and_changes_nothing(self):
        s = self.settled(self.start("edit"))
        write(f"{self.repo}/a.txt", "uno\ntwo\n")
        got = self.pool.apply(s.id)
        self.assertFalse(got["ok"])
        self.assertIn("does not fit", got["error"])
        self.assertEqual(read(f"{self.repo}/a.txt"), "uno\ntwo\n")

    def test_keep_commits_on_its_branch_and_ends_it(self):
        s = self.settled(self.start("edit"))
        tree = s.tree
        got = self.pool.keep(s.id)
        self.assertEqual((got["ok"], got["branch"], got["commits"]),
                         (True, "cortex/edit", 1))
        self.assertFalse(os.path.exists(tree))
        self.assertEqual(git(self.repo, "show", "cortex/edit:a.txt"), "ONE\ntwo\n")
        self.assertEqual(s.ended, "kept")
        self.assertFalse(self.pool.send(s.id, "more")["ok"])
        # forgetting it now leaves the branch: that is where the work is
        self.assertTrue(self.pool.forget(s.id)["ok"])
        self.assertIn("cortex/edit", git(self.repo, "branch", "--list"))

    def test_forgetting_work_that_is_nowhere_else_has_to_be_meant(self):
        s = self.settled(self.start("edit"))
        tree = s.tree
        got = self.pool.forget(s.id)
        self.assertEqual((got["ok"], got.get("needs")), (False, "discard"))
        self.assertTrue(os.path.isdir(tree))
        self.assertTrue(self.pool.forget(s.id, discard=True)["ok"])
        self.assertFalse(os.path.exists(tree))
        self.assertNotIn("cortex/edit", git(self.repo, "branch", "--list"))
        self.assertIsNone(self.pool.get(s.id))

    def test_work_that_was_applied_is_not_work_that_would_be_lost(self):
        s = self.settled(self.start("edit"))
        self.pool.apply(s.id)
        self.assertTrue(self.pool.forget(s.id)["ok"])        # it is all in your folder

        s = self.settled(self.start("new file"))
        self.pool.apply(s.id)
        write(f"{s.tree}/later.txt", "after the apply\n")    # it carried on
        self.assertEqual(self.pool.forget(s.id).get("needs"), "discard")

    def test_an_agent_with_nothing_to_show_is_forgotten_without_asking(self):
        s = self.settled(self.start("read"))
        self.assertTrue(self.pool.forget(s.id)["ok"])

    def test_working_right_here_changes_your_files_and_counts_only_its_own(self):
        write(f"{self.repo}/b.txt", "yours, uncommitted\n")
        s = self.settled(self.start("edit", mode="here"))
        self.assertIsNone(s.tree)
        self.assertEqual(s.perm, "ask")
        self.assertEqual(read(f"{self.repo}/a.txt"), "ONE\ntwo\n")
        self.assertEqual([f["path"] for f in self.pool.review(s.id)["files"]], ["a.txt"])
        self.assertFalse(self.pool.apply(s.id)["ok"])
        self.assertFalse(self.pool.keep(s.id)["ok"])
        self.assertTrue(self.pool.forget(s.id)["ok"])
        self.assertEqual(read(f"{self.repo}/a.txt"), "ONE\ntwo\n")   # forgetting undoes nothing

    def test_a_folder_that_is_not_a_repository_has_no_copy_to_make(self):
        plain = f"{self.tmp}/notes"
        write(f"{plain}/a.txt", "one\n")
        got = self.pool.start("claude", "edit", plain)
        self.assertFalse(got["ok"])
        self.assertIn("not in a git repository", got["error"])
        s = self.settled(self.start("edit", folder=plain, mode="here"))
        self.assertEqual(read(f"{plain}/a.txt"), "ONE\n")
        self.assertEqual(s.files, {f"{plain}/a.txt": "w"})
        self.assertFalse(self.pool.review(s.id)["ok"])


# ------------------------------------------------------------- other agents

@unittest.skipUnless(GIT, "git is not installed")
class TestOtherAgents(Fixture):
    def test_codex_a_turn_per_process_and_resumed_by_its_thread(self):
        s = self.settled(self.start("add a file", agent="codex"))
        self.assertEqual(self.kinds(s), ["user", "tool", "done", "tool", "say", "turn"])
        self.assertEqual(s.sid, "thread-7")
        self.assertEqual(s.files, {f"{self.repo}/codex.txt": "w"})
        first = self.launches()[0]["argv"]
        self.assertEqual(first[:2], ["exec", "--json"])
        self.assertEqual(first[first.index("-s") + 1], "workspace-write")
        self.assertEqual(first[-1], "add a file")

        self.pool.send(s.id, "and another")
        until(lambda: self.kinds(s).count("turn") == 2, what="the second turn")
        again = self.launches()[1]["argv"]
        self.assertEqual(again[:3], ["exec", "resume", "--json"])
        self.assertEqual(again[-2:], ["thread-7", "and another"])

    def test_codex_asked_only_to_plan_cannot_write(self):
        self.settled(self.start("add a file", agent="codex", perm="plan"))
        argv = self.launches()[0]["argv"]
        self.assertEqual(argv[argv.index("-s") + 1], "read-only")

    def test_a_codex_turn_that_fails_says_why(self):
        s = self.settled(self.start("fail now", agent="codex"), "failed")
        turn = self.pool.events(s.id)["events"][-1]
        self.assertEqual((turn["ok"], turn["text"]), (False, "no such model"))

    def test_an_agent_that_only_runs_turns_cannot_be_talked_over(self):
        s = self.start("sleep a while", agent="codex")
        until(lambda: s.state == "working" and s.proc is not None, what="it to start")
        self.assertFalse(self.pool.send(s.id, "hurry")["ok"])
        self.pool.stop(s.id)
        self.settled(s)
        self.assertEqual(self.pool.events(s.id)["events"][-1]["text"], "stopped")

    def test_any_program_at_all_and_git_says_what_it_changed(self):
        s = self.settled(self.start("tidy up", agent="plain"))
        out = [e["text"] for e in self.pool.events(s.id)["events"] if e["k"] == "out"]
        self.assertEqual(out, ["working on: tidy up", "done"])       # no colour codes
        self.assertEqual(s.files, {f"{self.repo}/plain.txt": "w"})
        self.assertEqual(s.stats["files"], 1)
        self.assertEqual(self.launches(), [])        # the stand-in for claude never ran


class TestTerminal(Fixture):
    def test_a_shell_in_its_folder_and_the_agent_itself(self):
        runner = ActionRunner(tty=True)
        self.pool = self.new_pool(runner=runner)
        s = self.settled(self.start("edit", mode="here"))
        got = self.pool.terminal(s.id)
        self.assertEqual((got["ok"], got["how"]), (True, "here"))
        job = runner.q.get_nowait()
        self.assertEqual((job["kind"], job["cwd"], job["hold"]), ("run", self.repo, False))

        got = self.pool.terminal(s.id, takeover=True)
        self.assertTrue(got["ok"], got)
        job = runner.q.get_nowait()
        self.assertEqual(job["argv"][1:], ["--resume", s.sid])
        self.assertEqual(os.path.basename(job["argv"][0]), "claude")
        until(lambda: s.proc is None, what="cortex to let go of the conversation")

    def test_no_terminal_no_offer(self):
        self.pool = self.new_pool(runner=ActionRunner(tty=False, terminal=[]))
        s = self.settled(self.start("edit", mode="here"))
        self.assertFalse(self.pool.terminal(s.id)["ok"])


# ----------------------------------------------------------- over the wire

@unittest.skipUnless(GIT, "git is not installed")
class TestOverHTTP(Fixture):
    read_only = False

    def setUp(self):
        super().setUp()
        old_state, old_data = agents.STATE_FILE, worktrees.DATA_DIR
        agents.STATE_FILE, worktrees.DATA_DIR = self.state_file, f"{self.tmp}/data"
        old_config = agents.AGENTS_FILE
        agents.AGENTS_FILE = self.config
        # the real one would put a notification on the desktop of whoever
        # is running the tests
        old_notify, desktop.notify = desktop.notify, lambda title, body: self.notes.append((title, body))
        self.addCleanup(setattr, desktop, "notify", old_notify)
        self.addCleanup(lambda: (setattr(agents, "STATE_FILE", old_state),
                                 setattr(worktrees, "DATA_DIR", old_data),
                                 setattr(agents, "AGENTS_FILE", old_config)))
        scanner = Scanner(self.repo)
        self.ctx = Context(scanner, LinkIndex(scanner), ActionRunner(tty=True), "tok",
                           title="t", watcher=Watcher(), lifecycle=Lifecycle(grace=0),
                           read_only=self.read_only)
        self.httpd = serve(self.ctx, 0)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, args=(0.05,), daemon=True).start()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)
        self.addCleanup(lambda: self.ctx.agents and self.ctx.agents.stop_all())

    def getj(self, path):
        try:
            with urlopen(f"http://127.0.0.1:{self.port}{path}", timeout=5) as r:
                return r.status, json.loads(r.read())
        except HTTPError as exc:
            return exc.code, json.loads(exc.read() or b"null")

    def post(self, payload):
        req = Request(f"http://127.0.0.1:{self.port}/api/agents?t=tok",
                      data=json.dumps(payload).encode(),
                      headers={"content-type": "application/json"})
        try:
            with urlopen(req, timeout=10) as r:
                return r.status, json.loads(r.read())
        except HTTPError as exc:
            return exc.code, json.loads(exc.read() or b"null")

    def test_start_watch_review_and_apply_through_the_api(self):
        status, got = self.getj("/api/agents?t=tok")
        self.assertEqual((status, got["sessions"]), (200, []))
        self.assertIn("claude", [a["id"] for a in got["agents"]])

        status, got = self.post({"op": "start", "agents": ["claude"], "prompt": "edit",
                                 "folder": self.repo})
        self.assertEqual((status, got["ok"]), (200, True))
        sid = got["ids"][0]
        until(lambda: self.getj("/api/agents?t=tok")[1]["sessions"][0]["state"] == "idle",
              what="the agent to finish")
        _, events = self.getj(f"/api/agent?t=tok&id={sid}&after=0")
        self.assertEqual(events["events"][0]["text"], "edit")
        _, diff = self.getj(f"/api/agent/diff?t=tok&id={sid}")
        self.assertEqual(diff["files"][0]["path"], "a.txt")
        _, one = self.getj(f"/api/agent/diff?t=tok&id={sid}&path={quote('a.txt')}")
        self.assertIn("+ONE", one["patch"])
        self.assertEqual(self.post({"op": "apply", "id": sid})[1]["ok"], True)
        self.assertEqual(read(f"{self.repo}/a.txt"), "ONE\ntwo\n")
        self.assertEqual(self.post({"op": "seen", "id": sid})[1]["ok"], True)
        self.assertEqual(self.getj("/api/agents?t=tok")[1]["sessions"][0]["unread"], False)

    def test_one_task_given_to_two_agents_is_a_race_in_two_copies(self):
        _, got = self.post({"op": "start", "agents": ["claude", "codex"],
                            "prompt": "edit", "folder": self.repo})
        self.assertEqual(len(got["ids"]), 2)
        _, state = self.getj("/api/agents?t=tok")
        a, b = state["sessions"]
        self.assertEqual(a["group"], b["group"])
        self.assertIsNotNone(a["group"])
        self.assertNotEqual(a["tree"], b["tree"])

    def test_what_is_refused(self):
        self.assertEqual(self.getj("/api/agents")[0], 403)                  # no token
        status, got = self.post({"op": "start", "agents": ["claude"], "prompt": "edit",
                                 "folder": self.tmp})                       # outside the root
        self.assertEqual(status, 400)
        self.assertEqual(self.post({"op": "start", "agents": [], "prompt": "x"})[0], 400)
        self.assertEqual(self.post({"op": "start", "agents": ["claude"] * 9,
                                    "prompt": "x"})[0], 400)
        self.assertFalse(self.post({"op": "send", "id": "nope", "text": "x"})[1]["ok"])
        self.assertFalse(self.post({"op": "dance", "id": "nope"})[1]["ok"])
        self.assertEqual(self.getj("/api/agent?t=tok&id=nope")[0], 404)
        self.assertEqual(self.getj("/api/agent?t=tok&id=x&after=soon")[0], 400)
        self.assertEqual(self.launches(), [])


@unittest.skipUnless(GIT, "git is not installed")
class TestReadOnlyOverHTTP(TestOverHTTP):
    read_only = True

    def test_start_watch_review_and_apply_through_the_api(self):
        status, got = self.getj("/api/agents?t=tok")
        self.assertEqual((status, got["off"], got["agents"]), (200, True, []))
        status, got = self.post({"op": "start", "agents": ["claude"], "prompt": "edit"})
        self.assertEqual(status, 403)
        self.assertEqual(self.launches(), [])
        self.assertIsNone(self.ctx.agents)

    test_one_task_given_to_two_agents_is_a_race_in_two_copies = None
    test_what_is_refused = None


if __name__ == "__main__":
    unittest.main(verbosity=1)
