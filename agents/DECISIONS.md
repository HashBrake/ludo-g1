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

## D-014  Phase 0 audit (CLAUDE.md section 8)  (2026-09-12T21:32+07:00)
Safety: hardware/session.enable git-ignored and absent from history (`git log --all -- hardware/session.enable` empty);
config/safety.yaml has one commit (its creation in T-003, values reviewed in REVIEW.md T-003); every motion path constructs
Guard.from_config (grep in REVIEW.md T-005/T-006); the one motion-marked test is skipped without a session.
Learned-only: no import of tools/ from runtime/, drivers/, policy/, board/, teleop/, engine/ (grep, plus the test added in
T-016); no literal joint arrays outside tools/ and tests/ (regex scan); HoldPolicy is the only "policy" and commands no motion.
Data/Training/Evaluation: not applicable yet (no dataset, no checkpoint); T-017 produces the first mock dataset and gets the
data audit at review.
Process: STATE.md rewritten every cycle; H-001..H-003 each have steps and a post-check; three or more non-hardware tasks in
todo after this entry (T-025, T-026, T-027 plus Phase 1 read-only tasks).
Phase 0 exit checks (section 6): tests on mocks green; safety.py rejects without a session (tests/test_safety.py);
docs/sdks.md covers all eight devices with state-read and target-write references; Greennode dummy round trip passed in
local transport only, the real one waits for Q-001 credentials. Phase 0 is closed with that single human-gated item open.
Phase 1 cannot start motion until Q-004 (e-stop path) is answered and a session is enabled; its read-only tasks (T-018,
T-019, T-020) can run as soon as H-002 and H-003 are done.

## D-015  Dataset format is LeRobot v3.0  (2026-09-11T21:32+07:00)
No installable lerobot release writes v2 on Python 3.10; lerobot 0.4.4 (the last 3.10-compatible release) writes v3.0.
CLAUDE.md 5.6 "LeRobot v2" is superseded by v3.0; the observation, action and metadata content of 5.3/5.6 is unchanged.
T-027 updates config/training.yaml dataset.format to lerobot_v3 and policy/dataset.py reads v3.

## D-016  OpenCV: two distributions pinned to one identical version  (2026-09-11T21:32+07:00)
lerobot requires opencv-python-headless; unitree_sdk2py requires opencv-python; both own site-packages/cv2. A reinstall
step after every install is fragile. Decision: pin both to the same version (opencv-python==4.12.0.88 to match the headless
pin lerobot resolves) so a fresh install writes identical files whichever comes last, and remove the reinstall recipe.
Supersedes D-008's choice of 5.0. Never uninstall either one. Applied by T-027 (the next task that touches requirements).

## D-017  Phase 3 preparation queued as non-hardware work  (2026-09-11T21:57+07:00)
With Phase 0 closed and every Phase 1 task gated on H-002/H-003/Q-004, the loop needs non-hardware work (4.5 step 4). Added
T-028 eval protocol and runner, T-029 Diffusion Policy wrapper + train/export, T-030 ACT baseline, T-031 real Greennode
launch. All run on the mock dataset; none claims a success rate (R5: HoldPolicy scores 0/20 by construction and the eval
JSON says so). Real training and evaluation still require Q-001, a real dataset (Phase 2 on hardware) and the robot.

## D-018  No teleop engage without a clutch; first-command step cap in the envelope  (2026-09-11T23:16+07:00)
T-032 measured that the first command of a teleop stream steps 0.443 rad in one 33 ms tick and is admitted, because the
velocity rule ages a fresh reference by command_gap_reset_s (0.5 s) and allows 0.75 rad. On the robot that is a lurch at
the moment the operator starts. Two changes, both tightenings: (1) runtime/safety.py gains a first-command step cap
(config/safety.yaml first_command_max_step_rad, placeholder 0.05 rad); (2) teleop/loop.py gains a clutch that holds the
measured state until the IK target is within tolerance and then blends in. T-033, P0, and T-021 depends on it. Alternatives
rejected: shrinking command_gap_reset_s alone (a long pause would still permit the same jump on resume); relying on the
operator to start near the rest pose (not a safety mechanism).

## D-019  Inference budget: the laptop CPU cannot run the diffusion policy at 10 Hz  (2026-09-11T23:16+07:00)
T-029 measured DDIM 10 at 804 ms median and DDIM 5 at 498 ms on this laptop (torch CPU, 293 M parameters, three ResNet-18
encoders at 240x320). CLAUDE.md 5.8's ladder is: DDIM 5, then ACT, then the Orin NX. DDIM 5 is 5x over budget, so the
decision is: (1) T-030 measures ACT on the same inputs (a single forward pass); (2) inference on the Jetson Orin NX 16 GB is
the expected landing and needs its JetPack/torch state confirmed (Q-011); (3) in parallel, a smaller configuration
(shared encoder, 120x160 inputs, fewer UNet channels) is measured in T-035 as a fallback that keeps the laptop viable.
No policy is chosen until Phase 3 eval numbers exist (R5). Training stays on Greennode (Q-001).

## D-020  Torch thread count is a configured value, measured on a quiet machine  (2026-09-12T01:17+07:00)
T-030 measured ACT act() at 154 ms with 4 torch threads and 3.7-6 s at the default 14 on this laptop (oversubscription of
the 14-thread pool on small tensors); the diffusion policy went 804 -> 560 ms (DDIM 10) at 4 threads. D-019's ladder stays,
but the first step is free: config/training.yaml gains `compute.torch_threads` (placeholder 4, UNMEASURED) applied by
policy/train.py, both adapters and eval/run_eval.py at start; T-035 re-measures both policies at 1/2/4/8 threads on a quiet
machine (no other builder running) and records the table. ACT at ~150 ms is still above the 100 ms budget at 10 Hz but
within reach of the 5.8 ladder (ACT then Orin NX); the diffusion policy is not, on this CPU.

## D-021  Inference can land on the laptop: ACT 91 ms, diffusion_small 80 ms at 8 threads  (2026-09-12T04:21+07:00)
T-035's sweep on a quiet machine: at torch_threads 8 the ACT baseline runs act() in 91 ms median and the diffusion_small
configuration (one shared ResNet-18, 120x160, 30 M parameters) in 80 ms at DDIM 10 and 53 ms at DDIM 5; the full 293 M
diffusion policy stays at 454 ms. Both small models fit the 100 ms budget of 5.2 on this laptop, so D-019's Orin NX path
is no longer the only landing; Q-011 stays open as an option, not a blocker. Which model is deployed is a Phase 3 eval
question (R5): both are trained on the same data and compared on held-out cell pairs before anything is chosen. The full
diffusion configuration remains the primary in the sense of 5.7 for training on Greennode; if its on-robot eval is not
better than diffusion_small's by a margin that pays for a 5x slower loop, diffusion_small is deployed.

## D-022  Envelope values for the first motion session are human-approved placeholders, not measurements  (2026-09-12T10:05+07:00)
The pre-flight (T-041) treats every UNMEASURED motion key as FAIL, but the workspace box, joint limits, waist clamp,
velocity limit and first-command step cap are what day 3 (T-024) measures; the actuation latencies are what T-021
measures; they cannot be MEASURED before the session they gate. R3 says the envelope may only be set by a human commit.
Decision: a third status value `HUMAN_APPROVED` for config keys: a human reads the value, agrees it is conservative for a
first session, and commits the status change (the value itself stays as the placeholder Fable proposed). The pre-flight
passes an envelope key at HUMAN_APPROVED for the first motion session and still fails it at UNMEASURED; latency and
pico_to_pelvis keys do not gate T-021 (they gate T-023 and Phase 2 recording instead). Alois: the values to approve are in
config/safety.yaml (all envelope keys) and config/robot.yaml control.kp/kd/weight_ramp_s; say HUMAN: with any change.

## D-023  Loop stop under R4(b) and the audit at the stop  (2026-09-12T11:14+07:00)
Audit (section 8) at this commit: hardware/session.enable git-ignored and absent from history; config/safety.yaml has three
commits (creation T-003, added key T-033, added key T-043), each with zero deletions; no import of tools/ in runtime/,
drivers/, policy/, board/, teleop/, engine/, eval/; no literal joint arrays outside tools/ and tests/; the single motion
test is skipped without a session; Guard is constructed only by the two mock actuators (no real writer exists yet); no
ChannelPublisher, rt/arm_sdk, rt/lowcmd, enableMotor or setJointPositionsAngle call outside NotImplementedError stubs;
four HARDWARE_NEEDED entries with post-checks; no open blocker. Data/Training/Evaluation rows: no real dataset or checkpoint
exists; the mock pipeline (recorder, viewer, dataset, two policies, eval JSON, watchdog) is complete and every success rate
it prints is 0/N by construction (HoldPolicy), as R5 requires.
Stop: every task left in todo (T-021..T-024) needs a human hardware action (H-002..H-004, Q-004, a session), and no further
non-hardware task is productive: the mock-side system is built end to end and further mock work would not change what the
first hardware day needs. R4(b) applies. The loop resumes the moment a HUMAN: line or a completed H-item appears.

## D-024  Loop resumed; one read-only Phase 1 measurement is possible without a human  (2026-09-14T09:10+07:00)
Alois restarted the session ("continue") with no HUMAN: line and no completed H-item: no 192.168.123.x interface, no serial
node, no Logitech id, port 63901 still held by RoboticsService, no data/calib stills, no ~/.config/ludo-g1/env, /home 12 GB.
D-023's stop condition still holds for T-021..T-024. One item D-023 missed: the Orbbec Ego (2bc5:1201) IS plugged in, and
Phase 1's read-only line ("stream every device at target rate for 10 minutes") was never run on it; T-010 ran 10 s only, and
T-018/T-019/T-020 cover arm, hand, palm, glove, pose but not `oblique`. A sensor read needs no session (R1). T-046 runs it.
Decision on T-010's finding ("the Ego overrules the requested resolution; Fable's call"): the capture request in
config/cameras.yaml `oblique.resolution` becomes [1600, 1200] MEASURED, the only mode the device negotiates; `fps` 30 and
`fourcc` MJPG become MEASURED on the 600 s evidence; `device`/`device_right` become the /dev/v4l/by-id paths (serial
AZER76400HV, this lab's unit); `policy_resolution` stays [640, 480] (CLAUDE.md 5.3), so nothing in policy/ or the dataset
changes. Rationale: a placeholder that names a mode the device cannot deliver is a false statement in a config file;
the by-id path is what the driver prefers (docs/drivers.md) and node numbers move. Alternatives rejected: (b) building
pyorbbecsdk for depth (D-009 stands, Q-008 open); (c) stereo depth from the two streams (no consumer). The `top` (Brio)
and `palm` rows stay UNMEASURED: those devices are not attached (H-001, H-003).
After T-046 the loop stops again under R4(b) unless an H-item or HUMAN: line has appeared.

## D-025  Oblique nodes by-path; the Ego's "drops" are delivery jitter, fixed at the timestamp, not the cable  (2026-09-14T13:10+07:00)
Supersedes D-024's wording "device -> /dev/v4l/by-id path": this Ego gives both UVC functions one by-id name, which pointed
at the left node on 2026-09-11 and at the right node on 2026-09-14 (verified by Fable with ls -l). `oblique.device` and
`device_right` are the /dev/v4l/by-path links (interface 1.0 left, 1.2 right), which fail loudly on a port change instead of
swapping cameras. Rule for every camera from now on: a by-id link goes into the config only after `readlink -f` shows it
names the intended node; otherwise by-path.
On the T-046 measurement: frame count over 600 s equals the nominal count (30.000 Hz achieved), so the 222/207 "missed"
frames are not lost frames. Fable's 30 s checks on the same node: run A (12:57, 5-min load 1.8 right after the gate's
pytest, governor powersave) 901 frames, 12 gaps > 50 ms and 11 intervals < 16.7 ms, arrival jitter p99 18 ms; run B
(13:03, host quiet) 901 frames, 0 gaps, arrival jitter p99 1.37 ms. The phenomenon is intermittent host-side delivery
delay, and the V4L2 buffer timestamp (cv2 CAP_PROP_POS_MSEC, CLOCK_MONOTONIC, lags arrival by 2.4 ms p50 / 3.2 ms p99)
carries the sensor-side time with jitter p99 0.68 ms in run B. Decision: the camera driver stamps frames with the kernel
buffer timestamp converted to runtime.clock ns, arrival time only as a fallback, and the recorder therefore aligns on the
time the frame was captured, not the time user space got round to it. This is T-047, non-hardware except its readonly
check. H-005 stays open but is reframed: its post-check runs after T-047 and its pass criterion is on the kernel stamp;
the USB-3 re-seat is still worth one try and costs nothing. T-016's 10 ms p99 skew budget is judged on kernel stamps.
Alternatives rejected: raising the drop threshold (hides the jitter the recorder cares about); a realtime priority for the
grab thread (root, and does not fix a late USB completion); switching the governor from the agents (system state, human).

## D-026  T-047 closes the Ego timing question; loop stops again under R4(b)  (2026-09-14T14:20+07:00)
With kernel buffer stamps the oblique stream over 600 s shows frames_lost 0 and jitter p99 0.81 ms (T-047), well inside
T-016's 10 ms p99 skew budget, on the same USB 2.0 port and powersave governor that produced the 18 ms arrival jitter of
T-046. H-005 is RESOLVED without a human action; a USB-3 re-seat is optional and no longer gates anything. The palm camera
takes the same V4L2Camera path and inherits the kernel stamp when it arrives (H-003). Rule going forward: every camera
statistic quoted for a skew or latency decision is on the kernel stamp; arrival jitter is reported beside it as a host-load
indicator only.
Audit at this commit (section 8, delta since D-023): hardware/session.enable absent and ignored; config/safety.yaml
untouched since T-043 (git log confirms); no import of tools/ from runtime/, drivers/, policy/, teleop/, board/, engine/,
eval/; the one motion test still skipped without a session; config/cameras.yaml changed twice (T-046 measured values, one
descriptive line here), never a safety value; no dataset or checkpoint exists, so the Data/Training/Evaluation rows are
unchanged. Suite: 902 passed 16 skipped through the hook.
Stop: every task left in todo (T-021..T-024) needs a human hardware action (H-002..H-004, Q-004, a session). The only
device attached is the Ego and its Phase 1 read-only line is done twice over. No non-hardware task would change what the
first hardware day needs. R4(b) applies; the loop resumes on a HUMAN: line or a completed H-item.
