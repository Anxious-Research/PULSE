"""Tests for legacy flat-memory migration (specs/brain.md S2).

The governing rule: migration is ADDITIVE and IDEMPOTENT. Nothing is deleted, and a second
run must not duplicate anything. A previous attempt lost the user's memory by replacing the
store before the replacement worked, so these properties are tested, not assumed.
"""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from agent.brain.migrate import (
    ENTRY_DELIMITER,
    fingerprint,
    migrate_legacy_memory,
    needs_migration,
    plan_migration,
    split_entries,
)
from agent.brain.vault import BrainVault


def _write_legacy(src: Path, memory: str = "", user: str = "") -> None:
    src.mkdir(parents=True, exist_ok=True)
    if memory:
        (src / "MEMORY.md").write_text(memory, encoding="utf-8")
    if user:
        (src / "USER.md").write_text(user, encoding="utf-8")


class MigrationTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="pulse-brain-migrate-"))
        self.src = self.tmp / "memories"
        self.vault = BrainVault(vault_dir=self.tmp / "brain")
        self.vault.ensure_vault_structure()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)


class TestParsing(MigrationTestCase):
    def test_split_entries(self):
        self.assertEqual(split_entries("one§two§three".replace("§", ENTRY_DELIMITER)), ["one", "two", "three"])

    def test_split_ignores_blank_entries(self):
        raw = f"{ENTRY_DELIMITER}a{ENTRY_DELIMITER}{ENTRY_DELIMITER}b{ENTRY_DELIMITER}"
        self.assertEqual(split_entries(raw), ["a", "b"])

    def test_empty_file(self):
        self.assertEqual(split_entries(""), [])
        self.assertEqual(split_entries("   \n "), [])

    def test_fingerprint_is_stable_and_whitespace_insensitive(self):
        self.assertEqual(fingerprint("x"), fingerprint("  x  "))
        self.assertNotEqual(fingerprint("x"), fingerprint("y"))


class TestDryRun(MigrationTestCase):
    def test_dry_run_is_the_default_and_writes_nothing(self):
        _write_legacy(self.src, memory="User likes Hinglish.")
        report = migrate_legacy_memory(source_dir=self.src, vault=self.vault)
        self.assertTrue(report["dry_run"])
        self.assertEqual(report["created"], 0)
        self.assertEqual(report["would_create"], 1)
        self.assertEqual(self.vault.list_all_nodes(), [])

    def test_missing_source_dir_is_not_an_error(self):
        report = migrate_legacy_memory(source_dir=self.tmp / "nope", vault=self.vault, dry_run=False)
        self.assertEqual(report["created"], 0)
        self.assertFalse(needs_migration(source_dir=self.tmp / "nope"))


class TestMigration(MigrationTestCase):
    def test_creates_one_node_per_entry_with_content_intact(self):
        _write_legacy(
            self.src,
            memory=f"PULSE vault lives in ~/.pulse/brain{ENTRY_DELIMITER}Forgetting is exp(-t/s)",
            user=f"Prefers Hinglish replies{ENTRY_DELIMITER}Wants no shortcuts",
        )
        report = migrate_legacy_memory(source_dir=self.src, vault=self.vault, dry_run=False)
        self.assertEqual(report["created"], 4)

        nodes = self.vault.list_all_nodes()
        self.assertEqual(len(nodes), 4)
        bodies = " ".join(n.content for n in nodes)
        for fragment in ("~/.pulse/brain", "exp(-t/s)", "Prefers Hinglish", "no shortcuts"):
            self.assertIn(fragment, bodies)

    def test_categories_follow_the_source_file(self):
        _write_legacy(self.src, memory="agent note", user="user fact")
        migrate_legacy_memory(source_dir=self.src, vault=self.vault, dry_run=False)
        categories = {n.category for n in self.vault.list_all_nodes()}
        self.assertEqual(categories, {"concept", "user"})

    def test_entries_are_tagged_as_migrated(self):
        _write_legacy(self.src, memory="some note")
        migrate_legacy_memory(source_dir=self.src, vault=self.vault, dry_run=False)
        node = self.vault.list_all_nodes()[0]
        self.assertIn("migrated", node.frontmatter.tags)

    def test_nodes_are_valid_vault_markdown(self):
        _write_legacy(self.src, memory="note body here")
        migrate_legacy_memory(source_dir=self.src, vault=self.vault, dry_run=False)
        node = self.vault.list_all_nodes()[0]
        self.assertTrue(node.path.is_file())
        self.assertIn("note body here", node.path.read_text(encoding="utf-8"))


class TestIdempotency(MigrationTestCase):
    def test_second_run_creates_nothing(self):
        _write_legacy(self.src, memory="first note", user="user note")
        first = migrate_legacy_memory(source_dir=self.src, vault=self.vault, dry_run=False)
        self.assertEqual(first["created"], 2)

        second = migrate_legacy_memory(source_dir=self.src, vault=self.vault, dry_run=False)
        self.assertEqual(second["created"], 0, "re-running must not duplicate")
        self.assertEqual(len(self.vault.list_all_nodes()), 2)

    def test_needs_migration_false_after_import(self):
        _write_legacy(self.src, memory="only note")
        self.assertTrue(needs_migration(source_dir=self.src, vault=self.vault))
        migrate_legacy_memory(source_dir=self.src, vault=self.vault, dry_run=False)
        self.assertFalse(needs_migration(source_dir=self.src, vault=self.vault))

    def test_needs_migration_checks_the_vault_it_is_given(self):
        """A vault with no fingerprints must still report work to do."""
        _write_legacy(self.src, memory="only note")
        other = BrainVault(vault_dir=self.tmp / "other-brain")
        other.ensure_vault_structure()
        self.assertTrue(needs_migration(source_dir=self.src, vault=other))

    def test_incremental_pickup_of_new_entries(self):
        _write_legacy(self.src, memory="note one")
        migrate_legacy_memory(source_dir=self.src, vault=self.vault, dry_run=False)
        _write_legacy(self.src, memory=f"note one{ENTRY_DELIMITER}note two")
        report = migrate_legacy_memory(source_dir=self.src, vault=self.vault, dry_run=False)
        self.assertEqual(report["created"], 1, "only the new entry should be imported")
        self.assertEqual(len(self.vault.list_all_nodes()), 2)

    def test_fingerprint_survives_a_round_trip(self):
        _write_legacy(self.src, memory="persisted fingerprint")
        migrate_legacy_memory(source_dir=self.src, vault=self.vault, dry_run=False)
        node = self.vault.list_all_nodes()[0]
        self.assertEqual(node.frontmatter.extra.get("source_fingerprint"), fingerprint("persisted fingerprint"))


class TestSafety(MigrationTestCase):
    def test_existing_nodes_are_never_touched_or_deleted(self):
        self.vault.write_node("user/preferences", "hand written, must survive")
        _write_legacy(self.src, memory="imported note")
        migrate_legacy_memory(source_dir=self.src, vault=self.vault, dry_run=False)
        preserved = self.vault.read_node("user/preferences")
        self.assertIsNotNone(preserved)
        self.assertEqual(preserved.content, "hand written, must survive")

    def test_near_duplicate_of_existing_node_is_skipped(self):
        self.vault.write_node("user/preferences", "Prefers Hinglish replies in chat", category="user")
        _write_legacy(self.src, user="Prefers Hinglish replies in the chat")
        report = migrate_legacy_memory(source_dir=self.src, vault=self.vault, dry_run=False)
        self.assertEqual(report["created"], 0)
        self.assertTrue(any("duplicate" in s["reason"] for s in report["plan"]["skipped"]))

    def test_plan_reports_without_side_effects(self):
        _write_legacy(self.src, memory="planned note")
        plan = plan_migration(source_dir=self.src, vault=self.vault)
        self.assertEqual(len(plan["to_create"]), 1)
        self.assertEqual(plan["found_files"], ["MEMORY.md"])
        self.assertEqual(self.vault.list_all_nodes(), [])

    def test_duplicate_entries_within_one_file_are_imported_once(self):
        _write_legacy(self.src, memory=f"same text{ENTRY_DELIMITER}same text")
        report = migrate_legacy_memory(source_dir=self.src, vault=self.vault, dry_run=False)
        self.assertEqual(report["created"], 1)
        self.assertEqual(len(self.vault.list_all_nodes()), 1)


if __name__ == "__main__":
    unittest.main()
