# docs/ index

One page per module. Each page states what the module does, the command or call that exercises it, and
the numbers that were measured for it; `CLAUDE.md` stays the source of truth for scope and rules, and
`agents/DECISIONS.md` for anything that deviates from it.

| Page | One line |
|---|---|
| [setup.md](setup.md) | How to build the project venv (uv, Python 3.10 exactly), what each pinned dependency is for, and the pre-commit gate every commit must pass. |
| [sdks.md](sdks.md) | The Phase 0 SDK inventory: for each of the eight devices, the package, the state-read call, the target-write call and the verdict on the CLAUDE.md 3.3 assumptions, every claim carrying a `path:line` reference. |
| [config.md](config.md) | The six yaml files under `config/`, the `runtime.config` loader, the config hash recorded with every dataset and eval, and the `UNMEASURED` convention for values Phase 1 still has to measure. |
| [clock.md](clock.md) | `runtime/clock.py`: the single monotonic time base, per-stream buffers, nearest-sample alignment at a common instant, latency compensation and the skew statistics the recorder is judged on. |
| [safety.md](safety.md) | `runtime/safety.py`: the human session gate (R1) and the envelope (R3) — joint limits, waist clamp, workspace box on the FK wrist point, velocity and rate limits — and how `enable_session.py` writes the file the gate reads. |
| [drivers.md](drivers.md) | `drivers/`: the only code that touches a device, the protocols in `drivers/interfaces.py`, the mock backend every test runs against, and the real camera backend. |
| [engine.md](engine.md) | `engine/`: the three-dataclass contract with the game engine (CLAUDE.md 5.5), the cell table loaded from `config/board.yaml`, and the scripted stub that stands in until the real engine exists. |
| [board.md](board.md) | `board/calibration.py` (the AprilTag homography from board millimetres to Brio pixels, its reprojection error) and `board/perception.py` (horse and die detection on the top frame, the before/after occupancy rules that yield the section 6.5 failure modes; placeholder thresholds until a real still exists, H-001). |
| [teleop.md](teleop.md) | `teleop/retarget.py`: controller 6-DoF pose to 8 arm/waist joint targets through a mink IK on the G1 MJCF, and glove thumb-index distance to the pinch scalar. |
| [controller.md](controller.md) | `runtime/controller.py`: the 10 Hz engine-to-policy-to-safety-to-drivers cycle, action chunking to 30 Hz, perception verification and the outcome reported back to the engine. |
| [runbook_phase1.md](runbook_phase1.md) | The three first hardware days in order: the read-only day (H-002/H-003/H-004, ten-minute stream statistics), the calibration day (H-001), and the first motion session (Q-004, `enable_session.py`, latency, envelope, hand bench, reachability), each step with its check, its BUILD_LOG entry and its abort criteria. |
| [cloud.md](cloud.md) | `cloud/greennode.sh`: push data and repo to the GPU VM, launch a detached training job with a heartbeat, pull checkpoints back; the local fake transport used until credentials exist. |
| [policy.md](policy.md) | `policy/dataset.py`: the training dataset over recorded sessions, goal-channel rendering, action chunks, the augmentation rules of CLAUDE.md 5.7 and the held-out cell-pair split. |
| [eval.md](eval.md) | `eval/protocol.py` and `eval/run_eval.py`: trial sets, success criteria per primitive, the failure-mode vocabulary of section 6.5 and the result JSON every reported success rate comes from (R5). |

Not a module page: `agents/` holds the process files (task queue, decisions, build log, reviews,
blockers, hardware requests, questions) described in CLAUDE.md section 4.3.
