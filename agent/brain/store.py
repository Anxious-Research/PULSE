"""Vault-backed memory store — the engine behind the ``memory`` tool (specs/brain.md §5).

The tool's contract does not move. Same actions (``add``/``replace``/``remove``/``batch``),
same targets (``memory``/``user``), same response shape, same approval pinning, same
"old_text locates a whole entry" rule. Only the engine underneath changes: an *entry* is a
vault node's body, a *target* is a folder, and the character budget is gone — nothing is
evicted to make room for anything (§1, §4: storage is unlimited, recall is what is bounded).

Three consequences worth stating plainly, because each one is a deliberate behaviour change
from the flat store:

1. **No budget rejections.** ``add`` cannot fail with "memory is full". The
   ``_consolidation_failure`` loop and its per-turn cap are kept only because a failed
   *match* is still possible (``replace``/``remove`` naming an entry that isn't there), and
   that must never loop a turn to exhaustion and suppress the user's reply.
2. **Nothing is deleted.** ``remove`` archives: the note leaves active memory and every
   reader of this store, while the file stays in the vault for history (§3.3, §12). The
   tool still reports ``Entry removed.`` because that is what the caller asked for and what
   the contract promises — the difference is on disk, not in the reply.
3. **``replace`` supersedes.** Rewriting an entry writes a new node and marks the old one
   ``superseded_by`` it, so the correction chain survives (§3.5). A correction is the one
   write that should always leave a trace.

Edit *matching* is not reimplemented here. Unique-match, ambiguity, stale-pin and the
threat scan are reused verbatim from ``tools.memory_tool_store`` — that reuse is what
"the contract does not change" actually means. They are imported lazily so ``agent.brain``
stays importable without ``tools/`` on the path.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from .encoding import category_for, title_for
from .models import NodeStatus
from .parser import slugify

logger = logging.getLogger("agent.brain.store")

# Same per-turn cap as the flat store: a fragile replace/remove that cannot find its entry
# gets a few retries, then a TERMINAL result, so the model stops looping memory calls and
# answers the user. A failed memory side effect must never block the turn's reply.
_MAX_CONSOLIDATION_FAILURES_PER_TURN = 3

# What each tool target reads. "memory" is everything PULSE curated about the world and
# itself; "user" is the person. ``daily/`` is deliberately absent — episodic notes are raw
# material for consolidation, not curated memory to echo back at the user.
_MEMORY_CATEGORIES: Tuple[str, ...] = ("self", "concept", "project", "belief")
_USER_CATEGORY = "user"
# Superseded and archived notes are history: visible in the graph, never served as current
# memory and never matched by a tool edit.
_VISIBLE_STATUSES = (NodeStatus.ACTIVE.value,)


def _flat() -> Any:
    """The flat store module, for the matching helpers whose semantics we keep."""
    from tools import memory_tool_store

    return memory_tool_store


class BrainStore:
    """``MemoryStore``-compatible facade over the brain vault.

    Constructed exactly like ``MemoryStore`` — ``agent_init`` and ``load_on_disk_store``
    pass char limits and enable flags — but the limits are accepted and ignored, and the
    flags still gate which targets are readable/writable.
    """

    _MAX_CONSOLIDATION_FAILURES_PER_TURN = _MAX_CONSOLIDATION_FAILURES_PER_TURN

    def __init__(
        self,
        memory_char_limit: Optional[int] = None,
        user_char_limit: Optional[int] = None,
        *,
        memory_enabled: bool = True,
        user_profile_enabled: bool = True,
        vault: Any = None,
    ) -> None:
        self.memory_enabled = memory_enabled
        self.user_profile_enabled = user_profile_enabled
        # Accepted for signature compatibility only; the vault has no budget (§1).
        self.memory_char_limit, self.user_char_limit = memory_char_limit, user_char_limit
        self._vault = vault
        self._consolidation_failures = 0
        self._legacy_checked = False

    # -- vault access -------------------------------------------------------

    @property
    def vault(self) -> Any:
        """The vault, created on demand. The write path owns directory creation; a store
        that cannot be built returns ``None`` and every method degrades to a clean error."""
        if self._vault is None:
            try:
                from .vault import BrainVault

                candidate = BrainVault()
                candidate.ensure_vault_structure()
            except Exception:
                logger.debug("brain store could not open a vault", exc_info=True)
                return None
            self._vault = candidate
        return self._vault

    # -- lifecycle ----------------------------------------------------------

    def load_from_disk(self) -> None:
        """Open the vault and import legacy flat memory once.

        Called by ``agent_init`` and by every agent-less surface (gateway, Desktop,
        ``/memory``) so all of them write to the same place. The import is additive and
        idempotent, and it never touches ``MEMORY.md``/``USER.md`` — a user who upgrades
        keeps every entry they had.
        """
        vault = self.vault
        if vault is None:
            return
        if not self._legacy_checked:
            self._legacy_checked = True
            try:
                from .session import migrate_legacy_once

                migrate_legacy_once(vault)
            except Exception:
                logger.debug("legacy memory import skipped", exc_info=True)

    # -- read surface -------------------------------------------------------

    def target_enabled(self, target: str) -> bool:
        return self.user_profile_enabled if target == "user" else self.memory_enabled

    def _nodes_for(self, target: str) -> List[Any]:
        """Active notes a target reads, oldest first so ids break ties deterministically.

        Ordering matters: ``_locate`` returns an index into this list, so an unstable order
        would make a staged write's pin resolve to a different entry on replay.
        """
        vault = self.vault
        if vault is None:
            return []
        try:
            nodes = vault.list_all_nodes()
        except Exception:
            logger.debug("vault listing failed", exc_info=True)
            return []
        wanted = (_USER_CATEGORY,) if target == "user" else _MEMORY_CATEGORIES
        live = [
            node
            for node in nodes
            if node.frontmatter.category in wanted
            and node.frontmatter.status in _VISIBLE_STATUSES
            and (node.content or "").strip()
        ]
        live.sort(key=lambda node: (node.timestamp or 0, node.id))
        return live

    def _entries_for(self, target: str) -> List[str]:
        return [(node.content or "").strip() for node in self._nodes_for(target)]

    @property
    def user_entries(self) -> List[str]:
        """User-profile entries, as the flat store exposed them. Read by the onboarding
        verifier to confirm a write landed — same read, different engine."""
        return self._entries_for("user")

    @property
    def memory_entries(self) -> List[str]:
        """Curated memory entries, as the flat store exposed them."""
        return self._entries_for("memory")

    def _usage(self, target: str) -> str:
        """Human-readable state, echoed to the model in errors. There is no budget to
        report, so it reports the count — and says so, since a bare number would read as
        a fraction of something."""
        count = len(self._entries_for(target))
        return f"{count} note{'s' if count != 1 else ''} (no limit)"

    def format_for_system_prompt(self, target: str) -> Optional[str]:
        """Always ``None`` — the vault does not render a standing prompt block here.

        This is deliberate, not an omission. The flat store injected MEMORY.md/USER.md as a
        frozen block on every turn; that is the design §1 replaces, and keeping it would put
        the same notes in the prompt twice once the brain's own layers are on. Identity and
        durable preferences reach the prompt through Layer 1 (``brain/prefix.py``) and
        everything else through Layer 2 per-turn recall — one source per block, both of them
        brain code. An empty vault and a full one both render nothing here, so this method
        can never change what the model sees.
        """
        return None

    # -- failure bookkeeping ------------------------------------------------

    def reset_consolidation_failures(self) -> None:
        """Call at turn start."""
        self._consolidation_failures = 0

    def _consolidation_failure(self, response: Dict[str, Any]) -> Dict[str, Any]:
        """Count a failed match: under the cap return ``response`` (it says how to retry);
        past it a TERMINAL result so the model stops looping memory calls."""
        self._consolidation_failures += 1
        if self._consolidation_failures <= self._MAX_CONSOLIDATION_FAILURES_PER_TURN:
            return response
        return {
            "success": False,
            "done": True,
            "error": (
                f"Memory consolidation failed {self._consolidation_failures} times this turn. "
                "Stop retrying memory calls — leave memory unchanged for now and continue with "
                "your reply to the user. The fact can be saved in a later turn."
            ),
        }

    # -- matching (same semantics as the flat store) ------------------------

    def _locate_entries(
        self, entries: List[str], old_text: str, verb: str, matched_entry: Optional[str] = None
    ) -> Any:
        """Index into *entries* that *old_text* selects, or the error dict the edit would
        return. A staged write carries the FULL entry it was reviewed against, so replay
        can only hit that exact entry."""
        flat = _flat()
        if matched_entry is not None:
            idx = flat._pinned_index(entries, matched_entry)
            return idx if idx is not None else flat._error(flat._stale_entry_message(matched_entry))
        idx, ambiguous = flat._find_unique_match(entries, old_text)
        if ambiguous:
            return flat._error(
                f"Multiple entries matched '{old_text}'. Be more specific.",
                matches=[e[:80] + ("..." if len(e) > 80 else "") for e in entries if old_text in e],
            )
        if idx is None:
            verb = "replace" if verb not in ("replace", "remove") else verb
            return self._consolidation_failure(
                flat._error(
                    f"No entry matched '{old_text}'. Check current_entries below and retry with "
                    f"the exact text of the entry you want to {verb}.",
                    current_entries=entries,
                )
            )
        return idx

    def _locate(
        self, target: str, old_text: str, verb: str, matched_entry: Optional[str] = None
    ) -> Any:
        return self._locate_entries(self._entries_for(target), old_text, verb, matched_entry)

    def resolve_entry(self, target: str, old_text: str, verb: str) -> Dict[str, Any]:
        """``{"success": True, "matched_entry": <full entry>}`` for the entry *old_text*
        selects now, or the error the direct edit would return."""
        if not self.target_enabled(target):
            return self._target_disabled(target)
        idx = self._locate(target, old_text.strip(), verb)
        if isinstance(idx, dict):
            return idx
        return {"success": True, "matched_entry": self._entries_for(target)[idx]}

    def resolve_batch_entries(self, target: str, operations: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Dry-run ``apply_batch``: the same scan, op walk and empty-store check, so it
        fails exactly where the direct batch would. On success ``{"success": True,
        "matched_entries": [...]}`` — per op, the full entry its replace/remove selects now
        (``None`` for add), in batch order."""
        return self._batch(target, operations, commit=False)

    # -- writes -------------------------------------------------------------

    def add(self, target: str, content: str) -> Dict[str, Any]:
        """Write a new note. Cannot fail for capacity — there is no limit (§1)."""
        flat = _flat()
        content = (content or "").strip()
        if not content:
            return flat._error("Content cannot be empty.")
        if scan_error := flat._scan_memory_content(content):
            return flat._error(scan_error)
        vault = self.vault
        if vault is None:
            return self._no_vault()
        if content in self._entries_for(target):
            return self._success(target, "Entry already exists (no duplicate added).")

        category = _USER_CATEGORY if target == "user" else category_for(content)
        title = title_for(content)
        try:
            node_id = vault.unique_node_id(category, slugify(title))
            vault.write_node(
                node_id,
                content,
                title=title,
                category=category,
                tags=["tool"],
                status=NodeStatus.ACTIVE.value,
            )
            superseded = self._supersede_contradicted(vault, content, exclude=node_id)
        except Exception:
            logger.debug("brain store add failed", exc_info=True)
            return flat._error("Could not write to the brain vault. Nothing was changed — retry.")
        return self._success(target, "Entry added.", superseded_nodes=superseded or None)

    def replace(
        self,
        target: str,
        old_text: str,
        new_content: str,
        matched_entry: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Supersede the entry containing *old_text* with *new_content*.

        ``old_text`` only locates the entry; the whole entry becomes ``new_content``. The
        old note is kept and marked ``superseded_by`` the new one (§3.5), so a correction is
        never a silent overwrite.
        """
        flat = _flat()
        new_content = (new_content or "").strip()
        if not old_text.strip():
            return flat._error("old_text cannot be empty.")
        if not new_content:
            return flat._error("new_content cannot be empty. Use 'remove' to delete entries.")
        if scan_error := flat._scan_memory_content(new_content):
            return flat._error(scan_error)
        return self._edit(target, old_text.strip(), new_content, matched_entry)

    def remove(self, target: str, old_text: str, matched_entry: Optional[str] = None) -> Dict[str, Any]:
        """Retire the entry containing *old_text*.

        Archives rather than deletes: it leaves memory and every reader of it, and the file
        stays in the vault (§3.3 explicit removal, §12 nothing destroyed).
        """
        flat = _flat()
        if not old_text.strip():
            return flat._error("old_text cannot be empty.")
        return self._edit(target, old_text.strip(), None, matched_entry)

    def _edit(
        self,
        target: str,
        old_text: str,
        new_content: Optional[str],
        matched_entry: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Locked replace (``new_content`` set) or archive (``None``) of the matched entry."""
        flat = _flat()
        vault = self.vault
        if vault is None:
            return self._no_vault()
        nodes = self._nodes_for(target)
        idx = self._locate(target, old_text, "replace" if new_content else "remove", matched_entry)
        if isinstance(idx, dict):
            return idx
        node = nodes[idx]
        previous = (node.content or "").strip()
        try:
            if new_content is None:
                node.frontmatter.status = NodeStatus.ARCHIVED.value
                vault.write_node(
                    node.id,
                    node.content,
                    title=node.frontmatter.title,
                    category=node.frontmatter.category,
                    tags=node.frontmatter.tags,
                    status=NodeStatus.ARCHIVED.value,
                )
                return self._success(target, "Entry removed.", removed_entry=previous)
            new_id = vault.unique_node_id(
                node.frontmatter.category, slugify(title_for(new_content))
            )
            vault.write_node(
                new_id,
                new_content,
                title=title_for(new_content),
                category=node.frontmatter.category,
                tags=node.frontmatter.tags or ["tool"],
                status=NodeStatus.ACTIVE.value,
                supersedes=[node.id],
            )
            vault.supersede(node.id, new_id)
            superseded = self._supersede_contradicted(vault, new_content, exclude=new_id)
        except Exception:
            logger.debug("brain store edit failed", exc_info=True)
            return flat._error("Could not update the brain vault. Nothing was changed — retry.")
        return self._success(
            target, "Entry replaced.", replaced_entry=previous, superseded_nodes=superseded or None
        )

    def apply_batch(self, target: str, operations: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Apply add/replace/remove ops. All-or-nothing: any malformed or unmatched op
        writes NOTHING and returns the first failure.

        "Nothing to make room for" is why the flat store's budget pass is gone, but the
        atomicity is not: a partially applied batch of edits is harder to reason about than
        either outcome, so each op is validated against a working copy before any of it is
        written.
        """
        return self._batch(target, operations, commit=True)

    def _batch(self, target: str, operations: List[Dict[str, Any]], *, commit: bool) -> Dict[str, Any]:
        flat = _flat()
        if not operations:
            return flat._error("operations list is empty.")
        if not self.target_enabled(target):
            return self._target_disabled(target)
        ops = [op or {} for op in operations]
        # Scan every add/replace content BEFORE touching the vault — one poisoned op
        # rejects the batch.
        for i, op in enumerate(ops):
            scan_error = (
                op.get("action") in {"add", "replace"}
                and op.get("content")
                and flat._scan_memory_content(op["content"])
            )
            if scan_error:
                return flat._error(f"Operation {i + 1}: {scan_error}")

        vault = self.vault
        if vault is None:
            return self._no_vault()
        nodes = list(self._nodes_for(target))
        working = [(node.content or "").strip() for node in nodes]
        matched: List[Optional[str]] = []
        applied: List[Tuple[Optional[str], Dict[str, Any]]] = []

        for i, op in enumerate(ops):
            act = op.get("action")
            content = (op.get("content") or op.get("new_text") or "").strip()
            old_text = (op.get("old_text") or "").strip()
            pos = f"Operation {i + 1} ({act or 'unknown'})"
            if act == "add":
                if not content:
                    return self._batch_failure(f"{pos}: content is required.")
                if content not in working:
                    working.append(content)
                matched.append(None)
                applied.append(("add", {"content": content, "target": target}))
                continue
            if act not in ("replace", "remove"):
                return self._batch_failure(f"{pos}: unknown action. Use add, replace, or remove.")
            if not old_text:
                return self._batch_failure(f"{pos}: old_text is required.")
            if act == "replace" and not content:
                return self._batch_failure(f"{pos}: content is required (use action='remove' to delete).")
            # Validate against the WORKING copy (so a replace can target an entry an
            # earlier op in this same batch added), then apply against the live vault.
            idx = self._locate_entries(working, old_text, act, op.get("matched_entry"))
            if isinstance(idx, dict):
                return self._batch_failure(f"{pos}: {idx.get('error', 'no match')}")
            previous = working[idx]
            working[idx : idx + 1] = [content] if act == "replace" else []
            matched.append(previous)
            applied.append((act, {"content": content, "old_text": old_text, "target": target, "index": idx}))

        if not commit:
            return {"success": True, "matched_entries": matched}

        # Re-resolve against the live node list as ops are applied, so an earlier op can
        # invalidate a later index — the working copy validated the *shape*, the real list
        # decides the *target*.
        replaced: Dict[int, str] = {}
        removed: Dict[int, str] = {}
        for i, (act, op) in enumerate(applied, 1):
            if act == "add":
                result = self.add(target, op["content"])
            elif act == "replace":
                result = self.replace(target, op["old_text"], op["content"])
            else:
                result = self.remove(target, op["old_text"])
            if not result.get("success"):
                return self._batch_failure(f"Operation {i}: {result.get('error', 'failed')}")
            previous = matched[i - 1]
            if previous is not None:
                (replaced if act == "replace" else removed)[i] = previous
        fields: Dict[str, Any] = {}
        if replaced:
            fields["replaced_entries"] = replaced
        if removed:
            fields["removed_entries"] = removed
        return self._success(target, f"Applied {len(ops)} operation(s).", **fields)

    # -- internals ----------------------------------------------------------

    def _supersede_contradicted(self, vault: Any, content: str, *, exclude: str = "") -> List[str]:
        """Supersede every active note the new text contradicts (§3.5).

        A correction the user states plainly ("no, it's X") must retire the claim it refutes,
        or recall keeps returning the superseded version. Runs on every write, so it needs a
        cheap topic-overlap prefilter; ``find_contradictions`` already applies one.
        """
        try:
            from .correction import find_contradictions

            candidates = [
                (node.id, (node.content or "").strip())
                for node in self._nodes_for("memory")
                if node.id != exclude and (node.content or "").strip()
            ]
            hits = find_contradictions(content, candidates)
        except Exception:
            logger.debug("contradiction check failed", exc_info=True)
            return []
        superseded: List[str] = []
        for old_id in hits:
            try:
                node = vault.read_node(old_id)
                if node is None or node.frontmatter.status != NodeStatus.ACTIVE.value:
                    continue
                new_id = vault.unique_node_id(node.frontmatter.category, slugify(title_for(content)))
                if not node.frontmatter.superseded_by and vault.supersede(old_id, new_id):
                    superseded.append(old_id)
            except Exception:
                logger.debug("supersede failed for %s", old_id, exc_info=True)
        return superseded

    def _success(self, target: str, message: str = "", **extra: Any) -> Dict[str, Any]:
        """TERMINAL and WITHOUT the entries list — echoing entries invites the model to
        "find more to fix" and re-issue the same ops. A successful write resets the
        per-turn failure budget."""
        fields = {k: v for k, v in extra.items() if v not in (None, [], {})}
        self._consolidation_failures = 0
        return {
            "success": True,
            "done": True,
            "target": target,
            "usage": self._usage(target),
            "entry_count": len(self._entries_for(target)),
            **({"message": message} if message else {}),
            **fields,
            "note": "Write saved. This update is complete — do not repeat it.",
        }

    def _batch_failure(self, message: str) -> Dict[str, Any]:
        return self._consolidation_failure(
            _flat()._error(message, current_entries=self._entries_for("memory"))
        )

    def _target_disabled(self, target: str) -> Dict[str, Any]:
        label = "user profile" if target == "user" else "memory"
        return {
            "success": False,
            "done": True,
            "error": f"The {label} store is disabled in config, so this write was not applied.",
        }

    def _char_limit(self, target: str) -> int:
        return 10_000_000  # effectively unbounded

    def _success_response(self, target: str, message: str = "", **extra: Any) -> Dict[str, Any]:
        return self._success(target, message, **extra)

    def _mutate(self, target: str, mutate_fn: Any, *, skip_drift: bool = False) -> Dict[str, Any]:
        """Apply a mutation closure `mutate_fn(entries, limit)` -> (new_entries, message) or error dict.

        Provided for compatibility with `agent.learning_mutations` and CLI memory editing.
        """
        if not self.target_enabled(target):
            return self._target_disabled(target)
        vault = self.vault
        if vault is None:
            return self._no_vault()

        entries = self._entries_for(target)
        nodes = self._nodes_for(target)
        limit = self._char_limit(target)

        result = mutate_fn(entries, limit)
        if isinstance(result, dict):
            return result
        if not isinstance(result, (tuple, list)) or len(result) < 2:
            return {"success": False, "error": "invalid mutation result"}

        new_entries = result[0]
        message = result[1]
        extra_fields = result[2] if len(result) > 2 else {}

        old_set = set(entries)
        new_set = set(new_entries)

        # Removed entries -> archive nodes
        for node in nodes:
            content = (node.content or "").strip()
            if content not in new_set:
                vault.delete_node(node.id)

        # Added entries -> create active nodes
        for entry in new_entries:
            if entry not in old_set:
                cat = _USER_CATEGORY if target == "user" else category_for(entry)
                node_id = vault.unique_node_id(cat, slugify(title_for(entry)))
                vault.write_node(
                    node_id,
                    entry,
                    title=title_for(entry),
                    category=cat,
                    status=NodeStatus.ACTIVE.value,
                )

        return self._success(target, message, **extra_fields)

    def _no_vault(self) -> Dict[str, Any]:
        return _flat()._error(
            "The brain vault is unavailable, so nothing was written. The note was not saved — "
            "retry, and if it keeps failing check that ~/.pulse/brain is writable."
        )


__all__ = ["BrainStore"]
