#!/usr/bin/env bash
# LUDO-G1 cloud driver (CLAUDE.md 5.8): push data and code up, launch training, pull checkpoints down.
#
#   cloud/greennode.sh up                       sync data/raw + the repo (minus third_party) to the remote
#   cloud/greennode.sh train SCRIPT [ARGS...]   run SCRIPT on the remote (nohup + heartbeat); waits for it
#                                               unless --detach. --timeout N bounds only the waiting.
#   cloud/greennode.sh down                     sync the remote's data/checkpoints back into this repo
#   cloud/greennode.sh status [JOB_ID]          transport, roots, and the state of the known jobs
#
# Two transports (GREENNODE_TRANSPORT):
#   remote (default)  ssh + rsync to the Greennode VM; the job runs inside the pinned cloud/Dockerfile
#                     image. UNTESTED until credentials exist (agents/QUESTIONS.md Q-001).
#   local             no ssh, no rsync, no docker: cp into a local directory that stands in for the
#                     remote root, and run the job with this repo's own .venv python. This exists so
#                     the whole up -> train -> down flow can be exercised without credentials.
#
# Credentials live in ~/.config/ludo-g1/env (never in git). Override the path with GREENNODE_ENV_FILE.
# See docs/cloud.md.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$SCRIPT_DIR/.." && pwd)"

readonly SCRIPT_DIR REPO
readonly DEFAULT_ENV_FILE="${HOME}/.config/ludo-g1/env"
readonly JOB_SUBDIR="data/logs/greennode"

die() {
  echo "greennode: $*" >&2
  exit 1
}

info() {
  echo "greennode: $*"
}

usage() {
  sed -n '2,17p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
}

# --- configuration -----------------------------------------------------------------------------

# Reads the credentials file when present and fills in the defaults. Refuses remote mode without it.
load_config() {
  ENV_FILE="${GREENNODE_ENV_FILE:-$DEFAULT_ENV_FILE}"
  if [ -f "$ENV_FILE" ]; then
    # shellcheck disable=SC1090  # operator-provided file, path known only at run time
    . "$ENV_FILE"
  fi

  TRANSPORT="${GREENNODE_TRANSPORT:-remote}"
  case "$TRANSPORT" in
    remote | local) ;;
    *) die "GREENNODE_TRANSPORT must be 'remote' or 'local', got '$TRANSPORT'" ;;
  esac

  if [ "$TRANSPORT" = "remote" ]; then
    [ -f "$ENV_FILE" ] || die "remote mode needs $ENV_FILE, which does not exist.
  It must define GREENNODE_HOST, GREENNODE_USER, GREENNODE_SSH_KEY (path) and GREENNODE_REMOTE_ROOT.
  That file is the human's to create; no agent writes it. See agents/QUESTIONS.md Q-001 and docs/cloud.md.
  To exercise the flow without credentials, run with GREENNODE_TRANSPORT=local."
    for var in GREENNODE_HOST GREENNODE_USER GREENNODE_REMOTE_ROOT; do
      [ -n "${!var:-}" ] || die "$ENV_FILE does not define $var (see agents/QUESTIONS.md Q-001)"
    done
    REMOTE_ROOT="$GREENNODE_REMOTE_ROOT"
    SSH_TARGET="${GREENNODE_USER}@${GREENNODE_HOST}"
    SSH_OPTS=(-o BatchMode=yes)
    if [ -n "${GREENNODE_SSH_KEY:-}" ]; then
      SSH_OPTS+=(-i "$GREENNODE_SSH_KEY")
    fi
    if [ -n "${GREENNODE_SSH_PORT:-}" ]; then
      SSH_OPTS+=(-p "$GREENNODE_SSH_PORT")
    fi
  else
    REMOTE_ROOT="${GREENNODE_LOCAL_ROOT:-$REPO/data/cloud_local}"
    SSH_TARGET=""
    SSH_OPTS=()
  fi

  DOCKER_IMAGE="${GREENNODE_IMAGE:-ludo-g1-train:0.2.0}"
  SHM_SIZE="${GREENNODE_SHM_SIZE:-8g}"
  LOCAL_PYTHON="${GREENNODE_LOCAL_PYTHON:-$REPO/.venv/bin/python}"
  HEARTBEAT_SECONDS="${GREENNODE_HEARTBEAT_SECONDS:-5}"
}

# --- transport primitives ----------------------------------------------------------------------
# Each has a remote implementation (ssh/rsync) and a local one (cp into REMOTE_ROOT).

# remote_exec CMD... : run a command on the remote root.
remote_exec() {
  if [ "$TRANSPORT" = "local" ]; then
    ( cd "$REMOTE_ROOT" && eval "$*" )
  else
    ssh "${SSH_OPTS[@]}" "$SSH_TARGET" "cd $(printf '%q' "$REMOTE_ROOT") && $*"
  fi
}

# remote_mkdir REL... : create directories under the remote root.
remote_mkdir() {
  if [ "$TRANSPORT" = "local" ]; then
    local rel
    for rel in "$@"; do mkdir -p "$REMOTE_ROOT/$rel"; done
  else
    local quoted=()
    local rel
    for rel in "$@"; do quoted+=("$(printf '%q' "$REMOTE_ROOT/$rel")"); done
    ssh "${SSH_OPTS[@]}" "$SSH_TARGET" "mkdir -p ${quoted[*]}"
  fi
}

# remote_cat REL : print a file from the remote root, or nothing when it does not exist.
# Always succeeds: an absent file is the normal answer while a job is still running, and callers use
# it inside $(...) under `set -e`, where a non-zero return would abort the script.
remote_cat() {
  local rel="$1"
  if [ "$TRANSPORT" = "local" ]; then
    if [ -f "$REMOTE_ROOT/$rel" ]; then cat "$REMOTE_ROOT/$rel"; fi
  else
    ssh "${SSH_OPTS[@]}" "$SSH_TARGET" "cat $(printf '%q' "$REMOTE_ROOT/$rel") 2>/dev/null" || true
  fi
  return 0
}

# push_files REL_PATH... : copy repo-relative files to the same paths under the remote root.
push_files() {
  [ "$#" -gt 0 ] || return 0
  if [ "$TRANSPORT" = "local" ]; then
    mkdir -p "$REMOTE_ROOT"
    # cp --parents keeps the repo-relative layout, which is what the job's script path depends on.
    printf '%s\0' "$@" | ( cd "$REPO" && xargs -0 cp -a --parents -t "$REMOTE_ROOT" )
  else
    printf '%s\n' "$@" | rsync -az --relative --files-from=- \
      -e "ssh ${SSH_OPTS[*]}" "$REPO/" "$SSH_TARGET:$REMOTE_ROOT/"
  fi
}

# push_tree REL_DIR : copy a whole repo-relative directory up (used for data/raw, which is untracked).
push_tree() {
  local rel="$1"
  [ -d "$REPO/$rel" ] || { info "nothing to push: $rel does not exist"; return 0; }
  if [ "$TRANSPORT" = "local" ]; then
    mkdir -p "$REMOTE_ROOT/$rel"
    cp -a "$REPO/$rel/." "$REMOTE_ROOT/$rel/"
  else
    remote_mkdir "$rel"
    rsync -az --delete -e "ssh ${SSH_OPTS[*]}" "$REPO/$rel/" "$SSH_TARGET:$REMOTE_ROOT/$rel/"
  fi
}

# pull_tree REL_DIR : copy a remote-root-relative directory back into the repo.
pull_tree() {
  local rel="$1"
  mkdir -p "$REPO/$rel"
  if [ "$TRANSPORT" = "local" ]; then
    if [ -d "$REMOTE_ROOT/$rel" ]; then
      cp -a "$REMOTE_ROOT/$rel/." "$REPO/$rel/"
    else
      info "nothing to pull: $REMOTE_ROOT/$rel does not exist"
    fi
  else
    rsync -az -e "ssh ${SSH_OPTS[*]}" "$SSH_TARGET:$REMOTE_ROOT/$rel/" "$REPO/$rel/"
  fi
}

# --- subcommands -------------------------------------------------------------------------------

cmd_up() {
  [ "$#" -eq 0 ] || die "up takes no arguments"
  info "transport=$TRANSPORT root=$REMOTE_ROOT"
  remote_mkdir . data/raw data/checkpoints "$JOB_SUBDIR"

  # The repo minus third_party/, as git sees it: tracked files plus any new file that is not
  # git-ignored, so a not-yet-committed script can still be pushed and .gitignore'd junk (data/,
  # .venv/, __pycache__/) never travels.
  local tracked=()
  mapfile -t tracked < <(cd "$REPO" && git ls-files --cached --others --exclude-standard \
    -- . ':(exclude)third_party/**' ':(exclude)third_party')
  [ "${#tracked[@]}" -gt 0 ] || die "git ls-files returned nothing; is $REPO a git checkout?"
  push_files "${tracked[@]}"
  info "pushed ${#tracked[@]} repo files (third_party and git-ignored paths excluded)"

  push_tree data/raw
  info "up: done"
}

cmd_train() {
  # The job itself is always launched detached (nohup + setsid) so it outlives the ssh connection.
  # --detach only changes whether *this* process waits for it; a client-side timeout never kills it.
  local wait_for_job=1
  local timeout=86400
  while [ "$#" -gt 0 ]; do
    case "$1" in
      --wait) wait_for_job=1; shift ;;
      --detach) wait_for_job=0; shift ;;
      --timeout) timeout="${2:-}"; [ -n "$timeout" ] || die "--timeout needs a value"; shift 2 ;;
      --) shift; break ;;
      -*) die "unknown train option: $1" ;;
      *) break ;;
    esac
  done
  [ "$#" -ge 1 ] || die "train needs a script path relative to the repo root, e.g. cloud/dummy_job.py"

  local script="$1"; shift
  local job_id="${GREENNODE_JOB_ID:-$(date -u +%Y%m%dT%H%M%SZ)-$$}"
  remote_mkdir "$JOB_SUBDIR" data/checkpoints

  # The command the wrapper supervises. Remote: the pinned image. Local: this repo's venv python.
  # Either way the job must import the *pushed* tree (policy/, runtime/, config/), not the laptop's:
  # `python policy/train.py` puts policy/ on sys.path, not the root above it, so PYTHONPATH says so.
  local job_cmd
  local job_env=""
  if [ "$TRANSPORT" = "local" ]; then
    [ -x "$LOCAL_PYTHON" ] || die "local transport needs a python at $LOCAL_PYTHON (see docs/setup.md)"
    job_env="PYTHONPATH=$(printf '%q' "$REMOTE_ROOT") "
    job_cmd="$(printf '%q ' "$LOCAL_PYTHON" "$script" "$@")"
  else
    job_cmd="docker run --rm --gpus all"
    # torch's DataLoader workers share tensors through /dev/shm, which docker caps at 64 MB by default.
    job_cmd+=" --shm-size=$(printf '%q' "$SHM_SIZE")"
    job_cmd+=" -v $(printf '%q' "$REMOTE_ROOT"):/work -w /work"
    job_cmd+=" -e LUDO_G1_ROOT=/work -e LUDO_G1_CHECKPOINT_DIR=/work/data/checkpoints -e PYTHONPATH=/work"
    job_cmd+=" $(printf '%q' "$DOCKER_IMAGE") python $(printf '%q ' "$script" "$@")"
  fi

  info "job $job_id: $script ${*:-}"
  # nohup + setsid so the job outlives the ssh session (or this shell, in local mode).
  local launch
  launch="${job_env}LUDO_G1_ROOT=$(printf '%q' "$REMOTE_ROOT")"
  launch+=" LUDO_G1_CHECKPOINT_DIR=$(printf '%q' "$REMOTE_ROOT/data/checkpoints")"
  launch+=" GREENNODE_HEARTBEAT_SECONDS=$(printf '%q' "$HEARTBEAT_SECONDS")"
  launch+=" nohup setsid bash cloud/job_wrapper.sh $(printf '%q' "$job_id") $(printf '%q' "$REMOTE_ROOT")"
  launch+=" $job_cmd >/dev/null 2>&1 &"
  remote_exec "$launch"

  if [ "$wait_for_job" -eq 1 ]; then
    wait_for_exit "$job_id" "$timeout"
    report_hashes "$job_id"
  else
    info "launched detached; follow it with: cloud/greennode.sh status $job_id, then cloud/greennode.sh down"
  fi
}

# report_hashes JOB_ID : repeat the run's identity lines from the mirrored job log (CLAUDE.md 5.8).
# policy/train.py prints the dataset manifest sha256 and the six config hashes; a training run is only
# comparable to another when those agree (R5), so they are surfaced here instead of only in the log.
report_hashes() {
  local log="$REPO/$JOB_SUBDIR/$1.log"
  [ -f "$log" ] || return 0
  local line
  while IFS= read -r line; do
    info "$line"
  done < <(grep -E '^(training config hash|dataset manifest sha256|run |written )' "$log" || true)
}

# wait_for_exit JOB_ID TIMEOUT_S : poll the job's exit file, mirroring its logs into data/logs/.
wait_for_exit() {
  local job_id="$1" timeout="$2"
  local waited=0 exit_code=""
  while [ "$waited" -lt "$timeout" ]; do
    exit_code="$(remote_cat "$JOB_SUBDIR/$job_id.exit" | tr -d '[:space:]')"
    [ -n "$exit_code" ] && break
    sleep 1
    waited=$((waited + 1))
  done
  fetch_logs
  [ -n "$exit_code" ] || die "job $job_id did not finish within ${timeout}s; last heartbeat: $(remote_cat "$JOB_SUBDIR/$job_id.heartbeat")"
  info "job $job_id finished with exit code $exit_code after ~${waited}s"
  [ "$exit_code" -eq 0 ] || die "job $job_id failed; log tail:
$(remote_cat "$JOB_SUBDIR/$job_id.log" | tail -20)"
}

# Mirror the remote job logs and heartbeats into this repo's data/logs/ (CLAUDE.md section 7).
fetch_logs() {
  pull_tree "$JOB_SUBDIR"
}

cmd_down() {
  [ "$#" -eq 0 ] || die "down takes no arguments"
  info "transport=$TRANSPORT root=$REMOTE_ROOT"
  pull_tree data/checkpoints
  fetch_logs
  info "down: checkpoints in $REPO/data/checkpoints, job logs in $REPO/$JOB_SUBDIR"
}

cmd_status() {
  local only_job="${1:-}"
  echo "transport:   $TRANSPORT"
  echo "env file:    $ENV_FILE $([ -f "$ENV_FILE" ] && echo '(present)' || echo '(absent)')"
  echo "remote root: $REMOTE_ROOT"
  if [ "$TRANSPORT" = "remote" ]; then
    echo "ssh target:  $SSH_TARGET"
    echo "image:       $DOCKER_IMAGE"
  else
    echo "python:      $LOCAL_PYTHON"
  fi
  fetch_logs
  local heartbeats=()
  mapfile -t heartbeats < <(ls -1 "$REPO/$JOB_SUBDIR"/*.heartbeat 2>/dev/null || true)
  if [ "${#heartbeats[@]}" -eq 0 ]; then
    echo "jobs:        none"
    return 0
  fi
  echo "jobs:"
  local hb job_id
  for hb in "${heartbeats[@]}"; do
    job_id="$(basename "$hb" .heartbeat)"
    if [ -n "$only_job" ] && [ "$job_id" != "$only_job" ]; then continue; fi
    echo "  $(cat "$hb")"
  done
}

# --- entry point -------------------------------------------------------------------------------

main() {
  [ "$#" -ge 1 ] || { usage; exit 2; }
  local sub="$1"; shift
  case "$sub" in
    up | train | down | status)
      load_config
      "cmd_$sub" "$@"
      ;;
    -h | --help | help) usage ;;
    *) usage; die "unknown subcommand: $sub" ;;
  esac
}

main "$@"
