"""The vault: one Markdown file per memory, under ``$PULSE_HOME/brain/``.

This module owns ALL reads, writes and deletes of brain memory. Nothing else may write to
the vault directory — a single writer is what keeps the store consistent (see specs/brain.md
§8.1 for why this rule exists: a previous attempt shipped two competing graph/storage
implementations and lost the user's data).

Guarantees:
- Every write is atomic (tmp file + ``os.replace``) — a crash mid-write cannot corrupt a node.
- Cross-process writes are serialised by a lock file, so the live agent and a UI action
  cannot interleave a read-modify-write.
- A malformed or unreadable node never raises out of the vault: it is reported as missing,
  so a single bad file cannot disable memory (the failure mode that broke the last attempt).
- Nothing in here deletes memory except an explicit ``delete_node`` call.
"""

from __future__ import annotations

import logging
import os
import re
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional

from . import decay as decay_mod
from .models import BrainNode, Frontmatter, NodeCategory, NodeStatus
from .parser import (
    compose_markdown,
    extract_wikilinks,
    normalize_node_id,
    parse_frontmatter_and_body,
    rewrite_link_target,
    slugify,
)

logger = logging.getLogger(__name__)

_VAULT_ENV = "PULSE_BRAIN_DIR"
LOCK_TIMEOUT_SECONDS = 10.0
_STEM_RE = re.compile(r"^[A-Za-z0-9._\- ]+$")

# Fallback used only when pulse_constants cannot be imported (keeps this package
# dependency-free); the real resolver is pulse_constants.get_pulse_home().
def get_brain_vault_dir() -> Path:
    """Canonical vault location: ``$PULSE_HOME/brain`` (profile-scoped).

    Honours ``PULSE_BRAIN_DIR`` for tests and for pointing a profile at an existing vault.
    """
    override = os.environ.get(_VAULT_ENV)
    if override:
        return Path(override).expanduser()
    try:
        from pulse_constants import get_pulse_home  # lazy: keeps this module import-trivial

        return Path(get_pulse_home()) / "brain"
    except Exception:  # pragma: no cover - only hit if pulse_constants is unavailable
        home = os.environ.get("PULSE_HOME")
        base = Path(home).expanduser() if home else Path.home() / ".pulse"
        return base / "brain"


# ── locking ────────────────────────────────────────────────────────────────

_PROCESS_LOCKS: Dict[str, threading.RLock] = {}
_PROCESS_LOCKS_GUARD = threading.Lock()


def _process_lock(path: Path) -> threading.RLock:
    key = str(path)
    with _PROCESS_LOCKS_GUARD:
        lock = _PROCESS_LOCKS.get(key)
        if lock is None:
            lock = _PROCESS_LOCKS[key] = threading.RLock()
        return lock


@contextmanager
def _vault_lock(vault_dir: Path, timeout: float = LOCK_TIMEOUT_SECONDS) -> Iterator[None]:
    """Serialise vault mutation across threads and processes.

    Uses ``fcntl.flock`` where available (macOS/Linux) and degrades to an in-process
    lock elsewhere — never blocks forever, and never raises on unlock failure.
    """
    vault_dir.mkdir(parents=True, exist_ok=True)
    lock_path = vault_dir / ".brain.lock"
    with _process_lock(lock_path):
        handle = None
        try:
            handle = open(lock_path, "a+")
        except OSError:
            handle = None
        if handle is not None:
            try:
                import fcntl  # POSIX only

                deadline = time.monotonic() + timeout
                while True:
                    try:
                        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                        break
                    except OSError:
                        if time.monotonic() >= deadline:
                            logger.warning("brain vault lock timed out for %s", vault_dir)
                            break
                        time.sleep(0.05)
            except ImportError:
                pass  # non-POSIX: the in-process lock above is the only guard
        try:
            yield
        finally:
            if handle is not None:
                try:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                except Exception:
                    pass
                try:
                    handle.close()
                except OSError:
                    pass


# ── vault ──────────────────────────────────────────────────────────────────

class BrainVault:
    """Filesystem-backed memory vault. One instance per vault directory."""

    def __init__(self, vault_dir: Optional[Path] = None) -> None:
        self.vault_dir = Path(vault_dir) if vault_dir is not None else get_brain_vault_dir()
        self._ensured = False

    # -- structure ---------------------------------------------------------

    def ensure_vault_structure(self) -> None:
        """Create the category folders and a minimal ``.obsidian/`` config.

        Idempotent, and safe to call on an existing vault (never overwrites a user file).
        """
        if self._ensured and self.vault_dir.exists():
            return
        self.vault_dir.mkdir(parents=True, exist_ok=True)
        for category in NodeCategory.all_values():
            (self.vault_dir / category).mkdir(exist_ok=True)
        self._write_obsidian_config()
        self._ensured = True

    def _write_obsidian_config(self) -> None:
        """Keep the vault openable in real Obsidian without clobbering user settings."""
        obsidian = self.vault_dir / ".obsidian"
        try:
            obsidian.mkdir(exist_ok=True)
        except OSError:
            return
        defaults = {
            "app.json": (
                '{\n'
                '  "alwaysUpdateLinks": true,\n'
                '  "newLinkFormat": "shortest",\n'
                '  "useMarkdownLinks": false,\n'
                '  "attachmentFolderPath": "attachments"\n'
                '}\n'
            ),
            "graph.json": (
                '{\n'
                '  "collapse-filter": false,\n'
                '  "showTags": true,\n'
                '  "showAttachments": false,\n'
                '  "showOrphans": true,\n'
                '  "showArrow": true,\n'
                '  "repelStrength": 12,\n'
                '  "linkDistance": 220,\n'
                '  "nodeSizeMultiplier": 1.2,\n'
                '  "lineSizeMultiplier": 0.8\n'
                '}\n'
            ),
        }
        for name, body in defaults.items():
            target = obsidian / name
            if target.exists():
                continue
            try:
                self._atomic_write(target, body)
            except OSError:
                pass

    # -- paths -------------------------------------------------------------

    def node_path(self, node_id: str) -> Path:
        """Absolute path for a node id. Rejects anything that could escape the vault."""
        normalized = normalize_node_id(node_id)
        if not normalized:
            raise ValueError("empty node id")
        parts = normalized.split("/")
        if any(p in ("..", ".") or p.startswith(".") for p in parts):
            raise ValueError(f"unsafe node id: {node_id!r}")
        if len(parts) == 1:
            parts = [NodeCategory.CONCEPT.value, parts[0]]
        return self.vault_dir.joinpath(*parts).with_suffix(".md")

    def _atomic_write(self, path: Path, text: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".tmp_{path.name}.{os.getpid()}.{threading.get_ident()}")
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)

    # -- read --------------------------------------------------------------

    def exists(self, node_id: str) -> bool:
        try:
            return self.node_path(node_id).is_file()
        except ValueError:
            return False

    def read_node(self, node_id: str) -> Optional[BrainNode]:
        """Load one node. Returns ``None`` for missing/unreadable/malformed files."""
        try:
            path = self.node_path(node_id)
        except ValueError:
            return None
        if not path.is_file():
            return None
        try:
            raw = path.read_text(encoding="utf-8-sig")
        except (OSError, UnicodeDecodeError) as exc:
            logger.warning("brain node unreadable: %s (%s)", path, exc)
            return None
        return self._node_from_text(normalize_node_id(node_id), path, raw)

    def _node_from_text(self, node_id: str, path: Path, raw: str) -> BrainNode:
        meta_raw, body = parse_frontmatter_and_body(raw)
        frontmatter = Frontmatter.from_dict(meta_raw, node_id=node_id)
        return BrainNode(
            id=node_id,
            path=path,
            frontmatter=frontmatter,
            content=body.strip(),
            raw_markdown=raw,
            wikilinks=extract_wikilinks(body),
        )

    def list_all_nodes(self) -> List[BrainNode]:
        """Every node in the vault, sorted by id. Unreadable files are skipped, not fatal."""
        return [node for _, node in self.iter_nodes()]

    def iter_nodes(self) -> List[tuple]:
        """``[(relative_id, BrainNode), ...]`` — the id comes from the relative path."""
        self.ensure_vault_structure()
        found: List[tuple] = []
        try:
            paths = sorted(self.vault_dir.rglob("*.md"))
        except OSError:
            return found
        for md_path in paths:
            rel = md_path.relative_to(self.vault_dir).as_posix()
            if rel.startswith(".") or "/." in rel:
                continue
            node_id = normalize_node_id(rel)
            if not node_id:
                continue
            try:
                raw = md_path.read_text(encoding="utf-8-sig")
            except (OSError, UnicodeDecodeError) as exc:
                logger.warning("brain node unreadable: %s (%s)", md_path, exc)
                continue
            found.append((node_id, self._node_from_text(node_id, md_path, raw)))
        return found

    def all_node_ids(self) -> List[str]:
        return [node_id for node_id, _ in self.iter_nodes()]

    # -- write -------------------------------------------------------------

    def write_node(
        self,
        node_id: str,
        content: str,
        *,
        title: Optional[str] = None,
        category: Optional[str] = None,
        tags: Optional[Iterable[str]] = None,
        confidence: Optional[float] = None,
        status: Optional[str] = None,
        salience: Optional[float] = None,
        supersedes: Optional[Iterable[str]] = None,
        aliases: Optional[Iterable[str]] = None,
        related: Optional[Iterable[str]] = None,
        relations: Optional[Iterable[str]] = None,
        source_turn: Optional[str] = None,
        extra: Optional[Dict[str, Any]] = None,
        now: Optional[float] = None,
        mark_access: bool = False,
    ) -> BrainNode:
        """Create or update a node.

        Preserves memory metadata across edits: ``created_at``, ``access_count`` and
        ``stability`` survive a content update, because rewriting a note must not reset how
        well PULSE knows it. Only an explicit recall (``mark_access=True``) strengthens it.
        """
        normalized = normalize_node_id(node_id)
        if not normalized:
            raise ValueError("empty node id")
        path = self.node_path(normalized)
        ts = int(now if now is not None else time.time())

        with _vault_lock(self.vault_dir):
            previous = self.read_node(normalized)
            prior = previous.frontmatter if previous else Frontmatter()

            fm = Frontmatter.from_dict(prior.to_dict(), node_id=normalized)
            fm.title = title or fm.title or normalized.split("/")[-1].replace("-", " ")
            # Path is authoritative: a node at user/preferences is a 'user' node even when the
            # caller passes no explicit category. Previously the prior/default category won, so
            # every node written without an explicit category was labelled 'concept'.
            fm.category = NodeCategory.coerce(category or normalized.split("/")[0] or fm.category).value
            if tags is not None:
                fm.tags = [str(t).strip() for t in tags if str(t).strip()]
            if confidence is not None:
                fm.confidence = float(confidence)
            if status is not None:
                fm.status = str(status)
            if salience is not None:
                fm.salience = float(salience)
            if supersedes is not None:
                fm.supersedes = [normalize_node_id(s) for s in supersedes if str(s).strip()]
            if aliases is not None:
                fm.aliases = [str(a).strip() for a in aliases if str(a).strip()]
            if related is not None:
                fm.related = [normalize_node_id(r) for r in related if str(r).strip()]
            if relations is not None:
                # Typed edges are authoritative when provided: store the tokens and keep the
                # denormalized ``related`` list in sync so legacy readers (index, learning_graph)
                # see the same targets.
                from .relations import parse_relations

                parsed = parse_relations([str(r) for r in relations])
                fm.relations = [rel.to_token() for rel in parsed]
                derived_targets = [normalize_node_id(rel.target) for rel in parsed if rel.target]
                merged_related = list(dict.fromkeys([*(fm.related or []), *derived_targets]))
                fm.related = [str(r) for r in merged_related if r]
            if source_turn is not None:
                fm.source_turn = str(source_turn)
            if extra:
                fm.extra.update({str(k): v for k, v in extra.items()})

            fm.touch_created(now=ts)
            if mark_access:
                fm.access_count = int(fm.access_count) + 1
                fm.last_accessed = ts
                fm.stability = decay_mod.on_recall(fm.stability, fm.access_count, fm.last_accessed, now=ts)
            else:
                fm.last_accessed = fm.last_accessed or ts

            text = compose_markdown(fm.to_dict(include_empty=False), content)
            self._atomic_write(path, text)

        node = self._node_from_text(normalized, path, text)
        self._emit_write_events(node, previous)
        return node

    def _emit_write_events(self, node: Any, previous: Any) -> None:
        """Publish the real events one write produced (§16). Called outside the vault lock.

        A write is either the birth of a node (``MEMORY_CREATED``) or a rewrite of one that
        already existed (``MEMORY_UPDATED``) — the graph needs to tell those apart, because a
        rewrite must move an existing node, not spawn a twin. Links in the body become
        relationship events, so an edge appears in the graph exactly when the wikilink that
        justifies it is written.
        """
        try:
            from .events import (
                MEMORY_CREATED,
                MEMORY_UPDATED,
                RELATIONSHIP_CREATED,
                emit_brain_event,
            )

            kind = MEMORY_UPDATED if previous is not None else MEMORY_CREATED
            emit_brain_event(
                kind,
                node.id,
                category=node.frontmatter.category,
                title=node.frontmatter.title,
                status=node.frontmatter.status,
                salience=round(float(node.frontmatter.salience or 0.0), 4),
            )
            for link in node.wikilinks or []:
                target = getattr(link, "target", None) or str(link)
                emit_brain_event(RELATIONSHIP_CREATED, node.id, target=target, rel="wikilink")
        except Exception:
            logger.debug("brain write event emit skipped", exc_info=True)

    def record_access(
        self,
        node_ids: Iterable[str],
        *,
        now: Optional[float] = None,
        activation: Optional[Dict[str, float]] = None,
    ) -> int:
        """Reconsolidation: strengthen recalled nodes (spacing effect). Returns count updated.

        ``activation`` carries the real spreading-activation value per node from the recall
        that triggered this call, so the graph can render actual cognitive state (§25) rather
        than a decorative pulse. A node that had gone dormant (retrievability decayed) emits
        MEMORY_REACTIVATED; any other recalled node emits MEMORY_ACTIVATED.
        """
        ts = int(now if now is not None else time.time())
        updated = 0
        act_map = activation or {}
        for node_id in node_ids:
            node = self.read_node(node_id)
            if node is None:
                continue
            fm = node.frontmatter
            was_dormant = int(fm.access_count or 0) > 0 and decay_mod.is_dormant(
                float(fm.stability or 0.0), fm.last_accessed, now=ts
            )
            fm.access_count = int(fm.access_count) + 1
            fm.last_accessed = ts
            fm.updated_at = fm.updated_at or ts
            fm.stability = decay_mod.on_recall(fm.stability, fm.access_count, fm.last_accessed, now=ts)
            text = compose_markdown(fm.to_dict(include_empty=False), node.content)
            with _vault_lock(self.vault_dir):
                self._atomic_write(node.path, text)
            updated += 1
            try:
                from .events import (
                    MEMORY_ACTIVATED,
                    MEMORY_REACTIVATED,
                    MEMORY_REINFORCED,
                    emit_brain_event,
                )

                # Transient activation for the live graph (§25): the memory was really retrieved.
                emit_brain_event(
                    MEMORY_REACTIVATED if was_dormant else MEMORY_ACTIVATED,
                    node_id,
                    activation=round(float(act_map.get(node_id, 0.0)), 4),
                    dormancy_break=was_dormant,
                )
                emit_brain_event(
                    MEMORY_REINFORCED,
                    node_id,
                    access_count=fm.access_count,
                    stability=round(float(fm.stability or 0.0), 4),
                )
            except Exception:
                logger.debug("reinforce event emit skipped", exc_info=True)
        return updated

    def revise_attribute(
        self,
        node_id: str,
        new_content: str,
        *,
        reason: str = "",
        source_turn: Optional[str] = None,
        now: Optional[float] = None,
    ) -> bool:
        """Correct one node's content in place, keeping the prior assertion as history (§3 A).

        This is reconsolidation, not supersession: the event is still true, so the node keeps its
        identity, edges and memory metadata — only the wrong attribute is rewritten. The previous
        text is appended to a ``corrections`` provenance log in frontmatter so the history is never
        lost, and a ``MEMORY_UPDATED`` event tells the graph to refresh the existing node rather
        than spawn a twin.
        """
        ts = int(now if now is not None else time.time())
        node = self.read_node(node_id)
        if node is None:
            return False
        prior_content = (node.content or "").strip()
        if prior_content == str(new_content or "").strip():
            return False
        fm = node.frontmatter
        history = list(fm.extra.get("corrections") or [])
        # Encoded with encode_record, like every other list-of-records field (a belief's revisions,
        # a procedure's outcomes, this node's corroborations). Frontmatter here round-trips scalars
        # and lists of scalars but *not* nested mappings, so appending a raw dict wrote its Python
        # repr: the prior assertion reached the file but could never be read back as structured
        # history, which is the same as losing it.
        from .parser import encode_record

        history.append(encode_record({
            "at": ts,
            "from": prior_content,
            "reason": reason or "temporal correction",
        }))
        fm.extra["corrections"] = history
        if source_turn is not None:
            fm.source_turn = str(source_turn)
        fm.updated_at = ts
        text = compose_markdown(fm.to_dict(include_empty=False), new_content)
        with _vault_lock(self.vault_dir):
            self._atomic_write(node.path, text)
        try:
            from .events import MEMORY_UPDATED, emit_brain_event

            emit_brain_event(MEMORY_UPDATED, node_id, reason="corrected")
        except Exception:
            logger.debug("revise event emit skipped", exc_info=True)
        return True

    def record_corroboration(
        self,
        node_id: str,
        *,
        text: str = "",
        source_turn: Optional[str] = None,
        now: Optional[float] = None,
        limit: int = 50,
    ) -> bool:
        """Record that a memory was asserted *again* — corroborating evidence, not a new memory.

        A restated claim should not become a second node (that is duplicate proliferation), but it
        is not nothing either: it is a second independent assertion of the same thing, which is
        exactly what lets reflection hold a belief instead of a provisional guess. Before this, the
        repeat was detected as a near-duplicate and discarded along with the recurrence signal it
        carried, which made a corroborated belief unreachable from real conversation.

        Returns ``True`` when a corroboration was added. The record is encoded with
        :func:`~agent.brain.parser.encode_record`, because frontmatter here does not round-trip
        nested mappings.
        """
        from .parser import encode_record

        node = self.read_node(node_id)
        if node is None:
            return False
        ts = int(now if now is not None else time.time())
        fm = node.frontmatter
        history = list(fm.extra.get("corroborations") or [])
        record = encode_record({
            "at": ts,
            "text": str(text or "")[:400],
            "turn": str(source_turn or ""),
        })
        if record in history:
            return False                     # the same assertion, replayed — a no-op
        history.append(record)
        fm.extra["corroborations"] = history[-max(1, int(limit)):]
        fm.updated_at = ts
        text_body = node.content or ""
        composed = compose_markdown(fm.to_dict(include_empty=False), text_body)
        with _vault_lock(self.vault_dir):
            self._atomic_write(node.path, composed)
        return True

    def corroborations_of(self, node: Any) -> List[Dict[str, Any]]:
        """Decoded corroboration records for a node (``[]`` when there are none)."""
        from .parser import decode_record

        try:
            raw = node.frontmatter.extra.get("corroborations") or []
        except Exception:
            return []
        out: List[Dict[str, Any]] = []
        for item in raw:
            try:
                decoded = decode_record(item) if isinstance(item, str) else item
            except Exception:
                continue
            if isinstance(decoded, dict):
                out.append(decoded)
        return out

    def corrections_of(self, node: Any) -> List[Dict[str, Any]]:
        """Decoded in-place correction history for a node, oldest first (``[]`` when there is none).

        The *previous* text is kept, not merely the fact that something changed, so "what did this
        claim before, and why was it changed" stays answerable after a temporal correction.
        """
        from .parser import decode_record

        try:
            raw = node.frontmatter.extra.get("corrections") or []
        except Exception:
            return []
        out: List[Dict[str, Any]] = []
        for item in raw:
            try:
                decoded = decode_record(item) if isinstance(item, str) else item
            except Exception:
                decoded = {}
            if not isinstance(decoded, dict) or not decoded:
                # Entries written before this used encode_record were stored as the repr of a
                # dict, which decode_record cannot read. Recover the history rather than report
                # that none exists.
                try:
                    import ast

                    legacy = ast.literal_eval(str(item))
                except Exception:
                    legacy = {}
                if isinstance(legacy, dict):
                    decoded = legacy
            if isinstance(decoded, dict) and decoded:
                out.append(decoded)
        return out

    def supersede(self, old_id: str, new_id: str, *, now: Optional[float] = None) -> bool:
        """Mark ``old_id`` as replaced by ``new_id`` — the correction path.

        The old node stays in the vault (history is never destroyed) but is excluded from
        active recall and flagged in the graph.
        """
        ts = int(now if now is not None else time.time())
        node = self.read_node(old_id)
        if node is None:
            return False
        fm = node.frontmatter
        fm.status = NodeStatus.SUPERSEDED.value
        fm.superseded_by = normalize_node_id(new_id)
        fm.updated_at = ts
        text = compose_markdown(fm.to_dict(include_empty=False), node.content)
        with _vault_lock(self.vault_dir):
            self._atomic_write(node.path, text)
        try:
            from .events import MEMORY_SUPERSEDED, emit_brain_event

            emit_brain_event(MEMORY_SUPERSEDED, old_id, superseded_by=fm.superseded_by)
        except Exception:
            logger.debug("supersede event emit skipped", exc_info=True)
        return True

    # -- delete / rename ---------------------------------------------------

    def delete_node(self, node_id: str) -> bool:
        """Delete one node. Only ever called for an explicit user/system-requested removal."""
        try:
            path = self.node_path(node_id)
        except ValueError:
            return False
        with _vault_lock(self.vault_dir):
            if not path.is_file():
                return False
            try:
                path.unlink()
            except OSError as exc:
                logger.warning("brain node delete failed: %s (%s)", path, exc)
                return False
        try:
            from .events import MEMORY_DELETED, emit_brain_event

            emit_brain_event(MEMORY_DELETED, node_id)
        except Exception:
            logger.debug("delete event emit skipped", exc_info=True)
        return True

    def rename_node(self, old_id: str, new_id: str, *, now: Optional[float] = None) -> Dict[str, Any]:
        """Move a node and update every ``[[wikilink]]`` pointing at it, vault-wide.

        Returns ``{ok, message, moved, links_updated}``.
        """
        old_norm, new_norm = normalize_node_id(old_id), normalize_node_id(new_id)
        if not old_norm or not new_norm:
            return {"ok": False, "message": "rename needs both old and new node ids", "links_updated": 0}
        if old_norm == new_norm:
            return {"ok": True, "message": "no change", "links_updated": 0}
        node = self.read_node(old_norm)
        if node is None:
            return {"ok": False, "message": f"node '{old_norm}' not found", "links_updated": 0}
        if self.exists(new_norm):
            return {"ok": False, "message": f"node '{new_norm}' already exists", "links_updated": 0}

        ts = int(now if now is not None else time.time())
        old_path, new_path = self.node_path(old_norm), self.node_path(new_norm)
        fm = node.frontmatter
        fm.updated_at = ts
        text = compose_markdown(fm.to_dict(include_empty=False), node.content)

        # Collect every link *spelling* that resolves to the old node, before the move.
        # A rename must update [[old-name]], [[concept/old-name]] and [[alias]] alike — the
        # way Obsidian does — not just links whose text happens to equal the node id.
        from .index import BrainIndex  # lazy import: index imports this module

        index = BrainIndex(self).rebuild()
        spellings = set()
        for _, candidate in self.iter_nodes():
            for link in candidate.wikilinks:
                if index.resolve(link.target) == old_norm:
                    spellings.add(link.target)
        spellings.add(old_norm)
        spellings.add(old_norm.split("/")[-1])
        spellings.discard("")
        spellings.discard(new_norm)

        links_updated = 0
        with _vault_lock(self.vault_dir):
            new_path.parent.mkdir(parents=True, exist_ok=True)
            self._atomic_write(new_path, text)
            if old_path.is_file():
                try:
                    old_path.unlink()
                except OSError as exc:
                    logger.warning("brain rename could not remove %s (%s)", old_path, exc)
            for other_id, other in self.iter_nodes():
                body = other.raw_markdown
                total = 0
                for spelling in sorted(spellings):
                    # Preserve the link style: a bare [[name]] stays bare after the rename.
                    replacement = new_norm if "/" in spelling else new_norm.split("/")[-1]
                    body, count = rewrite_link_target(body, spelling, replacement)
                    total += count
                if total:
                    self._atomic_write(other.path, body)
                    links_updated += total
        return {
            "ok": True,
            "message": f"renamed '{old_norm}' -> '{new_norm}'",
            "links_updated": links_updated,
        }

    # -- helpers -----------------------------------------------------------

    def unique_node_id(self, category: str, base: str) -> str:
        """A free node id inside ``category`` derived from ``base`` (adds -2, -3, ...)."""
        cat = NodeCategory.coerce(category).value
        stem = slugify(base)
        candidate = f"{cat}/{stem}"
        if not self.exists(candidate):
            return candidate
        for n in range(2, 500):
            candidate = f"{cat}/{stem}-{n}"
            if not self.exists(candidate):
                return candidate
        return f"{cat}/{stem}-{int(time.time())}"

    def stats(self) -> Dict[str, Any]:
        nodes = self.list_all_nodes()
        by_category: Dict[str, int] = {}
        for node in nodes:
            by_category[node.category] = by_category.get(node.category, 0) + 1
        return {
            "vault_dir": str(self.vault_dir),
            "nodes": len(nodes),
            "by_category": by_category,
            "links": sum(len(n.link_targets()) for n in nodes),
        }
