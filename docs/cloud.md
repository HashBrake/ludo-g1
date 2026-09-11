# Cloud training (Greennode)

All real training runs on a Greennode GPU VM; the laptop only collects data, runs tests and does
inference (CLAUDE.md 5.8). `cloud/greennode.sh` is the whole interface to that VM.

```
cloud/greennode.sh up                       push data/raw and the repo (minus third_party) to the remote
cloud/greennode.sh train SCRIPT [ARGS...]   run SCRIPT there, detached, with a heartbeat
cloud/greennode.sh down                     pull the remote's data/checkpoints back into this repo
cloud/greennode.sh status [JOB_ID]          transport, roots, and the state of the known jobs
```

`SCRIPT` is a repo-relative path: `cloud/dummy_job.py` today, `policy/train.py` from Phase 3 on.
Everything after it is passed through to the script untouched.

## Status: untested against the real VM

There are no credentials yet (agents/QUESTIONS.md **Q-001**), so **the remote transport has never been
run**, and neither has `cloud/Dockerfile` — docker is not installed on the control laptop, so the image
is delivered unbuilt and reviewed by eye only. What *is* tested is the local transport
(`tests/test_greennode_local.sh`), which exercises the same subcommands, the same job wrapper and the
same file layout with `cp` instead of `rsync`/`ssh` and this repo's venv python instead of docker.

Treat the first real run as bring-up, not as a regression check.

## Configuration

The credentials file is `~/.config/ludo-g1/env`, outside the repo and never in git. **The human writes
it; no agent creates, edits or reads it into a commit.** Override its path with `GREENNODE_ENV_FILE`
(the tests do this, pointing at a temporary HOME, so they can never touch the real one).

```bash
# ~/.config/ludo-g1/env   -- shell syntax, sourced by cloud/greennode.sh
GREENNODE_HOST=<hostname or IP>
GREENNODE_USER=<login>
GREENNODE_SSH_KEY=$HOME/.ssh/<private key file>
GREENNODE_REMOTE_ROOT=/home/<login>/ludo-g1      # the repo's mirror on the VM
# optional
GREENNODE_SSH_PORT=22
GREENNODE_IMAGE=ludo-g1-train:0.1.0
```

In remote mode, a missing file or a missing variable makes every subcommand refuse to run and print a
pointer to Q-001. Nothing is created on the human's behalf.

| Variable | Default | Meaning |
|---|---|---|
| `GREENNODE_TRANSPORT` | `remote` | `remote` (ssh + rsync + docker) or `local` (cp + venv python) |
| `GREENNODE_ENV_FILE` | `~/.config/ludo-g1/env` | credentials file path |
| `GREENNODE_LOCAL_ROOT` | `data/cloud_local` | local mode only: the directory standing in for the VM |
| `GREENNODE_LOCAL_PYTHON` | `.venv/bin/python` | local mode only: the interpreter that runs the job |
| `GREENNODE_IMAGE` | `ludo-g1-train:0.1.0` | remote mode only: the pinned training image |
| `GREENNODE_JOB_ID` | UTC timestamp + pid | name for this job's log, heartbeat and exit files |
| `GREENNODE_HEARTBEAT_SECONDS` | `5` | how often the wrapper rewrites the heartbeat |

## The two transports

| | `remote` | `local` |
|---|---|---|
| push | `rsync -az` over ssh | `cp -a --parents` into `GREENNODE_LOCAL_ROOT` |
| run | `docker run` the pinned image over ssh | `.venv/bin/python` directly |
| pull | `rsync -az` over ssh | `cp -a` |

Both use the same `cloud/job_wrapper.sh`, the same remote layout and the same completion signal, so the
local mode exercises the orchestration; only the three primitives differ. Local mode exists so the whole
flow can be run without credentials and without docker — not as a simulation of the GPU.

## What `up` pushes

`git ls-files --cached --others --exclude-standard` minus `third_party/`: every tracked file plus any
new file that is not git-ignored. So `data/`, `.venv/` and `__pycache__/` never travel, and a script
written but not yet committed still gets pushed. `data/raw/` is pushed separately as a tree
(`rsync --delete` in remote mode) because it is git-ignored by design.

`third_party/` stays on the laptop: the DexH15 wheel and the DDS bindings are device SDKs, useless on
the VM, and the training image installs its own dependency set (see `cloud/Dockerfile`).

## Jobs, heartbeats and logs

`train` launches `cloud/job_wrapper.sh` under `nohup setsid`, so the job survives the ssh connection
dropping. The wrapper writes, under `<remote root>/data/logs/greennode/`:

| File | Contents |
|---|---|
| `JOB_ID.log` | the job's merged stdout/stderr |
| `JOB_ID.pid` | the job's pid on the remote |
| `JOB_ID.heartbeat` | one line, rewritten every `GREENNODE_HEARTBEAT_SECONDS`: `<utc> job=… pid=… state=running` |
| `JOB_ID.exit` | the exit code; its existence is the "job is over" signal |

`train`, `down` and `status` all mirror that directory into the repo's own `data/logs/greennode/`, which
is where CLAUDE.md section 7 says long-running processes report.

By default `train` blocks until `JOB_ID.exit` appears and fails loudly (with the log tail) on a non-zero
exit, which is what makes `up && train && down` safe to chain. `--detach` returns as soon as the job is
launched; follow it with `status` and pull the results with `down` when it is finished. `--timeout N`
bounds only how long the *client* waits — it never kills the remote job.

## The dummy job

`cloud/dummy_job.py` waits `--seconds` (default 60) and writes `hostname`, platform, interpreter, start
and finish times to `$LUDO_G1_CHECKPOINT_DIR/dummy/result.txt` (`train` sets that variable to
`<remote root>/data/checkpoints`). It exists so that `up -> train -> down` can be proven end to end before
`policy/train.py` exists: if `data/checkpoints/dummy/result.txt` lands back in this repo naming the VM's
hostname, the whole path works.

### Phase 0 exit check — run this the moment credentials exist (Q-001)

```bash
cd ~/ludo-g1                                     # this checkout: /home/alois/Desktop/ludo-g1
docker --version                                 # on the VM, not here
ssh -i "$GREENNODE_SSH_KEY" "$GREENNODE_USER@$GREENNODE_HOST" \
    'cd ~/ludo-g1 && docker build -t ludo-g1-train:0.1.0 -f cloud/Dockerfile .'   # once, after the first `up`

cloud/greennode.sh up
cloud/greennode.sh train cloud/dummy_job.py --seconds 60 --note "phase 0 exit check"
cloud/greennode.sh down
cat data/checkpoints/dummy/result.txt            # must name the VM's hostname, not this laptop's
```

Record the `hostname:` line, the GPU and the instance type in `agents/BUILD_LOG.md`; that is the Phase 0
exit criterion in CLAUDE.md section 6.

### The same check without credentials (what is actually tested today)

```bash
cd ~/ludo-g1
GREENNODE_TRANSPORT=local cloud/greennode.sh up
GREENNODE_TRANSPORT=local cloud/greennode.sh train cloud/dummy_job.py --seconds 60
GREENNODE_TRANSPORT=local cloud/greennode.sh down
cat data/checkpoints/dummy/result.txt            # names this laptop; the fake remote is data/cloud_local/
```

`tests/test_greennode_local.sh` runs exactly this with `--seconds 1` in a temporary directory, plus the
remote-mode refusal, the `third_party` exclusion, the heartbeat and `--detach`. It runs under pytest via
`tests/test_greennode_local.py`, so it is part of the pre-commit gate.

## The training image

`cloud/Dockerfile` pins `python:3.10.20-slim-bookworm` (3.10 because the repo is pinned to it) and a CPU
torch wheel, which is all `cloud/dummy_job.py` needs. It installs the training subset of
`requirements.txt` explicitly, because two entries there cannot resolve on the VM: the `file://` DexH15
wheel (a laptop path, and hand hardware the VM does not have) and `unitree_sdk2py` (DDS to a robot the VM
cannot reach).

**TODO (Phase 3):** swap the base for a CUDA image and the torch wheel for the matching GPU build once
the instance type and driver version are known. The candidate pair is recorded in the Dockerfile header.

The repo is bind-mounted at `/work`, so the image carries no project code: what runs is whatever the last
`up` pushed. Rebuild the image only when the Dockerfile changes, not when the code does.

## Failure modes seen so far

| Symptom | Cause |
|---|---|
| `remote mode needs …/env, which does not exist` | Q-001: no credentials yet. Use `GREENNODE_TRANSPORT=local`. |
| `local transport needs a python at …/.venv/bin/python` | the venv is not created; see docs/setup.md |
| `job … did not finish within Ns` | the client's `--timeout`, not the job. The job is still running; use `status`. |
