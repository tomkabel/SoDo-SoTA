#!/usr/bin/env bash
#
# Stable entry point for SOTA-skills repository invariants. Run by pre-commit,
# CI, and contributors working locally.
set -euo pipefail

cd "$(git rev-parse --show-toplevel)"

PYTHON_BIN="${PYTHON:-python3}"
exec "$PYTHON_BIN" scripts/check_invariants.py "$@"
