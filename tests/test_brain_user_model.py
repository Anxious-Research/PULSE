"""User-model layer tests — evidence-backed facts, scoping, supersession, snapshot.

Asserts against the production entry points (record_user_fact, current_model,
extract_from_text) exactly as the live loop calls them — the same standard as
tests/test_brain_live_loop.py for the G10-G17 defects.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from agent.brain import user_model as um
from agent.brain.vault import BrainVault


class UserModelTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="pulse-brain-user-model-"))
        self.vault = BrainVault(vault_dir=self.tmp)
        self.vault.ensure_vault_structure()

    # ── recording ────────────────────────────────────────────────────────────

    def test_explicit_fact_creates_node(self):
        report = um.record_user_fact(
            self.vault, "User prefers local-first AI",
            fact_type="preference", source="explicit",
        )
        self.assertEqual(report["action"], "created")
        self.assertEqual(report["fact_type"], "preference")
        self.assertEqual(report["source"], "explicit")
        node = self.vault.read_node(report["node_id"])
        self.assertIsNotNone(node)
        self.assertEqual(node.frontmatter.extra.get("fact_type"), "preference")
        self.assertEqual(node.frontmatter.extra.get("fact_source"), "explicit")

    def test_restatement_reinforces_not_duplicates(self):
        r1 = um.record_user_fact(self.vault, "User prefers concise replies",
                                 source="explicit", evidence_id="t1")
        before = len(self.vault.list_all_nodes())
        r2 = um.record_user_fact(self.vault, "User prefers concise replies",
                                 source="explicit", evidence_id="t2")
        after = len(self.vault.list_all_nodes())
        self.assertEqual(r2["action"], "reinforced")
        self.assertEqual(after, before, "reinforcement must not create a second node")
        self.assertEqual(r2.get("evidence_count"), 2)

    def test_empty_content_skipped(self):
        report = um.record_user_fact(self.vault, "   ")
        self.assertEqual(report["action"], "skipped")

    def test_empty_message_yields_nothing(self):
        self.assertEqual(um.extract_from_text(""), [])
        self.assertEqual(um.extract_from_text("   "), [])

    # ── confidence ───────────────────────────────────────────────────────────

    def test_inferred_confidence_never_reaches_one(self):
        for count in (1, 2, 5, 20, 100, 1000):
            conf = um.inferred_confidence(count)
            self.assertLess(conf, 1.0, f"confidence reached {conf} at {count} obs")
        self.assertEqual(um.inferred_confidence(1), um.INFERRED_BASE)
        self.assertGreater(um.inferred_confidence(20), um.inferred_confidence(2))

    def test_confidence_monotonic(self):
        confs = [um.inferred_confidence(n) for n in range(1, 15)]
        self.assertEqual(confs, sorted(confs), "confidence must rise with evidence")

    def test_inferred_ceiling_below_assertion(self):
        self.assertLess(um.INFERRED_CEILING, 1.0)
        self.assertLessEqual(um.inferred_confidence(10000), um.INFERRED_CEILING)

    # ── supersession ─────────────────────────────────────────────────────────

    def test_supersede_preserves_history(self):
        r1 = um.record_user_fact(self.vault, "User prefers dark mode", source="explicit")
        old_id = r1["node_id"]
        r2 = um.supersede_user_fact(self.vault, old_id,
                                    "User now prefers light themes", source="corrected")
        self.assertEqual(r2["action"], "superseded")

        old_node = self.vault.read_node(old_id)
        self.assertEqual(old_node.frontmatter.status, "superseded")
        self.assertIsNotNone(old_node.frontmatter.extra.get("valid_until"))

        snap = um.current_model(self.vault)
        current_contents = {f.content for f in snap.preferences}
        self.assertTrue(any("light" in c for c in current_contents))
        self.assertNotIn("User prefers dark mode", current_contents)

    def test_supersede_nonexistent_node_is_safe(self):
        r = um.supersede_user_fact(self.vault, "user/ghost", "whatever")
        self.assertEqual(r["action"], "skipped")

    # ── scoping ─────────────────────────────────────────────────────────────

    def test_scoped_facts_do_not_leak(self):
        um.record_user_fact(self.vault, "Global preference fact", source="explicit")
        um.record_user_fact(self.vault, "Uses tabs not spaces",
                            source="explicit", scope="project-alpha")
        um.record_user_fact(self.vault, "Prefers detailed reports",
                            source="explicit", scope="project-beta")

        alpha = um.facts_for_scope(self.vault, scope="project-alpha")
        beta = um.facts_for_scope(self.vault, scope="project-beta")

        alpha_scoped = {f.content for f in alpha if f.scope == "project-alpha"}
        beta_scoped = {f.content for f in beta if f.scope == "project-beta"}

        self.assertIn("Uses tabs not spaces", alpha_scoped)
        self.assertNotIn("Uses tabs not spaces", beta_scoped)
        self.assertIn("Prefers detailed reports", beta_scoped)
        self.assertNotIn("Prefers detailed reports", alpha_scoped)

    def test_global_facts_visible_across_scopes(self):
        um.record_user_fact(self.vault, "Prefers concise replies", source="explicit")
        got = um.facts_for_scope(self.vault, scope="any-project")
        self.assertTrue(any(f.content == "Prefers concise replies" for f in got))

    # ── snapshot ────────────────────────────────────────────────────────────

    def test_snapshot_reflects_supersession(self):
        r1 = um.record_user_fact(self.vault, "User prefers dark mode", source="explicit")
        snap1 = um.current_model(self.vault)
        self.assertEqual(len(snap1.preferences), 1)
        um.supersede_user_fact(self.vault, r1["node_id"],
                               "User now prefers light themes", source="corrected")
        snap = um.current_model(self.vault)
        self.assertTrue(any("light" in f.content for f in snap.preferences))

    def test_snapshot_is_projection_not_store(self):
        snap1 = um.current_model(self.vault)
        um.record_user_fact(self.vault, "Prefers dark themes", source="explicit")
        snap2 = um.current_model(self.vault)
        self.assertEqual(len(snap2.preferences), len(snap1.preferences) + 1)
        self.assertEqual(snap2.explicit_count, snap1.explicit_count + 1)

    def test_render_empty_vault(self):
        snap = um.current_model(self.vault)
        self.assertEqual(um.render_model_block(snap), "")

    def test_render_respects_budget(self):
        for i in range(20):
            um.record_user_fact(self.vault, f"User prefers style number {i} for formatting",
                                source="explicit")
        snap = um.current_model(self.vault)
        block = um.render_model_block(snap, max_chars=400)
        self.assertLessEqual(len(block), 500)  # truncation adds ellipsis

    # ── extraction ─────────────────────────────────────────────────────────

    def test_preference_detected(self):
        out = um.extract_from_text("I really prefer concise answers")
        self.assertTrue(out)
        self.assertEqual(out[0]["fact_type"], "preference")

    def test_goal_detected(self):
        out = um.extract_from_text("I want to build a compiler this quarter")
        self.assertTrue(out)
        self.assertEqual(out[0]["fact_type"], "goal")

    def test_identity_detected(self):
        out = um.extract_from_text("Call me Ankit")
        self.assertTrue(out)
        self.assertEqual(out[0]["fact_type"], "identity")

    def test_constraint_detected(self):
        out = um.extract_from_text("I cannot use paid APIs on this machine")
        self.assertTrue(out)
        self.assertEqual(out[0]["fact_type"], "constraint")

    def test_context_detected(self):
        out = um.extract_from_text("My laptop runs Ubuntu 22.04 with 16GB RAM")
        self.assertTrue(out)
        self.assertEqual(out[0]["fact_type"], "context")

    def test_chatter_ignored(self):
        self.assertEqual(um.extract_from_text("what time is it"), [])
        self.assertEqual(um.extract_from_text("deploy the service"), [])

    def test_project_state_not_user_fact(self):
        self.assertEqual(um.extract_from_text("The payments project is paused"), [])

    def test_multi_statement_message(self):
        out = um.extract_from_text(
            "I prefer dark mode. Also, I want to learn Rust this year."
        )
        types = {r["fact_type"] for r in out}
        self.assertIn("preference", types)
        self.assertIn("goal", types)

    # ── evidence refs deduplication & capping ──────────────────────────────

    def test_record_user_fact_evidence_refs_dedup(self):
        # Repeated calls with the exact same turn ID (as str or numeric)
        r1 = um.record_user_fact(self.vault, "User prefers dark mode", evidence_id="20261029195908091780")
        node_id = r1["node_id"]
        # Call again with string
        um.record_user_fact(self.vault, "User prefers dark mode", evidence_id="20261029195908091780")
        # Call again with numeric / different type representation
        um.record_user_fact(self.vault, "User prefers dark mode", evidence_id=20261029195908091780)
        
        node = self.vault.read_node(node_id)
        self.assertIsNotNone(node)
        assert node is not None
        refs = node.frontmatter.extra.get("evidence_refs", [])
        self.assertEqual(refs, ["20261029195908091780"])

    def test_record_user_fact_evidence_refs_capped(self):
        content = "User loves Python"
        for i in range(30):
            um.record_user_fact(self.vault, content, evidence_id=f"turn_{i:02d}")
        
        facts = um.facts_for_scope(self.vault)
        fact = next(f for f in facts if f.content == content)
        node = self.vault.read_node(fact.node_id)
        self.assertIsNotNone(node)
        assert node is not None
        refs = node.frontmatter.extra.get("evidence_refs", [])
        self.assertEqual(len(refs), 20)
        self.assertEqual(refs[0], "turn_10")
        self.assertEqual(refs[-1], "turn_29")
        self.assertEqual(len(fact.evidence_ids), 20)

    def test_revise_user_fact_evidence_refs_dedup_and_cap(self):
        r = um.record_user_fact(self.vault, "User uses vim", evidence_id="turn_init")
        node_id = r["node_id"]

        # Duplicate revise call should not duplicate or increment evidence_count
        um.revise_user_fact(self.vault, node_id, evidence_id="turn_init")
        node = self.vault.read_node(node_id)
        self.assertIsNotNone(node)
        assert node is not None
        refs = node.frontmatter.extra.get("evidence_refs", [])
        self.assertEqual(refs, ["turn_init"])
        self.assertEqual(node.frontmatter.extra.get("evidence_count"), 1)

        # Add up to 25 unique turn IDs
        for i in range(25):
            um.revise_user_fact(self.vault, node_id, evidence_id=f"rev_{i:02d}")

        node = self.vault.read_node(node_id)
        self.assertIsNotNone(node)
        assert node is not None
        refs = node.frontmatter.extra.get("evidence_refs", [])
        self.assertEqual(len(refs), 20)
        self.assertEqual(refs[-1], "rev_24")

    # ── fail-soft ───────────────────────────────────────────────────────────

    def test_no_vault_never_raises(self):
        class Broken:
            def list_all_nodes(self):
                raise RuntimeError("disk gone")

        self.assertEqual(um.facts_for_scope(Broken()), [])
        self.assertEqual(um.active_projects(Broken()), [])
        self.assertEqual(um.current_model(Broken()).all_facts, [])

    def test_supersede_missing_node_safe(self):
        r = um.supersede_user_fact(self.vault, "user/ghost", "new content")
        self.assertEqual(r["action"], "skipped")


if __name__ == "__main__":
    unittest.main()
