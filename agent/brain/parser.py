"""Frontmatter + wikilink codec for the brain vault.

Deliberately dependency-free: a small, strict, well-tested reader/writer for the subset of
YAML that Obsidian frontmatter actually uses (scalars, block lists, inline lists). This
avoids adding a YAML dependency to PULSE's runtime — a previous attempt at this feature
broke the live agent, so the codec here must never be able to raise on a partial file.

Supported line forms:
    key: value
    key: "quoted value"
    key: 12
    key: 0.5
    key: true|false
    key: null|~
    key: [a, b, c]
    key:
      - a
      - b
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

_FRONTMATTER_RE = re.compile(r"^---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|$)", re.DOTALL)
_KEY_RE = re.compile(r"^([A-Za-z0-9_.\- ]+):(?:[ \t]*(.*))?$")
_LIST_ITEM_RE = re.compile(r"^[ \t]*[-*][ \t]+(.*)$")
_FENCE_RE = re.compile(r"^\s*(```|~~~)")
# [[target]] | [[target|alias]] | [[target#heading]] | [[target#heading|alias]]
_WIKILINK_RE = re.compile(r"\[\[([^\[\]]+?)\]\]")
_SLUG_RE = re.compile(r"[^a-z0-9]+")
# Strings that parse_scalar would turn into a non-string must be quoted on write.
_NUMERIC_RE = re.compile(r"^[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?$")
_AMBIGUOUS_STRINGS = frozenset({"true", "false", "null", "~", "none", "yes", "no", "on", "off"})


# ── scalars ────────────────────────────────────────────────────────────────

def _unquote(text: str) -> str:
    text = text.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in ("'", '"'):
        return text[1:-1]
    return text


def parse_scalar(text: str) -> Any:
    """Parse a single frontmatter value into a Python scalar/list.

    Unknown shapes fall back to the raw string — never raises.
    """
    text = text.strip()
    if text == "":
        return None
    if len(text) >= 2 and text[0] == text[-1] and text[0] in ("'", '"'):
        return text[1:-1]
    lowered = text.lower()
    if lowered in ("null", "~", "none"):
        return None
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    if text.startswith("[") and text.endswith("]"):
        inner = text[1:-1].strip()
        if not inner:
            return []
        return [parse_scalar(part) for part in _split_inline(inner)]
    try:
        return int(text)
    except ValueError:
        pass
    try:
        return float(text)
    except ValueError:
        pass
    return text


def _split_inline(inner: str) -> List[str]:
    """Split an inline list on commas, respecting quotes."""
    parts: List[str] = []
    buf: List[str] = []
    quote: Optional[str] = None
    for ch in inner:
        if quote:
            buf.append(ch)
            if ch == quote:
                quote = None
            continue
        if ch in ("'", '"'):
            quote = ch
            buf.append(ch)
            continue
        if ch == ",":
            parts.append("".join(buf))
            buf = []
            continue
        buf.append(ch)
    if buf:
        parts.append("".join(buf))
    return [p.strip() for p in parts if p.strip()]


def _needs_quotes(text: str) -> bool:
    """True when an unquoted ``text`` would be read back as something other than a string."""
    if text == "" or text != text.strip():
        return True
    if text.lower() in _AMBIGUOUS_STRINGS:
        return True
    if _NUMERIC_RE.match(text):
        return True
    if _KEY_RE.match(text) or text[0] in "[*-{" or ":" in text or "#" in text:
        return True
    return False


def render_scalar(value: Any) -> str:
    """Serialise a scalar so ``parse_scalar`` round-trips it exactly.

    Strings that *look* like another type (``"12"``, ``"true"``, ``"null"``) are quoted,
    otherwise a stored string would silently come back as an int/bool/None.
    """
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value) if isinstance(value, float) else str(value)
    text = str(value)
    if _needs_quotes(text):
        return '"' + text.replace('"', '\\"') + '"'
    return text


# ── frontmatter ────────────────────────────────────────────────────────────

def parse_frontmatter_and_body(raw: str) -> Tuple[Dict[str, Any], str]:
    """Split ``raw`` into (frontmatter dict, body). Malformed input ⇒ ({}, raw)."""
    if not raw:
        return {}, ""
    text = raw.lstrip("\ufeff")
    match = _FRONTMATTER_RE.match(text)
    if not match:
        return {}, text
    body = text[match.end():]
    return parse_frontmatter_block(match.group(1)), body


def parse_frontmatter_block(block: str) -> Dict[str, Any]:
    """Parse the inner YAML-ish block. Never raises; junk lines are ignored."""
    out: Dict[str, Any] = {}
    lines = block.splitlines()
    idx = 0
    while idx < len(lines):
        line = lines[idx]
        idx += 1
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        item = _LIST_ITEM_RE.match(line)
        if item and out:
            # A block-list item belongs to the most recent key.
            key = next(reversed(out))
            existing = out[key]
            if isinstance(existing, list):
                existing.append(parse_scalar(item.group(1)))
            else:
                out[key] = [parse_scalar(item.group(1))]
            continue
        m = _KEY_RE.match(line)
        if not m:
            continue
        key = m.group(1).strip()
        rest = (m.group(2) or "").strip()
        if rest:
            out[key] = parse_scalar(rest)
            continue
        # Empty value: either a block list on following lines, or null.
        collected: List[Any] = []
        lookahead = idx
        while lookahead < len(lines):
            nxt = lines[lookahead]
            it = _LIST_ITEM_RE.match(nxt)
            if it:
                collected.append(parse_scalar(it.group(1)))
                lookahead += 1
                continue
            if not nxt.strip():
                lookahead += 1
                continue
            break
        if collected:
            out[key] = collected
            idx = lookahead
        else:
            out[key] = None
    return out


def render_frontmatter(meta: Dict[str, Any]) -> str:
    """Render a frontmatter block (without the ``---`` fences)."""
    lines: List[str] = []
    for key, value in meta.items():
        if isinstance(value, (list, tuple)):
            if not value:
                lines.append(f"{key}: []")
                continue
            lines.append(f"{key}:")
            lines.extend(f"  - {render_scalar(v)}" for v in value)
        else:
            lines.append(f"{key}: {render_scalar(value)}")
    return "\n".join(lines)


def compose_markdown(meta: Dict[str, Any], body: str) -> str:
    """Full file text: frontmatter fence + body, newline-terminated."""
    body = (body or "").lstrip("\n")
    return f"---\n{render_frontmatter(meta)}\n---\n\n{body}".rstrip("\n") + "\n"


def load_frontmatter(raw: str) -> Dict[str, Any]:
    return parse_frontmatter_and_body(raw)[0]


# ── ids ────────────────────────────────────────────────────────────────────

def slugify(text: str, *, fallback: str = "untitled") -> str:
    """Filesystem/Obsidian-safe slug."""
    slug = _SLUG_RE.sub("-", str(text or "").strip().lower()).strip("-")
    slug = re.sub(r"-{2,}", "-", slug)
    return slug or fallback


def wikilinks_to_text(text: str) -> str:
    """Render ``[[...]]`` as plain prose — the display form of a note's text.

    ``[[a/b|c]]`` -> ``c`` and ``[[a/b]]`` -> ``b`` (path separators and hyphens become spaces).
    Used when note text is shown to a model: bracket syntax and directory paths are vault
    plumbing, and a dangling ``[[concept/x]]`` in a prompt spends tokens on punctuation while
    reading as a literal path the model might try to act on.

    This is deliberately *not* the term-extraction form. Matching strips links entirely (see
    ``recall._prose``) so a note that merely references a topic does not rank as though it were
    about it; display keeps the link's name so the sentence still reads as English.
    """
    def repl(match: re.Match) -> str:
        inner = match.group(1)
        if "|" in inner:
            return inner.split("|", 1)[1].strip()
        return inner.rsplit("/", 1)[-1].replace("-", " ").strip()

    return _WIKILINK_RE.sub(repl, text or "")


def strip_title_overlap(text: str, title: str, *, min_remaining: int = 12) -> str:
    """Drop a leading repeat of ``title`` from ``text``.

    Rendering a note as ``title: content`` is the readable form, but migration derives the title
    from the note's opening words, so the pair otherwise reads
    ``PULSE forgets exponentially: PULSE forgets exponentially: retrievability…``.

    Applied at render time rather than at write time on purpose: the vault keeps the full entry
    (lossless, and identical to what the user wrote), and only the prompt-facing rendering is
    de-duplicated.

    The comparison is word-by-word so whitespace and punctuation differences do not defeat the
    match, and the text is returned unchanged when stripping would leave too little behind to be
    worth showing — a note that is *only* its opening clause must not render as empty.
    """
    original = text or ""
    body = " ".join(original.split())
    title_words = " ".join((title or "").split()).split()
    if not body or not title_words:
        return original

    tokens = list(re.finditer(r"\S+", body))
    if len(tokens) < len(title_words):
        return original

    for index, word in enumerate(title_words):
        if tokens[index].group(0).strip("-—:;,.").lower() != word.strip("-—:;,.").lower():
            return original

    remainder = body[tokens[len(title_words) - 1].end() :].lstrip(" \t-—:;,.")
    if len(remainder) < min_remaining:
        return original
    return remainder


def normalize_node_id(node_id: str) -> str:
    """Canonical node id: no ``.md``, forward slashes, no leading/trailing slash.

    Path segments keep their original case (macOS/Windows filesystems are
    case-insensitive, and rewriting case would silently rename user files).
    """
    text = str(node_id or "").strip().replace("\\", "/")
    if text.lower().endswith(".md"):
        text = text[:-3]
    parts = [p.strip() for p in text.split("/") if p.strip() and p.strip() != "."]
    return "/".join(parts)


# ── wikilinks ──────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class WikiLink:
    """One ``[[...]]`` occurrence."""

    target: str          # normalised node id / page name
    alias: Optional[str] = None
    heading: Optional[str] = None
    raw: str = ""

    def display(self) -> str:
        return self.alias or self.target


def extract_wikilinks(body: str) -> List[WikiLink]:
    """All wikilinks in ``body``, ignoring fenced code blocks.

    Same target may appear more than once; callers dedupe if they need uniqueness.
    """
    if not body or "[[" not in body:
        return []
    links: List[WikiLink] = []
    in_fence = False
    for line in body.splitlines():
        if _FENCE_RE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        for match in _WIKILINK_RE.finditer(line):
            inner = match.group(1).strip()
            if not inner:
                continue
            alias = None
            head, sep, tail = inner.partition("|")
            if sep:
                inner = head
                alias = tail.strip() or None
            heading = None
            head, sep, tail = inner.partition("#")
            if sep:
                inner = head
                heading = tail.strip() or None
            target = normalize_node_id(inner)
            if not target:
                continue
            links.append(WikiLink(target=target, alias=alias, heading=heading, raw=match.group(0)))
    return links


def unique_link_targets(body: str) -> List[str]:
    """Deduped, order-preserving link targets from ``body``."""
    seen: Dict[str, None] = {}
    for link in extract_wikilinks(body):
        seen.setdefault(link.target, None)
    return list(seen)


def rewrite_link_target(body: str, old_target: str, new_target: str) -> Tuple[str, int]:
    """Rewrite every ``[[old]]``/``[[old|alias]]``/``[[old#h]]`` to ``new_target``.

    Only the target is replaced — alias and heading are preserved. Returns
    (new_body, replacements). Protected by the same fence rule as extraction.
    """
    old_norm, new_norm = normalize_node_id(old_target), normalize_node_id(new_target)
    if not old_norm or not new_norm or old_norm == new_norm:
        return body, 0
    out_lines: List[str] = []
    count = 0
    in_fence = False
    for line in body.splitlines(keepends=True):
        stripped = line.rstrip("\n")
        if _FENCE_RE.match(stripped):
            in_fence = not in_fence
            out_lines.append(line)
            continue
        if in_fence or "[[" not in line:
            out_lines.append(line)
            continue

        def _sub(match: "re.Match[str]") -> str:
            nonlocal count
            inner = match.group(1).strip()
            alias: Optional[str] = None
            if "|" in inner:
                inner, alias = inner.split("|", 1)
            heading: Optional[str] = None
            if "#" in inner:
                inner, heading = inner.split("#", 1)
            if normalize_node_id(inner) != old_norm:
                return match.group(0)
            count += 1
            rebuilt = new_norm
            if heading:
                rebuilt += "#" + heading
            if alias:
                rebuilt += "|" + alias
            return "[[" + rebuilt + "]]"

        out_lines.append(_WIKILINK_RE.sub(_sub, line))
    return "".join(out_lines), count
