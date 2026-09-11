# The control loop

`runtime/controller.py` is the cycle of CLAUDE.md 5.5. It asks the engine what to do, executes it
with a policy, checks what actually happened, and tells the engine. It decides nothing: the engine
chooses the move, the policy chooses the motion, the safety guard chooses what may be sent.

```bash
.venv/bin/python -m runtime.controller --backend mock --seconds 60
```

```python
from runtime.controller import build
controller = build("mock", seed=0)      # mock drivers, stub engine, HoldPolicy, MockPerception
summary = controller.run(seconds=60)    # or run(max_commands=3)
print("\n".join(summary.lines()))
```

`--backend real` refuses: the Phase 1 drivers do not exist, and `build()` refuses a placeholder
policy on anything but mocks anyway (R2, below).

## The cycle

```
cmd = engine.next_command()            # None -> the run ends
policy.reset(cmd)
loop until policy.done(), 20 s, or the watchdog:
    sample every stream                # cameras, palm, arm state, hand state
    obs = align(streams, now, 50 ms)   # runtime/clock.py; goal channels + task one-hot added
    watchdog.sample(...)               # once a second: perception.progress(cmd, before, now)
    chunk = policy.act(obs)            # 10 Hz
    send chunk[0..2] at 30 Hz          # never past diffusion.execute = 8 of the 16
if the watchdog halted it:             # no progress for runtime.watchdog_stall_s
    hold()                             # one command: the arm's own measured state, through the guard
    outcome = Outcome(False, delta, "policy_stalled")
else:
    outcome = perception.verify(cmd, before, after)
engine.report(outcome)                 # the engine decides on a RECOVER and a re-issue
one JSON line -> data/logs/controller_<session>.trials.jsonl
```

The controller never retries and never issues a RECOVER of its own. That is the engine's state
machine (`docs/engine.md`), which keeps R2 intact: retries are orchestration, not motion.

## Rates, and why the chunk is not played out whole

CLAUDE.md 5.2 asks for three things at once: inference at 10 Hz, actions at 30 Hz, and a receding
horizon that executes 8 of the 16 predicted actions. The loop honours all three by driving the action
index from the clock rather than from a counter:

| quantity | value | where from |
|---|---|---|
| policy call | every 100 ms | `rates.policy_hz` |
| action | every 33.3 ms | `rates.action_hz`, `action.hz` |
| chunk length | 16 | `diffusion.chunk` |
| executable horizon | 8 | `diffusion.execute` |

Under nominal timing a chunk is superseded after its first **3** actions, and the remaining 5 of the
8 are the margin that covers an inference over-run: if the next chunk is late, the loop keeps playing
the current one up to index 7, then stops sending, so the arm holds the last target it was given
rather than being driven off the end of a stale prediction. Entries 8..15 are never executed. A step
that over-runs its period drops the grid points it missed instead of firing a burst of catch-up
commands into the rate limiter (`_bump`).

## The progress watchdog (CLAUDE.md Phase 5, 6.5)

Two deadlines end a primitive that does not end itself, and they answer different questions. The
**timeout** (`runtime.primitive_timeout_s`, 20 s) bounds how long a primitive may take. The
**watchdog** (`runtime.watchdog_stall_s`, 20 s) bounds how long it may achieve *nothing*.

| quantity | value | where from |
|---|---|---|
| progress sample | every 1 s | `runtime.watchdog_interval_s` |
| stall verdict | 20 s without an increase | `runtime.watchdog_stall_s` |
| what is sampled | `Perception.progress(cmd, before, now) -> [0, 1]` | `board/perception.py` |

It is a few numbers (`runtime.controller.Watchdog`) read inside the loop's own tick: no thread, no
timer, nothing that can fire while the loop is elsewhere. Only the *direction* of the signal is read,
never its magnitude, so nothing has to be calibrated; `MockPerception.progress` counts the share of
the board that differs from where the primitive started, and is 0 for the whole of a primitive in
which nothing changed.

On a halt the loop stops sending policy actions, sends **one hold** -- the arm's and hand's own
measured state, through the same guard as every other command -- and reports
`failure_mode="policy_stalled"` to the engine, which then decides on a RECOVER and a re-issue exactly
as for any other failure. The hold is a reading, never a pose: R2 forbids this module from owning a
joint target, so the only target it can construct is the one the robot is already at. It occupies one
action slot (33 ms), so the next primitive's first action does not land inside the guard's rate-limit
window.

`policy_stalled` and `timeout_no_progress` are two modes and not one. The first is the watchdog's
verdict, made during the primitive: the policy was going nowhere and was stopped. The second is
perception's, made after it: nothing changed by the end. With `HoldPolicy` and the shipped config the
two deadlines coincide at 20 s and the loop reports the stall, because it says the more specific of
the two true things; a primitive cut short by the run deadline still ends in perception's verdict,
which is why a 60 s mock run shows both (below).

## Observation

Assembled at each policy instant from `runtime/clock.py` buffers, one nearest sample per stream
within `runtime.alignment_tolerance_ms` (50 ms). That tolerance is not the recorder's skew budget:
the cameras and the hand free-run at 30 Hz, so a nearest sample is inherently up to one 33.3 ms
period old, and 50 ms is that plus scheduling slack. Phase 2's `< 10 ms p99` applies to recorded,
latency-compensated episodes, not to live polling. An observation that will not align is counted in
`align_failures`, logged, and skipped; the previous chunk keeps playing.

Shapes are in `runtime/policy_api.py`: `top`/`oblique` 640x480x3 uint8, `palm` 320x240x3 uint8,
`state` 9 float64 (7 arm joints, waist yaw, pinch), `goal` 2x480x640 float32, `task_id` 3 float32.

## Goal conditioning

`runtime/goal.py` renders the two heatmap channels into the `top` frame -- source cell, then target
cell -- as unit-peak gaussians of `observation.goal_sigma_px` (12 px), plus the one-hot over
`observation.task_ids`. They depend only on the `Command`, which is frozen, so they are rendered once
per command and reused for every observation of it.

`Cell.top_px` is the truth and comes from the AprilTag homography in `board/calibration.py`. **While
no calibration is loaded it is `None`, and the renderer falls back to a placeholder**: board
millimetres mapped affinely onto the frame, assuming the board fills it squarely
(`GoalRenderer.placeholder_px`). That is a stand-in so the loop can run today; it is right only by
accident. The first use logs `goal_placeholder_px` and `GoalRenderer.placeholder_uses` counts them.
Pass real pixels with `engine.cells.load_cells(top_px=calibration.cell_px_all())` and the placeholder
is never touched. A channel whose cell is `None` (a ROLL, which addresses no cell) is all zeros: the
absence of a goal, not a goal at the origin.

## Safety (R1, R3)

Every action goes to `ArmDriver.send_targets` and `HandDriver.send_pinch`, and both admit through
`runtime.safety.Guard.admit` -- session gate, then envelope. The controller has no other path to an
actuator and no way to skip the guard. A refused command raises `SafetyViolation`, which the loop
**counts, logs and survives**: the envelope is the limiter, not a crash, and a loop that died on the
first clamp would leave the arm mid-primitive with the engine waiting for a report. `refused` in the
summary is how many were turned away; on mocks it should be 0, and any other number is a finding.

## R2: there is no learned policy yet

`HoldPolicy` returns the current measured state as all 16 actions. It commands no motion at all, it
holds no trajectory or waypoint list, and it exists only so this orchestration could be tested before
Phase 3. `build()` refuses it on any backend but `mock`. Nothing in `runtime/`, `board/` or `policy/`
imports from `tools/hardware_checks/`, and a test asserts that.

`MockPerception` is the same kind of stand-in: it reads the engine's own board state, not the table,
so it cannot see a fallen horse or a missed magnet. With `HoldPolicy` nothing moves and the board
state does not change, so its `progress` stays 0 and the watchdog halts every primitive as
`policy_stalled` (a primitive that ends some other way with a blank board verifies as
`timeout_no_progress`). **That is the expected result of every mock run** and it is what makes the
engine's recovery path observable end to end.

## What a mock run looks like

`--seconds 60` on this laptop (2026-09-12): 3 commands, all failing as designed, the stub answering
the first failure with a RECOVER.

```
elapsed            60.03 s
commands executed  3 (recover=2, roll=1)
outcomes           0 success, 3 failure
failure modes      policy_stalled=2, timeout_no_progress=1
stopped by         run_deadline=1, watchdog=2
policy calls       599 = 9.98 Hz
actions sent       1794 = 29.89 Hz
safety refusals    0
alignment failures 0
```

The two primitives that ran their course were halted by the watchdog after 20 s of a board that never
changed; the third was cut off by the run deadline, so perception judged it instead and saw the same
blank board from the other side.

On the fake clock of `tests/test_controller.py` the same loop measures 10.0 Hz exactly, because a
fake clock has no scheduling jitter. The ~0.4 % shortfall on the real clock is the one-off cost of
the first policy call (~230 ms of first-touch numpy and config work) amortised over the run.

## Heartbeat and logs

One line per `runtime.heartbeat_s` (1 s) to `data/logs/controller_<session>.heartbeat`, git-ignored:

```
ts_ns=1158816410 session=20260911T204420 commands=0 primitive=roll policy_calls=9 actions=24 refused=0
```

One JSON line per executed primitive to `data/logs/controller_<session>.trials.jsonl`, also
git-ignored. This is the per-trial failure log: `eval/run_eval.py` reads it back and refuses to write
a result whose rows disagree with it (`docs/eval.md`), and it is the record a phase report counts
failures from when no eval was running.

| key | meaning |
|---|---|
| `session`, `index`, `ts_ns` | the run, the command's position in it, and when it ended |
| `command` | `{primitive, src, dst, horse_id}`, cells by id |
| `success`, `failure_mode` | the `Outcome` reported to the engine |
| `stopped_by` | `policy_done`, `watchdog`, `timeout` or `run_deadline` |
| `duration_s`, `actions_sent`, `policy_calls`, `safety_refusals`, `align_failures` | this command's own cost, not the run's totals |
| `watchdog` | `{stalled, samples, progress, s_since_increase}` at the end of the primitive |
| `observed_state_delta` | what perception saw change |

Everything else goes through the one `structlog` logger (`--json-logs` for machine-readable output):
`run_start`, `observation_align_failed`, `safety_refused`, `primitive_end`, `watchdog_halt`,
`command_done`, `engine_exhausted`, `run_end`.

## Injecting your own pieces

`Controller` takes every collaborator as a keyword argument -- `arm`, `hand`, `cameras`, `engine`,
`policy`, `perception`, `goal`, `now_ns`, `sleep_until`, `session`, `log_dir` -- and owns no device.
The tests pass a fake clock whose `sleep_until` jumps instead of sleeping, which is how a 20 s
primitive execution runs in milliseconds with an exact loop rate. Phase 3 replaces `HoldPolicy` with
a trained policy and changes nothing else here; Phase 1 replaces the mock drivers the same way.
