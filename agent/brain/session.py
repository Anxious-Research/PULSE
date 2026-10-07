"""Agent-facing bridge to the brain — the only module that couples the vault to a live agent.

Everything below this module (`vault`, `parser`, `index`, `decay`, `similarity`, `recall`,
`prefix`) is pure and testable without an agent and imports nothing from `agent.*`. This
module is where that purity is spent, so the coupling is in exactly one reviewable place.

Three rules, each from the failure that cost the user their memory:

1. **Fail-open, never raise.** Vault missing, file unreadable, bad config, a bug in recall —
   every path degrades to "no brain context this turn". It must never break agent init and
   must never be swallowed by a bare ``suppress()`` that leaves a local unbound.
2. **The read path never creates or writes the vault.** It only reports a context block.
   Creating the vault belongs to the write path, so nothing here can touch disk on a turn
   that has no memory work to do. The one exception is reconsolidation (strengthening the
   notes that were actually recalled), which is opt-outable.
3. **Silent and absent-by-default in effect.** An empty vault yields ``""``. Installing this
   therefore cannot change what the agent says until the vault holds real notes — there is no
   "upgrade made it worse" failure mode.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from . import prefix as prefix_mod
from .index import BrainIndex
from .recall import (
    DEFAULT_DECAY,
    DEFAULT_HOPS,
    DEFAULT_LIMIT,
    DEFAULT_MAX_TOKENS,
    DEFAULT_MIN_SCORE,
    recall,
)
from .vault import BrainVault

logger = logging.getLogger("agent.brain.session")

# Layer-2 defaults (specs/brain.md §6: 400–800 tokens, hard cap).
DEFAULT_RECALL_MAX_TOKENS = DEFAULT_MAX_TOKENS  # 600
DEFAULT_RECALL_LIMIT = DEFAULT_LIMIT
DEFAULT_RECALL_HOPS = DEFAULT_HOPS
DEFAULT_RECALL_MIN_SCORE = DEFAULT_MIN_SCORE
DEFAULT_SPREAD_DECAY = DEFAULT_DECAY


def _truthy(value: Any, *, default: bool = True) -> bool:
    """Config values arrive as bools, strings or numbers depending on the writer."""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if not text:
        return default
    return text in {"1", "true", "yes", "on", "enabled"}


def _positive_int(value: Any, default: int, *, minimum: int = 1, maximum: int = 100000) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, number))


def _score(value: Any, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return max(0.0, min(1.0, number))


def get_brain_config(config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Normalized ``brain`` config section ({} when missing or malformed).

    Mirrors ``tools.memory_tool.get_builtin_memory_config`` so the brain reads its config the
    same way the legacy store does: a missing section means "use defaults", never "disabled".
    """
    if config is None:
        try:
            from pulse_cli.config import load_config_readonly

            config = load_config_readonly()
        except Exception:
            logger.debug("could not read brain config", exc_info=True)
            return {}
    section = config.get("brain") if isinstance(config, dict) else None
    return section if isinstance(section, dict) else {}


@dataclass(frozen=True)
class BrainSettings:
    """Resolved brain behaviour for one agent/session."""

    enabled: bool = True
    # Layer 2 — per-turn recall.
    recall_enabled: bool = True
    recall_max_tokens: int = DEFAULT_RECALL_MAX_TOKENS
    recall_limit: int = DEFAULT_RECALL_LIMIT
    recall_hops: int = DEFAULT_RECALL_HOPS
    recall_min_score: float = DEFAULT_RECALL_MIN_SCORE
    spread_decay: float = DEFAULT_SPREAD_DECAY
    # Reconsolidation: recalling a note strengthens it (spacing effect).
    reconsolidate: bool = True
    # Layer 1 — system-prompt prefix. Off by default: it edits the cached prefix, so it is
    # only turned on deliberately (and only matters once self/ and user/ notes exist).
    prefix_enabled: bool = False
    prefix_max_chars: int = prefix_mod.DEFAULT_MAX_CHARS

    @classmethod
    def from_config(cls, config: Optional[Dict[str, Any]] = None) -> "BrainSettings":
        section = get_brain_config(config)
        return cls(
            enabled=_truthy(section.get("enabled"), default=True),
            recall_enabled=_truthy(section.get("recall_enabled"), default=True),
            recall_max_tokens=_positive_int(
                section.get("recall_max_tokens"), DEFAULT_RECALL_MAX_TOKENS, minimum=64, maximum=8000
            ),
            recall_limit=_positive_int(section.get("recall_limit"), DEFAULT_RECALL_LIMIT, minimum=1, maximum=50),
            recall_hops=_positive_int(section.get("recall_hops"), DEFAULT_RECALL_HOPS, minimum=0, maximum=4),
            recall_min_score=_score(section.get("recall_min_score"), DEFAULT_RECALL_MIN_SCORE),
            spread_decay=_score(section.get("spread_decay"), DEFAULT_SPREAD_DECAY),
            reconsolidate=_truthy(section.get("reconsolidate"), default=True),
            prefix_enabled=_truthy(section.get("prefix_enabled"), default=False),
            prefix_max_chars=_positive_int(
                section.get("prefix_max_chars"), prefix_mod.DEFAULT_MAX_CHARS, minimum=200, maximum=60000
            ),
        )


def resolve_settings(agent: Any = None) -> BrainSettings:
    """Settings from the agent's config when it has one, else the process config."""
    config = getattr(agent, "_agent_config", None) if agent is not None else None
    return BrainSettings.from_config(config)


def get_agent_vault(agent: Any = None) -> Optional[BrainVault]:
    """Vault for this agent, memoized on the agent. Returns ``None`` when unavailable.

    Deliberately does **not** call ``ensure_vault_structure()``: the read path must not create
    directories. A missing vault means "no memory yet", which is not an error.
    """
    cached = getattr(agent, "_brain_vault", None) if agent is not None else None
    if cached is not None:
        return cached
    try:
        vault = BrainVault()
        if not Path(vault.vault_dir).is_dir():
            return None
    except Exception:
        logger.debug("brain vault unavailable", exc_info=True)
        return None
    if agent is not None:
        try:
            agent._brain_vault = vault
        except Exception:
            pass
    return vault


def vault_fingerprint(vault: Any) -> Tuple[int, float]:
    """``(note count, newest mtime)`` — cheap change detection that parses no note.

    Duck-typed on ``vault_dir`` rather than annotated ``BrainVault``: the only thing this needs
    is a directory to walk, which keeps it usable from diagnostics and tests.
    """
    try:
        files = list(Path(vault.vault_dir).rglob("*.md"))
    except Exception:
        return (0, 0.0)
    newest = 0.0
    for path in files:
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        if mtime > newest:
            newest = mtime
    return (len(files), newest)


def get_agent_index(agent: Any, vault: BrainVault) -> Optional[BrainIndex]:
    """Link index for this agent, rebuilt only when the vault changed."""
    cached = getattr(agent, "_brain_index_cache", None) if agent is not None else None
    fingerprint = vault_fingerprint(vault)
    if isinstance(cached, tuple) and len(cached) == 2 and cached[0] == fingerprint:
        return cached[1]
    try:
        index = BrainIndex(vault).rebuild()
    except Exception:
        logger.debug("brain index rebuild failed", exc_info=True)
        return None
    if agent is not None:
        try:
            agent._brain_index_cache = (fingerprint, index)
        except Exception:
            pass
    return index


def restamp_index_cache(agent: Any, vault: Any) -> None:
    """Re-stamp the cached index fingerprint after a write we know is structural no-op.

    Reconsolidation rewrites the recalled notes' frontmatter (``access_count``,
    ``last_accessed``, ``stability``), which bumps their mtime. The mtime fingerprint would then
    look stale and the link index would be rebuilt on every single turn that recalls anything —
    paying the full parse cost of the vault for a change that cannot affect ids, titles, links or
    tags. Re-stamping records the new fingerprint against the index already in hand.

    Safe precisely because self-inflicted writes are the only ones this covers: any other edit
    (a user editing a note, the agent writing via the memory tool) changes mtimes that this does
    not re-stamp, so it still triggers a rebuild on the next turn.
    """
    if agent is None:
        return
    cached = getattr(agent, "_brain_index_cache", None)
    if not (isinstance(cached, tuple) and len(cached) == 2):
        return
    try:
        agent._brain_index_cache = (vault_fingerprint(vault), cached[1])
    except Exception:
        pass


def reset_agent_cache(agent: Any) -> None:
    """Drop memoized vault/index/prefix so the next turn re-reads disk.

    Called on the same boundary that reloads the legacy store (after compression, and at
    session start) so a mid-session write is picked up.
    """
    for attribute in ("_brain_vault", "_brain_index_cache", "_brain_prefix_cache"):
        try:
            delattr(agent, attribute)
        except Exception:
            pass


def _query_text(message: Any) -> str:
    """Flatten a str or multimodal message to the text a cue can be extracted from."""
    if isinstance(message, str):
        return message.strip()
    if isinstance(message, list):
        parts = []
        for item in message:
            if isinstance(item, dict) and item.get("type") == "text":
                text = item.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return " ".join(parts).strip()
    return ""


def _is_trivial(text: str) -> bool:
    """Reuse the shared trivial-prompt gate (bare greetings, slash commands).

    Imported lazily: ``agent.brain`` must import with zero third-party dependencies, and this
    helper lives in a module that may not be that frugal.
    """
    try:
        from agent.memory_provider import is_trivial_prompt

        return bool(is_trivial_prompt(text))
    except Exception:
        return False


def brain_turn_context(agent: Any, user_message: Any, *, settings: Optional[BrainSettings] = None) -> str:
    """Layer-2 recall for this turn: the rendered memory block, or ``""``.

    Returns the block **unfenced** — the caller wraps it with ``build_memory_context_block`` so
    the pipeline keeps exactly one fence and the same replay/cache sidecar handling as every
    other per-turn injection.

    ``""`` is the normal answer and means "nothing here is relevant enough to inject", which is
    the intended selectivity: a human does not replay their whole life at every question.
    """
    settings = settings or resolve_settings(agent)
    if not settings.enabled or not settings.recall_enabled:
        return ""

    query = _query_text(user_message)
    if not query or _is_trivial(query):
        return ""

    vault = get_agent_vault(agent)
    if vault is None:
        return ""
    index = get_agent_index(agent, vault)
    if index is None:
        return ""

    try:
        result = recall(
            query,
            vault=vault,
            index=index,
            limit=settings.recall_limit,
            hops=settings.recall_hops,
            spread_decay=settings.spread_decay,
            min_score=settings.recall_min_score,
            max_tokens=settings.recall_max_tokens,
        )
    except Exception:
        logger.debug("brain recall failed", exc_info=True)
        return ""

    if result.empty:
        return ""

    if settings.reconsolidate:
        # Recall is what makes a memory stick: strengthen exactly the notes that were used.
        try:
            vault.record_access(result.node_ids())
            # Our own metadata write must not look like a structural vault change.
            restamp_index_cache(agent, vault)
        except Exception:
            logger.debug("brain reconsolidation skipped", exc_info=True)

    try:
        return result.render(max_tokens=settings.recall_max_tokens)
    except Exception:
        logger.debug("brain recall render failed", exc_info=True)
        return ""


def brain_stable_prefix(agent: Any, *, settings: Optional[BrainSettings] = None) -> str:
    """Layer-1 prefix for the system prompt, or ``""``. Off unless enabled in config.

    Memoized on the agent for the session. That is required for correctness, not just speed:
    this text lands in the cached prompt prefix, so recomputing it mid-session would invalidate
    the cache the moment any note changed. ``reset_agent_cache`` clears it on the same boundary
    the legacy store reloads.
    """
    settings = settings or resolve_settings(agent)
    if not settings.enabled or not settings.prefix_enabled:
        return ""

    cached = getattr(agent, "_brain_prefix_cache", None) if agent is not None else None
    if isinstance(cached, str):
        return cached

    vault = get_agent_vault(agent)
    text = ""
    if vault is not None:
        try:
            text = prefix_mod.compile_stable_prefix(vault, max_chars=settings.prefix_max_chars)
        except Exception:
            logger.debug("brain prefix compile failed", exc_info=True)
            text = ""
    if agent is not None:
        try:
            agent._brain_prefix_cache = text
        except Exception:
            pass
    return text


def brain_status(agent: Any = None) -> Dict[str, Any]:
    """Small diagnostic snapshot — used by tests and troubleshooting, never by a turn."""
    settings = resolve_settings(agent)
    vault = get_agent_vault(agent)
    status: Dict[str, Any] = {"settings": settings.__dict__.copy(), "vault": None}
    if vault is None:
        return status
    try:
        status["vault"] = {
            "dir": str(vault.vault_dir),
            "fingerprint": vault_fingerprint(vault),
            "stats": vault.stats(),
            "generated_at": int(time.time()),
        }
    except Exception:
        status["vault"] = {"error": "stats failed"}
    return status
