#!/usr/bin/env bash
# Supervises one training job on the remote root. Launched by cloud/greennode.sh train, never by hand.
#
#   bash cloud/job_wrapper.sh JOB_ID ROOT COMMAND [ARGS...]
#
# Writes, under ROOT/data/logs/greennode/:
#   JOB_ID.log        the job's merged stdout/stderr
#   JOB_ID.pid        the job's pid
#   JOB_ID.heartbeat  rewritten every GREENNODE_HEARTBEAT_SECONDS while the job runs, once more at the end
#   JOB_ID.exit       the job's exit code; its existence is the "job is over" signal greennode.sh polls
set -u

job_id="${1:?job id}"
root="${2:?root}"
shift 2
[ "$#" -ge 1 ] || { echo "job_wrapper: no command given" >&2; exit 2; }

log_dir="$root/data/logs/greennode"
mkdir -p "$log_dir"
log="$log_dir/$job_id.log"
heartbeat="$log_dir/$job_id.heartbeat"
interval="${GREENNODE_HEARTBEAT_SECONDS:-5}"

write_heartbeat() {
  # Rewritten in place, not appended: the file is a state line, not a history.
  printf '%s job=%s pid=%s state=%s%s\n' \
    "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$job_id" "${2:--}" "$1" "${3:-}" > "$heartbeat"
}

write_heartbeat starting
echo "=== $job_id: $* ===" > "$log"
"$@" >> "$log" 2>&1 &
job_pid=$!
echo "$job_pid" > "$log_dir/$job_id.pid"

(
  while kill -0 "$job_pid" 2>/dev/null; do
    write_heartbeat running "$job_pid"
    sleep "$interval"
  done
) &
heartbeat_pid=$!

wait "$job_pid"
rc=$?

kill "$heartbeat_pid" 2>/dev/null || true
wait "$heartbeat_pid" 2>/dev/null || true

write_heartbeat finished "$job_pid" " exit=$rc"
echo "$rc" > "$log_dir/$job_id.exit"
exit "$rc"
