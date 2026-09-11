#!/usr/bin/env bash
# Remove a builder worktree created by tools/worktree_setup.sh.
#
#   bash tools/worktree_teardown.sh PATH
#
# Refuses if the worktree has uncommitted changes (tracked or untracked; git-ignored
# files such as .venv/ and the linked payloads do not count). Removes the worktree,
# then deletes its branch only if `git branch --merged main` lists it -- otherwise the
# branch is left alone and the command to merge it is printed.
set -euo pipefail

usage() {
  echo "usage: bash tools/worktree_teardown.sh PATH" >&2
  echo "  e.g. bash tools/worktree_teardown.sh /home/alois/Desktop/ludo-g1-wt-t042" >&2
}

if [ "$#" -ne 1 ]; then
  usage
  exit 2
fi

WT_ARG="$1"
BASE_BRANCH="${WORKTREE_BASE_BRANCH:-main}"

die() { echo "worktree_teardown: $*" >&2; exit 1; }

git rev-parse --git-common-dir >/dev/null 2>&1 || die "not inside a git repository"
COMMON_GIT="$(cd "$(git rev-parse --git-common-dir)" && pwd -P)"
MAIN="$(dirname "$COMMON_GIT")"

[ -d "$WT_ARG" ] || die "$WT_ARG does not exist"
WT="$(cd "$WT_ARG" && pwd -P)"

[ "$WT" != "$MAIN" ] || die "$WT is the main working tree, not a worktree; refusing"

# Must be a registered worktree of THIS repository.
if ! git -C "$MAIN" worktree list --porcelain | grep -Fxq "worktree $WT"; then
  die "$WT is not a registered worktree of $MAIN"
fi

# Uncommitted work is never thrown away.
dirty="$(git -C "$WT" status --porcelain)"
if [ -n "$dirty" ]; then
  echo "worktree_teardown: $WT has uncommitted changes; refusing to remove it." >&2
  echo "$dirty" >&2
  exit 1
fi

BRANCH="$(git -C "$WT" rev-parse --abbrev-ref HEAD)"

echo "worktree_teardown: removing worktree $WT (branch $BRANCH)"
git -C "$MAIN" worktree remove "$WT"

if [ "$BRANCH" = "HEAD" ]; then
  echo "worktree_teardown: worktree was on a detached HEAD; no branch to delete."
  echo "worktree_teardown: OK  removed=$WT  branch=(detached, nothing deleted)"
  exit 0
fi
if [ "$BRANCH" = "$BASE_BRANCH" ]; then
  echo "worktree_teardown: worktree was on $BASE_BRANCH itself; branch kept."
  echo "worktree_teardown: OK  removed=$WT  branch=$BRANCH (kept)"
  exit 0
fi

if git -C "$MAIN" branch --merged "$BASE_BRANCH" --format='%(refname:short)' \
   | grep -Fxq "$BRANCH"; then
  git -C "$MAIN" branch -d "$BRANCH"
  echo "worktree_teardown: OK  removed=$WT  branch=$BRANCH deleted (merged into $BASE_BRANCH)"
else
  echo "worktree_teardown: branch $BRANCH is NOT merged into $BASE_BRANCH; keeping it."
  echo "worktree_teardown: merge it with:"
  echo "    git -C $MAIN merge --no-ff $BRANCH"
  echo "  then delete it with:"
  echo "    git -C $MAIN branch -d $BRANCH"
  echo "worktree_teardown: OK  removed=$WT  branch=$BRANCH (kept, unmerged)"
fi
exit 0
