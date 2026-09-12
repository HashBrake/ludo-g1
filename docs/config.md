# Configuration

Every number the system uses lives in a yaml file under `config/`, never as a constant in code
(CLAUDE.md section 7). There are six files, one loader, and one convention for marking a value that
has not been measured yet.

```python
from runtime import config

robot = config.load("robot")          # dict, schema-validated
config.config_hash("safety")          # sha256 hex, recorded with every dataset and eval result
config.unmeasured("safety")           # ['workspace_box_m.min', ...] - what is still a placeholder
config.NAMES                          # ('board', 'cameras', 'hand', 'robot', 'safety', 'training')
```

`load`, `config_hash` and `unmeasured` all take an optional `root=` to point at a different config
directory. That exists for tests; production code always uses the default.

## The UNMEASURED convention

Phase 0 writes the config files before any hardware has been touched, so most values in them are
guesses. A guess that is not marked as one becomes a fact by accident three weeks later. So:

**Form 1, the literal value.** The leaf's value is the string `UNMEASURED`. Use it when there is no
sensible placeholder at all, and when using the placeholder would be dangerous:

```yaml
network:
  dds_interface: UNMEASURED
```

**Form 2, the sibling status key.** The leaf `x` carries a usable placeholder number, and a sibling
`x_status: UNMEASURED` says it is a placeholder. Use it when code needs *a* number to run against
mocks, and when the placeholder is conservative enough to be safe:

```yaml
joint_velocity_limit_rad_s: 1.5
joint_velocity_limit_rad_s_status: UNMEASURED
```

`unmeasured(name)` reports both forms, under the path of the **value** (`joint_velocity_limit_rad_s`,
never `joint_velocity_limit_rad_s_status`), in document order, deduplicated. Paths are dotted, with
`[i]` for list elements: `observation.task_ids[1]`, `apriltags.ids.corner_neg_x_neg_y`.

A `_status` key may only hold `UNMEASURED`, `MEASURED` or `HUMAN_APPROVED`, and must annotate a key
that exists; anything else is a `ConfigError` at load time, so that a typo (`unmeasured`,
`UNMEASURD`) cannot silently hide a placeholder. A `_status` annotating a whole mapping marks the
whole subtree: `layout_status: UNMEASURED` next to `layout:` reports one entry, `layout`.

When a Phase 1 measurement lands, replace the value **and** flip the status to `MEASURED` (or delete
the status key) in the same commit, and name the measurement command in a comment next to the number.

**The third status: `HUMAN_APPROVED`** (D-022). Some placeholders cannot be measured before the run
they gate: the workspace box, the joint limits, the waist clamp, the velocity and first-step caps,
the watchdog and the arm gains are what the first motion session is going to measure, and R3 says
only a human may set them. `HUMAN_APPROVED` means a human read the value, agreed it is conservative
for a first session, and committed the status change; the **value stays the placeholder**. It is not
a measurement and never becomes one: when the session measures the real number, the value and the
status change together to `MEASURED`.

```yaml
first_command_max_step_rad: 0.05
first_command_max_step_rad_status: HUMAN_APPROVED   # Alois, 2026-09-xx: conservative for session 1
```

`unmeasured(name)` reports `UNMEASURED` only, so an approved key is not a gap in the config;
`status_of(name, key)` returns whichever word annotates a key (`None` when nothing does, which is
not a claim that it was measured). Only `tools/hardware_checks/session_preflight.py` treats the
difference as a verdict: it passes an approved envelope key for the motion step it gates and still
fails it at `UNMEASURED`, and it refuses an approval on a key that day 1 or day 2 measures read-only
(the DDS interface, the hand port, the top camera, the tag geometry) — see `docs/safety.md`.
Print the values to approve, and the exact status lines to write, with

```
.venv/bin/python tools/hardware_checks/session_preflight.py --show-envelope
```

## Schema validation

`runtime/config.py` holds `REQUIRED_KEYS`, a tuple of dotted paths per file. `load` raises
`ConfigError` naming the file and every missing key:

```
ConfigError: config/safety.yaml: missing required key: 'workspace_box_m.max'
```

The schema deliberately checks presence, not types or ranges: type and range invariants are pinned by
`tests/test_config.py`, where they can be expressed properly (the safety limits really are the MJCF
limits tightened by 5 degrees; the track really is a closed 48-cell cycle). A required path may not
traverse a list.

## config_hash

`config_hash(name)` is the sha256 of the parsed document re-serialised with sorted keys, so it
ignores comments, whitespace and key order and changes if and only if a value changes. Datasets
record `latency_config_hash`, `safety_config_hash` and `board_config_hash` (CLAUDE.md 5.6); training
runs and eval results record the hashes of everything they depended on. Two artefacts produced under
different hashes are not comparable without checking what changed.

## The files

### `config/robot.yaml`

The G1 left arm and waist yaw: joint names, the index of each into the 29-slot Unitree order
(`LowState_.motor_state[]` / `LowCmd_.motor_cmd[]`), the MJCF joint range of each, the DDS transport,
and the per-path actuation latencies.

- Joint indices are waist yaw 12, left shoulder pitch/roll/yaw 15/16/17, left elbow 18, left wrist
  roll/pitch/yaw 19/20/21. Source and verification: `docs/sdks.md` section 2.4. `G1JointIndex` lives
  in the SDK's *example*, not in the installed `unitree_sdk2py` package.
- `limit_rad` is the **mechanical** range from the G1 MJCF, not a safety limit. The limits that are
  enforced are in `config/safety.yaml`. `limits_source` points at the vendored MJCF that T-012 put
  under `third_party/unitree_g1_mjcf/`; that same file is the model `runtime/fk.py` and
  `teleop/retarget.py` load.
- `action_order` is the 9-D action vector of CLAUDE.md 5.3: 7 arm joints, waist yaw, pinch scalar.
  Anything that builds or consumes an action reads this list.
- `topics.command` is `rt/arm_sdk` and nothing else (D-007). `rt/lowcmd` owns the legs.
- Every entry under `latency` is `0.0` + `UNMEASURED` until Phase 1 measures it with the method
  written in `latency.method`. `runtime/clock.py` applies them (`docs/clock.md`).
- `teleop` holds the two operator-side maps of `teleop/retarget.py` (T-013, `docs/teleop.md`):
  `pico_to_pelvis`, the 4x4 from the Pico controller frame to the pelvis frame, an UNMEASURED
  identity until Phase 1 calibrates it; `rest_pose_rad`, the 8 commanded joints' rest pose, which is
  the IK's posture target and seed; and `teleop.ik`, the solver settings. The `ik` block carries no
  `_status` because it is design choices, and it holds no limits: the joint limits and the velocity
  limit the solver obeys are read from `config/safety.yaml` (R3).

### `config/safety.yaml`

The envelope `runtime/safety.py` enforces (R1, R3): the session gate, the workspace box, per-joint
limits, the waist clamp, the velocity and rate limits, and the hand's pinch range.

**Every number in this file is an unmeasured placeholder, and may be tightened or loosened only by a
human commit (R3).** Fable may propose a change in `agents/DECISIONS.md`; no agent applies one.

- `workspace_box_m` is an axis-aligned box in the G1 pelvis frame (+x forward, +y left, +z up),
  applied to the wrist point and the fingertip pinch point (workspace_box_m.points, T-043). The placeholder covers the table region in front of and to the left of
  the robot; the z span is a guess because the table height relative to the pelvis is unknown.
- `joint_limits_rad` is the MJCF range of each joint tightened by 5 degrees on each side, so a
  command can never ride a mechanical stop. `tests/test_config.py` asserts exactly this relation
  against `config/robot.yaml`, so the two files cannot drift apart.
- `waist_yaw_clamp_rad` (0.6 rad) is applied on top of the waist joint limit; the effective limit is
  the tighter of the two.
- `session.default_seconds` is 7200 (CLAUDE.md 4.6). `session.file` is written **only** by a human
  running `tools/hardware_checks/enable_session.py`.

### `config/cameras.yaml`

The three observation streams of CLAUDE.md 5.3: `top` (Brio, above the table), `oblique` (Orbbec Ego
left RGB over UVC, per D-009), `palm` (DexH15 palm camera). Each carries a V4L2 `device` selector,
the capture resolution/fps/fourcc, and `policy_resolution`, the size the policy actually sees.

`policy_resolution` is a design choice from the brief and is **not** a placeholder; every capture
value is, because no camera node was ever opened (T-002). Prefer a stable `/dev/v4l/by-id/...` path
over `/dev/videoN`: node numbers move when devices are replugged, and a policy trained on `top` must
never be fed `oblique`.

`top.crop` is the board region within the full Brio frame and is filled in by `board/calibration.py`
(T-008) from the AprilTag corners.

### `config/board.yaml`

Board geometry, AprilTags, and the cell table.

**The engine team owns the true topology and the true cell ids.** Everything under `layout` is a
placeholder so that `engine/stub.py`, the goal heatmaps and `eval/protocol.py` have something
concrete to address, and `layout_status: UNMEASURED` says so. When the engine team delivers, this
table is *replaced*, not merged, and every dataset recorded against the old one keeps the old
`board_config_hash`.

The placeholder layout is a standard 15x15 Ludo cross at a 40 mm pitch (15 x 40 = 600 mm). Grid
column `c`, row `r` maps to `board_xy_mm = [(c - 7) * 40, (7 - r) * 40]`, with `r = 0` the far edge
and the origin at the board centre. The four arms are 3 cells wide and 6 long; the centre 3x3 is the
finish and holds no addressable cell.

| group | count | where |
|---|---|---|
| `track-0` .. `track-47` | 48 | the two outer lanes of each arm, walked as one cycle in the direction of play |
| `<C>-home-0` .. `<C>-home-5` | 4 x 6 | the middle lane of each arm, `home-0` at the tip where the piece enters, `home-5` next to the centre |
| `<C>-base-0` .. `<C>-base-3` | 4 x 4 | a 3-cell-wide square in each corner region between two arms |

Colours `<C>` are `R`, `G`, `Y`, `B`. Steps between consecutive track cells are 40 mm within a lane,
56.57 mm (one diagonal) at the four outer corners of the cross, and 80 mm at the four arm tips where
the ring crosses the mouth of a home lane. That last step is where this layout differs from a
standard 52-cell Ludo ring: the tip cell belongs to the home lane here, which is what turns 52 track
cells into 48 and 5 home cells into 6, as the task specifies.

Starts are R `track-12`, G `track-24`, Y `track-36`, B `track-0` (evenly spaced 12 apart), and each
colour's home entry is the track cell immediately before the next colour's start.

`board_origin_in_base` (the board pose in the pelvis frame) is UNMEASURED; `runtime/safety.py` never
uses it, but `teleop/retarget.py` and `eval/` will.

### `config/hand.yaml`

The DexH15 transport, its 15 joints and 7 motors, the per-joint limits, the pinch synergy of
CLAUDE.md 5.4, and the glove-to-scalar mapping.

`joint_order` is the order `getJointPositionsAngle` / `setJointPositionsAngle` use. **It is a
hypothesis, not a fact**: it is the Paxini bundle's control-interface finger order
`[index, middle, ring, pinky, thumb]` crossed with its per-finger joint names, and that same bundle
warns that the SDK slot order is not the URDF order (`docs/sdks.md` 4.6). `joint_order_status` is
UNMEASURED until Phase 1 commands one joint at a time and watches which one moves.

The synergy is a single scalar `s` in [0, 1]:

```
q = open_pose + s * (closed_pose - open_pose)
```

i.e. the 15x2 matrix of CLAUDE.md 5.4 applied to `[1, s]`. All three poses stay the literal
`UNMEASURED` until the Phase 1 bench test (10 grasps on a horse, 10 on the die) fills them in,
because a wrong pose here closes the hand on a finger. The same scalar is what the glove drives
during teleop, so operator intent and robot action live in one dimension; the raw 15 hand joints and
17 glove channels are recorded anyway.

Units: the SDK's get/set work in *normalised* angle and `DexH15Kinematic.calculateRealAngle`
converts. `joint_limits_rad` here is in real radians; `drivers/dexh15.py` owns the conversion.

### `config/training.yaml`

Rates (5.2), observation and action spaces (5.3), the Diffusion Policy and ACT hyperparameters
(5.7), augmentation, dataset format (5.6) and compute targets (5.8).

**This file must contain no placeholders** - everything in it is a design choice from the brief, not
a property of the hardware. `unmeasured("training")` returning `[]` is an acceptance criterion and a
test. Anything that has to be measured belongs in one of the other five files.

Two entries are load-bearing beyond their value: `diffusion.execute: 8` of `chunk: 16` is the
receding horizon of 5.7, and `augmentation.geometric_on_top: false` is not tunable - the goal heatmap
lives in the `top` frame and a geometric augmentation would desynchronise it from the image.
