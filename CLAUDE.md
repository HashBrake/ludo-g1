# LUDO-G1: Project Brief and Operating Manual for the Agent Pair

Version 1.1, 2026-09-11. Owner: Alois (PT Lab). This file is the single source of truth for the two Claude Code agents working on this project. Read it fully before doing anything. If this file and any other file disagree, this file wins. If you believe this file is wrong, write the disagreement to `agents/DECISIONS.md` (Fable) or `agents/BUILD_LOG.md` (Opus); do not silently deviate.

---

## 1. Mission

Make a Unitree G1 humanoid, mounted on a rig, play a full game of Ludo (Vietnamese cờ cá ngựa variant) against human players using its left arm and a Paxini DexH15 left hand. Every robot motion is produced by a learned manipulation policy trained on teleoperation data collected on this robot. No scripted or hand-coded motion is allowed in the deployed system. A separate game engine (being built by another team) decides which move to make; the policy executes it.

Definition of done: the robot completes a full game against a human, executing every one of its own turns (roll die, move horse, capture, enter horse from base) with a per-primitive success rate above 90% and automatic recovery from the failure cases listed in section 6.5, with a human present and an emergency stop within reach but not used.

---

## 2. Non-negotiable rules

These apply to both agents at all times. Violating one is a project failure regardless of any other progress.

**R1. Hardware motion gate.** A motion command is any message that sets a target position, velocity, torque, or trajectory for any G1 joint (arm, waist, or otherwise) or for any DexH15 actuator. No agent sends a motion command unless the file `hardware/session.enable` exists, is unexpired, and was written by a human (see section 4.6). Agents never create, edit, copy, or restore this file. Reading robot state, cameras, glove, and Pico streams is allowed at any time. Running the SDK in a read-only or state-echo mode is allowed. Simulated robots are not subject to the gate.

**R2. No hard-coded motion.** The deployed system contains no scripted trajectories, waypoint lists, or hand-tuned grasp poses. Scripted motion is permitted only inside files under `tools/hardware_checks/` for bring-up and calibration, and those files are never imported by `policy/` or `runtime/`. Goal conditioning from the game engine, workspace safety limits, and state verification by the engine are not hard-coded motion and are required.

**R3. Safety envelope.** Every path that can produce a motion command passes through `runtime/safety.py`, which enforces a workspace box, joint limits, waist clamps, velocity limits, and a rate limit. Bypassing it is forbidden. The envelope values live in `config/safety.yaml` and may only be loosened by a human commit (Fable may propose, never apply).

**R4. Stop conditions.** The build runs continuously. It stops only when (a) a hard blocker is logged per section 4.7, (b) every remaining task requires a human action on physical hardware and no non-hardware task is productive, or (c) a safety incident occurred (unexpected contact, motion outside the envelope, e-stop pressed). Waiting for a human is not a reason to stop while any non-hardware task remains.

**R5. Learned behavior only, verified behavior always.** No claim of success without a logged measurement. Success rates come from `eval/` runs with recorded trial counts, never from a description.

**R6. File ownership.** Section 4.3 defines which agent may write which files. An agent that needs a change in a file it does not own requests it through the protocol.

---

## 3. Inventory and assumptions

### 3.1 Hardware

| Item | Role in this project | Notes |
|---|---|---|
| Unitree G1 Edu+ Ultimate | The player. Left arm (7 DoF) and waist yaw used. | Mounted on a rig by the hip so the torso is free; legs locked. Head will be replaced by a projector; there is no head camera. |
| Paxini DexH15 (left only) | End effector. | Used in pinch-only mode (section 5.4). Palm camera built in. Controlled through the Paxini SDK, not the Unitree SDK. |
| Paxini PxCap Pro glove (one) | Operator hand input for teleop. | Finger joint angles. Has a jig for the Pico controller, which supplies wrist 6-DoF pose. |
| Pico 4 + self-built teleop pipeline | Wrist tracking for the operator's left hand. | Controller sits in the glove jig. Existing pipeline maps controller pose to G1 arm targets. |
| Logitech Brio 4K | Fixed top-down camera over the table. | Global board state, goal grounding, AprilTag homography. |
| Orbbec Ego camera | Fixed oblique camera on the rig at chest height. | Depth cues, replaces the missing head camera. Not worn by a human in this project. |
| DexH15 palm camera | Close-in view during grasp. | Captured through the Paxini SDK if exposed; discover in Phase 0. |
| Jetson Orin NX 16 GB | Optional inference target if the laptop GPU cannot hold 10 Hz. | Default is to run everything on the laptop first. |
| Qualcomm RB9 | Unused unless a need appears. | Do not spend time on it. |
| Ubuntu laptop (this machine) | Control PC, both agents run here. | Weak GPU. Training runs on Greennode cloud GPUs. |
| Board | 60 x 60 cm, 3D printed, AprilTags in four corners, magnets under every cell, square horses with an arrow on top, physical die in a bowl. | All changeable; propose changes through `agents/DECISIONS.md`. |

### 3.2 Software present in the repo

All SDKs are checked into or vendored under `third_party/` (Alois places them in one folder; Phase 0 discovers the exact layout). Expected: Unitree SDK2 (Python bindings), Paxini DexH15 SDK, Paxini PxCap Pro to DexH15 teleop SDK, Orbbec SDK, the self-built Pico teleop pipeline, and the game engine codebase (incomplete, placeholder interface in this project).

### 3.3 Assumptions (state them, verify them in Phase 0, correct this section if wrong)

Python 3.10 or newer. Unitree SDK2 communicates over DDS on a wired Ethernet link to the G1. The Paxini SDK exposes joint position control for the DexH15 at 30 Hz or better and exposes the palm camera. The PxCap Pro exposes finger joint angles at 30 Hz or better and does not expose absolute wrist position. The Pico pipeline already produces G1 arm joint targets from controller pose through IK. Greennode provides a Linux GPU VM reachable over SSH with rsync available. The game engine will eventually expose a network or Python interface; until then, `engine/stub.py` stands in.

### 3.4 Known unknowns to resolve in Phase 0

End-to-end latency of glove to DexH15 and of controller to G1 wrist. Whether the DexH15 SDK allows partial joint commands (needed for the pinch synergy). Reachable cells with the hip-only mount and waist yaw enabled. Whether the arrow orientation on the horse matters to the game engine (assume no until the engine team says otherwise; record the assumption). Available disk for datasets (target at least 500 GB). Greennode instance type, GPU, and access method.

---

## 4. Two-agent protocol

### 4.1 Roles

**Fable (decider and reviewer).** Owns architecture, task definition, acceptance criteria, review, and all decisions that trade scope, time, or risk. Reads every commit Opus makes, reviews it against the acceptance criteria, and either accepts or returns it with specific findings. Audits data quality, evaluation methodology, and safety compliance. Does not write production code except one-line fixes inside a review when re-routing would waste a cycle, and logs even those.

**Opus (builder).** Owns implementation, tests, tooling, experiments, training runs, and documentation of what was built. Takes tasks from `agents/TASKS.md` in priority order, builds them to the acceptance criteria, runs the verification, commits with the required message format, and logs results in `agents/BUILD_LOG.md`. Does not change scope, architecture, or acceptance criteria; when it disagrees, it logs the disagreement and a proposed alternative, then continues with the task as written unless it is unsafe or impossible.

Both agents are equal on safety: either may halt the loop by writing `agents/BLOCKERS.md`.

### 4.2 Topology: one session, one folder

There is one Claude Code session, running Fable, in `~/ludo-g1/` (a normal git repo, `git init` on first run). Fable never builds; for every task it launches an Opus subagent (the Agent tool with `model: opus`) inside the same working tree, gives it the task, waits for its report, and reviews the result. Communication still goes through the files under `agents/` so that everything survives a restart and Alois can read it. Git commits mark every handoff: Opus commits its work at the end of each task, Fable commits its reviews and decisions.

```
~/ludo-g1/                   the repo; CLAUDE.md, third_party/, code, agents/
~/ludo-g1/data/              datasets, checkpoints, logs (git-ignored)
```

Only one subagent runs at a time unless two tasks touch disjoint files and neither needs hardware; then Fable may run two in parallel.

### 4.3 Communication files (all under `agents/`)

| File | Writer | Reader | Purpose |
|---|---|---|---|
| `DECISIONS.md` | Fable | Opus, human | Append-only log of decisions with date, rationale, alternatives rejected. Supersedes earlier entries explicitly by reference. |
| `TASKS.md` | Fable creates and prioritizes; Opus updates status fields only | both | The task queue. Format in 4.4. |
| `BUILD_LOG.md` | Opus | Fable, human | Append-only log: what was built, how it was verified, measurements, open questions, disagreements. |
| `REVIEW.md` | Fable | Opus | Review verdicts per commit or task: accept, or return with numbered findings. |
| `BLOCKERS.md` | either | both, human | Hard blockers only. Presence of an open blocker halts the loop. |
| `HARDWARE_NEEDED.md` | either | human | Actions a human must perform on physical hardware, with exact steps and the check the agent will run afterwards. |
| `STATE.md` | Fable | all | Current phase, current milestone, next three tasks, last accepted commit. Rewritten, not appended. |
| `QUESTIONS.md` | either | human | Non-blocking questions for Alois, with the assumption the agents are proceeding on in the meantime. |

Prose in these files: short, factual, dated (ISO 8601 with time, Asia/Bangkok). No pleasantries, no restating the brief.

### 4.4 Task format

```
## T-042  Fused teleop recorder: single-clock capture of arm, hand, cameras
status: todo | in_progress | review | returned | accepted | blocked
priority: P0 | P1 | P2
phase: 2
owner: opus
depends_on: T-031, T-035
hardware: none | read-only | motion
deliverables:
  - teleop/recorder.py producing LeRobot v2 episodes under ~/ludo-g1/data/raw/
  - tests/test_recorder.py running against mocked SDKs
acceptance:
  - 60 s mock episode recorded with all streams; timestamp skew between any two streams < 10 ms at p99
  - replaying a recorded mock episode through the mock robot reproduces joint targets within 1e-6
  - CLI documented in docs/teleop.md
notes: (Fable's design guidance, links to DECISIONS entries)
```

Opus edits only `status` and appends a `result:` block with measurements and the commit hash. Fable edits everything else.

### 4.5 The loop

Fable runs this cycle without end, with no idle waiting between steps:

1. Pick the next task: any `returned` task first, then the highest-priority `todo` task whose dependencies are `accepted`, then any non-hardware `todo` task if every remaining P0 task needs a human hardware action. Set its status to `in_progress` and commit.
2. Launch an Opus subagent (`model: opus`) with the prompt in section 4.5.1, containing the full task block and the review findings if it was returned. Wait for it to finish.
3. Review: read every file the subagent changed (`git diff` against the previous commit; read the code, do not trust the report). Re-run every acceptance check that does not need hardware. Write the verdict to `REVIEW.md`: `accepted`, or `returned` with numbered, specific findings (file, line, what is wrong, what would be acceptable). Update the task status. A task may be returned at most three times; on the third return Fable rewrites the task or its design rather than returning it again.
4. Re-plan: if the accepted work changes the picture, append to `DECISIONS.md` and add or reprioritize tasks in `TASKS.md`. Keep at least three unblocked, non-hardware tasks in `todo` at all times so the loop never idles while waiting for a human.
5. Audit: at every phase end, and whenever a dataset or checkpoint is produced, run the audit checklist in section 8.
6. Rewrite `STATE.md`, commit, go to 1.

#### 4.5.1 Opus subagent prompt (Fable fills in the task block)

> You are Opus, the builder for LUDO-G1, working in ~/ludo-g1. Read CLAUDE.md fully before starting; rules R1 to R6 are absolute. Your task is below. Build it to the acceptance criteria exactly. Write tests before or alongside the code; run every acceptance check yourself and record the command and the measured result; never claim a criterion is met without running it. Do not change scope, architecture, acceptance criteria, config/safety.yaml, or anything under third_party/. Never create or edit hardware/session.enable. Never send a motion command unless runtime/safety.py confirms a valid session. If you disagree with the task, log the disagreement and your alternative in agents/BUILD_LOG.md and still do the task as written unless it is unsafe or impossible. If you need a human to do something on the physical hardware, append exact steps and a post-check to agents/HARDWARE_NEEDED.md and finish what you can without it. If you hit a hard blocker after three distinct attempts, append it to agents/BLOCKERS.md in the section 4.7 format. When done: append to agents/BUILD_LOG.md what you changed, the commands you ran, the measured numbers, and anything not meeting the criteria and why; set the task status to review in agents/TASKS.md with a result block and commit hash; commit everything with message [opus][T-nnn] summary. Report back a summary of at most 30 lines.
>
> TASK:
> (full task block from TASKS.md, plus REVIEW.md findings if returned)

Review standards for Fable: a returned task must name concrete defects, not preferences. Accept work that meets the acceptance criteria even if Fable would have written it differently, and log the stylistic note in `DECISIONS.md` as a future guideline instead. Return work that meets the criteria but violates R1 to R6 or the safety envelope, always.

### 4.6 Hardware session gate (implements R1)

`hardware/session.enable` is git-ignored and never committed. A human creates it by running `tools/hardware_checks/enable_session.py`, which prompts for the human's name, the checklist confirmation (e-stop within reach, legs locked, workspace clear, humans out of the arm envelope), and writes:

```
enabled_by: Alois
enabled_at: 2026-09-15T14:02:11+07:00
expires_at: 2026-09-15T16:02:11+07:00
checklist: confirmed
```

`runtime/safety.py` refuses every motion command unless this file exists, parses, is unexpired, and `checklist: confirmed` is present. The default session length is two hours. Agents may ask a human to enable a session through `HARDWARE_NEEDED.md` and may run motion tasks while the file is valid, but must state in `BUILD_LOG.md` for each such run: what will move, the envelope in force, and the observed outcome. A test that would move the robot is marked `@pytest.mark.motion` and is skipped without a valid session.

### 4.7 Blockers and stopping

A hard blocker is something neither agent can resolve after Opus has made three distinct attempts and Fable has proposed one redesign. Examples: an SDK feature that does not exist, a hardware fault, a dependency that cannot be installed, a Greennode quota exhausted. Format in `BLOCKERS.md`:

```
## B-003  DexH15 SDK rejects partial joint commands  (OPEN)
raised_by: opus, 2026-09-16T09:40+07:00
task: T-018
attempts: (1) ... (2) ... (3) ...
fable_redesign: ...
what_we_need_from_human: contact Paxini for firmware flag X, or approve fallback Y (cost: ...)
work_that_can_continue: T-020, T-021, T-025
```

If `work_that_can_continue` is non-empty, both agents continue on those tasks and the blocker stays open; the loop only stops when the list is empty. When a human resolves a blocker they change `(OPEN)` to `(RESOLVED: ...)` and the agents resume.

### 4.8 Human interface

Alois reads `STATE.md` first, then `HARDWARE_NEEDED.md`, `BLOCKERS.md`, `QUESTIONS.md`. He answers by editing those files or `DECISIONS.md` with a line starting `HUMAN:`. A `HUMAN:` line overrides anything an agent wrote.

---

## 5. System architecture

### 5.1 Modules and repo layout

```
ludo-g1/
  agents/                 protocol files (section 4.3)
  config/                 yaml: robot, cameras, safety, board, training
  third_party/            vendored SDKs (never modified in place; wrap, do not patch)
  drivers/                thin, tested wrappers around each SDK
    g1_arm.py             read state, send arm+waist joint targets (through safety)
    dexh15.py             read state, send hand targets (through safety), palm camera
    pxcap.py              glove finger angles stream
    pico.py               controller 6-DoF pose stream (from the existing pipeline)
    cameras.py            Brio, Orbbec: synchronized frame grab with monotonic timestamps
    mock/                 mock of every driver with the same interface, used by all tests
  runtime/
    clock.py              single monotonic clock, stream alignment, latency compensation
    safety.py             envelope, session gate, rate limit (R1, R3)
    controller.py         10 Hz loop: observation -> policy -> safety -> drivers
  teleop/
    retarget.py           glove -> pinch scalar; controller pose -> arm targets (via existing IK)
    recorder.py           episode capture to LeRobot v2
    operator_ui.py        minimal on-screen goal display, episode start/stop/success/perturb keys
  board/
    calibration.py        AprilTag homography, cell centers in Brio pixels and board frame
    perception.py         horse and die detection from Brio (placeholder until engine team delivers)
  engine/
    interface.py          dataclasses and abstract EngineClient (the contract, section 5.5)
    stub.py               scripted test games, deterministic move sequences for eval
  policy/
    dataset.py            loading, goal heatmap rendering, augmentation
    diffusion.py          Diffusion Policy (primary)
    act.py                ACT (baseline)
    train.py              training entry point, local or cloud
    export.py             checkpoint -> inference bundle (TorchScript or ONNX)
  eval/
    protocol.py           trial definitions, held-out cell pairs, success criteria
    run_eval.py           runs N trials on robot or mock, writes eval/results/*.json
  cloud/
    greennode.sh          sync data up, launch training, sync checkpoints down
  tools/hardware_checks/  bring-up scripts (the only place scripted motion is allowed, R2)
  tests/                  pytest; hardware tests marked motion or readonly
  docs/                   one page per module, kept current by Opus
```

### 5.2 Control and data rates

Cameras 30 Hz. Robot and hand state 100 Hz where the SDK allows, subsampled to 30 Hz for the dataset. Policy inference 10 Hz, emitting chunks of 16 actions at 30 Hz, executed with temporal ensembling (ACT) or receding horizon (Diffusion Policy, execute 8 of 16). All streams timestamped from `runtime/clock.py` (monotonic). Latency of each actuation path is measured in Phase 1 and stored in `config/robot.yaml`; the recorder shifts the faster stream so that recorded actions align with the robot's actual response.

### 5.3 Observation and action spaces

Observation: `top` (Brio, 640x480 crop of the board region), `oblique` (Orbbec RGB, 640x480), `palm` (DexH15 palm camera, native resolution downscaled to 320x240), `state` (7 arm joints + 1 waist yaw + 1 pinch scalar, plus 15 raw DexH15 joints stored but not fed to the policy by default), `goal` (two heatmap channels rendered onto the `top` image: source cell and target cell; for roll and recover primitives the relevant channel points to the bowl or the fallen horse), `task_id` (one-hot: move, roll, recover).

Action: 7 arm joint targets + 1 waist yaw target + 1 pinch scalar, absolute, at 30 Hz. The pinch scalar in [0, 1] maps through a fixed synergy (section 5.4) to DexH15 joint targets in `drivers/dexh15.py`.

### 5.4 Pinch synergy

A fixed linear synergy from one scalar to the DexH15 joints: thumb opposition and flexion plus index (and middle if it helps stability) flexion, all other fingers held in a curled, out-of-the-way pose. Defined in `config/hand.yaml` after Phase 1 bench tests on a horse and on the die. During teleop the glove's thumb-index distance drives the same scalar, so the operator's intent and the robot's action live in the same one-dimensional space. Raw glove and hand joints are recorded anyway for future use.

### 5.5 Engine interface contract (placeholder implementation, real engine to follow)

`engine/interface.py`:

```python
from dataclasses import dataclass
from enum import Enum
from typing import Optional

class Primitive(Enum):
    MOVE = "move"        # pick horse at src, place at dst (covers enter-from-base and capture)
    ROLL = "roll"        # pick die from bowl, release above bowl
    RECOVER = "recover"  # re-stand or re-seat the horse at cell

@dataclass
class Cell:
    id: str              # engine's cell identifier, e.g. "R-base-2", "track-17", "R-home-3"
    board_xy_mm: tuple   # center in board frame (AprilTag-defined), mm
    top_px: tuple        # center in Brio pixels, from board/calibration.py

@dataclass
class Command:
    primitive: Primitive
    src: Optional[Cell]
    dst: Optional[Cell]
    horse_id: Optional[str]

@dataclass
class Outcome:
    success: bool
    observed_state_delta: dict   # what perception saw change
    failure_mode: Optional[str]  # "horse_fell", "missed_cell", "grasp_failed", "die_out_of_bowl", ...

class EngineClient:
    def next_command(self) -> Optional[Command]: ...
    def report(self, outcome: Outcome) -> None: ...
    def board_state(self) -> dict: ...
```

`engine/stub.py` implements this with scripted games and randomized move sequences for data collection and evaluation. `runtime/controller.py` runs: `cmd = engine.next_command()`, executes the primitive with the policy until the policy's own termination signal or a 20 s timeout, then `board/perception.py` verifies the expected state change and `engine.report(outcome)`. On failure the stub (and later the real engine) issues a `RECOVER` then re-issues the original command, at most twice. This retry logic is orchestration, allowed under R2.

### 5.6 Dataset format

LeRobot v2 under `~/ludo-g1/data/raw/<session_id>/`. One episode per primitive execution. Metadata per episode: `task_id`, `src_cell`, `dst_cell`, `success` (operator-marked), `perturbed` (a second person disturbed the scene), `operator`, `latency_config_hash`, `safety_config_hash`, `board_config_hash`. A dataset card (`README.md`) per session is generated automatically with stream rates, dropped-frame counts, and skew statistics; Fable audits it.

### 5.7 Policy

Primary: Diffusion Policy (LeRobot implementation as the base; modified to accept goal heatmap channels and task id), ResNet-18 encoders per camera, chunk 16, DDIM 10 steps at inference. Baseline: ACT, same inputs, chunk 32, trained on every dataset the diffusion model is trained on so that comparison is always available. Multi-task: one model over all three primitives. Augmentation: color jitter, small crops, random goal-heatmap blur; never geometric augmentation on `top` because the goal heatmap is in the same frame.

### 5.8 Compute

Local laptop: data collection, tests, mock training on tiny subsets (smoke tests only), inference. Greennode: all real training. `cloud/greennode.sh` handles rsync of `data/raw` up, launching `policy/train.py` inside a pinned Docker image, and rsync of `data/checkpoints` down. Every training run has a config hash and a dataset manifest hash logged in `BUILD_LOG.md`. If inference on the laptop cannot hold 10 Hz with DDIM 10, first reduce to DDIM 5, then fall back to ACT, then move inference to the Orin NX; log the decision.

---

## 6. Phases, tasks, and verification

Every phase ends with a written phase report in `BUILD_LOG.md` (Opus) and a phase audit in `DECISIONS.md` (Fable). Fable creates the concrete `TASKS.md` entries from this section at the start of each phase.

### Phase 0: Discovery and scaffolding (no hardware motion)

Inventory `third_party/`, record versions, Python requirements, and the exact API surface of each SDK in `docs/sdks.md` with code references. Verify the assumptions in 3.3 and correct them. Create the repo layout, `config/*.yaml` with placeholder values clearly marked `UNMEASURED`, mock drivers, `runtime/clock.py`, `runtime/safety.py` with the session gate, CI (`pytest` on every commit through a local pre-commit hook), and `cloud/greennode.sh` tested with a one-minute dummy job. Board calibration from a Brio still image of the real board (read-only hardware, no session needed).

Verify: all tests pass on mocks; `safety.py` rejects a motion command without a session file in a unit test; Greennode dummy job round-trips a file; `docs/sdks.md` exists with at least the state read and target write call for each of G1 arm, waist, DexH15, PxCap, Pico, Brio, Orbbec, palm camera.

### Phase 1: Bring-up and measurements (hardware, with sessions)

Read-only first: stream every device at target rate for 10 minutes and report drop rates and jitter. Then, in a human-enabled session, run `tools/hardware_checks/` scripts to: measure both actuation latencies; map the reachable cells with the hip-only mount and waist yaw enabled (operator drives to each cell; the script records success); define the pinch synergy and test it on a horse and the die (10 grasps each, count holds); confirm the safety envelope stops the arm at the box boundary and that the waist clamps hold.

Verify: latencies recorded in `config/robot.yaml` with the measurement method; reachable-cell map committed in `config/board.yaml` and every cell the game uses is reachable, otherwise a `DECISIONS.md` entry proposes board offset or cell layout changes; pinch holds at least 9 of 10 on both objects; envelope test passes with video evidence path logged.

### Phase 2: Fused teleop and recorder

Build `teleop/retarget.py`, `teleop/recorder.py`, `teleop/operator_ui.py`. Single clock, latency-compensated alignment, LeRobot v2 output, episode keys (start, stop, mark success, mark perturbed), goal display for the operator (the engine stub issues the command; the UI shows source and target cells on the Brio feed).

Verify: mock end-to-end test with skew below 10 ms p99; replay fidelity test; 10 real episodes recorded in a session with zero dropped frames on any stream; dataset card generated; Fable audits the card and five random episodes by viewing frame strips (`tools/dataset_view.py`).

### Phase 3: Pilot collection and first policy

Collect 30 move episodes across 10 cell pairs. Report operator success rate and per-episode duration. If operator success is below 80%, stop collecting and fix the cause (retargeting, synergy, latency, board) before continuing. Then collect to 250 move episodes across all used cell pairs with randomized distractor horses. Train Diffusion Policy and ACT on Greennode. Evaluate on the robot: 20 trials on held-out cell pairs, per `eval/protocol.py`.

Verify: pilot report with numbers; training runs logged with hashes; on-robot eval JSON with at least 70% success for the best model. Below 70%, Fable writes a diagnosis (data, model, or hardware) with the evidence and a corrective task before any further collection.

### Phase 4: Multi-task and recovery

Collect roll (100 episodes) and recover (150 episodes: horse on its side, horse on its back, horse between two cells, horse missed magnet; varied poses). Add perturbations to 20% of new move episodes. Retrain multi-task. Evaluate each primitive separately (20 trials each) and a 20-move scripted sequence from the engine stub with engine-level recovery enabled.

Verify: move above 85%, roll above 90%, recover above 80% on their own; the 20-move sequence completes with at most 3 engine-level retries.

### Phase 5: Game integration and hardening

Integrate the real engine when available (otherwise keep the stub with full-game scripts). Full games with a human opponent. Failure-mode logging per trial. Iterate: targeted collection on the worst failure mode, retrain, re-evaluate. Add a watchdog that halts on a policy that has not made progress for 20 s and reports to the engine.

Verify: definition of done in section 1, measured over at least three complete games.

### 6.5 Failure cases the system must recover from

Horse falls over at source or destination; horse placed between cells or missing the magnet; grasp closes on nothing; grasp on the wrong horse (adjacent cell); die lands outside the bowl (human replaces it; the system asks through the engine and waits); die grasp fails; policy stalls (watchdog). Each has a labeled failure mode string in `Outcome.failure_mode` and a counted entry in eval results.

---

## 7. Engineering standards

Python, type hints, `ruff` clean, `pytest` green before every commit. Every driver has a mock with an identical interface and every test runs on mocks by default. Configuration in yaml under `config/`, never constants in code. Logging through one `structlog` logger with the monotonic timestamp. Long-running processes write heartbeats to `~/ludo-g1/data/logs/`. Commit messages: `[opus][T-nnn] summary` or `[fable] summary`. No commit touches `third_party/` (wrap, do not patch; if a patch is unavoidable, it lives in `patches/` and is applied at setup with the reason logged). No secrets in git; Greennode credentials in `~/.config/ludo-g1/env`, referenced by `cloud/greennode.sh`. Minimum code: no abstractions for single-use code, no configurability that no task asked for. Every measurement that feeds a decision is reproducible from a command that is written next to the number.

---

## 8. Fable's audit checklist (run per phase and per dataset or checkpoint)

Safety: every motion path goes through `runtime/safety.py`; `hardware/session.enable` is git-ignored and absent from history; `config/safety.yaml` unchanged since the last human commit; motion tests are marked and skipped without a session.
Learned-only: `policy/` and `runtime/` import nothing from `tools/hardware_checks/`; no literal joint targets or waypoint lists outside `tools/`.
Data: dataset card numbers plausible (rates, skew, drop counts); five random episodes viewed; goal heatmaps land on the right cells; success labels spot-checked; episode count per cell pair not badly skewed.
Training: config and dataset hashes logged; validation loss curves saved; the ACT baseline was trained on the same data; eval protocol used held-out cell pairs.
Evaluation: trial counts as specified; failure modes labeled; no success rate reported without a JSON result file.
Process: `STATE.md` current; every `HARDWARE_NEEDED.md` entry has exact steps and a post-check; at least three non-hardware tasks in `todo`.

---

## 9. Startup

Human setup: one folder, `~/ludo-g1/`, containing this file named `CLAUDE.md` and the SDK folder named `third_party/`. Nothing else. Fable does the rest on its first cycle: `git init`, `.gitignore` (with `hardware/session.enable`, `data/`, `__pycache__/`, `.venv/`), stripping any nested `.git` directories inside `third_party/` so they are not treated as submodules, `data/` subfolders, the `agents/` files, and the first commit.

Start one terminal in `~/ludo-g1` with Claude Code on the Fable model and permissions skipped, and paste the Fable start prompt. Opus is launched by Fable as subagents; no second terminal is needed. Rule R1 is enforced in code by `runtime/safety.py`, not by the permission mode.

If the session is interrupted, restart it the same way; the Fable start prompt tells it to resume from `agents/STATE.md` when `agents/` already exists.

---

## 10. Glossary

Horse: game piece (square, arrow on top). Cell: a board position with a magnet. Base: the four starting cells per player. Home column: the final stretch. Primitive: one of move, roll, recover. Episode: one teleoperated or autonomous execution of one primitive. Session: a human-enabled window in which motion commands are allowed. Envelope: the workspace box and joint limits in `config/safety.yaml`. Pinch scalar: the single hand action dimension.
