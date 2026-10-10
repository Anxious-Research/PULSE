"""Reflection — bounded, evidence-grounded reasoning over memory (specs/brain.md §3.7, §7).

Recall answers "what is relevant?". Reflection answers "what does the evidence actually support,
what is in conflict, and should a belief change as a result?" It is deliberately *not* an LLM
call over a few snippets — it is a deterministic procedure over the memory graph, so it is
reproducible, cheap, testable, and cannot hallucinate: every conclusion it reaches names the
memory ids it came from, and it says "unknown" when the evidence does not settle the question.

The stages mirror §7:

1. Interpret what evidence is needed — the question plus an optional goal.
2. Retrieve episodes, facts and beliefs (cue recall + procedural memory for the goal).
3. Identify conflicts between retrieved evidence (:func:`~agent.brain.correction.contradicts`).
4. Retrieve more evidence for any conflicted claim (one bounded extra pass per round).
5. Cluster evidence that says the same thing; a cluster of ≥ :data:`MIN_EVIDENCE_FOR_BELIEF`
   independent memories is a belief PULSE may hold.
6. Persist the belief, and record the losing side of a conflict as counter-evidence — never a
   silent overwrite.
7. Report the conclusion, its supporting ids, the counter-evidence, and the residual uncertainty.

Guards, because reflection is the easiest place to build an unbounded self-referential loop:
reflection is **triggered**, not run every turn (:func:`should_reflect`); rounds are capped;
belief updates per run are capped (:data:`MAX_BELIEF_UPDATES`); and forming a belief that already
exists only reinforces it, so re-reflecting on the same evidence is a no-op.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from . import beliefs as belief_mod
from . import outcomes as outcome_mod
from .correction import contradicts, polarity, topic_overlap
from .recall import recall

# ── trigger policy (§7 "Do not force every conversation through an expensive reflection cycle") ─
NOVELTY_TRIGGER = 0.65
IMPORTANCE_TRIGGER = 0.65

# ── bounds ─────────────────────────────────────────────────────────────────
MAX_EVIDENCE = 12
MAX_BELIEF_UPDATES = 4
MIN_EVIDENCE_FOR_BELIEF = 2
CLUSTER_SIMILARITY = 0.72      #: two memories nearly identical in wording assert the same thing
CLUSTER_TOPIC_OVERLAP = 0.5    #: …or share a subject and polarity, even phrased differently
CONFLICT_TOPIC_OVERLAP = 0.5   #: subjects must clearly match before two claims can be "in conflict"
MAX_ROUNDS = 3


def should_reflect(
    *,
    contradiction: bool = False,
    failure: bool = False,
    novelty: float = 0.0,
    importance: float = 0.0,
    explicit: bool = False,
) -> bool:
    """Whether a situation warrants reflection.

    Reflection is expensive relative to ordinary recall, so it runs on signal: an explicit
    request, a detected contradiction, a task failure, or a sufficiently novel/important moment.
    """
    return bool(
        explicit
        or contradiction
        or failure
        or float(novelty or 0.0) >= NOVELTY_TRIGGER
        or float(importance or 0.0) >= IMPORTANCE_TRIGGER
    )


@dataclass
class Reflection:
    """The result of one reflection: a conclusion, its evidence, its conflicts, its uncertainty."""

    question: str
    goal: Optional[str] = None
    conclusion: str = ""
    confidence: float = 0.0
    uncertainty: Optional[str] = None
    evidence: List[Dict[str, Any]] = field(default_factory=list)
    contradictions: List[Dict[str, Any]] = field(default_factory=list)
    supporting: List[str] = field(default_factory=list)
    counter: List[str] = field(default_factory=list)
    updates: List[Dict[str, Any]] = field(default_factory=list)
    procedural_guidance: List[Dict[str, Any]] = field(default_factory=list)
    rounds: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "question": self.question,
            "goal": self.goal,
            "conclusion": self.conclusion,
            "confidence": round(self.confidence, 4),
            "uncertainty": self.uncertainty,
            "evidence": self.evidence,
            "contradictions": self.contradictions,
            "supporting": self.supporting,
            "counter": self.counter,
            "updates": self.updates,
            "procedural_guidance": self.procedural_guidance,
            "rounds": self.rounds,
        }

    def render(self) -> str:
        """Compact human-readable summary for a log line or a UI panel."""
        lines = [f"Reflection on: {self.question}"]
        if self.conclusion:
            lines.append(f"Conclusion ({self.confidence:.2f}): {self.conclusion}")
        else:
            lines.append("Conclusion: no evidence reached a conclusion.")
        if self.supporting:
            lines.append("Supported by: " + ", ".join(self.supporting[:5]))
        if self.counter:
            lines.append("Contradicted by: " + ", ".join(self.counter[:5]))
        if self.uncertainty:
            lines.append(f"Uncertainty: {self.uncertainty}")
        return "\n".join(lines)


def _node_text(vault: Any, node_id: str) -> str:
    node = vault.read_node(node_id)
    return (node.content if node else "") or ""


def _gather_evidence(vault: Any, query: str, *, index: Any, now: Optional[float], seen: set,
                     deep: bool = False) -> List[Dict[str, Any]]:
    """One retrieval pass → evidence dicts, skipping ids already gathered across rounds."""
    try:
        res = recall(query, vault=vault, index=index, limit=MAX_EVIDENCE, now=now, deep=deep)
    except Exception:
        return []
    out: List[Dict[str, Any]] = []
    for hit in res.hits:
        if hit.node_id in seen:
            continue
        seen.add(hit.node_id)
        try:
            node = vault.read_node(hit.node_id)
        except Exception:
            node = None
        text = (node.content if node else "") or hit.snippet
        corroborations = 0
        if node is not None:
            try:
                corroborations = len(vault.corroborations_of(node))
            except Exception:
                corroborations = 0
        out.append({
            "id": hit.node_id,
            "category": hit.category,
            "score": round(float(hit.score), 6),
            "text": text[:600],
            "corroborations": corroborations,
        })
    return out


def _evidence_refs(cluster: Sequence[Dict[str, Any]]) -> List[str]:
    """The independent *assertions* behind a cluster: each memory, plus each restatement of it.

    Counting nodes alone made a corroborated belief unreachable: a restated claim is folded into
    the existing node rather than duplicated, so its recurrence never raised the count.
    """
    refs: List[str] = []
    for entry in cluster:
        refs.append(entry["id"])
        for index in range(int(entry.get("corroborations") or 0)):
            refs.append(f"{entry['id']}#c{index + 1}")
    return refs


def _same_claim(a_text: str, b_text: str) -> bool:
    """Whether two memories assert the *same* thing — the same subject with the same polarity.

    Deliberately not raw lexical similarity: two notes can phrase one fact very differently
    ("the cache layer is shared" / "a single shared cache serves all subsystems") and still be
    corroborating evidence, while two notes can share almost every word and *disagree* ("did
    migrate" / "did not migrate"). So: opposing polarity is never the same claim, and otherwise
    the pair must share either wording or subject.
    """
    from .similarity import similarity

    if polarity(a_text) != polarity(b_text):
        return False
    if similarity(a_text, b_text) >= CLUSTER_SIMILARITY:
        return True
    return topic_overlap(a_text, b_text) >= CLUSTER_TOPIC_OVERLAP


def _cluster(evidence: Sequence[Dict[str, Any]]) -> List[List[Dict[str, Any]]]:
    """Group evidence that asserts the same thing (greedy, order-stable)."""
    clusters: List[List[Dict[str, Any]]] = []
    for item in evidence:
        placed = False
        for cluster in clusters:
            if _same_claim(item["text"], cluster[0]["text"]):
                cluster.append(item)
                placed = True
                break
        if not placed:
            clusters.append([item])
    return clusters


#: Interrogative scaffolding stripped to recover the *goal* a question is about. Procedural memory
#: is keyed by a goal, and the production path only ever has the turn's text — so the goal is
#: derived here rather than demanded from every caller (§8).
_GOAL_STRIP_RE = re.compile(
    r"^(?:(?:hi|hey|hello|ok(?:ay)?|so|now|please|pulse)\b[\s,]*)?"
    r"(?:how\s+(?:do|can|should|would|might|could)\s+(?:i|we|you)\b|how\s+to\b|"
    r"what(?:'s|\s+is)\s+the\s+(?:best|right|correct|proper)\s+way\s+to\b|"
    r"(?:the\s+)?(?:best|right|correct|proper)\s+way\s+to\b|"
    r"what\s+should\s+(?:i|we)\s+do\s+to\b|"
    r"can\s+you\b|could\s+you\b|would\s+you\b|will\s+you\b|"
    r"i\s+(?:need|want|have)\s+to\b|i(?:'m|\s+am)\s+trying\s+to\b|"
    r"help\s+me\b|let'?s\b|we\s+(?:need|want|have)\s+to\b|"
    r"is\s+there\s+a\s+way\s+to\b)\s*",
    re.IGNORECASE,
)


def _goal_from_question(question: str) -> str:
    """The action phrase a question is *about* — ``"How should I deploy the atlas service?"``
    becomes ``"deploy the atlas service"``.

    Without this the production path could store a learned procedure and never reach it again:
    :func:`reflect` is called with the turn's text, not with a goal.
    """
    text = " ".join((question or "").split()).strip()
    if not text:
        return ""
    text = text.rstrip("?.! ").strip()
    # Strip repeatedly: "ok, so how should I deploy X?" needs both the opener and the scaffolding.
    for _ in range(3):
        stripped = _GOAL_STRIP_RE.sub("", text).strip(" ,:-—")
        if stripped == text or not stripped:
            break
        text = stripped
    return text


def reflect(
    vault: Any,
    *,
    question: str,
    goal: Optional[str] = None,
    now: Optional[float] = None,
    max_rounds: int = 2,
    apply: bool = True,
    index: Any = None,
    source_turn: Optional[str] = None,
) -> Reflection:
    """Run a bounded reflection and (optionally) persist the justified belief/procedure updates.

    ``apply=False`` computes the reflection without writing — the analysis path. Returns a
    :class:`Reflection`.
    """
    ts = int(now if now is not None else time.time())
    question = (question or "").strip()
    # Procedural memory is keyed by a goal, and the production path only has the turn's text — so
    # derive the goal when the caller does not supply one. Without this, a learned procedure is
    # stored and never surfaced again (§8).
    goal = (goal or "").strip() or _goal_from_question(question)
    result = Reflection(question=question, goal=goal or None)
    if not question or vault is None:
        result.uncertainty = "nothing to reflect on"
        return result

    rounds = max(1, min(MAX_ROUNDS, int(max_rounds)))
    seen: set = set()
    evidence: List[Dict[str, Any]] = []
    for r in range(rounds):
        query = question if r == 0 else _conflict_query(evidence, question)
        # First round is the ordinary window; later rounds dig into history (§7 step 4 — retrieve
        # *additional* evidence, including older memories normal recency would hide).
        batch = _gather_evidence(vault, query, index=index, now=ts, seen=seen, deep=r > 0)
        if not batch:
            break
        evidence.extend(batch)
        result.rounds = r + 1
        # Stop early: once we have conflicts on record, another generic pass adds little.
        if r == 0 and not _find_conflicts(evidence):
            break

    result.evidence = evidence
    # A learned procedure relevant to the goal is itself evidence for how to proceed (§8) — and it
    # must be surfaced even when ordinary recall finds little, otherwise "learning changes future
    # behaviour" is not true.
    if goal:
        result.procedural_guidance = [
            p.as_dict() for p in outcome_mod.procedures_for_goal(vault, goal, limit=3)
        ]
        known = {e["id"] for e in evidence}
        for proc in result.procedural_guidance:
            if proc["id"] not in known:
                evidence.append({"id": proc["id"], "category": "procedure", "score": 0.5,
                                 "text": _node_text(vault, proc["id"])[:600]})
    if not evidence:
        result.uncertainty = "no relevant memories were recalled"
        return result

    # Stage 3-5: conflicts and clusters. Procedures are guidance, not claims — they are excluded
    # from belief clustering so a recipe is never mistaken for an assertion about the world.
    claims = [e for e in evidence if e["category"] != "procedure"]
    result.contradictions = _find_conflicts(claims)
    clusters = _cluster(claims)
    if not clusters:
        result.conclusion = result.procedural_guidance[0]["goal"] if result.procedural_guidance else ""
        result.confidence = result.procedural_guidance[0]["confidence"] if result.procedural_guidance else 0.0
        result.uncertainty = None if result.procedural_guidance else "no relevant memories were recalled"
        return result

    # The best-supported cluster is the provisional conclusion.
    best = max(clusters, key=lambda c: (len(c), max(e["score"] for e in c)))
    result.conclusion = _conclusion_of(best)
    result.supporting = _evidence_refs(best)
    losing_ids = _losing_sides(result.contradictions)
    result.counter = [i for i in losing_ids]

    # Stage 6: persist beliefs/procedures only when justified and asked to.
    if apply:
        # (a) a cluster of ≥2 independent memories is a belief PULSE may hold.
        for cluster in sorted(clusters, key=lambda c: -len(c))[:MAX_BELIEF_UPDATES]:
            statement = _conclusion_of(cluster)
            if not statement:
                continue
            # Evidence is independent *assertions*, not independent nodes: a memory the user
            # restated carries corroboration records, and each is a separate assertion of the same
            # claim. Counting nodes alone made a corroborated belief unreachable, because a
            # restatement is folded into the existing node rather than duplicated.
            supporting_ids = list(dict.fromkeys(_evidence_refs(cluster)))
            if len(supporting_ids) < MIN_EVIDENCE_FOR_BELIEF:
                continue
            counters = [e["id"] for e in cluster if e["id"] in losing_ids]
            formed = belief_mod.form_belief(
                vault, statement, evidence_refs=supporting_ids, now=ts, source_turn=source_turn,
            )
            result.updates.append({**formed, "kind": "belief"})
            if counters:
                weakened = belief_mod.add_counter_evidence(
                    vault, formed.get("belief_id", ""), counters, now=ts,
                )
                result.updates.append({**weakened, "kind": "belief_counter"})

    result.confidence = _confidence_of(best, result.contradictions, question)
    result.uncertainty = _uncertainty(result)
    return result


# ── internals ──────────────────────────────────────────────────────────────


def _find_conflicts(evidence: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    conflicts: List[Dict[str, Any]] = []
    for i, a in enumerate(evidence):
        for b in evidence[i + 1:]:
            if topic_overlap(a["text"], b["text"]) < 0.4:
                continue
            # Reflection only *flags* a conflict (it raises uncertainty and records counter-
            # evidence); it never supersedes or deletes. So it may use a slightly looser subject
            # bar than the encoding path, where a wrong match would rewrite a memory.
            if contradicts(a["text"], b["text"], min_topic_overlap=CONFLICT_TOPIC_OVERLAP):
                conflicts.append({
                    "a": a["id"], "b": b["id"],
                    "a_score": a["score"], "b_score": b["score"],
                    "reason": "same topic, opposing assertion",
                })
    return conflicts


def _conflict_query(evidence: Sequence[Dict[str, Any]], fallback: str) -> str:
    """A follow-up query built from the lowest-scoring evidence — the more likely to be wrong."""
    if not evidence:
        return fallback
    weakest = min(evidence, key=lambda e: e["score"])
    snippet = (weakest.get("text") or "").strip().splitlines()
    return snippet[0][:120] if snippet else fallback


def _losing_sides(conflicts: Sequence[Dict[str, Any]]) -> List[str]:
    losing: List[str] = []
    for c in conflicts:
        loser = c["a"] if c["a_score"] <= c["b_score"] else c["b"]
        if loser not in losing:
            losing.append(loser)
    return losing


def _conclusion_of(cluster: Sequence[Dict[str, Any]]) -> str:
    if not cluster:
        return ""
    top = max(cluster, key=lambda e: e["score"])
    first_line = (top.get("text") or "").strip().splitlines()
    return (first_line[0] if first_line else "").strip()[:240]


def _confidence_of(cluster: Sequence[Dict[str, Any]], conflicts: Sequence[Dict[str, Any]],
                   question: str) -> float:
    if not cluster:
        return 0.0
    support = len(cluster)
    base = belief_mod.evidence_confidence([e["id"] for e in cluster])
    # Relevance of the cluster to the question tempers confidence: a well-supported claim that
    # does not actually address the question is not a confident answer to it.
    relevance = topic_overlap(question, cluster[0]["text"]) if cluster else 0.0
    involved = sum(1 for c in conflicts if c["a"] in {e["id"] for e in cluster}
                   or c["b"] in {e["id"] for e in cluster})
    penalty = belief_mod.CONFLICT_PENALTY ** involved if involved else 1.0
    return round(max(0.0, min(1.0, base * (0.5 + 0.5 * relevance) * penalty)), 4)


def _uncertainty(result: Reflection) -> Optional[str]:
    if result.contradictions:
        return (f"{len(result.contradictions)} conflicting pair(s) of evidence remain unresolved; "
                f"conclusion is provisional")
    if len(result.supporting) < MIN_EVIDENCE_FOR_BELIEF:
        return "only a single memory supports this; provisional"
    if result.confidence < belief_mod.UNCERTAIN_THRESHOLD:
        return "evidence is weak; the conclusion is not certain"
    return None
