#!/usr/bin/env python3
"""
Entity extraction utility for semantic relationship formation.
Identifies entity mentions within memory content and titles.
"""
import re
from typing import List, Set, Tuple

# Ambient domain words that are mentioned by almost every memory. Using them as
# linking entities produces indiscriminate hub edges (every note mentions "PULSE",
# "Brain", "architecture"), which is relationship noise, not relationship quality.
# A phrase is only generic when EVERY one of its content words is ambient — a phrase
# that mixes a generic word with a specific one ("Memory Organ", "cognitive graph")
# is still specific enough to form a defensible edge.
GENERIC_ENTITIES: Set[str] = {
    "pulse", "brain", "architecture", "system", "systems", "memory", "memories",
    "graph", "project", "projects", "note", "notes", "data", "state", "code",
    "feature", "features", "concept", "concepts", "thing", "things", "today",
    "yesterday", "stuff", "entity", "entities", "model", "models", "agent",
}

# Function words that never carry entity meaning; stripped before the generic test
# so a phrase like "Brain and an architecture" is judged on {brain, architecture}.
_PHRASE_STOPWORDS: Set[str] = {
    "a", "an", "the", "of", "and", "or", "is", "are", "was", "were", "be", "to",
    "in", "on", "for", "with", "that", "this", "it", "its", "has", "have", "had",
    "other", "some", "any", "all", "as", "at", "by", "from",
}


def is_generic_entity(term: str) -> bool:
    """True when a term carries no specific meaning — every content word is ambient.

    Single ambient tokens ("PULSE") and all-ambient phrases ("Brain architecture")
    are generic; a phrase mixing ambient and specific words ("Memory Organ") is not.
    """
    tokens = [t for t in re.split(r"\s+", term.strip().lower()) if t]
    content = [t for t in tokens if t not in _PHRASE_STOPWORDS]
    if not content:
        return True
    return all(t in GENERIC_ENTITIES for t in content)

def extract_entity_candidates(text: str, *, min_length: int = 4) -> List[str]:
    """Extract potential entities from text.
    
    Entities: capitalized phrases (2-4 words), technical terms, proper nouns.
    Examples: "PULSE", "Brain architecture", "memory vault", "Markdown note"
    """
    if not text or len(text) < min_length:
        return []
    
    candidates: Set[str] = set()
    
    # Pattern 1: All-caps words (PULSE, API, SQLite)
    for match in re.finditer(r'\b[A-Z]{2,}(?:[A-Z][a-z]+)?\b', text):
        term = match.group(0)
        if len(term) >= min_length:
            candidates.add(term)
    
    # Pattern 2: Capitalized phrases (Brain architecture, Memory vault)
    for match in re.finditer(r'\b[A-Z][a-z]+(?:\s+[A-Z]?[a-z]+){0,3}\b', text):
        term = match.group(0)
        if len(term) >= min_length and not term[0].isupper() or ' ' in term:
            candidates.add(term)
    
    # Pattern 3: Technical compounds (snake_case, kebab-case, camelCase)
    for match in re.finditer(r'\b[a-z]+[_-][a-z]+(?:[_-][a-z]+)*\b', text):
        term = match.group(0)
        if len(term) >= min_length:
            candidates.add(term)
    
    # Pattern 4: Quoted terms ("memory vault", 'cognitive Brain')
    for match in re.finditer(r'["\']([^"\']{4,40})["\']', text):
        term = match.group(1).strip()
        if len(term) >= min_length:
            candidates.add(term)
    
    return sorted(candidates, key=lambda x: len(x), reverse=True)


def build_entity_index(nodes: List) -> List[Tuple[str, re.Pattern, str, str]]:
    """Build entity lookup index from memory nodes.
    
    Returns: [(node_id, pattern, entity_term, source)]
    where source is 'title' or 'content'
    """
    index: List[Tuple[str, re.Pattern, str, str]] = []
    
    for node in nodes:
        node_id = node.id
        
        # Extract entities from title
        title_entities = extract_entity_candidates(node.title or "")
        for entity in title_entities:
            if is_generic_entity(entity):
                continue
            # Whole-word boundary match, case-insensitive
            pattern = re.compile(r'\b' + re.escape(entity) + r'\b', re.IGNORECASE)
            index.append((node_id, pattern, entity, 'title'))
        
        # Extract entities from content (top entities only, to avoid noise)
        content_entities = extract_entity_candidates(node.content or "")[:5]
        for entity in content_entities:
            if is_generic_entity(entity):
                continue
            # Skip if already covered by title
            if entity not in title_entities:
                pattern = re.compile(r'\b' + re.escape(entity) + r'\b', re.IGNORECASE)
                index.append((node_id, pattern, entity, 'content'))
    
    return index


def find_entity_mentions(text: str, entity_index: List[Tuple[str, re.Pattern, str, str]]) -> List[Tuple[str, str]]:
    """Find which entities from the index are mentioned in the given text.
    
    Returns: [(node_id, matched_entity)]
    """
    matches: List[Tuple[str, str]] = []
    seen_nodes: Set[str] = set()
    
    for node_id, pattern, entity, source in entity_index:
        if node_id in seen_nodes:
            continue
        if pattern.search(text):
            matches.append((node_id, entity))
            seen_nodes.add(node_id)
    
    return matches
