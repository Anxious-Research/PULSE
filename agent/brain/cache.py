"""
§9 SQLite Cache Layer — Hybrid Architecture (brain.md v3)

Markdown vault remains authoritative (human-readable, Obsidian-compatible).
SQLite accelerates queries: fast graph load, FTS5 content search, and
SQL-side spreading activation for recall.

The cache is ALWAYS rebuildable from the vault — the .md files are the source
of truth, the .db is a disposable accelerator. Deleting .brain-cache.db is safe.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .vault import BrainVault

# Bump when the schema changes so an old cache is rebuilt, not mis-read.
SCHEMA_VERSION = 1


class BrainCache:
    """SQLite query accelerator over a BrainVault.

    Responsibilities (§9):
      • O(1) graph load — one SQL query instead of parsing every .md on open
      • Incremental sync — only reindex files whose mtime advanced
      • FTS5 content search — fast cue matching for recall
      • Spreading-activation recall — 1-hop join, ranked by match score
      • Optional embedding store — flag-gated, off by default

    Every public method degrades gracefully: if the cache is unavailable the
    caller falls back to the vault. A cache miss is never a hard failure.
    """

    def __init__(self, vault: BrainVault, embeddings_enabled: bool = False):
        self.vault = vault
        self.embeddings_enabled = embeddings_enabled
        self.db_path = vault.vault_dir / ".brain-cache.db"
        self.conn: Optional[sqlite3.Connection] = self._connect()
        self._ensure_schema()

    # -- lifecycle ----------------------------------------------------------

    def _connect(self) -> Optional[sqlite3.Connection]:
        try:
            conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
            conn.row_factory = sqlite3.Row
            return conn
        except sqlite3.Error:
            return None

    def _ensure_schema(self) -> None:
        if not self.conn:
            return

        # Version gate: an older/incompatible cache is dropped and rebuilt rather
        # than mis-read. The vault is authoritative, so this is always safe.
        stored = self._get_meta("schema_version")
        if stored is not None and stored != str(SCHEMA_VERSION):
            self.conn.executescript(
                "DROP TABLE IF EXISTS nodes; DROP TABLE IF EXISTS edges;"
                "DROP TABLE IF EXISTS nodes_fts; DROP TABLE IF EXISTS meta;"
            )
            self.conn.commit()

        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS nodes (
                node_id     TEXT PRIMARY KEY,
                category    TEXT NOT NULL,
                title       TEXT NOT NULL,
                content     TEXT NOT NULL,
                hash        TEXT NOT NULL,
                mtime       REAL NOT NULL,
                frontmatter TEXT NOT NULL,
                embedding   BLOB
            );

            CREATE TABLE IF NOT EXISTS edges (
                source TEXT NOT NULL,
                target TEXT NOT NULL,
                kind   TEXT NOT NULL,
                PRIMARY KEY (source, target, kind)
            );

            CREATE TABLE IF NOT EXISTS meta (
                key   TEXT PRIMARY KEY,
                value TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_nodes_category ON nodes(category);
            CREATE INDEX IF NOT EXISTS idx_nodes_mtime    ON nodes(mtime);
            CREATE INDEX IF NOT EXISTS idx_edges_source   ON edges(source);
            CREATE INDEX IF NOT EXISTS idx_edges_target   ON edges(target);

            -- Standalone FTS5 (not external-content): a plain table whose rows we
            -- own, so DELETE + reinsert works without the contentless dance.
            CREATE VIRTUAL TABLE IF NOT EXISTS nodes_fts USING fts5(
                node_id UNINDEXED,
                title,
                content
            );
            """
        )
        self.conn.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES ('schema_version', ?)",
            (str(SCHEMA_VERSION),),
        )
        self.conn.commit()

    def close(self) -> None:
        if self.conn:
            self.conn.close()
            self.conn = None

    # -- staleness ----------------------------------------------------------

    def is_empty(self) -> bool:
        if not self.conn:
            return True
        try:
            row = self.conn.execute("SELECT COUNT(*) AS n FROM nodes").fetchone()
            return (row["n"] if row else 0) == 0
        except sqlite3.Error:
            return True

    def is_stale(self) -> bool:
        """True when any vault .md is newer than the last sync watermark."""
        if not self.conn or self.is_empty():
            return True
        last = self._get_meta("last_sync")
        if last is None:
            return True
        try:
            watermark = float(last)
        except (TypeError, ValueError):
            return True
        try:
            for md_path in self.vault.vault_dir.rglob("*.md"):
                if md_path.stat().st_mtime > watermark:
                    return True
        except OSError:
            return True
        return False

    def _get_meta(self, key: str) -> Optional[str]:
        if not self.conn:
            return None
        try:
            row = self.conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
            return row["value"] if row else None
        except sqlite3.Error:
            return None

    # -- sync ---------------------------------------------------------------

    def rebuild(self) -> int:
        """Full rescan: drop everything and reindex every vault node.

        Returns the number of nodes indexed. Safe to call any time — the vault
        is authoritative, so a rebuild can never lose data.
        """
        if not self.conn:
            return 0
        self.conn.execute("DELETE FROM nodes")
        self.conn.execute("DELETE FROM edges")
        self.conn.execute("DELETE FROM nodes_fts")
        self.conn.commit()

        count = 0
        for node_id, node in self.vault.iter_nodes():
            self._upsert(node_id, node)
            count += 1
        self._set_watermark()
        return count

    def sync(self) -> int:
        """Incremental sync — only files newer than the watermark.

        O(changed_files), not O(vault_size). Falls back to a full rebuild when
        the cache is empty (first run).
        """
        if not self.conn:
            return 0
        if self.is_empty():
            return self.rebuild()

        last = self._get_meta("last_sync")
        try:
            watermark = float(last) if last else 0.0
        except (TypeError, ValueError):
            watermark = 0.0

        try:
            paths = sorted(self.vault.vault_dir.rglob("*.md"))
        except OSError:
            return 0

        changed = 0
        for md_path in paths:
            try:
                if md_path.stat().st_mtime <= watermark:
                    continue
                raw = md_path.read_text(encoding="utf-8-sig")
            except (OSError, UnicodeDecodeError):
                continue
            rel = md_path.relative_to(self.vault.vault_dir).as_posix()
            if rel.startswith(".") or "/." in rel:
                continue
            from .parser import normalize_node_id

            node_id = normalize_node_id(rel)
            if not node_id:
                continue
            self._upsert(node_id, self.vault._node_from_text(node_id, md_path, raw))
            changed += 1

        if changed:
            self._set_watermark()
        return changed

    def ensure_fresh(self) -> int:
        """Sync if stale, else no-op. The call sites should use this on open."""
        return self.sync() if self.is_stale() else 0

    def _set_watermark(self) -> None:
        if not self.conn:
            return
        self.conn.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES ('last_sync', ?)",
            (str(time.time()),),
        )
        self.conn.commit()

    # -- write-through ------------------------------------------------------

    def _upsert(self, node_id: str, node: Any) -> None:
        if not self.conn:
            return
        content = node.content or ""
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        fm = node.frontmatter.to_dict() if node.frontmatter else {}
        try:
            mtime = Path(node.path).stat().st_mtime
        except OSError:
            mtime = time.time()

        self.conn.execute(
            """
            INSERT OR REPLACE INTO nodes
            (node_id, category, title, content, hash, mtime, frontmatter, embedding)
            VALUES (?, ?, ?, ?, ?, ?, ?, NULL)
            """,
            (node_id, node.category, node.title, content, digest, mtime, json.dumps(fm)),
        )
        # FTS5 (external-content): delete the old row, then reinsert.
        self.conn.execute("DELETE FROM nodes_fts WHERE node_id = ?", (node_id,))
        self.conn.execute(
            "INSERT INTO nodes_fts (node_id, title, content) VALUES (?, ?, ?)",
            (node_id, node.title, content),
        )

        # Wikilink edges (raw target — resolution is the index's job).
        self.conn.execute("DELETE FROM edges WHERE source = ?", (node_id,))
        for link in node.wikilinks or []:
            target = getattr(link, "target", None)
            if target:
                self.conn.execute(
                    "INSERT OR IGNORE INTO edges (source, target, kind) VALUES (?, ?, 'wikilink')",
                    (node_id, target),
                )

        # supersedes is a real, typed edge — surface it so the graph can draw it.
        for target in (node.frontmatter.supersedes if node.frontmatter else None) or []:
            self.conn.execute(
                "INSERT OR IGNORE INTO edges (source, target, kind) VALUES (?, ?, 'supersedes')",
                (node_id, target),
            )
        self.conn.commit()

    def update_node(self, node_id: str) -> None:
        """Reindex a single node after a write (write-through hook)."""
        if not self.conn:
            return
        node = self.vault.read_node(node_id)
        if node is None:
            # Deleted on disk — drop it from the cache too.
            self.conn.execute("DELETE FROM nodes WHERE node_id = ?", (node_id,))
            self.conn.execute("DELETE FROM edges WHERE source = ?", (node_id,))
            self.conn.execute("DELETE FROM nodes_fts WHERE node_id = ?", (node_id,))
            self.conn.commit()
            return
        self._upsert(node_id, node)
        self._set_watermark()

    # -- read ---------------------------------------------------------------

    def all_nodes(self) -> List[Dict[str, Any]]:
        """Every node as a dict — the fast graph-load path (§9)."""
        if not self.conn:
            return []
        try:
            rows = self.conn.execute(
                "SELECT node_id, category, title, content, frontmatter FROM nodes"
            ).fetchall()
        except sqlite3.Error:
            return []
        return [
            {
                "id": r["node_id"],
                "category": r["category"],
                "title": r["title"],
                "content": r["content"],
                "frontmatter": json.loads(r["frontmatter"] or "{}"),
            }
            for r in rows
        ]

    def all_edges(self) -> List[Tuple[str, str, str]]:
        if not self.conn:
            return []
        try:
            rows = self.conn.execute("SELECT source, target, kind FROM edges").fetchall()
        except sqlite3.Error:
            return []
        return [(r["source"], r["target"], r["kind"]) for r in rows]

    # -- recall -------------------------------------------------------------

    def recall(self, cues: List[str], limit: int = 8) -> List[Dict[str, Any]]:
        """SQL-side spreading activation recall (§3.4 fast path).

        1. FTS5 MATCH on the cue terms (the direct hits)
        2. One hop over wikilink edges for the associative neighbourhood
        3. Rank by summed activation, top-k out

        This is the same shape as recall.py's spread, pushed into SQL so a
        1000-node vault answers in one query instead of a Python walk.
        """
        if not self.conn or not cues:
            return []
        match = self._fts_query(cues)
        if not match:
            return []

        try:
            rows = self.conn.execute(
                """
                WITH direct AS (
                    SELECT nodes_fts.node_id AS id, bm25(nodes_fts) AS bm
                    FROM nodes_fts
                    WHERE nodes_fts MATCH ?
                ),
                hop AS (
                    SELECT e.target AS id FROM edges e
                    JOIN direct d ON d.id = e.source
                    WHERE e.kind = 'wikilink'
                ),
                act AS (
                    SELECT id, -bm AS score FROM direct
                    UNION ALL
                    SELECT id, 0.5 AS score FROM hop
                )
                SELECT n.node_id AS id, n.category, n.title, n.content, n.frontmatter,
                       SUM(act.score) AS score
                FROM act
                JOIN nodes n ON n.node_id = act.id
                GROUP BY n.node_id
                ORDER BY score DESC
                LIMIT ?
                """,
                (match, limit),
            ).fetchall()
        except sqlite3.Error:
            return []

        return [
            {
                "id": r["id"],
                "category": r["category"],
                "title": r["title"],
                "content": r["content"],
                "frontmatter": json.loads(r["frontmatter"] or "{}"),
                "score": float(r["score"] or 0.0),
            }
            for r in rows
        ]

    @staticmethod
    def _fts_query(cues: List[str]) -> str:
        """Build a safe FTS5 OR query from cue terms.

        Quoted literals prevent FTS5 syntax errors from punctuation in cues; an
        empty/whitespace term is dropped rather than producing a bare `OR`.
        """
        terms = []
        for cue in cues[:8]:
            t = str(cue).strip().replace('"', "")
            if t:
                terms.append(f'"{t}"')
        return " OR ".join(terms)

    def search(self, query: str, limit: int = 20) -> List[Dict[str, Any]]:
        """Full-text search over title + content (FTS5)."""
        if not self.conn or not query.strip():
            return []
        try:
            rows = self.conn.execute(
                """
                SELECT n.node_id AS id, n.category, n.title, n.content, n.frontmatter
                FROM nodes_fts
                JOIN nodes n ON n.node_id = nodes_fts.node_id
                WHERE nodes_fts MATCH ?
                ORDER BY bm25(nodes_fts)
                LIMIT ?
                """,
                (self._fts_query([query]), limit),
            ).fetchall()
        except sqlite3.Error:
            return []
        return [
            {
                "id": r["id"],
                "category": r["category"],
                "title": r["title"],
                "content": r["content"],
                "frontmatter": json.loads(r["frontmatter"] or "{}"),
            }
            for r in rows
        ]

    # -- embeddings (opt-in §9.4) -------------------------------------------

    def store_embedding(self, node_id: str, vector: bytes) -> None:
        if not self.conn or not self.embeddings_enabled:
            return
        self.conn.execute("UPDATE nodes SET embedding = ? WHERE node_id = ?", (vector, node_id))
        self.conn.commit()

    def embedded_nodes(self) -> List[Tuple[str, bytes]]:
        """(node_id, embedding) pairs for a caller-side similarity pass."""
        if not self.conn or not self.embeddings_enabled:
            return []
        rows = self.conn.execute(
            "SELECT node_id, embedding FROM nodes WHERE embedding IS NOT NULL"
        ).fetchall()
        return [(r["node_id"], r["embedding"]) for r in rows]
