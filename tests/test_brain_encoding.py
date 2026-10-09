"""Tests for the encoding salience gate (specs/brain.md §3.1, S5).

The two failure modes this must avoid, both of which the user has been burned by:

- **Noise** — remembering every turn. "faltu knowledge" is exactly what that looks like, so the
  gate's *rejections* are tested as carefully as its acceptances: greetings, acknowledgements,
  questions, commands and bare fragments must produce nothing at all.
- **Silence** — a gate so strict that real information is dropped. A user correction must always
  encode (§3.5 says it always supersedes), and so must a fact they explicitly asked PULSE to hold.
"""

from __future__ import annotations

import shutil
import tempfile
import time
import unittest
from pathlib import Path

from agent.brain.encoding import (
    CORRECTION_FLOOR,
    DEFAULT_THRESHOLD,
    MemoryCandidate,
    SalienceSignals,
    category_for,
    daily_node_id,
    detect_signals,
    encode_turn,
    extract_candidates,
    is_trivial,
    score_salience,
    title_for,
)
from agent.brain.models import NodeStatus
from agent.brain.vault import BrainVault


class TestSalienceFunction(unittest.TestCase):
    def test_weights_are_not_presence_based(self):
        """A weak signal on a novel topic must not cross the gate — that is the noise failure."""
        weak = SalienceSignals(explicit=0.0, correction=0.0, outcome=0.0, novelty=1.0, recurrence=1.0)
        self.assertLess(score_salience(weak), DEFAULT_THRESHOLD)

    def test_explicit_statement_alone_encodes(self):
        signals = SalienceSignals(explicit=1.0, novelty=1.0)
        self.assertGreaterEqual(score_salience(signals), DEFAULT_THRESHOLD)

    def test_outcome_alone_encodes(self):
        signals = SalienceSignals(outcome=1.0, novelty=1.0)
        self.assertGreaterEqual(score_salience(signals), DEFAULT_THRESHOLD)

    def test_correction_is_floored(self):
        """§3.5: a correction always supersedes, even worded as a bare "wrong"."""
        bare = SalienceSignals(correction=1.0)
        self.assertGreaterEqual(score_salience(bare), CORRECTION_FLOOR)

    def test_score_is_clamped_to_one(self):
        saturated = SalienceSignals(explicit=1.0, correction=1.0, outcome=1.0, novelty=1.0, recurrence=1.0)
        self.assertEqual(score_salience(saturated), 1.0)

    def test_negative_and_oversized_inputs_are_tolerated(self):
        weird = SalienceSignals(explicit=-5.0, novelty=99.0)
        self.assertGreaterEqual(score_salience(weird), 0.0)
        self.assertLessEqual(score_salience(weird), 1.0)


class TestTriviaRejection(unittest.TestCase):
    def test_greetings_and_acks_are_trivial(self):
        for text in ["hi", "hey!", "hello", "thanks", "thank you!", "ok", "cool", "got it", "yo", "hmm"]:
            self.assertTrue(is_trivial(text), text)

    def test_slash_commands_are_trivial(self):
        self.assertTrue(is_trivial("/help"))
        self.assertTrue(is_trivial("/memory pending"))

    def test_empty_is_trivial(self):
        self.assertTrue(is_trivial(""))
        self.assertTrue(is_trivial("   \n "))

    def test_a_real_statement_is_not_trivial(self):
        self.assertFalse(is_trivial("The recall budget should be 800 tokens, not 600."))

    def test_a_bare_remember_instruction_is_not_trivial_despite_being_short(self):
        """Length is a proxy for substance, not a rule — "remember this" is a direct instruction."""
        self.assertFalse(is_trivial("remember this"))


class TestSignalDetection(unittest.TestCase):
    def test_novelty_is_maximal_on_an_empty_vault(self):
        self.assertEqual(detect_signals("PULSE stores memories as markdown").novelty, 1.0)

    def test_novelty_falls_when_the_vault_already_says_it(self):
        known = ["PULSE stores every memory as a plain markdown file"]
        signals = detect_signals("PULSE stores every memory as a plain markdown file", corpus=known)
        self.assertLess(signals.novelty, 0.2)

    def test_recurrence_rises_with_repeated_themes(self):
        corpus = [
            "the recall budget is a bounded block",
            "the recall budget caps how much memory is injected",
            "the recall budget keeps the prompt small",
        ]
        self.assertGreater(detect_signals("the recall budget limit", corpus=corpus).recurrence, 0.5)

    def test_ordinary_prose_fires_no_marker(self):
        """The detector's most important property: it does not match everything."""
        signals = detect_signals("let me look at the file and see what happens")
        self.assertEqual(signals.explicit, 0.0)
        self.assertEqual(signals.correction, 0.0)
        self.assertEqual(signals.outcome, 0.0)

    def test_explicit_markers_fire(self):
        for text in ["remember that I prefer Hinglish", "from now on use tabs", "my name is Ankit"]:
            self.assertEqual(detect_signals(text).explicit, 1.0, text)

    def test_correction_markers_fire(self):
        for text in ["That's wrong, it should be 800.", "stop adding a memory block every turn"]:
            self.assertEqual(detect_signals(text).correction, 1.0, text)

    def test_a_single_weak_marker_is_not_a_correction(self):
        """ "actually" and "instead" are ordinary words; one alone is not evidence."""
        self.assertEqual(detect_signals("actually let me check the logs").correction, 0.0)

    def test_two_weak_markers_are_a_correction(self):
        self.assertEqual(detect_signals("it should be 800 instead of 600").correction, 0.7)

    def test_outcome_markers_fire(self):
        for text in ["the deploy broke", "we shipped it", "the tests pass now"]:
            self.assertEqual(detect_signals(text).outcome, 1.0, text)


class TestCategoryAndTitle(unittest.TestCase):
    def test_user_statement_goes_to_user(self):
        self.assertEqual(category_for("I prefer Hinglish replies"), "user")

    def test_self_statement_goes_to_self(self):
        self.assertEqual(category_for("PULSE never deletes a memory"), "self")

    def test_neutral_fact_goes_to_concept(self):
        self.assertEqual(category_for("Retrievability decays exponentially"), "concept")

    def test_user_wins_over_self_when_both_appear(self):
        """ "your name for me is X" is a user preference — it is the user being described."""
        self.assertEqual(category_for("you should call me Ankit"), "user")

    def test_title_is_short_and_clause_bounded(self):
        title = title_for("PULSE forgets exponentially: retrievability decays as exp(-delta/stability)")
        self.assertEqual(title, "PULSE forgets exponentially")

    def test_title_falls_back_when_text_is_punctuation(self):
        self.assertTrue(title_for("...").strip())


class TestStatementExtraction(unittest.TestCase):
    def test_questions_produce_no_candidates(self):
        self.assertEqual(extract_candidates("What is the recall budget for a turn?"), [])

    def test_marker_prefix_is_stripped_from_the_fact(self):
        """The note must read as the fact, not as the request that produced it."""
        candidates = extract_candidates("Remember that I prefer Hinglish replies to English")
        self.assertEqual([c.text for c in candidates], ["I prefer Hinglish replies to English"])

    def test_multiple_statements_are_scored_independently(self):
        text = "Remember that I prefer tabs. What time is it?"
        candidates = extract_candidates(text)
        self.assertEqual([c.text for c in candidates], ["I prefer tabs"])

    def test_explicit_fact_is_semantic(self):
        (candidate,) = extract_candidates("Remember that my name is Ankit")
        self.assertEqual(candidate.kind, "semantic")
        self.assertEqual(candidate.category, "user")

    def test_bare_outcome_is_episodic_until_consolidation_promotes_it(self):
        (candidate,) = extract_candidates("The deploy broke and we rolled back the release")
        self.assertEqual(candidate.kind, "episodic")

    def test_correction_is_semantic_and_carries_what_it_supersedes(self):
        existing = [("concept/recall-budget", "the recall budget is 600 tokens")]
        (candidate,) = extract_candidates(
            "That's wrong, the recall budget is 800 tokens", existing=existing
        )
        self.assertEqual(candidate.kind, "semantic")
        self.assertEqual(candidate.supersedes, ["concept/recall-budget"])

    def test_a_greeting_yields_nothing(self):
        self.assertEqual(extract_candidates("thanks!"), [])

    def test_threshold_is_respected(self):
        text = "The deploy broke and we rolled back the release"
        self.assertEqual(extract_candidates(text, threshold=0.99), [])
        self.assertEqual(len(extract_candidates(text, threshold=0.0)), 1)

    def test_candidate_round_trips_to_dict(self):
        (candidate,) = extract_candidates("Remember that my name is Ankit")
        payload = candidate.as_dict()
        self.assertEqual(
            set(payload),
            {"text", "kind", "category", "title", "salience", "signals", "supersedes", "temporal_revisions"},
        )


class EncodingTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="pulse-brain-encoding-"))
        self.vault = BrainVault(vault_dir=self.tmp / "brain")
        self.vault.ensure_vault_structure()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)


class TestEncodeTurn(EncodingTestCase):
    def test_trivial_turn_writes_nothing(self):
        report = encode_turn(self.vault, "hi", turn_id="t1")
        self.assertEqual(report["candidates"], [])
        self.assertEqual(self.vault.list_all_nodes(), [])

    def test_episodic_candidate_lands_in_the_daily_note(self):
        report = encode_turn(self.vault, "the deploy broke and we rolled back", turn_id="t1")
        node_id = daily_node_id()
        self.assertEqual([e["node_id"] for e in report["episodic"]], [node_id])
        node = self.vault.read_node(node_id)
        self.assertIsNotNone(node)
        self.assertEqual(node.frontmatter.category, "daily")
        self.assertIn("the deploy broke", node.content)
        self.assertEqual(node.frontmatter.extra.get("source_turns"), ["t1"])

    def test_semantic_candidate_becomes_its_own_node(self):
        report = encode_turn(self.vault, "Remember that my name is Ankit", turn_id="t1")
        self.assertEqual(len(report["semantic"]), 1)
        node_id = report["semantic"][0]["node_id"]
        self.assertTrue(node_id.startswith("user/"))
        node = self.vault.read_node(node_id)
        self.assertEqual(node.frontmatter.category, "user")
        self.assertEqual(node.frontmatter.source_turn, "t1")

    def test_re_encoding_the_same_turn_does_not_duplicate(self):
        """Idempotence: encoding runs per turn and must be safe to repeat."""
        first = encode_turn(self.vault, "the deploy broke and we rolled back", turn_id="t1")
        self.assertEqual(len(first["episodic"]), 1)
        second = encode_turn(self.vault, "the deploy broke and we rolled back", turn_id="t1")
        self.assertEqual(second["episodic"], [])
        node = self.vault.read_node(daily_node_id())
        self.assertEqual(node.content.count("the deploy broke"), 1)

    def test_two_statements_in_one_turn_both_recorded(self):
        report = encode_turn(
            self.vault,
            "Remember that I prefer tabs. Also the deploy broke and we rolled back.",
            turn_id="t1",
        )
        self.assertEqual(len(report["episodic"]), 1)
        self.assertEqual(len(report["semantic"]), 1)

    def test_correction_supersedes_the_contradicted_node(self):
        old = self.vault.write_node(
            "concept/recall-budget", "the recall budget is 600 tokens", title="Recall Budget"
        )
        report = encode_turn(
            self.vault, "That's wrong, the recall budget is 800 tokens", turn_id="t2"
        )
        self.assertIn(old.id, report["superseded"])
        superseded = self.vault.read_node(old.id)
        self.assertEqual(superseded.frontmatter.status, NodeStatus.SUPERSEDED.value)
        self.assertIsNotNone(superseded.frontmatter.superseded_by)
        # Nothing is deleted — the old note is still in the vault, with its history intact.
        self.assertIn("600", superseded.content)

    def test_dry_run_writes_nothing(self):
        report = encode_turn(self.vault, "Remember that my name is Ankit", turn_id="t1", dry_run=True)
        self.assertTrue(report["dry_run"])
        self.assertEqual(len(report["semantic"]), 1)
        self.assertEqual(self.vault.list_all_nodes(), [])

    def test_encode_without_a_vault_is_a_no_op(self):
        report = encode_turn(None, "Remember that my name is Ankit")
        self.assertEqual(report["semantic"], [])
        self.assertEqual(report["episodic"], [])

    def test_daily_node_accumulates_across_turns(self):
        encode_turn(self.vault, "the deploy broke", turn_id="t1")
        encode_turn(self.vault, "we shipped the fix", turn_id="t2")
        node = self.vault.read_node(daily_node_id())
        self.assertIn("the deploy broke", node.content)
        self.assertIn("we shipped the fix", node.content)
        self.assertEqual(node.frontmatter.extra.get("source_turns"), ["t1", "t2"])

    def test_salience_recorded_on_the_node(self):
        report = encode_turn(self.vault, "Remember that my name is Ankit", turn_id="t1")
        node = self.vault.read_node(report["semantic"][0]["node_id"])
        self.assertGreater(node.frontmatter.salience, 0.0)


class TestDailyNodeId(unittest.TestCase):
    def test_is_a_daily_category_path(self):
        self.assertTrue(daily_node_id().startswith("daily/"))

    def test_is_date_shaped(self):
        stamp = daily_node_id(time.time()).split("/", 1)[1]
        self.assertRegex(stamp, r"^\d{4}-\d{2}-\d{2}$")


if __name__ == "__main__":
    unittest.main()
