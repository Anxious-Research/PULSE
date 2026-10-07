"""Text similarity for dedupe and consolidation.

Two backends behind one interface:

- ``LexicalHashEmbedder`` (default): deterministic hashed n-gram vectors, **zero third-party
  dependencies**. Uses ``blake2b`` so a vector is identical across processes and machines —
  Python's builtin ``hash()`` is salted per process and would make similarity unstable.
- ``SemanticEmbedder`` (optional): wraps a local embedding model (``fastembed`` or
  ``sentence-transformers``) when one is installed. PULSE's runtime ships no embedding stack,
  so this is opt-in; if it is unavailable the lexical backend is used and nothing degrades
  beyond similarity quality.

Nothing here imports an optional backend eagerly and nothing raises when one is missing —
this module must import on a bare PULSE install.

Note on the lexical backend: it measures *surface* similarity, which is exactly what a
near-duplicate check needs ("did we already store this fact?"). It is not a semantic
replacement for a real embedding model, and it does not pretend to be.
"""

from __future__ import annotations

import hashlib
import math
import re
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set

DEFAULT_DIM = 256
WORD_RE = re.compile(r"[a-z0-9]+")
_GRAM = 4
_WORD_WEIGHT = 1.0
_GRAM_WEIGHT = 0.4
DEFAULT_DUPLICATE_THRESHOLD = 0.86

_STOPWORDS = frozenset(
    """a an and are as at be by for from has have he her his i if in is it its me my not of on
    or our she that the their them there they this to was were will with you your""".split()
)


def tokenize(text: str, *, drop_stopwords: bool = True) -> List[str]:
    """Word tokens, lowercased."""
    words = WORD_RE.findall(str(text or "").lower())
    if drop_stopwords:
        filtered = [w for w in words if w not in _STOPWORDS]
        # Never return nothing just because the text was all stopwords.
        return filtered or words
    return words


def token_set(text: str) -> Set[str]:
    return set(tokenize(text))


def _bucket(token: str, dim: int) -> tuple:
    """Stable (index, sign) for a token — deterministic across processes."""
    digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
    value = int.from_bytes(digest, "big")
    return value % dim, (1.0 if (value >> 63) & 1 else -1.0)


class LexicalHashEmbedder:
    """Hashed bag-of-words + character n-grams, L2-normalised. Dependency-free."""

    name = "lexical-hashed"
    semantic = False

    def __init__(self, dim: int = DEFAULT_DIM) -> None:
        self.dim = max(16, int(dim))

    def vector(self, text: str) -> List[float]:
        vec = [0.0] * self.dim
        words = tokenize(text, drop_stopwords=False)
        if not words:
            return vec
        for word in words:
            idx, sign = _bucket(word, self.dim)
            vec[idx] += sign * _WORD_WEIGHT
        joined = " ".join(words)
        for i in range(0, max(0, len(joined) - _GRAM + 1)):
            gram = joined[i : i + _GRAM]
            if " " in gram:
                continue
            idx, sign = _bucket("#" + gram, self.dim)
            vec[idx] += sign * _GRAM_WEIGHT
        norm = math.sqrt(sum(v * v for v in vec))
        if norm <= 0:
            return vec
        return [v / norm for v in vec]


class SemanticEmbedder:
    """Optional local embedding model. Raises ``Unavailable`` only when used without one."""

    name = "semantic"
    semantic = True

    class Unavailable(RuntimeError):
        pass

    def __init__(self) -> None:
        self._model: Any = None
        self._kind: Optional[str] = None
        self._dim: Optional[int] = None

    def _load(self) -> Any:
        if self._model is not None:
            return self._model
        try:  # fastembed: small ONNX models, no torch
            from fastembed import TextEmbedding  # type: ignore

            self._model = TextEmbedding()
            self._kind = "fastembed"
            return self._model
        except Exception:
            pass
        try:
            from sentence_transformers import SentenceTransformer  # type: ignore

            self._model = SentenceTransformer("all-MiniLM-L6-v2")
            self._kind = "sentence-transformers"
            return self._model
        except Exception as exc:
            raise SemanticEmbedder.Unavailable(
                "no local embedding model available (install fastembed or sentence-transformers)"
            ) from exc

    @staticmethod
    def available() -> bool:
        try:
            import importlib.util

            return (
                importlib.util.find_spec("fastembed") is not None
                or importlib.util.find_spec("sentence_transformers") is not None
            )
        except Exception:
            return False

    def vector(self, text: str) -> List[float]:
        model = self._load()
        if self._kind == "fastembed":
            out = list(model.embed([str(text or "")]))[0]
        else:
            out = model.encode([str(text or "")], normalize_embeddings=True)[0]
        return [float(v) for v in out]


_EMBEDDER_CACHE: Dict[str, Any] = {}


def get_embedder(prefer_semantic: bool = True, dim: int = DEFAULT_DIM) -> Any:
    """Return the best available embedder (cached). Never raises."""
    key = f"semantic:{prefer_semantic}:{dim}"
    cached = _EMBEDDER_CACHE.get(key)
    if cached is not None:
        return cached
    chosen: Any = None
    if prefer_semantic and SemanticEmbedder.available():
        try:
            candidate = SemanticEmbedder()
            candidate.vector("probe")
            chosen = candidate
        except Exception:
            chosen = None
    if chosen is None:
        chosen = LexicalHashEmbedder(dim=dim)
    _EMBEDDER_CACHE[key] = chosen
    return chosen


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    """Cosine similarity in [-1, 1]; 0.0 for empty/degenerate input."""
    if not a or not b:
        return 0.0
    n = min(len(a), len(b))
    dot = sum(a[i] * b[i] for i in range(n))
    na = math.sqrt(sum(a[i] * a[i] for i in range(n)))
    nb = math.sqrt(sum(b[i] * b[i] for i in range(n)))
    if na <= 0 or nb <= 0:
        return 0.0
    return max(-1.0, min(1.0, dot / (na * nb)))


def jaccard(a_text: str, b_text: str) -> float:
    """Word-set overlap — catches short facts that hashed vectors can miss."""
    a, b = token_set(a_text), token_set(b_text)
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def overlap_coefficient(a_text_or_tokens, b_text_or_tokens) -> float:
    """|A ∩ B| / min(|A|, |B|) — coverage, not symmetric similarity.

    This is the right measure when one side is much shorter than the other, which is exactly
    the recall case: a 6-word query against a 400-word note. Jaccard divides by the *union*,
    so it collapses toward zero as the note gets longer — a note that contains every query
    term still scores poorly. The overlap coefficient asks "how much of the shorter side is
    found in the longer side", so a short query that is fully covered scores 1.0 regardless
    of document length.
    """
    a = token_set(a_text_or_tokens) if isinstance(a_text_or_tokens, str) else set(a_text_or_tokens)
    b = token_set(b_text_or_tokens) if isinstance(b_text_or_tokens, str) else set(b_text_or_tokens)
    if not a or not b:
        return 0.0
    return len(a & b) / min(len(a), len(b))


def bigrams(tokens: Iterable[str]) -> Set[str]:
    """Adjacent word pairs as ``{"first second", ...}`` — lets a phrase cue be matched."""
    seq = list(tokens)
    return {f"{a} {b}" for a, b in zip(seq, seq[1:])}


def similarity(a_text: str, b_text: str, *, embedder: Any = None) -> float:
    """Blended similarity in [0, 1]: vector cosine plus word overlap."""
    if not str(a_text or "").strip() or not str(b_text or "").strip():
        return 0.0
    emb = embedder if embedder is not None else get_embedder()
    cos = max(0.0, cosine(emb.vector(a_text), emb.vector(b_text)))
    jac = jaccard(a_text, b_text)
    return max(0.0, min(1.0, 0.65 * cos + 0.35 * jac))


def is_near_duplicate(
    a_text: str,
    b_text: str,
    *,
    threshold: float = DEFAULT_DUPLICATE_THRESHOLD,
    embedder: Any = None,
) -> bool:
    """True when two entries should be merged rather than stored twice."""
    return similarity(a_text, b_text, embedder=embedder) >= float(threshold)


def best_match(
    needle: str,
    haystack: Iterable[str],
    *,
    threshold: float = 0.0,
    embedder: Any = None,
) -> Optional[tuple]:
    """``(best_text, score)`` of the most similar candidate, or ``None`` if none clears it."""
    emb = embedder if embedder is not None else get_embedder()
    needle_vec = emb.vector(needle)
    best: Optional[tuple] = None
    for candidate in haystack:
        score = max(0.0, cosine(needle_vec, emb.vector(candidate))) * 0.65 + 0.35 * jaccard(needle, candidate)
        if best is None or score > best[1]:
            best = (candidate, score)
    if best is None or best[1] < float(threshold):
        return None
    return best
