#!/usr/bin/env python3
"""Regression tests for the repository invariant checker."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from scripts import check_invariants


class FrontmatterParsingTests(unittest.TestCase):
    def write_skill(self, contents: str) -> Path:
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        path = Path(temp_dir.name) / "SKILL.md"
        path.write_text(contents, encoding="utf-8")
        return path

    def load_description(self, yaml_body: str) -> str:
        path = self.write_skill(f"---\nname: example\n{yaml_body}\n---\n# Body\n")
        frontmatter = check_invariants.load_skill_frontmatter(path)
        return frontmatter["description"]  # type: ignore[return-value]

    def test_inline_yaml_comments_are_not_counted(self) -> None:
        self.assertEqual(
            self.load_description("description: short # comment"),
            "short",
        )

    def test_yaml_quote_escapes_use_yaml_semantics(self) -> None:
        self.assertEqual(
            self.load_description("description: 'it''s ok'"),
            "it's ok",
        )
        self.assertEqual(
            self.load_description('description: "a\\nb"'),
            "a\nb",
        )

    def test_block_scalar_comment_dash_does_not_change_chomping(self) -> None:
        self.assertEqual(
            self.load_description(
                "description: > # human-readable - comment\n"
                "  line one\n"
                "  line two"
            ),
            "line one line two\n",
        )

    def test_duplicate_keys_are_rejected(self) -> None:
        path = self.write_skill(
            "---\n"
            "name: example\n"
            "description: first\n"
            "description: second\n"
            "---\n"
        )
        with self.assertRaises(ValueError):
            check_invariants.load_skill_frontmatter(path)

    def test_extra_fields_are_rejected(self) -> None:
        path = self.write_skill(
            "---\n"
            "name: example\n"
            "description: valid\n"
            "keywords: invalid-extra-field\n"
            "---\n"
        )
        with self.assertRaises(ValueError):
            check_invariants.load_skill_frontmatter(path)

    def test_description_at_maximum_length_is_accepted(self) -> None:
        description = "x" * check_invariants.MAX_DESCRIPTION_CHARS
        self.assertFalse(check_invariants.description_exceeds_limit(description))

    def test_description_over_maximum_length_exceeds_limit(self) -> None:
        description = "x" * (check_invariants.MAX_DESCRIPTION_CHARS + 1)
        self.assertTrue(check_invariants.description_exceeds_limit(description))


class AuditChecklistTests(unittest.TestCase):
    def test_final_audit_checklist_section_accepts_suffix(self) -> None:
        lines = [
            "# Rule",
            "## Body",
            "content",
            "## Audit checklist - quality gate",
            "- [ ] checked",
        ]
        self.assertTrue(
            check_invariants.last_level_two_heading(lines).startswith("## Audit checklist")
        )


if __name__ == "__main__":
    unittest.main()
