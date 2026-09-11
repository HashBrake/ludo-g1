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

## What is not here yet

`teleop/recorder.py` (LeRobot v2 episode capture) and `teleop/operator_ui.py` (goal display, episode
keys) are the rest of Phase 2 and are separate tasks. So is the clutch and the 30 Hz loop that wires
`pico_bridge` -> `pico_to_g1_base` -> `ArmIK` -> `runtime/safety.py` -> `drivers/g1_arm.py`.
