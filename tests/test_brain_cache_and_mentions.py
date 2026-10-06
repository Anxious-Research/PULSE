"""Tests for Obsidian-Grade MetadataCache, Link Resolution, and Unlinked Mentions."""

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.brain.vault import BrainVault
from agent.brain.cache import MetadataCache
from agent.brain.parser import parse_linktext


class TestBrainCacheAndMentions(unittest.TestCase):

    def setUp(self):
        self.temp_dir = Path(tempfile.mkdtemp(prefix="pulse_test_cache_"))
        self.vault = BrainVault(self.temp_dir)
        self.vault.ensure_vault_structure()

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_parse_linktext(self):
        path, subpath = parse_linktext("Architecture#Core Loop")
        self.assertEqual(path, "Architecture")
        self.assertEqual(subpath, "#Core Loop")

        path2, subpath2 = parse_linktext("Tools#^block999")
        self.assertEqual(path2, "Tools")
        self.assertEqual(subpath2, "#^block999")

        path3, subpath3 = parse_linktext("Simple Note")
        self.assertEqual(path3, "Simple Note")
        self.assertIsNone(subpath3)

    def test_resolved_links_and_backlinks(self):
        self.vault.write_node(
            node_id="concept/python",
            content="Python is an interpreted language.",
            title="Python",
            category="concept",
        )

        self.vault.write_node(
            node_id="concept/fastapi",
            content="FastAPI is a modern web framework for [[concept/python]].",
            title="FastAPI",
            category="concept",
        )

        cache = MetadataCache(self.vault)
        cache.build_cache()

        # Check resolved links matrix
        self.assertEqual(cache.resolved_links["concept/fastapi"]["concept/python"], 1)

        # Check reverse backlinks
        backlinks = cache.get_backlinks("concept/python")
        self.assertEqual(len(backlinks), 1)
        self.assertEqual(backlinks[0]["source_id"], "concept/fastapi")

    def test_unlinked_mentions_discovery(self):
        # Target note: "React" with alias "ReactJS"
        self.vault.write_node(
            node_id="concept/react",
            content="A library for web interfaces.",
            title="React",
            category="concept",
            aliases=["ReactJS"],
        )

        # Note with explicit link
        self.vault.write_node(
            node_id="concept/nextjs",
            content="Next.js is a full-stack framework built around [[concept/react]].",
            title="NextJS",
            category="concept",
        )

        # Note with UNLINKED mention of "React" and "ReactJS"
        self.vault.write_node(
            node_id="project/pulse-desktop",
            content="The desktop UI frontend is built using React components and ReactJS patterns.",
            title="Pulse Desktop Project",
            category="project",
        )

        cache = MetadataCache(self.vault)
        cache.build_cache()

        # Unlinked mentions for "concept/react":
        # Note "concept/nextjs" has an explicit wikilink, so it must NOT show as unlinked.
        # Note "project/pulse-desktop" mentions React without [[...]], so it MUST show as unlinked!
        mentions = cache.find_unlinked_mentions("concept/react")
        self.assertGreaterEqual(len(mentions), 1)

        source_ids = {m["source_id"] for m in mentions}
        self.assertIn("project/pulse-desktop", source_ids)
        self.assertNotIn("concept/nextjs", source_ids)


if __name__ == "__main__":
    unittest.main()
