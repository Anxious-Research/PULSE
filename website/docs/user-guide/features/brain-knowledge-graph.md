---
title: "Native Cognitive Brain & Knowledge Graph"
description: "PULSE's native markdown knowledge vault, bi-directional wikilinks, belief evolution, and StarMap graph visualization."
---

# Native Cognitive Brain & Knowledge Graph

PULSE features a **native, local-first Cognitive Brain & Knowledge Graph engine** built directly into the agent runtime and desktop application.

Rather than relying on flat, character-constrained memory files (`MEMORY.md`, `USER.md`) or requiring external note-taking applications, PULSE maintains an interconnected Markdown vault under `~/.pulse/brain/`.

---

## Key Pillars of PULSE Brain

```
~/.pulse/brain/
├── self/       # PULSE self-awareness: identity, anatomy, runtime wiring, tools
├── user/       # User profile, mental models, preferences, workflow styles
├── concepts/   # Technical & domain knowledge, architectural patterns
├── beliefs/    # Evolving hypotheses, confidence ratings, resolved misconceptions
└── projects/   # Active projects, codebase states, and mission milestones
```

### 1. Bi-directional `[[wikilinks]]` & Backlinks
Every note can reference other concepts using Obsidian-compatible wikilinks:
- `[[self/identity]]`
- `[[User Preferences|prefs]]`
- `[[concepts/python-async#Event Loop|async loop]]`

The `BrainGraph` automatically parses forward links and indexes real-time **backlinks**, constructing a navigable semantic web.

### 2. Belief Evolution & Misconception Resolution
Human memory constantly updates when presented with newer facts. PULSE's `BeliefEngine` models this behavior:
- When a hypothesis or fact is disproved or updated, the old belief is marked `superseded` or `disproved`.
- The new belief links back to the old one via `supersedes: [[old-belief-id]]`.
- An audit trail is preserved so PULSE understands *why* its understanding evolved.

### 3. Native Self-Awareness (`self/`)
PULSE is endowed with foundational knowledge about its own architecture:
- Who built it (**Anxious Research**)
- How its agent runtime, tool registry, gateway, and desktop shell operate
- Its operating strengths, tools, and execution principles

### 4. StarMap Knowledge Graph
In the PULSE Desktop App, open the **StarMap** (`Cmd+K -> StarMap`) to visually explore your evolving knowledge graph in an interactive force-directed canvas.

---

## Agent Tool: `brain`

The `brain` tool allows PULSE to autonomously read, write, search, explore, and update its knowledge:

| Action | Description |
|---|---|
| `read` | Read a note, its YAML frontmatter, [[wikilinks]], and incoming backlinks |
| `write` | Create or update a node with tags, confidence, and Markdown body |
| `patch` | Targeted string replacement inside a note |
| `delete` | Delete a note from the vault |
| `search` | Full-text and tag/category search |
| `explore` | Retrieve local graph neighborhood (forward links + backlinks) |
| `evolve_belief` | Supersede an old belief with a new one and preserve reasoning |
| `graph_stats` | Retrieve global node/edge counts and category clusters |
