"""Priority-2 longitudinal memory-evolution suite — deterministic simulated time.

Everything runs in an isolated PULSE_HOME; the user's live vault is never touched. Time is
injected via the ``now=`` parameter every mechanism already accepts, so these tests are fully
deterministic (no sleeps, no wall-clock dependence). They assert that memory *evolves* over a
simulated timeline rather than accumulating as a static notes graph:

  * decay: an unaccessed memory loses retrievability as simulated days pass;
  * retrieval-priority loss: at equal cue match, a faded memory ranks below a fresh one;
  * reconsolidation (spacing effect): recalling a faded memory restores its strength and the
    gain survives a restart;
  * indirect-cue recall: a cue that never names an older episode still retrieves it through an
    associative hop;
  * durable salience: an important older memory stays recoverable after weeks of simulated
    neglect (forgetting floors retrievability, never zeroes it).
"""

import os
import tempfile
import unittest

DAY = 86400.0


class _IsolatedBrain(unittest.TestCase):
    def setUp(self):
        self._home = tempfile.mkdtemp(prefix="pulse-longitudinal-")
        self._prev = os.environ.get("PULSE_HOME")
        os.environ["PULSE_HOME"] = self._home
        from agent.brain.vault import BrainVault
        self.vault = BrainVault()
        self.vault.ensure_vault_structure()
        # A fixed simulated epoch so every timestamp in the test is relative and deterministic.
        self.t0 = 1_700_000_000.0

    def tearDown(self):
        import shutil
        if self._prev is None:
            os.environ.pop("PULSE_HOME", None)
        else:
            os.environ["PULSE_HOME"] = self._prev
        shutil.rmtree(self._home, ignore_errors=True)


class TestDecayLowersRetrievability(_IsolatedBrain):
    def test_unaccessed_memory_fades_as_simulated_time_passes(self):
        from agent.brain import decay as decay_mod
        # Same stability, measured now vs. 30 simulated days later.
        self.vault.write_node("concept/episode", "A one-off episode happened.", category="concept",
                              now=self.t0, mark_access=True)
        node = self.vault.read_node("concept/episode")
        s = float(node.frontmatter.stability)
        la = float(node.frontmatter.last_accessed)
        fresh = decay_mod.retrievability(s, la, now=self.t0 + 1)
        faded = decay_mod.retrievability(s, la, now=self.t0 + 30 * DAY)
        self.assertLess(faded, fresh, "retrievability must fall as unaccessed days accumulate")
        self.assertGreater(faded, 0.0, "forgetting floors retrievability, never zeroes it")


class TestRetrievalPriorityLoss(_IsolatedBrain):
    def test_faded_memory_ranks_below_fresh_at_equal_activation(self):
        from agent.brain import decay as decay_mod
        # Equal activation + equal salience; only recency differs.
        act, sal = 1.0, 0.5
        s = decay_mod.NEUTRAL_STABILITY
        fresh = decay_mod.rank_score(act, s, self.t0 + 29 * DAY, sal, now=self.t0 + 30 * DAY)
        faded = decay_mod.rank_score(act, s, self.t0, sal, now=self.t0 + 30 * DAY)
        self.assertGreater(fresh, faded,
                           "a recently-touched memory must out-rank a long-neglected one")


class TestReconsolidationRestoresAndPersists(_IsolatedBrain):
    def test_recall_strengthens_a_faded_memory_and_survives_restart(self):
        from agent.brain import decay as decay_mod
        self.vault.write_node("concept/spacing", "The spacing effect governs reconsolidation.",
                              category="concept", now=self.t0, mark_access=True)
        before = float(self.vault.read_node("concept/spacing").frontmatter.stability)
        # 20 simulated days later it is recalled again (a real spacing-effect reconsolidation).
        self.vault.record_access(["concept/spacing"], now=self.t0 + 20 * DAY)
        after = float(self.vault.read_node("concept/spacing").frontmatter.stability)
        self.assertGreater(after, before, "recall after a gap must raise stability (spacing effect)")
        # Restart: a brand-new vault object reading the same PULSE_HOME keeps the gain.
        from agent.brain.vault import BrainVault
        persisted = float(BrainVault().read_node("concept/spacing").frontmatter.stability)
        self.assertEqual(after, persisted, "reconsolidated strength must persist across restart")
        # And the strengthened memory is now more retrievable at a later day than if never recalled.
        r_recalled = decay_mod.retrievability(after, self.t0 + 20 * DAY, now=self.t0 + 25 * DAY)
        r_neglected = decay_mod.retrievability(before, self.t0, now=self.t0 + 25 * DAY)
        self.assertGreater(r_recalled, r_neglected)


class TestIndirectCueRecall(_IsolatedBrain):
    def test_a_cue_retrieves_an_associated_episode_it_never_names(self):
        from agent.brain.consolidate import relink_mentions
        from agent.brain.recall import recall
        # A hub concept and an older episode anchored to it by an explicit [[wikilink]].
        self.vault.write_node("concept/hippocampus",
                              "The hippocampus binds episodes into memory.", category="concept",
                              now=self.t0, mark_access=True)
        self.vault.write_node("concept/old-episode",
                              "Last spring the [[concept/hippocampus]] launch demo was recorded.",
                              category="concept", now=self.t0, mark_access=True)
        relink_mentions(self.vault, now=self.t0)
        # The query names ONLY the hub concept — never 'spring' / 'launch' / 'demo'. The older
        # episode is reached through the association (a spreading-activation hop), proving
        # indirect, cue-based retrieval rather than a direct lexical hit.
        result = recall("hippocampus", now=self.t0 + 1, hops=2)
        hit_ids = set(result.node_ids())
        self.assertIn("concept/old-episode", hit_ids,
                      "an indirect cue must retrieve the older associated episode through a hop")

    def test_relative_floor_drops_a_weaker_indirect_hit_behind_a_fresher_match(self):
        from agent.brain.consolidate import relink_mentions
        from agent.brain.recall import recall
        # Same hub, but now a fresher, more strongly-matching note competes. The older episode's
        # indirect activation falls under the relative floor — the stronger current match wins.
        # This is selective recall, not a bug: memory surfaces the best answer, not everything.
        self.vault.write_node("concept/hippocampus",
                              "The hippocampus binds episodes into memory.", category="concept",
                              now=self.t0, mark_access=True)
        self.vault.write_node("concept/old-episode",
                              "Last spring the [[concept/hippocampus]] launch demo was recorded.",
                              category="concept", now=self.t0, mark_access=True)
        self.vault.write_node("concept/new-note",
                              "Today's work builds on the [[concept/hippocampus]] binding idea.",
                              category="concept", now=self.t0 + 10 * DAY, mark_access=True)
        relink_mentions(self.vault, now=self.t0 + 10 * DAY)
        result = recall("Tell me about the hippocampus binding", now=self.t0 + 11 * DAY, hops=2)
        hit_ids = set(result.node_ids())
        self.assertIn("concept/new-note", hit_ids)
        self.assertNotIn("concept/old-episode", hit_ids,
                         "a weaker indirect hit must fall below the relative floor behind a stronger match")


class TestDurableSalienceRemainsRecoverable(_IsolatedBrain):
    def test_neglected_memory_leaves_active_recall_but_is_never_deleted(self):
        from agent.brain import decay as decay_mod
        from agent.brain.recall import recall
        from agent.brain.vault import BrainVault
        self.vault.write_node("concept/north-star",
                              "The north-star principle: one entity, one brain, one source of truth.",
                              category="concept", salience=1.0, now=self.t0, mark_access=True)
        later = self.t0 + 120 * DAY
        # After long neglect it drops out of active recall (forgetting is real)...
        self.assertEqual(recall("north-star principle one source of truth", now=later).node_ids(), [],
                         "a long-neglected memory should leave active recall")
        # ...but it is never destroyed: still on disk, retrievability floored above zero.
        node = BrainVault().read_node("concept/north-star")
        self.assertIsNotNone(node, "forgetting must never delete the memory")
        r = decay_mod.retrievability(float(node.frontmatter.stability),
                                     float(node.frontmatter.last_accessed), now=later)
        self.assertGreater(r, 0.0, "retrievability is floored, never zero (nothing is erased)")

    def test_periodically_reinforced_important_memory_stays_recallable(self):
        from agent.brain.recall import recall
        # An important memory is one that gets revisited; spaced reinforcement keeps it strong.
        self.vault.write_node("concept/north-star",
                              "The north-star principle one entity one brain one source of truth.",
                              category="concept", salience=1.0, now=self.t0, mark_access=True)
        for week in (1, 2, 3, 4, 5, 6):
            self.vault.record_access(["concept/north-star"], now=self.t0 + week * 7 * DAY)
        last = float(self.vault.read_node("concept/north-star").frontmatter.last_accessed)
        # Days after the last reinforcement it is still comfortably recallable.
        result = recall("north-star principle one source of truth", now=last + 5 * DAY)
        self.assertIn("concept/north-star", set(result.node_ids()),
                      "a periodically-reinforced important memory must remain recallable")


if __name__ == "__main__":
    unittest.main()
