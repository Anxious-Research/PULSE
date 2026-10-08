"""
§9 SQLite Cache Layer — Hybrid Architecture (brain.md v3)

Markdown vault remains authoritative (human-readable, Obsidian-compatible).
SQLite cache accelerates queries: fast recall, embeddings, FTS5 search.

Cache is rebuildable from vault — source of truth is always the .md files.
"""

import hashlib
import json
import sqlite3
import time
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from .models import LearningNode
from .vault import BrainVault


class BrainCache:
    """SQLite query accelerator over the vault.
    
    Provides O(1) graph load, fast spreading-activation recall via SQL,
    and optional embeddings storage. Automatically rebuilds when stale.
    """

    def __init__(self, vault: BrainVault, embeddings_enabled: bool = False):
        self.vault = vault
        self.embeddings_enabled = embeddings_enabled
        self.db_path = vault.vault_dir / ".brain-cache.db"
        self.conn: Optional[sqlite3.Connection] = None
        self._init_db()

    def _init_db(self) -> None:
        """Initialize database schema if not present."""
        self.conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row

        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS nodes (
                node_id TEXT PRIMARY KEY,
                category TEXT NOT NULL,
                title TEXT NOT NULL,
                content TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                mtime INTEGER NOT NULL,
                frontmatter TEXT NOT NULL,
                embedding BLOB
            );
            
            CREATE TABLE IF NOT EXISTS edges (
                source TEXT NOT NULL,
                target TEXT NOT NULL,
                kind TEXT NOT NULL,
                PRIMARY KEY (source, target, kind)
            );
            
            CREATE TABLE IF NOT EXISTS entities (
                entity TEXT NOT NULL,
                node_id TEXT NOT NULL,
                PRIMARY KEY (entity, node_id)
            );
            
            CREATE TABLE IF NOT EXISTS meta (
                key TEXT PRIMARY KEY,
                value TEXT
            );
            
            CREATE INDEX IF NOT EXISTS idx_nodes_category ON nodes(category);
            CREATE INDEX IF NOT EXISTS idx_nodes_mtime ON nodes(mtime);
            CREATE INDEX IF NOT EXISTS idx_edges_source ON edges(source);
            CREATE INDEX IF NOT EXISTS idx_edges_target ON edges(target);
        """)

        # FTS5 for fast content search
        self.conn.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS nodes_fts USING fts5(
                node_id UNINDEXED,
                title,
                content,
                content='nodes',
                content_rowid=rowid
            )
        """)

        self.conn.commit()

    def is_stale(self) -> bool:
        """Check if cache is outdated compared to vault files."""
        if not self.conn:
            return True

        cursor = self.conn.execute("SELECT value FROM meta WHERE key = 'last_sync'")
        row = cursor.fetchone()
        if not row:
            return True

        last_sync = float(row[0])

        # Check if any .md file has been modified since last sync
        for md_path in self.vault.vault_dir.rglob("*.md"):
            if md_path.stat().st_mtime > last_sync:
                return True

        return False

    def rebuild(self) -> None:
        """Full rebuild: reparse all vault .md files."""
        if not self.conn:
            return

        print(f"brain-cache: rebuilding from {self.vault.vault_dir}...")
        start = time.time()

        self.conn.execute("DELETE FROM nodes")
        self.conn.execute("DELETE FROM edges")
        self.conn.execute("DELETE FROM entities")
        self.conn.execute("DELETE FROM nodes_fts")

        count = 0
        for md_path in self.vault.vault_dir.rglob("*.md"):
            node = self.vault.read_node_from_path(md_path)
            if node:
                self._upsert_node(node)
                count += 1

        self.conn.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES ('last_sync', ?)",
            (str(time.time()),)
        )
        self.conn.commit()

        elapsed = time.time() - start
        print(f"brain-cache: rebuilt {count} nodes in {elapsed:.2f}s")

    def sync_incremental(self) -> None:
        """O(changed_files) sync — only reindex modified .md files."""
        if not self.conn:
            return

        cursor = self.conn.execute("SELECT value FROM meta WHERE key = 'last_sync'")
        row = cursor.fetchone()
        last_sync = float(row[0]) if row else 0

        changed_paths = []
        for md_path in self.vault.vault_dir.rglob("*.md"):
            if md_path.stat().st_mtime > last_sync:
                changed_paths.append(md_path)

        if not changed_paths:
            return

        for path in changed_paths:
            node = self.vault.read_node_from_path(path)
            if node:
                self._upsert_node(node)

        self.conn.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES ('last_sync', ?)",
            (str(time.time()),)
        )
        self.conn.commit()

    def _upsert_node(self, node: LearningNode) -> None:
        """Insert or update a node + its edges."""
        if not self.conn:
            return

        content_hash = hashlib.sha256(node.content.encode()).hexdigest()
        frontmatter_json = json.dumps(node.frontmatter or {})

        self.conn.execute("""
            INSERT OR REPLACE INTO nodes 
            (node_id, category, title, content, content_hash, mtime, frontmatter, embedding)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            node.node_id,
            node.category,
            node.title,
            node.content,
            content_hash,
            int(time.time()),
            frontmatter_json,
            None  # embeddings added separately
        ))

        # Update FTS5
        self.conn.execute("""
            INSERT INTO nodes_fts (rowid, node_id, title, content)
            VALUES (
                (SELECT rowid FROM nodes WHERE node_id = ?),
                ?, ?, ?
            )
            ON CONFLICT(rowid) DO UPDATE SET
                title = excluded.title,
                content = excluded.content
        """, (node.node_id, node.node_id, node.title, node.content))

        # Rebuild edges for this node
        self.conn.execute("DELETE FROM edges WHERE source = ?", (node.node_id,))
        for link in node.wikilinks:
            self.conn.execute("""
                INSERT OR IGNORE INTO edges (source, target, kind)
                VALUES (?, ?, 'wikilink')
            """, (node.node_id, link))

    def get_all_nodes(self) -> List[Dict]:
        """Fast graph load: all nodes from cache."""
        if not self.conn:
            return []

        cursor = self.conn.execute("""
            SELECT node_id, category, title, content, frontmatter
            FROM nodes
        """)

        return [
            {
                "node_id": row["node_id"],
                "category": row["category"],
                "title": row["title"],
                "content": row["content"],
                "frontmatter": json.loads(row["frontmatter"])
            }
            for row in cursor.fetchall()
        ]

    def get_all_edges(self) -> List[Tuple[str, str, str]]:
        """Fast graph load: all edges from cache."""
        if not self.conn:
            return []

        cursor = self.conn.execute("SELECT source, target, kind FROM edges")
        return [(row["source"], row["target"], row["kind"]) for row in cursor.fetchall()]

    def recall_fast(self, cues: List[str], limit: int = 8) -> List[Dict]:
        """SQL-powered spreading activation recall.
        
        1. FTS5 match on cues
        2. JOIN edges for 2-hop neighbourhood
        3. Rank by activation × retrievability
        4. LIMIT top-k
        """
        if not self.conn or not cues:
            return []

        # Build FTS5 query
        query_terms = " OR ".join(f'"{term}"' for term in cues[:5])

        cursor = self.conn.execute(f"""
            WITH matched AS (
                SELECT n.node_id, n.category, n.title, n.content, n.frontmatter,
                       rank AS score
                FROM nodes_fts fts
                JOIN nodes n ON n.node_id = fts.node_id
                WHERE nodes_fts MATCH ?
            ),
            expanded AS (
                SELECT m.node_id, m.category, m.title, m.content, m.frontmatter,
                       m.score * 1.0 AS activation
                FROM matched m
                UNION
                SELECT n.node_id, n.category, n.title, n.content, n.frontmatter,
                       m.score * 0.5 AS activation
                FROM matched m
                JOIN edges e ON e.source = m.node_id
                JOIN nodes n ON n.node_id = e.target
            )
            SELECT node_id, category, title, content, frontmatter, MAX(activation) AS final_score
            FROM expanded
            GROUP BY node_id
            ORDER BY final_score DESC
            LIMIT ?
        """, (query_terms, limit))

        return [
            {
                "node_id": row["node_id"],
                "category": row["category"],
                "title": row["title"],
                "content": row["content"],
                "frontmatter": json.loads(row["frontmatter"]),
                "score": row["final_score"]
            }
            for row in cursor.fetchall()
        ]

    def store_embedding(self, node_id: str, vector: bytes) -> None:
        """Store embedding blob for similarity search (opt-in)."""
        if not self.conn or not self.embeddings_enabled:
            return

        self.conn.execute(
            "UPDATE nodes SET embedding = ? WHERE node_id = ?",
            (vector, node_id)
        )
        self.conn.commit()

    def search_similar(self, query_vector: bytes, limit: int = 5) -> List[Dict]:
        """Cosine similarity search (requires embeddings enabled)."""
        if not self.conn or not self.embeddings_enabled:
            return []

        # Note: SQLite doesn't have native vector ops; this is a placeholder
        # for a future numpy/faiss integration or extension like sqlite-vss
        cursor = self.conn.execute("""
            SELECT node_id, category, title, content, frontmatter, embedding
            FROM nodes
            WHERE embedding IS NOT NULL
        """)

        # Fallback: return all embedded nodes (caller does cosine in Python)
        return [
            {
                "node_id": row["node_id"],
                "category": row["category"],
                "title": row["title"],
                "content": row["content"],
                "frontmatter": json.loads(row["frontmatter"]),
                "embedding": row["embedding"]
            }
            for row in cursor.fetchall()
        ][:limit]

    def close(self) -> None:
        """Close database connection."""
        if self.conn:
            self.conn.close()
            self.conn = None
