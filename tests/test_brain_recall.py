"""Tests for cue-based associative recall (specs/brain.md S3).

The behaviour that matters: recall is *associative* (a linked memory surfaces even when it
does not match the cue), *selective* (a weak cue yields nothing rather than filler), and
*bounded* (the rendered block never exceeds the token budget).
"""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from agent.brain.index import BrainIndex
from agent.brain.recall import (
    CHARS_PER_TOKEN,
    extract_cues,
    recall,
    recall_block,
)
from agent.brain.vault import BrainVault

T0 = 1_800_000_000
DAY = 86400


class RecallTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="pulse-brain-recall-"))
        self.vault = BrainVault(vault_dir=self.tmp)
        self.vault.ensure_vault_structure()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def seed_vault(self):
        self.vault.write_node(
            "concept/forgetting-curve",
            "Retrievability decays as exp(-delta over stability). Recall strengthens stability.",
            title="Forgetting Curve",
            tags=["memory", "decay"],
            salience=0.9,
            now=T0,
        )
        self.vault.write_node(
            "user/preferences",
            "User prefers Hinglish replies and wants real logic with no shortcuts.",
            title="User Preferences",
            tags=["preference", "language"],
            salience=0.95,
            now=T0,
        )
        self.vault.write_node(
            "project/kubernetes-migration",
            "Moving ingress to the new cluster with TLS termination at the edge.",
            title="Kubernetes Migration",
            tags=["infra"],
            salience=0.5,
            now=T0,
        )
        return BrainIndex(self.vault).rebuild()


class TestCues(RecallTestCase):
    def test_stopwords_and_short_tokens_dropped(self):
        cues = extract_cues("what is the and a of it")
        self.assertEqual(cues, [])

    def test_content_words_kept(self):
        self.assertIn("forgetting", extract_cues("explain the forgetting curve"))

    def test_word_pairs_help_phrases(self):
        self.assertIn("forgetting curve", extract_cues("explain the forgetting curve"))

    def test_explicit_mentions_extracted(self):
        cues = extract_cues("see [[concept/forgetting-curve]] and `stability`")
        self.assertTrue(any("forgetting" in c for c in cues))
        self.assertIn("stability", cues)

    def test_cue_count_is_bounded(self):
        self.assertLessEqual(len(extract_cues(" ".join(f"word{i}" for i in range(200)))), 24)

    def test_empty_input(self):
        self.assertEqual(extract_cues(""), [])


class TestSelectivity(RecallTestCase):
    def test_empty_query_recalls_nothing(self):
        self.seed_vault()
        self.assertTrue(recall("", vault=self.vault).empty)

    def test_irrelevant_query_injects_nothing(self):
        self.seed_vault()
        result = recall("quantum chromodynamics lattice gauge", vault=self.vault)
        self.assertTrue(result.empty, f"weak cue should be gated, got {result.to_dict()}")

    def test_irrelevant_query_renders_empty_string(self):
        self.seed_vault()
        self.assertEqual(recall_block("quantum chromodynamics lattice gauge", vault=self.vault), "")

    def test_empty_vault_recalls_nothing(self):
        self.assertTrue(recall("anything at all", vault=self.vault).empty)


class TestRelevance(RecallTestCase):
    def test_matching_memory_is_recalled_first(self):
        self.seed_vault()
        result = recall("what do you know about the forgetting curve", vault=self.vault)
        self.assertFalse(result.empty)
        self.assertEqual(result.hits[0].node_id, "concept/forgetting-curve")

    def test_user_preference_query_hits_the_right_node(self):
        self.seed_vault()
        result = recall("which language does the user prefer for replies", vault=self.vault)
        self.assertFalse(result.empty)
        self.assertEqual(result.hits[0].node_id, "user/preferences")

    def test_snippet_is_present_and_bounded(self):
        self.seed_vault()
        hit = recall("forgetting curve stability", vault=self.vault).hits[0]
        self.assertTrue(hit.snippet)
        self.assertLessEqual(len(hit.snippet), 300)

    def test_scores_are_ordered_descending(self):
        self.seed_vault()
        hits = recall("forgetting curve stability memory decay", vault=self.vault).hits
        self.assertEqual(hits, sorted(hits, key=lambda h: -h.score))

    def test_limit_is_respected(self):
        self.seed_vault()
        self.assertLessEqual(len(recall("memory user project", vault=self.vault, limit=1).hits), 1)


class TestAssociativeSpread(RecallTestCase):
    def test_linked_memory_surfaces_without_matching_the_cue(self):
        """The core associative property: a neighbour is recalled through the link."""
        self.vault.write_node(
            "concept/spreading-activation",
            "Activation spreads over links to [[user/preferences]].",
            title="Spreading Activation",
            tags=["memory", "graph"],
            now=T0,
        )
        self.vault.write_node(
            "user/preferences",
            "User prefers Hinglish replies.",
            title="User Preferences",
            tags=["preference"],
            now=T0,
        )
        result = recall("spreading activation graph", vault=self.vault, hops=2)
        ids = result.node_ids()
        self.assertIn("concept/spreading-activation", ids)
        self.assertIn(
            "user/preferences",
            ids,
            "a linked memory must surface through spreading activation, not just keyword match",
        )

    def test_no_spread_when_hops_zero(self):
        self.vault.write_node("concept/a", "mentions [[concept/b]]", title="Alpha topic", now=T0)
        self.vault.write_node("concept/b", "unrelated prose entirely", title="Beta topic", now=T0)
        result = recall("alpha topic", vault=self.vault, hops=0)
        self.assertIn("concept/a", result.node_ids())


class TestForgettingIntegration(RecallTestCase):
    def test_superseded_memory_is_not_recalled(self):
        self.vault.write_node("concept/old", "forgetting curve old version", title="Old", now=T0)
        self.vault.write_node("concept/new", "forgetting curve new version", title="New", now=T0)
        self.vault.supersede("concept/old", "concept/new", now=T0)
        result = recall("forgetting curve", vault=self.vault)
        self.assertNotIn("concept/old", result.node_ids())
        self.assertIn("concept/new", result.node_ids())

    def test_stale_memory_is_excluded_by_default_but_reachable_when_asked(self):
        self.vault.write_node("concept/stale", "forgetting curve ancient note", title="Stale", now=T0)
        far = T0 + 400 * DAY
        default = recall("forgetting curve ancient", vault=self.vault, now=far)
        self.assertTrue(default.empty, "a fully decayed memory should not be injected")

        included = recall(
            "forgetting curve ancient", vault=self.vault, now=far, include_dormant=True, min_score=0.0
        )
        self.assertIn("concept/stale", included.node_ids())

    def test_recently_recalled_memory_ranks_above_a_stale_one(self):
        self.vault.write_node("concept/fresh", "forgetting curve fresh note", title="Fresh", now=T0)
        self.vault.write_node("concept/stale", "forgetting curve stale note", title="Stale", now=T0)
        self.vault.record_access(["concept/fresh"] * 5, now=T0)
        later = T0 + 3 * DAY
        hits = recall("forgetting curve note", vault=self.vault, now=later).hits
        self.assertTrue(hits)
        self.assertEqual(hits[0].node_id, "concept/fresh")


class TestRetrievalQuality(RecallTestCase):
    """Retrieval must be discriminative: relevant scores well clear of the gate."""

    def test_relevant_query_scores_well_above_the_injection_gate(self):
        self.seed_vault()
        result = recall("how does the forgetting curve work", vault=self.vault)
        self.assertFalse(result.empty)
        self.assertEqual(result.hits[0].node_id, "concept/forgetting-curve")
        self.assertGreater(result.top_score(), 0.2, "a relevant hit must not sit on the threshold")

    def test_exact_phrase_in_body_beats_a_vague_mention(self):
        self.vault.write_node(
            "concept/specific",
            "The forgetting curve describes how retrievability decays over time.",
            title="Exponential decay of memories",
            now=T0,
        )
        self.vault.write_node(
            "concept/vague",
            "Memory in general is interesting and forgetting happens sometimes.",
            title="Thoughts on memory",
            now=T0,
        )
        hits = recall("forgetting curve", vault=self.vault).hits
        self.assertTrue(hits)
        self.assertEqual(hits[0].node_id, "concept/specific")

    def test_inflection_variants_still_match(self):
        """A cue in one form must match the note's other form (prefer/prefers)."""
        self.vault.write_node(
            "user/preferences",
            "The user prefers Hinglish replies.",
            title="User Preferences",
            now=T0,
        )
        result = recall("what does the user prefer", vault=self.vault)
        self.assertFalse(result.empty, "prefix matching should bridge prefer/prefers")
        self.assertEqual(result.hits[0].node_id, "user/preferences")

    def test_weak_also_rans_are_dropped(self):
        self.vault.write_node("concept/strong", "forgetting curve forgetting curve", title="Forgetting Curve", now=T0)
        for i in range(6):
            self.vault.write_node(f"concept/weak{i}", "forgetting is a thing that happens", title=f"Note {i}", now=T0)
        hits = recall("forgetting curve", vault=self.vault, limit=8).hits
        self.assertTrue(hits)
        self.assertEqual(hits[0].node_id, "concept/strong")
        for hit in hits:
            self.assertGreaterEqual(hit.score, hits[0].score * 0.25)

    def test_title_match_outranks_body_match(self):
        self.vault.write_node("concept/in-title", "unrelated prose", title="Forgetting Curve", now=T0)
        self.vault.write_node("concept/in-body", "prose about the forgetting curve", title="Some Other Note", now=T0)
        hits = recall("forgetting curve", vault=self.vault).hits
        self.assertEqual(hits[0].node_id, "concept/in-title")

    def test_a_node_that_merely_links_to_a_topic_does_not_outrank_it(self):
        """Links are structure: referencing a topic is not the same as being about it."""
        self.vault.write_node(
            "user/preferences", "The user prefers Hinglish replies.", title="User Preferences", now=T0
        )
        self.vault.write_node(
            "concept/referrer",
            "Cue-based recall spreads activation across [[user/preferences]].",
            title="Associative Recall",
            salience=0.9,
            now=T0,
        )
        hits = recall("what does the user prefer", vault=self.vault, hops=2).hits
        self.assertTrue(hits)
        self.assertEqual(hits[0].node_id, "user/preferences")

    def test_the_referrer_is_still_recalled_as_a_neighbour(self):
        self.vault.write_node(
            "user/preferences", "The user prefers Hinglish replies.", title="User Preferences", now=T0
        )
        self.vault.write_node(
            "concept/referrer",
            "Cue-based recall spreads activation across [[user/preferences]].",
            title="Associative Recall",
            now=T0,
        )
        ids = recall("what does the user prefer", vault=self.vault, hops=2).node_ids()
        self.assertIn("user/preferences", ids)
        self.assertIn("concept/referrer", ids, "the linked node must still surface by association")

    def test_alias_text_is_still_prose(self):
        """[[target|alias]] keeps the alias — that is authored text, not structure."""
        self.vault.write_node("concept/a", "see [[concept/b|the forgetting curve]] for details", title="Alpha", now=T0)
        self.vault.write_node("concept/b", "unrelated", title="Beta", now=T0)
        hits = recall("forgetting curve", vault=self.vault).hits
        self.assertTrue(hits)
        self.assertEqual(hits[0].node_id, "concept/a")


class TestRendering(RecallTestCase):
    def test_render_respects_the_token_budget(self):
        for i in range(12):
            self.vault.write_node(
                f"concept/topic-{i}",
                " ".join(f"word{j}" for j in range(60)),
                title=f"Topic {i} about memory",
                now=T0,
            )
        budget = 80
        block = recall("memory topic", vault=self.vault, limit=12, max_tokens=budget).render(max_tokens=budget)
        self.assertTrue(block)
        self.assertLessEqual(len(block), budget * CHARS_PER_TOKEN)

    def test_render_includes_related_links(self):
        self.vault.write_node("concept/a", "memory topic alpha see [[concept/b]]", title="Alpha memory topic", now=T0)
        self.vault.write_node("concept/b", "beta", title="Beta", now=T0)
        block = recall("alpha memory topic", vault=self.vault).render()
        self.assertIn("related:", block)

    def test_render_of_empty_result_is_empty(self):
        self.seed_vault()
        self.assertEqual(recall("quantum chromodynamics", vault=self.vault).render(), "")

    def test_to_dict_is_serialisable(self):
        import json

        self.seed_vault()
        json.dumps(recall("forgetting curve", vault=self.vault).to_dict())


if __name__ == "__main__":
    unittest.main()
