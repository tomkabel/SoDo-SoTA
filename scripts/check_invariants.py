#!/usr/bin/env python3
"""Enforce repository invariants for the SOTA-skills library."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path
from typing import Iterable

try:
    import yaml
except ModuleNotFoundError:
    print(
        "ERROR: PyYAML is required for invariant checks. "
        "Install it with `python3 -m pip install PyYAML==6.0.2`, or run "
        "`pre-commit run --all-files` so pre-commit provides the hook environment.",
        file=sys.stderr,
    )
    sys.exit(2)


MAX_LINES = 500
MAX_DESCRIPTION_CHARS = 1024
REQUIRED_SKILL_FIELDS = {"name", "description"}
AUDIT_CHECKLIST_HEADING_PREFIX = "## Audit checklist"
EXCLUDED_DENYLIST_FILES = {
    "scripts/check-invariants.sh",
    "scripts/check_invariants.py",
}
INTERNAL_DENYLIST = re.compile(
    r"Probably\.Group|dn-platform|dn-go-api|dn-fe|ContextIPS|AethelGard|"
    r"Vuln-AID|JARVIS|\bMemex\b|the user runs|the user operates"
)


class UniqueKeySafeLoader(yaml.SafeLoader):
    """Safe YAML loader that rejects duplicate mapping keys."""


def construct_mapping_without_duplicate_keys(
    loader: UniqueKeySafeLoader,
    node: yaml.nodes.MappingNode,
    deep: bool = False,
) -> dict[object, object]:
    mapping: dict[object, object] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                f"found duplicate key {key!r}",
                key_node.start_mark,
            )
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


UniqueKeySafeLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    construct_mapping_without_duplicate_keys,
)


def note(message: str) -> None:
    print(f"    {message}")


def run_git_ls_files(*pathspecs: str) -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", *pathspecs],
        check=True,
        capture_output=True,
        text=True,
    )
    return [Path(line) for line in result.stdout.splitlines() if line]


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def frontmatter_text(path: Path) -> str:
    lines = read_text(path).splitlines()
    if not lines or lines[0].strip() != "---":
        raise ValueError("missing opening frontmatter delimiter")

    for idx, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            return "\n".join(lines[1:idx]) + "\n"

    raise ValueError("missing closing frontmatter delimiter")


def load_skill_frontmatter(path: Path) -> dict[str, object]:
    try:
        data = yaml.load(frontmatter_text(path), Loader=UniqueKeySafeLoader)
    except yaml.YAMLError as exc:
        raise ValueError(f"invalid YAML frontmatter: {exc}") from exc

    if not isinstance(data, dict):
        raise ValueError("frontmatter must be a mapping")

    keys = set(data.keys())
    if keys != REQUIRED_SKILL_FIELDS:
        missing = REQUIRED_SKILL_FIELDS - keys
        extra = keys - REQUIRED_SKILL_FIELDS
        details = []
        if missing:
            details.append(f"missing {', '.join(sorted(missing))}")
        if extra:
            details.append(f"extra {', '.join(sorted(str(key) for key in extra))}")
        raise ValueError("frontmatter must contain exactly name and description fields (" + "; ".join(details) + ")")

    if not isinstance(data["name"], str) or not data["name"].strip():
        raise ValueError("name must be a non-empty string")

    if not isinstance(data["description"], str) or not data["description"].strip():
        raise ValueError("description must be a non-empty string")

    return data


def check_line_budget() -> bool:
    print(f"[1/4] Markdown files <= {MAX_LINES} lines")
    failed = False

    for path in run_git_ls_files("*.md"):
        with path.open("rb") as handle:
            line_count = sum(1 for _ in handle)
        if line_count > MAX_LINES:
            note(f"OVER {MAX_LINES} ({line_count} lines): {path}")
            failed = True

    if not failed:
        note("ok")
    return not failed


def check_skill_descriptions() -> bool:
    print(f"[2/4] Skill descriptions < {MAX_DESCRIPTION_CHARS} characters")
    failed = False

    for path in run_git_ls_files("skills/*/SKILL.md"):
        try:
            frontmatter = load_skill_frontmatter(path)
        except ValueError as exc:
            note(f"INVALID frontmatter ({exc}): {path}")
            failed = True
            continue

        description = frontmatter["description"]
        assert isinstance(description, str)
        length = len(description)
        if length >= MAX_DESCRIPTION_CHARS:
            note(f"TOO LONG ({length} chars, must be < {MAX_DESCRIPTION_CHARS}): {path}")
            failed = True

    if not failed:
        note("ok")
    return not failed


def last_level_two_heading(lines: Iterable[str]) -> str | None:
    heading = None
    for line in lines:
        if line.startswith("## "):
            heading = line.strip()
    return heading


def check_audit_checklists() -> bool:
    print("[3/4] Every skills/*/rules/*.md ends with an '## Audit checklist' section")
    failed = False

    for path in run_git_ls_files("skills/*/rules/*.md"):
        heading = last_level_two_heading(read_text(path).splitlines())
        if not heading or not heading.startswith(AUDIT_CHECKLIST_HEADING_PREFIX):
            note(f"MISSING FINAL '{AUDIT_CHECKLIST_HEADING_PREFIX}' SECTION: {path}")
            failed = True

    if not failed:
        note("ok")
    return not failed


def check_internal_name_leaks() -> bool:
    print("[4/4] No internal-name leaks")
    failed = False
    hits: list[str] = []

    for path in run_git_ls_files():
        if path.as_posix() in EXCLUDED_DENYLIST_FILES:
            continue
        try:
            lines = read_text(path).splitlines()
        except UnicodeDecodeError:
            continue
        for line_no, line in enumerate(lines, start=1):
            if INTERNAL_DENYLIST.search(line):
                hits.append(f"{path}:{line_no}:{line}")

    if hits:
        note("Internal reference(s) found - keep the library generic:")
        for hit in hits:
            print(f"      {hit}")
        failed = True

    if not failed:
        note("ok")
    return not failed


def main() -> int:
    checks = [
        check_line_budget(),
        check_skill_descriptions(),
        check_audit_checklists(),
        check_internal_name_leaks(),
    ]

    print()
    if not all(checks):
        print("FAIL: repository invariants violated (see above).")
        return 1

    print("PASS: all repository invariants satisfied.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
