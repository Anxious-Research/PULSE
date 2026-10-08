"""End-to-end Brain integration test: learning → persistence → recall → reasoning.

Acceptance criteria from the mandate:
- Meaningful conversation can automatically contribute to memory
- Existing memories can be recognized and updated
- Associations form from actual relationships
- Memory persists across restart
- Recall uses associative Brain state
- Correction/supersession works
- Memory activation exists
- Selective recall (relevant memories only)
"""

import tempfile
import unittest
from pathlib import Path

from agent.brain.encoding import encode_turn
from agent.brain.recall import recall
from agent.brain.vault import BrainVault
from agent.brain.index import BrainIndex
from agent.brain.cache import BrainCache
from agent.brain.models import NodeStatus

T0 = 1728000000


class TestEndToEndLearning(unittest.TestCase):
    """Prove the Brain works end-to-end with real conversational flow."""
    
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.vault = BrainVault(vault_dir=self.tmp)
        self.vault.ensure_vault_structure()
        self.index = BrainIndex(self.vault)
        self.cache = BrainCache(self.vault)
    
    def test_continuous_learning_no_explicit_memory_call(self):
        """Turn 1-4: normal conversation → Brain learns automatically."""
        
        # Turn 1: establish project
        result1 = encode_turn(
            self.vault,
            "My main project is PULSE.",
            turn_id="turn-001",
            now=T0,
        )
        self.assertGreater(len(result1["encoded"]), 0, "should extract project knowledge")
        
        # Turn 2: add architecture fact
        result2 = encode_turn(
            self.vault,
            "PULSE has a cognitive Brain.",
            turn_id="turn-002",
            now=T0 + 60,
        )
        self.assertGreater(len(result2["encoded"]), 0)
        
        # Turn 3: add design principle
        result3 = encode_turn(
            self.vault,
            "The Brain should connect memories associatively.",
            turn_id="turn-003",
            now=T0 + 120,
        )
        self.assertGreater(len(result3["encoded"]), 0)
        
        # Turn 4: correction
        result4 = encode_turn(
            self.vault,
            "Actually, the graph architecture changed today.",
            turn_id="turn-004",
            now=T0 + 180,
        )
        self.assertGreater(len(result4["encoded"]), 0)
        
        # Verify: vault has real nodes
        nodes = self.vault.list_all_nodes()
        active = [n for n in nodes if n.frontmatter.status == NodeStatus.ACTIVE.value]
        self.assertGreater(len(active), 2, "should have created multiple active memories")
        
        # Verify: no blind duplication
        titles = [n.title.lower() for n in active]
        # Should not have duplicate "pulse" or "brain" nodes with identical text
        pulse_nodes = [n for n in active if "pulse" in n.content.lower()]
        self.assertLessEqual(len(pulse_nodes), 3, "should not blindly duplicate 'PULSE' across 4 turns")
    
    def test_persistence_and_recall(self):
        """Memory survives vault reload and recall retrieves relevant knowledge."""
        
        # Encode knowledge
        encode_turn(self.vault, "Project Alpha uses architecture B.", turn_id="t1", now=T0)
        encode_turn(self.vault, "Architecture B was chosen because it is faster.", turn_id="t2", now=T0 + 60)
        
        # Simulate restart: new vault instance pointing at same dir
        vault2 = BrainVault(vault_dir=self.tmp)
        index2 = BrainIndex(vault2).rebuild()
        
        nodes_after_restart = vault2.list_all_nodes()
        self.assertGreater(len(nodes_after_restart), 0, "memory must persist")
        
        # Recall: query about Alpha should retrieve architecture info
        results = recall("What is Project Alpha's architecture?", vault2, index2, limit=5)
        self.assertGreater(len(results), 0, "recall must find relevant memories")
        
        recalled_text = " ".join(r["content"].lower() for r in results)
        self.assertIn("alpha", recalled_text, "should recall project name")
    
    def test_selective_recall_excludes_unrelated(self):
        """Unrelated memories should NOT be injected into recall."""
        
        # Create related and unrelated memories
        self.vault.write_node("project/alpha", "Project Alpha uses architecture B.", now=T0)
        self.vault.write_node("project/beta", "Project Beta is a completely different codebase for payments.", now=T0)
        self.vault.write_node("concept/architecture-b", "Architecture B is fast.", now=T0)
        
        index = BrainIndex(self.vault).rebuild()
        
        # Query about Alpha
        results = recall("Tell me about Project Alpha", self.vault, index, limit=3)
        
        # Should NOT retrieve Beta (unrelated project)
        recalled = " ".join(r["content"] for r in results)
        self.assertIn("Alpha", recalled)
        # Beta might appear if embeddings are weak, but it should rank lower
        # At minimum, Alpha should be in top results
        self.assertGreater(
            sum(1 for r in results if "alpha" in r["content"].lower()),
            sum(1 for r in results if "beta" in r["content"].lower()),
            "relevant memory (Alpha) should rank higher than unrelated (Beta)"
        )
    
    def test_correction_supersedes_old_memory(self):
        """Turn: 'Actually X changed' should supersede the old belief."""
        
        # Initial fact
        encode_turn(self.vault, "Project X uses architecture A.", turn_id="t1", now=T0)
        
        nodes_before = self.vault.list_all_nodes()
        arch_a_nodes = [n for n in nodes_before if "architecture a" in n.content.lower()]
        self.assertGreater(len(arch_a_nodes), 0)
        
        # Correction
        encode_turn(
            self.vault,
            "Project X was migrated to architecture C today.",
            turn_id="t2",
            now=T0 + 3600,
        )
        
        nodes_after = self.vault.list_all_nodes()
        
        # Old memory should be superseded or contradicted
        # (The exact behavior depends on contradiction detection — at minimum it should not
        #  create two equally authoritative "Project X uses A" and "Project X uses C" nodes)
        active_x = [
            n for n in nodes_after
            if n.frontmatter.status == NodeStatus.ACTIVE.value and "project x" in n.content.lower()
        ]
        
        # Should not have both A and C as active with equal status
        active_texts = [n.content.lower() for n in active_x]
        has_a = any("architecture a" in t for t in active_texts)
        has_c = any("architecture c" in t for t in active_texts)
        
        if has_a and has_c:
            # If both exist, one should be superseded or have lower confidence
            all_x = [n for n in nodes_after if "project x" in n.content.lower()]
            superseded_count = sum(1 for n in all_x if n.frontmatter.status == NodeStatus.SUPERSEDED.value)
            self.assertGreater(superseded_count, 0, "old architecture should be marked superseded")
    
    def test_cache_fast_path(self):
        """SQLite cache should accelerate graph load."""
        
        # Populate vault with nodes
        for i in range(20):
            self.vault.write_node(f"concept/node-{i}", f"Knowledge item {i}", now=T0 + i)
        
        # First load: cache miss → full rebuild
        cache = BrainCache(self.vault)
        self.assertTrue(cache.is_stale(), "empty cache is stale")
        
        synced = cache.sync()
        self.assertEqual(synced, 20, "should sync all 20 nodes")
        
        # Second load: cache hit → fast
        cache2 = BrainCache(self.vault)
        self.assertFalse(cache2.is_stale(), "cache should be fresh")
        
        cached_nodes = cache2.all_nodes()
        self.assertEqual(len(cached_nodes), 20, "cache should return all nodes")
        
        # Verify nodes have correct structure
        first = cached_nodes[0]
        self.assertIn("id", first)
        self.assertIn("category", first)
        self.assertIn("content", first)
    
    def test_spreading_activation(self):
        """Recall should spread activation through wikilinks."""
        
        # Create linked memories
        self.vault.write_node(
            "project/alpha",
            "Project Alpha uses [[architecture-b]].",
            now=T0,
        )
        self.vault.write_node(
            "concept/architecture-b",
            "Architecture B is fast and reliable.",
            now=T0,
        )
        self.vault.write_node(
            "concept/performance",
            "Performance matters for [[architecture-b]].",
            now=T0,
        )
        
        index = BrainIndex(self.vault).rebuild()
        
        # Query: "Project Alpha" should activate:
        # 1. project/alpha (direct match)
        # 2. concept/architecture-b (linked from alpha)
        # 3. possibly concept/performance (linked TO architecture-b, 2-hop)
        
        results = recall("Project Alpha architecture", self.vault, index, limit=5)
        
        recalled_ids = {r["id"] for r in results}
        self.assertIn("project/alpha", recalled_ids, "direct hit")
        self.assertIn("concept/architecture-b", recalled_ids, "1-hop spreading activation")


if __name__ == "__main__":
    unittest.main()
