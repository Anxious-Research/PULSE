"""Tests for the forgetting curve / reconsolidation math."""

from __future__ import annotations

import unittest

from agent.brain.decay import (
    DAY_SECONDS,
    clamp_stability,
    is_dormant,
    on_recall,
    rank_score,
    retrievability,
)

T0 = 1_800_000_000.0


class TestRetrievability(unittest.TestCase):
    def test_no_elapsed_time_is_full_strength(self):
        self.assertAlmostEqual(retrievability(1.0, T0, now=T0), 1.0, places=6)

    def test_unknown_timestamp_is_full_strength(self):
        self.assertEqual(retrievability(1.0, None), 1.0)

    def test_decays_monotonically_with_time(self):
        values = [retrievability(2.0, T0, now=T0 + d * DAY_SECONDS) for d in range(0, 30)]
        self.assertEqual(values, sorted(values, reverse=True))
        self.assertLess(values[-1], values[0])

    def test_half_life_matches_stability(self):
        # At t = stability days, exp(-1) ~= 0.3679
        self.assertAlmostEqual(retrievability(1.0, T0, now=T0 + DAY_SECONDS), 0.367879, places=5)

    def test_higher_stability_decays_more_slowly(self):
        weak = retrievability(1.0, T0, now=T0 + 5 * DAY_SECONDS)
        strong = retrievability(10.0, T0, now=T0 + 5 * DAY_SECONDS)
        self.assertLess(weak, strong)

    def test_never_goes_negative_or_above_one(self):
        for d in (0, 1, 100, 10_000):
            value = retrievability(0.5, T0, now=T0 + d * DAY_SECONDS)
            self.assertGreaterEqual(value, 0.0)
            self.assertLessEqual(value, 1.0)

    def test_future_timestamp_clamps_to_one(self):
        self.assertEqual(retrievability(1.0, T0 + 10 * DAY_SECONDS, now=T0), 1.0)


class TestStability(unittest.TestCase):
    def test_clamp_rejects_junk(self):
        self.assertEqual(clamp_stability("abc"), 1.0)
        self.assertEqual(clamp_stability(None), 1.0)
        self.assertEqual(clamp_stability(float("nan")), 1.0)

    def test_clamp_bounds(self):
        self.assertLessEqual(clamp_stability(1e12), 3650.0)
        self.assertGreater(clamp_stability(-5), 0.0)

    def test_recall_increases_stability(self):
        self.assertGreater(on_recall(1.0, 0, T0, now=T0), 1.0)

    def test_spacing_effect_longer_gap_grows_more(self):
        soon = on_recall(1.0, 1, T0, now=T0 + 0.1 * DAY_SECONDS)
        later = on_recall(1.0, 1, T0, now=T0 + 10 * DAY_SECONDS)
        self.assertGreater(later, soon)

    def test_diminishing_returns_on_repeated_recall(self):
        first = on_recall(1.0, 0, T0, now=T0) - 1.0
        tenth = on_recall(1.0, 9, T0, now=T0) - 1.0
        self.assertGreater(first, tenth)

    def test_stability_growth_is_bounded(self):
        s = 1.0
        for i in range(200):
            s = on_recall(s, i, T0, now=T0 + 100 * DAY_SECONDS)
        self.assertLessEqual(s, 3650.0)


class TestForgettingIsNeverDeletion(unittest.TestCase):
    def test_dormant_but_still_present(self):
        # A very old, weak memory is dormant...
        self.assertTrue(is_dormant(0.1, T0, now=T0 + 400 * DAY_SECONDS))
        # ...but retrievability is a positive number, and the stability is untouched.
        value = retrievability(0.1, T0, now=T0 + 400 * DAY_SECONDS)
        self.assertGreater(value, 0.0)
        self.assertGreater(clamp_stability(0.1), 0.0)

    def test_a_dormant_memory_still_ranks_above_zero(self):
        score = rank_score(1.0, 0.1, T0, 1.0, now=T0 + 400 * DAY_SECONDS)
        self.assertGreater(score, 0.0)


class TestRankScore(unittest.TestCase):
    def test_activation_dominates(self):
        high = rank_score(1.0, 1.0, T0, 0.5, now=T0)
        low = rank_score(0.1, 1.0, T0, 0.5, now=T0)
        self.assertGreater(high, low)

    def test_salience_breaks_ties(self):
        strong = rank_score(0.5, 1.0, T0, 1.0, now=T0)
        weak = rank_score(0.5, 1.0, T0, 0.0, now=T0)
        self.assertGreater(strong, weak)

    def test_faded_memory_loses_at_equal_activation(self):
        fresh = rank_score(0.5, 5.0, T0, 0.8, now=T0)
        faded = rank_score(0.5, 5.0, T0, 0.8, now=T0 + 300 * DAY_SECONDS)
        self.assertGreater(fresh, faded)

    def test_junk_inputs_do_not_raise(self):
        for args in [("x", "y", None, "z"), (None, None, None, None)]:
            rank_score(*args)


if __name__ == "__main__":
    unittest.main()
