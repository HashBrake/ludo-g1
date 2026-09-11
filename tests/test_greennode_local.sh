#!/usr/bin/env bash
# Acceptance test for T-009: exercise cloud/greennode.sh end to end without credentials.
#
#   bash tests/test_greennode_local.sh          (also run by tests/test_greennode_local.py under pytest)
#
# Covers:
#   1. local-mode round trip  up -> train cloud/dummy_job.py -> down  produces data/checkpoints/dummy/result.txt
#   2. remote mode refuses to run without the credentials file, and says so pointing at Q-001
#   3. up does not push third_party/
#   4. the heartbeat lands in data/logs/greennode/ and ends in state=finished
#   5. train --detach returns before the job is over and status sees it running
#
# It never reads or writes the real ~/.config/ludo-g1/env: HOME and GREENNODE_ENV_FILE both point into
# a temporary directory that is removed on exit.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
GN="$REPO/cloud/greennode.sh"

TMP="$(mktemp -d -t ludo-t009-XXXXXX)"
FAKE_HOME="$TMP/home"
FAKE_REMOTE="$TMP/remote"
mkdir -p "$FAKE_HOME"

failures=0
cleanup() {
  rm -rf "$TMP"
  rm -rf "$REPO/data/checkpoints/dummy"
  rm -f "$REPO/data/logs/greennode"/t009-*
}
trap cleanup EXIT

pass() { echo "ok   - $*"; }
fail() { echo "FAIL - $*" >&2; failures=$((failures + 1)); }
check() { if [ "$1" = "0" ]; then pass "$2"; else fail "$2"; fi }

# Every invocation runs with a HOME that has no credentials file in it.
gn() {
  env HOME="$FAKE_HOME" \
      GREENNODE_ENV_FILE="$FAKE_HOME/.config/ludo-g1/env" \
      GREENNODE_TRANSPORT=local \
      GREENNODE_LOCAL_ROOT="$FAKE_REMOTE" \
      GREENNODE_HEARTBEAT_SECONDS=1 \
      "$GN" "$@"
}

echo "# repo:        $REPO"
echo "# fake remote: $FAKE_REMOTE"
echo

# --- 2. remote mode without credentials ---------------------------------------------------------
set +e
remote_out="$(env HOME="$FAKE_HOME" GREENNODE_ENV_FILE="$FAKE_HOME/.config/ludo-g1/env" \
  GREENNODE_TRANSPORT=remote "$GN" up 2>&1)"
remote_rc=$?
set -e
[ "$remote_rc" -ne 0 ] && check 0 "remote mode without the credentials file exits non-zero (rc=$remote_rc)" \
  || check 1 "remote mode without the credentials file exits non-zero (rc=$remote_rc)"
grep -q "Q-001" <<<"$remote_out" && check 0 "the refusal points at QUESTIONS.md Q-001" || check 1 "the refusal points at QUESTIONS.md Q-001 (got: $remote_out)"
grep -q "ludo-g1/env" <<<"$remote_out" && check 0 "the refusal names the credentials file" || check 1 "the refusal names the credentials file"
[ ! -e "$FAKE_HOME/.config/ludo-g1/env" ] && check 0 "the refusal did not create the credentials file" || check 1 "the refusal did not create the credentials file"
echo

# --- 1. local-mode round trip -------------------------------------------------------------------
rm -rf "$REPO/data/checkpoints/dummy"

gn up > "$TMP/up.log" 2>&1 && check 0 "up" || { check 1 "up"; cat "$TMP/up.log" >&2; }
[ -f "$FAKE_REMOTE/cloud/dummy_job.py" ] && check 0 "up pushed cloud/dummy_job.py to the fake remote" || check 1 "up pushed cloud/dummy_job.py to the fake remote"
[ -f "$FAKE_REMOTE/CLAUDE.md" ] && check 0 "up pushed the repo (CLAUDE.md present)" || check 1 "up pushed the repo (CLAUDE.md present)"

# --- 3. third_party is excluded -----------------------------------------------------------------
[ ! -e "$FAKE_REMOTE/third_party" ] && check 0 "up excluded third_party/" || check 1 "up excluded third_party/"
echo

# The literal acceptance chain: no --wait, no --timeout. train blocks until the job is over, so down
# cannot race it.
GREENNODE_JOB_ID="t009-roundtrip"
export GREENNODE_JOB_ID
gn train cloud/dummy_job.py --seconds 1 --note "T-009 local-mode round trip" \
  > "$TMP/train.log" 2>&1 && check 0 "train (dummy job, --seconds 1)" || { check 1 "train (dummy job, --seconds 1)"; cat "$TMP/train.log" >&2; }
grep -q "exit code 0" "$TMP/train.log" && check 0 "train reported exit code 0" || check 1 "train reported exit code 0"

# --- 4. heartbeat -------------------------------------------------------------------------------
hb="$REPO/data/logs/greennode/$GREENNODE_JOB_ID.heartbeat"
[ -f "$hb" ] && check 0 "heartbeat mirrored into data/logs/greennode/" || check 1 "heartbeat mirrored into data/logs/greennode/"
if [ -f "$hb" ]; then
  grep -q "state=finished exit=0" "$hb" && check 0 "heartbeat ends in state=finished exit=0: $(cat "$hb")" || check 1 "heartbeat ends in state=finished exit=0: $(cat "$hb")"
fi
echo

gn down > "$TMP/down.log" 2>&1 && check 0 "down" || { check 1 "down"; cat "$TMP/down.log" >&2; }

result="$REPO/data/checkpoints/dummy/result.txt"
if [ -f "$result" ]; then
  pass "ACCEPTANCE: data/checkpoints/dummy/result.txt exists after up -> train -> down"
  echo "--- result.txt ---"
  sed 's/^/    /' "$result"
  echo "------------------"
  grep -q "^hostname: " "$result" && check 0 "result.txt names the host that ran the job" || check 1 "result.txt names the host that ran the job"
else
  fail "ACCEPTANCE: $result does not exist"
fi
echo

gn status > "$TMP/status.log" 2>&1 && check 0 "status" || { check 1 "status"; cat "$TMP/status.log" >&2; }
grep -q "transport:   local" "$TMP/status.log" && check 0 "status reports the local transport" || check 1 "status reports the local transport"
grep -q "(absent)" "$TMP/status.log" && check 0 "status reports the credentials file as absent" || check 1 "status reports the credentials file as absent"
grep -q "$GREENNODE_JOB_ID" "$TMP/status.log" && check 0 "status lists the finished job" || check 1 "status lists the finished job"
echo

# --- 5. --detach returns before the job is over -------------------------------------------------
GREENNODE_JOB_ID="t009-detached"
start=$(date +%s)
gn train --detach cloud/dummy_job.py --seconds 5 > "$TMP/detach.log" 2>&1 \
  && check 0 "train --detach" || { check 1 "train --detach"; cat "$TMP/detach.log" >&2; }
elapsed=$(( $(date +%s) - start ))
[ "$elapsed" -lt 5 ] && check 0 "train --detach returned in ${elapsed}s, before the 5 s job finished" \
  || check 1 "train --detach returned in ${elapsed}s, expected < 5"
gn status "$GREENNODE_JOB_ID" > "$TMP/status2.log" 2>&1 || true
grep -q "state=running" "$TMP/status2.log" && check 0 "status shows the detached job running" \
  || check 1 "status shows the detached job running: $(cat "$TMP/status2.log")"
# Let it finish so the temporary directory is not removed under a live job.
sleep 6
echo

if [ "$failures" -eq 0 ]; then
  echo "all checks passed"
  exit 0
fi
echo "$failures check(s) failed" >&2
exit 1
