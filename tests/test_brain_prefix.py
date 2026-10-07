"""Tests for the Layer-1 stable prefix (specs/brain.md §6).

Two properties matter more than the formatting:

- **Cache safety.** The same vault state must compile to byte-identical text. If it does not,
  the prompt cache is invalidated and every turn pays full price for its prefix.
- **Selectivity.** Only identity and durable facts belong here. If stray categories leak in,
  the prefix grows with the vault and we are back to the char-budgeted flat memory this
  replaces.
"""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from agent.brain.prefix import (
    DEFAULT_MAX_CHARS,
    POINTER,
    compile_stable_prefix,
    has_stable_content,
)
from agent.brain.vault import BrainVault

T0 = 1_800_000_000


class PrefixTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="pulse-brain-prefix-"))
        self.vault = BrainVault(vault_dir=self.tmp)
        self.vault.ensure_vault_structure()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)


class TestEmptyAndDegenerate(PrefixTestCase):
    def test_empty_vault_produces_nothing(self):
        self.assertEqual(compile_stable_prefix(self.vault), "")
        self.assertFalse(has_stable_content(self.vault))

    def test_vault_with_only_other_categories_produces_nothing(self):
        self.vault.write_node("concept/forgetting-curve", "decay math", title="Forgetting Curve", now=T0)
        self.vault.write_node("project/x", "work", title="Project X", now=T0)
        self.assertEqual(compile_stable_prefix(self.vault), "")

    def test_node_with_empty_body_is_skipped(self):
        self.vault.write_node("user/empty", "", title="Empty Note", now=T0)
        self.assertEqual(compile_stable_prefix(self.vault), "")

    def test_broken_vault_returns_nothing_instead_of_raising(self):
        class Broken:
            def list_all_nodes(self):
                raise RuntimeError("vault on fire")

        self.assertEqual(compile_stable_prefix(Broken()), "")


class TestContent(PrefixTestCase):
    def test_self_and_user_are_included_with_fixed_section_order(self):
        self.vault.write_node("user/preferences", "prefers Hinglish replies", title="Preferences", now=T0)
        self.vault.write_node("self/identity", "PULSE is a single entity", title="Identity", now=T0)
        text = compile_stable_prefix(self.vault)
        self.assertIn("## Self", text)
        self.assertIn("## User", text)
        self.assertLess(text.index("## Self"), text.index("## User"), "Self must precede User")

    def test_title_and_content_both_render(self):
        self.vault.write_node("user/preferences", "prefers Hinglish replies", title="Preferences", now=T0)
        text = compile_stable_prefix(self.vault)
        self.assertIn("Preferences", text)
        self.assertIn("prefers Hinglish replies", text)

    def test_pointer_is_appended(self):
        self.vault.write_node("self/identity", "PULSE is a single entity", title="Identity", now=T0)
        self.assertIn(POINTER, compile_stable_prefix(self.vault))

    def test_wikilinks_render_as_plain_text(self):
        """Brackets and paths in a system prompt are noise; the alias or slug is the prose."""
        self.vault.write_node("self/wiring", "Memory lives in [[concept/vault|the vault]].", title="Wiring", now=T0)
        text = compile_stable_prefix(self.vault)
        self.assertIn("the vault", text)
        self.assertNotIn("[[", text)

    def test_unaliased_wikilink_uses_the_last_path_segment(self):
        self.vault.write_node("self/wiring", "See [[user/preferences]] for tone.", title="Wiring", now=T0)
        text = compile_stable_prefix(self.vault)
        self.assertIn("preferences", text)
        self.assertNotIn("[[", text)

    def test_note_ids_are_sorted_so_order_is_stable(self):
        for name in ("zzz", "aaa", "mmm"):
            self.vault.write_node(f"user/{name}", f"fact {name}", title=name, now=T0)
        text = compile_stable_prefix(self.vault)
        self.assertLess(text.index("aaa"), text.index("mmm"))
        self.assertLess(text.index("mmm"), text.index("zzz"))


class TestStatusFiltering(PrefixTestCase):
    def test_superseded_note_is_excluded(self):
        self.vault.write_node("user/old", "outdated preference", title="Old", now=T0)
        self.vault.write_node("user/new", "current preference", title="New", now=T0)
        self.vault.supersede("user/old", "user/new", now=T0)
        text = compile_stable_prefix(self.vault)
        self.assertIn("current preference", text)
        self.assertNotIn("outdated preference", text)

    def test_dormant_identity_is_still_included(self):
        """Self/user notes are identity: fading them out of the prompt would erase who PULSE is."""
        self.vault.write_node("self/identity", "PULSE is a single entity", title="Identity", now=T0)
        self.vault.write_node("self/identity", "PULSE is a single entity", title="Identity", status="dormant", now=T0)
        self.assertIn("PULSE is a single entity", compile_stable_prefix(self.vault))


class TestCacheSafety(PrefixTestCase):
    def test_output_is_byte_identical_across_calls(self):
        self.vault.write_node("self/identity", "PULSE is a single entity", title="Identity", now=T0)
        self.vault.write_node("user/preferences", "prefers Hinglish", title="Preferences", now=T0)
        self.assertEqual(compile_stable_prefix(self.vault), compile_stable_prefix(self.vault))

    def test_access_is_not_rendered_so_recall_does_not_reword_the_prefix(self):
        """A recalled note must not change the system prompt and bust the cache prefix."""
        self.vault.write_node("self/identity", "PULSE is a single entity", title="Identity", now=T0)
        before = compile_stable_prefix(self.vault)
        self.vault.record_access(["self/identity"] * 5, now=T0 + 10_000)
        self.assertEqual(compile_stable_prefix(self.vault), before)

    def test_a_new_note_does_change_the_prefix(self):
        self.vault.write_node("self/identity", "PULSE is a single entity", title="Identity", now=T0)
        before = compile_stable_prefix(self.vault)
        self.vault.write_node("user/language", "prefers Hinglish", title="Language", now=T0)
        self.assertNotEqual(compile_stable_prefix(self.vault), before)


class TestBudget(PrefixTestCase):
    def test_budget_is_never_exceeded(self):
        for i in range(40):
            self.vault.write_node(
                f"self/note-{i:03d}", " ".join(f"word{j}" for j in range(60)), title=f"Note {i:03d}", now=T0
            )
        for budget in (200, 400, 1000):
            text = compile_stable_prefix(self.vault, max_chars=budget)
            self.assertLessEqual(len(text), budget, f"budget {budget} exceeded")

    def test_single_note_cannot_crowd_out_others(self):
        self.vault.write_node("self/huge", "x " * 5000, title="Huge", now=T0)
        self.vault.write_node("self/small", "tiny fact", title="Small", now=T0)
        text = compile_stable_prefix(self.vault, max_chars=2000)
        self.assertLessEqual(len(text), 2000)

    def test_long_content_is_clipped_with_a_marker(self):
        self.vault.write_node("self/wordy", " ".join(["filler"] * 400) + " ENDMARKER", title="Wordy", now=T0)
        text = compile_stable_prefix(self.vault, max_chars=DEFAULT_MAX_CHARS)
        self.assertIn("…", text)
        self.assertNotIn("ENDMARKER", text)

    def test_tiny_budget_still_yields_something_readable(self):
        self.vault.write_node("self/identity", "PULSE is a single entity", title="Identity", now=T0)
        text = compile_stable_prefix(self.vault, max_chars=200)
        self.assertTrue(text)
        self.assertLessEqual(len(text), 200)


class TestNoTimestampLeak(PrefixTestCase):
    def test_no_epoch_numbers_leak_into_the_prefix(self):
        """Timestamps would make the prefix differ between sessions on the same content."""
        self.vault.write_node("self/identity", "PULSE is a single entity", title="Identity", now=T0)
        text = compile_stable_prefix(self.vault)
        self.assertNotIn(str(T0), text)
        self.assertNotIn("2027", text)


if __name__ == "__main__":
    unittest.main()
