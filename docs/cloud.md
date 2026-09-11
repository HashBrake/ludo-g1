# Cloud training (Greennode)

All real training runs on a Greennode GPU VM; the laptop only collects data, runs tests and does
inference (CLAUDE.md 5.8). `cloud/greennode.sh` is the whole interface to that VM.

```
cloud/greennode.sh up                       push data/raw and the repo (minus third_party) to the remote
cloud/greennode.sh train SCRIPT [ARGS...]   run SCRIPT there, detached, with a heartbeat
cloud/greennode.sh down                     pull the remote's data/checkpoints back into this repo
cloud/greennode.sh status [JOB_ID]          transport, roots, and the state of the known jobs
```

`SCRIPT` is a repo-relative path — `cloud/dummy_job.py` for the transport check, `policy/train.py` for
a training run — and everything after it is passed through to the script untouched.

## Status: untested against the real VM

There are no credentials yet (agents/QUESTIONS.md **Q-001**), so **the remote transport has never been
run**, and neither has `cloud/Dockerfile` — docker is not installed on the control laptop, so the image
is delivered unbuilt and reviewed by eye only. What *is* tested is the local transport
(`tests/test_greennode_local.sh`), which exercises the same subcommands, the same job wrapper and the
same file layout with `cp` instead of `rsync`/`ssh` and this repo's venv python instead of docker:
`tests/test_greennode_local.sh` for the dummy job and `tests/test_greennode_train.py` for a real
`policy/train.py` run (see "Training for real" below).

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
GREENNODE_IMAGE=ludo-g1-train:0.2.0
```

In remote mode, a missing file or a missing variable makes every subcommand refuse to run and print a
pointer to Q-001. Nothing is created on the human's behalf.

| Variable | Default | Meaning |
|---|---|---|
| `GREENNODE_TRANSPORT` | `remote` | `remote` (ssh + rsync + docker) or `local` (cp + venv python) |
| `GREENNODE_ENV_FILE` | `~/.config/ludo-g1/env` | credentials file path |
| `GREENNODE_LOCAL_ROOT` | `data/cloud_local` | local mode only: the directory standing in for the VM |
| `GREENNODE_LOCAL_PYTHON` | `.venv/bin/python` | local mode only: the interpreter that runs the job |
| `GREENNODE_IMAGE` | `ludo-g1-train:0.2.0` | remote mode only: the pinned training image |
| `GREENNODE_SHM_SIZE` | `8g` | remote mode only: `docker run --shm-size`, for torch DataLoader workers |
| `GREENNODE_JOB_ID` | UTC timestamp + pid | name for this job's log, heartbeat and exit files |
| `GREENNODE_HEARTBEAT_SECONDS` | `5` | how often the wrapper rewrites the heartbeat |

## The two transports

| | `remote` | `local` |
|---|---|---|
| push | `rsync -az` over ssh | `cp -a --parents` into `GREENNODE_LOCAL_ROOT` |
| run | `docker run` the pinned image over ssh | `.venv/bin/python` directly |
| import path | `PYTHONPATH=/work` (the bind mount) | `PYTHONPATH=$GREENNODE_LOCAL_ROOT` |
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
    'cd ~/ludo-g1 && docker build -t ludo-g1-train:0.2.0 -f cloud/Dockerfile .'   # once, after the first `up`

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

`cloud/Dockerfile` is the GPU image the remote transport runs every job in. It pins
`nvidia/cuda:12.8.1-cudnn-runtime-ubuntu22.04` **by digest** and installs `cloud/requirements-train.txt`
with Ubuntu 22.04's own `python3.10` (3.10 is fixed by the DexH15 cp310 wheel, D-002 A1, and by lerobot
0.4.4 being the last release that installs on it, D-015).

`cloud/requirements-train.txt` is the training subset of `requirements.txt`, with two differences, both
deliberate:

* `torch==2.9.1+cu128` / `torchvision==0.24.1+cu128` instead of the `+cpu` wheels the laptop installs
  (D-011): the same versions, the CUDA builds. torch 2.9.1 publishes cp310 linux wheels for cu126,
  cu128, cu129 and cu130; **cu128** is chosen because CUDA 12.x minor-version compatibility runs it on
  any driver ≥ 525.60.13, it covers Ampere through Blackwell, and unlike cu130 it does not need an r580+
  driver. If the VM's `nvidia-smi` reports a CUDA version below 12.8, switch the base tag *and* the two
  pins to cu126 together.
* `pxdex` and `unitree_sdk2py` are absent: the first is a `file://` path to a cp310 wheel on this laptop
  for hand hardware the VM does not have, the second is DDS to a robot the VM cannot reach. Neither is a
  training dependency — `python -c "import policy.train"` pulls in neither.

`tests/test_greennode_train.py` asserts that every version pinned in both files agrees, that the two
excluded entries stay excluded, and that the Dockerfile still pins a CUDA base by digest, installs that
requirements file and sets `PYTHONPATH=/work`. That, plus reading, is the whole review the image gets
here: **docker is not installed on this laptop** (`command -v docker` prints nothing), so the image has
never been built. The first build on the VM is bring-up; record `docker build`'s outcome and the built
image's `pip freeze` in `agents/BUILD_LOG.md` (that freeze then replaces the loose transitive pins).

The repo is bind-mounted at `/work`, so the image carries no project code: what runs is whatever the last
`up` pushed, imported through `PYTHONPATH=/work`. Rebuild the image only when the Dockerfile or
`cloud/requirements-train.txt` changes, not when the code does.

## Training for real (Phase 3)

`policy/train.py` is a normal job: `train` passes everything after the script path through untouched
(`--sessions`, `--steps`, `--device`, `--batch-size`, `--run-name`, …; `python -m policy.train --help`
lists them). It writes `data/checkpoints/<run>/` on the *remote* — `run.json`, `loss.csv`,
`checkpoint.pt` — and `down` brings that directory back. After the job, `train` repeats the run's
identity lines from the job log: the run name, the **training config hash** and the **dataset manifest
sha256**, which are what make two runs comparable (CLAUDE.md 5.6, R5). Both are also inside `run.json`,
together with all six config hashes, the git commit and the per-session frame counts.

### The exact remote command — the Phase 3 gate, blocked on Q-001

```bash
cd /home/alois/Desktop/ludo-g1                    # the brief's ~/ludo-g1

# once per image change, on the VM (it has the GPU and docker; this laptop has neither)
ssh -i "$GREENNODE_SSH_KEY" "$GREENNODE_USER@$GREENNODE_HOST" 'nvidia-smi'
ssh -i "$GREENNODE_SSH_KEY" "$GREENNODE_USER@$GREENNODE_HOST" \
    'cd ~/ludo-g1 && docker build -t ludo-g1-train:0.2.0 -f cloud/Dockerfile .'

cloud/greennode.sh up
cloud/greennode.sh train --detach policy/train.py \
    --sessions data/raw/<session> [data/raw/<session2> ...] \
    --steps 200000 --device cuda --batch-size 64 --workers 8 --run-name <YYYYmmddTHHMMSS>_diffusion \
    --checkpoint-every 1000 --keep-last 2
cloud/greennode.sh status <JOB_ID>                # while it runs; the heartbeat is mirrored here
cloud/greennode.sh down
cat data/checkpoints/<run>/run.json               # the two hashes go into agents/BUILD_LOG.md
```

`--steps 200000` is `config/training.yaml` `diffusion.train_iterations`; `--device cuda` is the only
argument that must change from the laptop's smoke runs. `--detach` because a real run is hours long: the
job is launched under `nohup setsid` and survives the ssh connection dropping either way, and `--detach`
only stops *this* shell from waiting.

`--checkpoint-every 1000 --keep-last 2` (T-036) are the `compute.checkpoint_every` and
`compute.keep_last` defaults written out, because on a rented box they are the arguments that decide
what a crash costs: the run writes `checkpoint.pt` — atomically, temp then rename — every 1000 steps
and keeps the two newest `checkpoint_step<N>.pt` beside it, so a preempted or killed job resumes with
`--resume data/checkpoints/<run>/checkpoint.pt` (or a named step) having lost at most 1000 steps.
`down` brings the step checkpoints back with the rest of the run directory; at 293 M parameters each
is 4.7 GB, so **delete them from the remote once a run is finished** and keep only what
`policy/export.py` needs. The job also refuses to start when the remote's free space is below
`compute.disk_guard_factor` (2) × the estimated checkpoint size — 10.32 GB for the configured diffusion
policy, 1.82 GB for ACT, 1.07 GB for `diffusion_small` — with a message naming Q-002; that estimate is
printed by every run, and `--no-disk-guard` overrides the refusal. The local smoke command below does
not pass `--checkpoint-every`: 30 steps never reach the first periodic write, and this laptop has 12 GB. The ACT baseline (5.7) is the same command with its own entry
point, on the same sessions, so that the comparison always exists.

### The same run without credentials (what is actually tested today)

```bash
GREENNODE_TRANSPORT=local cloud/greennode.sh up
GREENNODE_TRANSPORT=local cloud/greennode.sh train policy/train.py \
    --sessions data/raw/<session> --smoke --run-name smoke
GREENNODE_TRANSPORT=local cloud/greennode.sh down
```

`tests/test_greennode_train.py` runs exactly that on a freshly recorded mock session, with the pushed
config shrunk to the test model size, and then deletes what it wrote (Q-002: 12 GB free). Measured at
that scale: 38.4 M parameters, `checkpoint.pt` 614.8 MB, ~27 s for the job. A checkpoint is **four
times** the parameters since T-035 — the weights, their EMA, and Adam's two moments — which is what
resuming a run exactly costs; it was 153.7 MB before. At the configured scale a checkpoint is 293 M
parameters and ~4.7 GB, which is why the test shrinks it, why real training belongs on the VM in the
first place, and why nothing keeps two of them on this laptop (Q-002).

## Failure modes seen so far

| Symptom | Cause |
|---|---|
| `remote mode needs …/env, which does not exist` | Q-001: no credentials yet. Use `GREENNODE_TRANSPORT=local`. |
| `local transport needs a python at …/.venv/bin/python` | the venv is not created; see docs/setup.md |
| `job … did not finish within Ns` | the client's `--timeout`, not the job. The job is still running; use `status`. |
