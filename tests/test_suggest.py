#!/usr/bin/env python3
"""Tests for ghost notes and suggested links. Standard library only.

Run: python3 tests/test_suggest.py

Every test builds its own throwaway folder of notes under /tmp and indexes it
in-line; nothing here touches the real home directory.
"""

import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cortex import links
from cortex.links import (LinkIndex, STOPWORDS, ghost_key, note_terms,
                          suggest_links)
from cortex.scanner import Scanner


def write(path, text=""):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return path


class Vault(unittest.TestCase):
    """An empty throwaway folder; each test writes the notes it needs."""

    def setUp(self):
        self.root = os.path.realpath(tempfile.mkdtemp(prefix="cortex-test-"))
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)

    def note(self, rel, text=""):
        return write(os.path.join(self.root, rel), text)

    def build(self):
        index = LinkIndex(Scanner(self.root))
        index._build()
        return index.snapshot()

    def edges(self, kind, snap=None):
        snap = snap or self.build()
        return {(a, b) for a, b, k in snap["edges"] if k == kind}


# --- ghosts -----------------------------------------------------------------


class TestGhosts(Vault):
    """A link to a note nobody has written is drawn, not dropped."""

    def test_an_unresolved_wikilink_becomes_a_ghost(self):
        index = self.note("index.md", "The agenda is in [[Meeting Notes]].\n")
        snap = self.build()
        self.assertIn([index, "ghost:meeting notes", "ghost"], snap["edges"])
        self.assertEqual(snap["ghosts"], {"ghost:meeting notes": "Meeting Notes"})

    def test_a_ghost_keeps_the_folder_it_was_written_with(self):
        self.note("index.md", "next: [[projects/Roadmap]]\n")
        self.assertEqual(self.build()["ghosts"],
                         {"ghost:projects/roadmap": "projects/Roadmap"})

    def test_two_notes_share_one_ghost_whatever_the_case_or_spacing(self):
        a = self.note("a.md", "see [[Meeting Notes]]\n")
        b = self.note("b.md", "see [[ meeting   NOTES ]]\n")
        c = self.note("c.md", "see [[Meeting Notes.md]]\n")
        snap = self.build()
        self.assertEqual(list(snap["ghosts"]), ["ghost:meeting notes"])
        self.assertEqual(self.edges("ghost", snap),
                         {(a, "ghost:meeting notes"), (b, "ghost:meeting notes"),
                          (c, "ghost:meeting notes")})

    def test_the_name_shown_is_the_first_spelling_met(self):
        self.note("a.md", "[[Plan]], then later [[PLAN]]\n")
        self.assertEqual(self.build()["ghosts"], {"ghost:plan": "Plan"})

    def test_a_ghost_edge_appears_once_however_often_it_is_linked(self):
        self.note("a.md", "[[Missing]] and [[missing]] and [[Missing|again]]\n")
        self.assertEqual(len(self.edges("ghost")), 1)

    def test_a_resolved_link_is_not_a_ghost(self):
        a = self.note("a.md", "see [[b]]\n")
        b = self.note("b.md", "# B\n")
        snap = self.build()
        self.assertIn((a, b), self.edges("note", snap))
        self.assertEqual(snap["ghosts"], {})
        self.assertEqual(self.edges("ghost", snap), set())

    def test_a_path_link_picks_the_note_in_that_folder(self):
        one = self.note("one/deep.md", "# one\n")
        sub = self.note("sub/deep.md", "# sub\n")
        a = self.note("a.md", "[[sub/deep]]\n")
        b = self.note("b.md", "[[one/deep]]\n")
        edges = self.edges("note")
        # Matching the stem alone sends both to whichever `deep` came first.
        self.assertIn((a, sub), edges)
        self.assertIn((b, one), edges)
        self.assertNotIn((a, one), edges)
        self.assertNotIn((b, sub), edges)

    def test_a_path_link_may_carry_its_extension(self):
        self.note("one/deep.md", "# one\n")
        sub = self.note("sub/deep.md", "# sub\n")
        a = self.note("a.md", "[[sub/deep.md]]\n")
        self.assertIn((a, sub), self.edges("note"))

    def test_a_path_to_a_folder_that_is_not_there_falls_back_to_the_stem(self):
        other = self.note("notes/other.md", "# other\n")
        a = self.note("a.md", "[[elsewhere/other]]\n")
        snap = self.build()
        self.assertIn((a, other), self.edges("note", snap))
        self.assertEqual(snap["ghosts"], {})

    def test_a_wikilink_in_a_code_block_is_not_a_ghost(self):
        self.note("a.md", "Syntax:\n\n```md\n[[missing]]\n```\n")
        snap = self.build()
        self.assertEqual(snap["ghosts"], {})
        self.assertEqual(snap["edges"], [])

    def test_a_wikilink_in_inline_code_is_not_a_ghost(self):
        self.note("a.md", "write `[[missing]]` to link a note\n")
        snap = self.build()
        self.assertEqual(snap["ghosts"], {})
        self.assertEqual(snap["edges"], [])

    def test_a_wikilink_in_code_is_not_a_link_either(self):
        a = self.note("a.md", "```md\n[[other]]\n```\nand `[[other]]`\n")
        other = self.note("other.md", "# other\n")
        self.assertNotIn((a, other), self.edges("note"))

    def test_a_missing_picture_is_not_a_ghost(self):
        self.note("a.md", "![[diagram.png]] and [[report.pdf]]\n")
        self.assertEqual(self.build()["ghosts"], {})

    def test_a_nested_list_in_prose_is_not_a_ghost(self):
        self.note("a.md", "the matrix [[1, 2], [3, 4]] is square\n")
        self.assertEqual(self.build()["ghosts"], {})

    def test_ghosts_stop_at_the_cap(self):
        self.note("a.md", " ".join(f"[[missing {i}]]" for i in range(8)))
        with mock.patch.object(links, "MAX_GHOSTS", 3):
            snap = self.build()
        self.assertEqual(len(snap["ghosts"]), 3)
        self.assertEqual(len(self.edges("ghost", snap)), 3)

    def test_the_key_ignores_case_spacing_and_the_extension(self):
        self.assertEqual(ghost_key("  Meeting   Notes.MD "), "meeting notes")
        self.assertEqual(ghost_key("projects/Roadmap"), "projects/roadmap")


# --- suggested links --------------------------------------------------------


FINANCE_Q3 = """# Q3 review

The quarter closed under budget. Our forecast for the next quarter assumes
the budget holds, and the revenue forecast was revised twice. Spending on
contractors ran over the forecast.
"""

FINANCE_PLAN = """# Planning

Draft the budget before the quarter starts. Compare the budget with last
quarter's forecast and adjust the forecast for hiring. Contractors are the
biggest line in the budget.
"""

UNRELATED = {
    "garden/tomatoes.md": "Tomatoes need full sun and deep watering. Pinch the "
                          "side shoots, stake the plants early, and feed them "
                          "compost once the first fruit sets.\n",
    "travel/kyoto.md": "Took the early train from Osaka. Temples in the "
                       "morning, the bamboo grove after lunch, and a long walk "
                       "along the river at dusk.\n",
    "kitchen/bread.md": "Feed the starter the night before. Mix flour, water "
                        "and salt, fold the dough every half hour, then shape "
                        "and bake in a hot oven.\n",
    "music/guitar.md": "Practise the barre chords slowly. Change strings every "
                       "month, tune by ear, and record a take of the song each "
                       "week to hear progress.\n",
}


class TestSuggestions(Vault):
    """Notes about one thing are offered as a link nobody has made yet."""

    def setUp(self):
        super().setUp()
        self.q3 = self.note("finance/q3-review.md", FINANCE_Q3)
        self.plan = self.note("finance/planning.md", FINANCE_PLAN)
        for rel, text in UNRELATED.items():
            self.note(rel, text)

    def pairs(self, snap=None):
        snap = snap or self.build()
        return {(a, b): terms for a, b, _, terms in snap["suggest"]}

    def test_two_notes_on_one_topic_are_suggested(self):
        pairs = self.pairs()
        key = tuple(sorted((self.q3, self.plan)))
        self.assertIn(key, pairs)
        self.assertLessEqual({"budget", "forecast", "quarter"}, set(pairs[key]))

    def test_unrelated_notes_are_not_suggested(self):
        finance = {self.q3, self.plan}
        for a, b in self.pairs():
            self.assertEqual({a, b}, finance)

    def test_an_existing_wikilink_suppresses_the_suggestion(self):
        write(self.q3, FINANCE_Q3 + "\nSee [[planning]].\n")
        self.assertEqual(self.pairs(), {})

    def test_a_link_the_other_way_suppresses_it_too(self):
        write(self.plan, FINANCE_PLAN + "\nLast time: [review](./q3-review.md)\n")
        self.assertEqual(self.pairs(), {})

    def test_a_new_link_drops_the_suggestion_on_the_next_build(self):
        index = LinkIndex(Scanner(self.root))
        index._build()
        self.assertTrue(index.snapshot()["suggest"])
        write(self.q3, FINANCE_Q3 + "\nSee [[planning]].\n")
        index._build()
        self.assertEqual(index.snapshot()["suggest"], [])

    def test_stopwords_are_never_the_reason(self):
        filler = ("We would rather they should do it about now, which is what "
                  "their team said. TODO: done. http https www com. ")
        self.note("ops/deploy.md", filler + "Roll the canary deploy to the "
                  "cluster, then widen the rollout.\n")
        self.note("ops/rollback.md", filler + "If the canary fails, the "
                  "rollout stops and the cluster goes back a deploy.\n")
        snap = self.build()
        self.assertTrue(any("ops/" in a for a, _, _, _ in snap["suggest"]))
        for _, _, _, terms in snap["suggest"]:
            self.assertEqual(set(terms) & STOPWORDS, set())

    def test_fewer_than_three_notes_suggest_nothing(self):
        for rel in UNRELATED:
            os.remove(os.path.join(self.root, rel))
        self.assertEqual(self.build()["suggest"], [])

    def test_each_pair_appears_once_in_path_order(self):
        self.note("finance/forecast.md", FINANCE_PLAN.replace("Planning", "Fc"))
        suggest = self.build()["suggest"]
        self.assertTrue(suggest)
        for a, b, _, _ in suggest:
            self.assertLess(a, b)
        keys = [(a, b) for a, b, _, _ in suggest]
        self.assertEqual(len(keys), len(set(keys)))

    def test_a_score_is_rounded_and_the_reasons_are_few(self):
        for _, _, score, terms in self.build()["suggest"]:
            self.assertEqual(score, round(score, 2))
            self.assertGreaterEqual(score, links.SUGGEST_MIN)
            self.assertLessEqual(len(terms), 4)

    def test_code_files_never_take_part(self):
        self.note("finance/budget.py",
                  '"""Budget forecast for the quarter, and contractors."""\n'
                  "BUDGET = 1  # the quarter forecast budget\n")
        self.note("finance/forecast.js",
                  "// quarter budget forecast for contractors\n")
        for a, b, _, _ in self.build()["suggest"]:
            self.assertTrue(a.endswith(".md") and b.endswith(".md"), (a, b))

    def test_a_copy_of_a_note_is_not_suggested_as_its_partner(self):
        copy = self.note("archive/q3-review.md", FINANCE_Q3)
        self.assertNotIn(tuple(sorted((self.q3, copy))), self.pairs())

    def test_a_build_stopped_while_pairing_publishes_nothing(self):
        index = LinkIndex(Scanner(self.root))
        real = links.suggest_links

        def stop_midway(*args):
            index.stop()
            return real(*args)

        with mock.patch.object(links, "suggest_links", stop_midway):
            index._build()
        snap = index.snapshot()
        self.assertFalse(snap["ready"])
        self.assertEqual(snap["suggest"], [])


class TestPairing(unittest.TestCase):
    """The scoring on its own, without a folder to walk."""

    DOCS = [("/a.md", {"budget": 3, "forecast": 2, "garden": 1}),
            ("/b.md", {"budget": 2, "forecast": 2, "kyoto": 1}),
            ("/c.md", {"garden": 1, "kyoto": 1, "flour": 4})]

    def test_a_pairing_cut_short_returns_nothing_to_publish(self):
        self.assertIsNone(suggest_links(self.DOCS, set(), lambda: True))

    def test_a_pair_already_joined_is_left_out(self):
        self.assertTrue(suggest_links(self.DOCS))
        self.assertEqual(suggest_links(self.DOCS, {("/a.md", "/b.md")}), [])

    def test_one_shared_word_is_not_enough(self):
        docs = [("/a.md", {"budget": 3, "zebra": 1}),
                ("/b.md", {"budget": 3, "okapi": 1}),
                ("/c.md", {"zebra": 1, "okapi": 1})]
        self.assertEqual(suggest_links(docs), [])


class TestNoteTerms(unittest.TestCase):
    """What a note is about, as far as pairing is concerned."""

    def test_front_matter_words_count_but_its_keys_do_not(self):
        terms = note_terms("---\ntitle: Budget review\ntags: [forecast]\n---\n")
        self.assertLessEqual({"budget", "review", "forecast"}, set(terms))
        self.assertNotIn("title", terms)
        self.assertNotIn("tags", terms)

    def test_code_urls_and_link_targets_are_not_words(self):
        terms = note_terms("```\nkubectl apply\n```\nrun `terraform` and see "
                           "https://example.org/zebra or [label](./giraffe.md)\n")
        for word in ("kubectl", "terraform", "zebra", "giraffe", "example"):
            self.assertNotIn(word, terms)
        self.assertIn("label", terms)

    def test_the_words_inside_a_wikilink_count(self):
        self.assertLessEqual({"quarterly", "budget"},
                             set(note_terms("see [[Quarterly Budget]]\n")))

    def test_plurals_fold_into_the_singular(self):
        self.assertEqual(note_terms("forecasts forecast"), {"forecast": 2})

    def test_words_that_only_look_plural_are_left_alone(self):
        self.assertEqual(set(note_terms("class focus analysis bus")),
                         {"class", "focus", "analysis", "bus"})

    def test_short_words_and_bare_numbers_are_dropped(self):
        self.assertEqual(note_terms("ox 42 2024 a1b"), {"a1b": 1})

    def test_stopwords_and_web_noise_are_dropped(self):
        self.assertEqual(note_terms("would about their TODO done http www"), {})

    def test_letters_outside_ascii_still_make_words(self):
        self.assertLessEqual({"café", "naïve"}, set(note_terms("Café naïve")))


if __name__ == "__main__":
    unittest.main(verbosity=1)
