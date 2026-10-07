"""Tests for the brain vault frontmatter/wikilink codec.

The codec is hand-rolled to avoid a YAML dependency, so it is tested as a real codec:
round-trips, hostile input, and the exact forms Obsidian writes.
"""

from __future__ import annotations

import unittest

from agent.brain.parser import (
    compose_markdown,
    extract_wikilinks,
    normalize_node_id,
    parse_frontmatter_and_body,
    parse_scalar,
    render_frontmatter,
    rewrite_link_target,
    slugify,
    unique_link_targets,
)


class TestScalars(unittest.TestCase):
    def test_roundtrip_preserves_types(self):
        cases = [1, 2.5, True, False, None, "plain", "12", "true"]
        for value in cases:
            rendered = render_frontmatter({"k": value})
            parsed = parse_frontmatter_and_body(f"---\n{rendered}\n---\nbody")[0]
            self.assertEqual(parsed["k"], value, f"round-trip failed for {value!r}")

    def test_tricky_strings_are_quoted_and_restored(self):
        for value in ["null", "yes: no", "a # comment", " padded ", "[not, a, list]"]:
            rendered = render_frontmatter({"k": value})
            parsed = parse_frontmatter_and_body(f"---\n{rendered}\n---\n")[0]
            self.assertEqual(parsed["k"], value, f"round-trip failed for {value!r}")

    def test_empty_string_roundtrips(self):
        rendered = render_frontmatter({"k": ""})
        parsed = parse_frontmatter_and_body(f"---\n{rendered}\n---\n")[0]
        self.assertEqual(parsed["k"], "")

    def test_parse_scalar_never_raises_on_junk(self):
        for junk in ["", ":::", "{a: b}", "[[x]]", "\u0000", "1e999"]:
            parse_scalar(junk)  # must not raise


class TestFrontmatter(unittest.TestCase):
    def test_no_frontmatter_returns_body_unchanged(self):
        meta, body = parse_frontmatter_and_body("# Just a note\n\ntext")
        self.assertEqual(meta, {})
        self.assertEqual(body, "# Just a note\n\ntext")

    def test_empty_input(self):
        self.assertEqual(parse_frontmatter_and_body(""), ({}, ""))

    def test_unterminated_frontmatter_is_not_consumed(self):
        raw = "---\ntitle: x\n\nbody without closing fence"
        meta, body = parse_frontmatter_and_body(raw)
        self.assertEqual(meta, {})
        self.assertEqual(body, raw)

    def test_block_list_and_inline_list(self):
        raw = "---\ntags:\n  - one\n  - two\naliases: [a, b]\n---\nbody"
        meta, body = parse_frontmatter_and_body(raw)
        self.assertEqual(meta["tags"], ["one", "two"])
        self.assertEqual(meta["aliases"], ["a", "b"])
        self.assertEqual(body, "body")

    def test_key_with_no_value_is_null(self):
        meta = parse_frontmatter_and_body("---\nsuperseded_by:\n---\n")[0]
        self.assertIsNone(meta["superseded_by"])

    def test_quoted_list_items_with_commas(self):
        meta = parse_frontmatter_and_body('---\ntags: ["a, b", c]\n---\n')[0]
        self.assertEqual(meta["tags"], ["a, b", "c"])

    def test_comments_and_blank_lines_ignored(self):
        meta = parse_frontmatter_and_body("---\n# comment\n\ntitle: t\n---\n")[0]
        self.assertEqual(meta, {"title": "t"})

    def test_bom_and_crlf_tolerated(self):
        meta, body = parse_frontmatter_and_body("\ufeff---\r\ntitle: t\r\n---\r\nbody")
        self.assertEqual(meta["title"], "t")
        self.assertIn("body", body)

    def test_compose_markdown_is_reparseable(self):
        text = compose_markdown({"title": "T", "tags": ["x"]}, "# Heading\n\n[[other]]\n")
        meta, body = parse_frontmatter_and_body(text)
        self.assertEqual(meta["title"], "T")
        self.assertEqual(meta["tags"], ["x"])
        self.assertIn("[[other]]", body)
        self.assertTrue(text.endswith("\n"))


class TestNodeIds(unittest.TestCase):
    def test_normalisation(self):
        cases = {
            "user/preferences.md": "user/preferences",
            "/self//identity.md": "self/identity",
            "a\\b.md": "a/b",
            "  spaced/name  ": "spaced/name",
            "concept/x.MD": "concept/x",
        }
        for raw, expected in cases.items():
            self.assertEqual(normalize_node_id(raw), expected)

    def test_case_is_preserved_so_files_are_not_silently_renamed(self):
        self.assertEqual(normalize_node_id("User/Preferences.md"), "User/Preferences")

    def test_slugify(self):
        self.assertEqual(slugify("Hello, World!"), "hello-world")
        self.assertEqual(slugify(""), "untitled")
        self.assertEqual(slugify("  ---x---  "), "x")
        self.assertNotIn("/", slugify("a/b"))


class TestWikiLinks(unittest.TestCase):
    def test_plain_alias_heading(self):
        links = extract_wikilinks("see [[user/preferences]] and [[a|Alias]] and [[b#Head]]")
        self.assertEqual([l.target for l in links], ["user/preferences", "a", "b"])
        self.assertEqual(links[1].alias, "Alias")
        self.assertEqual(links[2].heading, "Head")

    def test_alias_and_heading_together(self):
        link = extract_wikilinks("[[a#Head|Alias]]")[0]
        self.assertEqual((link.target, link.heading, link.alias), ("a", "Head", "Alias"))

    def test_fenced_code_is_ignored(self):
        body = "real [[a]]\n```\nfake [[b]]\n```\nalso [[c]]\n~~~\nfake [[d]]\n~~~\n"
        self.assertEqual([l.target for l in extract_wikilinks(body)], ["a", "c"])

    def test_empty_and_malformed_links_ignored(self):
        self.assertEqual(extract_wikilinks("[[]] [[   ]] [[|alias]]"), [])

    def test_no_links_fast_path(self):
        self.assertEqual(extract_wikilinks("nothing here"), [])

    def test_unique_link_targets_preserves_order(self):
        self.assertEqual(unique_link_targets("[[b]] [[a]] [[b]]"), ["b", "a"])

    def test_rewrite_preserves_alias_and_heading(self):
        body = "[[old]] [[old|Alias]] [[old#Head]] [[other]]"
        new, count = rewrite_link_target(body, "old", "new")
        self.assertEqual(count, 3)
        self.assertIn("[[new]]", new)
        self.assertIn("[[new|Alias]]", new)
        self.assertIn("[[new#Head]]", new)
        self.assertIn("[[other]]", new)

    def test_rewrite_ignores_code_fences(self):
        body = "```\n[[old]]\n```\n[[old]]"
        new, count = rewrite_link_target(body, "old", "new")
        self.assertEqual(count, 1)
        self.assertIn("[[old]]", new)

    def test_rewrite_no_match_is_noop(self):
        body = "[[keep]]"
        new, count = rewrite_link_target(body, "absent", "new")
        self.assertEqual((new, count), (body, 0))

    def test_rewrite_same_target_is_noop(self):
        body = "[[same]]"
        self.assertEqual(rewrite_link_target(body, "same", "same"), (body, 0))

    def test_rewrite_normalises_the_match(self):
        body = "[[Old.md]]"
        new, count = rewrite_link_target(body, "Old", "new")
        self.assertEqual(count, 1)
        self.assertIn("[[new]]", new)


if __name__ == "__main__":
    unittest.main()
