#!/usr/bin/env python3
"""Tests for the python side of cortex. Standard library only.

Run: python3 tests/test_cortex.py   (or tests/run)

Every test builds its own throwaway tree under /tmp; nothing here touches the
real home directory.
"""

import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
import zipfile
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cortex import reader
from cortex.actions import (ActionRunner, available_editors, env_editor,
                            _editor_argv, _is_gui, pager_argv)
from cortex.links import LinkIndex, _code_targets, _crate_src, _resolve_rel
from cortex.ignore import IgnoreFile
from cortex.scanner import Scanner, group_of
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
        ctx = Context(self.scanner, links, self.runner, "tok",
                      title="t", ui_config={"autoExpand": {"depth": 0}})
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
