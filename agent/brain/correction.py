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
    embedder: Any = None,
) -> List[str]:
    """Ids from ``(node_id, text)`` pairs that *new_text* contradicts.

    Ordered most-similar-first so the caller supersedes the closest claim when several notes on
    the same topic are all refuted by one correction.
    """
    from .similarity import similarity

    hits: List[Tuple[float, str]] = []
    for node_id, text in candidates:
        if contradicts(new_text, text, min_topic_overlap=min_topic_overlap):
            hits.append((similarity(new_text, text, embedder=embedder), str(node_id)))
    hits.sort(key=lambda pair: (-pair[0], pair[1]))
    return [node_id for _, node_id in hits]
