"""Encoding — the salience gate over what is worth remembering (specs/brain.md §3.1).

Not every turn writes a memory: that produces noise, which is exactly what "faltu knowledge"
looks like. At turn end, candidate memories are extracted and salience-scored, and only those
above threshold are written.

    salience = w1·explicit      (the user stated a fact/decision/preference directly)
             + w2·correction    (the user corrected PULSE — always encodes)
             + w3·novelty       (not already represented in the vault)
             + w4·recurrence    (the same theme has been seen before)
             + w5·outcome       (something was decided / shipped / broke)

Two record kinds come out of this, matching §3.1:

- **Episodic** — "what happened": turn-scoped and dated, appended to ``daily/YYYY-MM-DD``.
  Cheap, high volume, naturally time-ordered. Hippocampal capture.
- **Semantic** — "what is true": durable and decontextualised, in ``concept/``, ``user/``,
  ``self/``. Created *mostly by consolidation* (``consolidate.py``), not directly here.

The one deliberate exception is a **correction**: §3.5 requires it to take effect immediately
and supersede what it contradicts, so corrections are written semantic on the spot rather than
waiting for a background pass that may be debounced past the end of the conversation.

The detectors are lexical and explainable on purpose. A model-judged "is this important?" call
would move the gate into a prompt, which is the promptic shortcut this design exists to avoid —
and it would make the gate untestable. Every signal below is a rule with a test.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .correction import find_contradictions, find_temporal_revisions
from .models import NodeCategory, NodeStatus
from .parser import slugify
from .similarity import similarity

logger = logging.getLogger(__name__)

# ── the salience function ──────────────────────────────────────────────────
#
# The three primary signals are each strong enough to clear the gate on their own; novelty and
# recurrence only sharpen a score. The weights therefore sum above 1.0 and the result is clamped
# to [0, 1] — the score answers "how memorable is this", not "what is the probability".
PRIMARY_EXPLICIT = 0.40
PRIMARY_CORRECTION = 0.30
PRIMARY_OUTCOME = 0.32
#: A declarative statement about an entity ("Project Alpha uses architecture B") asserts how
#: things ARE — the shape of durable knowledge. Weighted to clear the gate with novelty alone
#: (0.34 + 0.10 = 0.44 fresh, ≥0.41 in a populated vault), so ordinary project facts are learned
#: without an explicit "remember this", while chatter (no relational verb, no entity anchor) stays out.
PRIMARY_DECLARATIVE = 0.34
#: A durable state change ("we changed the graph architecture", "migrated to architecture B")
#: asserts that reality is now different — that is knowledge about the world, not a fleeting
#: episode, and it is exactly what a later correction revises. Weighted to clear the gate with
#: novelty alone (0.38 + 0.10 = 0.48) so the event is an addressable, correctable node.
PRIMARY_STATE_CHANGE = 0.38
MODIFIER_NOVELTY = 0.10
#: Restating something already known. Weighted so that a repeated declarative fact clears the gate
#: (0.34 + 0.08 = 0.42): recurrence is the strongest corroboration there is, and at 0.05 it summed
#: to 0.39 and could never tip a declarative statement over, so a verbatim restatement was dropped
#: before it could be recorded as evidence — which left the belief layer with no way to accumulate
#: corroboration. Only repeats of already-known material can benefit, since recurrence fires on
#: overlap with the corpus and nothing else.
MODIFIER_RECURRENCE = 0.08

#: A weak signal with a novel topic maxes out at 0.15, so trivia cannot cross this — something
#: must actually be asserted, corrected or decided for a turn to be remembered. Raised to 0.40
#: to filter marginal spec/doc fragments while preserving outcome-only memories (0.32+0.10=0.42).
DEFAULT_THRESHOLD = 0.40

#: §3.5: corrections always encode, however they are worded.
CORRECTION_FLOOR = 0.95

#: Two notes this similar are restatements, so recurrence is counting and consolidation will
#: merge them.
RECURRENCE_SIMILARITY = 0.5
RECURRENCE_SATURATION = 3

# ── signal lexicons ────────────────────────────────────────────────────────
#
# Patterns are matched on the raw lowercased message. Each has a test that it fires, and — more
# important — a test that ordinary prose does NOT fire it, because a detector that matches
# everything silently returns the system to "remember every turn".

_EXPLICIT_PATTERNS: Tuple[re.Pattern, ...] = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        # Direct instruction to remember.
        r"\bremember\b", r"\bnote that\b", r"\bkeep in mind\b", r"\bfor future reference\b",
        r"\bdon'?t forget\b", r"\bsave (?:this|that)\b", r"\bmake a note\b",
        r"\byaad rakh", r"\byaad rakhna\b", r"\bnote kar lo\b",
        # Standing instructions, which are the durable kind of statement.
        r"\bfrom now on\b", r"\bgoing forward\b", r"\balways\b", r"\bnever\b",
        r"\bwhenever\b", r"\bevery time\b", r"\bmake sure\b",
        # First-person disclosure: the substance of a user profile.
        r"\bmy name is\b", r"\bcall me\b", r"\bi prefer\b", r"\bi like\b", r"\bi hate\b",
        r"\bi want you to\b", r"\bi need you to\b", r"\bi'?m a\b", r"\bi am a\b",
        r"\bi work (?:at|on|as)\b", r"\bi use\b", r"\bi live\b", r"\bmy \w+ is\b",
        # Conventions and rules worth keeping.
        r"\bthe (?:rule|convention|standard|policy|decision) is\b", r"\bwe (?:decided|agreed)\b",
    )
)

_CORRECTION_STRONG: Tuple[re.Pattern, ...] = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\bthat'?s (?:wrong|incorrect|not right|not what)\b", r"\byou'?re wrong\b",
        r"\bnot what i (?:said|asked|meant)\b", r"\bi (?:said|told you|already said)\b",
        r"\bstop (?:doing|that|using|adding|writing)\b", r"\bdon'?t do that\b",
        r"\bnever do that\b", r"\bthat is not (?:what|how)\b", r"\bwrong\b",
        r"\bgalat\b", r"\baisa nahi\b", r"\bnahi,?\s+(?:aisa|ye|wo)\b",
        r"\bmat karo\b", r"\bband karo\b", r"\bcorrections?\b\s*[,:]",
        r"\bi (?:did ?n'?t|never) (?:say|ask|mean)\b", r"\bundo (?:that|this)\b",
        r"\brevert (?:that|this)\b",
    )
)

# Weak markers only count when two of them co-occur: "actually" and "instead" are ordinary
# English words, and a single one of them is not evidence of a correction.
_CORRECTION_WEAK = re.compile(
    r"\b(?:actually|instead|rather than|not \w+ but|only \w+ not|should be|shouldn'?t)\b",
    re.IGNORECASE,
)
_CORRECTION_WEAK_MIN = 2
# A turn that OPENS with one of these revises what PULSE holds even when no other marker fires:
# "Actually, that happened yesterday" is a revision, not idle narration. The comma/colon and the
# negative lookahead matter — "actually let me check the logs" is discourse filler, not a correction.
_CORRECTION_OPENER_RE = re.compile(
    r"^\s*(?:actually|no|wait|correction|hold on|sorry)\s*[,:]\s*"
    r"(?!let me\b|i'?ll\b|i will\b|could\b|can we\b|should we\b|maybe\b|hold on\b)",
    re.IGNORECASE,
)

# ── declarative-fact detection ─────────────────────────────────────────────
#
# "Project X uses architecture A." is knowledge; "thanks, cool" is not. The discriminator is
# grammatical: a declarative fact links a subject to a predicate with a *stable* relational verb,
# and it is *about* something recallable (a proper noun, a coded name, or a domain concept).
# Spec/doc prose is already gated by ``_is_specification_prose`` before this runs, so a plain
# sentence that survives that gate and has this shape is genuine conversational knowledge.
# A *stable relational verb* links a subject to a predicate durably. The list must cover the
# verbs people actually use for durable facts, not a handful of programming nouns: an earlier
# list omitted `writes` / `pushes` / `runs`, so "The atlas deploy pipeline pushes to staging
# before production" scored 0 on this signal and — carrying only the 0.10 novelty modifier — fell
# under the 0.4 gate and was never remembered. A closed list is never complete, so it is kept
# broad and includes the regular third-person forms.
_DECLARATIVE_VERBS = re.compile(
    r"\b(?:is|are|was|were|has|have|had|does|do|"
    r"uses?|used|requires?|required|depends?\s+on|contains?|includes?|provides?|"
    r"connects?|links?|supports?|exposes?|represents?|means?|equals?|consists?\s+of|"
    r"serves?|powers?|runs?|run|"
    # Common transitive / stative verbs people state durable facts with.
    r"writes?|write|reads?|read|sends?|send|pushes?|push|pulls?|pull|"
    r"calls?|call|creates?|create|deletes?|delete|removes?|remove|adds?|add|"
    r"updates?|update|triggers?|trigger|returns?|return|accepts?|accept|"
    r"emits?|emit|listens?|listen|starts?|start|stops?|stop|checks?|check|"
    r"validates?|validate|process(?:es)?|handles?|handle|manages?|manage|"
    r"owns?|own|holds?|hold|keeps?|keep|allows?|allow|prevents?|prevent|"
    r"takes?|take|gets?|get|makes?|make|moves?|move|lives?|live|sits?|sit|"
    r"points?|point|maps?|map|routes?|route|renders?|render|parses?|parse|"
    r"loads?|load|saves?|save|stores?|store|cach(?:es|e)|index(?:es)?|tracks?|track|"
    r"records?|record|logs?|log|reports?|report|counts?|count|measures?|measure|"
    r"limits?|limit|caps?|cap|wraps?|wrap|guards?|guard|gates?|gate|"
    r"fails?|fail|pass(?:es)?|breaks?|break|fix(?:es)?|changes?|change|"
    r"replaces?|replace|merges?|merge|splits?|split|sorts?|sort|filters?|filter|"
    r"matches?|match|search(?:es)?|finds?|find|resolves?|resolve|computes?|compute|"
    r"generates?|generate|builds?|build|deploys?|deploy|installs?|install|"
    r"configures?|configure|enables?|enable|disables?|disable|sets?|set|"
    r"opens?|open|closes?|close|binds?|bind|registers?|register|"
    r"subscribes?|subscribe|publishes?|publish|consumes?|consume|"
    r"dispatch(?:es)?|schedules?|schedule|retries|retry|awaits?|await|"
    r"expects?|expect|assumes?|assume|asserts?|assert|denies|deny|"
    r"permits?|permit|blocks?|block|isolates?|isolate|shares?|share|syncs?|sync|"
    r"imports?|import|exports?|export|extends?|extend|inherits?|inherit|"
    r"overrides?|override|implements?|implement|defines?|define|describes?|describe|"
    r"should|must|will|needs?\s+to)\b",
    re.IGNORECASE,
)

#: Domain concept words: a sentence anchored on one of these is about PULSE's world, so its
#: linking verb asserts durable project knowledge rather than casual chat ("it is fine").
_DECLARATIVE_DOMAIN_NOUNS = frozenset({
    "pulse", "user", "project", "architecture", "brain", "graph", "memory", "system",
    "model", "design", "plan", "decision", "feature", "api", "database", "server",
    "deployment", "codebase", "repo", "repository", "workflow", "pipeline", "schema",
    "config", "agent", "skill", "tool", "vault", "relationship", "node", "edge",
    "recall", "encoder", "consolidation", "decay",
})


#: A *coded name* — the third anchor the docstring promises — is a compound technical noun phrase:
#: "the payments service", "our deploy pipeline", "the ledger table". The head noun is what makes
#: the phrase a name rather than prose, and the set is technical vocabulary common to any project,
#: so this does not depend on PULSE's own subject matter the way the domain-noun list does.
_CODED_NAME_HEADS = (
    "service", "services", "module", "modules", "system", "systems", "pipeline", "pipelines",
    "api", "apis", "server", "servers", "database", "databases", "db", "repo", "repos",
    "repository", "component", "components", "layer", "layers", "engine", "engines",
    "store", "stores", "index", "indexes", "indices", "cache", "caches", "queue", "queues",
    "worker", "workers", "job", "jobs", "scheduler", "cluster", "clusters", "node", "nodes",
    "table", "tables", "schema", "schemas", "endpoint", "endpoints", "gateway", "gateways",
    "proxy", "proxies", "client", "clients", "backend", "backends", "frontend", "frontends",
    "daemon", "daemons", "bot", "bots", "app", "apps", "application", "applications",
    "ledger", "ledgers", "account", "accounts", "invoice", "invoices", "payment", "payments",
    "user", "users", "tenant", "tenants", "team", "teams", "org", "orgs", "project", "projects",
    "framework", "library", "sdk", "cli", "tool", "tools", "script", "scripts", "file", "files",
    "directory", "folder", "bucket", "container", "image", "images", "pod", "pods", "vm",
    "host", "hosts", "domain", "environment", "environments", "stage", "stages", "region",
    "bucket", "stream", "streams", "topic", "topics", "event", "events", "webhook", "webhooks",
)
_CODED_NAME_RE = re.compile(
    r"\b\w[\w-]*\s+(?:" + "|".join(re.escape(h) for h in sorted(set(_CODED_NAME_HEADS))) + r")\b",
    re.IGNORECASE,
)


def _has_entity_anchor(statement: str) -> bool:
    """True when a statement is *about* an entity PULSE could later recall it by.

    Three anchors count: a proper noun / acronym (a capitalised token after the first word —
    "Project Alpha", "PULSE", "architecture B"), a coded name ("the payments service", "our deploy
    pipeline"), or a domain concept word ("the graph", "the Brain"). The first-word capital of
    "The graph…" is sentence case, not a proper noun, so it is deliberately not treated as an
    anchor.
    """
    words = statement.split()
    for i, word in enumerate(words):
        core = word.strip("\"'(),.;:!?")
        if i > 0 and len(core) >= 2 and core[0].isupper() and (core.isupper() or core[1:].islower()):
            return True
    if _CODED_NAME_RE.search(statement):
        return True
    lower = statement.lower()
    return any(re.search(rf"\b{re.escape(noun)}\b", lower) for noun in _DECLARATIVE_DOMAIN_NOUNS)


def _declarative_signal(text: str) -> float:
    """``1.0`` for a declarative statement about a recallable entity, else ``0.0``."""
    if not _DECLARATIVE_VERBS.search(text):
        return 0.0
    if not _has_entity_anchor(text):
        return 0.0
    return 1.0

_OUTCOME_PATTERNS: Tuple[re.Pattern, ...] = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        # Shipped / landed — a result that outlives the turn.
        r"\bshipped\b", r"\bdeployed\b", r"\breleased\b", r"\bpublished\b",
        r"\bmerged\b", r"\bcommitted\b", r"\bpushed\b", r"\blanded\b",
        # Resolved / decided.
        r"\bfixed\b", r"\bresolved\b", r"\bdecided\b", r"\bdecision\b", r"\bwe'?re going with\b",
        r"\bapproved\b", r"\bconfirmed\b",
        # Broke — equally memorable, and the reason "what went wrong" is recallable.
        r"\bbroke\b", r"\bbroken\b", r"\bregression\b", r"\breverted\b", r"\brolled back\b",
        r"\bfailed\b", r"\bfailure\b", r"\boutage\b",
        # Verified state.
        r"\btests? pass", r"\bpassing\b", r"\bverified\b", r"\bit works\b", r"\bworks now\b",
        # Hinglish, same states.
        r"\bkar diya\b", r"\bho gaya\b", r"\bban gaya\b", r"\btest pass\b",
    )
)

#: Durable *state changes* — the world is now different. These are knowledge (an event worth a
#: node, addressable and revisable), not a transient episode, so they encode semantic. A later
#: correction ("that change happened yesterday") revises this node's attribute instead of
#: orphaning the event in a day note.
_STATE_CHANGE_PATTERNS: Tuple[re.Pattern, ...] = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\bchanged?\b", r"\bupdated?\b", r"\bmodified\b", r"\bswitched?\b", r"\bmigrated\b",
        r"\breplaced?\b", r"\brefactored\b", r"\brewrote\b", r"\brenamed\b", r"\bupgraded?\b",
        r"\bmoved?\s+(?:to|from)\b", r"\bnow uses?\b", r"\bno longer\b",
    )
)

# ── statement extraction ───────────────────────────────────────────────────

_BOUNDARY_RE = re.compile(r"(?<=[.!?;])\s+|\n+")

# Ceremonial openers stripped from a statement so the note reads as the fact, not as the request
# that produced it ("remember that I prefer Hinglish" -> "I prefer Hinglish").
_MARKER_PREFIX_RE = re.compile(
    r"^(?:ok(?:ay)?[,:]?\s*)?(?:please\s+)?"
    r"(?:remember|note|keep in mind|don'?t forget|make a note|save this|fyi|for your information)"
    r"(?:(?:\s+that\b)|\s*[,:—-]|\s+)\s*",
    re.IGNORECASE,
)
_HINGLISH_PREFIX_RE = re.compile(
    r"^(?:yaad rakh(?:na|o|iye)?|note kar lo)(?:(?:\s+ki\b)|\s*[,:—-]|\s+)\s*",
    re.IGNORECASE,
)

# Whole-message trivia: greetings, acknowledgements, commands, and bare fragments carry nothing
# worth remembering. Checked before any scoring so a polite "thanks!" never becomes a memory.
_TRIVIA_RE = re.compile(
    r"^(?:hi|hey|hello|yo|sup|thanks|thank you|thx|ty|ok|okay|k|cool|nice|great|got it|"
    r"understood|sure|yes|no|yep|nope|done|continue|go on|go ahead|next|please continue|"
    r"hmm+|ah+|oh+|👍|🙏)[\s!.?,]*$",
    re.IGNORECASE,
)
_COMMAND_RE = re.compile(r"^/[a-z][a-z0-9_-]*\b", re.IGNORECASE)
# "remember" alone is a memory instruction even when the message is short ("remember this").
_REMEMBER_RE = re.compile(r"\bremember\b|\byaad rakh", re.IGNORECASE)

_MIN_STATEMENT_WORDS = 3
_MIN_STATEMENT_CHARS = 10

# Formatted prose markers: tree diagrams, list bullets, box-drawing, markdown structure.
# When >40% of lines start with these, the message is spec/doc content, not conversational facts.
_PROSE_MARKERS = re.compile(r'^[\s│├└─•\-*>#`]{1,4}(?:\s|[a-zA-Z])', re.MULTILINE)


@dataclass(frozen=True)
class SalienceSignals:
    """The five §3.1 inputs, each normalised to ``[0, 1]``."""

    explicit: float = 0.0
    correction: float = 0.0
    novelty: float = 0.0
    recurrence: float = 0.0
    outcome: float = 0.0
    declarative: float = 0.0
    state_change: float = 0.0

    def as_dict(self) -> Dict[str, float]:
        return {
            "explicit": round(self.explicit, 4),
            "correction": round(self.correction, 4),
            "novelty": round(self.novelty, 4),
            "recurrence": round(self.recurrence, 4),
            "outcome": round(self.outcome, 4),
            "declarative": round(self.declarative, 4),
            "state_change": round(self.state_change, 4),
        }


def score_salience(signals: SalienceSignals) -> float:
    """The §3.1 weighted sum, clamped to ``[0, 1]``.

    A correction is floored at :data:`CORRECTION_FLOOR` rather than merely weighted: §3.5 says a
    user correction always supersedes, and a correction worded as a bare "wrong" carries almost
    no other signal, so leaving it to the additive score would silently drop the most important
    thing a user can say.
    """
    score = (
        PRIMARY_EXPLICIT * max(0.0, min(1.0, signals.explicit))
        + PRIMARY_CORRECTION * max(0.0, min(1.0, signals.correction))
        + PRIMARY_OUTCOME * max(0.0, min(1.0, signals.outcome))
        + PRIMARY_DECLARATIVE * max(0.0, min(1.0, signals.declarative))
        + PRIMARY_STATE_CHANGE * max(0.0, min(1.0, signals.state_change))
        + MODIFIER_NOVELTY * max(0.0, min(1.0, signals.novelty))
        + MODIFIER_RECURRENCE * max(0.0, min(1.0, signals.recurrence))
    )
    if signals.correction >= 0.5:
        score = max(score, CORRECTION_FLOOR)
    return max(0.0, min(1.0, score))


def is_trivial(text: str) -> bool:
    """True for a message that cannot contain a memory (greeting, ack, command, fragment)."""
    stripped = " ".join((text or "").split())
    if not stripped:
        return True
    if _COMMAND_RE.match(stripped) or _TRIVIA_RE.match(stripped):
        return True
    return len(stripped) < _MIN_STATEMENT_CHARS and not _REMEMBER_RE.search(stripped)


def _is_specification_prose(text: str) -> bool:
    """True when the message is primarily spec/doc prose, not conversational facts.
    
    Detects tree diagrams, bullet lists, markdown structure, and formatted blocks that are
    documentation content rather than user statements to remember. A message that is >40%
    structured prose (lines starting with formatting markers) is filtered wholesale before
    statement extraction, so spec trees and skill content aren't blindly turned into nodes.
    """
    lines = [line for line in (text or "").splitlines() if line.strip()]
    if not lines:
        return False
    # Count lines starting with tree/list/structure markers
    formatted = sum(1 for line in lines if _PROSE_MARKERS.match(line))
    return formatted / len(lines) > 0.40


def _correction_signal(text: str) -> float:
    """``1.0`` on an unambiguous correction, ``0.7`` on two weak markers, else ``0.0``."""
    for pattern in _CORRECTION_STRONG:
        if pattern.search(text):
            return 1.0
    if _CORRECTION_OPENER_RE.match(text):
        # A turn that opens with "Actually, …" / "No, …" revises what PULSE holds even when no
        # other marker fires — the opener itself is the correction signal.
        return 1.0
    if len(_CORRECTION_WEAK.findall(text)) >= _CORRECTION_WEAK_MIN:
        return 0.7
    return 0.0


def _marker_signal(text: str, patterns: Sequence[re.Pattern]) -> float:
    """``1.0`` when any marker matches, else ``0.0``.

    Binary rather than count-based: the second "remember" in a sentence does not make the fact
    twice as memorable, and a count-based score would rank verbosity over substance.
    """
    return 1.0 if any(pattern.search(text) for pattern in patterns) else 0.0


def detect_signals(
    text: str,
    *,
    corpus: Sequence[str] = (),
    embedder: Any = None,
) -> SalienceSignals:
    """Score the five §3.1 signals for one candidate statement.

    ``corpus`` is the text of the existing vault nodes (their content). It supplies novelty and
    recurrence — the only two signals that need to know what PULSE already knows.
    """
    explicit = _marker_signal(text, _EXPLICIT_PATTERNS)
    correction = _correction_signal(text)
    outcome = _marker_signal(text, _OUTCOME_PATTERNS)
    declarative = _declarative_signal(text)
    # A state change only counts when the statement is otherwise substantive (≥3 content words):
    # "updated" alone in a fragment is not a durable claim about the world.
    state_change = _marker_signal(text, _STATE_CHANGE_PATTERNS) if len(text.split()) >= _MIN_STATEMENT_WORDS else 0.0

    texts = [text for text in (str(t or "") for t in corpus) if text.strip()]
    if not texts:
        # Nothing to compare against: the fact is new to PULSE, and no theme has recurred yet.
        novelty, recurrence = 1.0, 0.0
    else:
        scores = [similarity(text, existing, embedder=embedder) for existing in texts]
        novelty = 1.0 - max(scores)
        hits = sum(1 for score in scores if score >= RECURRENCE_SIMILARITY)
        recurrence = min(1.0, hits / RECURRENCE_SATURATION)

    return SalienceSignals(
        explicit=explicit,
        correction=correction,
        novelty=novelty,
        recurrence=recurrence,
        outcome=outcome,
        declarative=declarative,
        state_change=state_change,
    )


def _statements(text: str) -> List[str]:
    """Split a message into candidate statements, dropping questions and fragments.

    Questions are excluded because a question is a request for information, not information; and
    fragments are excluded because a four-word floor is what separates "the deploy broke" from
    "yes, that one".
    """
    out: List[str] = []
    for chunk in _BOUNDARY_RE.split(text or ""):
        statement = " ".join(chunk.split()).strip()
        if not statement:
            continue
        statement = _MARKER_PREFIX_RE.sub("", statement)
        statement = _HINGLISH_PREFIX_RE.sub("", statement)
        statement = statement.strip(" -—:;,.")
        if not statement or statement.endswith("?"):
            continue
        if len(statement) < _MIN_STATEMENT_CHARS:
            continue
        if len(statement.split()) < _MIN_STATEMENT_WORDS:
            continue
        if not re.search(r"[a-zA-Z\u0900-\u097F]", statement):
            continue
        out.append(statement)
    return out


_USER_PATTERNS = re.compile(
    r"\b(?:i|i'?m|i'?ve|my|me|mine|user prefers|user wants|user is|the user)\b", re.IGNORECASE
)
_SELF_PATTERNS = re.compile(
    r"\b(?:pulse|the agent|your|you are|you'?re|assistant)\b", re.IGNORECASE
)


def category_for(text: str) -> str:
    """Which vault folder a durable statement belongs in.

    Order matters: a note about PULSE *and* the user ("you should call me by my name") is a user
    preference, because that is the thing being stored.
    """
    if _USER_PATTERNS.search(text):
        return NodeCategory.USER.value
    if _SELF_PATTERNS.search(text):
        return NodeCategory.SELF.value
    return NodeCategory.CONCEPT.value


def title_for(text: str, *, max_words: int = 7) -> str:
    """Short human-readable title from a statement (the node id derives from it)."""
    head = re.split(r"[;:—–]|,\s+(?:and|but)\s+", text.strip(), maxsplit=1)[0].strip()
    words = (head or text).split()
    return " ".join(words[:max_words]).rstrip(" ,;:-—–.") or "memory"


@dataclass
class MemoryCandidate:
    """One statement that passed the gate, with the evidence for why."""

    text: str
    kind: str                      # "episodic" | "semantic"
    category: str
    title: str
    salience: float
    signals: SalienceSignals
    supersedes: List[str] = field(default_factory=list)
    #: Set when this statement restates an existing node (``kind == "restatement"``): the id of the
    #: memory it corroborates. A restatement is not written as a second node — it is recorded as
    #: evidence on the existing one.
    restates: str = ""
    #: Attribute-level corrections: ``[(node_id, corrected_text)]`` for existing nodes whose
    #: temporal attribute this statement revises (e.g. "happened yesterday" fixing "changed today").
    #: These are updated in place — the event is not superseded, only its attribute is corrected.
    temporal_revisions: List[Tuple[str, str]] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "text": self.text,
            "kind": self.kind,
            "category": self.category,
            "title": self.title,
            "salience": round(self.salience, 4),
            "signals": self.signals.as_dict(),
            "supersedes": list(self.supersedes),
            "restates": self.restates,
            "temporal_revisions": list(self.temporal_revisions),
        }


def extract_candidates(
    text: str,
    *,
    corpus: Sequence[str] = (),
    existing: Sequence[Tuple[str, str]] = (),
    recency: Optional[Dict[str, int]] = None,
    threshold: float = DEFAULT_THRESHOLD,
    embedder: Any = None,
) -> List[MemoryCandidate]:
    """Salience-gate one message into candidates.

    ``existing`` is ``(node_id, content)`` for the nodes a correction may supersede; ``corpus`` is
    the broader text pool used for novelty and recurrence. Passing ``existing`` implies ``corpus``
    entries with the same shape, so the caller can hand one list to both. ``recency`` maps a node
    id to its last-updated timestamp, used only to break ties when a deictic correction
    ("it happened yesterday") must pick which recent event it refers to.
    """
    if is_trivial(text):
        return []
    # Block spec/documentation prose before statement extraction (fixes junk-encoding defect).
    if _is_specification_prose(text):
        return []

    corpus_texts = list(corpus) or [content for _, content in existing]
    candidates: List[MemoryCandidate] = []

    for statement in _statements(text):
        signals = detect_signals(statement, corpus=corpus_texts, embedder=embedder)
        salience = score_salience(signals)

        # A restatement of something already known is checked BEFORE the salience gate. It is not a
        # new memory to be scored — it is another assertion of an existing one, and its entire value
        # is corroboration. Left to the gate it would be dropped: a verbatim repeat has novelty 0.0
        # and recurrence alone (1 hit / RECURRENCE_SATURATION) cannot reach the threshold, so the
        # strongest evidence the system can gather was being discarded before it was ever seen.
        restates = ""
        for node_id, content in existing:
            if content.strip() and similarity(statement, content, embedder=embedder) > 0.85:
                restates = node_id
                break
        if restates:
            candidates.append(
                MemoryCandidate(
                    text=statement,
                    kind="restatement",
                    category=category_for(statement),
                    title=title_for(statement),
                    salience=salience,
                    signals=signals,
                    restates=restates,
                )
            )
            continue

        if salience < float(threshold):
            continue

        # Attribute-level temporal correction: the statement corrects *when* a known event
        # happened. The event node is revised in place instead of being superseded, and this
        # statement does not itself become a duplicate node. Checked first because a pure
        # temporal fix must not also be read as a contradiction of the event it belongs to.
        temporal_revisions = (
            find_temporal_revisions(statement, existing, recency=recency)
            if existing and signals.correction >= 0.5
            else []
        )
        revised_ids = {node_id for node_id, _ in temporal_revisions}
        supersedes = (
            [
                node_id
                for node_id in find_contradictions(
                    statement, existing, correction_mode=True, embedder=embedder
                )
                if node_id not in revised_ids
            ]
            if existing and signals.correction >= 0.5
            else []
        )
        # Durable vs episode is decided by *kind of evidence*, not by score. An explicit statement
        # is one the user asked PULSE to hold ("remember that I prefer Hinglish", "my name is",
        # "from now on"), a correction is one that must take effect now, a declarative fact is
        # a stable assertion about an entity ("Project Alpha uses architecture B"), and a state
        # change ("we changed the graph architecture") is a durable event the world now reflects —
        # all four are knowledge and become addressable nodes. A bare transient outcome is an
        # episode ("the deploy broke") until the pattern behind it recurs (§3.1).
        durable = (
            signals.correction >= 0.5
            or signals.explicit >= 1.0
            or signals.declarative >= 1.0
            or signals.state_change >= 1.0
        )
        candidates.append(
            MemoryCandidate(
                text=statement,
                kind="semantic" if durable else "episodic",
                category=category_for(statement),
                title=title_for(statement),
                salience=salience,
                signals=signals,
                supersedes=supersedes,
                temporal_revisions=temporal_revisions,
            )
        )
    return candidates


def daily_node_id(now: Optional[float] = None) -> str:
    """``daily/YYYY-MM-DD`` in the vault's local-date convention."""
    ts = now if now is not None else time.time()
    return f"{NodeCategory.DAILY.value}/{datetime.fromtimestamp(ts, tz=timezone.utc).astimezone().strftime('%Y-%m-%d')}"


def encode_turn(
    vault: Any,
    text: str,
    *,
    turn_id: Optional[str] = None,
    now: Optional[float] = None,
    threshold: float = DEFAULT_THRESHOLD,
    embedder: Any = None,
    corpus_extra: str = "",
    dry_run: bool = False,
) -> Dict[str, Any]:
    """Encode one turn into the vault. Returns a report; ``dry_run`` writes nothing.

    Episodic candidates are appended to the day's note; semantic candidates (corrections and
    explicitly stated lasting facts) are written as their own node, and a correction supersedes
    every node it contradicts. Nothing is ever deleted, so re-encoding the same turn is safe:
    the day's note tracks the ``source_turn``\\ s it already holds and skips a repeat.
    """
    nodes = vault.list_all_nodes() if vault is not None else []
    existing = [(node.id, node.content or "") for node in nodes if (node.content or "").strip()]
    recency = {
        node.id: int(node.frontmatter.updated_at or node.frontmatter.created_at or 0)
        for node in nodes
    }
    corpus = [content for _, content in existing]
    # ``corpus_extra`` widens the novelty pool without being a statement source: the
    # assistant's own reply goes here, so a fact PULSE merely restated this turn does
    # not look novel enough to encode a second time.
    if (corpus_extra or "").strip():
        corpus = [*corpus, corpus_extra.strip()]

    candidates = extract_candidates(
        text, corpus=corpus, existing=existing, recency=recency, threshold=threshold, embedder=embedder
    )
    report: Dict[str, Any] = {
        "candidates": [candidate.as_dict() for candidate in candidates],
        "episodic": [],
        "semantic": [],
        "superseded": [],
        "revised": [],
        "corroborated": [],
        "skipped": [],
        "dry_run": bool(dry_run),
    }
    if not candidates or vault is None:
        return report

    ts = now if now is not None else time.time()

    episodic = [candidate for candidate in candidates if candidate.kind == "episodic"]
    semantic = [candidate for candidate in candidates if candidate.kind == "semantic"]
    restatements = [candidate for candidate in candidates if candidate.kind == "restatement"]

    # A restatement is evidence about an existing memory, not a new one: record the corroboration
    # and write nothing. Keeping the node count flat is what lets a belief accumulate independent
    # support without the vault filling up with the same sentence said twice.
    for candidate in restatements:
        corroborated = False
        if not dry_run and candidate.restates:
            try:
                corroborated = bool(vault.record_corroboration(
                    candidate.restates, text=candidate.text, source_turn=turn_id, now=ts,
                ))
            except Exception:
                logger.debug("corroboration record failed", exc_info=True)
        report["skipped"].append({
            "reason": "near_duplicate",
            "candidate": candidate.as_dict(),
            "existing_node": candidate.restates,
            "corroborated": corroborated,
        })
        if corroborated:
            report["corroborated"].append({"node_id": candidate.restates, "text": candidate.text})

    if episodic:
        report["episodic"] = _append_episodic(vault, episodic, turn_id=turn_id, now=ts, dry_run=dry_run)

    for candidate in semantic:
        # Attribute-level temporal correction (§3 A): the statement only revises *when* a known
        # event happened. Fix that event in place and keep its history — do NOT supersede it and
        # do NOT write a twin node for the correction itself.
        if candidate.temporal_revisions:
            revised_ids: List[str] = []
            for target_id, corrected_text in candidate.temporal_revisions:
                if dry_run:
                    revised_ids.append(target_id)
                    continue
                if vault.revise_attribute(
                    target_id, corrected_text, reason="temporal correction", source_turn=turn_id, now=ts
                ):
                    revised_ids.append(target_id)
            if revised_ids:
                report["revised"].append({"candidate": candidate.as_dict(), "nodes": revised_ids})
                continue  # correction folded into the existing event; no new node

        # Immediate duplicate check: skip if content is >85% similar to an existing node in
        # the same category (fixes -2/-3 suffix proliferation).
        existing_similar = [
            node for node in nodes
            if node.category == candidate.category
            and (node.content or "").strip()
            and similarity(candidate.text, node.content, embedder=embedder) > 0.85
        ]
        if existing_similar:
            # A restatement is not a second memory, but it is not nothing: it is an independent
            # corroboration of the existing claim, and reflection needs ≥2 independent memories to
            # hold a belief rather than a provisional guess. Previously this branch detected the
            # recurrence and then discarded it, which made a corroborated belief unreachable from
            # real conversation.
            target = existing_similar[0]
            corroborated = False
            if not dry_run:
                try:
                    corroborated = bool(vault.record_corroboration(
                        target.id, text=candidate.text, source_turn=turn_id, now=ts,
                    ))
                except Exception:
                    logger.debug("corroboration record failed", exc_info=True)
            report["skipped"].append({
                "reason": "near_duplicate",
                "candidate": candidate.as_dict(),
                "existing_node": target.id,
                "corroborated": corroborated,
            })
            if corroborated:
                report["corroborated"].append({"node_id": target.id, "text": candidate.text})
            continue
        
        node_id = vault.unique_node_id(candidate.category, slugify(candidate.title))
        if dry_run:
            report["semantic"].append({"node_id": node_id, **candidate.as_dict()})
            continue
        vault.write_node(
            node_id,
            candidate.text,
            title=candidate.title,
            category=candidate.category,
            tags=["encoded"],
            salience=candidate.salience,
            status=NodeStatus.ACTIVE.value,
            supersedes=candidate.supersedes or None,
            source_turn=turn_id,
        )
        superseded: List[str] = []
        for old_id in candidate.supersedes:
            if old_id == node_id:
                continue
            if vault.supersede(old_id, node_id, now=ts):
                superseded.append(old_id)
        if superseded:
            report["superseded"].extend(superseded)
        report["semantic"].append({"node_id": node_id, **candidate.as_dict()})

    return report


def _append_episodic(
    vault: Any,
    candidates: Sequence[MemoryCandidate],
    *,
    turn_id: Optional[str],
    now: float,
    dry_run: bool,
) -> List[Dict[str, Any]]:
    """Append one day's candidates to its ``daily/`` note, skipping a turn already recorded."""
    node_id = daily_node_id(now)
    existing_node = None if dry_run else vault.read_node(node_id)
    body = (existing_node.content or "") if existing_node else ""
    recorded = set()
    if existing_node is not None:
        recorded = {str(t) for t in (existing_node.frontmatter.extra.get("source_turns") or [])}

    # One line per statement, each carrying the turn it came from so later consolidation can trace
    # a promoted fact back to the episode that produced it.
    lines = [line for line in body.splitlines() if line.strip()]
    added: List[Dict[str, Any]] = []
    for candidate in candidates:
        # Containment against the whole line, not a re-parse of it: the line format is ours
        # ("- HH:MM <text> (turn id)") and a statement that is already recorded must not be
        # appended again when the same turn is re-encoded.
        if candidate.text in body:
            continue
        prefix = f"- {datetime.fromtimestamp(now, tz=timezone.utc).astimezone().strftime('%H:%M')}"
        line = f"{prefix} {candidate.text}" + (f" (turn {turn_id})" if turn_id else "")
        lines.append(line)
        added.append({"node_id": node_id, **candidate.as_dict()})

    if dry_run or not added:
        return added

    turns = sorted(recorded | ({str(turn_id)} if turn_id else set()))
    vault.write_node(
        node_id,
        "\n".join(lines),
        title=f"Episodes {node_id.split('/', 1)[-1]}",
        category=NodeCategory.DAILY.value,
        tags=["episodic"],
        # Salience of a day note is the best thing that happened in it, so a day containing a
        # correction is not itself treated as trivia.
        salience=max(candidate.salience for candidate in candidates),
        extra={"source_turns": turns},
        now=now,
    )
    return added
