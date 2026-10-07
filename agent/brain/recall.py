"""Cue-based associative recall — the mechanism that makes memory feel like remembering.

Stage S3 of specs/brain.md §3.4. Given the current message (and optional recent context):

    1. extract cues         salient terms from the query
    2. seed                 score vault nodes against those cues
    3. spread activation    over the wikilink graph, degree-normalised, bounded
    4. rank                 activation x retrievability x salience
    5. gate                 nothing above threshold => inject NOTHING this turn
    6. render               a hard token-bounded block for the per-turn context

Deliberate properties:
- **Bounded.** The rendered block never exceeds ``max_tokens``; the caller passes the budget.
- **Silent.** This module never narrates its work into the reply. It returns data; the prompt
  builder decides what to do with it.
- **Absent by default.** A weak query yields an empty result rather than filler — the user
  requirement was that irrelevant memory must not be injected every turn.
- **Nothing is deleted.** Decayed nodes are still ranked (lowly); they are excluded from the
  *rendered* block unless ``include_dormant``, because they are not useful context.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence

from . import decay as decay_mod
from .index import BrainIndex
from .models import NodeStatus
from .parser import normalize_node_id
from .similarity import bigrams, overlap_coefficient, similarity, tokenize
from .vault import BrainVault

DEFAULT_MAX_TOKENS = 600
DEFAULT_LIMIT = 8
DEFAULT_HOPS = 2
DEFAULT_DECAY = 0.55
DEFAULT_MIN_SCORE = 0.10
# A hit scoring below this fraction of the best hit is dropped: when one memory is clearly
# what the cue is about, injecting five also-rans adds noise, not context.
RELATIVE_FLOOR = 0.25
MAX_CUES = 24
SNIPPET_CHARS = 260
# Rough characters-per-token. Deliberately conservative (real tokenizers average ~4);
# the budget is a hard cap, so over-estimating tokens is the safe direction.
CHARS_PER_TOKEN = 4
# Prefix length used as a crude stemmer — see _key().
PREFIX_LEN = 5

# Interaction words carry no retrieval signal and would seed everything equally.
_QUERY_STOPWORDS = frozenset(
    """about after again all also and any are because been before being both but can cant could
    did do does doing dont down during each few for from further had has have having he her here
    hers him his how into its itself just like make many me might more most much must my no nor
    not now off on once only or other our out over own same should so some such than that the
    their them then there these they this those through to too under until up very was we were
    what when where which while who whom why will with would you your""".split()
)

_STATUS_EXCLUDED = {NodeStatus.SUPERSEDED.value, NodeStatus.ARCHIVED.value}


def _key(term: str) -> str:
    """Match key for a token or phrase cue.

    Uses a 5-character prefix in place of a real stemmer. Rationale: a short query rarely
    spells a word exactly as the note does ('prefer' vs 'prefers', 'forget' vs 'forgetting'),
    and an exact token comparison silently misses those. A hand-rolled suffix stripper fixes
    some of those while breaking others (it turns 'forgetting' into 'forgett' but leaves
    'forget' alone, so the two stop matching) — prefix matching has no such failure mode and
    is monotone: any two words sharing a prefix match. Known limitation: words sharing a
    5-character prefix but unrelated ('forget'/'forgery') are treated as equal. For cue
    matching that is an acceptable trade, and it never makes retrieval *worse* than exact
    matching for the words it does match.
    """
    if " " in term:
        return " ".join(_key(part) for part in term.split())
    return term if len(term) < PREFIX_LEN else term[:PREFIX_LEN]


def _keyset(tokens: Iterable[str]) -> set:
    return {_key(t) for t in tokens}


def _bigram_keys(tokens: Sequence[str]) -> set:
    return {_key(bg) for bg in bigrams(tokens)}


@dataclass
class RecallHit:
    node_id: str
    title: str
    category: str
    score: float
    activation: float
    retrievability: float
    salience: float
    snippet: str
    links: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.node_id,
            "title": self.title,
            "category": self.category,
            "score": round(self.score, 6),
            "activation": round(self.activation, 6),
            "retrievability": round(self.retrievability, 6),
            "salience": round(self.salience, 4),
            "links": list(self.links),
        }


@dataclass
class RecallResult:
    hits: List[RecallHit] = field(default_factory=list)
    cues: List[str] = field(default_factory=list)
    seeded: int = 0
    candidates: int = 0

    @property
    def empty(self) -> bool:
        return not self.hits

    def top_score(self) -> float:
        return self.hits[0].score if self.hits else 0.0

    def node_ids(self) -> List[str]:
        return [h.node_id for h in self.hits]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "cues": list(self.cues),
            "seeded": self.seeded,
            "candidates": self.candidates,
            "hits": [h.to_dict() for h in self.hits],
        }

    def render(self, *, max_tokens: int = DEFAULT_MAX_TOKENS) -> str:
        """Token-bounded markdown block for the per-turn context. Empty result => ''."""
        if not self.hits:
            return ""
        budget_chars = max(120, int(max_tokens) * CHARS_PER_TOKEN)
        lines: List[str] = ["Relevant memories (recalled by cue, not exhaustive):"]
        used = len(lines[0])

        for hit in self.hits:
            connection = ""
            if hit.links:
                connection = " (related: " + ", ".join(hit.links[:3]) + ")"
            line = f"- [{hit.category}] {hit.title}: {hit.snippet}{connection}"
            if used + len(line) + 1 > budget_chars:
                # Reserve the newline *and* the ellipsis so the total cannot exceed budget.
                remaining = budget_chars - used - 1
                if remaining > 40:
                    lines.append(line[: remaining - 1].rstrip() + "…")
                break
            lines.append(line)
            used += len(line) + 1

        return "\n".join(lines) if len(lines) > 1 else ""


def extract_cues(text: str, *, extra: Optional[Iterable[str]] = None, max_cues: int = MAX_CUES) -> List[str]:
    """Salient terms from the query: words, plus wikilink/backtick mentions, minus stopwords.

    Also emits adjacent word pairs — a phrase like "graph rendering" is a far better cue than
    either word alone, and this keeps the extractor free of any keyword table.
    """
    raw = str(text or "")
    cues: List[str] = []
    seen: set = set()

    def _add(term: str) -> None:
        cleaned = term.strip().strip(".,:;!?()[]{}<>\"'`").lower()
        if not cleaned or cleaned in seen or cleaned in _QUERY_STOPWORDS:
            return
        if len(cleaned) < 3 and not cleaned.isdigit():
            return
        seen.add(cleaned)
        cues.append(cleaned)

    # Explicit mentions first: [[node/id]], `code`, quoted phrases, and path-like tokens.
    for pattern in (r"\[\[([^\[\]]+?)\]\]", r"`([^`]+)`", r'"([^"]{3,})"'):
        for match in re.findall(pattern, raw):
            _add(match)
    for token in re.findall(r"[A-Za-z0-9_./\-]{3,}", raw):
        _add(token)

    words = [w for w in tokenize(raw, drop_stopwords=False) if w not in _QUERY_STOPWORDS and len(w) >= 3]
    for first, second in zip(words, words[1:]):
        _add(f"{first} {second}")

    for term in extra or []:
        _add(str(term))

    return cues[:max_cues]


_WIKILINK_STRIP_RE = re.compile(r"\[\[([^\[\]]*?)\]\]")


def _prose(text: str) -> str:
    """Content terms only — ``[[wikilinks]]`` are removed before term extraction.

    A link is *structure*, not prose. Leaving ``[[user/preferences]]`` in a node's text made
    that node match queries about preferences and outrank the preferences note itself, purely
    because it referenced it. Associations belong to spreading activation (index.activate),
    which is how the referrer still gets recalled — as a neighbour, ranked below the topic.
    An aliased link keeps its alias, because an alias is authored prose
    (``[[concept/x|the forgetting curve]]`` does say something).
    """
    return _WIKILINK_STRIP_RE.sub(
        lambda m: (" " + m.group(1).split("|", 1)[1] + " ") if "|" in m.group(1) else " ",
        text or "",
    )


def _prepare(nodes: Sequence[Any]) -> List[Dict[str, Any]]:
    """Per-node term sets, computed once and reused for every cue."""
    prepared: List[Dict[str, Any]] = []
    for node in nodes:
        title_text = f"{node.title} {' '.join(node.tags)}".strip()
        title_tokens = tokenize(title_text)
        body_tokens = tokenize(_prose(node.content or ""))
        prepared.append(
            {
                "node": node,
                "title_text": title_text,
                "title_uni": _keyset(title_tokens),
                "body_uni": _keyset(body_tokens),
                "title_bi": _bigram_keys(title_tokens),
                "body_bi": _bigram_keys(body_tokens),
            }
        )
    return prepared


def _matches(cue_key: str, uni: set, bi: set) -> bool:
    return cue_key in bi if " " in cue_key else cue_key in uni


def _seed_scores(
    nodes: Sequence[Any],
    query: str,
    cues: Sequence[str],
    *,
    embedder: Any = None,
) -> Dict[str, float]:
    """Seed weight per node from IDF-weighted cue matches, tempered by salience.

    Scoring, in order of what actually drives retrieval:

    1. **Cue coverage weighted by rarity (IDF).** A cue that appears in many notes
       discriminates nothing, so common cues contribute ~0 and rare cues contribute most.
       This is what makes an exact phrase like "forgetting curve" beat a vague topical echo.
    2. **Asymmetric coverage** of the query's content words by the note (overlap coefficient,
       not Jaccard) — the note is far longer than the query, and Jaccard would collapse as a
       note grows even when it contains everything asked for.
    3. **Salience** tempers the weight, so a trivial note matching the same cues loses to an
       important one.

    Without (1) and (2) the raw vector similarity of a short query against a long note is
    diluted to a few percent, every candidate fell under the injection threshold, and recall
    returned nothing even for an obviously relevant cue — which is exactly what happened
    when this was first run end to end.
    """
    entries = _prepare(nodes)
    if not entries:
        return {}

    cue_keys = [(cue, _key(cue)) for cue in cues]
    n_docs = len(entries)

    idf: Dict[str, float] = {}
    for cue, cue_key in cue_keys:
        df = sum(
            1
            for e in entries
            if _matches(cue_key, e["title_uni"], e["title_bi"]) or _matches(cue_key, e["body_uni"], e["body_bi"])
        )
        idf[cue] = max(0.0, math.log((n_docs + 1) / (df + 1)))
    if not any(idf.values()):
        # Every cue occurs in every note (tiny vault): IDF carries no information, so use
        # uniform weights rather than zeroing out all retrieval.
        idf = {cue: 1.0 for cue, _ in cue_keys}
    total_weight = sum(idf.values()) or 1.0

    query_keys = _keyset(tokenize(query))
    seeds: Dict[str, float] = {}

    for e in entries:
        matched = 0.0
        for cue, cue_key in cue_keys:
            weight = idf[cue]
            if weight <= 0.0:
                continue
            if _matches(cue_key, e["title_uni"], e["title_bi"]):
                matched += weight
            elif _matches(cue_key, e["body_uni"], e["body_bi"]):
                matched += 0.85 * weight

        cue_score = matched / total_weight
        doc_keys = e["title_uni"] | e["body_uni"]
        coverage = len(query_keys & doc_keys) / min(len(query_keys), len(doc_keys)) if query_keys and doc_keys else 0.0
        title_sim = similarity(query, e["title_text"], embedder=embedder) if e["title_text"] else 0.0
        semantic = max(title_sim, 0.7 * coverage)

        weight = max(cue_score, semantic)
        if weight <= 0.0:
            continue
        sal = max(0.0, min(1.0, e["node"].frontmatter.salience))
        seeds[e["node"].id] = weight * (0.5 + 0.5 * sal)

    return seeds


def recall(
    query: str,
    *,
    vault: Optional[BrainVault] = None,
    index: Optional[BrainIndex] = None,
    limit: int = DEFAULT_LIMIT,
    hops: int = DEFAULT_HOPS,
    spread_decay: float = DEFAULT_DECAY,
    min_score: float = DEFAULT_MIN_SCORE,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    include_dormant: bool = False,
    extra_cues: Optional[Iterable[str]] = None,
    embedder: Any = None,
    now: Optional[float] = None,
) -> RecallResult:
    """Cue-based associative recall over the vault.

    Returns a :class:`RecallResult`; an empty result is a valid, common answer meaning
    "nothing here is relevant enough to inject".
    """
    if not str(query or "").strip():
        return RecallResult()

    v = vault or BrainVault()
    idx = index if index is not None else BrainIndex(v).rebuild()
    entries = v.iter_nodes()
    if not entries:
        return RecallResult()

    cues = extract_cues(query, extra=extra_cues)
    if not cues:
        return RecallResult()

    by_id = {node_id: node for node_id, node in entries}
    eligible = [
        node
        for node_id, node in entries
        if node.frontmatter.status not in _STATUS_EXCLUDED
    ]
    seeds = _seed_scores(eligible, query, cues, embedder=embedder)
    if not seeds:
        return RecallResult(cues=cues, candidates=len(entries))

    activation = idx.activate(seeds, hops=hops, decay=spread_decay)

    hits: List[RecallHit] = []
    for node_id, act in activation.items():
        node = by_id.get(node_id)
        if node is None:
            continue
        if node.frontmatter.status in _STATUS_EXCLUDED:
            continue
        fm = node.frontmatter
        score = decay_mod.rank_score(act, fm.stability, fm.last_accessed, fm.salience, now=now)
        if score <= 0.0:
            continue
        retriev = decay_mod.retrievability(fm.stability, fm.last_accessed, now=now)
        if not include_dormant and decay_mod.is_dormant(fm.stability, fm.last_accessed, now=now):
            continue
        snippet = " ".join((node.content or "").split())
        if len(snippet) > SNIPPET_CHARS:
            snippet = snippet[:SNIPPET_CHARS].rstrip() + "…"
        hits.append(
            RecallHit(
                node_id=node_id,
                title=node.title,
                category=node.category,
                score=score,
                activation=act,
                retrievability=retriev,
                salience=fm.salience,
                snippet=snippet,
                links=[n for n in idx.links_of(node_id) if n in by_id][:4],
            )
        )

    hits.sort(key=lambda h: (-h.score, h.node_id))
    # Drop also-rans: keep only hits in the same league as the best one.
    if hits:
        floor = hits[0].score * RELATIVE_FLOOR
        hits = [h for h in hits if h.score >= floor]
    hits = hits[: max(1, int(limit))]

    # Gate: a weak best hit means the vault has nothing worth saying this turn.
    if not hits or hits[0].score < float(min_score):
        return RecallResult(cues=cues, seeded=len(seeds), candidates=len(entries))

    # Never let the rendered block exceed the budget, even if more hits were found.
    return RecallResult(hits=hits, cues=cues, seeded=len(seeds), candidates=len(entries))


def recall_block(query: str, *, max_tokens: int = DEFAULT_MAX_TOKENS, **kwargs: Any) -> str:
    """Convenience: recall and render in one call. Empty string when nothing qualifies."""
    return recall(query, max_tokens=max_tokens, **kwargs).render(max_tokens=max_tokens)
