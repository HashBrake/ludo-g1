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
| `Envelope` | R3 | Joint limits, waist clamp, workspace box, velocity, command rate, pinch range |
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
5. **Joint velocity.** `|target - reference| / dt` must stay under `joint_velocity_limit_rad_s`, or
   the command is *rejected*. The reference is the previously accepted command and the monotonic time
   since it. When the previous command is older than `command_gap_reset_s` — and for the first
   command of a stream — the reference is instead the **measured state**, aged by exactly
   `command_gap_reset_s`: a fresh command may step the arm by `velocity_limit × gap_reset` from where
   the arm actually is, and no further. That is what stops a lunge when a stalled stream resumes.
6. **Workspace box.** `fk(joints)` gives the position of `workspace_box_m.point`
   (`left_wrist_yaw_link`) in `workspace_box_m.frame` (`g1_pelvis`); it must lie inside
   `[min + margin_m, max - margin_m]`, or the command is *rejected*. The box is checked on the
   **clamped** targets, so what is checked is exactly what would be sent.

Only then is the command recorded as the new reference and returned. A rejected command never becomes
the reference.

`fk` is injected (`Callable[[np.ndarray (8,)], np.ndarray (3,)]`, radians in, metres out). The real
one arrives with T-011; tests inject a mock. An envelope built **without** one fails closed: every
`check()` raises `workspace_box`, because an unverifiable box is not a passed box. An `fk` that
raises, or that returns anything but three finite numbers, is treated the same way.

`Envelope.reset()` drops the rate and velocity reference; a driver calls it when it releases and
re-takes the arm. `Envelope.watchdog_timeout_s` is exposed here for the driver that implements the
arm_sdk weight release (D-007); the release itself is a driver concern and is not implemented in this
module.

### Clamped, or rejected?

A limit that the arm can simply be held at is **clamped** (joint limits, waist clamp, pinch range and
slew): stopping the whole command because the operator pushed a joint 2° past its limit would make
teleoperation unusable and would not make anything safer. Everything that indicates the command is
*wrong* rather than merely far — a velocity step, a point outside the box, a command rate that cannot
be real, a NaN, a missing session — raises `SafetyViolation`, whose `rule` field names which of
`command_rate`, `non_finite`, `joint_velocity`, `workspace_box`, `session_gate` refused it. Every
rejection is logged through `runtime.log` as `safety_reject` with the rule and the numbers.

## Two clocks, never mixed

The session file is a human artifact, so its timestamps are wall clock and expiry is judged against
`datetime.now`. Rate and velocity are judged against `runtime.clock.now_ns`, which is monotonic and
cannot be moved by NTP. `check()` takes `now_ns` explicitly so that tests are deterministic; in
production it defaults to `runtime.clock.now_ns()`.

## Before a motion run

Per CLAUDE.md 4.6, `agents/BUILD_LOG.md` must state, for every run that moves the robot: what will
move, the envelope in force (the `config/safety.yaml` hash from `runtime.config.config_hash`), and
the observed outcome.
