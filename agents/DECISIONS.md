# DECISIONS.md (Fable, append-only)

## D-001  Adopt the section 5 architecture as written  (2026-09-11T18:35+07:00)
Adopted: repo layout 5.1, rates 5.2, observation/action spaces 5.3, pinch synergy 5.4, engine contract 5.5,
LeRobot v2 dataset 5.6, Diffusion Policy primary with ACT baseline 5.7, laptop-collect / Greennode-train 5.8.
Rationale: the brief is the source of truth; no evidence yet that any part is wrong.
Alternatives rejected: none considered at bootstrap. Deviations will be logged here by number.

## D-002  Assumptions (3.3) and unknowns (3.4) that Phase 0 must verify  (2026-09-11T18:35+07:00)
Each item gets a verdict in docs/sdks.md (Opus, T-002) and a correction here (Fable) if wrong.
Assumptions from 3.3:
- A1 Python >= 3.10. PRE-VERIFIED at bootstrap: /usr/bin/python3 is 3.10.12; the DexH15 wheel is cp310 only, so 3.10 is pinned, not "or newer".
- A2 Unitree SDK2 talks DDS over wired Ethernet to the G1. UNVERIFIED. Prior lab setup (Teleopit README): laptop 192.168.123.2, robot 192.168.123.164.
- A3 Paxini SDK exposes DexH15 joint position control at >= 30 Hz and the palm camera. UNVERIFIED; examples position_control.py and camera.py exist in third_party/dexh15_sdk.
- A4 PxCap Pro exposes finger joint angles at >= 30 Hz and no absolute wrist position. UNVERIFIED; manual says 17 encoders + 17 joint angles, host-side ns timestamps.
- A5 Pico pipeline already produces G1 arm joint targets through IK. LIKELY WRONG as stated: the vendored pipeline (third_party/g1_pico_teleop, a Teleopit fork) is a full-body GMR retarget -> RL whole-body policy stack. Phase 0 must find the point in that stack where left-arm joint references exist (GMR/mink IK output) and whether it can run without the RL policy and without a headset-mounted body skeleton.
- A6 Greennode gives a Linux GPU VM over SSH with rsync. UNVERIFIED; no credentials at ~/.config/ludo-g1/env yet (Q-001).
- A7 Engine exposes a network or Python interface later; engine/stub.py stands in. ADOPTED.
Unknowns from 3.4:
- U1 Glove->DexH15 and controller->G1 wrist latency: Phase 1 measurement.
- U2 DexH15 partial joint commands: Phase 0 reads the SDK; Phase 1 tests on hardware.
- U3 Reachable cells with hip mount + waist yaw: Phase 1.
- U4 Horse arrow orientation matters to the engine: ASSUMED NO until the engine team says otherwise (Q-006).
- U5 Disk for datasets: PRE-VERIFIED SHORT. /home has 15 GB free of 76 GB; target is >= 500 GB (Q-002).
- U6 Greennode instance type, GPU, access: Q-001.

## D-003  third_party layout and what is actually present  (2026-09-11T18:35+07:00)
Found at bootstrap (top level, not under third_party/): dexh15_sdk/, g1-pico-teleop-main/g1-pico-teleop-main/,
pxcap_pro_teleop_sdk/, pxcap_pro_sdk.md. Moved into third_party/ as:
- third_party/dexh15_sdk/            DexHandSDK 3.2.1 (.deb, pxdex cp310 wheel, examples for dexh5/13/15, README_CN.md)
- third_party/g1_pico_teleop/        lab fork of Teleopit v0.5.0 (double nesting flattened)
- third_party/pxcap_pro_teleop_sdk/  PxCapPro local release (PyInstaller bundle with its own CPython, retargeting to DH13/DH15)
- third_party/pxcap_pro_sdk.md       PxCapPro SDK Linux manual (Chinese)
Not present anywhere: Unitree SDK2 Python (the submodule dir third_party/g1_pico_teleop/third_party/unitree_sdk2_python is empty;
the fork instead uses a C++ DDS bridge at third_party/g1_pico_teleop/third_party/g1_bridge_sdk), Orbbec SDK, the standalone
PxCapPro .deb the manual describes. See Q-003, Q-005.
Git policy: third_party/ is tracked except binary payloads that would bloat the repo (the 1.7 GB PyInstaller _internal/ tree and
executable, the 139 MB .deb, .tar.gz archives). They stay on disk and are listed in .gitignore. Nothing under third_party/ is
ever modified in place (section 7).
Rationale: the brief says Fable does the layout on first run; a plain move within the project folder is reversible.
Alternative rejected: leave SDKs at top level (breaks section 5.1 and the "wrap, do not patch" audit rule that keys on third_party/).

## D-004  Safety context inherited from the lab's prior G1 work  (2026-09-11T18:35+07:00)
The vendored Teleopit README and the lab's earlier sessions record that a software damp from the laptop failed once at a
critical moment and that the stock remote's damp chord can be inert while a custom low-level controller is running.
Decision: for LUDO-G1 the human e-stop is a physical path decided by Alois (Q-004), never a software call from this repo.
runtime/safety.py is a limiter, not the e-stop. This does not change R1-R3.

## D-005  Phase 0 task set  (2026-09-11T18:35+07:00)
T-001..T-011 in TASKS.md. Ordering rationale: scaffold and CI first (T-001) so every later task has a green pytest to commit
against; the SDK inventory (T-002) next because it can invalidate A5 and reshape Phase 1 and 2; config and clock (T-003, T-004)
before safety (T-005) because safety reads config/safety.yaml and rate-limits on the clock; mocks (T-006) after safety so the
mock robot path already goes through the envelope; engine stub (T-007) and calibration (T-008) are independent and give the
loop non-hardware work while questions are open. No task in Phase 0 has hardware: motion.

## D-006  A5 refuted: LUDO-G1 solves its own arm IK; the vendored Teleopit stack is reference only  (2026-09-11T20:25+07:00)
Evidence: docs/sdks.md section 7 (T-002, verified in REVIEW.md). The vendored pipeline needs a full body skeleton (headset +
two ankle trackers), drives all 29 joints through an RL balancing policy, and its Pico provider discards the controller pose.
Decision: teleop/retarget.py reads the left controller 6-DoF pose from pico_bridge (meters, quaternion xyzw) and solves an
8-DoF IK (left arm joints 15..21 + waist yaw 12) with mink on the G1 MJCF with legs and right arm fixed. Output goes through
runtime/safety.py to the G1 arm driver. GMR, the RL policy, the ankle trackers and the body-tracking dependency leave the
critical path. The vendored fork is kept under third_party/ as a reference for the DDS bridge and the Pico transport only.
Dependencies approved: mujoco, mink, pico_bridge 0.2.1 (pure-Python wheel from the BotRunner64 GitHub release, pinned by URL
and sha256), plus the G1 MJCF vendored into third_party/ with its license (T-012). Section 3.3 A5 of CLAUDE.md is wrong as
written; this entry corrects it. Alternative rejected: run Teleopit's arm-only mode (still needs skeleton input and the RL
policy; balancing policy on a rig-mounted robot with locked legs is unsafe and pointless).

## D-007  G1 write path is rt/arm_sdk only  (2026-09-11T20:25+07:00)
drivers/g1_arm.py publishes LowCmd_ on rt/arm_sdk with motor_cmd[29].q as the enable weight (1 enable, 0 release), commands
only left arm 15..21 and waist yaw 12, and leaves every other slot untouched. rt/lowcmd is never used by drivers/, runtime/
or policy/ (it owns the legs). The driver publishes at the SDK's 50 Hz with zero-order hold of the 30 Hz policy action;
whether interpolation is needed is a Phase 1 measurement. The weight must ramp 0 -> 1 on enable and 1 -> 0 on release so
the robot's own controller never sees a step; the ramp is a driver mechanism, not scripted motion (R2).

## D-008  Q-009 decided: keep opencv-python, drop opencv-python-headless  (2026-09-11T20:25+07:00)
unitree_sdk2py depends on opencv-python; teleop/operator_ui.py (Phase 2) needs a GUI build anyway. Two distributions owning
cv2/ is a hazard. T-012 removes the headless pin and re-resolves.

## D-009  Orbbec depth deferred; oblique is RGB over UVC  (2026-09-11T20:25+07:00)
pyorbbecsdk on PyPI is unusable (docs/sdks.md 8.2); the Ego enumerates as a UVC stereo pair. Observation `oblique` is the
left RGB stream at 640x480 as CLAUDE.md 5.3 already specifies. Depth is not in the policy input; Q-008 stays open for Alois
to choose an SDK route later without changing anything downstream.

## D-010  Envelope facts from the real FK (T-011)  (2026-09-11T23:00+07:00)
The G1 all-zero pose puts the left wrist at [0.20, 0.15, 0.10] m in the pelvis frame, inside the placeholder box: the upper
arm points forward at zero, it does not hang. Wrist roll and wrist yaw do not move the wrist point, so the box currently
constrains 6 of 8 joints. Phase 1 adds a second checked point (the DexH15 fingertip pinch point, tool offset measured on the
hand) rather than widening the box. config/safety.yaml is unchanged; Fable proposes, a human applies (R3).

## D-011  Recorder writes LeRobot v2 through the lerobot package; torch CPU comes to the laptop now  (2026-09-12T00:40+07:00)
The Phase 2 recorder (T-017) uses LeRobotDataset.create / add_frame / save_episode from a pinned lerobot release rather than
hand-writing parquet and mp4, so the format matches what policy/train.py will load. That pulls torch (CPU wheel) onto the
laptop, which Phase 3 inference needs anyway. Alternative rejected: hand-rolled v2 layout (format drift risk, no benefit).
Both pins go into requirements.txt in T-017 with the resolved versions recorded in docs/setup.md.

## D-012  Phase 0 wrap-up and early non-hardware Phase 2/5 work queued  (2026-09-12T00:40+07:00)
Every Phase 0 task except T-010 (running) and T-014 is accepted; the two Phase 0 exit checks that need a human (Greennode
dummy job, Q-001; real board still, H-001) stay open without blocking. To keep the loop stocked with non-hardware work
(section 4.5 step 4) while those wait: T-015 Phase 0 report, T-016 mock end-to-end controller loop, T-017 recorder on mocks.

## D-013  Builder guidelines from the T-010..T-016 reviews  (2026-09-12T02:10+07:00)
1. Never commit with --no-verify, not even for a markdown-only follow-up; if the hook is slow, batch the hash record into the
   work commit by amending before the first push (there is no remote yet) or accept the wait.
2. Style guideline (not a return reason): keep a module under the size the task names by moving report/CLI types to a sibling
   module rather than by cutting docstrings.
3. MockPerception fails every RECOVER because a recovery restores the engine's own belief (empty delta). A mock run can show
   that recoveries are issued and executed, never that they succeed; the first real success rate for RECOVER comes from
   Phase 4 eval on the robot, as the brief already says.
