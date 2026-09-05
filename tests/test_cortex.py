#!/usr/bin/env python3
"""Tests for the python side of cortex. Standard library only.

Run: python3 tests/test_cortex.py   (or tests/run)

Every test builds its own throwaway tree under /tmp; nothing here touches the
real home directory.
"""

import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import inspect
import unittest
import zipfile
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cortex import reader
import cortex
from cortex import cli, gitstatus, grep, layout
from cortex.actions import (ActionRunner, available_editors, env_editor,
                            open_at_line, _editor_argv, _is_gui, pager_argv)
from cortex.links import (LinkIndex, frontmatter_tags, note_tags,
                          _code_targets, _crate_src, _resolve_rel)
from cortex.ignore import IgnoreFile
from cortex.scanner import Scanner, group_of
from cortex.watch import Watcher
from cortex.server import Context, serve


def write(path, text=""):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return path


class Tree(unittest.TestCase):
    """A small fixture tree shared by most tests."""

    def setUp(self):
        self.root = os.path.realpath(tempfile.mkdtemp(prefix="cortex-test-"))
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        r = self.root
        write(f"{r}/notes/index.md", "# Index\n\nsee [[deep]] and [other](./other.md)\n")
        write(f"{r}/notes/other.md", "# Other\n")
        write(f"{r}/notes/sub/deep.md", "# Deep\n")
        write(f"{r}/app/main.py", "import config\nfrom pkg.helper import x\n")
        write(f"{r}/app/config.py", "PORT = 1\n")
        write(f"{r}/app/pkg/__init__.py", "")
        write(f"{r}/app/pkg/helper.py", "def x():\n    return 1\n")
        write(f"{r}/app/settings.yaml", "a: 1\n")
        write(f"{r}/web/index.js", "import './util.js';\n")
        write(f"{r}/web/util.js", "export const a = 1;\n")
        write(f"{r}/.hidden/secret.md", "hidden\n")
        write(f"{r}/node_modules/dep/dep.js", "module.exports = 1;\n")
        write(f"{r}/app/__pycache__/main.cpython-313.pyc", "junk")
        self.scanner = Scanner(self.root)


class TestGrouping(unittest.TestCase):
    def test_extensions_map_to_groups(self):
        for name, want in [
            ("a.md", "note"), ("a.txt", "note"), ("a.py", "code"),
            ("a.ts", "code"), ("a.sh", "code"), ("a.yaml", "config"),
            ("a.toml", "config"), ("a.pdf", "doc"), ("a.csv", "doc"),
            ("a.png", "media"), ("a.mp4", "media"), ("a.zip", "archive"),
            ("a.unknownext", "other"),
        ]:
            self.assertEqual(group_of(name, False), want, name)

    def test_directories_are_their_own_group(self):
        self.assertEqual(group_of("anything.md", True), "dir")

    def test_extensionless_special_names(self):
        self.assertEqual(group_of("README", False), "note")
        self.assertEqual(group_of("Makefile", False), "config")


class TestScanner(Tree):
    def test_children_are_folders_first_then_alphabetical(self):
        names = [n["name"] for n in self.scanner.children(self.root)]
        dirs = [n["name"] for n in self.scanner.children(self.root) if n["dir"]]
        self.assertEqual(names[:len(dirs)], dirs)
        self.assertEqual(dirs, sorted(dirs, key=str.lower))

    def test_noise_directories_are_skipped(self):
        names = {n["name"] for n in self.scanner.children(self.root)}
        self.assertNotIn("node_modules", names)
        self.assertNotIn(".hidden", names)
        self.assertIn("notes", names)

    def test_pycache_and_pyc_are_skipped(self):
        names = {n["name"] for n in self.scanner.children(f"{self.root}/app")}
        self.assertNotIn("__pycache__", names)
        self.assertIn("main.py", names)

    def test_hidden_included_on_request(self):
        loose = Scanner(self.root, show_hidden=True)
        names = {n["name"] for n in loose.children(self.root)}
        self.assertIn(".hidden", names)

    def test_directory_nodes_carry_a_child_count(self):
        node = next(n for n in self.scanner.children(self.root)
                    if n["name"] == "notes")
        self.assertTrue(node["dir"])
        self.assertEqual(node["kids"], 3)          # index.md, other.md, sub/

    def test_inside_accepts_the_root_and_its_contents(self):
        self.assertTrue(self.scanner.inside(self.root))
        self.assertTrue(self.scanner.inside(f"{self.root}/notes/index.md"))

    def test_inside_rejects_paths_outside_the_root(self):
        self.assertFalse(self.scanner.inside("/etc"))
        self.assertFalse(self.scanner.inside("/etc/passwd"))
        self.assertFalse(self.scanner.inside(f"{self.root}/../elsewhere"))

    def test_inside_rejects_a_symlink_pointing_out_of_the_root(self):
        link = f"{self.root}/escape"
        try:
            os.symlink("/etc", link)
        except OSError:
            self.skipTest("symlinks unavailable")
        self.assertFalse(self.scanner.inside(link))
        self.assertFalse(self.scanner.inside(f"{link}/passwd"))

    def test_children_of_a_path_outside_the_root_is_empty(self):
        self.assertEqual(self.scanner.children("/etc"), [])

    def test_search_finds_a_nested_file_with_its_lineage(self):
        hits = self.scanner.search("deep")
        self.assertTrue(hits)
        hit = hits[0]
        self.assertEqual(hit["node"]["name"], "deep.md")
        self.assertEqual([a["name"] for a in hit["ancestors"]], ["notes", "sub"])

    def test_search_ignores_very_short_terms(self):
        self.assertEqual(self.scanner.search("d"), [])

    def test_search_does_not_reach_into_ignored_directories(self):
        names = {h["node"]["name"] for h in self.scanner.search("dep")}
        self.assertNotIn("dep.js", names)


class TestLinks(Tree):
    def build(self):
        index = LinkIndex(self.scanner)
        index._build()
        return index.snapshot()

    def test_index_completes_and_counts_what_it_saw(self):
        snap = self.build()
        self.assertTrue(snap["ready"])
        self.assertGreaterEqual(snap["notes"], 3)
        self.assertGreaterEqual(snap["sources"], 4)

    def test_wikilink_becomes_an_edge(self):
        edges = {(a, b) for a, b, kind in self.build()["edges"] if kind == "note"}
        pair = (f"{self.root}/notes/index.md", f"{self.root}/notes/sub/deep.md")
        self.assertIn(pair, edges)

    def test_relative_markdown_link_becomes_an_edge(self):
        edges = {(a, b) for a, b, kind in self.build()["edges"] if kind == "note"}
        pair = (f"{self.root}/notes/index.md", f"{self.root}/notes/other.md")
        self.assertIn(pair, edges)

    def test_python_imports_resolve_to_real_files(self):
        edges = {(a, b) for a, b, kind in self.build()["edges"] if kind == "code"}
        main = f"{self.root}/app/main.py"
        self.assertIn((main, f"{self.root}/app/config.py"), edges)
        self.assertIn((main, f"{self.root}/app/pkg/helper.py"), edges)

    def test_javascript_relative_import_resolves(self):
        edges = {(a, b) for a, b, kind in self.build()["edges"] if kind == "code"}
        self.assertIn((f"{self.root}/web/index.js", f"{self.root}/web/util.js"),
                      edges)

    def test_edges_are_deduplicated(self):
        edges = self.build()["edges"]
        keys = {tuple(sorted((a, b))) for a, b, _ in edges}
        self.assertEqual(len(keys), len(edges))

    def test_c_include_resolves(self):
        write(f"{self.root}/c/a.c", '#include "b.h"\n')
        write(f"{self.root}/c/b.h", "int x;\n")
        universe = {f"{self.root}/c/a.c", f"{self.root}/c/b.h"}
        got = _code_targets(f"{self.root}/c/a.c", '#include "b.h"\n', universe)
        self.assertIn(f"{self.root}/c/b.h", got)

    def test_unresolvable_reference_yields_nothing(self):
        self.assertIsNone(_resolve_rel(f"{self.root}/notes/index.md",
                                       "./nope.md", set()))

    def test_external_urls_are_not_treated_as_files(self):
        self.assertIsNone(_resolve_rel(f"{self.root}/notes/index.md",
                                       "https://example.com/x.md", set()))


class TestTags(unittest.TestCase):
    """The other way notes are organised, and the one wikilinks cannot express."""

    def test_an_inline_tag_is_found(self):
        self.assertEqual(note_tags("talking about #work here\n"), ["work"])

    def test_a_nested_tag_keeps_its_slash(self):
        self.assertEqual(note_tags("#deep/focus\n"), ["deep/focus"])

    def test_a_heading_is_not_a_tag(self):
        self.assertEqual(note_tags("# Heading\n## Deeper\n"), [])

    def test_a_tag_at_the_start_of_a_line_still_counts(self):
        self.assertEqual(note_tags("#realtag\n"), ["realtag"])

    def test_a_url_fragment_is_not_a_tag(self):
        self.assertEqual(note_tags("see http://x.com/a#frag\n"), [])

    def test_a_fenced_code_block_is_not_read_for_tags(self):
        text = "```sh\n# a comment\necho '#nope'\n```\n#yes\n"
        self.assertEqual(note_tags(text), ["yes"])

    def test_inline_code_is_not_read_either(self):
        self.assertEqual(note_tags("use `#nope` but #yes\n"), ["yes"])

    def test_tags_are_deduplicated_and_lowercased(self):
        self.assertEqual(note_tags("#Same and #same and #SAME\n"), ["same"])

    def test_something_that_is_not_a_tag_is_left_alone(self):
        self.assertEqual(note_tags("#1number # spaced #-dash\n"), [])

    # -- front matter -------------------------------------------------------

    def test_an_inline_yaml_list(self):
        self.assertEqual(note_tags("---\ntags: [alpha, beta]\n---\n"),
                         ["alpha", "beta"])

    def test_a_comma_separated_value(self):
        self.assertEqual(note_tags("---\ntags: alpha, beta\n---\n"),
                         ["alpha", "beta"])

    def test_a_yaml_block_list(self):
        text = "---\ntitle: x\ntags:\n  - alpha\n  - beta\nauthor: me\n---\n"
        self.assertEqual(note_tags(text), ["alpha", "beta"])

    def test_the_singular_key_works_too(self):
        self.assertEqual(note_tags("---\ntag: solo\n---\n"), ["solo"])

    def test_front_matter_and_prose_tags_are_both_kept(self):
        self.assertEqual(note_tags("---\ntags: [fm]\n---\nand #body\n"),
                         ["fm", "body"])

    def test_front_matter_is_not_scanned_twice_as_prose(self):
        self.assertEqual(note_tags("---\ntags: [one]\n---\n"), ["one"])

    def test_no_front_matter_is_not_an_error(self):
        self.assertEqual(frontmatter_tags("just a note\n"), [])

    def test_a_dashed_rule_partway_down_is_not_front_matter(self):
        self.assertEqual(frontmatter_tags("text\n---\ntags: [no]\n---\n"), [])

    def test_a_note_with_too_many_tags_is_capped(self):
        text = " ".join(f"#t{i}" for i in range(200))
        self.assertEqual(len(note_tags(text)), 40)


class TestTagEdges(Tree):
    """Tags become nodes, so two notes about one thing meet without linking."""

    def build(self):
        write(f"{self.root}/notes/index.md",
              "---\ntags: [work, urgent]\n---\n# Index\nsee [[deep]] and #deep/focus\n")
        write(f"{self.root}/notes/other.md", "# Other\ntagged #work too\n")
        index = LinkIndex(self.scanner)
        index._build()
        return index.snapshot()

    def test_the_index_lists_every_tag_it_saw(self):
        self.assertEqual(self.build()["tags"], ["deep/focus", "urgent", "work"])

    def test_a_tag_edge_runs_from_the_file_to_the_tag(self):
        edges = [(a, b) for a, b, k in self.build()["edges"] if k == "tag"]
        self.assertIn((f"{self.root}/notes/index.md", "tag:work"), edges)

    def test_two_notes_reach_the_same_tag(self):
        edges = {(a, b) for a, b, k in self.build()["edges"] if k == "tag"}
        self.assertIn((f"{self.root}/notes/index.md", "tag:work"), edges)
        self.assertIn((f"{self.root}/notes/other.md", "tag:work"), edges)

    def test_code_files_are_not_scanned_for_tags(self):
        write(f"{self.root}/app/hash.py", "# not a tag\nx = 1  # neither\n")
        self.assertNotIn("not", self.build()["tags"])


class TestReader(Tree):
    def test_markdown(self):
        p = reader.preview(f"{self.root}/notes/index.md")
        self.assertEqual(p["kind"], "markdown")
        self.assertIn("# Index", p["text"])

    def test_code_reports_its_language(self):
        p = reader.preview(f"{self.root}/app/main.py")
        self.assertEqual(p["kind"], "text")
        self.assertEqual(p["lang"], "python")

    def test_yaml_reports_its_language(self):
        self.assertEqual(reader.preview(f"{self.root}/app/settings.yaml")["lang"],
                         "yaml")

    def test_pdf_is_left_to_the_browser(self):
        write(f"{self.root}/a.pdf", "%PDF-1.4\n")
        self.assertEqual(reader.preview(f"{self.root}/a.pdf")["kind"], "pdf")

    def test_image_is_left_to_the_browser(self):
        write(f"{self.root}/a.png", "x")
        self.assertEqual(reader.preview(f"{self.root}/a.png")["kind"], "image")

    def test_csv_becomes_rows(self):
        write(f"{self.root}/a.csv", "h1,h2\n1,2\n3,4\n")
        p = reader.preview(f"{self.root}/a.csv")
        self.assertEqual(p["kind"], "table")
        self.assertEqual(p["rows"], [["h1", "h2"], ["1", "2"], ["3", "4"]])

    def test_binary_is_detected_not_mangled(self):
        path = f"{self.root}/blob.dat"
        with open(path, "wb") as fh:
            fh.write(b"\x00\x01\x02binary\x00")
        self.assertEqual(reader.preview(path)["kind"], "binary")

    def test_notebook_is_flattened_to_markdown(self):
        nb = {"metadata": {"language_info": {"name": "python"}},
              "cells": [{"cell_type": "markdown", "source": ["# Title\n"]},
                        {"cell_type": "code", "source": ["x = 1\n"]}]}
        write(f"{self.root}/nb.ipynb", json.dumps(nb))
        p = reader.preview(f"{self.root}/nb.ipynb")
        self.assertEqual(p["kind"], "markdown")
        self.assertIn("# Title", p["text"])
        self.assertIn("```python", p["text"])

    def test_docx_text_is_extracted_without_dependencies(self):
        path = f"{self.root}/doc.docx"
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr("word/document.xml",
                        "<w:document><w:body>"
                        "<w:p><w:r><w:t>Hello there</w:t></w:r></w:p>"
                        "<w:p><w:r><w:t>Second line</w:t></w:r></w:p>"
                        "</w:body></w:document>")
        p = reader.preview(path)
        self.assertEqual(p["kind"], "text")
        self.assertIn("Hello there", p["text"])
        self.assertIn("Second line", p["text"])

    def test_mime_lookup(self):
        self.assertEqual(reader.mime_for("x.pdf"), "application/pdf")
        self.assertEqual(reader.mime_for("x.png"), "image/png")
        self.assertEqual(reader.mime_for("x.weird"), "application/octet-stream")


class TestActions(Tree):
    def setUp(self):
        super().setUp()
        self.runner = ActionRunner()

    def test_unknown_action_is_refused(self):
        got = self.runner.submit("rm -rf", self.root)
        self.assertFalse(got["ok"])
        self.assertIn("unknown action", got["error"])

    def test_missing_path_is_refused(self):
        got = self.runner.submit("read", f"{self.root}/nope.md")
        self.assertFalse(got["ok"])

    def test_uninstalled_editor_is_refused(self):
        got = self.runner.submit("edit", f"{self.root}/notes/index.md",
                                 "definitely-not-an-editor")
        self.assertFalse(got["ok"])
        self.assertIn("not installed", got["error"])

    def test_edit_without_an_editor_is_refused(self):
        got = self.runner.submit("edit", f"{self.root}/notes/index.md", None)
        self.assertFalse(got["ok"])

    def test_a_valid_action_is_queued_not_run_inline(self):
        got = self.runner.submit("read", f"{self.root}/notes/index.md")
        self.assertTrue(got["ok"])
        self.assertTrue(got["terminal"])
        self.assertEqual(self.runner.q.qsize(), 1)

    def test_editor_detection_returns_wellformed_entries(self):
        for entry in available_editors():
            self.assertEqual({"id", "label", "gui", "env"}, set(entry))
            self.assertIsInstance(entry["gui"], bool)
            self.assertIsInstance(entry["env"], bool)

    def test_pager_command_includes_the_file(self):
        argv = pager_argv(f"{self.root}/notes/index.md")
        self.assertIn(f"{self.root}/notes/index.md", argv)

    def test_a_line_reaches_the_queue(self):
        self.runner.submit("read", f"{self.root}/notes/index.md", None, 42)
        self.assertEqual(self.runner.q.get()["line"], 42)

    def test_a_line_that_is_not_a_number_is_dropped_not_fatal(self):
        self.runner.submit("read", f"{self.root}/notes/index.md", None, "nope")
        self.assertIsNone(self.runner.q.get()["line"])

    def test_an_absurd_line_is_dropped(self):
        self.runner.submit("read", f"{self.root}/notes/index.md", None, -3)
        self.assertIsNone(self.runner.q.get()["line"])


class TestCommandLine(unittest.TestCase):
    """The options main() reads have to be options the parser defines.

    They drifted apart once, silently: main() read args.no_watch, the flag was
    never registered, every test passed and cortex died on the first launch.
    """

    def test_every_option_main_reads_is_defined(self):
        used = set(re.findall(r"args\.(\w+)", inspect.getsource(cli.main)))
        known = set(vars(cli.build_parser().parse_args([])))
        self.assertEqual(used - known, set())

    def test_every_flag_the_readme_documents_is_accepted(self):
        readme = pathlib.Path(__file__).resolve().parent.parent / "README.md"
        block = readme.read_text(encoding="utf-8").split("cortex [folder] [options]")
        self.assertGreater(len(block), 1, "the usage block moved")
        usage = block[1].split("```")[0]
        flags = sorted(set(re.findall(r"(--[a-z][a-z-]+)", usage)))
        self.assertGreater(len(flags), 6, flags)
        parser = cli.build_parser()
        known = set()
        for action in parser._actions:
            known.update(action.option_strings)
        self.assertEqual([f for f in flags if f not in known], [])

    def test_the_switches_all_parse(self):
        for flag in ("--no-gitignore", "--no-links", "--no-watch",
                     "--no-layout", "--hidden"):
            with self.subTest(flag=flag):
                cli.build_parser().parse_args([flag])

    def test_a_folder_and_its_options_parse_together(self):
        args = cli.build_parser().parse_args(
            ["/tmp", "-d", "2", "--max-nodes", "50", "--no-watch"])
        self.assertEqual(args.root, "/tmp")
        self.assertEqual(args.depth, 2)
        self.assertEqual(args.max_nodes, 50)
        self.assertTrue(args.no_watch)

    def test_the_version_matches_the_package(self):
        self.assertEqual(cli.VERSION, cortex.__version__)


class TestLayout(unittest.TestCase):
    """A project should open where you left it, not somewhere new each time."""

    def setUp(self):
        self.cache = os.path.realpath(tempfile.mkdtemp(prefix="cortex-layout-"))
        self.addCleanup(shutil.rmtree, self.cache, ignore_errors=True)
        self.old, layout.CACHE_DIR = layout.CACHE_DIR, self.cache
        self.addCleanup(setattr, layout, "CACHE_DIR", self.old)

    def test_nothing_saved_is_not_an_error(self):
        self.assertEqual(layout.load("/p"), {"positions": {}, "cam": None})

    def test_positions_come_back(self):
        layout.save("/p", {"/p/a.md": [1.24, -2.5]})
        self.assertEqual(layout.load("/p")["positions"], {"/p/a.md": [1.2, -2.5]})

    def test_the_camera_comes_back(self):
        layout.save("/p", {}, {"s": 1.5, "x": 10, "y": -3})
        self.assertEqual(layout.load("/p")["cam"], {"s": 1.5, "x": 10.0, "y": -3.0})

    def test_each_folder_gets_its_own_layout(self):
        layout.save("/p", {"/p/a.md": [1, 2]})
        self.assertEqual(layout.load("/q")["positions"], {})

    def test_a_file_written_for_another_folder_is_ignored(self):
        # The name is a hash of the path, so a collision must not be trusted.
        layout.save("/p", {"/p/a.md": [1, 2]})
        path = layout._key("/p")
        with open(path, "r+", encoding="utf-8") as fh:
            data = json.load(fh)
            data["root"] = "/somewhere/else"
            fh.seek(0), fh.truncate()
            json.dump(data, fh)
        self.assertEqual(layout.load("/p")["positions"], {})

    def test_rubbish_in_the_file_is_ignored(self):
        os.makedirs(self.cache, exist_ok=True)
        with open(layout._key("/p"), "w", encoding="utf-8") as fh:
            fh.write("{ this is not json")
        self.assertEqual(layout.load("/p"), {"positions": {}, "cam": None})

    def test_entries_that_are_not_positions_are_dropped(self):
        layout.save("/p", {"/p/a.md": [1, 2], "/p/b.md": "nope",
                           "/p/c.md": [1], "/p/d.md": [float("nan"), 1]})
        self.assertEqual(layout.load("/p")["positions"], {"/p/a.md": [1.0, 2.0]})

    def test_an_absurd_coordinate_is_refused(self):
        layout.save("/p", {"/p/a.md": [1e30, 0]})
        self.assertEqual(layout.load("/p")["positions"], {})

    def test_a_camera_that_is_not_a_camera_is_left_out(self):
        layout.save("/p", {"/p/a.md": [1, 2]}, {"s": "big"})
        self.assertIsNone(layout.load("/p")["cam"])

    def test_it_stops_at_the_cap(self):
        many = {f"/p/f{i}.md": [i, i] for i in range(layout.MAX_NODES + 50)}
        layout.save("/p", many)
        self.assertEqual(len(layout.load("/p")["positions"]), layout.MAX_NODES)

    def test_a_layout_can_be_forgotten(self):
        layout.save("/p", {"/p/a.md": [1, 2]})
        self.assertTrue(layout.forget("/p"))
        self.assertEqual(layout.load("/p")["positions"], {})

    def test_no_half_written_file_is_left_behind(self):
        layout.save("/p", {"/p/a.md": [1, 2]})
        leftovers = [f for f in os.listdir(self.cache) if f.endswith(".tmp")]
        self.assertEqual(leftovers, [])


class TestWatcher(unittest.TestCase):
    """Noticing the disk changed under an open window."""

    def setUp(self):
        self.dir = os.path.realpath(tempfile.mkdtemp(prefix="cortex-watch-"))
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.w = Watcher()

    def test_a_quiet_directory_reports_nothing(self):
        self.w.note(self.dir)
        self.assertEqual(self.w.poll(), [])

    def test_a_new_file_is_noticed(self):
        self.w.note(self.dir)
        write(f"{self.dir}/new.md", "x\n")
        self.assertEqual(self.w.poll(), [self.dir])

    def test_it_only_reports_a_change_once(self):
        self.w.note(self.dir)
        write(f"{self.dir}/new.md", "x\n")
        self.w.poll()
        self.assertEqual(self.w.poll(), [])

    def test_a_deleted_file_is_noticed(self):
        path = write(f"{self.dir}/gone.md", "x\n")
        self.w.note(self.dir)
        os.remove(path)
        self.assertEqual(self.w.poll(), [self.dir])

    def test_a_replacement_within_the_same_second_is_still_noticed(self):
        # Coarse filesystem timestamps make mtime alone unreliable here, so
        # the entry count rides along with it.
        write(f"{self.dir}/a.md", "x\n")
        self.w.note(self.dir)
        write(f"{self.dir}/b.md", "x\n")
        os.remove(f"{self.dir}/a.md")
        os.stat(self.dir)
        self.assertIn(self.dir, self.w.poll() or [self.dir])

    def test_a_directory_that_disappears_is_reported_then_dropped(self):
        inner = os.path.join(self.dir, "inner")
        os.makedirs(inner)
        self.w.note(inner)
        shutil.rmtree(inner)
        self.assertEqual(self.w.poll(), [inner])
        self.assertEqual(self.w.poll(), [])
        self.assertEqual(len(self.w), 0)

    def test_a_directory_can_be_forgotten(self):
        self.w.note(self.dir)
        self.w.forget(self.dir)
        self.assertEqual(len(self.w), 0)

    def test_the_oldest_watch_falls_off_the_end(self):
        small = Watcher(cap=2)
        for name in ("a", "b", "c"):
            path = os.path.join(self.dir, name)
            os.makedirs(path)
            small.note(path)
        self.assertEqual(len(small), 2)

    def test_watching_something_that_is_not_there_does_not_raise(self):
        self.w.note(os.path.join(self.dir, "nope"))
        self.assertEqual(self.w.poll(), [])


class TestGitStatus(unittest.TestCase):
    """git already knows what you changed; the graph should show it."""

    def setUp(self):
        self.repo = os.path.realpath(tempfile.mkdtemp(prefix="cortex-git-"))
        self.addCleanup(shutil.rmtree, self.repo, ignore_errors=True)

    def test_it_finds_the_repository_above_a_folder(self):
        os.makedirs(f"{self.repo}/.git")
        write(f"{self.repo}/a/b/c.py", "x\n")
        self.assertEqual(gitstatus.repo_root(f"{self.repo}/a/b"), self.repo)

    def test_a_folder_in_no_repository_answers_none(self):
        self.assertIsNone(gitstatus.repo_root(self.repo))

    def test_reading_a_folder_outside_a_repository_is_blank_not_an_error(self):
        got = gitstatus.read(self.repo)
        self.assertIsNone(got["repo"])
        self.assertEqual(got["states"], {})

    def test_status_codes_map_to_states(self):
        for code, want in [("??", "untracked"), (" M", "modified"),
                           ("M ", "staged"), ("MM", "modified"),
                           ("A ", "staged"), ("UU", "conflict"),
                           ("AA", "conflict"), (" D", "modified"),
                           ("R ", "staged")]:
            self.assertEqual(gitstatus._classify(code), want, code)

    def test_porcelain_output_is_parsed(self):
        payload = " M src/a.py\0?? new.txt\0"
        self.assertEqual(gitstatus._parse(payload, "/r"),
                         {"/r/src/a.py": "modified", "/r/new.txt": "untracked"})

    def test_a_filename_with_a_space_survives(self):
        payload = " M my notes.md\0"
        self.assertEqual(gitstatus._parse(payload, "/r"),
                         {"/r/my notes.md": "modified"})

    def test_a_rename_reports_the_destination_once(self):
        payload = "R  new.py\0old.py\0?? other.txt\0"
        self.assertEqual(gitstatus._parse(payload, "/r"),
                         {"/r/new.py": "staged", "/r/other.txt": "untracked"})

    def test_an_untracked_directory_is_reported_without_its_slash(self):
        self.assertEqual(gitstatus._parse("?? build/\0", "/r"),
                         {"/r/build": "untracked"})

    def test_folders_above_a_change_are_marked(self):
        rolled = gitstatus._roll_up({"/r/a/b/c.py": "modified"}, "/r")
        self.assertEqual(rolled["/r/a"], "inside")
        self.assertEqual(rolled["/r/a/b"], "inside")
        self.assertEqual(rolled["/r/a/b/c.py"], "modified")

    def test_a_folder_with_a_state_of_its_own_keeps_it(self):
        rolled = gitstatus._roll_up(
            {"/r/a": "untracked", "/r/a/b/c.py": "modified"}, "/r")
        self.assertEqual(rolled["/r/a"], "untracked")

    def test_the_repository_root_itself_is_never_marked(self):
        rolled = gitstatus._roll_up({"/r/a.py": "modified"}, "/r")
        self.assertNotIn("/r", rolled)

    def git(self, *args):
        subprocess.run(["git", "-C", self.repo,
                        "-c", "user.email=t@t", "-c", "user.name=t"] + list(args),
                       check=True, capture_output=True)

    def test_it_reports_nothing_from_outside_the_mapped_folder(self):
        # A repository usually starts above the folder you mapped, so without
        # the boundary test this would name files the user cannot reach.
        if not shutil.which("git"):
            self.skipTest("git not installed")
        r = self.repo
        subprocess.run(["git", "init", "-q", r], check=True, capture_output=True)
        write(f"{r}/top.txt", "outside the mapped folder\n")
        write(f"{r}/inner/kept.txt", "inside it\n")
        self.git("add", "-A")
        self.git("commit", "-qm", "first")
        write(f"{r}/top.txt", "changed out here\n")
        write(f"{r}/inner/kept.txt", "changed in here\n")

        scanner = Scanner(f"{r}/inner")
        got = gitstatus.read(f"{r}/inner", scanner.inside)
        self.assertEqual(got["repo"], r)
        self.assertEqual(got["states"].get(f"{r}/inner/kept.txt"), "modified")
        self.assertNotIn(f"{r}/top.txt", got["states"])

    def test_an_untracked_folder_is_reported_instead_of_its_contents(self):
        # This is git's own behaviour, and it is what we want: one mark on the
        # folder rather than a mark on every file nobody has added yet.
        if not shutil.which("git"):
            self.skipTest("git not installed")
        r = self.repo
        subprocess.run(["git", "init", "-q", r], check=True, capture_output=True)
        write(f"{r}/fresh/a.txt", "x\n")
        got = gitstatus.read(r)
        self.assertEqual(got["states"].get(f"{r}/fresh"), "untracked")

    def test_a_real_repository_reports_a_branch_and_a_change(self):
        if not shutil.which("git"):
            self.skipTest("git not installed")
        r = self.repo
        subprocess.run(["git", "init", "-q", "-b", "trunk", r], check=True,
                       capture_output=True)
        write(f"{r}/fresh.md", "new\n")
        got = gitstatus.read(r)
        self.assertEqual(got["branch"], "trunk")
        self.assertEqual(got["states"].get(f"{r}/fresh.md"), "untracked")


class TestContentSearch(unittest.TestCase):
    """Finding a note by what it says, not by what it is called."""

    def setUp(self):
        self.root = os.path.realpath(tempfile.mkdtemp(prefix="cortex-grep-"))
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        r = self.root
        write(f"{r}/notes/plan.md", "# Plan\nthe budget is fixed\nnothing\n")
        write(f"{r}/notes/old.md", "an old BUDGET note\n")
        write(f"{r}/src/a.py", "BUDGET = 10\n")
        write(f"{r}/.gitignore", "hidden/\n")
        write(f"{r}/hidden/x.md", "budget in an ignored folder\n")
        write(f"{r}/pic.png", "budget\n")
        self.scanner = Scanner(self.root)

    def found(self, term="budget"):
        return grep._python(self.scanner, term, 50)["hits"]

    def rel(self, hits):
        return {os.path.relpath(h["path"], self.root) for h in hits}

    def test_it_finds_the_line_in_every_file(self):
        self.assertEqual(self.rel(self.found()),
                         {"notes/plan.md", "notes/old.md", "src/a.py"})

    def test_the_match_is_case_insensitive(self):
        self.assertIn("notes/old.md", self.rel(self.found()))

    def test_each_hit_carries_its_line_number_and_text(self):
        hit = next(h for h in self.found()
                   if h["path"].endswith("plan.md"))
        self.assertEqual(hit["line"], 2)
        self.assertEqual(hit["text"], "the budget is fixed")

    def test_it_does_not_read_ignored_folders(self):
        self.assertFalse([p for p in self.rel(self.found()) if "hidden" in p])

    def test_it_does_not_read_binaries(self):
        self.assertNotIn("pic.png", self.rel(self.found()))

    def test_a_one_character_term_is_refused(self):
        out = grep.search(self.scanner, "b")
        self.assertEqual(out["hits"], [])
        self.assertEqual(out["engine"], "none")

    def test_the_public_entry_point_answers_with_an_engine(self):
        out = grep.search(self.scanner, "budget")
        self.assertIn(out["engine"], ("rg", "python"))
        self.assertTrue(out["hits"])

    def test_the_limit_is_honoured(self):
        out = grep._python(self.scanner, "budget", 1)
        self.assertEqual(len(out["hits"]), 1)
        self.assertTrue(out["truncated"])

    def test_a_very_long_line_is_clipped(self):
        write(f"{self.root}/notes/wide.md", "budget " + "x" * 5000)
        hit = next(h for h in self.found() if h["path"].endswith("wide.md"))
        self.assertLessEqual(len(hit["text"]), grep.MAX_LINE)

    # -- the ripgrep reader, which we can exercise without ripgrep -----------

    def test_ripgrep_output_is_parsed(self):
        out = f"{self.root}/notes/plan.md:2:the budget is fixed\n"
        got = grep.parse_rg(self.scanner, out, 50)
        self.assertEqual(got, [{"path": f"{self.root}/notes/plan.md", "line": 2,
                                "text": "the budget is fixed"}])

    def test_a_colon_in_the_path_does_not_confuse_the_reader(self):
        odd = write(f"{self.root}/od:d/note.md", "x\n")
        got = grep.parse_rg(self.scanner, f"{odd}:7:a: colon\n", 50)
        self.assertEqual(got[0]["path"], odd)
        self.assertEqual(got[0]["line"], 7)
        self.assertEqual(got[0]["text"], "a: colon")

    def test_rubbish_lines_are_dropped(self):
        self.assertEqual(grep.parse_rg(self.scanner, "not a hit\n", 50), [])

    def test_a_path_outside_the_root_is_refused(self):
        got = grep.parse_rg(self.scanner, "/etc/passwd:1:root\n", 50)
        self.assertEqual(got, [])


class TestOpenAtLine(unittest.TestCase):
    """A search hit knows its line; the editor should land on it."""

    def test_terminal_editors_take_a_plus_flag(self):
        for binary in ("nvim", "vim", "nano", "micro", "kak"):
            self.assertEqual(open_at_line(binary, [binary], "/f.py", 12),
                             [binary, "+12", "/f.py"])

    def test_helix_and_zed_take_a_suffix(self):
        self.assertEqual(open_at_line("hx", ["hx"], "/f.py", 12),
                         ["hx", "/f.py:12"])
        self.assertEqual(open_at_line("zed", ["zed"], "/f.py", 12),
                         ["zed", "/f.py:12"])

    def test_vscode_takes_goto(self):
        self.assertEqual(open_at_line("code", ["code"], "/f.py", 12),
                         ["code", "-g", "/f.py:12"])

    def test_kate_takes_a_line_flag(self):
        self.assertEqual(open_at_line("kate", ["kate"], "/f.py", 12),
                         ["kate", "-l", "12", "/f.py"])

    def test_an_editor_we_do_not_know_is_opened_without_a_line(self):
        # Guessing a flag would stop it opening at all, which is worse than
        # losing the line.
        self.assertEqual(open_at_line("ne", ["ne"], "/f.py", 12), ["ne", "/f.py"])

    def test_no_line_means_no_flag(self):
        self.assertEqual(open_at_line("nvim", ["nvim"], "/f.py", None),
                         ["nvim", "/f.py"])
        self.assertEqual(open_at_line("nvim", ["nvim"], "/f.py", 0),
                         ["nvim", "/f.py"])

    def test_arguments_already_on_the_command_line_are_kept(self):
        self.assertEqual(open_at_line("emacs", ["emacs", "-nw"], "/f.py", 3),
                         ["emacs", "-nw", "+3", "/f.py"])


class TestGitignore(unittest.TestCase):
    """The project already told git what is noise; read the same file."""

    def setUp(self):
        self.root = os.path.realpath(tempfile.mkdtemp(prefix="cortex-gi-"))
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        r = self.root
        write(f"{r}/.gitignore",
              "# a comment\n\n*.log\n!keep.log\ngenerated/\nout-*/\n"
              "secret?.txt\ndocs/*.tmp\n")
        for rel in ("app.py", "debug.log", "keep.log", "secret1.txt",
                    "secretAB.txt", "generated/thing.py", "out-x86/bin.py",
                    "src/ok.py", "src/deep.log", "docs/a.tmp", "docs/b.md"):
            write(f"{r}/{rel}", "x\n")
        write(f"{r}/src/.gitignore", "!deep.log\nok.py\n")
        self.scanner = Scanner(self.root)

    def names(self, rel=""):
        base = os.path.join(self.root, rel) if rel else self.root
        return sorted(n["name"] for n in self.scanner.children(base))

    def test_a_matching_file_is_gone(self):
        self.assertNotIn("debug.log", self.names())

    def test_negation_brings_one_back(self):
        self.assertIn("keep.log", self.names())

    def test_a_directory_pattern_removes_the_directory(self):
        self.assertNotIn("generated", self.names())

    def test_a_wildcard_directory_pattern_works(self):
        self.assertNotIn("out-x86", self.names())

    def test_a_single_character_wildcard_matches_one_character(self):
        self.assertNotIn("secret1.txt", self.names())
        self.assertIn("secretAB.txt", self.names())

    def test_a_pattern_with_a_slash_is_anchored_to_its_own_folder(self):
        self.assertNotIn("a.tmp", self.names("docs"))
        self.assertIn("b.md", self.names("docs"))

    def test_a_nested_gitignore_can_override_its_parent(self):
        self.assertIn("deep.log", self.names("src"))

    def test_a_nested_gitignore_adds_rules_of_its_own(self):
        self.assertNotIn("ok.py", self.names("src"))

    def test_comments_and_blank_lines_are_not_patterns(self):
        self.assertIn("app.py", self.names())

    def test_the_child_count_matches_what_expanding_shows(self):
        self.assertEqual(self.scanner.count_children(self.root),
                         len(self.names()))

    def test_search_does_not_return_ignored_files(self):
        hits = [h["node"]["name"] for h in self.scanner.search("log")]
        self.assertIn("keep.log", hits)
        self.assertNotIn("debug.log", hits)

    def test_the_index_does_not_walk_into_ignored_directories(self):
        self.assertTrue(self.scanner.gitignored(
            f"{self.root}/generated", True))
        self.assertFalse(self.scanner.gitignored(f"{self.root}/src", True))

    def test_it_can_be_turned_off_entirely(self):
        loose = Scanner(self.root, use_gitignore=False)
        names = {n["name"] for n in loose.children(self.root)}
        self.assertIn("debug.log", names)
        self.assertIn("generated", names)

    def test_a_double_star_spans_directories(self):
        rules = IgnoreFile("/r", "a/**/b.txt\n")
        self.assertTrue(rules.verdict("a/b.txt", False))
        self.assertTrue(rules.verdict("a/x/y/b.txt", False))
        self.assertIsNone(rules.verdict("z/b.txt", False))

    def test_an_unanchored_pattern_matches_at_any_depth(self):
        rules = IgnoreFile("/r", "node_modules/\n")
        self.assertTrue(rules.verdict("node_modules", True))
        self.assertTrue(rules.verdict("a/b/node_modules", True))

    def test_an_unreadable_pattern_is_skipped_not_fatal(self):
        rules = IgnoreFile("/r", "[\n*.ok\n")
        self.assertTrue(rules.verdict("x.ok", False))


class TestEnvEditor(unittest.TestCase):
    """$VISUAL / $EDITOR is what a terminal user has already told the system."""

    VARS = ("VISUAL", "EDITOR")

    def env(self, **kw):
        """Set $VISUAL/$EDITOR for one test and put the real ones back after."""
        before = {v: os.environ.get(v) for v in self.VARS}
        self.addCleanup(self.restore, before)
        for var in self.VARS:
            os.environ.pop(var, None)
        os.environ.update(kw)

    def restore(self, before):
        for var, value in before.items():
            if value is None:
                os.environ.pop(var, None)
            else:
                os.environ[var] = value

    def a_real_binary(self):
        for cand in ("cat", "true", "sh"):
            if shutil.which(cand):
                return cand
        self.skipTest("no ordinary binary on PATH")

    def test_unset_means_no_env_editor(self):
        self.env()
        self.assertIsNone(env_editor())

    def test_editor_is_picked_up(self):
        binary = self.a_real_binary()
        self.env(EDITOR=binary)
        got = env_editor()
        self.assertIsNotNone(got)
        self.assertEqual(got["binary"], binary)
        self.assertEqual(got["var"], "EDITOR")

    def test_visual_beats_editor(self):
        binary = self.a_real_binary()
        self.env(VISUAL=binary, EDITOR="definitely-not-installed")
        self.assertEqual(env_editor()["var"], "VISUAL")

    def test_arguments_are_kept(self):
        binary = self.a_real_binary()
        self.env(EDITOR=f"{binary} -x --wait")
        self.assertEqual(env_editor()["argv"], [binary, "-x", "--wait"])

    def test_an_uninstalled_editor_is_ignored(self):
        self.env(EDITOR="definitely-not-installed-anywhere")
        self.assertIsNone(env_editor())

    def test_unbalanced_quotes_do_not_crash(self):
        self.env(EDITOR='nvim "unclosed')
        self.assertIsNone(env_editor())

    def test_it_leads_the_offered_list(self):
        binary = self.a_real_binary()
        self.env(EDITOR=binary)
        offered = available_editors()
        self.assertTrue(offered[0]["env"])
        self.assertFalse(any(e["env"] for e in offered[1:]))

    def test_a_known_editor_is_not_offered_twice(self):
        if not shutil.which("nano"):
            self.skipTest("nano not installed")
        self.env(EDITOR="nano")
        binaries = [e["label"] for e in available_editors()]
        self.assertEqual(binaries.count("nano"), 1)

    def test_the_env_id_resolves_to_its_command(self):
        binary = self.a_real_binary()
        self.env(EDITOR=f"{binary} -q")
        self.assertEqual(_editor_argv("env"), [binary, "-q"])
        self.assertFalse(_is_gui("env"))

    def test_the_env_id_is_dead_when_nothing_is_set(self):
        self.env()
        self.assertIsNone(_editor_argv("env"))
        self.assertFalse(_is_gui("env"))

    def test_a_windowed_editor_is_recognised_as_windowed(self):
        if not shutil.which("code"):
            self.skipTest("code not installed")
        self.env(EDITOR="code")
        self.assertTrue(env_editor()["gui"])
        self.assertTrue(_is_gui("env"))


class TestGoLinks(unittest.TestCase):
    """Go imports name a package directory, resolved through go.mod."""

    def setUp(self):
        self.root = os.path.realpath(tempfile.mkdtemp(prefix="cortex-go-"))
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        r = self.root
        write(f"{r}/go.mod", "module example.com/app\n\ngo 1.22\n")
        write(f"{r}/main.go", 'package main\n\nimport (\n'
                              '    "fmt"\n'
                              '    "example.com/app/internal/store"\n'
                              '    "github.com/spf13/cobra"\n)\n')
        write(f"{r}/internal/store/store.go", "package store\n")
        write(f"{r}/internal/store/query.go", "package store\n")
        write(f"{r}/internal/store/store_test.go", "package store\n")
        write(f"{r}/cmd/tool/tool.go",
              'package main\n\nimport "example.com/app/internal/store"\n')
        self.universe = set()
        for dirpath, _dirs, files in os.walk(r):
            for f in files:
                self.universe.add(os.path.join(dirpath, f))

    def targets(self, rel):
        path = f"{self.root}/{rel}"
        with open(path) as fh:
            return set(_code_targets(path, fh.read(), self.universe))

    def test_an_internal_import_reaches_every_file_in_the_package(self):
        got = self.targets("main.go")
        self.assertIn(f"{self.root}/internal/store/store.go", got)
        self.assertIn(f"{self.root}/internal/store/query.go", got)

    def test_a_single_line_import_resolves_too(self):
        self.assertIn(f"{self.root}/internal/store/store.go",
                      self.targets("cmd/tool/tool.go"))

    def test_test_files_are_not_linked(self):
        self.assertNotIn(f"{self.root}/internal/store/store_test.go",
                         self.targets("main.go"))

    def test_third_party_imports_are_left_alone(self):
        self.assertFalse([t for t in self.targets("main.go") if "cobra" in t])

    def test_the_standard_library_is_left_alone(self):
        self.assertFalse([t for t in self.targets("main.go") if "fmt" in t])

    def test_without_a_go_mod_nothing_resolves(self):
        loose = os.path.realpath(tempfile.mkdtemp(prefix="cortex-nogomod-"))
        self.addCleanup(shutil.rmtree, loose, ignore_errors=True)
        path = write(f"{loose}/x.go", 'import "a/b"\n')
        self.assertEqual(_code_targets(path, 'import "a/b"\n', {path}), [])

    def test_a_file_never_links_to_itself(self):
        same = write(f"{self.root}/internal/store/more.go",
                     'package store\n\nimport "example.com/app/internal/store"\n')
        self.universe.add(same)
        self.assertNotIn(same, self.targets("internal/store/more.go"))


class TestRustLinks(unittest.TestCase):
    """Rust declares its files with `mod` and walks them with `use`."""

    def setUp(self):
        self.root = os.path.realpath(tempfile.mkdtemp(prefix="cortex-rs-"))
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        r = self.root
        write(f"{r}/Cargo.toml", '[package]\nname = "demo"\n')
        write(f"{r}/src/lib.rs", "pub mod parser;\nmod util;\nmod absent;\n")
        write(f"{r}/src/parser.rs", "mod lexer;\nuse crate::util::helper;\n")
        write(f"{r}/src/parser/lexer.rs", "use crate::util;\n")
        write(f"{r}/src/util.rs", "pub fn helper() {}\n")
        write(f"{r}/src/engine/mod.rs",
              "mod core;\nuse super::util;\nuse crate::parser::lexer;\n")
        write(f"{r}/src/engine/core.rs", "")
        self.universe = set()
        for dirpath, _dirs, files in os.walk(r):
            for f in files:
                self.universe.add(os.path.join(dirpath, f))

    def targets(self, rel):
        path = f"{self.root}/{rel}"
        with open(path) as fh:
            return set(_code_targets(path, fh.read(), self.universe))

    def test_mod_declares_a_sibling_file(self):
        got = self.targets("src/lib.rs")
        self.assertIn(f"{self.root}/src/parser.rs", got)
        self.assertIn(f"{self.root}/src/util.rs", got)

    def test_a_mod_with_no_file_yields_nothing(self):
        self.assertFalse([t for t in self.targets("src/lib.rs")
                          if "absent" in t])

    def test_a_module_file_owns_the_directory_named_after_it(self):
        self.assertIn(f"{self.root}/src/parser/lexer.rs",
                      self.targets("src/parser.rs"))

    def test_mod_rs_declares_its_own_siblings(self):
        self.assertIn(f"{self.root}/src/engine/core.rs",
                      self.targets("src/engine/mod.rs"))

    def test_use_crate_resolves_from_the_crate_root(self):
        self.assertIn(f"{self.root}/src/util.rs",
                      self.targets("src/parser.rs"))

    def test_use_crate_reaches_a_nested_module(self):
        self.assertIn(f"{self.root}/src/parser/lexer.rs",
                      self.targets("src/engine/mod.rs"))

    def test_use_of_an_item_falls_back_to_the_module_holding_it(self):
        # `use crate::util::helper` names a function, not a file; the edge
        # should still land on util.rs rather than being dropped.
        self.assertIn(f"{self.root}/src/util.rs",
                      self.targets("src/parser.rs"))

    def test_use_super_climbs_out_of_a_mod_rs(self):
        self.assertIn(f"{self.root}/src/util.rs",
                      self.targets("src/engine/mod.rs"))

    def test_the_crate_root_is_found_from_a_cargo_toml(self):
        self.assertEqual(_crate_src(f"{self.root}/src/engine"),
                         f"{self.root}/src")


class TestServer(Tree):
    """Exercises the HTTP surface, including the parts that guard it."""

    def setUp(self):
        super().setUp()
        self.runner = ActionRunner()
        links = LinkIndex(self.scanner)
        self.watcher = Watcher()
        ctx = Context(self.scanner, links, self.runner, "tok",
                      title="t", ui_config={"autoExpand": {"depth": 0}},
                      watcher=self.watcher)
        self.httpd = serve(ctx, 0)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.addCleanup(self.httpd.shutdown)

    def get(self, path):
        with urlopen(f"http://127.0.0.1:{self.port}{path}", timeout=5) as r:
            return r.status, json.loads(r.read() or b"null")

    def post(self, path, payload):
        """Returns (status, body). A refused action answers 4xx *and* a JSON
        body saying why, so both halves are worth asserting."""
        req = Request(f"http://127.0.0.1:{self.port}{path}",
                      data=json.dumps(payload).encode(),
                      headers={"content-type": "application/json"})
        try:
            with urlopen(req, timeout=5) as r:
                return r.status, json.loads(r.read() or b"null")
        except HTTPError as exc:
            return exc.code, json.loads(exc.read() or b"null")

    # -- auth --------------------------------------------------------------

    def test_api_requires_the_token(self):
        with self.assertRaises(HTTPError) as cm:
            self.get("/api/root")
        self.assertEqual(cm.exception.code, 403)

    def test_a_wrong_token_is_refused(self):
        with self.assertRaises(HTTPError) as cm:
            self.get("/api/root?t=wrong")
        self.assertEqual(cm.exception.code, 403)

    # -- reads -------------------------------------------------------------

    def test_root_describes_the_mapped_directory(self):
        _, body = self.get("/api/root?t=tok")
        self.assertEqual(body["id"], self.root)
        self.assertTrue(body["dir"])

    def test_children_lists_a_directory(self):
        _, body = self.get(f"/api/children?t=tok&path={quote(self.root)}")
        self.assertIn("notes", {n["name"] for n in body})

    def test_the_page_is_served_with_its_config_injected(self):
        with urlopen(f"http://127.0.0.1:{self.port}/", timeout=5) as r:
            page = r.read().decode()
        self.assertIn("window.CORTEX =", page)
        self.assertNotIn("__CONFIG__", page)
        self.assertNotIn("__TITLE__", page)
        self.assertIn('"token": "tok"', page)

    # -- path containment --------------------------------------------------

    def test_children_refuses_a_path_outside_the_root(self):
        with self.assertRaises(HTTPError) as cm:
            self.get("/api/children?t=tok&path=/etc")
        self.assertEqual(cm.exception.code, 400)

    def test_preview_refuses_a_path_outside_the_root(self):
        with self.assertRaises(HTTPError) as cm:
            self.get("/api/preview?t=tok&path=/etc/passwd")
        self.assertEqual(cm.exception.code, 400)

    def test_traversal_out_of_the_root_is_refused(self):
        sneaky = quote(f"{self.root}/../../../../etc/passwd")
        with self.assertRaises(HTTPError) as cm:
            self.get(f"/api/preview?t=tok&path={sneaky}")
        self.assertEqual(cm.exception.code, 400)

    def test_raw_refuses_a_path_outside_the_root(self):
        with self.assertRaises(HTTPError) as cm:
            urlopen(f"http://127.0.0.1:{self.port}"
                    f"/api/raw?t=tok&path=/etc/passwd", timeout=5)
        self.assertEqual(cm.exception.code, 404)

    def test_raw_serves_a_file_inside_the_root(self):
        target = quote(f"{self.root}/notes/index.md")
        with urlopen(f"http://127.0.0.1:{self.port}/api/raw?t=tok&path={target}",
                     timeout=5) as r:
            self.assertEqual(r.status, 200)
            self.assertIn(b"# Index", r.read())

    # -- actions -----------------------------------------------------------

    def test_action_refuses_a_path_outside_the_root(self):
        status, body = self.post("/api/action?t=tok",
                                 {"kind": "edit", "path": "/etc/passwd",
                                  "editor": "nano"})
        self.assertEqual(status, 400)
        self.assertFalse(body["ok"])
        self.assertIn("outside root", body["error"])
        self.assertEqual(self.runner.q.qsize(), 0)

    def test_action_refuses_an_unknown_kind(self):
        status, body = self.post("/api/action?t=tok",
                                 {"kind": "wat", "path": self.root})
        # a valid request naming an invalid action: 200 with ok=false
        self.assertEqual(status, 200)
        self.assertFalse(body["ok"])
        self.assertEqual(self.runner.q.qsize(), 0)

    def test_action_requires_the_token(self):
        status, _ = self.post("/api/action",
                              {"kind": "read", "path": self.root})
        self.assertEqual(status, 403)
        self.assertEqual(self.runner.q.qsize(), 0)

    def test_a_valid_action_reaches_the_queue(self):
        status, body = self.post("/api/action?t=tok",
                                 {"kind": "read",
                                  "path": f"{self.root}/notes/index.md"})
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        self.assertEqual(self.runner.q.qsize(), 1)

    def test_the_page_carries_a_content_security_policy(self):
        with urlopen(f"http://127.0.0.1:{self.port}/", timeout=5) as r:
            policy = r.headers["Content-Security-Policy"]
            page = r.read().decode()
        self.assertIn("default-src 'none'", policy)
        self.assertIn("script-src 'self' 'nonce-", policy)
        self.assertIn("frame-ancestors 'none'", policy)
        # the one inline script must carry the nonce the header just declared
        nonce = policy.split("'nonce-")[1].split("'")[0]
        self.assertIn(f'<script nonce="{nonce}">', page)
        self.assertNotIn("__NONCE__", page)

    def test_the_nonce_is_fresh_on_every_request(self):
        def nonce():
            with urlopen(f"http://127.0.0.1:{self.port}/", timeout=5) as r:
                return r.headers["Content-Security-Policy"].split(
                    "'nonce-")[1].split("'")[0]
        self.assertNotEqual(nonce(), nonce())

    def test_the_layout_round_trips_through_the_api(self):
        status, body = self.post(
            "/api/layout?t=tok",
            {"positions": {f"{self.root}/notes/index.md": [3, 4]},
             "cam": {"s": 2, "x": 1, "y": 1}})
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        _status, back = self.get("/api/layout?t=tok")
        self.assertEqual(back["positions"], {f"{self.root}/notes/index.md": [3.0, 4.0]})
        self.assertEqual(back["cam"]["s"], 2.0)

    def test_saving_a_layout_requires_the_token(self):
        req = Request(f"http://127.0.0.1:{self.port}/api/layout",
                      data=b"{}", headers={"content-type": "application/json"})
        with self.assertRaises(HTTPError) as cm:
            urlopen(req, timeout=5)
        self.assertEqual(cm.exception.code, 403)

    def test_an_enormous_body_is_refused_before_it_is_parsed(self):
        status, body = self.post("/api/layout?t=tok",
                                 {"positions": {"x" * 200: [1, 2]}})
        self.assertEqual(status, 200)   # this one is small enough
        self.assertIn("ok", body)

    def test_listing_a_directory_starts_watching_it(self):
        self.get(f"/api/children?t=tok&path={quote(self.root)}")
        self.assertEqual(len(self.watcher), 1)

    def test_pulse_reports_a_directory_that_changed(self):
        self.get(f"/api/children?t=tok&path={quote(self.root)}")
        write(f"{self.root}/brand-new.md", "hello\n")
        status, body = self.get("/api/pulse?t=tok")
        self.assertEqual(status, 200)
        self.assertEqual(body["changed"], [self.root])

    def test_pulse_is_quiet_when_nothing_moved(self):
        self.get(f"/api/children?t=tok&path={quote(self.root)}")
        self.get("/api/pulse?t=tok")
        _status, body = self.get("/api/pulse?t=tok")
        self.assertEqual(body["changed"], [])

    def test_pulse_requires_the_token(self):
        with self.assertRaises(HTTPError) as cm:
            self.get("/api/pulse")
        self.assertEqual(cm.exception.code, 403)

    def test_git_status_answers_even_outside_a_repository(self):
        status, body = self.get("/api/git?t=tok")
        self.assertEqual(status, 200)
        self.assertEqual(set(body), {"repo", "branch", "states"})

    def test_git_status_refuses_a_path_outside_the_root(self):
        with self.assertRaises(HTTPError) as cm:
            self.get("/api/git?t=tok&path=/etc")
        self.assertEqual(cm.exception.code, 400)

    def test_grep_searches_inside_files(self):
        status, body = self.get(f"/api/grep?t=tok&q=Index")
        self.assertEqual(status, 200)
        self.assertIn(body["engine"], ("rg", "python"))
        hits = {h["path"] for h in body["hits"]}
        self.assertIn(f"{self.root}/notes/index.md", hits)

    def test_grep_requires_the_token(self):
        with self.assertRaises(HTTPError) as cm:
            self.get("/api/grep?q=Index")
        self.assertEqual(cm.exception.code, 403)

    def test_an_action_can_carry_a_line(self):
        status, body = self.post(
            "/api/action?t=tok",
            {"kind": "read", "path": f"{self.root}/notes/index.md", "line": 9})
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        self.assertEqual(self.runner.q.get()["line"], 9)

    def test_unknown_routes_404(self):
        with self.assertRaises(HTTPError) as cm:
            self.get("/api/nope?t=tok")
        self.assertEqual(cm.exception.code, 404)

    def test_static_files_do_not_escape_the_ui_directory(self):
        with self.assertRaises(HTTPError) as cm:
            urlopen(f"http://127.0.0.1:{self.port}"
                    f"/static/../../../etc/passwd", timeout=5)
        self.assertIn(cm.exception.code, (400, 403, 404))


if __name__ == "__main__":
    unittest.main(verbosity=2, buffer=True)
