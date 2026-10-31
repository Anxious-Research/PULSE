"""Hybrid retrieval channels and rank fusion (specs/brain.md §3.4, §6, §9.4).

The production recall path (``recall.recall``) is a single pipeline: cue seeding → spreading
activation → decay-aware ranking → gate. It is good at *associative* recall (a linked memory
surfaces even without a direct cue) but it scores direct lexical matches with a bespoke
cue-coverage measure rather than a calibrated term-weighting model, and it has no independent
semantic channel.

This module adds the missing *independently-evaluated* channels and a principled way to merge
them, without discarding anything the existing pipeline does well:

* **Lexical / BM25** — Okapi BM25 over tokenised title+body. A genuinely different lexical signal
  from the cue seeder: BM25 has term-frequency saturation (``k1``) and document-length
  normalisation (``b``), so it ranks a focused note above a long one that merely mentions a term
  once. Dependency-free, deterministic.
* **Semantic** — cosine over real embedding vectors, **only when a real semantic model is
  installed** (``fastembed``/``sentence-transformers``). When it is not, this channel returns
  nothing: we never substitute the lexical-hash placeholder for a trained embedder and call it
  "semantic". That keeps the lightweight fallback honest (the §9.4 bar).
* **Associative / graph** — the existing spreading-activation ranking, passed in as a channel.
* **Temporal / historical** — the existing deep/dormant route in ``recall``; preserved, not
  replaced (hybrid reorders what recall admits, so historical hits keep their place).

The channels are merged with **Reciprocal Rank Fusion** (RRF, Cormack et al. 2009): each channel
contributes ``1/(k + rank)`` to a document's fused score. RRF needs no score calibration across
channels (BM25 scores and cosine similarities are not comparable), is robust to a channel being
empty, and is the standard justified-and-tested fusion method the mandate asks for. Candidates are
deduplicated by node id as a natural consequence of summing per-document contributions.

Nothing here writes to the vault or imports an optional backend eagerly; it imports on a bare
install and degrades to BM25 + associative when no embedder is present.
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .similarity import cosine, tokenize

# Okapi BM25 parameters — the literature defaults; k1 in [1.2, 2.0], b = 0.75.
BM25_K1 = 1.5
BM25_B = 0.75
# RRF constant. 60 is the value from the original paper and the common default; it damps the
# influence of a single channel's top rank so no one channel dominates the fusion.
RRF_K = 60

Ranked = List[Tuple[str, float]]


def _doc_tokens(node: Any) -> List[str]:
    """Content tokens for one node — title plus body, stopwords dropped (deterministic)."""
    text = f"{getattr(node, 'title', '') or ''} {getattr(node, 'content', '') or ''}"
    return tokenize(text)


def bm25_rank(entries: Sequence[Tuple[str, Any]], query: str, *, limit: int = 50) -> Ranked:
    """Rank ``(node_id, node)`` entries against ``query`` by Okapi BM25.

    Returns ``[(node_id, score), ...]`` highest first, scores > 0 only. Deterministic: ties break
    on node id. An empty query, empty corpus, or a query whose terms never appear returns ``[]``.
    """
    docs: List[Tuple[str, List[str]]] = [(nid, _doc_tokens(node)) for nid, node in entries]
    docs = [(nid, toks) for nid, toks in docs if toks]
    n_docs = len(docs)
    if not n_docs:
        return []
    avgdl = sum(len(toks) for _, toks in docs) / n_docs

    df: Dict[str, int] = {}
    for _, toks in docs:
        for term in set(toks):
            df[term] = df.get(term, 0) + 1

    q_terms = [t for t in dict.fromkeys(tokenize(query)) if t in df]
    if not q_terms:
        return []

    idf = {
        t: math.log(1.0 + (n_docs - df[t] + 0.5) / (df[t] + 0.5))
        for t in q_terms
    }

    scored: Ranked = []
    for nid, toks in docs:
        dl = len(toks)
        tf: Dict[str, int] = {}
        for t in toks:
            tf[t] = tf.get(t, 0) + 1
        score = 0.0
        for t in q_terms:
            f = tf.get(t, 0)
            if not f:
                continue
            denom = f + BM25_K1 * (1.0 - BM25_B + BM25_B * dl / avgdl)
            score += idf[t] * (f * (BM25_K1 + 1.0)) / denom
        if score > 0.0:
            scored.append((nid, score))
    scored.sort(key=lambda x: (-x[1], x[0]))
    return scored[: max(1, int(limit))]


def semantic_available(embedder: Any = None) -> bool:
    """True only when a *real* trained semantic embedder is in hand.

    The lexical-hash placeholder sets ``semantic = False``; a real model sets it ``True``. This is
    the single gate that stops the hybrid path from dressing up hash similarity as semantics.
    """
    if embedder is None:
        return False
    return bool(getattr(embedder, "semantic", False))


def semantic_rank(
    entries: Sequence[Tuple[str, Any]],
    query: str,
    *,
    limit: int = 50,
    embedder: Any = None,
) -> Ranked:
    """Rank entries by embedding cosine — **only** with a real semantic model, else ``[]``.

    This deliberately does not fall back to the lexical-hash embedder: a hash-similarity ranking
    is not a semantic ranking, and pretending otherwise is exactly what the mandate forbids. The
    caller keeps BM25 + associative recall when this returns empty, so retrieval never degrades
    below the lightweight baseline.
    """
    if not semantic_available(embedder) or not str(query or "").strip():
        return []
    try:
        q_vec = embedder.vector(query)
    except Exception:
        return []
    scored: Ranked = []
    for nid, node in entries:
        text = f"{getattr(node, 'title', '') or ''} {getattr(node, 'content', '') or ''}".strip()
        if not text:
            continue
        try:
            sim = cosine(q_vec, embedder.vector(text))
        except Exception:
            continue
        if sim > 0.0:
            scored.append((nid, sim))
    scored.sort(key=lambda x: (-x[1], x[0]))
    return scored[: max(1, int(limit))]


def rrf_fuse(ranked_lists: Sequence[Ranked], *, k: int = RRF_K) -> Ranked:
    """Reciprocal-rank fusion of several ranked lists → one fused ranking.

    Each list contributes ``1/(k + rank)`` (rank 0-based) to each node it ranks; contributions
    sum across channels, so a node ranked well by several channels rises. Tied scores within
    a channel share the same rank. Empty lists are ignored.
    Deduplicates by node id (summing is per-id). Deterministic: ties break on node id.
    """
    fused: Dict[str, float] = {}
    for lst in ranked_lists:
        last_score: Optional[float] = None
        current_rank = 0
        for i, (nid, score) in enumerate(lst):
            try:
                s = float(score)
            except (TypeError, ValueError):
                s = 0.0
            if last_score is not None and abs(s - last_score) > 1e-9:
                current_rank = i
            last_score = s
            fused[nid] = fused.get(nid, 0.0) + 1.0 / (k + current_rank + 1)
    return sorted(fused.items(), key=lambda x: (-x[1], x[0]))
