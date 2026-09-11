# Teleop: retargeting the operator on to the robot

`teleop/retarget.py` turns operator input into robot targets and nothing else. The operator holds a
Pico 4 controller in a PxCap Pro glove jig; the controller gives a 6-DoF wrist pose, the glove gives
finger angles. Two maps turn that into the 9-D action of CLAUDE.md 5.3: an 8-DoF arm IK for the
joints, a one-dimensional normalisation for the pinch scalar.

```python
from teleop.retarget import ArmIK, pico_to_g1_base, pinch_from_glove

ik = ArmIK()                                              # compiles the MJCF once, ~0.2 s
pos, quat = pico_to_g1_base(frame.controllers.left.pose.position, ...rotation)
q8 = ik.solve(pos, quat, q_current)                       # 8 joints, action_order
pinch = pinch_from_glove(thumb_index_distance_m)          # 0 open .. 1 pinched
```

**Nothing here sends anything.** `q8` and `pinch` are a proposal. They become a `MotionCommand` and
then a command on the wire only through `runtime/safety.py` (R1, R3) and a driver. `teleop/` imports
no driver.

## Why this exists (D-006)

`docs/sdks.md` section 7 established that the vendored Teleopit stack has no controller-pose-to-arm
IK: it needs a full body skeleton (headset plus two ankle trackers), drives all 29 joints through an
RL balancing policy, and its Pico provider throws the controller pose away. D-006 replaced it with
this module: the controller pose read straight from `pico_bridge`, an 8-DoF IK over the vendored G1
MJCF, and the safety guard. GMR, the RL policy and the body-tracking dependency are off the critical
path.

## `pico_to_g1_base`

`pico_bridge` reports `Pose(position, rotation)` in metres with an **xyzw** quaternion, in its own
`pico_native` frame (`docs/sdks.md` 7.1). The workspace box, `runtime/fk.py` and `ArmIK` all work in
the **G1 pelvis frame** (+x forward, +y to the robot's left, +z up). `config/robot.yaml`
`teleop.pico_to_pelvis` is the row-major 4x4 `[R t; 0 1]` between them, applied to both halves of the
pose.

It is the identity and tagged `UNMEASURED`. That is a placeholder, **not** a claim that the two
frames coincide: until Phase 1 calibrates it on the rig, a controller pose maps to a wrist pose only
up to that unknown transform. What the function does guarantee today is the units and the quaternion
order.

## `ArmIK`

| | |
|---|---|
| model | `third_party/unitree_g1_mjcf/g1_29dof.xml`, the same file `runtime/fk.py` checks the box on |
| solved joints | the 8 of `action_order`: 7 left-arm joints, then waist yaw |
| frozen | every other joint at `qpos0`, the floating base at `runtime.fk.BASE_QPOS` (identity) |
| target frame | `config/safety.yaml` `workspace_box_m.point`, today `left_wrist_yaw_link` |
| solver | `mink` `FrameTask` + `PostureTask`, QP by `daqp`, settings in `config/robot.yaml` `teleop.ik` |
| limits | `config/safety.yaml` `joint_limits_rad` with `waist_yaw_clamp_rad` applied, and `joint_velocity_limit_rad_s` per iteration |

Sharing one model with `runtime/fk.py` is deliberate: the envelope and the IK cannot disagree about
where the wrist is. `ArmIK.fk_pose(q8)` returns the same position as `runtime.fk.left_arm_fk(q8)`
(asserted in `tests/test_retarget.py`) plus the orientation, which the box does not need and the IK
does.

Freezing is done by giving every uncommanded hinge a **zero** velocity limit and the floating base a
zero `FreeJointVelocityLimit`, so the QP itself cannot move them; measured drift over a solve is
below 1e-9 rad. The safety joint limits are written into this instance's `MjModel.jnt_range`, which
is what `mink.ConfigurationLimit` reads; `mj_kinematics` does not read `jnt_range`, so the geometry
`runtime/fk.py` sees is untouched.

`solve()` returns the 8 joints. `solve_detailed()` returns an `IkResult` with the iteration count,
the residual position and orientation error, whether it converged, and — with `record_steps=True` —
every intermediate configuration, which is what the velocity-limit test inspects. An unreachable
target is not faked: the joints come back inside the limits with `converged=False`.

### `step_dt_s` is a trust region, not a control period

Each iteration solves a QP for a joint velocity and integrates it by `teleop.ik.step_dt_s`, with the
velocity limit bounding the step at `joint_velocity_limit_rad_s * step_dt_s` = 0.15 rad. Over
`max_iters` = 30 iterations that covers 4.5 rad, wider than the widest commanded joint range, which
is what lets a cold solve from the rest pose reach an arbitrary target.

It is **not** the 30 Hz action period, and a single cold `solve` may therefore return a pose further
from `q_current` than one tick at 1.5 rad/s allows. The command-level velocity limit is a different
thing and is enforced where it belongs: `runtime/safety.py`, against the *measured* state and the
*real* elapsed monotonic time. In teleop this never binds, because the operator's hand moves
continuously and the loop engages from a clutch (the first target is the current wrist pose), so
every solve is warm and converges in one or two iterations.

### Measured, `tests/test_retarget.py` on the laptop

```
.venv/bin/python -m pytest tests/test_retarget.py -q
```

| | |
|---|---|
| cold solve, 50 reachable targets from the rest pose | 100% within 5 mm and 3 deg, <= 30 iterations |
| position error | median 0.30 mm, p90 0.91 mm |
| orientation error | median 0.09 deg, p90 0.22 deg |
| warm solve (tracking, target moved a few mm) | mean 0.62 ms |
| cold solve from the rest pose | mean 3.9 ms |

Targets are sampled by rejection: random joint values inside the safety limits, kept when the wrist
lands inside the workspace box with its margin removed. Every target is therefore reachable by
construction and a failure is the solver's.

## `pinch_from_glove`

One scalar, `0` open and `1` pinched, linear in the thumb-tip-to-index-tip distance between
`config/hand.yaml` `glove.open_distance_mm` and `glove.closed_distance_mm`, clamped to
`pinch.scalar_range`. Both bounds are `UNMEASURED`: they are captured per operator at the start of a
session, because hands differ. `drivers/dexh15.py` turns the same scalar into 15 joint targets
through the synergy of CLAUDE.md 5.4, so operator intent and robot action live in one dimension.

The distance itself is not computed here. The PxCap Pro reports 17 encoder angles and no tip
positions (`docs/sdks.md` 6); turning those into a tip-to-tip distance is the glove driver's job and
is a Phase 1 item.

# The recorder

`teleop/recorder.py` captures episodes to a LeRobot dataset under `data/raw/<session_id>/`
(CLAUDE.md 5.6, agents/DECISIONS.md D-011). It reads streams and writes files; it never sends a
command, and the action it stores is the one the caller already sent, *after* the guard clamped it.

```python
from teleop.recorder import Recorder

rec = Recorder("20260912T0900", arm=arm, hand=hand, cameras={"top": top, "oblique": oblique},
               glove=glove, operator="alois")          # palm frames come from the hand driver
rec.start_episode(command)                             # engine.interface.Command
while running:
    if now >= next_tick:
        admitted = arm.send_targets(target)            # R1/R3: the Guard decides
        hand.send_pinch(admitted.pinch)
        rec.tick(admitted)                             # polls, then writes at most one frame
        next_tick = rec.next_grid_ns(now) or now + rec.period_ns
    else:
        rec.poll()                                     # read-only, allowed with no session
rec.mark_success(); rec.mark_perturbed(False)
rec.stop_episode()                                     # writes the episode, sidecar and card
rec.close()                                            # flushes lerobot's parquet writers
```

## The lerobot release, and what "v2" means now

`lerobot==0.4.4` is the **last release that installs on Python 3.10**, which the DexH15 cp310 wheel
fixes for this project (D-002 A1); 0.5.0 and later require >= 3.12. Its dataset API is
`from lerobot.datasets.lerobot_dataset import LeRobotDataset` (`.venv/lib/python3.10/site-packages/
lerobot/datasets/lerobot_dataset.py:1641` for `create`, `:1171` `add_frame`, `:1225` `save_episode`,
`:1131` `finalize`), and it writes `CODEBASE_VERSION = "v3.0"` (same file, `:83`) — **not** the "v2"
CLAUDE.md 5.6 and this task name. No installable release writes v2 any more; see agents/BUILD_LOG.md
(T-017) for the disagreement and the alternatives that were rejected.

Two consequences of that release worth knowing:

- `finalize()` is not optional. lerobot buffers episode metadata and only writes the parquet footer
  when the writers close; a dataset that was never finalised fails to load and then silently falls
  back to the Hugging Face Hub. `Recorder.close()` is that call.
- `add_frame` rejects a `timestamp` key even though it pops one (`validate_frame` counts it as an
  extra feature), so the frame timestamp is always `frame_index / fps`. That is exact here: frames
  are written on a fixed grid, one per grid point.

## Features (CLAUDE.md 5.3)

| feature | dtype | shape | source |
|---|---|---|---|
| `observation.images.top` | image | 3x480x640 | Brio, `config/cameras.yaml` `top.policy_resolution` |
| `observation.images.oblique` | image | 3x480x640 | Orbbec Ego left RGB |
| `observation.images.palm` | image | 3x240x320 | DexH15 palm camera, via the hand driver |
| `observation.state` | float32 | 9 | 7 arm joints, waist yaw, pinch scalar (`action_order`) |
| `observation.hand_joints` | float32 | 15 | raw DexH15 joints, recorded but not fed to the policy |
| `observation.glove` | float32 | 17 | raw PxCap Pro encoder angles, degrees |
| `task_id` | int64 | 1 | index into `observation.task_ids` |
| `action` | float32 | 9 | the admitted `MotionCommand`, absolute targets |

Image storage is PNG, not video (`config/training.yaml` `recorder.use_videos: false`). lerobot 0.4.4
encodes through PyAV, so video would work without an `ffmpeg` binary (there is none on this laptop),
but the frames are what the goal-heatmap audit (CLAUDE.md section 8) and the replay checks read back
and a lossy codec changes them. Flip the key when disk, not fidelity, is what binds.

**The goal heatmaps are not a feature.** They are a pure function of the command's two cells and
`observation.goal_sigma_px`, constant for a whole episode, and would cost 2x480x640 float32 =
2.4 MB per frame — 70 MB/s, ~1.4 GB for one 20 s episode, against ~2 kB for the two pixel pairs. The
cells and their pixels go in the per-episode sidecar and `policy/dataset.py` re-renders them with
`runtime.goal.GoalRenderer`, which is the same object `runtime/controller.py` uses at inference.

## Alignment: poll fast, write at 30 Hz, lock the grid to the board camera

Three decisions, all in `config/training.yaml` `recorder:`, each worth a sentence:

1. **Poll faster than you write.** Polling only at 30 Hz does not just thin a 100 Hz stream, it
   misplaces it: the samples that survive are whichever fell just before a tick, so the nearest one
   to an alignment instant can be a whole device period away. Measured on the mocks over a 10 s
   episode, same code, only the poll rate changed:

   | poll rate | skew p50 | skew p99 | dropped |
   |---|---|---|---|
   | 30 Hz | 13.333 ms | 20.000 ms | state 700, glove 200 |
   | 100 Hz | 6.667 ms | 6.667 ms | none |
   | 200 Hz | 6.667 ms | 6.667 ms | none |

   `Recorder.poll()` is read-only and allowed with no session (R1), so call it at least as fast as
   the fastest device runs (`rates.state_hz`, 100 Hz).
2. **Write one lag behind** (`alignment_lag_periods: 1`). The frame a tick writes is for the grid
   point one dataset period back, so every stream has samples on *both* sides of the alignment
   instant and `runtime.clock.align` picks the nearest rather than the newest-not-after. Without it
   the worst-case offset is a whole device period instead of half of one.
3. **The grid is the board camera's** (`Recorder.next_grid_ns`). A 30 Hz stream is at best half a
   period from an arbitrary 30 Hz grid, so a grid pinned to whenever the operator pressed start puts
   16.7 ms of skew on `top` — the frame the goal channels and the board state live in — for nothing.
   The grid starts at the first `top` sample at or after the first command, and the teleop loop puts
   its commands on the same grid, which is what keeps the recorded action next to the observation it
   belongs with.

Latency compensation is `runtime.clock.shift` with `config/robot.yaml` `latency.*`: a sample stamped
at `ts` describes an event `L` earlier, so its timestamp moves back by `L`. `action` is a command,
not an observation, and is not shifted. Every one of those latencies is `0.0` and `UNMEASURED` until
Phase 1 measures it, and the dataset card says so on every card it writes.

## What a session directory holds

```
data/raw/<session_id>/
  meta/, data/, images/     the lerobot v3.0 dataset (loads with LeRobotDataset(repo_id, root=...))
  episodes_meta.jsonl       one JSON object per episode: the metadata of CLAUDE.md 5.6
  README.md                 the dataset card, rewritten after every saved episode
```

lerobot v3.0 has no free-form per-episode metadata field, so `episodes_meta.jsonl` is where the 5.6
fields it does not model go: `task_id`, `src_cell`, `dst_cell`, `success` (operator-marked),
`perturbed`, `operator`, `latency_config_hash` (= `config_hash("robot")`, which is where the
latencies live), `safety_config_hash`, `board_config_hash`, plus the frame count, the goal pixels,
the per-stream sample counts, the skew percentiles and the dropped-sample counts.

The card reports stream rates, frame counts, dropped frames per stream, skew p50/p99 and the three
config hashes. `dropped` counts samples missing from a stream, judged against that stream's own
nominal rate (`drop_gap_periods: 1.5`), so it catches both a device that stalled and a caller that
polled more slowly than the device — both are losses as far as the dataset is concerned.

### Measured, `tests/test_recorder.py` on the laptop

```
.venv/bin/python -m pytest tests/test_recorder.py -q -s
```

| | |
|---|---|
| 60 s mock episode, 200 Hz poll / 30 Hz write | 1800 frames, all 7 streams |
| skew | p50 6.666 ms, p99 6.667 ms (budget: < 10 ms p99) |
| dropped frames | 0 on every stream; 0 skipped ticks, 0 alignment failures |
| replay through a fresh `MockArm` | worst \|admitted - recorded\| = 0.0 over 150 actions (budget 1e-6) |
| round trip | `LeRobotDataset` reloads 2 episodes, 1860 frames, all 8 feature keys |

The long tests shrink the camera frames through a `config/` copy in `tmp_path` (a 640x480 PNG costs
~8 ms to write and a 60 s episode writes 5400 of them); a separate test records at the real
configured sizes and asserts the 3x480x640 / 3x240x320 shapes survive the round trip.

# The operator UI

`teleop/operator_ui.py` is what the operator looks at while collecting: the engine's command drawn on
the board camera, and the keys that open, close and label an episode (CLAUDE.md 5.5, 5.6). It drives
one `Recorder` and one `EngineClient` and does nothing else — it never builds a target, never touches
a driver, and reads the board camera only to draw it (R2).

```python
from teleop.operator_ui import OperatorUI

ui = OperatorUI(engine=engine, recorder=rec, cameras=cams, headless=False)
ui.run_window(step=teleop_step)        # step() sends and returns the admitted action, or None
```

## The state machine

```
idle --(a command from the engine)--> armed --s--> recording --x--> stopped --y/n--> idle
                                       `--a--> idle                  `--p--> perturbed toggles
```

The UI arms itself from `engine.next_command()` as soon as it has one, so the operator sees what to
do before touching the glove. `stopped` is deliberate: an episode that has finished is still *open*
until the operator gives it a verdict, because `success` is an operator label (5.6) and the frames
are written when it is given, not before.

Every way out of a command — `y`, `n` and `a` — reports exactly one `Outcome` to the engine, because
the engine hands out exactly one command per report (5.5). What happens next is then the engine's
decision: after an abort or a failure the stub issues a `RECOVER` and re-issues the original command,
which is the retry logic of 5.5 and not something this file knows about.

| key | action | states it works in | what it does |
|---|---|---|---|
| `s` | start | armed (and idle, which re-arms first) | `Recorder.start_episode(command)` |
| `x` | stop | recording | stop writing frames; the episode stays open |
| `y` | mark success | recording, stopped | `mark_success(True)`, write the episode, report success |
| `n` | mark failure | recording, stopped | `mark_success(False)`, write it, report `operator_marked_failure` |
| `p` | perturbed | recording, stopped | toggle `perturbed` (5.6): someone disturbed the scene |
| `a` | abort | armed, recording, stopped | discard the episode (nothing is written), report `operator_aborted` |
| `q` | quit | any | abort whatever is open, then stop `run_window` |

The bindings are `config/training.yaml` `operator_ui.keys`, not constants (section 7); a key that the
current state has no meaning for is logged and ignored, never an error, because a fat-fingered key
during collection must not end the session. `handle_key` takes a character or a `cv2.waitKey` code.

`Outcome.failure_mode` for the two operator verdicts is `operator_marked_failure` and
`operator_aborted`. Neither is a failure mode of CLAUDE.md 6.5: those describe what the *robot* did
and are labelled by `board/perception.py` in the autonomous loop, while these two describe what the
*operator* decided during collection, and no eval result ever carries one.

## What the frame shows

`render()` returns `(h + banner, w, 3)` uint8: the current `top` frame with the source cell circled
in green and the target cell in magenta, over a text band. The markers are drawn *in* the image and
every word is *below* it, so the operator sees the board exactly as the recorder stores it, plus two
circles, and never reads the board through text. A primitive with no cell (a `ROLL`) draws no marker
for that channel, which is the same "absence of a goal" the goal heatmaps encode.

The pixels come from `runtime/goal.py` — `Cell.top_px` once `board/calibration.py` has produced one,
and the documented placeholder map until then — so the circle the operator aims at and the gaussian
the policy is conditioned on are the same point by construction, not by two similar computations.

The banner's three lines are the command (primitive, `src`, `dst`), the episode (state, elapsed,
frames written, perturbed, episodes kept, aborted) and the key list. Everything about the drawing —
radius, thickness, colours, banner height, font — is in `config/training.yaml` `operator_ui`.

`headless=True` (the default, and what the tests use) returns those arrays and opens no window;
`run_window` refuses to run in it. `run_window(step=...)` is the windowed loop: it calls `step()`
once per iteration (the teleop loop's own send, returning the admitted action or `None`), ticks the
recorder with it, shows the frame and reads one key. It needs a display and is not covered by tests.

### Measured, `tests/test_operator_ui.py` on the laptop

```
.venv/bin/python -m pytest tests/test_operator_ui.py -q -s
## Viewing a session: `tools/dataset_view.py` (T-026)

The section 8 audit asks for five random episodes of every session to be looked at and for the goal
heatmaps to be checked against the cells they claim. This renders that check as one PNG per episode.

```
.venv/bin/python -m tools.dataset_view data/raw/20260912T090000
.venv/bin/python -m tools.dataset_view data/raw/20260912T090000 --episodes 0,3 --out /tmp/strips
```

It prints the dataset card to stdout and writes `episode_nnnnnn.png` to `SESSION_ROOT/strips/`
(`--out` moves them). Each strip, 1927 px wide, is:

| band | what it shows |
|---|---|
| header | episode index, `task_id`, `src -> dst`, `success`, `perturbed`, frame count, skew p99 |
| `top` | 8 evenly spaced frames with the goal channels blended over them: **green** source, **magenta** target |
| `oblique` | the same 8 instants, untouched |
| `palm` | the same 8 instants, scaled up to the same column width |
| legend + plots | the 9 action dims and the 9 state dims over the whole episode, two panels, one shared y range per panel |

The overlay is not a redrawing of the goal: it is `runtime/goal.py`'s `GoalRenderer` fed the pixels
and the sigma the recorder stored in `episodes_meta.jsonl`, so what the auditor sees is the gaussian
the policy will be conditioned on. A ROLL, whose `src_px`/`dst_px` are `None`, gets no overlay at
all rather than a blob at the origin, and the `top` row is then pixel-identical to the raw frames
(a test asserts exactly that, and that the `oblique` and `palm` rows always are).

Every number on a strip is read from the session, never recomputed here: the skew is the recorder's
measurement, not the viewer's opinion (R5). The tool is read-only and imports no driver.

### Measured, `tests/test_dataset_view.py` on the laptop

```
.venv/bin/python -m pytest tests/test_dataset_view.py -q -s
```

| | |
|---|---|
| 30 s headless mock session (seed 2) | 2 episodes: `roll` success, `move` R-base-0 -> track-12 failure+perturbed |
| frames per episode | 298 at 30 Hz over 10 s of recording each, 0 dropped on any stream |
| skew | p50/p99 6.667 ms on both episodes (budget < 10 ms p99) |
| goal markers, 640x480 frame | 542 px changed, all within 15.6 px of a cell centre (radius 14 + thickness 2) |
| state machine | 11 tests: every key in every state, abort discards, quit aborts, idle engine is inert |

The marker test reads the two cell pixels back through `runtime/goal.py`, renders, and asserts that
*no* pixel further than the marker radius from either cell changed: the display cannot quietly draw
anything else over the board.
| mock session (64x48 / 48x32 frames), 2 episodes | 2 PNGs, 1927x797 px, 451 kB and 323 kB |
| real configured sizes (640x480 / 320x240), 3 s episode | 1927x817 px, 647 kB |
| goal overlay on the first `top` frame | changed 3072/3072 px, peak \|diff\| 218/765, each channel peaking within 1 px of the stored cell centre |

## What is not here yet

The clutch and the 30 Hz loop that wires `pico_bridge` -> `pico_to_g1_base` -> `ArmIK` ->
`runtime/safety.py` -> `drivers/g1_arm.py` -> `OperatorUI.tick`.
