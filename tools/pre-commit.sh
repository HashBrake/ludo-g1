#!/usr/bin/env bash
# LUDO-G1 pre-commit hook: ruff + pytest must pass before any commit (CLAUDE.md section 7).
# Installed to .git/hooks/pre-commit by tools/install_hooks.sh.
set -u

REPO="$(git rev-parse --show-toplevel)"
VENV="$REPO/.venv"

if [ ! -x "$VENV/bin/python" ] || [ ! -x "$VENV/bin/ruff" ]; then
  echo "pre-commit: $VENV is missing or incomplete." >&2
  echo "pre-commit: create it with:  uv venv --python 3.10 && uv pip install -r requirements.txt" >&2
  echo "pre-commit: see docs/setup.md" >&2
  exit 1
fi

cd "$REPO" || exit 1

echo "pre-commit: ruff check ."
if ! "$VENV/bin/ruff" check .; then
  echo "pre-commit: ruff failed; commit aborted." >&2
  exit 1
fi

echo "pre-commit: pytest -q"
if ! "$VENV/bin/python" -m pytest -q; then
  echo "pre-commit: pytest failed; commit aborted." >&2
  exit 1
fi

echo "pre-commit: ok"
exit 0
