"""Tests for the prompt-facing text helpers.

``strip_title_overlap`` exists because migration derives a note's title from its opening words,
so rendering ``title: content`` duplicated the opening ("PULSE forgets exponentially: PULSE
forgets exponentially: retrievability…") — visible in the exact bytes sent to the model.

The rule it must obey: de-duplicate for the prompt, never for the vault (the note keeps every
word the user wrote), and never render a note as empty.
"""

from __future__ import annotations

import unittest

from agent.brain.parser import strip_title_overlap, wikilinks_to_text


class TestWikilinksToText(unittest.TestCase):
    """The *display* form: a link's name survives so the sentence reads as English.

    Distinct from matching, which strips links entirely so a note that merely references a
    topic does not rank as though it were about it.
    """

    def test_unaliased_link_becomes_its_page_name(self):
        self.assertEqual(wikilinks_to_text("See [[user/preferences]] now."), "See preferences now.")

    def test_aliased_link_becomes_its_alias(self):
        self.assertEqual(wikilinks_to_text("See [[concept/x|the forgetting curve]]."), "See the forgetting curve.")

    def test_hyphens_become_spaces(self):
        self.assertEqual(wikilinks_to_text("[[concept/forgetting-curve]]"), "forgetting curve")

    def test_plain_text_is_untouched(self):
        self.assertEqual(wikilinks_to_text("no links here"), "no links here")

    def test_multiple_links(self):
        self.assertEqual(wikilinks_to_text("[[a/one]] and [[b/two|Two]]"), "one and Two")

    def test_empty_input(self):
        self.assertEqual(wikilinks_to_text(""), "")


class TestStripTitleOverlap(unittest.TestCase):
    def test_removes_the_repeated_opening(self):
        self.assertEqual(
            strip_title_overlap("PULSE forgets exponentially: retrievability decays", "PULSE forgets exponentially"),
            "retrievability decays",
        )

    def test_separator_punctuation_is_dropped(self):
        got = strip_title_overlap("Forgetting Curve - retrievability decays over time", "Forgetting Curve")
        self.assertEqual(got, "retrievability decays over time")

    def test_leaves_text_alone_when_the_title_is_absent(self):
        text = "an unrelated body of prose"
        self.assertEqual(strip_title_overlap(text, "Something Else"), text)

    def test_leaves_text_alone_when_the_remainder_is_too_short(self):
        """A note that is only its opening clause must not render as empty."""
        text = "PULSE is one entity"
        self.assertEqual(strip_title_overlap(text, "PULSE is one entity"), text)

    def test_byte_identical_text_is_returned_when_short(self):
        text = "short fact"
        self.assertEqual(strip_title_overlap(text, "short fact"), text)

    def test_partial_title_match_is_not_stripped(self):
        text = "PULSE forgets nothing at all"
        self.assertEqual(strip_title_overlap(text, "PULSE forgets exponentially"), text)

    def test_case_and_whitespace_differences_still_match(self):
        got = strip_title_overlap("forgetting   curve:  decays over time", "Forgetting Curve")
        self.assertEqual(got, "decays over time")

    def test_word_order_matters(self):
        text = "curve forgetting: decays over time"
        self.assertEqual(strip_title_overlap(text, "forgetting curve"), text)

    def test_empty_inputs_are_safe(self):
        self.assertEqual(strip_title_overlap("", "anything"), "")
        self.assertEqual(strip_title_overlap("body text here", ""), "body text here")

    def test_does_not_mangle_punctuation_inside_the_body(self):
        got = strip_title_overlap("Identity: PULSE is one entity; memory is an organ.", "Identity")
        self.assertEqual(got, "PULSE is one entity; memory is an organ.")


if __name__ == "__main__":
    unittest.main()
