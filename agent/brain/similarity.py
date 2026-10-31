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

import array
import hashlib
import json
import logging
import math
import re
import urllib.error
import urllib.request
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple, Union

logger = logging.getLogger(__name__)

DEFAULT_DIM = 256
WORD_RE = re.compile(r"[a-z0-9]+")
_GRAM = 4
_WORD_WEIGHT = 1.0
_GRAM_WEIGHT = 0.4
DEFAULT_DUPLICATE_THRESHOLD = 0.86

_STOPWORDS = frozenset(
    """a an and are as at be by for from has have he her his i if in is it its me my not of on
    or our she that the their them there they this to was were will with you your""".split()
) | frozenset(
    # Filler/function words that carry no task content. Without these, two different methods of
    # the same task ("deploy via ftp" / "deploy via docker") share the filler and score as near-
    # identical, merging distinct procedures. Kept to unambiguous function words only.
    """via using into onto per then also please across within without toward towards""".split()
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


class LexicalBM25Embedder:
    """Lexical embedder combining BM25 term frequency saturation with blake2b hashed projections.

    Zero third-party dependencies, deterministic across processes and machines.
    Saturates term frequencies so repeated common words cannot dominate the representation.
    """

    name = "lexical-bm25"
    semantic = False

    def __init__(self, dim: int = DEFAULT_DIM, k1: float = 1.5) -> None:
        self.dim = max(16, int(dim))
        self.k1 = float(k1)

    def vector(self, text: str) -> List[float]:
        vec = [0.0] * self.dim
        words = tokenize(text, drop_stopwords=False)
        if not words:
            return vec
        tf: Dict[str, int] = {}
        for w in words:
            tf[w] = tf.get(w, 0) + 1
        for word, count in tf.items():
            idx, sign = _bucket(word, self.dim)
            bm25_weight = (count * (self.k1 + 1.0)) / (count + self.k1)
            vec[idx] += sign * bm25_weight * _WORD_WEIGHT
        joined = " ".join(words)
        gram_tf: Dict[str, int] = {}
        for i in range(0, max(0, len(joined) - _GRAM + 1)):
            gram = joined[i : i + _GRAM]
            if " " in gram:
                continue
            gram_tf[gram] = gram_tf.get(gram, 0) + 1
        for gram, count in gram_tf.items():
            idx, sign = _bucket("#" + gram, self.dim)
            bm25_weight = (count * (self.k1 + 1.0)) / (count + self.k1)
            vec[idx] += sign * bm25_weight * _GRAM_WEIGHT
        norm = math.sqrt(sum(v * v for v in vec))
        if norm <= 0:
            return vec
        return [v / norm for v in vec]


def serialize_vector(vec: Sequence[float] | bytes) -> bytes:
    """Serialize a float vector into float32 binary bytes."""
    if isinstance(vec, (bytes, bytearray, memoryview)):
        return bytes(vec)
    arr = array.array("f", [float(x) for x in vec])
    return arr.tobytes()


def deserialize_vector(blob: bytes) -> List[float]:
    """Deserialize float32 binary bytes into a list of floats."""
    if not blob:
        return []
    arr = array.array("f")
    arr.frombytes(blob)
    return arr.tolist()


def _get_secret_safe(name: str) -> str:
    """Read a credential safely via profile secret scope or os.environ."""
    try:
        from agent.secret_scope import get_secret

        val = get_secret(name)
        if val:
            return str(val).strip()
    except Exception:
        pass
    import os

    return str(os.environ.get(name, "")).strip()


def _get_embedding_config() -> Dict[str, Any]:
    """Read embedding config from brain section of config.yaml safely."""
    try:
        from pulse_cli.config import load_config_readonly

        cfg = load_config_readonly() or {}
        brain = cfg.get("brain")
        if isinstance(brain, dict):
            return brain
    except Exception:
        pass
    return {}


def _resolve_provider_credentials(
    provider: Optional[str] = None,
    model: Optional[str] = None,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
) -> Tuple[str, str, str, str]:
    """Resolve (provider_name, model_name, api_key, base_url).

    Returns ("", "", "", "") if no provider credentials can be found.
    """
    brain_cfg = _get_embedding_config()
    p = provider or brain_cfg.get("embedding_provider") or ""
    m = model or brain_cfg.get("embedding_model") or ""
    k = api_key or brain_cfg.get("embedding_api_key") or ""
    u = base_url or brain_cfg.get("embedding_base_url") or ""

    p_norm = p.strip().lower() if p else ""

    if p_norm == "openai":
        resolved_key = k or _get_secret_safe("OPENAI_API_KEY")
        resolved_url = u or _get_secret_safe("OPENAI_BASE_URL") or "https://api.openai.com/v1"
        resolved_model = m or "text-embedding-3-small"
        if resolved_key:
            return ("openai", resolved_model, resolved_key, resolved_url)

    elif p_norm == "openrouter":
        resolved_key = (
            k
            or _get_secret_safe("OPENROUTER_API_KEY")
            or _get_secret_safe("OPENAI_API_KEY")
        )
        resolved_url = (
            u
            or _get_secret_safe("OPENROUTER_BASE_URL")
            or "https://openrouter.ai/api/v1"
        )
        resolved_model = m or "openai/text-embedding-3-small"
        if resolved_key:
            return ("openrouter", resolved_model, resolved_key, resolved_url)

    elif p_norm:
        resolved_key = (
            k
            or _get_secret_safe(f"{p_norm.upper()}_API_KEY")
            or _get_secret_safe("OPENAI_API_KEY")
        )
        resolved_url = (
            u
            or _get_secret_safe(f"{p_norm.upper()}_BASE_URL")
            or "https://api.openai.com/v1"
        )
        resolved_model = m or "text-embedding-3-small"
        if resolved_key:
            return (p_norm, resolved_model, resolved_key, resolved_url)

    # Auto-detection when provider is not explicitly set
    if not p_norm:
        openai_key = k or _get_secret_safe("OPENAI_API_KEY")
        if openai_key:
            resolved_url = u or _get_secret_safe("OPENAI_BASE_URL") or "https://api.openai.com/v1"
            resolved_model = m or "text-embedding-3-small"
            return ("openai", resolved_model, openai_key, resolved_url)

        openrouter_key = k or _get_secret_safe("OPENROUTER_API_KEY")
        if openrouter_key:
            resolved_url = u or _get_secret_safe("OPENROUTER_BASE_URL") or "https://openrouter.ai/api/v1"
            resolved_model = m or "openai/text-embedding-3-small"
            return ("openrouter", resolved_model, openrouter_key, resolved_url)

    return ("", "", "", "")


def _fetch_remote_embeddings(
    texts: Sequence[str],
    *,
    model: str,
    api_key: str,
    base_url: str,
    timeout: float = 15.0,
) -> List[List[float]]:
    """Fetch embeddings from an OpenAI-compatible /embeddings endpoint."""
    cleaned = [str(t or "") for t in texts]
    if not cleaned:
        return []
    url = base_url.rstrip("/")
    if not url.endswith("/embeddings"):
        url = f"{url}/embeddings"

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "User-Agent": "PULSE-Agent/Brain",
    }
    if "openrouter" in url.lower() or "openrouter" in model.lower():
        headers["HTTP-Referer"] = "https://pulse-agent.anxious-research.com"
        headers["X-Title"] = "PULSE Agent"

    payload = {
        "input": cleaned if len(cleaned) > 1 else cleaned[0],
        "model": model,
    }

    req_data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=req_data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw_body = resp.read().decode("utf-8")
            res = json.loads(raw_body)
    except Exception as exc:
        raise RuntimeError(f"Embedding request to {url} failed: {exc}") from exc

    data_list = res.get("data", [])
    if not isinstance(data_list, list) or not data_list:
        raise RuntimeError(f"Unexpected response from embedding API: {res}")

    if len(data_list) > 1:
        data_list = sorted(data_list, key=lambda x: x.get("index", 0))

    results: List[List[float]] = []
    for item in data_list:
        raw_emb = item.get("embedding", [])
        vec = [float(x) for x in raw_emb]
        norm = math.sqrt(sum(x * x for x in vec))
        if norm > 0:
            vec = [x / norm for x in vec]
        results.append(vec)
    return results


class SemanticEmbedder:
    """Robust, pluggable semantic embedding pipeline.

    Cascade:
      1. Local fast inference: fastembed (e.g. BAAI/bge-small-en) or sentence-transformers (all-MiniLM-L6-v2)
         or custom onnxruntime.
      2. Provider fallback: remote API (OpenAI / OpenRouter text-embedding-3-small) via credentials
         configured in brain.embedding_provider or environment, with LRU vector caching.
      3. Clean lexical fallback: BM25 + blake2b hashed projections when no provider or model
         is available, guaranteeing zero crashes.
    """

    name = "semantic"

    class Unavailable(RuntimeError):
        pass

    def __init__(
        self,
        model_name: Optional[str] = None,
        provider: Optional[str] = None,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        dim: Optional[int] = None,
        cache_size: int = 2048,
        fallback_to_lexical: bool = True,
        model: Any = None,
    ) -> None:
        self.model_name = model_name
        self.provider = provider
        self.api_key = api_key
        self.base_url = base_url
        self._configured_dim = dim
        self._dim = dim
        self._cache_size = max(16, int(cache_size))
        self._fallback_to_lexical = fallback_to_lexical
        self._cache: Dict[str, List[float]] = {}
        self._lexical_fallback = LexicalBM25Embedder(dim=dim or DEFAULT_DIM)

        self._model: Any = model
        self._kind: Optional[str] = None
        self.semantic: bool = True

        if model is not None:
            self._kind = "custom"
            self.semantic = True
        else:
            self._load()

    def _load(self) -> None:
        if self._kind is not None:
            return

        # Tier 1: Local fast inference (fastembed / sentence-transformers / onnxruntime)
        if self.provider is None:
            # Try fastembed
            try:
                from fastembed import TextEmbedding  # type: ignore

                m_name = self.model_name or "BAAI/bge-small-en-v1.5"
                self._model = TextEmbedding(model_name=m_name)
                self._kind = "fastembed"
                self.model_name = m_name
                self.semantic = True
                self._dim = 384
                return
            except Exception:
                pass

            # Try sentence-transformers
            try:
                from sentence_transformers import SentenceTransformer  # type: ignore

                m_name = self.model_name or "all-MiniLM-L6-v2"
                self._model = SentenceTransformer(m_name)
                self._kind = "sentence-transformers"
                self.model_name = m_name
                self.semantic = True
                self._dim = 384
                return
            except Exception:
                pass

        # Tier 2: Provider fallback (e.g. OpenAI / OpenRouter text-embedding-3-small)
        p, m, k, u = _resolve_provider_credentials(
            provider=self.provider,
            model=self.model_name,
            api_key=self.api_key,
            base_url=self.base_url,
        )
        if p and k:
            self.provider = p
            self.model_name = m
            self.api_key = k
            self.base_url = u
            self._kind = p
            self.semantic = True
            if self._dim is None:
                if "small" in m or "1536" in m:
                    self._dim = 1536
                elif "large" in m or "3072" in m:
                    self._dim = 3072
            return

        # Tier 3: Clean lexical fallback
        if self._fallback_to_lexical:
            self._kind = "lexical-fallback"
            self.semantic = False
            self._dim = self._configured_dim or DEFAULT_DIM
            return

        raise SemanticEmbedder.Unavailable(
            "no local embedding model available and no provider credentials configured"
        )

    @property
    def kind(self) -> str:
        if self._kind is None:
            try:
                self._load()
            except Exception:
                return "unknown"
        return self._kind or "unknown"

    @property
    def dim(self) -> int:
        return self._dim or self._configured_dim or DEFAULT_DIM

    def clear_cache(self) -> None:
        """Clear memory cache of generated embeddings."""
        self._cache.clear()

    @staticmethod
    def available() -> bool:
        return SemanticEmbedder.local_available() or SemanticEmbedder.provider_available()

    @staticmethod
    def local_available() -> bool:
        try:
            import importlib.util

            return (
                importlib.util.find_spec("fastembed") is not None
                or importlib.util.find_spec("sentence_transformers") is not None
            )
        except Exception:
            return False

    @staticmethod
    def provider_available() -> bool:
        try:
            p, _, k, _ = _resolve_provider_credentials()
            return bool(p and k)
        except Exception:
            return False

    def vector(self, text: str) -> List[float]:
        cleaned = str(text or "").strip()
        if not cleaned:
            return [0.0] * self.dim

        self._load()

        cache_key = f"{self._kind}:{self.model_name}:{cleaned}"
        if cache_key in self._cache:
            return self._cache[cache_key]

        vec: Optional[List[float]] = None

        if self._kind == "custom" and self._model is not None:
            if hasattr(self._model, "vector"):
                vec = self._model.vector(cleaned)
            elif hasattr(self._model, "encode"):
                out = self._model.encode([cleaned], normalize_embeddings=True)
                vec = [float(v) for v in out[0]]
            elif hasattr(self._model, "embed"):
                out = list(self._model.embed([cleaned]))
                vec = [float(v) for v in out[0]]
            elif callable(self._model):
                raw_out = self._model(cleaned)
                if isinstance(raw_out, (list, tuple)):
                    vec = [float(v) for v in raw_out]
                elif hasattr(raw_out, "__iter__"):
                    vec = [float(v) for v in raw_out]  # type: ignore[union-attr]

        elif self._kind == "fastembed" and self._model is not None:
            out = list(self._model.embed([cleaned]))[0]
            vec = [float(v) for v in out]

        elif self._kind == "sentence-transformers" and self._model is not None:
            out = self._model.encode([cleaned], normalize_embeddings=True)[0]
            vec = [float(v) for v in out]

        elif self._kind in ("openai", "openrouter") or (self.provider and self.api_key):
            try:
                vecs = _fetch_remote_embeddings(
                    [cleaned],
                    model=self.model_name or "text-embedding-3-small",
                    api_key=self.api_key or "",
                    base_url=self.base_url or "https://api.openai.com/v1",
                )
                if vecs:
                    vec = vecs[0]
            except Exception as exc:
                if self._fallback_to_lexical:
                    logger.debug("Provider embedding failed, falling back to lexical: %s", exc)
                    vec = self._lexical_fallback.vector(cleaned)
                else:
                    raise

        if vec is None:
            vec = self._lexical_fallback.vector(cleaned)

        norm = math.sqrt(sum(v * v for v in vec))
        if norm > 0:
            vec = [v / norm for v in vec]

        if self._dim is None or self._dim != len(vec):
            self._dim = len(vec)

        if len(self._cache) >= self._cache_size:
            first_key = next(iter(self._cache))
            self._cache.pop(first_key, None)
        self._cache[cache_key] = vec

        return vec

    def vector_batch(self, texts: Sequence[str]) -> List[List[float]]:
        """Vectorize multiple texts."""
        return [self.vector(t) for t in texts]


_EMBEDDER_CACHE: Dict[str, Any] = {}


def clear_embedder_cache() -> None:
    """Clear cached embedder instances (useful in tests and config changes)."""
    _EMBEDDER_CACHE.clear()


def get_embedder(prefer_semantic: bool = True, dim: int = DEFAULT_DIM, **kwargs: Any) -> Any:
    """Return the best available embedder (cached). Never raises."""
    kw_tuple = tuple(sorted((k, str(v)) for k, v in kwargs.items()))
    key = f"semantic:{prefer_semantic}:{dim}:{kw_tuple}"
    cached = _EMBEDDER_CACHE.get(key)
    if cached is not None:
        return cached
    chosen: Any = None
    if prefer_semantic and SemanticEmbedder.available():
        try:
            candidate = SemanticEmbedder(dim=dim, **kwargs)
            candidate.vector("probe")
            chosen = candidate
        except Exception:
            chosen = None
    if chosen is None:
        chosen = LexicalHashEmbedder(dim=dim)
    _EMBEDDER_CACHE[key] = chosen
    return chosen


def cosine(
    a: Sequence[float] | bytes,
    b: Sequence[float] | bytes,
) -> float:
    """Cosine similarity in [-1, 1]; 0.0 for empty/degenerate input.

    Accepts sequences of floats or serialized float32 binary blobs.
    """
    if isinstance(a, (bytes, bytearray, memoryview)):
        arr_a = array.array("f")
        arr_a.frombytes(a)
        a = arr_a
    if isinstance(b, (bytes, bytearray, memoryview)):
        arr_b = array.array("f")
        arr_b.frombytes(b)
        b = arr_b
    if not a or not b:
        return 0.0
    n = min(len(a), len(b))
    if n == 0:
        return 0.0
    dot = 0.0
    na = 0.0
    nb = 0.0
    for i in range(n):
        ai = a[i]
        bi = b[i]
        dot += ai * bi
        na += ai * ai
        nb += bi * bi
    if na <= 0.0 or nb <= 0.0:
        return 0.0
    return max(-1.0, min(1.0, dot / (math.sqrt(na) * math.sqrt(nb))))


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
