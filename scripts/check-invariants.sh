#!/usr/bin/env bash
#
# Enforce SOTA-skills repository invariants. Run by pre-commit and CI.
# Exits non-zero (and prints offenders) if any invariant is violated.
#
# Invariants:
#   1. Every tracked *.md is <= 500 lines  (skills load incrementally).
#   2. Every skills/*/SKILL.md description is < 1024 characters.
#   3. Every skills/*/rules/*.md ends with an "## Audit checklist".
#   4. No internal/private references leak in (the library stays generic).
#
# Portable to macOS bash 3.2 (no mapfile/associative arrays).
set -euo pipefail

cd "$(git rev-parse --show-toplevel)"

MAX_LINES=500
MAX_DESCRIPTION_CHARS=1024
fail=0
note() { printf '    %s\n' "$1"; }

# --- 1. Line budget -------------------------------------------------------
echo "[1/4] Markdown files <= ${MAX_LINES} lines"
over=0
while IFS= read -r f; do
  n=$(wc -l < "$f")
  if [ "$n" -gt "$MAX_LINES" ]; then
    note "OVER ${MAX_LINES} (${n} lines): $f"
    over=1
  fi
done < <(git ls-files '*.md')
if [ "$over" -eq 0 ]; then echo "    ok"; else fail=1; fi

# --- 2. Skill description length ------------------------------------------
echo "[2/4] Skill descriptions < ${MAX_DESCRIPTION_CHARS} characters"
if ! python3 - "$MAX_DESCRIPTION_CHARS" <<'PY'
import re
import subprocess
import sys
from pathlib import Path

limit = int(sys.argv[1])


def frontmatter_lines(path):
    lines = Path(path).read_text(encoding="utf-8").splitlines()
    if not lines or lines[0].strip() != "---":
        raise ValueError("missing opening frontmatter delimiter")
    for idx in range(1, len(lines)):
        if lines[idx].strip() == "---":
            return lines[1:idx]
    raise ValueError("missing closing frontmatter delimiter")


def strip_quotes(value):
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def deindent_block(lines):
    indents = [len(line) - len(line.lstrip(" ")) for line in lines if line.strip()]
    if not indents:
        return []
    indent = min(indents)
    return [line[indent:] if len(line) >= indent else "" for line in lines]


def parse_block_scalar(header, lines):
    style = header[0]
    chomp = "clip"
    if "-" in header:
        chomp = "strip"
    elif "+" in header:
        chomp = "keep"

    block_lines = deindent_block(lines)
    if style == "|":
        text = "\n".join(block_lines)
    else:
        pieces = []
        for idx, line in enumerate(block_lines):
            pieces.append(line)
            if idx == len(block_lines) - 1:
                continue
            next_line = block_lines[idx + 1]
            pieces.append("\n" if line == "" or next_line == "" else " ")
        text = "".join(pieces)

    if chomp == "strip":
        return text
    return text + "\n"


def description_from_frontmatter(lines):
    for idx, line in enumerate(lines):
        if not line.startswith("description:"):
            continue

        value = line.split(":", 1)[1].strip()
        if value.startswith((">", "|")):
            block = []
            for continuation in lines[idx + 1 :]:
                if (
                    continuation
                    and not continuation.startswith((" ", "\t"))
                    and re.match(r"^[A-Za-z0-9_-]+:", continuation)
                ):
                    break
                block.append(continuation)
            return parse_block_scalar(value, block)
        return strip_quotes(value)
    raise ValueError("missing description field")


failed = False
paths = subprocess.check_output(
    ["git", "ls-files", "skills/*/SKILL.md"],
    text=True,
).splitlines()

for path in paths:
    try:
        description = description_from_frontmatter(frontmatter_lines(path))
    except Exception as exc:
        print(f"    INVALID description ({exc}): {path}")
        failed = True
        continue

    length = len(description)
    if length >= limit:
        print(f"    TOO LONG ({length} chars, must be < {limit}): {path}")
        failed = True

if not failed:
    print("    ok")
sys.exit(1 if failed else 0)
PY
then
  fail=1
fi

# --- 3. Audit checklist in every rules file -------------------------------
echo "[3/4] Every skills/*/rules/*.md has an '## Audit checklist'"
missing=0
while IFS= read -r f; do
  if ! grep -qE '^## Audit checklist' "$f"; then
    note "MISSING '## Audit checklist': $f"
    missing=1
  fi
done < <(git ls-files 'skills/*/rules/*.md')
if [ "$missing" -eq 0 ]; then echo "    ok"; else fail=1; fi

# --- 4. No internal/private references -------------------------------------
# Keep the library generic and shareable. This guards against re-introducing
# the personal stack/project names that were scrubbed before going public.
echo "[4/4] No internal-name leaks"
DENY='Probably\.Group|dn-platform|dn-go-api|dn-fe|ContextIPS|AethelGard|Vuln-AID|JARVIS|\bMemex\b|the user runs|the user operates'
# This script necessarily contains the patterns above, so exclude it from its
# own scan (it is small and reviewed; everything else is checked).
hits=$(git ls-files -z -- ':(exclude)scripts/check-invariants.sh' \
       | xargs -0 grep -InE "$DENY" 2>/dev/null || true)
if [ -n "$hits" ]; then
  note "Internal reference(s) found — keep the library generic:"
  printf '%s\n' "$hits" | sed 's/^/      /'
  fail=1
else
  echo "    ok"
fi

# --- Result ---------------------------------------------------------------
echo
if [ "$fail" -ne 0 ]; then
  echo "FAIL: repository invariants violated (see above)."
  exit 1
fi
echo "PASS: all repository invariants satisfied."
