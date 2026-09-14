# Phase 1 session runbook

Written 2026-09-12 (T-044). This is the order the first three hardware days run in. Alois reads it
before the first day; the agents follow the same steps and write the results where each step says.

Every command is run from the repo root (`/home/alois/Desktop/ludo-g1`, Q-007), Python through
`.venv/bin/python`. Days 1 and 2 send no motion command and need no session (R1); only day 3 does,
and it does not start until `session_preflight.py` exits 0 and a human has run `enable_session.py`.
No number in this document is a measurement: every measurement lands in `agents/BUILD_LOG.md` with
the command that produced it (R5).

Three days, in this order, because each one unblocks the next:

| Day | What happens | Session | Gate to the next day |
|---|---|---|---|
| 1 | H-002, H-003, H-004; 10-minute statistics for all seven streams; fill the device config keys | none | every device row of the pre-flight PASS or SKIP for a known reason |
| 2 | H-001 board stills; homography; perception on the real still | none | `config/board_calib.yaml` written, reprojection error accepted |
| 3 | Q-004 answered, session opened, first motion: latency, envelope, hand bench, reachability | **yes** | the Phase 1 verify list of CLAUDE.md section 6 |

---

## Day 0 (any time before day 1): the tree is where the agents left it

The pre-flight table is the one artefact both the human and the agents read, so start from a green
tree and a known table.

```
.venv/bin/python -m pytest -q
```

Check: the suite is green (14 skips is the no-hardware baseline of 2026-09-12: they name H-002,
H-003, H-004 and the missing session).

```
.venv/bin/python tools/hardware_checks/session_preflight.py
```

Check: it exits 1 and prints `NO-GO for a motion session.` This is the baseline to compare against;
on 2026-09-12 it was 0 of 28 motion-relevant checks passing (T-041 result in `agents/TASKS.md`).

**What the agents do with this.** Nothing is written on day 0. If the suite is not green the loop
stops before any hardware day (R4) and the failure goes to `agents/BLOCKERS.md`.

---

## Day 1: read-only, every device streamed for ten minutes

No session. No motion command is possible from any command on this day: the arm driver has no DDS
writer at all (T-018), the hand driver has no `set*` call (T-019), and the glove and the controller
are input devices (T-020).

### 1.1 Bring up the robot LAN (H-002)

Physical: plug the G1's Ethernet into the laptop, power the robot, wait for its Orin to boot, then
follow H-002 steps 3 and 4 in `agents/HARDWARE_NEEDED.md` (the `robot-lan` nmcli profile).

```
ping -c 2 192.168.123.164
```

Check: two replies. If not, stop at H-002; the rest of day 1 does not need the robot and continues.

### 1.2 Plug in the Brio, the DexH15 and the glove (H-003, H-004a)

Physical: H-003 steps 1 to 3 (Brio on a USB 3 port, DexH15 Modbus adapter, glove; **the hand's
motors stay off**, nothing in day 1 enables a motor) and H-004 step (a).

```
ls -l /dev/ttyUSB* /dev/ttyACM*
```

Check: the nodes are group `dialout` readable. If not, `sudo usermod -aG dialout $USER`, then log
out and in (H-003 step 3; do it once for both devices).

### 1.3 Put the headset on the network (H-004b)

Physical: free TCP 63901 (`ss -ltnp | grep 63901`, then `systemctl --user stop holosim-pcservice` —
the leftover XRoboToolkit service found by T-020 on 2026-09-12), power the headset, open PicoBridge,
and choose one of H-004's two network paths. The laptop never joins the `g1-teleop` AP.

Check: `ss -ltnp | grep 63901` prints nothing before the bridge is started.

### 1.4 Enumerate everything once

```
.venv/bin/python tools/hardware_checks/list_devices.py
```

Check: the interface holding 192.168.123.2 is printed with `<-- G1 LAN`; a `046d` Brio video node, a
DexH15 palm-camera node, and two serial nodes (`067b:23a3` for the hand, an unknown vendor id for
the glove) are listed.

```
.venv/bin/python tools/hardware_checks/list_devices.py --json
```

Check: exit 0. Keep the JSON: it is the evidence for the `docs/sdks.md` rows that are still
`UNMEASURED`.

### 1.5 Fill the device keys the drivers refuse to run without

Edit, by hand, from the JSON of 1.4 (stable `by-id` paths only, never `/dev/videoN` or `/dev/ttyACMn`
— the node numbers move). Check the link you paste actually resolves to the node you mean
(`readlink -f`): a multi-interface camera can give two nodes the same by-id name, which is why
`oblique` ended up on `/dev/v4l/by-path/...` (T-046, D-024):

| File | Key | Source |
|---|---|---|
| `config/robot.yaml` | `network.dds_interface` | the interface marked `<-- G1 LAN` |
| `config/cameras.yaml` | `top.device`, `top.usb_id` | the Brio node |
| `config/cameras.yaml` | `palm.device`, `palm.usb_id` | the node the hand adds |
| `config/hand.yaml` | `device.port` | the `/dev/serial/by-id/...` path of the DexH15 adapter |
| `config/hand.yaml` | `glove.port`, `glove.usb_id` | the glove's node and vendor:product |
| `config/robot.yaml` | `teleop.pico.advertise_ip` | only on H-004's robot-NAT path; leave `UNMEASURED` on shared Wi-Fi |

Check: rerun 1.4's `--json` and compare each pasted value character for character. A wrong `top.device`
is the one error that survives silently into a trained policy.

### 1.6 Ten minutes per stream (CLAUDE.md section 6, Phase 1 read-only)

One command per device, in this order (the arm first: if `rt/lowstate` is silent, the robot is in a
mode that day 3 cannot use, and that is worth knowing before the other 60 minutes are spent).

```
.venv/bin/python tools/hardware_checks/stream_stats.py --backend real --stream arm --seconds 600 --json
.venv/bin/python tools/hardware_checks/stream_stats.py --backend real --stream hand --seconds 600 --json
.venv/bin/python tools/hardware_checks/stream_stats.py --backend real --stream palm --seconds 600 --json
.venv/bin/python tools/hardware_checks/stream_stats.py --backend real --stream top --seconds 600 --json
.venv/bin/python tools/hardware_checks/stream_stats.py --backend real --stream oblique --seconds 600 --json   # done: T-046
.venv/bin/python tools/hardware_checks/stream_stats.py --backend real --stream glove --seconds 600 --json
.venv/bin/python tools/hardware_checks/stream_stats.py --backend real --stream pose --seconds 600 --json
```

Check: per run, exit 0 and a non-zero achieved rate. Exit 3 means the stream is absent, busy or
silent, and the message names the config key or the H-item that is missing — fix that, do not rerun
blindly. Nothing on the board and nobody near the arm: these are ten-minute recordings of what the
devices do when undisturbed, and a hand waved through the Brio frame is a drop that is not the
camera's.

### 1.7 The read-only tests, now that the devices answer

```
.venv/bin/python -m pytest -m readonly -q
```

Check: the tests that were skipped on 2026-09-12 now run and pass. A skip here means the device the
test names is still not reachable.

### 1.8 Pre-flight until only the session and the e-stop are left

```
.venv/bin/python tools/hardware_checks/session_preflight.py --json
```

Check: every `device` row is PASS with a plausible rate; the `dds_interface`, `hand port`, and
`top.device` config rows are PASS. Still FAIL, and expected to be: the e-stop row (Q-004, day 3), the
envelope and gain and latency placeholders (measured on day 3), the board calibration row (day 2),
and the dataset disk row (Q-002). Still SKIP: the session gate row.

**What the agents do with the results of day 1.** The seven JSON outputs and their rate, drop and
jitter numbers go into `agents/BUILD_LOG.md` under T-018 (arm), T-019 (hand, palm) and T-020 (glove,
pose), which are the acceptance lines those three tasks left open; T-010 owns `top` and `oblique`.
The achieved hand joint read rate becomes the **A3 verdict** and the achieved glove rate the **A4
verdict** in `docs/sdks.md` (CLAUDE.md 3.3, D-002). The measured pose rate replaces the placeholder
in `config/robot.yaml` `teleop.pico.input_hz` with `_status: MEASURED` and the date; `control.state_hz`
gets the same treatment from the arm run. Fable writes one `agents/DECISIONS.md` entry recording the
verdicts on A2, A3 and A4 and closing H-002, H-003 and H-004; Q-005 gets the device-side half of its
answer (the bundle binding was already proven host-side on 2026-09-12, but nothing about connect,
rate or channel order was). If a rate is far under target — a hand under 30 Hz, a Brio under 30 Hz —
that is a Phase 1 finding that reshapes CLAUDE.md 5.2, and Fable writes the decision before Phase 2
records anything.

---

## Day 2: calibration, still no session

### 2.1 The two board stills (H-001)

Physical: mount the Brio in its **final** top-down position, board in its play position, all four
AprilTags visible, no horses.

```
.venv/bin/python tools/hardware_checks/brio_still.py --out data/calib/board_empty.png
```

Check: exit 0; the tool prints the focus and exposure it settled on. Write those two numbers down —
a refocus after this invalidates the homography, so the Brio's autofocus stays off from here on.

Physical: place every horse in its base cell, change nothing else (not the camera, not the board).

```
.venv/bin/python tools/hardware_checks/brio_still.py --out data/calib/board_start.png
```

Check: exit 0, and the two PNGs differ only by the horses.

### 2.2 The homography

```
.venv/bin/python -m board.calibration --image data/calib/board_empty.png
```

Check: four tag ids, and a reprojection error the log states in pixels. It writes
`config/board_calib.yaml`. If a tag is missing, the still is bad — relight or reposition and retake
2.1 rather than accepting three tags.

### 2.3 Perception on the real still

```
.venv/bin/python -m board.calibration --image data/calib/board_start.png --no-write
```

Check: the same four tags and a reprojection error within a pixel of 2.2's. This proves the
calibration is stable across the two stills; it does not test the detector.

The detector check has no CLI yet: the agent runs `board.perception.detect()` on
`data/calib/board_start.png` with the calibration from 2.2 and compares the recovered occupancy
against where the horses actually are, cell by cell (`docs/board.md`, section "Perception", names the
comparison). Every threshold in `board/perception.py` today is a guess fitted to synthetic scenes,
so a mismatch here is expected and is a tuning task, not a failure of the day.

**What the agents do with the results of day 2.** `config/board_calib.yaml` exists, so the pre-flight
calibration row turns PASS and T-008's open acceptance line closes; H-001 is closed. The four
`board.apriltags.*` placeholders in `config/board.yaml` become MEASURED from the printed board (size
in mm, family, ids, centres), which clears four of the pre-flight's 25 config FAILs. The occupancy
comparison of 2.3 goes into `agents/BUILD_LOG.md` as the first real number for `board/perception.py`,
and Fable writes a `DECISIONS.md` entry either accepting the thresholds or opening a task to refit
them on the real still. The Brio focus and exposure from 2.1 go into `config/cameras.yaml` next to
`top.device`, because they are part of the calibration.

---

## Day 3: the first motion session

Nothing here starts until 3.0, 3.1 and 3.2 have all passed. This is the first time in the project a
joint is commanded.

### 3.0 Answer Q-004 and approve the envelope values (two human commits)

**(a) The e-stop.** Q-004 is "which physical action is the e-stop for a rig-mounted G1 with legs locked". D-004 already
decided that the e-stop is a physical path Alois chooses and never a software call from this repo,
and that `runtime/safety.py` is a limiter, not an e-stop. Answer Q-004 with a `HUMAN:` line in
`agents/QUESTIONS.md` (CLAUDE.md 4.8), then put the named action into the session checklist by
editing `config/safety.yaml` `session.checklist` — a human commit, because `config/safety.yaml` is
human-only (R3). The item must name the device or the action, not just promise one is near:
`session_preflight.py`'s `estop_row` looks for an e-stop word **and** a device word (remote, chord,
power, breaker, switch, button, plug, mains, killswitch, cut, ...), which is exactly the difference
between "e-stop within reach" and an answer.

```
.venv/bin/python tools/hardware_checks/session_preflight.py
```

Check: the `e-stop named` row is PASS and quotes the checklist item.

**(b) The envelope.** Every number the arm is limited by is still a placeholder, and it cannot be
anything else: the workspace box, the joint limits, the waist clamp, the velocity and first-step
caps, the watchdog and the arm gains are what **this session** measures (steps 3.5 and 3.4). D-022
settles it: a human reads each value, agrees it is conservative for a first session, and commits the
status change from `UNMEASURED` to `HUMAN_APPROVED`, leaving the value alone. The pre-flight then
passes those keys for this session and still fails them at `UNMEASURED`. This command prints the
values to read and the exact line to write beside each one:

```
.venv/bin/python tools/hardware_checks/session_preflight.py --show-envelope
```

The eleven status lines to change, and nowhere else:

- `config/safety.yaml`: `workspace_box_m.min_status`, `workspace_box_m.max_status`,
  `workspace_box_m.margin_m_status`, `joint_limits_rad_status`, `waist_yaw_clamp_rad_status`,
  `joint_velocity_limit_rad_s_status`, `first_command_max_step_rad_status`, `watchdog_timeout_s_status`
- `config/robot.yaml`: `control.kp_status`, `control.kd_status`, `control.weight_ramp_s_status`

Each becomes `<key>_status: HUMAN_APPROVED` with the approver and the date in a comment. Both files
are human-only (R3); **no agent makes this edit**, and an agent that thinks a value is wrong writes
to `agents/DECISIONS.md` instead. Approving is not measuring (R5): after 3.5 measures the real box,
the value and the status change together to `MEASURED`.

Check: `--show-envelope` shows `[HUMAN_APPROVED]` against all eleven keys, and `git status` shows
the two config files committed by a human, not by an agent.

### 3.1 Rehearse the loop on mocks, on the day, before the robot is enabled

```
.venv/bin/python -m teleop.loop --backend mock --seconds 10
.venv/bin/python -m runtime.controller --backend mock --seconds 60
```

Check: both exit 0 with zero Guard refusals. This costs 70 seconds and catches a config edit from
days 1 and 2 that broke the envelope or the clutch (D-018) before the arm is live.

### 3.2 Pre-flight must say GO for the step you are about to run

```
.venv/bin/python tools/hardware_checks/session_preflight.py
.venv/bin/python tools/hardware_checks/session_preflight.py --for t021_latency
```

The first command is the whole picture and will say `NO-GO for a motion session.` all day: it judges
every key, including `latency.arm_ms` and the pinch poses, which are what 3.4 and 3.6 are here to
measure. Read it anyway — every device row must be PASS with a plausible rate, and the board
calibration row must be PASS from day 2.

The verdict that decides whether 3.4 happens is the second command (D-022, T-045): `--for STEP`
counts only the keys that gate that step, and each later motion step has its own:
`--for t024_envelope` before 3.5, `--for t022_hand` before 3.6, and `--for t023_reach` before 3.7.
Run the one for the step, immediately before the step.

Check: exit 0 and `GO: every check that gates t021_latency passes.` Exit 1 means at least one starred
row is not PASS, and the run does not happen. Two FAILs that look like they should be waived never
are: `UNMEASURED` on any key (3.0(b) is how an envelope value becomes acceptable, not a waiver), and
`HUMAN_APPROVED` on a key days 1 and 2 measured read-only, which means someone approved a value that
should have been read off the machine.

### 3.3 A human opens the session (R1, CLAUDE.md 4.6)

Physical, in this order: legs locked and the rig bolted; workspace clear of horses, tools and cables;
everyone out of the arm's envelope; the e-stop from 3.0 in one hand, and it stays there for every
step below. **No agent runs this command, ever, and no agent creates, edits, copies or restores
`hardware/session.enable`.**

```
.venv/bin/python tools/hardware_checks/enable_session.py
```

Check: it asks for a name and four `yes` answers and writes `hardware/session.enable` with
`checklist: confirmed` and a two-hour `expires_at`. Rerun `session_preflight.py`: the session gate row
is now PASS and names the human and the expiry. To end the session at any moment, **delete the
file** — that stops the next command, and it is the correct thing to do at the first surprise.

### 3.4 First motion: the arm actuation latency (T-021)

Delivered by T-021: `tools/hardware_checks/arm_latency.py`. It commands a 0.05 rad step on one wrist
joint from the arm's current state, through `Guard.from_config(simulated=False).admit`, and measures
publish-to-first-state-change over 20 repetitions. The arm is over the table, empty; the human's hand
is on the e-stop for all 20.

Check: 20 completed repetitions, a p50 and a p99, and zero Guard refusals; the arm ends where it
started. Anything else and the run is an abort (below).

What moves: one wrist joint, 0.05 rad (2.9°), 20 times. Nothing else is commanded — `rt/arm_sdk`
carries only the eight slots the driver owns and the enable weight ramps 0 to 1 over
`control.weight_ramp_s` (D-007).

BUILD_LOG entry for this step: what moved (the joint name and the step size), the envelope in force
(`runtime.config.config_hash("safety")` and `config_hash("robot")`, plus the box, the clamp and
`first_command_max_step_rad` as values), the session (who enabled it, when it expires), the observed
outcome (p50 and p99 latency over 20 repetitions, the number of Guard refusals and their rule names),
and — if there was any contact, any motion outside the envelope, or the e-stop was used — a line
beginning `SAFETY INCIDENT:`, which stops the loop under R4(c).

### 3.5 The envelope boundary and the waist clamp (T-024)

Delivered by T-024: `tools/hardware_checks/envelope_test.py`. Under teleop, the wrist is driven slowly
towards each of the six box faces and towards both waist clamp directions; each approach must be
refused by the Guard within `workspace_box_m.margin_m` of the face. The human films it (CLAUDE.md
Phase 1 asks for video evidence) and keeps a hand on the e-stop.

Note that by then the box is checked at **two** points, not one: T-043 (in progress on 2026-09-12)
adds the DexH15 fingertip pinch point beside the wrist point, so a refusal may name `pinch_point`
rather than `left_wrist_yaw_link`, and the fingertip reaches the face first. The envelope test must
show a rejection for each face at whichever point hits it first, and the log records which.

Check: eight rejections, one per box face and one per waist direction, each within
`workspace_box_m.margin_m` of the configured face. A face that does not reject fails the step and
ends the session.

What moves: the wrist, slowly, towards one face at a time under the operator's hand, plus the waist
yaw in both directions. Nothing is meant to reach a face: the Guard stops each approach.

BUILD_LOG entry: what moved, the envelope in force (`runtime.config.config_hash("safety")` and
`config_hash("robot")`, with the box and the clamp as values), the session, the observed outcome —
the video file path, the eight rejections with the FK position and the point named at each, and the
distance from the configured face — and a `SAFETY INCIDENT:` line if a face did not reject, if there
was contact, or if the e-stop was used (R4(c)).

### 3.6 The pinch synergy and the hand bench (T-022)

Delivered by T-022: `tools/hardware_checks/hand_synergy.py` (record the open, closed and curled poses
on a horse and on the die into `config/hand.yaml` with `_status: MEASURED`) and
`tools/hardware_checks/pinch_bench.py` (10 grasp-and-hold trials on the horse and 10 on the die; the
object is placed in the pinch zone by hand, the hand closes, holds 5 s, opens; nothing is lifted).
The arm is idle or the hand is off the arm for this bench — it is still a motion task, so the session
from 3.3 must still be valid.

Check: 9 of 10 holds on the horse and 9 of 10 on the die (CLAUDE.md Phase 1), each trial recorded
as hold or slip as it happens, not from memory afterwards.

What moves: DexH15 fingers only, thumb and index (and middle if it helps), between the recorded open
and closed poses; the idle fingers are set once to the curled pose and do not move again.

BUILD_LOG entry: what moved, the envelope in force (`config_hash("safety")`, `config_hash("hand")`),
per-trial hold or slip for all 20 trials — the Phase 1 bar is 9 of 10 on each object — and the hand
latency p50/p99 with the command that measured it. A finger that pinches a human finger is a contact
event and gets the `SAFETY INCIDENT:` line.

### 3.7 Reachability with waist yaw (T-023)

Delivered by T-023: `tools/hardware_checks/reach_map.py`. The operator teleoperates to every cell the
game uses, with the target cell drawn on the Brio feed; the tool records reachable true/false and the
joint pose per cell into `config/board.yaml`. This needs day 2's calibration and the full teleop chain
(glove, controller, IK, clutch), so it is the last step of day 3 and usually the longest.

Check: every cell the game uses carries `reachable: true|false` and either a pose or a failure note
in `config/board.yaml` when the tool exits.

What moves: the whole left arm and the waist yaw, under the operator's control, through the clutch of
D-018 — the clutch is engaged with `e` only when the IK target is within `clutch_engage_tolerance_rad`
of the measured state, and any Guard refusal disengages it.

BUILD_LOG entry: what moved, the envelope in force (`runtime.config.config_hash("safety")`,
`config_hash("robot")` and `config_hash("board")`, the box and the clamp as values), the session,
and the observed outcome: the cells reached and the cells not reached, the tick count, the refusal
count with rule names, and a `SAFETY INCIDENT:` line if there was contact or out-of-envelope motion.

### 3.8 Close the session

Physical: delete `hardware/session.enable` (a human does this too), or let it expire. Power the arm
down before anyone reaches into the workspace.

Check: `session_preflight.py`'s session gate row is SKIP again.

**What the agents do with the results of day 3.** `latency.arm_ms` and `latency.hand_ms` become
MEASURED in `config/robot.yaml` from 3.4 and 3.6, and `runtime/clock.py`'s latency compensation
finally has real numbers (CLAUDE.md 5.2); `control.kp`, `control.kd` and `control.weight_ramp_s` get
the values the runs actually used. `config/hand.yaml` `pinch.open_pose`, `pinch.closed_pose` and the
curled pose become MEASURED from 3.6, and `tool.pinch_offset_m` (T-043's placeholder) is replaced by
the offset measured on the real hand. `config/board.yaml` gains a `reachable` flag and a pose per
cell from 3.7. From 3.5, Fable **proposes** the measured envelope for `config/safety.yaml` in a
`DECISIONS.md` entry and Alois commits it — an agent never applies it (R3). Fable writes the Phase 1
audit (CLAUDE.md section 8) and the Phase 1 report goes into `agents/BUILD_LOG.md`. Tasks that close:
T-021 (3.4), T-024 (3.5), T-022 (3.6), T-023 (3.7); Q-010 (the teleop rest pose and elbow
configuration) is answered on the rig during 3.7. If a cell the game uses is unreachable, Fable writes
the `DECISIONS.md` proposal for a board offset or a cell layout change that CLAUDE.md Phase 1 asks
for, and Phase 2 does not start until it is decided.

---

## Abort criteria and what happens after an abort

These apply to every motion step of day 3 (3.4 through 3.7). The human watching is the abort
authority, and abort is always cheap: the worst case of aborting is a repeated measurement.

**Abort the moment any of these is true**, without waiting to see whether it resolves:

1. The arm moves faster or further than the step the step description says (3.4 is 0.05 rad; if it
   traverses, abort).
2. The arm, the hand, or a horse touches anything not in the plan — the table, the board, the rig, a
   cable, a person.
3. The arm keeps moving after the tool prints that it finished, or moves while no tool is running.
4. Anything is heard that the previous run did not make: a motor whine, a click, a knock.
5. A Guard refusal appears where the step expects none (3.4, 3.6, 3.7), or no refusal appears where
   the step expects one (3.5 — an envelope face that does **not** reject is the more dangerous
   result, and it aborts the session, not just the step).
6. Anyone puts a hand inside the arm's envelope for any reason.
7. The session expires mid-run, or `hardware/session.enable` is not where it was.

**What the human does**, in this order: trigger the e-stop named in the answer to Q-004 (the action
quoted in `config/safety.yaml` `session.checklist`; until Q-004 is answered no motion step runs at
all); then stop the tool with Ctrl-C; then delete `hardware/session.enable`, which makes the next
command from any process impossible (R1). Do not put hands into the workspace before the arm is
powered down. Do not restart the tool to "see if it does it again".

**What the agent writes**, immediately, before anything else and before any retry — one
`agents/BUILD_LOG.md` entry, dated ISO 8601 with the Asia/Bangkok offset, containing:

- what moved: the joints commanded, the commanded values, and the last measured state read back;
- the envelope in force: `runtime.config.config_hash("safety")` and `config_hash("robot")`, and the
  box, joint limits, waist clamp, velocity limit and `first_command_max_step_rad` as values, so the
  hash can be resolved later;
- the session: `enabled_by` and `expires_at` from the gate;
- the observed outcome: what the tool printed, every Guard refusal with its rule name and worst
  joint, and what the human saw that triggered the abort;
- if there was unexpected contact, motion outside the envelope, or the e-stop was used: a line
  starting `SAFETY INCIDENT:` describing it.

**After an abort.** A `SAFETY INCIDENT:` line stops the build loop under R4(c) — no further task
runs, hardware or not, until Alois has read the entry and written a `HUMAN:` line saying what to do.
An abort with no safety incident (a Guard refusal in an unexpected place, a tool crash, an expired
session) does not stop the loop: the agent appends the entry, leaves the task `in_progress`, and
moves to non-hardware work while the cause is diagnosed. Either way, the session is over: the next
motion step needs a fresh `enable_session.py` run by a human, which means the four checklist items are
confirmed again. Three aborts on the same step with three distinct causes ruled out make it a blocker
in `agents/BLOCKERS.md` in the CLAUDE.md 4.7 format, with `work_that_can_continue` filled in.

---

## Referenced process items

`agents/HARDWARE_NEEDED.md`: H-001 (board stills), H-002 (robot LAN), H-003 (Brio, DexH15, glove
enumeration), H-004 (glove and PICO).
`agents/QUESTIONS.md`: Q-002 (dataset disk), Q-004 (the e-stop; day 3 does not start without it),
Q-005 (the PxCapPro SDK route), Q-007 (the repo path), Q-010 (teleop rest pose).
`agents/DECISIONS.md`: D-002 (the assumptions each day verifies), D-004 (the e-stop is physical),
D-007 (`rt/arm_sdk` only), D-010 and T-043 (the second checked point), D-018 (clutch and first-command
step cap).
Tasks: T-008, T-010, T-018, T-019, T-020 close their open acceptance lines on days 1 and 2; T-021,
T-022, T-023, T-024 are day 3; T-041 built the pre-flight this runbook starts from.
