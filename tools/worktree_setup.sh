#!/usr/bin/env bash
# Create a ready-to-build git worktree for a parallel builder (CLAUDE.md 4.2).
#
#   bash tools/worktree_setup.sh BRANCH PATH
#
# Creates PATH as a new worktree of this repo on a new BRANCH branched from main,
# builds its .venv from requirements.txt with uv, links in the git-ignored on-disk
# payloads listed in tools/worktree_payloads.txt, and runs the test suite there.
#
# Nothing in the main working tree is written: the worktree is registered in the
# shared .git directory and every payload is a symlink pointing back at the main
# tree. Nothing under third_party/ is modified.
set -euo pipefail

usage() {
  echo "usage: bash tools/worktree_setup.sh BRANCH PATH" >&2
  echo "  e.g. bash tools/worktree_setup.sh wt/t042 /home/alois/Desktop/ludo-g1-wt-t042" >&2
}

if [ "$#" -ne 2 ]; then
  usage
  exit 2
fi

BRANCH="$1"
WT="$2"
BASE_BRANCH="${WORKTREE_BASE_BRANCH:-main}"
PAYLOADS_REL="tools/worktree_payloads.txt"

die() { echo "worktree_setup: $*" >&2; exit 1; }

# --- locate the main working tree through the shared git directory -----------
git rev-parse --git-common-dir >/dev/null 2>&1 || die "not inside a git repository"
COMMON_GIT="$(cd "$(git rev-parse --git-common-dir)" && pwd -P)"
MAIN="$(dirname "$COMMON_GIT")"
[ -d "$MAIN" ] || die "cannot locate the main working tree (git dir: $COMMON_GIT)"

# The payload list travels with this script, not with whatever revision the main tree
# happens to have checked out.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
PAYLOADS="$SCRIPT_DIR/$(basename "$PAYLOADS_REL")"
[ -f "$PAYLOADS" ] || PAYLOADS="$MAIN/$PAYLOADS_REL"
[ -f "$PAYLOADS" ] || die "missing $PAYLOADS_REL next to this script or in $MAIN"

UV="${UV:-$(command -v uv || true)}"
[ -n "$UV" ] && [ -x "$UV" ] || UV="$HOME/.local/bin/uv"
[ -x "$UV" ] || die "uv not found (looked at \$UV, PATH and ~/.local/bin/uv); see docs/setup.md"

# --- refuse to clobber anything ----------------------------------------------
if [ -e "$WT" ] || [ -L "$WT" ]; then
  die "$WT already exists; pick another path or tear it down with tools/worktree_teardown.sh"
fi
if git -C "$MAIN" show-ref --verify --quiet "refs/heads/$BRANCH"; then
  die "branch $BRANCH already exists; pick another name"
fi
git -C "$MAIN" show-ref --verify --quiet "refs/heads/$BASE_BRANCH" \
  || die "base branch $BASE_BRANCH does not exist"

# --- create the worktree ------------------------------------------------------
echo "worktree_setup: creating worktree $WT on $BRANCH from $BASE_BRANCH"
git -C "$MAIN" worktree add -b "$BRANCH" "$WT" "$BASE_BRANCH"
WT="$(cd "$WT" && pwd -P)"

# --- virtualenv ---------------------------------------------------------------
echo "worktree_setup: creating .venv with $UV"
"$UV" venv --python 3.10 "$WT/.venv"
"$UV" pip install --python "$WT/.venv/bin/python" -r "$WT/requirements.txt"

# --- payloads -----------------------------------------------------------------
link_dir_payload() {
  # $1 repo-relative path of a directory in the main tree
  local rel="$1" src dst entry
  src="$MAIN/$rel"
  dst="$WT/$rel"
  rm -rf "$dst"
  mkdir -p "$dst"
  local n=0
  while IFS= read -r entry; do
    ln -sfn "$entry" "$dst/$(basename "$entry")"
    n=$((n + 1))
  done < <(find "$src" -mindepth 1 -maxdepth 1)
  echo "worktree_setup: payload dir  $rel  ($n symlinks -> main tree)"
}

link_file_payload() {
  local rel="$1" src dst
  src="$MAIN/$rel"
  dst="$WT/$rel"
  mkdir -p "$(dirname "$dst")"
  ln -sfn "$src" "$dst"
  echo "worktree_setup: payload file $rel  (symlink -> main tree)"
}

while IFS= read -r line || [ -n "$line" ]; do
  line="${line%%$'\r'}"
  case "$line" in ''|'#'*) continue ;; esac
  rel="${line%/}"
  if [ -d "$MAIN/$rel" ]; then
    link_dir_payload "$rel"
  elif [ -e "$MAIN/$rel" ]; then
    link_file_payload "$rel"
  else
    echo "worktree_setup: WARNING payload not present in the main tree, skipped: $rel" >&2
  fi
done < "$PAYLOADS"

# --- the worktree must be clean: every payload is git-ignored ------------------
if [ -n "$(git -C "$WT" status --porcelain)" ]; then
  echo "worktree_setup: WARNING git status in the new worktree is not clean:" >&2
  git -C "$WT" status --porcelain >&2
fi

# --- run the suite ------------------------------------------------------------
echo "worktree_setup: running the test suite in $WT"
set +e
(cd "$WT" && .venv/bin/python -m pytest -q)
rc=$?
set -e

echo
if [ "$rc" -eq 0 ]; then
  echo "worktree_setup: OK  worktree=$WT  branch=$BRANCH  suite=green"
else
  echo "worktree_setup: FAIL  worktree=$WT  branch=$BRANCH  suite=red (pytest exit $rc)" >&2
fi
exit "$rc"
