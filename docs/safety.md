# Safety: the session gate and the envelope

`runtime/safety.py` is the only path from a policy, a teleoperator or a test to a robot joint
(CLAUDE.md **R1** and **R3**). Every number it enforces lives in `config/safety.yaml`, which may only
be loosened by a human commit; this page explains what the code does with them.

```python
from runtime.safety import Guard, SafetyViolation

guard = Guard.from_config(fk)             # session gate + envelope, from config/
safe = guard.admit(cmd, state)            # -> MotionCommand to send, or raises SafetyViolation
```

Nothing below is a suggestion: a driver that sends `cmd` instead of `guard.admit(cmd, ...)` is a
violation of R3, and Fable audits for it every phase (CLAUDE.md section 8).

## The three pieces

| Piece | Rule | What it does |
|---|---|---|
| `SessionGate` | R1 | Is a human-enabled motion session running right now? |
| `Envelope` | R3 | Joint limits, waist clamp, workspace box, velocity, first-command step, command rate, pinch range |
| `Guard` | both | `admit()`: gate (unless simulated), then envelope, always |

## R1: the session gate

A motion command is any message that sets a target position, velocity, torque or trajectory for a G1
joint or a DexH15 actuator. None may leave this machine unless `hardware/session.enable` exists, is
unexpired and was written by a human. That file is git-ignored, is written **only** by
`tools/hardware_checks/enable_session.py`, and no agent ever creates, edits, copies or restores it.

A human opens a session at a keyboard:

```
.venv/bin/python tools/hardware_checks/enable_session.py
```

It refuses (exit 2) when stdin is not a terminal, asks for a name, and asks the four items of
CLAUDE.md 4.6 — e-stop within reach, legs locked, workspace clear, humans out of the arm envelope —
each of which must be answered `yes`. Then it writes, atomically, exactly four lines:

```
enabled_by: Alois
enabled_at: 2026-09-15T14:02:11+07:00
expires_at: 2026-09-15T16:02:11+07:00
checklist: confirmed
```

Timestamps are Asia/Bangkok wall clock with the offset written out. The length is
`session.default_seconds` (2 h). **To end a session early, delete the file.**

`SessionGate.status()` returns `SessionStatus(valid, reason, enabled_by, expires_at)` and fails
closed on every one of: no file, an unreadable file, a line that is not `key: value`, a duplicate
key, a missing `session.required_fields` entry, an empty `enabled_by`, `checklist` not equal to
`session.required_checklist_value`, a timestamp without a UTC offset, `expires_at` not after
`enabled_at`, a window longer than `session.max_seconds` (8 h — a typo must not grant a week), an
`enabled_at` in the future, and an `expires_at` in the past.

The file is re-`stat`ed on every `status()` call and re-read whenever it changed, and expiry is
re-judged against the wall clock every time. A session that runs out mid-run stops the next command;
deleting the file stops the next command. There is no caching beyond "the file has not changed".

**There is no bypass.** No environment variable, no flag, no config key, no "dev mode". The single
exception in the code is `Guard(..., simulated=True)`, which is keyword-only, defaults to `False`,
skips the session gate *only* (never the envelope), and is set by `drivers/mock` and by simulated
robots alone (R1: "Simulated robots are not subject to the gate"). `tests/conftest.py` asks the same
gate, so a test marked `@pytest.mark.motion` is skipped unless a session is live.

## R3: the envelope

`Envelope.from_config(fk)` builds itself from `config/safety.yaml` and cross-checks it against
`config/robot.yaml`. It refuses to build at all when

* the joint names or their order differ between the two files, or
* a safety limit is **wider** than the joint's mechanical range in `config/robot.yaml`, or
* the workspace box is empty once the margin is applied, or a limit is inverted or non-positive.

The envelope may only ever be tighter than the hardware.

### What `check()` does, in order

1. **Command rate.** A command less than `1 / command_rate_limit_hz` after the previously accepted
   one is *rejected*, not queued: a runaway loop must not saturate the DDS link.
2. **Finiteness.** A NaN survives `np.clip` and would be sent as a joint target, so a non-finite
   target, pinch or state is rejected.
3. **Joint limits and the waist clamp.** Out-of-limit targets are **clamped**, and the names of the
   clamped joints come back on the returned command's `clamped` field. The waist yaw limit is the
   tighter of `joint_limits_rad[waist_yaw_joint]` and `±waist_yaw_clamp_rad`; the two are folded
   together at construction, so a waist clamp can never be forgotten at check time.
4. **Pinch.** The scalar is clamped to `hand.pinch_scalar_range` (reported as `pinch_scalar`) and
   then slew-limited to `hand.pinch_rate_limit_per_s` against the last accepted pinch (reported as
   `pinch_rate`). The hand is one scalar, and a fast finger is not a reason to drop an arm command.
5. **First-command step.** A command whose velocity reference is *fresh* — the first command of a
   stream, or the first after a gap longer than `command_gap_reset_s` — is compared against the
   **measured state**, and no joint may be further from it than `first_command_max_step_rad`
   (0.05 rad), or the command is *rejected* with the rule `first_command_step` naming the worst
   joint. Nobody is tracking the arm at that moment, so the command is not a step in a trajectory,
   it is a jump to wherever the new sender happens to be pointing. T-032 measured a teleop stream
   engaging with a **0.443 rad** jump in one 33 ms tick, which the velocity rule below allowed
   because it ages a fresh reference by `command_gap_reset_s` (0.75 rad of slack); D-018 closed
   that. A sender that wants to move the arm somewhere else engages from where the arm is and walks
   there under the velocity limit — which is exactly what `teleop/loop.py`'s clutch does.
6. **Joint velocity.** `|target - reference| / dt` must stay under `joint_velocity_limit_rad_s`, or
   the command is *rejected*. The reference is the previously accepted command and the monotonic time
   since it. When the previous command is older than `command_gap_reset_s` — and for the first
   command of a stream — the reference is instead the **measured state**, aged by exactly
   `command_gap_reset_s`. That fresh case is the one rule 5 caps first, so in practice the velocity
   rule governs a stream that is already running and rule 5 governs how one starts.
7. **Workspace box.** `fk(joints)` gives the position of `workspace_box_m.point`
   (`left_wrist_yaw_link`) in `workspace_box_m.frame` (`g1_pelvis`); it must lie inside
   `[min + margin_m, max - margin_m]`, or the command is *rejected*. The box is checked on the
   **clamped** targets, so what is checked is exactly what would be sent. The frame, the point and
   the fk are the next section.

Only then is the command recorded as the new reference and returned. A rejected command never becomes
the reference.

`fk` is injected (`Callable[[np.ndarray (8,)], np.ndarray (3,)]`, radians in, metres out).
`Envelope.from_config()` injects the real one, `runtime.fk.left_arm_fk`, when no `fk` is passed;
passing one explicitly overrides it, which is what the envelope's own tests do. An envelope built
**without** one — the `Envelope(...)` constructor's default — fails closed: every `check()` raises
`workspace_box`, because an unverifiable box is not a passed box. An `fk` that raises, or that
returns anything but three finite numbers, is treated the same way.

### The box: frame, point, and the kinematics behind it

**Frame (`g1_pelvis`).** The origin is the G1's pelvis body origin, the frame the robot's own state is
naturally expressed in: **+x forward out of the chest, +y to the robot's left, +z up**. It is attached
to the pelvis, so it turns with the robot but *not* with the waist: a waist yaw moves the arm **within**
this frame, which is exactly why the waist is one of the 8 joints the fk takes. On the rig the pelvis
is bolted down, so this frame is also fixed relative to the table; the transform from it to the board
frame is a Phase 1 measurement and is not needed here.

**Point (`left_wrist_yaw_link`).** The box is checked on **one** point: the origin of the left wrist
yaw link, which is where the DexH15 bolts on. The offset from there to the fingertip pinch point is
`UNMEASURED` until Phase 1 (`config/hand.yaml`), so **the hand, the fingers and a held horse stick out
past the box and are not themselves checked**. `config/safety.yaml` says the box is drawn with that
slack already removed from the reachable volume, and `margin_m` (20 mm) is taken off every face on top
of it. When Phase 1 measures the tool offset, the right fix is a second checked point, not a wider box.

**The box itself, in words** (all values in `config/safety.yaml`, all `UNMEASURED` placeholders):
an axis-aligned box, 500 mm deep × 700 mm wide × 700 mm tall before the margin, spanning x
`0.15 … 0.65` m (from just in front of the chest to arm's length forward), y `-0.10 … 0.60` m (from
100 mm across the body's centreline to 600 mm out on the robot's left), and z `-0.40 … 0.30` m (from
400 mm below the pelvis to 300 mm above it). The margin shrinks it to `0.17 … 0.63`, `-0.08 … 0.58`,
`-0.38 … 0.28`. It is meant to cover the table region in front of and to the left of the robot and
nothing else: not the robot's own torso, not the operator's side of the table, not above head height.
The z span is a guess until the rig height is measured.

**The kinematics (`runtime/fk.py`, T-011).** `left_arm_fk(q7, waist_yaw)` — also callable as
`left_arm_fk(joints8)` in `action_order` — evaluates the vendored MJCF
`third_party/unitree_g1_mjcf/g1_29dof.xml` (T-012, checksums in its `MANIFEST.txt`) with mujoco:
the 8 commanded joints are written to the `mjcf_qpos_index` addresses `config/robot.yaml` records,
every other joint is held at the model's `qpos0` (zero for all of them), the floating base is pinned
to the origin with an identity quaternion so the result is already pelvis-relative, and
`mj_kinematics` is called — kinematics only, no dynamics, contacts or gravity. The model is compiled
once and cached; a call costs **8.4 µs** (mean of 1000, T-011), against a 16.7 ms budget at the 60 Hz
command rate limit. It is the same model the arm IK solves on (D-006), so the box and the IK cannot
disagree about geometry.

**Where the all-zero pose sits.** At all 8 joints zero the wrist is at
**(0.1998, 0.1487, 0.0952) m**, which is **inside** the current placeholder box (`tests/test_fk.py`
prints and asserts this). The G1's zero pose is not the arm hanging down — shoulder pitch zero points
the upper arm forward — so a zeroed arm reaching into the box is expected, not a sign the box is
wrong. It does mean the box does **not** by itself stop a command that parks the arm at zero; that is
a fact for the envelope review, and the numbers stay as committed until a human changes them (R3).

`Envelope.reset()` drops the rate and velocity reference; a driver calls it when it releases and
re-takes the arm. `Envelope.watchdog_timeout_s` is exposed here for the driver that implements the
arm_sdk weight release (D-007); the release itself is a driver concern and is not implemented in this
module.

### Clamped, or rejected?

A limit that the arm can simply be held at is **clamped** (joint limits, waist clamp, pinch range and
slew): stopping the whole command because the operator pushed a joint 2° past its limit would make
teleoperation unusable and would not make anything safer. Everything that indicates the command is
*wrong* rather than merely far — a velocity step, a first command that jumps, a point outside the box,
a command rate that cannot be real, a NaN, a missing session — raises `SafetyViolation`, whose `rule`
field names which of `command_rate`, `non_finite`, `first_command_step`, `joint_velocity`,
`workspace_box`, `session_gate` refused it. Every
rejection is logged through `runtime.log` as `safety_reject` with the rule and the numbers.

## Two clocks, never mixed

The session file is a human artifact, so its timestamps are wall clock and expiry is judged against
`datetime.now`. Rate and velocity are judged against `runtime.clock.now_ns`, which is monotonic and
cannot be moved by NTP. `check()` takes `now_ns` explicitly so that tests are deterministic; in
production it defaults to `runtime.clock.now_ns()`.

## Before a session

`tools/hardware_checks/session_preflight.py` (T-041) prints the go/no-go table a human reads before
running `enable_session.py`. It is read-only: it asks the gate for its status, reads the six config
files, opens each device through the read-only drivers for three seconds, and sends nothing.

```
.venv/bin/python tools/hardware_checks/session_preflight.py          # the table
.venv/bin/python tools/hardware_checks/session_preflight.py --json   # the same rows as a list
```

Each row is PASS, FAIL or SKIP, and the rows marked `*` are the ones the exit code is made of: the
placeholders in `session_preflight.MOTION_KEYS` that make a motion command wrong or impossible while
they are guesses (the envelope box, the joint and waist limits, the velocity and first-step caps, the
DDS interface, the arm gains, the two actuation and two teleop latencies, the controller-to-pelvis
transform, the hand's bus and pinch poses, the top camera and the AprilTag geometry), the e-stop the
session checklist must name by device (Q-004, D-004), and the `arm` and `hand` devices, which are the
two a motion command can reach (R1). The other rows -- the session gate itself, the sensor devices,
the board calibration, the dataset disk and the git tree -- are printed because a human about to open
a session wants to see them, and are not part of the verdict: none of them can make an arm move
wrongly. Exit 0 means every `*` row passed, 1 that one did not (a SKIP counts as not passing, because
a device that is absent is not a device that answered), 2 a usage error.

The tool is the first step of the Phase 1 session procedure: run it, clear every `*` FAIL, then a
human runs `enable_session.py`. It never writes `hardware/session.enable` and never asks for it; a
closed gate is the expected answer here.

## Before a motion run

Per CLAUDE.md 4.6, `agents/BUILD_LOG.md` must state, for every run that moves the robot: what will
move, the envelope in force (the `config/safety.yaml` hash from `runtime.config.config_hash`), and
the observed outcome.
