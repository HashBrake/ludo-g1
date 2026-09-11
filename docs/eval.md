# Evaluation: trials, criteria, and the JSON every number comes from

R5: *no claim of success without a logged measurement.* `eval/` is where that rule is implemented.
Every success rate this project ever reports -- in `BUILD_LOG.md`, in a phase report, in a decision --
comes out of a file under `eval/results/`, written by `eval/run_eval.py`, with the git commit and the
config hashes it was produced under. A number without such a file is not a result.

```bash
.venv/bin/python -m eval.run_eval --backend mock --kind move --n 20 --policy hold
```

```
success 0/20 (0.0%)
failure modes
  timeout_no_progress    20
written /home/alois/Desktop/ludo-g1/eval/results/20260911T221321_move-hold.json
```

That 0/20 is correct and expected: `HoldPolicy` commands no motion (R2), `MockPerception` sees the
engine's own board state, so nothing changes and every primitive times out. **The mock runner
measures the orchestration, never the robot.** The first non-zero success rate needs a trained policy
(T-029, Phase 3) and the real robot.

```python
from eval.protocol import make_trials, script_pairs
from eval.run_eval import run_trials

trials = make_trials("move", 20, script_pairs(), seed=0)
result = run_trials(trials, kind="move", backend="mock", seed=0)
result["summary"]            # {"success": 0, "n": 20, "rate": 0.0, "by_failure_mode": {...}, ...}
```

## Trials (`eval/protocol.py`)

A `Trial` is one primitive execution plus what makes it repeatable: `index`, `primitive`, `src`,
`dst`, `horse_id`, `seed`, `perturbed`, `perturbation`. `make_trials(kind, n, held_out_pairs, seed)`
builds the sets CLAUDE.md section 6 asks for, and the same `seed` always builds the same set.

| kind | what it is | CLAUDE.md |
|---|---|---|
| `move` | `n` MOVEs over the given held-out cell pairs: one seeded shuffle of the pair list per lap, so 20 trials over 10 pairs is each pair exactly twice and never twice in a row | Phase 3, "20 trials on held-out cell pairs" |
| `roll` | `n` ROLLs over the bowl (`src = dst = None`, as in `engine/stub.py`) | Phase 4, "evaluate each primitive separately" |
| `recover` | `n` RECOVERs at seeded random cells, each labelled with one of `PERTURBATIONS` in turn: `horse_on_side`, `horse_on_back`, `between_cells`, `missed_magnet` | Phase 4, 6.5 |
| `sequence` | the 20 commands of `engine/scripts/eval_20_moves.yaml`, in order; `n` must be 20 | Phase 4, "a 20-move scripted sequence" |

**Held-out pairs are an argument, never a decision this module makes.** The train/eval split of the
recorded sessions belongs to `policy/dataset.py` (T-027); importing it here would let an eval quietly
score itself on pairs the policy trained on. Until that split is wired in, `protocol.script_pairs()`
offers the ten distinct pairs of `eval_20_moves.yaml` -- the file was written to be exactly that --
and it is what `--kind move` uses when `--pairs` is not given.

A `recover` trial is `perturbed = True` by definition: a human sets the perturbation up before the
trial, and the label is in the result so a recovery rate can be broken down by what it recovered from.

## When a trial counts as a success

Not "the policy said so", and not "nothing crashed". `SuccessCriterion` states per primitive which
fields of the reported `Outcome` must hold, and `judge(trial, outcome)` is the only thing that turns
an `Outcome` into a counted success.

| primitive | required |
|---|---|
| MOVE | `success`, no `failure_mode`, **and** `observed_state_delta` puts `horse_id` on `dst` |
| ROLL | `success`, no `failure_mode`, **and** `observed_state_delta` contains a new `die` value |
| RECOVER | `success` and no `failure_mode`; a recovery restores what the engine already believes, so it changes no board state and none can be demanded of it (`DECISIONS.md` D-013) |

Each trial row therefore carries both `success` (the criterion's verdict) and `reported_success`
(what perception said). They differ exactly when an `Outcome` claims a success the criterion saw no
evidence for; a result where they differ is a finding about perception, not a better success rate.

## Failure modes (CLAUDE.md 6.5)

One enum, `board.perception.FailureMode`, defined in the module that *produces* the strings and
imported by `eval/protocol.py`, so a failure the robot had and a failure the JSON counts can never
become two spellings of the same thing.

| value | the 6.5 case |
|---|---|
| `horse_fell` | horse falls over at the source or the destination |
| `missed_cell` | horse placed between cells, or missing the magnet |
| `grasp_failed` | the grasp closed on nothing |
| `wrong_horse` | the grasp took the wrong horse (an adjacent cell) |
| `die_out_of_bowl` | the die landed outside the bowl (a human replaces it) |
| `die_grasp_failed` | the die grasp failed |
| `timeout_no_progress` | the policy stalled and the watchdog ended the primitive |

`failure_key(outcome)` is what the table counts under: the mode itself, `unlabelled` when a failure
carries none (a finding, not a category), and an unknown string passed through unchanged rather than
hidden.

## Engine-level recovery, and what a retry is

Recovery is the engine's state machine, not the runner's (`docs/engine.md`). The runner turns it
**on for `kind="sequence"` only**:

- `move` / `roll` / `recover`: `max_reissues=0`. One attempt per trial, which is what a per-primitive
  success rate means.
- `sequence`: the stub's own budget of two re-issues, because Phase 4 evaluates that sequence *with*
  recovery and asks how many retries it took.

A RECOVER the engine injects is attributed to the trial that provoked it -- counted in that trial's
`engine_retries` and in `summary.engine_retries` -- and never becomes a trial of its own, or a
20-trial eval would report a different trial count than the one it was asked for. Only an execution
of the trial's *own* command decides the trial.

On mocks every RECOVER fails too (D-013), so a `sequence` run measures 2 retries per trial, 40 in
total, and completes 0/20. The Phase 4 target of "at most 3 engine-level retries" is an on-robot
number.

## The result JSON

`eval/results/<timestamp>_<tag>.json`, tag defaulting to `<kind>-<policy>`. Top level:

| key | meaning |
|---|---|
| `created_at` | local ISO 8601 with offset; the timestamp in the file name |
| `git_commit` | `git rev-parse HEAD`, or `null` outside a checkout |
| `config_hashes` | `runtime.config.config_hash` of `safety`, `robot`, `board`, `training` (5.6) |
| `policy` | `{tag, checkpoint, checkpoint_sha256}`; the last two are `null` for `hold` |
| `backend`, `realtime`, `kind`, `n`, `seed` | how the run was asked for |
| `engine_recovery` | whether the engine was allowed to re-issue (true only for `sequence`) |
| `pairs` | the distinct `(src, dst)` pairs the trials covered |
| `trials` | one row per trial, in trial order |
| `summary` | `{success, n, rate, by_failure_mode, engine_retries, safety_refusals, duration_s}` |

Per trial: the `Trial` fields (`index`, `primitive`, `src`, `dst`, `horse_id`, `seed`, `perturbed`,
`perturbation`) plus what the run measured:

| key | meaning |
|---|---|
| `success` | `judge`'s verdict -- the only field a success rate is computed from |
| `reported_success` | `Outcome.success` as perception reported it |
| `failure_mode` | `failure_key(outcome)` when the trial failed, else `null` |
| `duration_s` | seconds of the controller's clock, summed over every execution of the trial |
| `actions_sent`, `policy_calls` | what the loop did (5.2 rates); `actions_sent` counts sends admitted by the guard |
| `safety_refusals` | `SafetyViolation`s the guard raised (R1, R3). On mocks this is 0; anything else is a finding |
| `engine_retries` | executions after the trial's first attempt: the engine's RECOVERs and re-issues |
| `stopped_by` | `policy_done`, `timeout` or `run_deadline` |
| `observed_state_delta` | what perception saw change |

Results are **not** committed by default: `eval/results/` is tracked (a `.gitkeep`), its contents are
not staged by a run. A result is committed when it is a number the project stands behind.

## The clock

On `--backend mock` the controller runs on a fake clock whose `sleep_until` jumps instead of
sleeping. 20 trials at the 20 s primitive timeout are 400 s of loop time and about 8 s of wall time,
at exactly the nominal 10 Hz / 30 Hz. `--realtime` runs the mocks on the real clock instead, which is
what to use when the thing being measured is timing rather than orchestration.

## What refuses, and why

- `--backend real` exits 2: no actuated real driver exists (Phase 1), and `HoldPolicy` is a test
  double that is refused on any backend but mocks (R2), exactly as in `runtime/controller.py`.
- `--policy bundle PATH` needs a bundle `policy/export.py` wrote (T-029); a path that is not one is a
  `FileNotFoundError`, and the result records the bundle's weights hash, its DDIM step count and the
  training run behind it, so no success rate is ever separable from the checkpoint that produced it.
- `--kind sequence --n 19` is a `ValueError`: the script has 20 commands and a shorter run would be a
  different evaluation.

## CLI

| flag | default | meaning |
|---|---|---|
| `--backend` | `mock` | `mock`, or `real` (refused) |
| `--kind` | `move` | `move`, `roll`, `recover`, `sequence` |
| `--n` | 20 | trials |
| `--policy` | `hold` | `hold`, or `bundle PATH` (an inference bundle from `policy/export.py`) |
| `--seed` | 0 | seeds the trial set and the stub engine |
| `--pairs` | the `eval_20_moves` pairs | held-out pairs for `--kind move`, as `src:dst` |
| `--tag` | `<kind>-<policy>` | result file tag |
| `--out-dir` | `eval/results/` | where the JSON goes |
| `--realtime` | off | run the mocks on the real clock |
| `--session` | timestamp | session id, used for the heartbeat file name |
| `--json-logs` | off | JSON log lines instead of key=value |

Logging is configured at WARNING, not INFO: the loop logs one `run_start`/`run_end` pair per command,
and at 60 commands that would bury the result the run exists to print.
