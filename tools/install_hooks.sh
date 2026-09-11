#!/usr/bin/env bash
# Install the repo's git hooks. Run once after cloning: bash tools/install_hooks.sh
set -eu

REPO="$(git rev-parse --show-toplevel)"
SRC="$REPO/tools/pre-commit.sh"
DST="$REPO/.git/hooks/pre-commit"

install -m 0755 "$SRC" "$DST"
echo "installed $DST (from tools/pre-commit.sh)"
