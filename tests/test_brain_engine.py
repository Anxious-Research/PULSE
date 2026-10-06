"""Unit and integration tests for PULSE Native Brain & Cognitive Knowledge Graph."""

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.brain.models import BeliefStatus, BrainNode, FrontmatterMetadata, NodeCategory, WikilinkTarget
from agent.brain.parser import parse_frontmatter_and_body, parse_inline_tags, parse_wikilinks, serialize_note
from agent.brain.vault import BrainVault, normalize_node_id
from agent.brain.graph import BrainGraph
from agent.brain.belief import BeliefEngine
from tools.brain_tool import brain_tool


class TestBrainEngine(unittest.TestCase):

    def setUp(self):
        self.temp_dir = Path(tempfile.mkdtemp(prefix="pulse_test_brain_"))
        self.vault = BrainVault(self.temp_dir)
        self.vault.ensure_vault_structure()

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_wikilink_parser(self):
        text = """
        Here is a reference to [[self/identity]] and another to [[User Preferences|prefs]].
        Also check [[concepts/python-async#Event Loop|async loop]] and [[self/identity]] again.
        """
        links = parse_wikilinks(text)
        self.assertEqual(len(links), 3)

        targets = {l.target: l for l in links}
        self.assertIn("self/identity", targets)
        self.assertIsNone(targets["self/identity"].alias)

        self.assertIn("User Preferences", targets)
        self.assertEqual(targets["User Preferences"].alias, "prefs")

        self.assertIn("concepts/python-async", targets)
        self.assertEqual(targets["concepts/python-async"].heading, "Event Loop")
        self.assertEqual(targets["concepts/python-async"].alias, "async loop")

    def test_frontmatter_parser_and_serialization(self):
        raw_md = """---
title: "Custom Note"
category: "concept"
tags: ["ai", "agents"]
confidence: 0.85
status: "active"
aliases: ["Agentic AI"]
related: ["self/identity"]
---

# Heading
This is content with #inline_tag and a link to [[self/identity]].
"""
        meta, body = parse_frontmatter_and_body(raw_md)
        self.assertEqual(meta.title, "Custom Note")
        self.assertEqual(meta.category, "concept")
        self.assertIn("ai", meta.tags)
        self.assertIn("agents", meta.tags)
        self.assertIn("inline_tag", meta.tags)
        self.assertEqual(meta.confidence, 0.85)
        self.assertEqual(meta.status, "active")
        self.assertIn("Agentic AI", meta.aliases)
        self.assertIn("self/identity", meta.related)
        self.assertIn("# Heading", body)

        # Test serialization
        serialized = serialize_note(meta, body)
        self.assertIn("title: Custom Note", serialized)
        self.assertIn("# Heading", serialized)

        # Re-parse serialized output
        meta2, body2 = parse_frontmatter_and_body(serialized)
        self.assertEqual(meta2.title, meta.title)
        self.assertEqual(meta2.confidence, meta.confidence)

    def test_vault_crud(self):
        # Verify self-knowledge seeded
        identity_node = self.vault.read_node("self/identity")
        self.assertIsNotNone(identity_node)
        self.assertIn("PULSE", identity_node.title)

        # Write a new concept node
        created = self.vault.write_node(
            node_id="concept/graph-algorithms",
            content="Graph traversal with [[self/identity]] connections.",
            title="Graph Algorithms",
            category="concept",
            tags=["math", "cs"],
            confidence=0.95,
        )
        self.assertEqual(created.id, "concept/graph-algorithms")
        self.assertEqual(created.frontmatter.title, "Graph Algorithms")
        self.assertEqual(len(created.wikilinks), 1)
        self.assertEqual(created.wikilinks[0].target, "self/identity")

        # Read back
        read_node = self.vault.read_node("concept/graph-algorithms")
        self.assertIsNotNone(read_node)
        self.assertEqual(read_node.title, "Graph Algorithms")

        # Patch node
        patched = self.vault.patch_node(
            node_id="concept/graph-algorithms",
            old_string="Graph traversal",
            new_string="Advanced graph traversal",
        )
        self.assertIsNotNone(patched)
        self.assertIn("Advanced graph traversal", patched.content)

        # Delete node
        self.assertTrue(self.vault.delete_node("concept/graph-algorithms"))
        self.assertIsNone(self.vault.read_node("concept/graph-algorithms"))

    def test_brain_graph_and_backlinks(self):
        self.vault.write_node(
            node_id="concept/neural-networks",
            content="Base concept of deep learning.",
            title="Neural Networks",
            category="concept",
            tags=["ai"],
        )

        self.vault.write_node(
            node_id="concept/transformers",
            content="Transformers build on [[concept/neural-networks]] and [[self/identity]].",
            title="Transformers Architecture",
            category="concept",
            tags=["ai", "nlp"],
        )

        graph = BrainGraph(self.vault)
        graph.rebuild_index()

        # Check node A backlinks
        nn_node = graph.get_node("concept/neural-networks")
        self.assertIsNotNone(nn_node)
        self.assertIn("concept/transformers", nn_node.backlinks)

        # Check explore neighbors
        subgraph = graph.get_neighbors("concept/neural-networks", depth=1)
        node_ids = {n["id"] for n in subgraph["nodes"]}
        self.assertIn("concept/neural-networks", node_ids)
        self.assertIn("concept/transformers", node_ids)

        # Search nodes
        search_res = graph.search_nodes(query="transformers", category="concept")
        self.assertGreaterEqual(len(search_res), 1)
        self.assertEqual(search_res[0]["id"], "concept/transformers")

        # Full payload
        payload = graph.get_full_graph_payload()
        self.assertGreaterEqual(payload["stats"]["total_nodes"], 3)
        self.assertGreaterEqual(payload["stats"]["total_edges"], 2)

    def test_belief_engine_and_misconception_resolution(self):
        belief_engine = BeliefEngine(self.vault)

        # 1. Record an initial hypothesis
        old_belief = belief_engine.record_belief(
            title="Python Global Interpreter Lock Limit",
            content="Python cannot do true multi-threading due to the GIL.",
            confidence=0.8,
            tags=["python", "concurrency"],
        )
        self.assertEqual(old_belief.frontmatter.status, BeliefStatus.ACTIVE.value)

        # 2. Evolve belief when new information arrives
        new_belief, updated_old = belief_engine.evolve_belief(
            old_belief_id=old_belief.id,
            new_title="Python Free-Threaded Concurrency (PEP 703)",
            new_content="Python 3.13+ supports free-threaded execution without GIL.",
            reason="Python 3.13 introduced experimental nogil build (PEP 703)",
            new_confidence=1.0,
        )

        self.assertEqual(new_belief.frontmatter.status, BeliefStatus.ACTIVE.value)
        self.assertEqual(new_belief.frontmatter.supersedes, old_belief.id)

        self.assertIsNotNone(updated_old)
        self.assertEqual(updated_old.frontmatter.status, BeliefStatus.SUPERSEDED.value)
        self.assertIn("Superseded Belief", updated_old.content)
        self.assertIn(new_belief.id, updated_old.frontmatter.related)

        # 3. List active beliefs
        active_beliefs = belief_engine.list_active_beliefs()
        active_ids = {b.id for b in active_beliefs}
        self.assertIn(new_belief.id, active_ids)
        self.assertNotIn(old_belief.id, active_ids)

    def test_brain_tool_actions(self):
        # Test write via tool
        res_write = brain_tool(
            action="write",
            node_id="user/preferences",
            title="User Preferences",
            category="user",
            content="Prefers clean code, strict tests, and native architecture.",
            tags=["preferences", "user-profile"],
            confidence=1.0,
            vault=self.vault,
        )
        self.assertTrue(res_write["success"])

        # Test read via tool
        res_read = brain_tool(
            action="read",
            node_id="user/preferences",
            vault=self.vault,
        )
        self.assertTrue(res_read["found"])
        self.assertIn("User Preferences", res_read["node"]["title"])

        # Test search via tool
        res_search = brain_tool(
            action="search",
            query="preferences",
            category="user",
            vault=self.vault,
        )
        self.assertGreaterEqual(res_search["total_matches"], 1)

        # Test graph_stats via tool
        res_stats = brain_tool(
            action="graph_stats",
            vault=self.vault,
        )
        self.assertGreaterEqual(res_stats["stats"]["total_nodes"], 3)


if __name__ == "__main__":
    unittest.main()
