"""Contradiction detection — which existing note a new fact supersedes (specs/brain.md §3.5).

If new information contradicts an existing node, the old node is **superseded**, not orphaned:
``status: superseded``, ``superseded_by: [[new-node]]``, kept in place for history. Corrections
from the user always supersede. This is the mechanism that fixes "galat direction me knowledge
store kar raha hai".

Deliberately lexical and explainable. Two notes contradict when they are *about the same thing*
but *assert the opposite* of it:

- **Same thing** — their topic signatures overlap heavily. The signature is content tokens with
  stopwords, negation markers and bare numbers removed, because "PULSE does not grow without
  bound" and "PULSE grows without bound" share every content word; the disagreement lives in the
  negations, which must not count towards sameness.
- **Opposite** — either the negation parity differs (one is negated, the other is not), or both
  state numbers and the numbers differ ("recall budget is 600" vs "recall budget is 800").

High overall similarity with *matching* polarity is a near-duplicate, not a contradiction — that
is consolidation's job (``is_near_duplicate``), and treating it as a correction would make every
restatement supersede its own source.

**Scope, stated plainly:** the comparison is lexical and same-language. A correction written in
Hinglish will supersede a Hinglish note; it will *not* supersede the same claim written in
English, because nothing here understands that "hona chahiye" and "should happen" are the same
assertion. Making that work needs the semantic embedder seam, and pretending it already works
would be worse than the limitation. Cross-language claims are still *encoded* (the correction
signal is language-aware), so nothing is lost — it simply is not yet superseded.
"""

from __future__ import annotations

import re
from typing import Any, Iterable, List, Optional, Set, Tuple

from .similarity import _STOPWORDS, tokenize

# Negation / reversal markers. Matched as whole tokens so "no" does not fire inside "notion",
# and so a word like "cannot" is covered by its parts ("can" is a stopword, "not" is not).
NEGATION_TOKENS = frozenset(
    {
        "not",
        "no",
        "never",
        "none",
        "nothing",
        "dont",
        "doesnt",
        "didnt",
        "isnt",
        "arent",
        "wasnt",
        "werent",
        "wont",
        "cant",
        "couldnt",
        "shouldnt",
        "wouldnt",
        "hasnt",
        "havent",
        "stop",
        "avoid",
        "without",
        "instead",
        "rather",
        "nor",
        "wrong",
        "incorrect",
        "false",
        # Hinglish, because this user writes in it and a correction in Hinglish must still land.
        "nahi",
        "nahin",
        "mat",
        "band",
        "chhodo",
        "hata",
        "galat",
    }
)

# Auxiliary and light verbs carry no topic. "does not delete" and "deletes" are the same claim,
# so leaving "does" in the signature would dilute overlap between a claim and its negation —
# the one comparison this module exists to make.
AUXILIARIES = frozenset(
    {
        "be", "am", "is", "are", "was", "were", "been", "being",
        "do", "does", "did", "done", "doing",
        "have", "has", "had", "having",
        "will", "would", "shall", "should", "can", "could", "may", "might", "must",
        "s", "t", "d", "ll", "ve", "re", "m",
        "hai", "hain", "tha", "thi", "ho", "hoga", "hona",
    }
)


def _stem(token: str) -> str:
    """Deliberately shallow English suffix stripping.

    Real data writes the same fact inflected differently — a note says "deletes", the correction
    says "delete" — and without this the topic signatures do not overlap and the contradiction is
    missed entirely. This is not a Porter stemmer: it handles plurals, third-person ``-s``, past
    ``-ed`` and progressive ``-ing``, which is what ordinary prose uses, and stops there. A full
    stemmer is a dependency and an accuracy trade this module does not need.
    """
    if len(token) <= 3:
        return token
    for suffix in ("ing", "ed"):
        if token.endswith(suffix) and len(token) - len(suffix) >= 3:
            token = token[: -len(suffix)]
            # "stopping" -> "stopp" -> "stop"; an undoubled consonant is the common case.
            if len(token) > 3 and token[-1] == token[-2] and token[-1] not in "aeiou":
                token = token[:-1]
            return token
    if token.endswith("ies") and len(token) >= 5:
        return token[:-3] + "y"
    if token.endswith("ss"):
        return token
    if token.endswith("es") and len(token) >= 5 and token[-3] in "sxz":
        return token[:-2]
    if token.endswith("s"):
        return token[:-1]
    return token


# A number is part of an assertion's content ("budget is 600"), so it is kept for comparison but
# excluded from the topic signature — otherwise "600" alone would make two unrelated notes look
# like they share a topic.
_NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")

# Below this topic overlap the two notes are simply about different things, however they are
# worded. Calibrated against the near-duplicate threshold: a restatement scores ~1.0 and a
# different-topic note scores well under 0.2.
DEFAULT_MIN_TOPIC_OVERLAP = 0.6

# Above this overall similarity two notes say the same thing in the same direction, so the pair
# is a duplicate for consolidation to merge, never a correction.
DUPLICATE_SIMILARITY = 0.9


def _surface_tokens(text: str) -> List[str]:
    """Tokens preserving negation markers and numbers (``tokenize`` drops stopwords only)."""
    return tokenize(text, drop_stopwords=False)


def polarity(text: str) -> int:
    """``-1`` when the assertion is negated an odd number of times, else ``+1``.

    Parity, not presence: "it is not impossible" is affirmative, and a double negative in a
    correction should not read as a second disagreement with itself.
    """
    negations = sum(1 for token in _surface_tokens(text) if token in NEGATION_TOKENS)
    return -1 if negations % 2 else 1


def numbers(text: str) -> Set[str]:
    """Numeric literals in *text* (``"600"``, ``"1.5"``), for value-change detection."""
    return set(_NUMBER_RE.findall(text or ""))


def topic_signature(text: str) -> Set[str]:
    """Content tokens: stopwords, auxiliaries, negation markers and bare numbers removed, stemmed.

    This is the "what is this note about" set. Two notes with the same signature are talking
    about the same subject, whatever they say about it — which is why the disagreement markers
    (negations) and the value markers (numbers) must be out of it: they are what *differs*.
    """
    return {
        _stem(token)
        for token in _surface_tokens(text)
        if token not in NEGATION_TOKENS
        and token not in AUXILIARIES
        and token not in _STOPWORDS
        and not _NUMBER_RE.fullmatch(token)
    }


def topic_overlap(a_text: str, b_text: str) -> float:
    """Jaccard overlap of the two topic signatures, in ``[0, 1]``.

    Jaccard (not containment) on purpose: "the budget is 600" against "the recall budget is 600
    and the prefix budget is 6000" must not score 1.0, or any note that merely *mentions* a
    topic could supersede a note that *is* the topic.
    """
    a, b = topic_signature(a_text), topic_signature(b_text)
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def contradicts(
    new_text: str,
    old_text: str,
    *,
    min_topic_overlap: float = DEFAULT_MIN_TOPIC_OVERLAP,
) -> bool:
    """True when *new_text* asserts the opposite of *old_text* about the same subject."""
    if not str(new_text or "").strip() or not str(old_text or "").strip():
        return False

    if topic_overlap(new_text, old_text) < float(min_topic_overlap):
        return False

    # Polarities differ on the same topic: this is the definition of a contradiction, and it is
    # checked before the near-duplicate guard on purpose. A pair that differs ONLY by a negation
    # ("the tool writes X" / "the tool does not write X") is maximally similar *and* genuinely
    # contradictory — gating on similarity here would reject precisely the correction that
    # matters most, which is what an earlier ordering of these checks did.
    if polarity(new_text) != polarity(old_text):
        return True

    from .similarity import similarity

    # Same direction: a restatement, not a disagreement. Consolidation merges these (§3.2).
    if similarity(new_text, old_text) >= DUPLICATE_SIMILARITY:
        return False

    # Same direction, but the numbers disagree — a changed value corrects the value.
    new_numbers, old_numbers = numbers(new_text), numbers(old_text)
    return bool(new_numbers and old_numbers and new_numbers != old_numbers)


def find_contradictions(
    new_text: str,
    candidates: Iterable[Tuple[str, str]],
    *,
    min_topic_overlap: float = DEFAULT_MIN_TOPIC_OVERLAP,
    correction_mode: bool = False,
    embedder: Any = None,
) -> List[str]:
    """Ids from ``(node_id, text)`` pairs that *new_text* contradicts.

    Ordered most-similar-first so the caller supersedes the closest claim when several notes on
    the same topic are all refuted by one correction.

    ``correction_mode`` is set when the caller already knows *new_text* is a user correction (the
    only context this is called from). A correction that is *about the same thing* as an existing
    fact but is *not a restatement of it* is correcting that fact, even when the disagreement is a
    changed value with no negation and no number ("the project uses architecture B" correcting
    "the project uses architecture A"). The generic :func:`contradicts` cannot see that — the two
    differ only by one short value token — but the explicit correction signal plus high topic
    overlap makes superseding the right call and keeps provenance intact.
    """
    from .similarity import similarity

    stripped = strip_correction_opener(new_text) if correction_mode else new_text
    hits: List[Tuple[float, str]] = []
    for node_id, text in candidates:
        matched = contradicts(stripped, text, min_topic_overlap=min_topic_overlap)
        if not matched and correction_mode:
            overlap = topic_overlap(stripped, text)
            sim = similarity(stripped, text, embedder=embedder)
            # On the same topic, not a duplicate: a correction here changes the stored value.
            matched = overlap >= float(min_topic_overlap) and sim < DUPLICATE_SIMILARITY
        if matched:
            hits.append((similarity(stripped, text, embedder=embedder), str(node_id)))
    hits.sort(key=lambda pair: (-pair[0], pair[1]))
    return [node_id for _, node_id in hits]


# ======================================================================================
# Attribute-level temporal correction (§3 correction A)
# ======================================================================================
#
# A correction may not contradict a stored event at all — it may only revise *when* it
# happened ("the migration happened today" -> "the migration happened yesterday"). That is not a
# supersession: the event is still true, only one attribute is wrong, and superseding the whole
# event would throw away correct knowledge and orphan the node. This section finds the event
# whose temporal attribute the correction revises, so the caller can fix it in place and keep
# the change as history.
#
# Event identity across the correction is established by *morphological* root overlap, not surface
# tokens: the correction refers to "the migration" while the event says "migrated", so a
# derivational stemmer collapses both to a shared root. That is a defensible semantic basis
# (both words denote the same event), unlike raw lexical overlap. Generic nouns that would link
# unrelated memories ("project", "brain", "architecture", §3 quality bar) are excluded so they can
# never be the reason two notes are considered the same event.

# Temporal expressions, as whole phrases, that place an event in time. Kept to ordinary prose that
# actually appears in conversation; a calendar date is handled by the numeric form separately.
_TEMPORAL_PATTERNS: Tuple[re.Pattern, ...] = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\bthe day before yesterday\b",
        r"\b\d+\s+(?:days?|weeks?|months?|years?|hours?|minutes?)\s+ago\b",
        r"\ba\s+(?:few|couple of|several)\s+(?:days?|weeks?|months?|years?)\s+ago\b",
        r"\bthis\s+(?:morning|afternoon|evening|night|week|weekend|month|year)\b",
        r"\blast\s+(?:night|week|weekend|month|year)\b",
        r"\bnext\s+(?:week|weekend|month|year)\b",
        r"\btoday\b",
        r"\byesterday\b",
        r"\btomorrow\b",
        r"\btonight\b",
        r"\bnowadays\b",
        r"\bcurrently\b",
        r"\brecently\b",
        r"\bearlier\b",
        r"\blater\b",
        r"\b\d{4}-\d{2}-\d{2}\b",
    )
)

# Leading openers a correction uses to announce itself; stripped before comparing content, because
# "Correction:" / "sorry" / "actually" are discourse markers, not part of the event.
_CORRECTION_OPENER_RE = re.compile(
    r"^\s*(?:correction|actually|sorry|my bad|i (?:was wrong|made a mistake|mean|meant)|"
    r"to be precise|more precisely|nahi|nahin|galat|btw|by the way|wait)\b[\s:,.\-\u2014]*",
    re.IGNORECASE,
)

# Placeholder verbs that assert "something happened" without identifying what. On their own they
# cannot establish event identity, so a correction that carries only these falls back to recency.
_DEICTIC_ROOTS = frozenset({"happen", "occur", "went", "done", "made", "took", "taken", "got"})


def temporal_phrases(text: str) -> List[str]:
    """Temporal expressions in *text*, lowercased, in order, without duplicates."""
    found: List[str] = []
    seen: Set[str] = set()
    for pattern in _TEMPORAL_PATTERNS:
        for match in pattern.finditer(str(text or "")):
            phrase = match.group(0).lower()
            if phrase not in seen:
                seen.add(phrase)
                found.append(phrase)
    return found


def asserted_temporal_phrases(text: str) -> List[str]:
    """Temporal phrases the statement *asserts* — excluding ones it explicitly negates.

    "the migration happened yesterday, not today" asserts ``yesterday``: ``today`` is rejected by
    the "not", so it must not be mistaken for a second, conflicting temporal value.
    """
    raw = str(text or "")
    tokens = list(re.finditer(r"[A-Za-z0-9\u2019'-]+", raw))
    negated_spans: List[Tuple[int, int]] = []
    for i, token in enumerate(tokens):
        if token.group(0).lower() in NEGATION_TOKENS:
            start = token.start()
            end = tokens[min(i + 2, len(tokens) - 1)].end()
            negated_spans.append((start, end))

    asserted: List[str] = []
    for pattern in _TEMPORAL_PATTERNS:
        for match in pattern.finditer(raw):
            if any(lo <= match.start() < hi for lo, hi in negated_spans):
                continue
            phrase = match.group(0).lower()
            if phrase not in asserted:
                asserted.append(phrase)
    return asserted


def _root(token: str) -> str:
    """Derivational root: ``migration``/``migrated``/``migrate`` all collapse to ``migrat``, so an
    event and its nominalised reference in a correction share a root even though their surface
    tokens differ. Only ``-ion`` is stripped (not ``-ation``) precisely so the verb stem's ``-at``
    survives and the noun lands on the same root as the ``-ed`` form.
    """
    root = _stem(token)
    if root.endswith("ion") and len(root) - 3 >= 4:
        root = root[:-3]            # migration -> migrat ; creation -> creat
    elif root.endswith("e") and len(root) > 4:
        root = root[:-1]            # migrate -> migrat
    if len(root) > 4 and root[-1] == root[-2] and root[-1] not in "aeiou":
        root = root[:-1]
    return root


# Generic concepts, normalised through the same root pipeline so a surface form like
# "architecture" is filtered even though it inflects differently from "architect".
_GENERIC_ROOTS = frozenset(
    _root(word)
    for word in (
        "project", "projects", "system", "systems", "thing", "things", "stuff", "brain",
        "graph", "memory", "memories", "architecture", "architectures", "code", "work",
        "task", "tasks", "item", "items", "data", "info", "information", "point", "place",
        "time", "date", "day", "week", "month", "year", "note", "notes", "turn",
    )
)


def event_roots(text: str) -> Set[str]:
    """Content roots that identify *what event* a statement is about.

    Temporal expressions are removed (time is the attribute being corrected, not the event), as are
    negation markers, auxiliaries, numbers and the generic-concept roots that must never drive
    identity.
    """
    roots = {
        _root(token)
        for token in _surface_tokens(text)
        if token not in NEGATION_TOKENS and token not in AUXILIARIES and token not in _STOPWORDS
    }
    for phrase in temporal_phrases(text):
        for token in re.findall(r"[a-z0-9]+", phrase):
            roots.discard(_root(token))
    return {root for root in roots if len(root) >= 3 and root not in _GENERIC_ROOTS}


def _polarity_core(text: str) -> int:
    """Polarity of the *event assertion*, ignoring negation that governs a temporal phrase.

    "the migration happened yesterday, not today" negates only the *time*, not the event: the
    migration still happened. Sentence-level polarity would read ``-1`` and wrongly block the
    temporal revision, so the "not" attaching to a temporal expression is excluded here.
    """
    raw = str(text or "")
    spans: List[Tuple[int, int]] = []
    for pattern in _TEMPORAL_PATTERNS:
        for match in pattern.finditer(raw):
            spans.append((match.start(), match.end()))
    tokens = list(re.finditer(r"[A-Za-z0-9']+", raw))
    for i, token in enumerate(tokens):
        if token.group(0).lower() not in NEGATION_TOKENS:
            continue
        followers = tokens[i + 1: i + 3]
        if any(any(lo <= follower.start() < hi for lo, hi in spans) for follower in followers):
            spans.append((token.start(), token.end()))
    negations = sum(
        1
        for token in tokens
        if token.group(0).lower() in NEGATION_TOKENS
        and not any(lo <= token.start() < hi for lo, hi in spans)
    )
    return -1 if negations % 2 else 1


def _replace_temporal(text: str, new_phrase: str) -> Optional[str]:
    """Rewrite *text* with its earliest temporal expression replaced by *new_phrase*."""
    best: Optional[re.Match] = None
    for pattern in _TEMPORAL_PATTERNS:
        match = pattern.search(text)
        if match is not None and (best is None or match.start() < best.start()):
            best = match
    if best is None:
        return None
    return text[: best.start()] + new_phrase + text[best.end():]


def strip_correction_opener(text: str) -> str:
    """Drop a leading correction marker (``Correction:``, ``actually``, ...) from *text*."""
    return _CORRECTION_OPENER_RE.sub("", str(text or ""), count=1).strip()


def find_temporal_revisions(
    new_text: str,
    candidates: Iterable[Tuple[str, str]],
    *,
    recency: Optional[dict] = None,
    embedder: Any = None,
) -> List[Tuple[str, str]]:
    """``[(node_id, corrected_text)]`` for an existing event whose temporal attribute *new_text* fixes.

    Returns at most one revision: a correction refers to a single event. Event identity is a shared
    non-generic content root (morphological, so "migration" matches "migrated"); when the correction
    carries only a deictic verb ("it happened yesterday") identity falls back to the most recently
    updated node that has a (differing) temporal attribute, which is what the gesture refers to.
    """
    corrected = strip_correction_opener(new_text)
    new_temporals = asserted_temporal_phrases(corrected)
    if not new_temporals:
        return []
    new_set = set(new_temporals)
    new_roots = event_roots(corrected)
    new_is_deictic = not new_roots or new_roots <= _DEICTIC_ROOTS

    scored: List[Tuple[int, int, str, str]] = []   # (shared, recency, node_id, text)
    fallback: List[Tuple[int, str, str]] = []      # (recency, node_id, text)
    for node_id, text in candidates:
        text = str(text or "")
        old_temporals = asserted_temporal_phrases(text)
        if not old_temporals:
            continue
        if new_set & set(old_temporals):
            continue                       # same time asserted -> nothing to revise
        if _polarity_core(corrected) != _polarity_core(text):
            continue                       # opposite assertion -> supersession, not a revision
        shared = new_roots & event_roots(text)
        ts = int((recency or {}).get(str(node_id), 0))
        if shared:
            scored.append((len(shared), ts, str(node_id), text))
        elif new_is_deictic:
            fallback.append((ts, str(node_id), text))

    if not scored:
        if not fallback:
            return []
        fallback.sort(key=lambda row: (-row[0], row[1]))
        _, node_id, text = fallback[0]
    else:
        scored.sort(key=lambda row: (-row[0], -row[1], row[2]))
        _, _, node_id, text = scored[0]

    revised = _replace_temporal(text, new_temporals[0])
    if not revised or revised == text:
        return []
    return [(node_id, revised)]
