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

import hashlib
import math
import re
import threading
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from . import decay as decay_mod
from .index import BrainIndex
from .models import NodeStatus
from .parser import normalize_node_id, strip_title_overlap, wikilinks_to_text
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
# A standalone embedding match must clear this to seed a node with no lexical support.
# Rationale: the default embedder is a zero-dependency lexical placeholder, and it scores
# unrelated *short* strings well above zero — measured at 0.135 between a query and a two-word
# unrelated title. That was enough to seed, and therefore inject, notes with no connection to
# the cue. Lexical evidence (cue overlap) is what admits a node; an embedding may raise its
# weight but not invent the hit. A genuine semantic embedder clears this floor on a real
# synonym match, so the optional-backend seam keeps working.
SEMANTIC_ONLY_FLOOR = 0.45
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
    stripped = _WIKILINK_STRIP_RE.sub(
        lambda m: (" " + m.group(1).split("|", 1)[1] + " ") if "|" in m.group(1) else " ",
        text or "",
    )
    # A removed link can leave dangling whitespace before punctuation ("lives in [[x]]." ->
    # "lives in ."), which then shows up verbatim in the prompt block.
    return re.sub(r"\s+([.,;:!?])", r"\1", stripped)


def _node_terms(node: Any) -> Dict[str, Any]:
    """The token/term sets for one node — everything the scorer needs, minus the node itself."""
    title_text = f"{node.title} {' '.join(node.tags)}".strip()
    title_tokens = tokenize(title_text)
    body_tokens = tokenize(_prose(node.content or ""))
    return {
        "title_text": title_text,
        "title_uni": _keyset(title_tokens),
        "body_uni": _keyset(body_tokens),
        "title_bi": _bigram_keys(title_tokens),
        "body_bi": _bigram_keys(body_tokens),
    }


def _content_digest(node: Any) -> str:
    """Digest of what the term sets depend on: title, tags, body. Cheap, stable, collision-safe."""
    h = hashlib.blake2b(digest_size=16)
    h.update(str(getattr(node, "title", "") or "").encode("utf-8", "ignore"))
    h.update(b"\x00")
    h.update(" ".join(getattr(node, "tags", None) or []).encode("utf-8", "ignore"))
    h.update(b"\x00")
    h.update((getattr(node, "content", "") or "").encode("utf-8", "ignore"))
    return h.hexdigest()


# Prepared term sets, keyed by (node_id, content-digest). Recall used to re-tokenize EVERY node
# on every turn — an O(vault) cost that defeats the point of a growing memory. Caching the
# per-node term sets makes a turn pay only for nodes that changed since the last turn, with
# byte-identical scores (the sets are pure functions of title+tags+body). Bounded LRU so a very
# large vault cannot make recall an unbounded RAM consumer: evicted nodes are simply re-tokenized.
_PREPARE_CACHE: "OrderedDict[Tuple[str, str], Dict[str, Any]]" = OrderedDict()
_PREPARE_CACHE_MAX = 40000
_PREPARE_LOCK = threading.Lock()


def _prepared_entry(node: Any) -> Dict[str, Any]:
    """Term sets for *node*, from the cache when unchanged."""
    key = (node.id, _content_digest(node))
    with _PREPARE_LOCK:
        entry = _PREPARE_CACHE.get(key)
        if entry is not None:
            _PREPARE_CACHE.move_to_end(key)
    if entry is None:
        entry = _node_terms(node)
        with _PREPARE_LOCK:
            _PREPARE_CACHE[key] = entry
            while len(_PREPARE_CACHE) > _PREPARE_CACHE_MAX:
                _PREPARE_CACHE.popitem(last=False)
    return {**entry, "node": node}


def _prepare(nodes: Sequence[Any]) -> List[Dict[str, Any]]:
    """Per-node term sets, computed once per node and reused across cues AND turns."""
    return [_prepared_entry(node) for node in nodes]


def _matches(cue_key: str, uni: set, bi: set) -> bool:
    return cue_key in bi if " " in cue_key else cue_key in uni


def _entries_from_index(index: Any, vault: BrainVault) -> List[tuple]:
    """``[(node_id, node)]`` from the built index when it belongs to *vault*.

    A passed index is authoritative for its own vault; the vault-dir match guards against a
    caller handing a mismatched pair, in which case we fall back to a real disk read.
    """
    try:
        index_vault = getattr(index, "vault", None)
        if index_vault is not None and getattr(index_vault, "vault_dir", object()) == getattr(vault, "vault_dir", None):
            entries = index.entries()
            if entries:
                return entries
    except Exception:
        pass
    return vault.iter_nodes()


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
    df_counts: Dict[str, int] = {}
    for cue, cue_key in cue_keys:
        df = sum(
            1
            for e in entries
            if _matches(cue_key, e["title_uni"], e["title_bi"]) or _matches(cue_key, e["body_uni"], e["body_bi"])
        )
        df_counts[cue] = df

    seen = [cue for cue, df in df_counts.items() if df]
    if seen:
        raw = {cue: math.log((n_docs + 1) / (df_counts[cue] + 1)) for cue in seen}
        best = max(raw.values())
        if best <= 0.0:
            # Every matching cue appears in every note — which is always true in a one-note
            # vault, and is where a fresh vault starts. IDF cannot discriminate here (every
            # log is 0), and treating that as "no signal" would make the very first note the
            # user writes permanently unrecallable. Weight the matching cues uniformly instead.
            for cue in seen:
                idf[cue] = 1.0
            best = 1.0
        else:
            idf.update(raw)
    else:
        best = 1.0

    # A cue that matches NOTHING in the vault is evidence of partial coverage, not the strongest
    # signal in the query. Plain IDF peaks at df=0 — log((N+1)/1) is its maximum — which makes
    # the noisiest terms (a typo, a word this vault simply does not use, an over-specific phrase)
    # the most influential. Their weight then dominates the denominator and dilutes the cues
    # that DO match, so a clearly relevant note scores below the injection gate and recall goes
    # silent. Scaling unseen terms to a quarter of the best real match keeps partial coverage
    # penalised without letting it swamp a genuine hit.
    unseen = 0.25 * best
    for cue, df in df_counts.items():
        if not df:
            idf[cue] = unseen

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
        coverage = overlap_coefficient(query_keys, e["title_uni"] | e["body_uni"])
        # Lexical evidence is the admission ticket: no cue overlap and no coverage means this
        # note is not a direct hit for the cue, whatever an embedding thinks.
        weight = max(cue_score, 0.7 * coverage)
        if e["title_text"]:
            title_sim = similarity(query, e["title_text"], embedder=embedder)
            if title_sim >= SEMANTIC_ONLY_FLOOR:
                weight = max(weight, title_sim)
        if weight <= 0.0:
            continue
        # Salience deliberately does NOT scale the seed. It is applied once, in
        # ``decay.rank_score``, exactly as specs/brain.md §3.4 specifies
        # (activation x retrievability x salience). Applying it here as well squared its effect:
        # a default-salience note was attenuated to 0.75 x 0.625 = 47% of its activation, which
        # pushed genuinely relevant hits under the injection gate.
        seeds[e["node"].id] = weight

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
    # Reuse the nodes the index already read+parsed. Calling ``v.iter_nodes()`` here re-read and
    # re-parsed the whole vault on every turn even though the index had just done exactly that.
    entries = _entries_from_index(idx, v)
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
        # The snippet is injected into a prompt, so it must read as prose: ``[[a/b|c]]`` -> ``c``.
        # Bracket syntax and path separators are vault plumbing, and the associations they encode
        # are already surfaced by the ``related:`` line. Leaving them in spends tokens on
        # punctuation and reads as noise (or worse, as a literal instruction to fetch a path).
        snippet = strip_title_overlap(" ".join(wikilinks_to_text(node.content).split()), node.title)
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
