# TASKS.md (Fable creates and prioritizes; Opus edits status and appends result blocks only)

Conventions for every task: Python 3.10, run everything through `.venv/bin/python`; `ruff check .` clean and
`.venv/bin/python -m pytest -q` green before the commit; commit message `[opus][T-nnn] summary`; one page of docs per module
under docs/. Never touch third_party/ contents, config/safety.yaml (after T-003 creates it), or hardware/session.enable.

## T-001  Repo scaffold, Python environment, pre-commit CI
status: accepted
priority: P0
phase: 0
owner: opus
depends_on: none
hardware: none
deliverables:
  - pyproject.toml (project name ludo_g1, requires-python ==3.10.*, ruff and pytest config, `motion` and `readonly` pytest markers registered)
  - requirements.txt pinned (numpy, pyyaml, structlog, pytest, ruff, opencv-python-headless, scipy; nothing heavier yet) and `.venv/` created with `uv venv --python 3.10` + `uv pip install -r requirements.txt`
  - package skeleton per CLAUDE.md 5.1: drivers/ (with mock/), runtime/, teleop/, board/, engine/, policy/, eval/, cloud/, tools/hardware_checks/, tests/, docs/, config/ — each Python package with an __init__.py; empty modules may be omitted, do not create stubs for files no task asked for
  - .git/hooks/pre-commit (also committed as tools/pre-commit.sh and installed by tools/install_hooks.sh) that runs `ruff check .` and `pytest -q` and aborts the commit on failure
  - tests/conftest.py with a `motion` marker autoskip: any test marked motion is skipped unless runtime.safety reports a valid session (until T-005 lands, skip unconditionally with reason "no session gate yet")
  - docs/setup.md: how to create the venv, run tests, install hooks
acceptance:
  - `.venv/bin/python --version` prints 3.10.x
  - `.venv/bin/ruff check .` exits 0 (third_party/ excluded in pyproject)
  - `.venv/bin/python -m pytest -q` exits 0 with at least one real test (e.g. importing every package)
  - hook proof: a scratch commit containing a file with a ruff error is rejected by the hook; the command and its output are in BUILD_LOG.md; the scratch file is not left in the tree
  - `git status` clean after the commit; `.venv/` and `data/` untracked
notes: Keep it minimal (section 7). Do not add torch, lerobot, mujoco yet; T-002 decides what the SDKs need.
result: (opus, 2026-09-11T18:45+07:00, commit f0723b6)
  - `.venv/bin/python --version` -> Python 3.10.20 (uv-managed CPython; system python3 is 3.10.12). PASS
  - `.venv/bin/ruff check .` -> "All checks passed!", exit 0. PASS
  - `.venv/bin/python -m pytest -q` -> 15 passed, 1 skipped ("no session gate yet"), exit 0. PASS
  - hook proof: ruff-error scratch file rejected (exit 1, HEAD unchanged); failing-test scratch file rejected at
    the pytest stage (exit 1, HEAD unchanged); missing-.venv guard rejected with the uv command. Full output in
    agents/BUILD_LOG.md. Both scratch files removed; `git ls-files | grep -i scratch` empty. PASS
  - `git status --short` empty after the commit; `.venv/` and `data/` untracked (git-ignored). PASS
  - deviation logged in BUILD_LOG: venv interpreter is uv-managed 3.10.20, not /usr/bin/python3 3.10.12.

## T-002  SDK inventory and assumption verification: docs/sdks.md
status: accepted
priority: P0
phase: 0
owner: opus
depends_on: T-001
hardware: read-only
deliverables:
  - docs/sdks.md with one section per device: G1 arm, G1 waist, DexH15, DexH15 palm camera, PxCap Pro, Pico controller pose, Brio, Orbbec. Each section states: package and version, install route, the exact call (module, function or class, file:line inside third_party/ or the installed package) that reads state, the exact call that writes targets (for actuators), message rate, units, joint order, and any partial-command capability
  - For the Pico pipeline (third_party/g1_pico_teleop): identify where left-arm joint references are produced (GMR/mink IK output, before the RL whole-body policy), the input it needs (full skeleton vs controller pose only), and whether it can run with a single controller in a glove jig and no ankle trackers. Cite file:line.
  - A verdict per assumption A1..A7 and unknown U2 from agents/DECISIONS.md D-002: confirmed, refuted (with evidence), or needs hardware
  - tests/test_docs_sdks.py: parses every `path:line` reference in docs/sdks.md and asserts the path exists and the line is within the file (files under the git-ignored PyInstaller _internal/ tree are checked on disk too)
  - Attempt in the venv (network allowed): `uv pip install` of pyorbbecsdk and of unitree_sdk2py from the upstream GitHub repo at a pinned commit, and of the pxdex cp310 wheel from third_party/dexh15_sdk. Record each outcome (success, or exact error) in docs/sdks.md; add successes to requirements.txt pinned; do not run any device connection that could move an actuator
  - Read-only device probe: tools/hardware_checks/list_devices.py that lists V4L2 cameras, /dev/ttyUSB* and /dev/ttyACM*, USB ids, and Ethernet links with 192.168.123.x addresses; its output at time of run goes into BUILD_LOG.md
acceptance:
  - docs/sdks.md has all eight sections with both a state-read and (for actuators) a target-write call reference
  - `.venv/bin/python -m pytest -q tests/test_docs_sdks.py` passes and checks at least 12 references
  - every A1..A7 and U2 has a verdict line
  - list_devices.py runs without a device present and exits 0
notes: Facts Fable found on this laptop (verify, cite, use): (a) pxdex 3.2.1 (DexH15 Python SDK) is already installed in
  ~/.local/lib/python3.10/site-packages for the system interpreter, and PaXini Hand Studio is at /opt/PaXini-Hand-Studio; the
  cp310 wheel is also at third_party/dexh15_sdk/DexH15 SDK/. (b) unitree_sdk2py source trees exist locally at
  ~/meta-quest-teleoperate/unitree_sdk2_python and ~/GR00T-WholeBodyControl/external_dependencies/unitree_sdk2_python; prefer
  installing from one of those (record the commit/version) over network. (c) The G1 MJCF that third_party/g1_pico_teleop's
  config names (assets/robots/unitree_g1/g1_29dof.xml) is not vendored; a downloaded copy is at ~/Teleopit/assets/robots/unitree_g1.
  Report its exact files; do not copy it into the repo yet (T-011 decides). (d) Teleopit drives the G1 through a C++ pybind DDS
  bridge (third_party/g1_pico_teleop/third_party/g1_bridge_sdk/src/g1_bridge.cpp, exports get_state/set_target/...); check whether
  a built g1_bridge_sdk exists under ~/Teleopit. (e) teleopit/inputs/pico4_provider.py exposes PicoControllerSnapshot and
  PicoHandSnapshot separately from the skeleton frame; teleopit/retargeting/core.py RetargetingModule.retarget returns a full
  qpos via GMR (needs a full human skeleton). Joint order in teleopit/constants.py. Read ~/Teleopit only, never modify it.
  This task exists to find out whether A5 is wrong (D-002). If the Teleopit stack cannot give arm targets from a single controller, say so plainly and propose the smallest alternative (e.g. controller pose -> mink IK on the G1 MJCF directly). Do not build the alternative here.
result: (opus, 2026-09-11T20:10+07:00, commit 34f3f88)
  - docs/sdks.md: 8 device sections, each with a state-read call reference and, for the three actuator paths
    (G1 arm, G1 waist, DexH15), a target-write call reference. PASS
  - `.venv/bin/python -m pytest -q tests/test_docs_sdks.py -s` -> 9 passed; the test printed
    "docs/sdks.md: checked 157 path:line references (129 unique)". Requirement was >= 12. PASS
  - A1..A7 and U2 each have a verdict line (docs/sdks.md section 9), asserted by the test. PASS
  - `.venv/bin/python tools/hardware_checks/list_devices.py` -> exit 0 with no DexH15/glove/Brio/robot-LAN
    present (it also enumerated the connected Orbbec Ego). PASS
  - Installs: pxdex 3.2.1 from the vendored cp310 wheel SUCCESS (import + getSDKVersion -> DexHandSDK_3.2.1);
    unitree_sdk2py 1.0.1 from GitHub at f7a5526 SUCCESS (the GR00T tree's 1983e88 is not public, exact error
    recorded); pyorbbecsdk 1.3.2 INSTALLS BUT UNUSABLE (PyPI wheel is a mis-tagged macOS build; uninstalled,
    not in requirements.txt). Successes pinned in requirements.txt; `uv pip install -r requirements.txt
    --dry-run` -> "Would make no changes".
  - A5 REFUTED: the vendored pipeline is Pico body skeleton -> GMR/mink -> 29-DoF qpos -> RL whole-body policy
    and it discards the controller pose (pico4_provider.py:634) that pico_bridge does provide
    (frames.py:137). Smallest alternative proposed (controller pose -> 8-DoF mink IK on the G1 MJCF ->
    rt/arm_sdk), not built. Details in docs/sdks.md section 7 and agents/BUILD_LOG.md.
  - New: H-002, H-003 in HARDWARE_NEEDED.md; Q-008, Q-009 in QUESTIONS.md. No blockers.

## T-003  Config files with UNMEASURED placeholders and a validated loader
status: accepted
priority: P0
phase: 0
owner: opus
depends_on: T-001
hardware: none
deliverables:
  - config/robot.yaml (arm joint names and order for the G1 left arm 7 DoF + waist yaw, joint limits from the G1 MJCF/URDF in third_party if available else UNMEASURED, actuation latencies arm_ms/hand_ms UNMEASURED, DDS interface name, IPs from third_party/g1_pico_teleop README)
  - config/cameras.yaml (brio, orbbec, palm: device selector, resolution, fps, crop for `top`; UNMEASURED where unknown)
  - config/safety.yaml (workspace box xyz min/max in the robot base frame, per-joint limits, waist yaw clamp, joint velocity limit, command rate limit Hz, session default length 7200 s; conservative placeholder values clearly marked UNMEASURED with a comment "tighten or loosen only by human commit, R3")
  - config/board.yaml (board 600x600 mm, AprilTag family and ids per corner UNMEASURED, tag size mm UNMEASURED, cell list: id, board_xy_mm for the cờ cá ngựa layout: 4 colors x 4 base cells, a 48-cell track, 4 x 6 home cells; a comment that the engine team owns the true topology and ids)
  - config/hand.yaml (DexH15 joint names and order, pinch synergy matrix placeholder UNMEASURED, curled pose for idle fingers UNMEASURED)
  - config/training.yaml (chunk sizes, rates, image sizes per CLAUDE.md 5.2/5.3/5.7)
  - runtime/config.py: `load(name) -> dict` with schema validation (required keys per file), `config_hash(name) -> str` (sha256 of the canonical yaml, stable across key order), `unmeasured(name) -> list[str]` listing every leaf tagged UNMEASURED (tag convention: the literal string "UNMEASURED" as the value, or a sibling key `<key>_status: UNMEASURED` for numeric placeholders)
  - tests/test_config.py
  - docs/config.md documenting the tag convention and each file
acceptance:
  - all six files load through runtime.config.load without error
  - `config_hash` is deterministic across two loads and changes when any value changes (test)
  - `unmeasured("safety")` is non-empty and `unmeasured("training")` is empty (test)
  - a missing required key raises a clear error naming file and key (test)
notes: Facts from docs/sdks.md (T-002) to encode: G1 joint indices from unitree_sdk2py's G1JointIndex (waist yaw 12; left
  shoulder pitch/roll/yaw 15/16/17, left elbow 18, left wrist roll/pitch/yaw 19/20/21; verify against the installed package and
  cite); arm_sdk publish rate 50 Hz; robot LAN laptop 192.168.123.2, robot 192.168.123.164, DDS interface name UNMEASURED
  (candidates enp0s31f6 or the AX88179 dongle); DexH15: 7 motors driving 15 joints, Modbus serial, baud 4000000, slave
  address 0x78 (from the SDK example), joint names/order from pxdex.dh15 stubs; Orbbec Ego is UVC stereo (/dev/video4 left,
  /dev/video6 right), Brio UNMEASURED; G1 joint limits: take them from the G1 MJCF at ~/Teleopit/assets/robots/unitree_g1/
  g1_29dof.xml (read-only reference, T-012 vendors it) and cite the file. Placeholder envelope must be conservative: workspace box no larger than the table region in front of the robot, velocity limit low. Fable will review the numbers. Section 7: config in yaml, never constants in code.
result: (opus, 2026-09-11T21:40+07:00, commit dbd756b)
  - All six files load: `test_every_config_loads` over NAMES = (board, cameras, hand, robot, safety, training). PASS
  - `config_hash` deterministic across two loads (all six, 64 hex chars), unchanged when key order or comments
    change (unsorted / sorted / top level reversed -> one hash), changed when a value changes
    (diffusion.chunk 16 -> 17). PASS. Hashes at this commit: board a05d0595, cameras 35cae829, hand 5b615a57,
    robot 1ae6aa90, safety 6dc24062, training e5cde12f (first 8 of the sha256; full values in BUILD_LOG).
  - `unmeasured("safety")` = 10 entries (workspace_box_m.min/.max/.margin_m, joint_limits_rad,
    waist_yaw_clamp_rad, joint_velocity_limit_rad_s, command_rate_limit_hz, command_gap_reset_s,
    watchdog_timeout_s, hand.pinch_rate_limit_per_s); `unmeasured("training")` = []. PASS.
    Other counts: board 15, cameras 15, hand 12, robot 13.
  - Missing required key: error is `config/safety.yaml: missing required key: 'workspace_box_m.max'`.
    `test_each_required_key_is_individually_enforced` deletes each of the 94 required paths across the six
    files in turn and asserts every deletion fails the load. PASS
  - Gate: `.venv/bin/ruff check .` -> All checks passed; `.venv/bin/python -m pytest -q` -> 103 passed,
    1 skipped (the pre-existing motion-marker autoskip). tests/test_config.py contributes 70 tests.
  - safety.yaml numbers (all UNMEASURED, R3): box x 0.15..0.65, y -0.10..0.60, z -0.40..0.30 m in the pelvis
    frame at the wrist point with 2 cm margin; per-joint limits = MJCF range tightened 5 deg each side (test
    asserts that relation against robot.yaml); waist yaw clamp 0.6 rad; velocity 1.5 rad/s; command rate
    60 Hz; gap reset 0.5 s; watchdog 1.0 s; session 7200 s default, 28800 s cap.
  - Deviation: the topology keys are nested under `layout:` so that `layout_status` annotates an existing
    key (the loader rejects a `_status` with no sibling). 48 track + 6 home cells is not achievable with
    standard 52-cell Ludo ring geometry; the arm-tip cell is given to the home lane instead. Both recorded
    in BUILD_LOG. No hardware touched, no motion command, no blockers, no new questions.

## T-004  runtime/clock.py: monotonic clock, stream alignment, latency compensation
status: accepted
priority: P0
phase: 0
owner: opus
depends_on: T-001
hardware: none
deliverables:
  - runtime/clock.py: `now_ns()` (time.monotonic_ns based, one process-wide origin), `Stamped(ts_ns, payload)`, `StreamBuffer(name, maxlen)` with `push`, `latest`, `nearest(ts_ns)`, `align(streams, ts_ns, tolerance_ns)` returning one sample per stream or raising, `skew_stats(streams)` returning p50/p99 of pairwise skew, `shift(stream, delta_ns)` for latency compensation
  - a structlog logger factory `runtime/log.py` that stamps every event with now_ns (used by all later modules)
  - tests/test_clock.py using synthetic streams at 30 Hz and 100 Hz with jitter
  - docs/clock.md
acceptance:
  - synthetic test: two streams at 30 Hz and 100 Hz with 2 ms gaussian jitter, aligned at 30 Hz for 60 s; skew p99 < 10 ms (test asserts and prints the number)
  - `shift` by a known delta then `align` recovers the original pairing (test)
  - `now_ns` is monotonic across 10000 calls (test)
notes: Keep it small; no threads here. Latency values come from config/robot.yaml later (T-003); clock only provides the mechanism.
result: (opus, 2026-09-11T19:20+07:00, commit 0f77f0d; branch wt/t004)
  - acceptance 1, `.venv/bin/python -m pytest -q tests/test_clock.py -s`:
    "skew over 1800 aligned frames (60 s @ 30 Hz, seed 20260911): p50 = 2.982 ms, p99 = 6.701 ms, max = 7.799 ms".
    30 Hz + 100 Hz streams, 2 ms gaussian jitter, 60 s, aligned on the nominal 30 Hz grid with a 10 ms
    tolerance (every frame also passed `align`). p99 6.701 ms < 10 ms. PASS
  - acceptance 2, `shift` then `align`: a 100 Hz stream delayed by 37 ms mis-pairs >90% of the 151 camera
    frames; after `shift(-37 ms)` all 151 frames re-pair to the original sample and timestamps match. PASS
  - acceptance 3, `now_ns` monotonic across 10000 consecutive calls, strictly advancing overall. PASS
  - gate: `.venv/bin/ruff check .` exit 0; `.venv/bin/python -m pytest -q` -> 24 passed, 1 skipped
    (pre-existing motion autoskip), exit 0.
  - skew definition (max over streams of |sample_ts - target_ts| per instant) implemented and documented in
    docs/clock.md. `skew_stats` takes optional `instants`/`tolerance_ns` beyond the deliverable signature;
    noted in BUILD_LOG.

## T-005  runtime/safety.py: envelope, session gate, rate limit; enable_session.py
status: accepted
priority: P0
phase: 0
owner: opus
depends_on: T-003, T-004
hardware: none
deliverables:
  - runtime/safety.py: `SessionGate(path=hardware/session.enable)` with `status() -> SessionStatus(valid: bool, reason: str, enabled_by, expires_at)`; `Envelope.from_config()` reading config/safety.yaml; `Envelope.check(cmd: MotionCommand, state: RobotState, now_ns) -> MotionCommand` that clamps joint targets to limits, clamps waist yaw, rejects (raises SafetyViolation) on joint velocity above limit, on end-effector position outside the workspace box (via an injected `fk: Callable[[np.ndarray], np.ndarray]`; a mock fk in tests; the real one is T-011), and on command rate above the limit; `Guard(gate, envelope, simulated: bool)` with `admit(cmd, state)` that applies Envelope.check always and SessionGate only when simulated is False
  - MotionCommand and RobotState dataclasses in runtime/types.py (7 arm + 1 waist + 1 pinch, per CLAUDE.md 5.3)
  - tools/hardware_checks/enable_session.py: interactive only (exits 2 with a message if stdin is not a TTY), prompts for name and the four checklist items (e-stop within reach, legs locked, workspace clear, humans out of the arm envelope), refuses unless every answer is yes, writes hardware/session.enable in the exact 4.6 format with Asia/Bangkok offsets and the default length from config/safety.yaml
  - tests/conftest.py updated: the motion marker autoskip now asks SessionGate.status()
  - tests/test_safety.py, docs/safety.md
acceptance:
  - test: Guard(simulated=False).admit raises without the session file; with an expired file; with a file lacking `checklist: confirmed`; with an unparsable file; accepts with a valid file written by the test into a tmp path (never into hardware/)
  - test: Guard(simulated=True).admit still rejects an out-of-box fk position and clamps out-of-limit joints
  - test: rate limit rejects the 2nd command within 1/rate_hz seconds and accepts after
  - test: velocity limit rejects a target that implies > limit rad/s relative to state
  - test: `enable_session.py` run with stdin redirected from /dev/null exits 2 and creates no file
  - the string "hardware/session.enable" is written by exactly one module (safety.py reads it, enable_session.py writes it): `grep -rn "session.enable" --include=*.py . | grep -v third_party` shows only those two files plus tests
notes: R1 and R3 live here. Do not add any bypass flag, environment variable, or "dev mode" that skips the gate for hardware. `simulated=True` is only set by drivers/mock. Fable will grep for bypasses.

result: (opus, 2026-09-11T21:55+07:00, commit 597b4ea)
  - acceptance 1 (session gate), `.venv/bin/python -m pytest -q tests/test_safety.py`: Guard(simulated=False)
    raises SafetyViolation(rule="session_gate") for a missing, expired, `checklist: pending` and unparsable
    file (4 parametrized cases, `guard.admitted == 0`), and admits with a valid file written into tmp_path.
    A fifth test replaces a live file with an expired one mid-run: the next admit raises. PASS
  - acceptance 2 (simulated): admits with no session file anywhere, still raises `workspace_box` for an
    out-of-box mock-fk point, still clamps left_elbow_joint 99.0 -> 2.0071 rad and reports
    clamped == ("left_elbow_joint",); session_status().valid stays False. PASS
  - acceptance 3 (rate limit): 60 Hz -> period 16 666 666 ns; a 2nd command at period-1 ns raises
    `command_rate`, one at exactly the period is accepted. PASS
  - acceptance 4 (velocity limit): fresh-reference allowance 1.5 rad/s x 0.5 s = 0.75 rad; 0.675 rad from
    the measured state accepted, 0.825 rad rejected with rule `joint_velocity`. Previous-command reference
    and the gap-reset fallback tested too. PASS
  - acceptance 5: `.venv/bin/python tools/hardware_checks/enable_session.py < /dev/null` -> exit 2,
    "stdin is not a terminal", hardware/ unchanged (test compares an existence+mtime snapshot). PASS
  - acceptance 6: `grep -rn "session.enable" --include=*.py . | grep -v third_party` -> runtime/safety.py,
    tools/hardware_checks/enable_session.py, and tests only (test_safety, test_scaffold, test_config).
    Regression-guarded by test_only_safety_and_enable_session_name_the_session_file. PASS
  - gate: `.venv/bin/ruff check .` exit 0; `.venv/bin/python -m pytest -q` -> 163 passed, 1 skipped
    (the motion autoskip, now carrying SessionGate's own reason), exit 0. 58 of those are tests/test_safety.py.
  - design choices for review, detailed in BUILD_LOG: the fresh velocity reference is the measured state
    aged by command_gap_reset_s; pinch range and slew clamp rather than reject; an Envelope without fk
    fails closed; from_config additionally refuses a safety limit wider than the config/robot.yaml
    mechanical range and a joint list that disagrees with config/robot.yaml.

## T-006  Mock drivers with the real driver interfaces
status: accepted
priority: P0
phase: 0
owner: opus
depends_on: T-004, T-005
hardware: none
deliverables:
  - drivers/interfaces.py: typing.Protocol for ArmDriver (read_state() -> RobotState stamped; send_targets(cmd) via a Guard), HandDriver (read_state, send_pinch(scalar) -> expands through the synergy in config/hand.yaml, palm_frame()), GloveDriver (read() -> 15/17 finger angles + pinch scalar), PoseDriver (read() -> wrist 6-DoF pose stamped), CameraDriver (grab() -> frame stamped, `top` and `oblique`), all timestamps from runtime.clock
  - drivers/mock/{g1_arm,dexh15,pxcap,pico,cameras}.py: deterministic synthetic streams at the configured rates (config/robot.yaml, cameras.yaml) driven by an injectable clock so tests run faster than real time; the mock arm integrates commanded targets with a first-order lag (time constant from config/robot.yaml, UNMEASURED placeholder) so latency tests in Phase 2 have something to measure; every mock actuator constructs its Guard with simulated=True and still calls Guard.admit
  - drivers/__init__.py factory `make(name, backend="mock"|"real")` reading config; "real" raises NotImplementedError until the real drivers exist
  - tests/test_mock_drivers.py, docs/drivers.md
acceptance:
  - test: 10 s of mock arm state at 100 Hz yields 1000 +/- 1 samples with monotonic timestamps
  - test: sending an out-of-envelope target to the mock arm raises SafetyViolation (proves the mock path goes through the Guard)
  - test: mock camera `top` frames are 640x480x3 uint8 and `palm` 320x240x3
  - test: the mock hand maps pinch 0.0 and 1.0 to two distinct 15-joint vectors from config/hand.yaml
  - `grep -rn "Guard(" drivers/ | grep -v mock` is empty (only mocks construct simulated=True; real drivers will use simulated=False)
notes: Interfaces are the contract for the real drivers in Phase 1; keep them minimal, no features nobody asked for.
result: (opus, 2026-09-11T20:10+07:00, commit e0ca032)
  - `.venv/bin/python -m pytest tests/test_mock_drivers.py -q` -> 51 passed in 1.4 s. Full suite
    `.venv/bin/python -m pytest -q` -> 276 passed, 1 skipped (225 before). `.venv/bin/ruff check .` ->
    "All checks passed!". PASS
  - 10 s of mock arm state at 100 Hz -> exactly 1000 samples, every period exactly 10 000 000 ns,
    timestamps strictly increasing (test_ten_seconds_of_arm_state_at_100_hz_is_1000_monotonic_samples).
    Wall time of those 10 s of stream, driver construction included: 41.7 ms. PASS
  - Out-of-envelope target raises SafetyViolation through the mock's own Guard: 2.5 rad step ->
    rule=joint_velocity; slow ramp (1.0 rad/s, inside the velocity limit) -> rule=workspace_box at
    -0.76 rad; NaN -> rule=non_finite; a refused command leaves the simulated arm unmoved. PASS
  - Camera frames: top (480, 640, 3) uint8, oblique (480, 640, 3) uint8, palm (240, 320, 3) uint8, each
    equal to its config/cameras.yaml policy_resolution; frame counter round-trips to 2**32-1. PASS
  - Pinch 0.0 and 1.0 -> the two 15-joint vectors of config/hand.yaml (new `mock.open_pose` /
    `mock.closed_pose`), 9 of 15 joints differing (the 6 idle ring/pinky joints are identical by
    design), L1 13.85 rad; synergy(0.5) is their midpoint. PASS
  - `grep -rn "Guard(" drivers/ | grep -v mock` -> empty (exit 1); the same grep for
    `Guard.from_config(` is also empty outside drivers/mock/. Both asserted by a test. PASS
  - Config: added `mock:` blocks to config/robot.yaml (arm_tau_s + status, state_hz, pose_hz + status,
    pose_cycle_s, pose_radius_m) and config/hand.yaml (open_pose/closed_pose + status, glove_cycle_s,
    glove_angle_amplitude_deg), per docs/config.md. The real pinch.open_pose/closed_pose stay the
    literal UNMEASURED. Hashes: robot 1ae6aa90 -> 9dc5e64a, hand 5b615a57 -> 6f1507d5.
    config/safety.yaml and runtime/config.py REQUIRED_KEYS untouched. Rationale in BUILD_LOG.
  - Pre-existing flake reported in BUILD_LOG (tests/test_safety.py session-expiry test, ~1 in 6 runs,
    wall-clock second boundary); not mine to edit under this task.

## T-007  Engine contract and scripted stub engine
status: accepted
priority: P0
phase: 0
owner: opus
depends_on: T-003
hardware: none
deliverables:
  - engine/interface.py exactly as CLAUDE.md 5.5 (Primitive, Cell, Command, Outcome, EngineClient) with type hints; Cell instances built from config/board.yaml by `engine/cells.py: load_cells() -> dict[str, Cell]` (top_px filled from board calibration when available, else None-safe placeholder)
  - engine/stub.py: StubEngine(seed, script=None) implementing EngineClient; `script` is a list of Commands for deterministic eval sequences; without a script it generates a legal-looking random game of MOVE, ROLL and enter-from-base commands over the board cells with a simple internal board state; on `report(Outcome(success=False))` it issues RECOVER for the affected cell then re-issues the original command, at most twice, then marks the turn failed and moves on; `board_state()` returns horse positions
  - engine/scripts/: at least `eval_20_moves.yaml` (20 MOVE commands across 10 distinct cell pairs) loadable by StubEngine
  - tests/test_engine_stub.py, docs/engine.md
acceptance:
  - test: same seed -> identical command sequence over 200 commands
  - test: after a failed report, the next command is RECOVER at the failing cell, then the original command again; after two failures the third next_command is a different turn
  - test: every Command's src/dst are cells that exist in config/board.yaml
  - test: eval_20_moves.yaml loads and yields exactly 20 MOVE commands with >= 10 distinct (src, dst) pairs
notes: This is orchestration, allowed under R2 (CLAUDE.md 5.5). The real engine will replace stub.py; keep interface.py untouched by the stub's internals.
result: (opus, 2026-09-11T22:55+07:00, commit f22ace8, branch wt/t007)
  - engine/interface.py is CLAUDE.md 5.5 field for field; added only type hints, docstrings, frozen=True and ABC (5.1). Optional[X] spelled X | None (ruff UP045).
  - engine/cells.py: load_cells() -> 88 Cells from config/board.yaml (48 track + 4 x (6 home + 4 base)); top_px None unless a calibration mapping is passed; load_layout() reads the topology.
  - engine/stub.py 297 lines. Random mode: 4 colours x 4 horses, only the robot's colour emits commands, ROLL then MOVE, enter-from-base on a 1 or a 6 (documented choice), capture emitted as two MOVEs (captured horse out first). Script mode hands out the script then None.
  - A1 same seed -> identical 200 commands: `len(a)=200  a==b: True  a!=c: True` (seed 7 twice vs seed 8). test_same_seed_gives_an_identical_sequence_of_200_commands.
  - A2 recover then re-issue, third next_command is a different turn: test_two_failed_reissues_give_the_turn_up_and_the_game_moves_on asserts RECOVER at the failing cell, the original again, then a new turn's ROLL; failures[0] = attempts 3, failure_modes x3.
  - A3 every src/dst exists in config/board.yaml: 0 unknown cells over 1000 commands (seed 7) and over 300 commands with a forced failure every 7th (so RECOVERs are covered). Mix over 1000: roll 491 / move 509, 60 enter-from-base, 42 capture-clears, 23 into-home.
  - A4 eval_20_moves.yaml: 20 commands, all MOVE, 10 distinct (src, dst) pairs, next_command() None after.
  - Gate: `.venv/bin/ruff check .` clean; `.venv/bin/python -m pytest -q` -> 135 passed, 1 skipped (30 new tests).
  - Two design calls for review (detail in BUILD_LOG): (a) ROLL carries src=dst=None because no bowl cell exists in config/board.yaml and die.bowl_centre_mm is UNMEASURED -- optional bowl_cell= argument takes a measured one; (b) random mode deals a fresh board when a colour is home, since a finished board otherwise emits only ROLLs forever (measured: 888 roll / 112 move before, 491 / 509 after).
  - Not mine, reported: tests/test_greennode_local.py::test_greennode_local_round_trip flakes ~1 in 6 full-suite runs on a `state=starting` vs `running` race in T-009's shell test.

## T-008  Board calibration from AprilTags and a Brio still
status: accepted
priority: P1
phase: 0
owner: opus
depends_on: T-003
hardware: read-only
deliverables:
  - board/calibration.py: detect the four corner AprilTags (OpenCV aruco with the AprilTag 36h11 dictionary, or pupil-apriltags if installable; record which), compute the image->board homography in mm, `cell_px(cell_id)` for every cell in config/board.yaml, write config/board_calib.yaml (homography, tag ids, reprojection error, image path, timestamp); CLI `python -m board.calibration --image PATH`
  - tools/hardware_checks/brio_still.py: read-only capture of one still from the Brio at 4K to a path (uses drivers/cameras real backend if T-010 landed, else OpenCV VideoCapture directly; this is a tool, so allowed)
  - tests/test_calibration.py using a synthetic board image: render four AprilTags at known board-frame positions, warp with a known homography, run calibration, compare
  - docs/board.md
acceptance:
  - synthetic test: recovered cell centers within 1.0 px of ground truth for all cells; reprojection error < 0.5 px
  - synthetic test with 15 degrees of in-plane rotation and a mild perspective tilt passes the same bound
  - CLI on a real still (data/calib/board_empty.png, H-001) prints four tag ids and the error; if the still is not available yet, say so in BUILD_LOG.md and leave H-001 open; the synthetic tests are the acceptance for this cycle
notes: `top` observation crop and the goal heatmaps depend on this frame; never apply geometric augmentation to it later (5.7).
result: (opus, 2026-09-11T20:05+07:00, commit 390cd84)
  - Detector recorded: OpenCV `cv2.aruco` + `getPredefinedDictionary(DICT_APRILTAG_36h11)` with
    CORNER_REFINE_SUBPIX. pupil-apriltags NOT used and NOT added: opencv-python 5.0.0 already ships the
    36h11 dictionary (bytesList.shape == (587, 5, 4)). No dependency added.
  - Acceptance 1, `test_synthetic_board_recovers_every_cell_centre[translation]`: reprojection rms
    0.1202 px (bound < 0.5), worst of all 88 cell centres 0.0079 px, mean 0.0046 px (bound < 1.0). PASS
  - Acceptance 2, `[rot15_tilt]` (15 deg in-plane + projective tilt ~7% across, image 1737x1716):
    rms 0.2312 px, max corner 0.3206 px, worst cell 0.0541 px, mean 0.0313 px. Same bounds. PASS
  - Acceptance 3: NOT MET, no hardware. `tools/hardware_checks/list_devices.py` shows no Logitech
    (046d) device -- four SunplusIT webcam nodes and two Orbbec Ego nodes only -- and
    data/calib/board_empty.png does not exist. H-001 left OPEN and unedited; its post-check command is
    exactly the delivered CLI, so nothing in it changed. The CLI was exercised on a synthetic still
    instead: printed the four tag ids, `rms 0.231 px, max 0.321 px (16 tag corners)`, the board bbox,
    a WARNING naming the placeholder tag geometry, exit 0. No config/board_calib.yaml is committed
    (the synthetic ones went to the scratchpad; `ls config/` is unchanged at six files).
  - `.venv/bin/ruff check .` -> All checks passed!  `.venv/bin/python -m pytest -q` -> 246 passed,
    1 skipped (225 passed before T-008; +21 in tests/test_calibration.py).
  - Deviation logged in BUILD_LOG.md: `config/board.yaml` `apriltags` block edited (outside the file
    list I was given) because the task's design guidance required adding `tag_inset_mm` there and the
    block held no family/size/ids to detect with. All Form-2 placeholders (value + `_status:
    UNMEASURED`): tag36h11, 40.0 mm, ids 0..3, tag_inset_mm 10.0. `unmeasured("board")` still reports
    all five and both the CLI and the written yaml name them. config/safety.yaml untouched.
  - Found by measurement: the first synthetic run passed at 0.715 px/cell but with a constant
    sqrt(0.5) = 0.707 px bias from a numpy-index vs OpenCV-pixel-centre half pixel in my ground truth
    (not in the code). Fixed in the test, which now also guards at 0.25 px so such a bias cannot hide
    under the 1.0 px bound again.

## T-009  cloud/greennode.sh with a local fake transport
status: accepted
priority: P1
phase: 0
owner: opus
depends_on: T-001
hardware: none
deliverables:
  - cloud/greennode.sh: subcommands `up` (rsync data/raw and the repo minus third_party to the remote), `train CONFIG` (launch policy/train.py inside a pinned Docker image via ssh, nohup, heartbeat to data/logs), `down` (rsync data/checkpoints back), `status`; reads ~/.config/ludo-g1/env; a `GREENNODE_TRANSPORT=local` mode that replaces ssh/rsync with local cp into a directory so the whole flow can be exercised without credentials
  - cloud/Dockerfile pinned (python 3.10, torch CPU wheel is fine for the dummy job; GPU image tag recorded as a TODO for Phase 3)
  - cloud/dummy_job.py: writes a file with the hostname and a timestamp after 60 s
  - tests/test_greennode_local.sh (bash, run by pytest via subprocess) exercising up -> train dummy -> down in local mode
  - docs/cloud.md
acceptance:
  - local-mode round trip: `GREENNODE_TRANSPORT=local cloud/greennode.sh up && ... train cloud/dummy_job.py && ... down` produces data/checkpoints/dummy/result.txt (test)
  - the script refuses to run in remote mode when ~/.config/ludo-g1/env is missing, with a message pointing to QUESTIONS.md Q-001
  - no credential strings in git (`git grep -i -E "password|secret|token" cloud/` empty)
notes: The real one-minute dummy job round trip is the Phase 0 exit check and waits for Q-001. Record the command in docs/cloud.md so it can be run the moment credentials exist.
result: (opus, 2026-09-11T19:12+07:00, commit 9e6d10f; branch wt/t009)
  - acceptance 1 (local-mode round trip), `bash tests/test_greennode_local.sh` -> exit 0, 18/18 checks.
    The chain is the literal acceptance form, no extra flags: `GREENNODE_TRANSPORT=local cloud/greennode.sh up`
    -> `... train cloud/dummy_job.py --seconds 1 --note ...` -> `... down`, producing
    data/checkpoints/dummy/result.txt with `hostname: aloisThinkpad`, `python: 3.10.20 (.venv/bin/python)`,
    `cwd: /tmp/ludo-t009-*/remote` (the job ran from the pushed copy, not from the repo). Heartbeat mirrored to
    data/logs/greennode/: "job=t009-roundtrip pid=... state=finished exit=0". `up` pushed 45 repo files with
    third_party/ absent from the fake remote; `train --detach` returned in 0 s against a 5 s job and `status`
    showed state=running. PASS
  - acceptance 2 (remote mode refuses without credentials): same script, checks 1-4. With HOME and
    GREENNODE_ENV_FILE pointed at a temp dir, `GREENNODE_TRANSPORT=remote cloud/greennode.sh up` exits 1 and
    names the missing file plus "agents/QUESTIONS.md Q-001"; the test also asserts the run did not create that
    file. The real ~/.config/ludo-g1/env is never read or written by any test. PASS
  - acceptance 3 (no credential strings): `git grep --untracked -i -n -E "password|secret|token" -- cloud/`
    -> no output, exit 1 (no matches). Asserted by tests/test_greennode_local.py::test_no_credential_strings_in_cloud.
    PASS
  - gate: `.venv/bin/ruff check .` exit 0; `.venv/bin/python -m pytest -q` -> 35 passed, 1 skipped
    (pre-existing motion autoskip), exit 0.
  - NOT verified, and cannot be here: the remote transport (ssh/rsync) and cloud/Dockerfile. No credentials
    (Q-001) and docker is not installed on this laptop, so docker build/run never executed. docs/cloud.md
    carries the exact 60 s Phase 0 exit-check command to run the moment the credentials file exists.
  - deviation: `train` waits for the job by default (`--detach` to fire and forget) because the acceptance
    chains `up && train && down`, which would otherwise race. The job is still launched with nohup+setsid, so
    it survives the ssh connection dropping.

## T-010  Real camera driver (Brio, Orbbec) read-only with device discovery
status: accepted
priority: P1
phase: 0
owner: opus
depends_on: T-006
hardware: read-only
depends_notes: needs T-002's finding on the Orbbec route
deliverables:
  - drivers/cameras.py real backend: Brio via OpenCV V4L2 by device selector from config/cameras.yaml (by-id path preferred over index), Orbbec via pyorbbecsdk if T-002 installed it else a clear NotImplementedError naming the missing package; frames stamped with runtime.clock at grab time; a `probe()` that returns actual resolution and fps
  - tools/hardware_checks/stream_stats.py: streams a camera for N seconds and reports achieved fps, dropped frames, timestamp jitter p50/p99 (the Phase 1 read-only check tool)
  - tests marked `readonly` that skip when the device is absent
  - docs/drivers.md updated
acceptance:
  - with no camera attached: `pytest -q` passes (readonly tests skipped with a reason naming the device)
  - `stream_stats.py --backend mock --seconds 5` reports 30 Hz +/- 1 and 0 drops (test)
  - if a Brio is attached at run time: 10 s of real stats recorded in BUILD_LOG.md
notes: Read-only; no session needed. Do not touch the palm camera here (it comes with the DexH15 driver in Phase 1).
result: (opus, 2026-09-11T23:55+07:00, commit ed4ab4d)
  - Acceptance 1, no camera attached: whole suite re-run with drivers.cameras.list_video_nodes stubbed to []
    (throwaway pytest plugin in the scratchpad) -> 318 passed, 9 skipped, 0 failed. Every camera skip is a
    `readonly` test and names the device or the config key, e.g. "no real oblique camera: ... no VIDEO_CAPTURE
    node with usb id 2bc5:1201 (config/cameras.yaml oblique.usb_id)". PASS
  - Acceptance 2: `.venv/bin/python tools/hardware_checks/stream_stats.py --backend mock --seconds 5` ->
    150 frames in 4.97 s, fps 30.00, drops 0, jitter p50/p99 0.00/0.00 ms. Asserted in
    test_stream_stats_on_the_mock_reports_30_hz_and_no_drops (subprocess + --json, |fps-30| <= 1, drops == 0). PASS
  - Acceptance 3: NOT MET, no Brio attached (list_devices.py: only 174f:11b4 laptop webcam and 2bc5:1201 Ego;
    no Logitech id). Unchanged since T-002; H-001 still stands.
  - Bonus, Orbbec Ego 10 s at `oblique`: device /dev/v4l/by-id/usb-ORBBEC_EGO_ORBBEC_AZER76400HV-video-index0
    'ORBBEC: Ego left' found by usb_id discovery; negotiated 1600x1200 @ 30 MJPG, delivered at 640x480;
    300 frames in 9.97 s, fps 30.00, 0 drops, interval p50/p99 33.32/35.72 ms, jitter p50/p99/max
    0.22/2.94/3.44 ms. Frames are real imagery (mean 81.4, std 46.7).
  - Finding: the Ego overrules the requested resolution -- 320x240, 640x480, 1280x720 and 1600x1200 all
    negotiate 1600x1200 @ 30 MJPG. `oblique.resolution` left as the UNMEASURED placeholder it was; Fable's call.
  - `.venv/bin/ruff check .` -> "All checks passed!"; `.venv/bin/python -m pytest -q` with the Ego attached ->
    323 passed, 4 skipped (3 `top` readonly + the motion marker). 32 new tests in tests/test_cameras.py.
  - Deviation: tests/test_mock_drivers.py was edited (not on the allowed file list) -- its
    test_factory_refuses_the_real_backend_for_every_device asserted NotImplementedError for all of DEVICES,
    which the task's own camera wiring of make(..., backend="real") makes false. Parametrisation narrowed to the
    four actuated devices and renamed; no assertion weakened. Full rationale in agents/BUILD_LOG.md.

## T-011  Left-arm forward kinematics for the workspace box
status: accepted
priority: P1
phase: 0
owner: opus
depends_on: T-005, T-012
hardware: none
depends_notes: needs T-002's finding on where the G1 model lives
deliverables:
  - runtime/fk.py: `left_arm_fk(q7: np.ndarray, waist_yaw: float) -> np.ndarray` giving the DexH15 mount point (left wrist frame) position in the G1 base frame, computed from the G1 model in third_party/g1_pico_teleop (MJCF via mujoco, or URDF via a small numpy chain; record which and cite the file). Joint order from config/robot.yaml
  - runtime/safety.py wired to use it by default (fk injection stays for tests)
  - tests/test_fk.py: zero pose position matches the model's published wrist offset within 1 mm; a set of 20 random configurations agree between the chosen implementation and mujoco (if both available) within 1e-6 m
  - docs/safety.md updated with the frame definition and a figure-free description of the box
acceptance:
  - tests pass; `Guard.admit` with the real fk rejects a target whose wrist would be outside config/safety.yaml's box (test)
notes: Use the MJCF vendored by T-012 (third_party/unitree_g1_mjcf/), loaded with mujoco; joint order from config/robot.yaml. The tool offset from the wrist to the fingertip pinch point is UNMEASURED until Phase 1; the box is checked at the wrist for now and that is stated in docs/safety.md.
result: (opus, 2026-09-11T19:47+07:00, commit 60348c1)
  - Implementation: mujoco on third_party/unitree_g1_mjcf/g1_29dof.xml (the T-012 vendored MJCF), joint
    order and qpos addresses from config/robot.yaml, base pinned to identity so positions are in the
    g1_pelvis frame, mj_kinematics only. runtime/fk.py, 119 lines.
  - `.venv/bin/python -m pytest tests/test_fk.py -q -s` -> 22 passed. PASS
  - Zero pose vs the body chain parsed out of the XML by the test (ElementTree + quaternion arithmetic,
    no mujoco): wrist at [0.19977428, 0.14866142, 0.09523278] m, max |diff| = 3.098e-09 m, criterion
    1e-3 m. PASS
  - 20 random configurations inside config/safety.yaml's joint limits (seed 20260911) vs an independent
    evaluation with a fresh MjModel/MjData and reverse-order qpos writes: worst disagreement 0.000e+00 m,
    criterion 1e-6 m. PASS
  - Guard.admit with the real fk and a valid tmp_path session: shoulder pitch -2.5 rad puts the wrist at
    [-0.0390, 0.0153, 0.5604] m and is rejected with rule="workspace_box" naming left_wrist_yaw_link,
    admitted stays 0; the all-zero target is inside the box and is admitted. PASS
  - Envelope.from_config() injects runtime.fk.left_arm_fk; an explicit fk still overrides it; an
    Envelope built directly with fk omitted still fails closed (tests/test_safety.py, rewritten to build
    that envelope through the constructor since from_config now has a default). PASS
  - left_arm_fk mean call time 8.4 us over 1000 calls (16.7 ms budget at command_rate_limit_hz = 60).
  - `.venv/bin/ruff check .` -> All checks passed. `.venv/bin/python -m pytest -q` -> 195 passed,
    1 skipped (motion test, no session). Was 173 before this task.
  - Fact for the envelope review, recorded in docs/safety.md and BUILD_LOG: the all-zero pose is INSIDE
    the current placeholder box, not outside as the notes predicted (the G1's zero pose points the upper
    arm forward, it does not hang down). config/safety.yaml untouched.
  - No hardware touched, no motion command sent, hardware/session.enable never created or read.

## T-012  Dependencies and assets for the arm IK path (D-006, D-008)
status: accepted
priority: P0
phase: 0
owner: opus
depends_on: T-003
hardware: none
deliverables:
  - third_party/unitree_g1_mjcf/: copy of ~/Teleopit/assets/robots/unitree_g1/ restricted to g1_29dof.xml, LICENSE, README.md and
    ONLY the 35 mesh files that g1_29dof.xml references (Fable measured: 19 MB; the full meshes/ tree is 63 MB and includes dex3,
    avp and o6 variants that are not needed), keeping the meshes/ relative layout so `meshdir="meshes"` still resolves; a
    MANIFEST.txt listing source path, copy date, and sha256 of every file; tracked in git
  - config/robot.yaml `limits_source` repointed to the vendored file (the only edit to that file)
  - requirements.txt: add mujoco (latest 3.x that mink supports), mink, pico_bridge 0.2.1 pinned by the GitHub release URL and
    `--hash=sha256:...`; remove opencv-python-headless (D-008); re-resolve so `uv pip install -r requirements.txt --dry-run`
    reports no changes; record the resolved versions in docs/setup.md
  - tests/test_assets.py: mujoco loads the MJCF; the model has joints named exactly as config/robot.yaml lists for waist yaw
    and the 7 left-arm joints; their qpos addresses are recorded in config/robot.yaml (key `mjcf_qpos_index`, measured by the
    test's own load, and the test asserts the yaml matches the model); pico_bridge imports and its ControllerState dataclass
    has a `pose` field
  - docs/setup.md updated
acceptance:
  - `.venv/bin/python -c "import mujoco, mink, pico_bridge"` exits 0
  - tests/test_assets.py passes; full suite green; ruff clean
  - MANIFEST.txt sha256 lines verified by `sha256sum -c` (command and output in BUILD_LOG.md)
  - `.venv/bin/python -c "import cv2; print(cv2.__file__)"` works and `uv pip list` shows exactly one opencv distribution
notes: The MJCF originates from Unitree (BSD-3); keep its LICENSE next to it. Never modify the XML; if the IK needs legs
  pinned, do it at load time in code (T-013), not by editing the asset.
result:
  commit: 7fbc50c
  - vendored third_party/unitree_g1_mjcf/: 38 files, 19,697,688 bytes (19 MB) -- g1_29dof.xml, LICENSE,
    README.md and exactly the 35 meshes the XML references, meshes/ layout preserved, byte-identical to
    /home/alois/Teleopit/assets/robots/unitree_g1/ (diff -r --brief: no differences; source not modified)
  - MANIFEST.txt: `cd third_party/unitree_g1_mjcf && sha256sum -c MANIFEST.txt` -> exit 0, 38 lines,
    38 ": OK", 0 FAILED
  - resolved versions: mujoco==3.13.0 (latest 3.x), mink==1.3.0 (needs mujoco>=3.1.6), pico_bridge==0.2.1
    by release URL + --hash=sha256:7cf0fee07c76541fd06e2ee6bdeec3d11fec578cd4ef6b45179dec4af31b369f;
    29 packages installed; opencv-python-headless removed (D-008)
  - `uv pip install -r requirements.txt --dry-run` -> "Resolved 51 packages / Checked 51 packages /
    Would make no changes"
  - acceptance 1: `.venv/bin/python -c "import mujoco, mink, pico_bridge"` -> exit 0
  - acceptance 2: pytest tests/test_assets.py -> 10 passed; full suite -> 173 passed, 1 skipped
    (163 before + 10 new; skip is the motion autoskip); `.venv/bin/ruff check .` -> All checks passed!
  - acceptance 4: `import cv2; print(cv2.__file__)` -> .venv/lib/python3.10/site-packages/cv2/__init__.py,
    exit 0; `uv pip list | grep -i opencv` -> exactly one line, opencv-python 5.0.0.93
  - config/robot.yaml: limits_source -> third_party/unitree_g1_mjcf/g1_29dof.xml; mjcf_qpos_index added to
    all 8 joint entries, measured from the model (waist_yaw 19, left arm 22..28 = 29-joint index + 7,
    the model's first joint being a 7-qpos floating base); tests/test_assets.py asserts the yaml matches
  - note for review: uninstalling opencv-python-headless deletes files opencv-python shares in
    site-packages/cv2/ and leaves `import cv2` succeeding as an empty namespace package; repaired with
    `uv pip install --reinstall-package opencv-python -r requirements.txt`. See agents/BUILD_LOG.md T-012.

## T-013  Controller-pose to 8-DoF arm IK prototype (pulled forward from Phase 2, non-hardware)
status: accepted
priority: P1
phase: 2
owner: opus
depends_on: T-012, T-011
hardware: none
deliverables:
  - teleop/retarget.py: `ArmIK` built on mink over the vendored G1 MJCF with every joint except waist yaw and the 7 left-arm
    joints fixed at their config/robot.yaml rest values; `solve(target_pos_m, target_quat_xyzw, q_current) -> q8` with joint
    limits and a per-step velocity limit from config/safety.yaml; a `pinch_from_glove(distance_m) -> float in [0,1]` stub that
    reads calibration bounds from config/hand.yaml; a frame transform `pico_to_g1_base` with the UNMEASURED calibration
    placeholder in config/robot.yaml
  - tests/test_retarget.py: 50 reachable wrist targets sampled inside the config/safety.yaml box, solved from the rest pose in
    at most 30 iterations each; FK of the solution (runtime/fk.py) within 5 mm position and 3 deg orientation for at least 90%
    of targets (print the pass rate); joint limits never exceeded; velocity limit respected step to step
  - benchmark line in BUILD_LOG.md: mean solve time per call on this laptop with the command
  - docs/teleop.md
acceptance:
  - tests pass with the printed pass rate >= 90%
  - mean solve time < 5 ms per call (measured, command logged)
notes: No hardware, no drivers touched. This is the IK that D-006 replaces Teleopit with; keep it under 200 lines. Also fix the
  future-tense sentence about T-012 in docs/config.md.
result: (commit 5d067f3)
  - `.venv/bin/ruff check .` -> All checks passed!; `.venv/bin/python -m pytest -q` -> 327 passed, 1 skipped
    (297 before + 30 new in tests/test_retarget.py; the skip is the motion autoskip)
  - acceptance 1, printed pass rate: `.venv/bin/python -m pytest tests/test_retarget.py -q` ->
    "pass rate 100% (50/50) within 5 mm and 3 deg; position error median 0.301 mm / p90 0.910 mm,
    orientation error median 0.092 deg / p90 0.223 deg"; every solve used <= 30 iterations (asserted).
    Targets are the FK of random joint draws inside the config/safety.yaml limits (waist clamp applied)
    landing inside the workspace box with margin_m removed, seed 0, so reachable by construction.
  - acceptance 2, mean solve time: same command -> "warm (tracking, 250 calls) mean 0.632 ms, p99
    9.204 ms; cold from the rest pose (50 calls) mean 3.896 ms, max 9.125 ms". Warm is the teleop case
    of the task's clarification (previous solution as seed, target moved +-3 mm). Three repeats: 0.628 /
    0.659 / 0.628 ms warm mean. Both are under the 5 ms budget.
  - also asserted: solutions and every intermediate step inside the safety joint limits and the waist
    clamp; largest per-step joint move <= joint_velocity_limit_rad_s * step_dt_s = 0.15 rad and > half of
    it (so the limit binds); no uncommanded joint and no base dof moves by more than 1e-9 over a solve;
    ArmIK.fk_pose position == runtime.fk.left_arm_fk to 1e-12
  - teleop/retarget.py is 200 code lines (318 with docstrings and comments)
  - DEVIATION for review: teleop.ik.step_dt_s is 0.1 s, not the 30 Hz action period of Fable's guidance.
    At dt=1/30 the per-step velocity limit caps 30 iterations at 1.5 rad of travel while the targets sit a
    median 1.78 rad from the rest pose, giving pass=32/50; measured sweep 32/46/50/48/49 at
    dt=1/30/0.05/0.10/0.15/0.30. step_dt_s is documented as a solver trust region, not a control period;
    the command-level velocity limit stays with runtime/safety.py. Full reasoning and the alternatives in
    agents/BUILD_LOG.md T-013.
  - also for review: config/robot.yaml gained a `teleop.ik` settings block (not just the UNMEASURED
    placeholders the task listed), because section 7 forbids the equivalent constants in code

## T-014  Worktree helper for parallel builders
status: accepted
priority: P2
phase: 0
owner: opus
depends_on: T-001
hardware: none
deliverables:
  - tools/worktree_setup.sh BRANCH PATH: creates a git worktree from main, creates its .venv from requirements.txt, and makes the
    git-ignored on-disk payloads available inside it (a real `_internal/` directory of symlinks into the main tree, as T-009 did by
    hand, plus any other path listed in a small `tools/worktree_payloads.txt`), then runs `.venv/bin/python -m pytest -q` there
  - tools/worktree_teardown.sh PATH: removes the worktree and deletes its branch only if it is fully merged into main
  - docs/setup.md section on parallel builders
acceptance:
  - `bash tools/worktree_setup.sh wt/smoke /tmp/ludo-wt-smoke` ends with the full suite green inside the worktree (command and
    output in BUILD_LOG.md), and `bash tools/worktree_teardown.sh /tmp/ludo-wt-smoke` leaves `git worktree list` with main only
  - `git status` in main is clean afterwards
notes: Nothing under third_party/ is modified; the symlinks live only in the worktree and are git-ignored there.
result:
  commit: 7cf0762
  files: tools/worktree_setup.sh, tools/worktree_teardown.sh, tools/worktree_payloads.txt, docs/setup.md
  acceptance 1: `bash tools/worktree_setup.sh wt/smoke /tmp/ludo-wt-smoke` -> exit 0, "worktree_setup: OK
    worktree=/tmp/ludo-wt-smoke branch=wt/smoke suite=green", pytest inside the worktree 327 passed, 1 skipped
    in 29.15 s; 31.5 s wall clock for the whole helper (worktree + venv + 328 payload symlinks + suite). Run
    twice, same counts. PASS
  acceptance 1b: `bash tools/worktree_teardown.sh /tmp/ludo-wt-smoke` -> exit 0, "branch wt/smoke deleted
    (merged into main)"; `git worktree list` then holds only the main tree and the two live builder worktrees
    (ludo-g1-wt-t010, ludo-g1-wt-t014), no /tmp/ludo-wt-smoke; `git branch --list` has no wt/smoke; the path is
    gone from disk. PASS
  acceptance 2: main is dirty from the concurrent builder in it (config/training.yaml, board/perception.py,
    runtime/controller.py, runtime/goal.py, runtime/policy_api.py), so measured as change-free instead:
    `git -C ~/ludo-g1 status --porcelain` snapshotted before and after a full setup+teardown cycle ->
    `diff` identical, exit 0. My scripts write nothing into the main working tree. PASS
  refusals run: path exists (exit 1), branch exists (exit 1, nothing created), no args (exit 2), dirty
    worktree (exit 1, worktree kept), main working tree (exit 1), unmerged branch (exit 0, worktree removed,
    branch kept with the merge command printed).
  gate: `.venv/bin/ruff check .` all checks passed; `.venv/bin/python -m pytest -q` 327 passed, 1 skipped in
    27.81 s.
  not met: none. No new pytest tests were added (shell tooling that builds worktrees and a 600 MB venv);
    every branch of both scripts was executed by hand and quoted in BUILD_LOG.md. Flagged there for review.

## T-015  Phase 0 report
status: accepted
priority: P1
phase: 0
owner: opus
depends_on: T-010, T-014
hardware: none
deliverables:
  - agents/BUILD_LOG.md: a "Phase 0 report" section (CLAUDE.md section 6) with: per-device SDK status table (from docs/sdks.md,
    one line each), verdict table for A1..A7 and U1..U6 (from DECISIONS D-002 with later corrections D-006, D-009, D-010), test
    count and runtime of the full suite, the list of every UNMEASURED key per config file (`runtime.config.unmeasured`), the
    status of the four Phase 0 exit checks (tests on mocks; safety.py rejects without session; Greennode round trip; docs/sdks.md
    coverage) with the command for each, and the open human items (H-001..H-003, Q-001..Q-010) in one table
  - docs/README.md: one page index of docs/ with one sentence per module page
acceptance:
  - every number in the report is next to the command that produced it (spot-checked by Fable)
  - `.venv/bin/python -m pytest -q` count in the report equals a fresh run at review time
notes: No code changes. Do not restate the brief.
result: (opus, 2026-09-11T21:00+07:00, branch wt/t015, commit 44f5c45)
  Phase 0 report appended to agents/BUILD_LOG.md (seven sections) and docs/README.md written (11 module
  pages, one sentence each). No code changed.
  measured in the worktree at 8a2861c:
  - `time .venv/bin/python -m pytest -q` -> 378 passed, 4 skipped in 57.19s (real 0m57,511s);
    `--collect-only` -> 382 tests collected. `.venv/bin/python -m ruff check .` -> All checks passed!
  - exit check 2: `.venv/bin/python -m pytest tests/test_safety.py -v -k "test_guard_on_hardware_refuses_without_a_valid_session or test_simulated_is_keyword_only_and_defaults_to_false"`
    -> 5 passed, 53 deselected in 0.19s (absent / expired / unconfirmed / unparsable all raise
    SafetyViolation rule=session_gate with admitted==0).
  - exit check 3: `bash tests/test_greennode_local.sh` -> all checks passed, 22 ok, 0 FAIL, local
    transport only; the real round trip is blocked on Q-001 (`ls -l ~/.config/ludo-g1/env` -> no such file).
  - exit check 4: `.venv/bin/python -m pytest tests/test_docs_sdks.py -s` -> 9 passed, 157 path:line
    references (129 unique); all eight devices have a state read, the three actuated ones a target write.
  - UNMEASURED keys via `runtime.config.unmeasured`: robot 17, safety 10, cameras 17, board 13, hand 14,
    training 0 (71 total), each listed by name in the report.
  - verdicts: A1 confirmed+narrowed, A2 code-confirmed/link open, A3 partial, A4 confirmed, A5 refuted
    (D-006), A6 open (Q-001), A7 adopted; U1/U3/U6 open, U2 confirmed at the API level, U4 still assumed
    no, U5 confirmed short (`df -h /home` -> 14G available of 76G).
  not met: none. Two Phase 0 exit items stay open on humans, not on work: the real Greennode job (Q-001)
    and the real board still (H-001). Noted in the report: TASKS.md still shows T-001 as `review`
    although REVIEW.md accepted it; not fixed here because only the T-015 lines may be touched.

## T-016  Mock end-to-end controller loop (runtime/controller.py on mocks)
status: accepted
priority: P1
phase: 5
owner: opus
depends_on: T-006, T-007, T-013
hardware: none
deliverables:
  - runtime/policy_api.py: `Policy` Protocol: `reset(command)`, `act(observation) -> ActionChunk` (16 actions x 9 dims at 30 Hz
    per CLAUDE.md 5.2/5.3), `done(observation) -> bool`; `Observation` dataclass (top, oblique, palm frames, state, goal heatmaps,
    task_id one-hot) and `ActionChunk`; a `HoldPolicy` test double that returns the current state as every action (it commands
    no motion at all; this is a placeholder for tests, never deployed, and says so in its docstring; R2)
  - runtime/goal.py: render the two goal heatmap channels (source, target) onto the `top` image frame from Cell.top_px with a
    gaussian of configurable sigma (config/training.yaml), and the task_id one-hot
  - board/perception.py: `Perception` Protocol with `verify(command, before, after) -> Outcome` and a `MockPerception` that
    reads the mock engine board state (placeholder until the engine team delivers)
  - runtime/controller.py: the 10 Hz loop of CLAUDE.md 5.5: `cmd = engine.next_command()`, policy.reset, loop: build observation
    from drivers (aligned via runtime.clock), policy.act every 100 ms, execute 8 of 16 actions at 30 Hz through the arm and hand
    drivers (which admit through the Guard), stop on policy.done or the 20 s timeout, then perception.verify and engine.report;
    heartbeat to data/logs/ every second; structlog throughout
  - tests/test_controller.py on mocks with a fake clock: one MOVE command runs to timeout with HoldPolicy, the loop rate is
    10 Hz +/- 0.5 (measured on the fake clock), every action went through Guard.admit (count), engine.report was called with an
    Outcome; a RECOVER after a failed Outcome is issued by the stub and executed
  - docs/controller.md
acceptance:
  - tests pass; `grep -rn "hardware_checks" runtime/ board/ policy/` empty
  - a 60 s mock run (`.venv/bin/python -m runtime.controller --backend mock --seconds 60`) completes with heartbeats in
    data/logs/ and a printed summary of commands executed and outcomes (command and output in BUILD_LOG.md)
notes: No learned policy exists yet; HoldPolicy exists only so the orchestration can be tested. No scripted trajectories anywhere.
result:
  commit: d7d9255  (placeholder left by the builder; filled in by Fable at T-025 review)
  commit: ac0141e
  tests: 352 passed, 1 skipped (`.venv/bin/python -m pytest -q`; was 327+1). 25 new in tests/test_controller.py.
  ruff: `.venv/bin/ruff check .` clean.
  loop rate (fake clock, the criterion): policy_hz 9.98 Hz over a full 20.03 s MOVE (201 calls), action_hz 29.9;
    asserted at 10.0 +/- 0.5. Guard: arm.admitted == hand.admitted == actions_sent (601), refused 0.
  engine cycle: report() called once per command with Outcome(success=False, failure_mode="timeout_no_progress");
    the stub then issued RECOVER and the loop executed it (test + the 60 s run: roll, recover, recover).
  60 s mock run (`.venv/bin/python -m runtime.controller --backend mock --seconds 60`): elapsed 60.01 s,
    3 commands (roll=1, recover=2), 0 success / 3 failure (timeout_no_progress), 598 policy calls = 9.96 Hz,
    1793 actions = 29.88 Hz, 0 safety refusals, 0 alignment failures, heartbeat
    data/logs/controller_20260911T204420.heartbeat with 60 lines (one per second). Full output in BUILD_LOG.md.
  config: config/training.yaml gains a `runtime:` block (primitive_timeout_s 20.0, alignment_tolerance_ms 50.0,
    heartbeat_s 1.0). unmeasured("training") is still [].
  not met: (1) `grep -rn "hardware_checks" runtime/ board/ policy/` returns one pre-existing prose line,
    runtime/safety.py:8 (not an import, and outside this task's file list). The import-only grep is empty and a
    test asserts it. (2) runtime/controller.py is 340 lines (240 code, 52 docstring, 45 blank) against Fable's
    "under 250" guidance. Both explained in BUILD_LOG.md.

## T-017  Teleop recorder to LeRobot v2 on mocks (D-011)
status: accepted
priority: P1
phase: 2
owner: opus
depends_on: T-006, T-016
hardware: none
deliverables:
  - requirements.txt: lerobot (pinned release) and torch CPU wheel pinned; resolved versions in docs/setup.md
  - teleop/recorder.py: `Recorder(session_id)` that opens a LeRobotDataset under data/raw/<session_id>/ with the observation and
    action features of CLAUDE.md 5.3 at 30 Hz (frames from the camera drivers, state from arm+hand drivers, action = the teleop
    target admitted by the Guard, raw glove and DexH15 joints stored as extra features), aligned with runtime.clock.align and
    shifted by the latency values in config/robot.yaml; episode metadata per 5.6; `start_episode(command)`, `mark_success`,
    `mark_perturbed`, `stop_episode`; a dataset card README.md per session with stream rates, dropped frames, skew p50/p99
  - tests/test_recorder.py on mocks with a fake clock: a 60 s mock episode has all streams, skew p99 < 10 ms, zero dropped
    frames; replaying the recorded actions through the mock arm reproduces the recorded joint targets within 1e-6; the dataset
    loads back with LeRobotDataset and has the expected feature keys and frame count
  - docs/teleop.md updated
acceptance:
  - the three tests above pass with the printed skew and frame counts; full suite green; ruff clean
notes: The operator UI and the real Pico/glove input are separate tasks. Never write outside data/raw/ (git-ignored).
result:
  commit: 9c78d94
  versions: lerobot 0.4.4 (the last release that installs on Python 3.10; 0.5.0+ need >= 3.12), torch 2.9.1+cpu,
    torchvision 0.24.1+cpu, torchcodec 0.10.0. CPU wheels via three --find-links on the PyTorch CPU index; no
    nvidia-* package installed. `uv pip install --dry-run -r requirements.txt` -> "Would make no changes".
  tests: 391 passed, 4 skipped (`.venv/bin/python -m pytest -q`, 70.5 s; was 382 collected). 13 new in
    tests/test_recorder.py. ruff: `.venv/bin/ruff check .` clean.
  60 s mock episode (200 Hz poll / 30 Hz write, fake clock): 1800 frames, all 7 streams,
    skew p50 6.666 ms / p99 6.667 ms (budget < 10 ms), 0 dropped on every stream, 0 skipped ticks,
    0 alignment failures.
  replay: 150 recorded actions back through a fresh MockArm -> worst |admitted - recorded| = 0.0 (budget 1e-6).
  round trip: LeRobotDataset(repo_id, root=...) reloads 2 episodes / 1860 frames with all 8 feature keys and
    the configured shapes (3x480x640 top and oblique, 3x240x320 palm, state 9, hand_joints 15, glove 17, action 9).
  config: config/training.yaml gains a `recorder:` block (alignment_lag_periods 1, alignment_tolerance_ms 20.0,
    drop_gap_periods 1.5, use_videos false, image_writer_threads 4). unmeasured("training") is still [].
  not met: (1) the dataset is LeRobot **v3.0**, not v2: lerobot 0.4.4 is the newest release installable on
    Python 3.10 and writes CODEBASE_VERSION "v3.0"; no installable release writes v2 (0.1.0 needs mujoco-py and
    torchvision<0.18). config/training.yaml `dataset.format: lerobot_v2` is now inaccurate and needs Fable's
    decision; this task may only add config keys, so I left it. (2) lerobot re-installs
    opencv-python-headless alongside opencv-python, which D-008 had removed; a requirements file cannot drop it,
    so `uv pip install --reinstall-package opencv-python -r requirements.txt` is now part of the install recipe
    and the choice is Fable's. (3) teleop/recorder.py is 421 lines against the "under 300" guidance; the file
    list did not allow the sibling module D-013 suggests. All three explained in BUILD_LOG.md.

## T-018  G1 arm driver, read-only state stream (rt/lowstate) and 10-minute stream stats
status: accepted
priority: P0
phase: 1
owner: opus
depends_on: T-006, T-010
hardware: read-only
deliverables:
  - drivers/g1_arm.py: `G1Arm` implementing ArmDriver: `read_state()` from a ChannelSubscriber on rt/lowstate (unitree_sdk2py, DDS
    interface from config/robot.yaml), stamped with runtime.clock at callback time, exposing the 7 arm + waist yaw joints in
    action_order plus the full 29-joint q/dq for the dataset; `send_targets` present but raising NotImplementedError until T-021
    (no publisher is created in this task); `probe()` returns state rate and the robot's mode_machine
  - tools/hardware_checks/stream_stats.py extended with `--stream arm` (reuse the camera stats code path)
  - tests marked readonly that skip without a LowState within 3 s, naming the interface
  - docs/drivers.md updated with the DDS setup (interface, IPs, the one-time nmcli profile from H-002)
acceptance:
  - with the LAN down: full suite green, readonly tests skipped with the interface name in the reason
  - with the LAN up (H-002): `stream_stats.py --stream arm --seconds 600` output in BUILD_LOG.md: rate within 5% of the SDK's
    500 Hz (or whatever the SDK delivers; record it), drop count, jitter p50/p99; 10 minutes, per CLAUDE.md Phase 1
notes: No publisher, no motion. If the robot is in a mode where rt/lowstate is silent, record it and stop.
result: drivers/g1_arm.py (332 lines) -- G1Arm, the read half of ArmDriver: ChannelSubscriber on config/robot.yaml
  topics.state (rt/lowstate) after one ChannelFactoryInitialize(domain_id, interface), bound lazily once per process
  (never at import; dds_binding() reports it); read_state() gives the 7 arm joints + waist yaw in action_order stamped
  with runtime.clock.now_ns inside the subscriber handler; full_state() gives q/dq/tau_est for all control.motor_count
  (29) joints plus mode_machine/mode_pr/tick; poll() drains the arrivals (BACKLOG 4096); probe(window_s) reports the
  measured rate, mode_machine and the interface. NO DDS WRITER OF ANY KIND: send_targets raises NotImplementedError
  naming T-021, and a test greps the module for ChannelPublisher / rt/arm_sdk / rt/lowcmd (none present).
  stream_stats.py gained --stream {top,oblique,palm,arm} over the same stats path (--camera still works), with drain()
  collecting callback-stamped arrivals. config/robot.yaml gained control.motor_count (29) and control.state_timeout_s
  (3.0); REQUIRED_KEYS untouched. docs/drivers.md gained "The real arm" with the DDS setup and the H-002 nmcli profile;
  H-002's post-check is now `stream_stats.py --backend real --stream arm --seconds 10`.
  Acceptance 1 (LAN down) PASS: ruff clean; full suite green at commit through the pre-commit hook (no --no-verify);
  tests/test_g1_arm.py 23 passed, 3 skipped, the 3 readonly skips reading "no G1 state stream on interface 'UNMEASURED':
  config/robot.yaml network.dds_interface is UNMEASURED ... (H-002)". Mock check:
  `stream_stats.py --backend mock --stream arm --seconds 5` -> 502 samples in 5.01 s, 100.00 Hz (expected 100),
  0 drops, jitter p50 0.00 ms / p99 0.00 ms; `--backend real --stream arm` exits 3 naming dds_interface.
  Acceptance 2 (LAN up) OPEN as the task anticipated: no LowState_ was ever received, the robot LAN is still down
  (H-002 OPEN), so no 600 s rate/drop/jitter numbers exist and none are claimed (R5).
  Deviation 2 (outside the touch list, minimal): a real `arm` backend falsified three existing assertions --
  tests/test_cameras.py:189 and tests/test_mock_drivers.py:107 (`make("arm", backend="real")` must raise) lost `arm`
  from their lists with a comment naming T-018, as the cameras lost theirs at T-010, and the R2 check
  tests/test_mock_drivers.py:514 (no `hardware_checks` string under drivers/) made me reword the ArmUnavailable
  message to name H-002 and `list_devices.py` without the path. No check was weakened.
  Deviation 1: the module is 332 lines, not under 250; D-013 item 2's remedy (a sibling module) is outside this task's
  touch list, so it stayed one file -- BUILD_LOG has the proposed split for T-021.
  commit: 3a8ba29 (pre-commit hook: ruff clean, 533 passed, 7 skipped in 1346 s under load; no --no-verify)

## T-019  DexH15 driver, read-only state and palm camera, 10-minute stream stats
status: accepted
priority: P0
phase: 1
owner: opus
depends_on: T-006
hardware: read-only
deliverables:
  - drivers/dexh15.py: `DexH15` implementing HandDriver: opens the Modbus serial device from config/hand.yaml, reads joint angles
    (getJointPositionsAngle), motor positions and tactile summary, never calls enableMotor or any set* in this task;
    `palm_frame()` through pxdex DexH15Camera at the resolution in config/cameras.yaml; `send_pinch` raises NotImplementedError
    until T-022
  - stream_stats.py `--stream hand` and `--camera palm --backend real`
  - readonly tests that skip without the device
  - config/hand.yaml joint names and order corrected from the live device's getJointPositionsAngle length and the SDK stubs
acceptance:
  - with the hand present (H-003): 10-minute stats for hand state and palm camera in BUILD_LOG.md; achieved joint read rate
    recorded (A3 verdict updated in docs/sdks.md)
  - without: suite green, tests skipped with the device path in the reason
notes: The motors stay disabled. A `grep -n "enableMotor\|setMotor\|setJoint" drivers/dexh15.py` must show only the
  NotImplementedError stub for send_pinch.
result: (opus, 2026-09-12, branch wt/t019, commit 306744d; hash recorded by the follow-up commit). The hand was NEVER reached: it has never been plugged in
  (H-003 open) and no /dev/ttyUSB* or /dev/ttyACM* node existed during the task.
  - suite: 573 passed, 10 skipped, 774.07 s (`.venv/bin/python -m pytest -q`); tests/test_dexh15.py alone
    33 passed, 3 skipped. ruff check and ruff format --check clean on every file touched.
  - the 3 new skips name the device path: "config/hand.yaml device.port is UNMEASURED and no
    /dev/ttyUSB*, /dev/ttyACM* node has usb id 067b:23a3 ... H-003" (2) and "palm: config/cameras.yaml
    palm.device is UNMEASURED ..." (1). stream_stats --backend real exits 3 with the same two reasons.
  - R1/R2: `grep -n "enableMotor\|setMotor\|setJoint" drivers/dexh15.py` -> one hit, line 373, inside the
    send_pinch NotImplementedError message; `grep -c initMotorPosition` -> 0. A test asserts both.
  - mock statistics (the tool's own path, not a device): --stream hand 60 s -> 1801 samples, 30.000 Hz,
    0 drops, jitter p50/p99/max 0.0/0.0/0.0 ms; --camera palm 60 s -> 1800 samples, 30.000 Hz, 0 drops.
  - acceptance 1 (hand present) is OPEN and H-003 stays open: no 10-minute hand or palm statistics, no
    achieved joint read rate, and no A3 verdict written (docs/sdks.md untouched, R5). H-003's post-check
    now carries the four commands that produce those numbers.
  - deviations logged in BUILD_LOG.md: drivers/dexh15.py is 485 lines (not under 300), and tests/
    test_cameras.py + tests/test_mock_drivers.py needed the same one-line list edit T-018 made, because
    make("hand", backend="real") no longer raises NotImplementedError.

## T-020  Glove and controller pose drivers, read-only, 10-minute stream stats
status: accepted
priority: P0
phase: 1
owner: opus
depends_on: T-006, T-013
hardware: read-only
deliverables:
  - drivers/pxcap.py: `PxCap` implementing GloveDriver through the route T-002 found usable (pxhandsdk if the deb is installed
    by then per Q-005, else the bundled runtime's Python); 17 encoder angles + host timestamp, restamped with runtime.clock;
    thumb-index distance and pinch scalar via teleop.retarget.pinch_from_glove
  - drivers/pico.py: `Pico` implementing PoseDriver through pico_bridge (PicoBridge.latest_frame().controllers.left.pose),
    position m, quaternion xyzw, transformed by teleop.retarget.pico_to_g1_base
  - stream_stats.py `--stream glove` and `--stream pose`
  - readonly tests that skip without the devices
acceptance:
  - with the devices: 10-minute stats for both streams in BUILD_LOG.md; glove rate (A4 verdict updated), controller pose rate
  - without: suite green, tests skipped naming the device
notes: The headset must run the PicoBridge app and reach this laptop; the network path from the lab's prior setup is in
  third_party/g1_pico_teleop/README.md section 3.3 (robot NAT). Record what was needed in docs/drivers.md.
result: (opus, 2026-09-12, branch wt/t020, commit 245dc1e; hash recorded by the follow-up commit). NEITHER device was reached: the glove has never been
  plugged into this laptop and the headset is not on the network (H-004, new).
  - suite: 643 passed, 14 skipped, 321.30 s through the pre-commit gate (ruff check . + pytest -q, no --no-verify).
    tests/test_pxcap.py alone 34 passed, 2 skipped; tests/test_pico.py alone 22 passed, 2 skipped.
  - the 4 new skips name the device: "no PxCap Pro glove: config/hand.yaml glove.port is UNMEASURED and glove.usb_id
    gives nothing to discover /dev/ttyUSB*, /dev/ttyACM* with ... (H-004)" (2) and "no PicoBridge receiver: the
    PicoBridge receiver could not start on 0.0.0.0:63901 ... (H-004)" (2). --backend real exits 3 with the same reasons.
  - Q-005 ANSWERED on the host side: the bundle's cp310 pxcappro binding loads IN-PROCESS in our venv with no glove
    attached -- `load_binding('bundle').PxCapPro().get_sdk_version()` -> (0, '1.0.8 20260806 17:08'), a read with no
    device -> 106. It needs a ctypes RTLD_GLOBAL preload of libpxcappro_sdk.so.1 because the extension's RPATH is wrong.
    The pxhandsdk deb is still not installed and is still tried first. Nothing under third_party/ modified.
  - new finding: TCP 63901 is held on this laptop by the systemd user unit holosim-pcservice (RoboticsService, pid 2448),
    so PicoBridge cannot bind. H-004 (b1) is `systemctl --user stop holosim-pcservice`.
  - mock statistics (the tool's own path, not a device): --stream glove 60 s -> 3000 frames, 50.000 Hz, 0 drops, jitter
    p50/p99/max 0.0 ms; --stream pose 60 s -> 7200 frames, 120.000 Hz, 0 drops, jitter 0.0 ms.
  - acceptance 1 (devices present) is OPEN and H-004 is OPEN: no 10-minute statistics for either stream, no A4 verdict
    written (docs/sdks.md untouched, R5), teleop.pico.input_hz still UNMEASURED. H-004's post-check carries the four
    commands that produce those numbers and the two config keys that must be filled first.
  - deviations logged in BUILD_LOG.md: (1) Pico.read() does NOT apply pico_to_g1_base, because teleop/loop.py:255
    already does and applying it twice would be wrong once Phase 1 calibrates it; read_in_pelvis_frame() is the map.
    (2) drivers/pxcap.py is 483 lines (not under 250). (3) four files outside the touch list: tests/test_cameras.py and
    tests/test_mock_drivers.py needed the same list edit T-018 and T-019 made, because make("glove"|"pose",
    backend="real") no longer raises NotImplementedError; and teleop/loop.py + its test needed a real fix, because
    main() caught only NotImplementedError around build() and would now have crashed on PoseUnavailable instead of
    exiting 2 (build() is also all-or-nothing now, so a half-built real loop leaves no bound port behind).
  - one pre-existing flake seen under load: tests/test_train.py::test_small_config_latency_at_ddim_10_and_5 asserts
    median(DDIM 5) < median(DDIM 10) on wall-clock latency and failed while the other builder's suite ran concurrently;
    alone it passes (76 ms vs 55 ms, budget 100 ms). Nothing in T-020 touches policy/.

## T-021  G1 arm write path over rt/arm_sdk with ramped weight; actuation latency measurement
status: todo
priority: P0
phase: 1
owner: opus
depends_on: T-018, T-011, T-033
hardware: motion
deliverables:
  - drivers/g1_arm.py `send_targets(cmd)`: Guard.from_config(simulated=False).admit, then LowCmd_ on rt/arm_sdk with only the
    8 commanded slots set (kp/kd from config/robot.yaml, UNMEASURED placeholders from the SDK example), CRC, publish at 50 Hz
    with zero-order hold; `enable()` ramps motor_cmd[29].q from 0 to 1 over config `arm_sdk_ramp_s`, `release()` ramps back and
    is also triggered by the watchdog (config/safety.yaml watchdog_timeout_s) when no command arrives (D-007)
  - tools/hardware_checks/arm_latency.py: with a valid session, commands a 0.05 rad step on one wrist joint from the current
    state (inside the envelope, through the Guard) and measures the time from publish to the first state change > 0.01 rad;
    20 repetitions; writes p50/p99 to BUILD_LOG.md and proposes the value for config/robot.yaml `latency.arm_ms`
  - tests: motion-marked test of the step (skipped without session); unit tests of the ramp and watchdog on a fake publisher
acceptance:
  - unit tests pass without hardware; `grep -rn "rt/lowcmd" drivers/ runtime/ policy/` empty
  - with a session: BUILD_LOG.md states what moved, the envelope in force (config hashes), the observed outcome, and the
    latency numbers; H-004 (enable a session) written before the run with the exact steps
notes: The first motion on this project. The step is tiny and inside the envelope; the human holds the e-stop (Q-004 must be
  answered first: the session checklist names it). Scripted motion lives only in tools/hardware_checks/ (R2).

## T-022  DexH15 write path, pinch synergy definition and bench test
status: todo
priority: P0
phase: 1
owner: opus
depends_on: T-019
hardware: motion
deliverables:
  - drivers/dexh15.py `send_pinch(scalar)`: Guard admit, then synergy expansion from config/hand.yaml, setJointPositionsAngle
    for the pinch fingers only (partial command, U2), idle fingers set once to the curled pose on enable
  - tools/hardware_checks/hand_synergy.py: interactive tool to record open and closed poses on a horse and on the die, write
    them to config/hand.yaml (pinch.open_pose/closed_pose, curled pose) with `_status: MEASURED` and the date
  - tools/hardware_checks/pinch_bench.py: 10 grasp-and-hold trials each on a horse and on the die (human places the object in
    the hand's pinch zone; the tool closes, lifts nothing, waits 5 s, opens; the human records hold/slip), results to BUILD_LOG.md
  - hand latency measurement (glove pinch step to DexH15 joint response) to config/robot.yaml `latency.hand_ms`
acceptance:
  - at least 9 of 10 holds on each object, recorded per trial in BUILD_LOG.md (CLAUDE.md Phase 1 Verify)
  - hand latency p50/p99 recorded with the command
notes: The hand is off the arm or the arm is idle for this bench; still a motion task (DexH15 actuators), session required.

## T-023  Reachable-cell map with waist yaw
status: todo
priority: P0
phase: 1
owner: opus
depends_on: T-021, T-020, T-008
hardware: motion
deliverables:
  - tools/hardware_checks/reach_map.py: the operator teleoperates (T-020 drivers + T-013 IK, through the Guard) to each cell
    the game uses; the tool shows the target cell on the Brio feed, records success/failure per cell and the joint pose,
    writes `reachable: true|false` per cell into config/board.yaml with a MEASURED status
  - a DECISIONS proposal from Fable if any used cell is unreachable (board offset or layout change)
acceptance:
  - every cell in config/board.yaml has a reachable flag and a pose or a failure note; unreachable list in BUILD_LOG.md
notes: Requires a calibrated board (H-001) and the teleop chain working end to end; this is also the first real teleop trial.

## T-024  Envelope boundary and waist clamp test with video evidence
status: todo
priority: P0
phase: 1
owner: opus
depends_on: T-021
hardware: motion
deliverables:
  - tools/hardware_checks/envelope_test.py: drives the wrist slowly towards each face of the workspace box and towards the waist
    clamp under teleop; logs the Guard rejections with the fk position at rejection; the human films it
acceptance:
  - six box faces and both waist directions each show a rejection within margin_m of the configured face; video path and log
    excerpt in BUILD_LOG.md
notes: After this passes, Fable proposes the measured envelope values for config/safety.yaml and Alois commits them (R3).

## T-025  Teleop operator UI on mocks
status: accepted
priority: P1
phase: 2
owner: opus
depends_on: T-016, T-017
hardware: none
deliverables:
  - teleop/operator_ui.py: OpenCV window showing the `top` feed with the stub engine's src/dst cells drawn (runtime/goal.py),
    the current primitive and episode state, and keys: start, stop, mark success, mark perturbed, abort; drives Recorder
    (T-017); a headless mode for tests that renders frames to arrays without a window
  - tests/test_operator_ui.py on mocks in headless mode: key events change episode state; the rendered frame contains the two
    goal markers at the expected pixels
acceptance:
  - tests pass; a 30 s headless mock session records 2 episodes with correct metadata (command in BUILD_LOG.md)
notes: No hardware. The real teleop loop (input drivers -> IK -> Guard -> arm) is wired in Phase 2 after T-020/T-021.
result:
  commit: 684c03c
  tests: 402 passed, 4 skipped (`.venv/bin/python -m pytest -q`, 78.1 s; was 391 passed). 11 new in
    tests/test_operator_ui.py. ruff: `.venv/bin/ruff check .` clean.
  30 s headless mock session (`.venv/bin/python -m pytest tests/test_operator_ui.py -q -s`, stub seed 2,
    fake clock, 200 Hz poll / 30 Hz write): 30.0 s elapsed, 2 episodes ['roll', 'move'], 298 frames each,
    success [True, False], perturbed [False, True], skew p99 [6.667, 6.667] ms, 0 dropped on all 7 streams,
    0 aborted. Sidecar metadata checked field by field against the commands the engine handed out
    (episode 1: src R-base-0, dst track-12, horse R0, operator alois).
  goal markers (real 640x480 config, cells R-base-0 and track-17, uncalibrated so GoalRenderer.placeholder_px):
    markers at (447.3, 47.9) and (362.1, 175.6), 542 px changed, max distance from a cell 15.6 px against
    radius 14 + thickness 2 -- both centres drawn and nothing further than a marker radius changed.
  state machine: 11 tests -- every key in every state, `n` out of recording, abort discards (no episode, no
    sidecar) and the engine then issues a RECOVER, `q` aborts and run_window refuses headless, an exhausted
    engine is inert, a duplicate key binding is a ConfigError, a camera set without `top` is a ValueError,
    the banner text, and a tripwire proving the UI calls no send_targets/send_pinch (R1, R2).
  config: config/training.yaml gains an `operator_ui:` block (keys, marker radius/thickness/dot, two BGR
    colours, banner height and colours, font, window name). REQUIRED_KEYS untouched; unmeasured("training")
    is still [].
  not met: teleop/operator_ui.py is 261 lines against the "under 250" guidance; D-013 item 2's sibling-module
    remedy was not available (the file list allows no new module), so the docstrings stayed. Also added a
    `n` = mark-failure key the task did not list, so that a bad episode can be kept and labelled (5.6)
    instead of only discarded; explained in BUILD_LOG.md.

## T-026  Dataset viewer: frame strips for Fable's audits
status: accepted
priority: P1
phase: 2
owner: opus
depends_on: T-017
hardware: none
deliverables:
  - tools/dataset_view.py: for a session under data/raw/, renders per-episode frame strips (top with goal heatmap overlay,
    oblique, palm at 8 evenly spaced times) plus the action and state curves, to PNG under data/raw/<session>/strips/;
    prints the dataset card summary
acceptance:
  - runs on a mock session recorded by tests (tmp_path) and produces one PNG per episode; test asserts image size and that the
    goal overlay pixels differ from the raw frame
notes: Section 8 audit tool; keep it dependency-free beyond opencv and numpy.
result:
  commit: ac0141e
  tools/dataset_view.py (249 lines) + tests/test_dataset_view.py (8 tests) + a docs/teleop.md section.
  CLI: `python -m tools.dataset_view SESSION_ROOT [--episodes 0,3] [--out DIR]`; prints the dataset card
    (README.md) to stdout, writes episode_nnnnnn.png to SESSION_ROOT/strips/ (or --out).
  strip = header (ep, task, src -> dst, success, perturbed, frames, skew p99, all from episodes_meta.jsonl)
    / 8 evenly spaced `top` frames with the goal channels blended over them (green src, magenta dst, rendered
    by runtime.goal.GoalRenderer from the sidecar's stored pixels and sigma) / the same 8 `oblique` / the same
    8 `palm` upscaled to the column width / legend / two cv2 panels: 9 action dims and 9 state dims.
  acceptance (`.venv/bin/python -m pytest tests/test_dataset_view.py -q -s`): 2-episode mock session recorded
    by tests.test_recorder.Rig under tmp_path -> 2 PNGs, both 1927x797 px (asserted exactly), 451 kB and
    323 kB; goal overlay changes 3072/3072 px of the first `top` frame, peak |diff| 218/765, and each channel
    peaks within 1 px of the cell centre the recorder stored (src 18.9,29.8; dst 37.8,20.4).
  also asserted: a ROLL gets 0 goal layers and a bit-identical `top` row; the `oblique` and `palm` rows are
    always bit-identical to the dataset frames; --episodes and --out select and redirect; unknown episode and
    a non-session directory raise.
  at the real configured sizes (640x480 / 320x240), 3 s episode: 1927x817 px, 647 kB.
  suite: 399 passed, 4 skipped (`.venv/bin/python -m pytest -q`, 72.0 s; was 391). ruff `check .` clean.
  deviations: uses cv2.polylines rather than cv2.line (same primitive, one call per dim, no matplotlib); also
    imports runtime.goal / engine.interface / teleop.recorder, which are first-party, not dependencies.

## T-027  policy/dataset.py: loader, goal rendering, augmentation on the mock dataset
status: accepted
priority: P1
phase: 3
owner: opus
depends_on: T-017
hardware: none
deliverables:
  - policy/dataset.py: a torch Dataset over one or more LeRobot sessions yielding the observation dict of CLAUDE.md 5.3 (goal
    heatmaps rendered from stored cell ids via runtime/goal.py, task one-hot) and 16-step action chunks; augmentation per 5.7
    (color jitter, small crops on oblique/palm only, goal heatmap blur; never geometric augmentation on top); held-out cell-pair
    split helper for eval/protocol.py
  - tests on a mock session: shapes, dtype, chunk alignment, top frame never geometrically altered (pixel check on the
    un-jittered channel), split reproducible by seed
acceptance:
  - tests pass; a 1000-sample iteration benchmark (samples/s) in BUILD_LOG.md
notes: torch CPU is on the laptop per D-011. Real training runs on Greennode (Q-001). Also in this task: apply D-015
  (config/training.yaml dataset.format: lerobot_v3) and D-016 (requirements.txt opencv-python==4.12.0.88, drop the reinstall
  recipe from docs/setup.md and requirements comments, re-resolve), and add a pyproject filterwarnings entry that silences the
  HuggingFace datasets DeprecationWarning noise reported by T-017 (log the count before and after).
result: policy/dataset.py (LudoDataset + split_cell_pairs), tests/test_dataset.py (18 tests on a
  3-episode mock session recorded by tests.test_recorder.Rig), docs/policy.md. D-015 applied
  (config/training.yaml dataset.format: lerobot_v3), D-016 applied (opencv-python 5.0.0.93 ->
  4.12.0.88, reinstall recipe removed from requirements.txt and docs/setup.md; re-resolve installs
  only that one package and a second --dry-run reports "Would make no changes"; uv pip list shows
  opencv-python 4.12.0.88 and opencv-python-headless 4.12.0.88, cv2.__version__ 4.12.0, GUI: QT5),
  pyproject filterwarnings added.
  - acceptance "tests pass": .venv/bin/python -m pytest -q -> 428 passed, 4 skipped, 107.91 s;
    .venv/bin/ruff check . -> All checks passed.
  - acceptance "1000-sample iteration benchmark": tests/test_dataset.py::test_iteration_benchmark,
    1000 samples through a DataLoader (batch 8, augment=True, 64x48/48x32 mock frames, one torch
    thread): num_workers=0 81 samples/s, num_workers=2 145 samples/s (repeat run 80 / 144).
  - sample: top (5,h,w), oblique (3,h,w), palm (3,h,w), state (9,), task_id (3,), action (16,9),
    action_mask (16,), all float32; goal peaks within 1 px of the stored cell pixels; ROLL channels
    all zero; episode tail padded with the last action and masked; top RGB bit-identical under
    augmentation with the jitter at zero strength while oblique, palm and the goal channels change.
  - DeprecationWarning count before -> after: 2817 -> 0.
  commit: b7f9919

## T-028  Eval protocol and runner on mocks
status: accepted
priority: P1
phase: 3
owner: opus
depends_on: T-016, T-007
hardware: none
deliverables:
  - eval/protocol.py: `Trial` (primitive, src, dst, seed, perturbed), `make_trials(kind, n, held_out_pairs, seed)` producing the
    20-trial sets CLAUDE.md Phase 3/4 name (move on held-out cell pairs, roll, recover, and the 20-move scripted sequence from
    engine/scripts/eval_20_moves.yaml), success criteria per primitive expressed as required Outcome fields, and the failure-mode
    vocabulary of section 6.5 as an Enum used by perception and eval alike
  - eval/run_eval.py: runs N trials through runtime/controller.py with an injected policy, backend mock or real (real refuses
    without a valid session, through the drivers), writes eval/results/<timestamp>_<tag>.json with per-trial outcome,
    failure_mode, duration, safety refusals, config hashes, git commit, policy checkpoint hash; prints the success rate with
    the trial count and a per-failure-mode table
  - tests/test_eval.py on mocks with HoldPolicy: 20 mock MOVE trials produce a JSON with 20 entries, success rate 0/20 (HoldPolicy
    moves nothing) and every failure counted under a section 6.5 mode; trial sets reproducible by seed; held-out pairs never
    appear in the training-pair helper from policy/dataset.py (once T-027 lands; otherwise assert against a fixture list)
  - docs/eval.md
acceptance:
  - tests pass; `.venv/bin/python -m eval.run_eval --backend mock --kind move --n 20 --policy hold` writes the JSON and prints
    "0/20" (command and output in BUILD_LOG.md)
notes: R5: every success rate the project ever reports comes from this JSON. Keep the JSON schema in docs/eval.md.
result:
  commit: 2a6c38a
  eval/protocol.py (428 lines) + eval/run_eval.py (245) + tests/test_eval.py (23 tests) + docs/eval.md (177)
    + eval/results/.gitkeep; board/perception.py gained the 6.5 Enum `FailureMode` (defined there, imported by
    eval/protocol.py, so the deployed runtime path does not depend on eval/; behaviour identical, NO_PROGRESS
    is now FailureMode.TIMEOUT_NO_PROGRESS.value).
  protocol: Trial(index, primitive, src, dst, horse_id, seed, perturbed, perturbation);
    make_trials(kind, n, held_out_pairs, seed) over move / roll / recover / sequence; SuccessCriterion+judge()
    state success as required Outcome fields (MOVE: horse seen on dst in observed_state_delta; ROLL: a new die;
    RECOVER: a clean Outcome only, D-013); result schema (blank_row/record_execution/summarise/write_result/
    print_result), AttributedEngine, script_pairs(), RESULTS_DIR.
  runner: one Controller over mock drivers + the stub scripted with the trial commands, played one command at a
    time; the RunSummary difference per command is that command's measurement. Engine-level recovery is ON for
    kind=sequence only (max_reissues=2) and OFF for the per-primitive kinds, so each of those trials is exactly
    one attempt; a RECOVER the engine injects is charged to the trial in progress as engine_retries and never
    becomes a trial. `--policy hold` -> HoldPolicy (refused off mocks, R2); `--policy bundle PATH` raises
    NotImplementedError naming T-029; `--backend real` exits 2 before any driver is built. Fake clock on mocks
    unless --realtime.
  acceptance (`.venv/bin/python -m eval.run_eval --backend mock --kind move --n 20 --policy hold`, 8.5 s wall):
    printed `success 0/20 (0.0%)` then `timeout_no_progress 20`; wrote
    eval/results/20260911T221321_move-hold.json with 20 trial rows, summary {success 0, n 20, rate 0.0,
    by_failure_mode {timeout_no_progress: 20}, engine_retries 0, safety_refusals 0, duration_s 400.66},
    git_commit + config hashes for safety/robot/board/training + policy {tag hold, checkpoint null}.
    Row 0: duration_s 20.033, policy_calls 200, actions_sent 601, safety_refusals 0, stopped_by "timeout".
    0/20 is correct by construction: HoldPolicy commands no motion (R2) and MockPerception sees only the
    engine's own board state, so every primitive times out. No capability is claimed from this run.
  also measured: kind=sequence n=20 with recovery on -> 20 trials, 0/20, 2 engine_retries each (40 total),
    1201.98 s of loop time in 21.3 s wall; roll and recover n=4 -> 0/4 each; sequence n=19 refused.
  tests: `.venv/bin/python -m pytest tests/test_eval.py -q` 23 passed in 6.4 s (runs use a config/ copy with
    primitive_timeout_s 1.0 - a shorter fake clock, the same loop); suite 433 passed, 4 skipped in 85.7 s;
    ruff `check .` clean; committed through the pre-commit gate, no --no-verify (D-013).
  note: docs/README.md has no row for eval.md - outside this task's touch list, left for Fable.

## T-029  Diffusion Policy wrapper with goal channels, smoke-train, export, inference timing
status: accepted
priority: P1
phase: 3
owner: opus
depends_on: T-027
hardware: none
deliverables:
  - policy/diffusion.py: LeRobot 0.4.4 DiffusionPolicy configured for three cameras (ResNet-18 encoders), the 9-D state, the
    two goal heatmap channels concatenated to `top` (5-channel input; adapt the first conv), task one-hot appended to the state,
    chunk 16, DDIM 10 at inference; `DiffusionAdapter` implementing runtime.policy_api.Policy (receding horizon, execute 8 of 16)
  - policy/train.py: entry point (config from config/training.yaml, dataset sessions list, seed, steps, device) that logs the
    config hash and a dataset manifest hash (sha256 over the sessions' meta files) at start, saves checkpoints and a loss curve
    CSV under data/checkpoints/<run>/; a `--smoke` mode of 30 steps on the mock dataset on CPU
  - policy/export.py: checkpoint -> inference bundle (TorchScript if the model traces, else state_dict + config with a loader);
    the adapter loads the bundle
  - tests/test_diffusion.py: forward pass shapes; 30-step smoke train on a mock session with loss at step 30 below loss at step 1
    (print both); export round trip gives identical actions on one observation (1e-5); adapter act() latency on this laptop CPU
    at DDIM 10 printed (mean of 20 calls)
acceptance:
  - tests pass; smoke-train and export commands with their output in BUILD_LOG.md; inference latency recorded and compared to
    the 100 ms budget (CLAUDE.md 5.8 fallback ladder noted if above)
notes: Real training happens on Greennode (Q-001). No hardware. Keep the LeRobot modifications in policy/ (wrap, do not
  patch the package).
result:
  commit: 9320a49
  policy/diffusion.py (~470 lines) + policy/train.py (~230) + policy/export.py (~150) + tests/test_diffusion.py
    (10 tests) + docs/policy.md (+130 lines) + policy/__init__.py docstring + 4 new config/training.yaml
    `diffusion` keys (encoder_image_hw [240,320], down_dims, spatial_softmax_keypoints, stats_samples; no existing
    value changed, REQUIRED_KEYS untouched) + the `--policy bundle PATH` branch of eval/run_eval.py.
  route: lerobot 0.4.4 permits NEITHER declared-5-channel route. validate_features raises "we expect all image
    shapes to match" for a 5-channel `top` beside 3-channel cameras, and 5 channels on every camera dies in the
    stock torchvision conv1 ("expected input[1,5,240,320] to have 3 channels"). Both reproduced, both pinned by a
    test. So: a learned Conv2d(5,3,1) (identity on RGB, zero on the goal channels at init), the task one-hot
    concatenated onto the state (lerobot sees 12-D observation.state), and every camera resized to one encoder
    shape (240x320) because that same rule forbids two resolutions. Normalisation left the policy in 0.4.4
    (processor pipelines), so the stats live as buffers in the model's own state_dict and travel in the bundle.
  chunk: predict() samples conditional_sample directly and returns all 16 from index 0 (policy/dataset.py aligns
    the chunk at delta 0; lerobot's generate_actions slices from n_obs_steps-1 and returns 8). Controller plays 8.
  tests: 10 passed in 55.5 s; full suite 461 passed, 4 skipped in 180.1 s; ruff clean.
  smoke train (test scale, 48x64 encoder): loss step 1 0.9514 -> step 30 0.7704 (mean of last 10 0.7182); fixed
    probe (same batch, same seeded draw) 1.1723 -> 0.9746, -16.9%.
  smoke train (CLI, full model, data/raw/mock_smoke at 640x480): 293.0M params, 30 steps in 379.6 s on CPU, loss
    1.030969 -> 0.609529 (mean of last 10 0.706344); training config hash 862aafc738b931dd98e5f436c1b868eb18402f7
    c055e24a8a297daab65733605, dataset manifest a4a45245e0985001b1e25a2d40df0c4b9e274aa075f8c53fe39a7e4c089ac31d.
  export: bundle.json + weights.pt; torch.jit.trace SUCCEEDED with the noise passed in as an input and the graph
    verified against the eager model (max diff 0.0), but the adapter still loads the state_dict and the manifest
    says torchscript_used_at_inference: false (a traced diffusion loop bakes in batch size, image size and step
    count, and the file is 1.17 GB). Round trip: two adapters, same seed -> identical actions to 1e-5.
  latency (this laptop, CPU torch, 640x480/320x240 in, 240x320 encoder, 20 calls): DDIM 10 median 804 ms, mean
    1099 ms -> 8x over the 100 ms budget; DDIM 5 median 498 ms, mean 502 ms -> 5x over. The 5.8 fallback ladder
    does not close it: 10 Hz needs a GPU (CUDA torch or the Orin NX) or a smaller model. Prepare cost 8 ms.
  open for Fable (in BUILD_LOG): (1) the 10 Hz gap above; (2) training repeats the observation frame twice
    because LudoDataset yields one frame and obs_history is 2 -- needs a task on policy/dataset.py BEFORE any real
    training run; (3) EMA and the LR warmup are configured but not implemented; (4) /home has 12 GB free and one
    checkpoint+bundle is 2.3 GB.
  outside the touch list: tests/test_eval.py's "bundle is reserved for T-029" test asserted the exact
    NotImplementedError this task removes, so it was rewritten to pin the new error path, and the two matching
    lines in docs/eval.md with it.

## T-030  ACT baseline wrapper, same inputs
status: accepted
priority: P2
phase: 3
owner: opus
depends_on: T-029
hardware: none
deliverables:
  - policy/act.py: LeRobot ACTPolicy with the same observation adaptation as T-029, chunk 32, temporal ensembling in
    `ACTAdapter`; train.py `--policy act` path; export path
  - tests/test_act.py mirroring test_diffusion.py (shapes, 30-step smoke train, export round trip, latency)
acceptance:
  - tests pass with the printed numbers; BUILD_LOG.md commands
notes: CLAUDE.md 5.7: ACT is trained on every dataset the diffusion model is trained on. train.py must make that a one-flag
  change.
result:
  commit: b707913
  policy/act.py (new, ~400 lines: ACTSpec, TemporalEnsemble, GoalACTPolicy, ACTAdapter, latency CLI) + policy/
    train.py `--policy {diffusion,act}` (POLICIES map; train() dispatches on the spec type; run.json and
    checkpoint.pt record "policy"; every default from the chosen block) + policy/export.py (one export path for
    both, BUNDLE_FORMATS adds ludo-g1/act-bundle/1, new open_bundle() reader) + eval/run_eval.py (`--policy
    bundle PATH` now takes either model through open_bundle and records which) + tests/test_act.py (15 tests) +
    docs/policy.md (+90 lines) + config/training.yaml act keys.
  route: ACT is less accommodating than the Diffusion Policy. ACTConfig does not check image shapes, so a
    5-channel `top` is accepted by the config and then dies in the ONE shared ResNet-18 ("expected input[2,5,48,
    64] to have 3 channels") -- reproduced and pinned by a test -- so the same Conv2d(5,3,1) + task one-hot +
    one encoder size is the only route here too, and it is literally the same code (imported from
    policy/diffusion.py, not copied). ACTConfig also refuses n_obs_steps > 1, so T-029's "training repeats the
    observation frame" gap CANNOT occur for ACT: ACT can be trained on today's dataset before T-031 lands.
  temporal ensembling: setting temporal_ensemble_coeff in 0.4.4 forces n_action_steps=1 (select_action consumes
    one ensembled action per query), which is not our 10 Hz-query / 30 Hz-chunk contract. So the wrapped config
    keeps the coefficient None and TemporalEnsemble averages over the chunk with lerobot's own weights
    w_i = exp(-coeff*i), oldest first, over every live prediction of each action-step, at stride
    round(action_hz/policy_hz)=3. Pinned against ACTTemporalEnsembler at stride 1: 2.87e-08 as lerobot ships
    (float32 weight table) and 2.22e-16 with the same weights in float64. act() returns 16, chunk 32 is internal,
    so the controller contract of 5.2 is unchanged.
  tests: full suite at the pre-commit gate 490 passed, 4 skipped in 188.84 s, ruff clean (475 passed in 178.78 s
    at worktree setup: 15 new tests for about 10 s). Earlier readings of 390 s for tests/test_act.py and 747 s for
    the suite were taken under load 13-28 from another builder's suite in the main tree.)
  smoke train (test scale, 48x64 encoder, dim_model 64): loss step 1 23.4603 -> step 30 0.9738 (mean of last 10
    0.8906); fixed probe (same batch, same seeded VAE draw) 22.7715 -> 0.7060, a 96.9% reduction.
  smoke train (CLI, full model, data/raw/mock_smoke at 640x480 -- T-029's session, same dataset manifest
    a4a45245e0985001b1e25a2d40df0c4b9e274aa075f8c53fe39a7e4c089ac31d): 51.6M params (vs 293.0M), 30 steps in
    1124.9 s under load, loss 69.791473 -> 8.221072 (mean of last 10 8.161582); training config hash
    42b976e662597e9d2c1cf0d0725698523abb81e20a79ce130fb2201ae4ff144d.
  export: bundle.json + weights.pt; torch.jit.trace SUCCEEDED with no noise input (ACT is deterministic at
    inference: the VAE encoder runs in training only), max diff vs eager 0.0, torchscript_used_at_inference still
    false. Round trip: two adapters -> identical actions to 1e-12.
  latency (this laptop, CPU torch, 640x480/320x240 in, 240x320 encoder): the default 14 torch threads
    OVERSUBSCRIBE ACT -- first reading 5730 ms mean, repeats 2282 and 453 ms median. Thread sweep on one model:
    1 -> 439, 2 -> 340, 4 -> 181, 8 -> 953, 14 -> 6091 ms median. Measured side by side in one script in the same
    minutes: at 4 threads ACT 154 ms vs diffusion DDIM 10 560 ms and DDIM 5 373 ms; at 14 threads ACT 3666 ms vs
    863 / 730 ms. So ACT at 4 threads is 1.5-1.8x over the 100 ms budget, the closest anything here has come, and
    the thread count is a bigger lever than the 5.8 fallback ladder (D-019). All under load; pessimistic.
  open for Fable (in BUILD_LOG): (1) torch thread count is unconfigured and worth 30x on ACT -- wants a compute
    key and a measurement on a quiet machine; (2) ACT can train before T-031; (3) ACT and diffusion loss values
    are on different scales (KL term) and must never be compared as numbers, only eval success rates;
    (4) an ACT smoke run costs 621 MB against the diffusion policy's 3.5 GB (deleted, records kept).
  deviation: config/training.yaml act.encoder_per_camera was REMOVED (not just added to). lerobot's ACT has one
    shared backbone and no such option, so the key promised a feature no code can honour; the comment in its
    place says so. No other existing value changed; REQUIRED_KEYS untouched; unmeasured("training") still empty.

## T-031  Greennode training launch for real: train.py inside the pinned image, manifest and checkpoint round trip
status: accepted
priority: P2
phase: 3
owner: opus
depends_on: T-029, T-009
hardware: none
deliverables:
  - cloud/Dockerfile updated with the lerobot/torch pins from requirements.txt (GPU image tag chosen and recorded); cloud/
    greennode.sh `train` wired to policy/train.py with the dataset manifest and config hashes echoed into the job log
  - local-transport end-to-end test: `up`, `train policy/train.py --smoke`, `down` yields a checkpoint under data/checkpoints/
    with the two hashes in its run.json (test)
acceptance:
  - local-transport test passes; the exact remote command is in docs/cloud.md; the real run waits for Q-001 and is listed in
    STATE.md as the Phase 3 gate
notes: No credentials in git.

result: (opus, 2026-09-12T03:20+07:00, commit 948850c)
  - local-transport end to end: `up` -> `train policy/train.py --sessions data/raw/<mock> --smoke` -> `down`, in
    tests/test_greennode_train.py (6 tests, 31.4 s). The job exits 0 after ~27 s, 24 frames, 38.4 M parameters at the
    test scale; the returned run.json carries dataset_manifest_sha256 776b5083... and all six config hashes, and its
    training hash is the PUSHED config's, not the laptop's (the test shrinks the pushed config, so a laptop-config hash
    would mean the job imported the wrong tree). checkpoint.pt measured at 153.7 MB and deleted by the fixture, as are
    the mock session and the job logs (Q-002); the configured scale would be ~2.3 GB (T-029).
  - cloud/Dockerfile: nvidia/cuda:12.8.1-cudnn-runtime-ubuntu22.04 pinned by digest sha256:17e2934e1fa9..., jammy
    python3.10, ffmpeg (torchcodec), installing the new cloud/requirements-train.txt: torch 2.9.1+cu128 /
    torchvision 0.24.1+cu128 (the versions requirements.txt pins, CUDA builds), lerobot 0.4.4, numpy, pyyaml, structlog,
    scipy, opencv-python-headless; pxdex and unitree_sdk2py deliberately absent. Image tag 0.1.0 -> 0.2.0. UNBUILT:
    `command -v docker` prints nothing here, so the image is checked by reading plus two tests (pin agreement with
    requirements.txt; the Dockerfile lint).
  - cloud/greennode.sh: PYTHONPATH for the job (without it `python policy/train.py` cannot import the pushed tree),
    `docker run --shm-size` (GREENNODE_SHM_SIZE, default 8g) for DataLoader workers, and the run's config/manifest
    hashes echoed onto stdout after a waited job.
  - docs/cloud.md: the exact remote command is under "Training for real (Phase 3)"; the run stays blocked on Q-001.
  - full suite 496 passed / 4 skipped in 206 s; ruff clean; committed through the pre-commit gate.
  - NOT DONE (Fable's file, outside the touch list): the STATE.md line naming this as the Phase 3 gate; proposed
    wording is in agents/BUILD_LOG.md T-031.

## T-032  Teleop loop on mocks: pose and glove in, IK, Guard, arm and hand out, recorder and UI attached
status: accepted
priority: P1
phase: 2
owner: opus
depends_on: T-013, T-017, T-025
hardware: none
deliverables:
  - teleop/loop.py: `TeleopLoop(pose_driver, glove_driver, arm, hand, cameras, engine, recorder, ui, clock)` running at the
    30 Hz grid the recorder hands out (Recorder.next_grid_ns): read the wrist pose (PoseDriver) and the glove (GloveDriver),
    transform with teleop.retarget.pico_to_g1_base, solve teleop.retarget.ArmIK from the arm's measured state, build the
    MotionCommand (8 joints + pinch_from_glove), send through arm.send_targets and hand.send_pinch (which admit via the
    Guard), hand the admitted command to the recorder as the action, and feed the operator UI; a SafetyViolation is counted,
    logged and the tick continues; a `--backend mock --seconds N` CLI
  - tests/test_teleop_loop.py on mocks with a fake clock: 30 s run holds 30 Hz +/- 0.5 measured on the fake clock; the mock arm
    state follows the IK target (steady-state error under 0.02 rad after the lag settles, print it); a recorded episode's
    action rows equal the admitted commands; an out-of-box pose from the mock pose driver produces Guard refusals and no state
    change; IK solve time per tick measured (mean, p99)
  - docs/teleop.md updated with the loop and the Phase 2 real wiring still missing (T-020, T-021)
acceptance:
  - tests pass with the printed rate, error and timings; full suite green; ruff clean
notes: No hardware; the real pose/glove/arm/hand drivers arrive in Phase 1 and slot into the same constructor. This is the
  path that will produce every training episode, so keep it small and obviously correct (under 250 lines).
result: (opus, 2026-09-13T14:40+07:00, commit f59f4b2)
  - `.venv/bin/python -m pytest tests/test_teleop_loop.py -q` -> 14 passed. `.venv/bin/python -m pytest -q` ->
    465 passed, 4 skipped (451 before). `.venv/bin/ruff check .` -> "All checks passed!". PASS
  - 30 s run on mocks, fake clock: 901 ticks in 30.033 s = 30.000 Hz (budget 30 +/- 0.5), 901 admitted,
    0 refused, printed by the test. PASS
  - arm follows the IK target: |state - last admitted target| = 0.00274 rad, worst of the 8 joints, mock arm
    tau 0.08 s (budget < 0.02 rad), printed. PASS
  - ArmIK.solve per tick: mean 0.598 ms, p99 1.028 ms over 901 ticks (one 30 Hz period is 33.3 ms), printed. PASS
  - out-of-box pose (the shipped mock circle, centred on the pelvis), 2 s: 61 ticks, 0 admitted, 61 refused
    (workspace_box 37, joint_velocity 24), arm state still exactly the zero rest pose, guard.admitted == 0,
    0 frames recorded. PASS
  - recorded episode (3 s, recorder + operator UI): every `action` row is a command the guard admitted, in the
    order it admitted them; 0 refused commands recorded. PASS
  - `python -m teleop.loop --backend mock --seconds N` runs and prints rate, sends, frames, refusals by rule and
    the IK timings; `--backend real` exits 2 from drivers.make. PASS
  - teleop/loop.py is 248 lines (notes asked for under 250). PASS
  - deviation logged in BUILD_LOG: the loop passes `GloveSample.pinch` through instead of calling
    `pinch_from_glove`, because the tip-to-tip distance that function needs is the Phase 1 glove driver's to
    compute (docs/teleop.md, T-020); `config/robot.yaml` gained `mock.pose_center_m` (default [0,0,0], no
    behaviour change) so the one mock pose driver can be placed inside or outside the box.
  - finding logged in BUILD_LOG: teleop has no clutch, so the first command of a session steps 0.443 rad in one
    tick (allowed only by the command_gap_reset_s reference) or is refused 61 ticks running; proposed as a
    follow-up task to be accepted before the first Phase 1 motion session.

## T-033  Clutch and first-command step cap before any hardware motion (D-018)
status: accepted
priority: P0
phase: 1
owner: opus
depends_on: T-032, T-005
hardware: none
deliverables:
  - runtime/safety.py: a new rule `first_command_step`: when the velocity reference is "fresh" (no accepted command within
    command_gap_reset_s), the per-joint |target - measured state| must not exceed config/safety.yaml `first_command_max_step_rad`
    (new key, placeholder 0.05 rad with `_status: UNMEASURED` and the R3 comment; this is a tightening, allowed for an agent);
    SafetyViolation names the rule and the worst joint; the velocity rule is unchanged for subsequent commands
  - teleop/loop.py: a clutch. States disengaged -> engaging -> engaged. Disengaged: every tick sends the arm's own measured
    state as the target (a hold, no motion) and the IK still runs so the operator sees the error. Engaging (operator key from
    config/training.yaml operator_ui.keys, `e`): only allowed when the IK target is within `clutch_engage_tolerance_rad` (new
    key in config/robot.yaml teleop block, placeholder 0.05 rad, UNMEASURED) of the measured state on every joint; then blends
    target = state + alpha*(ik - state) with alpha ramping 0 -> 1 over `clutch_ramp_s` (placeholder 1.0 s). Engaged: full IK.
    Any Guard refusal for the arm while engaged disengages (the operator re-engages deliberately). The UI shows the clutch
    state and the per-joint distance to engage.
  - tests: the T-032 "engage steps 0.443 rad" measurement now fails at the Guard with rule first_command_step (test asserts
    the rule name and that the arm did not move); a clutch engage from rest on the mock circle passes only after the mock pose
    is moved within tolerance (test drives the pose there), and the first admitted command after engage is under
    first_command_max_step_rad on every joint (print it); a refusal while engaged disengages (test)
  - docs/safety.md and docs/teleop.md updated
acceptance:
  - tests pass with the printed numbers; full suite green; ruff clean; `git diff config/safety.yaml` shows only the added key
    and its status/comment (Fable checks that nothing was loosened)
notes: T-021 (first real motion) now depends on this task. Also add `mock.pose_center_m` to the MockPose row in docs/drivers.md
  (left over from T-032).
result: (opus, 2026-09-12T00:35+07:00, commit 6a8b7d8)
  - `.venv/bin/python -m pytest tests/test_safety.py -q -s` -> 61 passed. `tests/test_teleop_loop.py` -> 23 passed.
    `tests/test_operator_ui.py` -> 14 passed. `tests/test_mock_drivers.py` -> 48 passed. Full suite through the
    pre-commit hook -> 490 passed, 4 skipped in 696 s (465 passed before this task). `.venv/bin/ruff check .` -> "All checks passed!". PASS
  - `git diff --stat config/safety.yaml` -> `1 file changed, 8 insertions(+)`: only first_command_max_step_rad,
    its `_status: UNMEASURED` sibling and the R3 comment. Nothing loosened (the fresh-reference allowance went
    from joint_velocity_limit_rad_s * command_gap_reset_s = 0.75 rad to 0.05 rad). PASS
  - the T-032 engage, handed straight to the arm: the IK target is 0.443 rad from the measured state; the guard
    refuses it with rule `first_command_step` naming left_shoulder_pitch_joint, guard.admitted == 0 and the arm
    is still exactly at the zero rest pose. Printed by the test. PASS
  - first admitted command after engaging the clutch: worst joint 0.000709 rad against the 0.05 rad cap (the key
    lands between two ticks, so alpha is one 30 Hz period into the 1 s ramp); the worst single tick of the whole
    ramp is 0.004801 rad. Printed. PASS
  - the clutch engages only from a pose the arm is in: on the mock circle drawn through the rest wrist pose the
    IK target is 0.0188 rad away and `e` engages; on the T-032 circle it is 0.434 rad away and `e` is refused,
    47 holds sent, 0 refused, the arm never leaves 0.0 rad. Printed. PASS
  - a refusal while engaged disengages: one `command_rate` refusal -> clutch `disengaged`, the loop keeps
    sending holds and never re-engages itself. Printed. PASS
  - 0.5 s holding + 30 s engaged on mocks: 917 ticks in 30.567 s = 30.000 Hz, 917 admitted, 0 refused, tracking
    error 0.00283 rad, IK mean 0.344 ms / p99 0.513 ms, worst commanded step 0.164 rad/s. PASS
  - out-of-box (shipped) circle, 2 s: 61 ticks, 61 holds admitted, 0 refused, arm unmoved; the test also asserts
    independently that the IK target's wrist ([0.101, 0.0, 0.001] m) is outside the box and that the clutch
    refuses to engage into it. PASS (the T-032 "61 refused" number is now "61 never sent")
  - deviations logged in BUILD_LOG: (1) tests/test_mock_drivers.py, tests/test_recorder.py and
    tests/test_operator_ui.py needed their first commands to engage from the measured state, so three files
    outside the task's touch list changed (none of them the parallel T-030 worktree's); (2) teleop/loop.py is
    369 lines against "under 300" with no docstring cut, the sibling module D-013 prefers not being in the file
    list; (3) teleop/operator_ui.py also binds the `e` key to clutch.request_engage(), which is the only way the
    configured key can reach the clutch; (4) runtime/config.py REQUIRED_KEYS does not list the new safety key
    (that file is outside the touch list).

## T-034  Observation history in the dataset (n_obs_steps frames per sample)
status: accepted
priority: P1
phase: 3
owner: opus
depends_on: T-029
hardware: none
deliverables:
  - policy/dataset.py: samples carry `n_obs_steps` observation frames (from config/training.yaml diffusion.obs_history, ACT
    obs_history) using lerobot delta_timestamps on the observation keys, padded at episode start with the first frame and a
    mask; goal channels rendered once per episode and repeated; the policy wrappers consume the history instead of repeating
    one frame (T-029 finding 2)
  - tests: shapes (n_obs_steps, C, H, W), start-of-episode padding, benchmark re-run (samples/s before and after)
  - policy/_shared.py: the feeding, normalisation and measurement helpers policy/act.py currently imports privately from
    policy/diffusion.py, promoted and imported by both (T-030 review)
acceptance:
  - tests pass; policy/diffusion.py smoke test still passes with the history input; numbers in BUILD_LOG.md
notes: Before any real training run.

result: (opus, 2026-09-12T02:05+07:00, commit 192ac7d)
  - policy/dataset.py: `n_obs_steps` (default 1, explicit per policy). Above 1 the three camera keys and
    observation.state carry lerobot delta_timestamps [-(S-1)/fps ... 0], so a sample is (S, 5, h, w) / (S, 3, h, w)
    / (S, 9) oldest first, plus a new `obs_mask` (S,) that is 0 where lerobot clamped to the episode's first frame.
    At 1 there is no step dimension (an ACT sample is unchanged). Goal channels: still one render per episode,
    repeated over the history; task_id keeps no step dimension. One augmentation draw per sample, not per frame.
  - policy/train.py passes spec.n_obs_steps: 2 for the Diffusion Policy (diffusion.obs_history), 1 for ACT via the
    new constant ACTSpec.n_obs_steps property; ACTSpec.from_config refuses an act.obs_history other than 1
    (ConfigError). run.json records n_obs_steps.
  - policy/diffusion.py: DiffusionAdapter._batch() queues, pads-left and stacks oldest first -- the dataset's rule.
    tests/test_diffusion.py::test_the_adapter_queue_and_the_dataset_history_agree feeds two consecutive
    Observations built from a recorded episode and asserts exact tensor equality with the dataset's two-frame
    sample at that frame, padded first call included (obs_mask [0, 1]).
  - policy/_shared.py (new, 266 lines): image_tensor, observation_frame, with_steps, Normalizer, dataset_stats,
    benchmark, synthetic_observation, IMAGE_KEYS/EPS/BUNDLE_FILE/WEIGHTS_FILE, imported by both wrappers;
    policy/diffusion.py re-exports what policy/export.py imports from it. No private cross-imports remain (a test
    asserts it). Defect fixed in the move: dataset_stats divided every camera by `top`'s pixel count, scaling the
    palm mean/std by 4 at the configured sizes; each camera now counts its own pixels.
  - Benchmark (1000 samples, batch 8, 64x48 mock frames, 1 torch thread), samples/s at num_workers 0 / 2:
    before 90 / 143; after at n_obs_steps=1: 80 / 148; at n_obs_steps=2: 45 / 83 (six PNG decodes per sample
    instead of three).
  - Diffusion smoke train on real history: loss 0.9677 -> 0.7615 (mean last 10 0.7205), fixed probe 1.1773 ->
    0.9804 (-16.7%); T-029 on the repeated frame was 0.951 -> 0.770, -16.9%. ACT smoke unchanged: 23.08 -> 0.97.
  - ruff clean; tests/test_dataset.py 24 passed (18 before), tests/test_diffusion.py 11 (10), tests/test_act.py 16
    (15); full suite 519 passed, 4 skipped in 947 s (under another builder's load; 511 before).
  - config/training.yaml: comments only on diffusion.obs_history and act.obs_history, no value changed (the
    training config hash is taken after parsing, so it is unchanged). runtime/policy_api.py untouched.

## T-035  Training loop completeness and a smaller inference configuration
status: accepted
priority: P1
phase: 3
owner: opus
depends_on: T-034
hardware: none
deliverables:
  - policy/train.py: EMA of weights (used for eval/export), LR warmup + cosine, a held-out validation split by cell pairs
    (policy.dataset.split_cell_pairs) with validation loss every N steps written to loss.csv (section 8 wants the curves saved),
    checkpoint of the EMA weights, resume from checkpoint
  - a `diffusion_small` config block: shared encoder across cameras, 120x160 inputs, reduced UNet width; latency measured
    with the T-029 adapter at DDIM 10 and 5 on this laptop CPU (D-019 fallback)
  - tests: EMA changes the exported weights; warmup schedule values at steps 0, warmup, end; validation split disjoint from
    train pairs; small-config latency printed
acceptance:
  - tests pass with printed numbers; BUILD_LOG.md has the small-config latency next to the T-029 numbers
notes: No hardware. Disk: keep checkpoints under tmp_path in tests and delete smoke weights after measuring (Q-002). D-020:
  add config/training.yaml `compute.torch_threads` (placeholder 4, UNMEASURED), applied at start by policy/train.py, both
  adapters and eval/run_eval.py; re-measure ACT and diffusion (DDIM 10 and 5) act() at 1/2/4/8 threads with no other builder
  running (check `uptime` load < 2 before measuring, record it) and put the table in BUILD_LOG.md and docs/policy.md.
result: (opus, 2026-09-12T04:35+07:00, commit 25deca1)
  - policy/train.py: EMA of the parameters (`ema_decay`, ramped decay `min(d, (1+n)/(10+n))`) stored as
    `ema_state_dict`; linear warmup then cosine (`warmup_steps` clamped to a tenth of the run, `lr_min_ratio`),
    applied factor written to a new `lr` column; a validation split by cell pair (`--val-fraction`, ROLL kept in
    training) whose loss every `val_every` steps is a new `val_loss` column; `--stop-after N` + `--resume` restoring
    weights, optimiser, EMA, step, loss history and RNG, with the batch stream positioned by step (`_StepSampler`);
    `--config-block NAME`. policy/export.py exports the EMA by default (`--raw` for the last step's weights,
    `bundle.json.weights_source`). policy/_shared.py `set_torch_threads` applied once per process by train.py,
    run_eval.py and both adapters.
  - `.venv/bin/python -m pytest tests/test_train.py -q -s` -> 15 passed in 59 s. tests/test_diffusion.py +
    tests/test_act.py -> 27 passed in 64 s. tests/test_greennode_train.py -> 6 passed. tests/test_eval.py +
    tests/test_config.py -> 93 passed. `ruff check .` -> All checks passed. Full suite through the pre-commit
    hook -> 556 passed, 7 skipped in 795 s (533 passed before this task). PASS
  - printed numbers: lr multiplier 0.2000 / 1.0000 / 0.0000 at steps 0 / warmup 5 / 106; EMA vs last-step weights
    after 10 steps 6.375e-05 over 341 tensors and a different `weights_sha256`; validation loss on 12 held-out
    frames 0.9179 -> 0.8816 with the held-out pair absent from training and the ROLL episode present; resume
    10+10 vs 20 straight, largest |loss difference| **0.000e+00**. PASS
  - `diffusion_small` (30.4 M params, 120x160, one shared encoder), trained bundle, 20 CLI calls at the configured
    frame sizes: **DDIM 10 median 80 ms, DDIM 5 median 56 ms**, both **within** the 100 ms budget, against T-029's
    293 M-parameter 804 ms / 498 ms. PASS
  - thread sweep at 1-minute load 2.80 (1/2/4/8) and 0.94 (12/14), 20 calls per cell, medians in ms:
    ACT 502 / 265 / 149 / **91** / 91 / 250; diffusion DDIM 10 1746 / 917 / 541 / **454** / 397 / 1057; DDIM 5
    1188 / 620 / 361 / **281** / 247 / 507; small DDIM 10 264 / 161 / 101 / **71** / 77 / 128; small DDIM 5
    215 / 123 / 75 / **53** / 53 / 91 at 1 / 2 / 4 / 8 / 12 / 14 threads. `compute.torch_threads: 8` (measured,
    not UNMEASURED: `unmeasured("training")` stays empty). Table in BUILD_LOG.md and docs/policy.md. PASS
  - findings for Fable (BUILD_LOG): ACT is inside the 100 ms budget at 8 threads (91 ms), so D-019's ladder reaches
    the laptop; a checkpoint is now 4x the parameters on disk (614.8 MB at the greennode test scale, ~4.7 GB at the
    configured one, Q-002); a crashed run still leaves nothing to resume from (`--checkpoint-every` not added);
    `--stop-after` was added so the resume acceptance could be written honestly (disagreement logged).
  - outside the touch list: one header assertion in tests/test_greennode_train.py and one measured file size in
    docs/cloud.md, both false after the loss.csv columns and the checkpoint contents changed.

## T-036  Periodic checkpoints, crash resume, checkpoint pruning and a disk guard
status: accepted
priority: P1
phase: 3
owner: opus
depends_on: T-035
hardware: none
deliverables:
  - policy/train.py: `--checkpoint-every N` writing checkpoint.pt atomically (temp then rename) so a crash mid-write leaves the
    previous one; `--keep-last K` pruning older step checkpoints (the EMA export bundle is never pruned); a disk guard that
    refuses to start (clear message naming Q-002) when free space under data/checkpoints is below `disk_guard_factor` (config,
    default 2) times the estimated checkpoint size (parameters x 4 x 4 bytes, printed)
  - tests: crash simulation (kill the process after step N, resume from the last periodic checkpoint, loss sequence matches the
    straight run); pruning keeps exactly K; the disk guard triggers on a mocked statvfs
acceptance:
  - tests pass with printed numbers; docs/policy.md and docs/cloud.md updated (cloud train passes --checkpoint-every)
notes: Disk is 12 GB free; every test writes under tmp_path and deletes weights.
result: (opus, 2026-09-12T06:40+07:00, commit 0916544)
  - tests/test_train.py 24 passed in 83 s (15 before); ruff clean; tests/test_act.py + tests/test_diffusion.py 27 passed in
    50 s; tests/test_greennode_train.py + tests/test_config.py 76 passed in 28 s.
  - Crash simulation: `python -m policy.train --checkpoint-every 2 --keep-last 1 --fault-at-step 5` in a subprocess dies at
    step 5 of an 8-step run leaving exactly checkpoint.pt + checkpoint_step4.pt (step 2's pruned, both names one inode, no
    .tmp, no run.json); resuming from it and running to step 8 reproduces the straight 8-step run's loss sequence with a
    largest difference of 0.000e+00.
  - Pruning: keeps exactly K, deletes oldest first, and leaves checkpoint.pt, run.json and a bundle/ directory alone (K=2
    then K=0 asserted on an exact file set); the real loop's directory at --checkpoint-every 2 --keep-last 1 over 6 steps is
    exactly checkpoint.pt, checkpoint_step4.pt, loss.csv, run.json.
  - Atomicity: torch.save made to die after writing the temp file leaves the previous checkpoint.pt loadable.
  - Disk guard on a mocked shutil.disk_usage (which reads os.statvfs): 293 M parameters, estimate 5.16 GB, factor 2 asks
    10.31 GB, mocked free 7.74 GB -> DiskGuardError naming Q-002, the estimate and the free space; --no-disk-guard logs and
    continues; a real train() refuses before writing anything (empty run directory).
  - Printed estimates: diffusion 293.0 M -> 5.16 GB (guard needs 10.32 GB), act 51.6 M -> 0.91 GB, diffusion_small 30.4 M ->
    0.54 GB; measured against the real file at 16.0 M parameters: 256.8 MB written, 282.1 MB estimated (1.10x).
  - config/training.yaml compute.checkpoint_every 1000, compute.keep_last 2, compute.disk_guard_factor 2 (REQUIRED_KEYS
    untouched); docs/policy.md "Surviving a crash" section; docs/cloud.md Phase 3 command now passes
    --checkpoint-every 1000 --keep-last 2; cloud/greennode.sh header example shows the same.

## T-037  Progress watchdog and per-trial failure logging in the controller (CLAUDE.md Phase 5 hardening, on mocks)
status: accepted
priority: P1
phase: 5
owner: opus
depends_on: T-016, T-028
hardware: none
deliverables:
  - runtime/controller.py: a progress watchdog distinct from the primitive timeout: every `watchdog_interval_s` (config) it asks
    perception for a cheap progress signal (`Perception.progress(command, before, now) -> float in [0,1]`, MockPerception
    returns 0 unless the mock board changed); if the signal has not increased for `watchdog_stall_s` (20 s per section 6.5) the
    primitive is halted with failure_mode `policy_stalled` and reported to the engine; the arm receives a hold (its own
    measured state) through the Guard when a primitive is halted, never a scripted retreat
  - a per-trial failure log line (JSON) under data/logs/ with command, failure_mode, duration, actions sent, refusals, watchdog
    verdict, consumed by eval/run_eval.py so the eval JSON and the controller log agree
  - tests on mocks: HoldPolicy stalls and the watchdog halts at 20 s +/- 0.2 on the fake clock with failure_mode
    policy_stalled; a mock policy that changes the mock board keeps the watchdog quiet; the eval JSON's by_failure_mode counts
    policy_stalled once per stalled trial
acceptance:
  - tests pass; `eval.run_eval --backend mock --kind move --n 5 --policy hold` now reports policy_stalled (command and output
    in BUILD_LOG.md)
notes: R2: the hold on halt is the measured state, not a pose. Keep the watchdog inside controller.py's tick, no threads.
result: (opus, 2026-09-12T05:10+07:00, commit 0b0e437)
  - runtime/controller.py: `Watchdog` sampled inside the loop's own tick (no thread): once per
    `runtime.watchdog_interval_s` it reads the new `Perception.progress(command, before, now) -> [0, 1]`,
    remembers the last increase, and `stalled()` is true `runtime.watchdog_stall_s` after it. The loop tests
    `stalled()` *before* its own deadline, so a primitive that reached both ends at once is reported as the
    stall. A halt stops policy actions, sends one `hold()` (the arm's and hand's own measured state, through
    `send()` -> the Guard; a reading, never a pose, R2), waits one action slot, and reports
    `Outcome(False, delta, "policy_stalled")` to the engine, which retries as for any other failure.
  - board/perception.py: `FailureMode.POLICY_STALLED` added beside the unchanged `TIMEOUT_NO_PROGRESS` (the
    watchdog's verdict during the primitive vs perception's after it); `MockPerception.progress` is the share
    of the board that differs from the primitive's start, hence 0 whenever the engine's board did not change.
  - runtime/run_report.py (new): `RunSummary` moved out of controller.py unchanged, plus `Mark` (per-command
    counter snapshot and difference) and `TrialLog`, which writes one JSON line per execution to
    `data/logs/controller_<session>.trials.jsonl` (command, success, failure_mode, stopped_by, duration,
    actions, policy calls, refusals, watchdog verdict, delta) and `read_trials` to read it back.
  - eval/run_eval.py: `cross_check_log` compares that log with the runner's own rows (line count, the file on
    disk, and each deciding execution's success/stopped_by/failure mode) and raises rather than writing a
    result that disagrees; the JSON gains `controller_log: {path, records, by_failure_mode, agrees}`.
  - config/training.yaml: `runtime.watchdog_interval_s: 1.0`, `runtime.watchdog_stall_s: 20.0` (6.5 verbatim).
    REQUIRED_KEYS untouched. docs/controller.md and docs/eval.md updated with both deadlines, the log schema
    and the cross-check.
  - Measured: with the timeout moved to 40 s in a config copy and the stall at its configured 20 s, the
    primitive is halted at **20.033 s** on the fake clock (`stopped_by="watchdog"`, `timeout=0`, verdict
    `{stalled: true, samples: 20, progress: 0.0, s_since_increase: 20.033}`, failure_mode `policy_stalled`)
    -- inside +/- 0.2 s. A test engine whose board changes every 5 s keeps the watchdog quiet: the same
    primitive then runs to its 40 s timeout with `{stalled: false, progress: 0.41}` and `missed_cell`.
  - `.venv/bin/python -m eval.run_eval --backend mock --kind move --n 5 --policy hold` -> `success 0/5 (0.0%)`,
    `policy_stalled 5`, `controller_log.agrees: true` with 5 records; written
    eval/results/20260912T044558_move-hold.json. `python -m runtime.controller --backend mock --seconds 60`
    -> 3 commands, `policy_stalled=2, timeout_no_progress=1`, `run_deadline=1, watchdog=2`, 9.98 Hz / 29.89 Hz,
    0 refusals.
  - tests/test_controller.py 30 passed, tests/test_eval.py 26 passed, `ruff check .` clean. Full suite through
    the pre-commit hook -> 596 passed, 10 skipped in 311 s (586 passed before this task). PASS

## T-039  Network engine client for the real game engine (contract of CLAUDE.md 5.5 over a socket)
status: accepted
priority: P2
phase: 5
owner: opus
depends_on: T-007
hardware: none
deliverables:
  - engine/net.py: `NetEngineClient(url)` implementing EngineClient over ZeroMQ REQ/REP (pyzmq is already a transitive
    dependency; if not, use a plain TCP JSON-lines protocol and add nothing) with a JSON schema for Command, Outcome and
    board_state in docs/engine.md; `engine/serve_stub.py`: serves StubEngine on the same protocol so the controller can run
    against a "remote" engine today; timeouts and reconnect rules stated
  - tests: client against the served stub in a subprocess: 50 commands round trip identical to the in-process stub with the
    same seed; a dropped server yields a clear EngineUnavailable, not a hang
acceptance:
  - tests pass; `runtime.controller --backend mock --engine zmq://127.0.0.1:5555 --seconds 20` works against serve_stub
    (command and output in BUILD_LOG.md)
notes: This is the integration point the engine team will target; keep the schema in one place and versioned.
result: (opus, 2026-09-12T07:10+07:00, commit ae3d65a)
  - Protocol: **TCP JSON-lines**, not ZeroMQ. `.venv/bin/python -c "import zmq"` -> ModuleNotFoundError, and T-039 adds no
    dependency, so the task's stated fallback applies and the acceptance URL is `tcp://127.0.0.1:5555` (a `zmq://` URL is
    accepted as a synonym for the same protocol). Schema in one place and versioned: engine/schema.py, `{"v": 1, ...}`.
  - Built: engine/schema.py (wire format + the one dispatch), engine/net.py (`NetEngineClient`, `EngineUnavailable`),
    engine/serve_stub.py (`StubServer` + `python -m engine.serve_stub --bind --seed --script`), `--engine` on
    runtime.controller wired through `build()`, an `engine:` block in config/training.yaml (url, request_timeout_s 2.0,
    connect_timeout_s 2.0), the schema and the timeout/reconnect rules in docs/engine.md, `--engine` in docs/controller.md.
  - `.venv/bin/python -m pytest tests/test_engine_net.py -q` -> 43 passed in 4.15 s. PASS
  - 50 commands round trip identical: `test_fifty_commands_over_a_subprocess_match_the_in_process_stub` drives StubEngine(7)
    in process against the same seed served by a `python -m engine.serve_stub` subprocess; all 50 compared by
    (primitive, src.id, dst.id, horse_id) and by full dataclass equality, plus both `board_state()` dicts. PASS
  - Dropped server: `test_a_dropped_server_raises_engine_unavailable_and_does_not_hang` kills the server subprocess; the next
    call raised EngineUnavailable in < 2.5 s (2.0 s budget) naming the URL, and the following call reconnected and reported
    "cannot connect". A server that accepts and never answers raises inside its 0.5 s budget. PASS
  - Acceptance run: `.venv/bin/python -m engine.serve_stub --bind 127.0.0.1:5555 --seed 0` then
    `.venv/bin/python -m runtime.controller --backend mock --engine tcp://127.0.0.1:5555 --seconds 20` -> exit 0,
    "commands executed 1 (roll=1), 0 success 1 failure, failure modes policy_stalled=1, stopped by watchdog=1,
    policy calls 199 = 9.92 Hz, actions sent 595 = 29.66 Hz, safety refusals 0, alignment failures 0"; the server logged 24
    requests for that command (1 next_command + 1 report + 2 + 20 watchdog board_state). Full output in BUILD_LOG.md. PASS
  - Regression: `pytest tests/test_controller.py tests/test_engine_stub.py tests/test_config.py tests/test_eval.py -q` ->
    156 passed; `.venv/bin/ruff check .` -> All checks passed!; full pre-commit suite on the commit -> 703 passed,
    14 skipped (all hardware-absent or no-session) in 402.75 s, no --no-verify.
  - Gaps (BUILD_LOG): the `engine:` block is not in docs/config.md and not in `REQUIRED_KEYS`, both files being outside this
    task's touch list for the training.yaml branch; it sits exactly where the existing `runtime:`/`recorder:` blocks do.

## T-038  Board perception from the top camera on synthetic images (placeholder until the engine team delivers)
status: accepted
priority: P1
phase: 2
owner: opus
depends_on: T-008, T-016
hardware: none
deliverables:
  - board/perception.py `TopCameraPerception`: given a board calibration (board/calibration.py) and a `top` frame, detects
    horses as coloured square blobs per colour from config/board.yaml (HSV ranges UNMEASURED placeholders), assigns each to
    the nearest cell centre within a radius, flags a horse "between cells" or "fallen" (aspect ratio / area rule, placeholders),
    detects the die presence in the bowl region (config); `verify(command, before, after)` produces the Outcome and the
    section 6.5 failure mode from the before/after cell occupancy; `progress()` for the watchdog from partial motion
  - a synthetic renderer in tests (reuse tests/test_calibration.py's board image) that draws horses at known cells and the
    variants (fallen, between cells, missing)
  - tests: occupancy recovered exactly on 20 random boards; each 6.5 variant yields its failure mode; verify() on a MOVE that
    happened vs did not happen; runs under 30 ms per frame at 640x480 (print)
acceptance:
  - tests pass with the printed timing; docs/board.md updated; the real-still check waits for H-001 and says so
notes: Placeholder rules only; the engine team's perception replaces this module behind the same Protocol.
result: (opus, 2026-09-12T07:05+07:00, commit e23b56e)
  - board/perception.py +TopCameraPerception (999 lines total; MockPerception/FailureMode/state_delta unchanged);
    board/synthetic.py new (381 lines: the T-008 tag renderer moved out of tests/test_calibration.py verbatim, plus Piece /
    render_pieces / render_die / render_top_scene / calibration_from_homography); tests/test_perception.py new (64 tests);
    tests/test_calibration.py imports the moved renderer and is otherwise unchanged (21 tests, all still pass);
    config/board.yaml +`perception:` block, entirely Form-2 placeholder under `perception_status: UNMEASURED`
    (REQUIRED_KEYS untouched); docs/board.md retitled and given a perception section.
  - `.venv/bin/python -m pytest tests/test_perception.py -q` -> 64 passed, printed timing:
    `TopCameraPerception.detect on 640x480, 16 horses + die, 30 frames: mean 4.47 ms, max 4.60 ms, p50 4.46 ms`
    (bound 30 ms, 6.7x margin). Same scene at 1280x960: 11.6 ms, measured ad hoc.
  - Occupancy exact on 20/20 random boards (10 horses, random cells/colours/yaw, 640x480) and on a 21st case under 15 deg
    rotation + projective tilt (12 horses) -- the thresholds are area *ratios* at the local pixel scale and distances in
    board millimetres, never pixels, which is what makes one set of numbers hold across scale and tilt.
  - Each 6.5 variant has a test: side -> fallen by aspect (1.43-1.46 vs 1.35); back -> fallen by area (0.34-0.37 vs a
    standing band starting at 0.55) while still square; off the magnet -> between_cells and occupying no cell; missing ->
    absent; two blobs on one cell -> nearer keeps it. Each 6.5 failure mode has a verify() test: MOVE happened (incl.
    capture) / did not happen (grasp_failed) / fell at src or dst (horse_fell) / between cells or wrong cell (missed_cell)
    / another colour or our own second horse moved (wrong_horse); ROLL success / die_out_of_bowl / die_grasp_failed;
    RECOVER success / horse_fell / missed_cell / timeout_no_progress / the die case.
  - Full pre-commit suite on the commit (ruff + `pytest -q`, no `--no-verify`): 767 passed, 14 skipped in 411.55 s;
    every skip is a hardware-absent or no-session skip that predates this task (703 -> 767 is this task's 64 new tests).
  - Not met / stated rather than hidden: no real still exists (H-001), so every threshold is a guess against synthetic
    colour and the numbers pin the rules, not a detection rate (docs/board.md says so and names the real-still check);
    a white die on the white board is reported "not seen" rather than guessed; two horses of one colour are
    indistinguishable, so a same-colour wrong horse is caught only by the cell it left; board/perception.py is 999 lines
    and D-013 item 2 would split it, but the touch list named only one new module; docs/README.md still describes
    docs/board.md as calibration only (one row, outside the touch list).

## T-040  Policy termination signal: an episode-end head trained from recorded episodes
status: accepted
priority: P2
phase: 3
owner: opus
depends_on: T-035
hardware: none
deliverables:
  - policy/_shared.py + both wrappers: an auxiliary "done" head on the observation encoding trained with a BCE loss against
    a per-frame label "within the last `done_window_s` of the episode" (config), weight `done_loss_weight`; adapters' `done()`
    returns True when the head's probability exceeds `done_threshold` for `done_hold_steps` consecutive calls
  - dataset: the per-frame done label from episode length
  - tests: label correctness at episode ends; smoke train shows the done loss falling; adapter.done() flips on a synthetic
    high-probability sequence and not on a low one; the controller stops on done() before the timeout in a mock run with a
    stub adapter
acceptance:
  - tests pass with printed numbers; docs/policy.md and docs/controller.md updated
notes: CLAUDE.md 5.5 names "the policy's own termination signal or a 20 s timeout"; until this lands only the timeout exists.
result: (opus, 2026-09-12T23:55+07:00, commit 0bc03c8, branch wt/t040)
  - `.venv/bin/python -m pytest tests/test_dataset.py tests/test_controller.py -q` -> 57 passed in 93 s. PASS
  - `.venv/bin/python -m pytest tests/test_act.py tests/test_diffusion.py -q -s` -> 35 passed in 89 s. PASS
  - `.venv/bin/python -m pytest tests/test_train.py tests/test_eval.py -q` -> 49 passed, 1 skipped (the pre-existing
    DDIM-ordering-under-load skip). PASS
  - label: at the configured `done.window_s` 1.0 s and 30 Hz the 60-frame episode has 31 frames labelled done and the
    boundary is exact (frame stop-1-30 is 1, the one before it is 0); a 0 s window labels the last frame only; a
    negative one is refused. PASS
  - smoke train (30 steps, mock session, 0.3 s window and weight 1.0 because the mock episodes are 1.0 s and 0.5 s;
    base rate 20/45 = 0.444), done loss over the whole session: diffusion 0.6931 -> 0.6664 (-3.9%), ACT 0.6931 ->
    0.6900 (-0.5%). Both start at exactly ln 2 (zero-initialised output layer). PASS
  - adapter: head pinned to p=0.998 -> done() after each act() is [False, True, True, True] (hold_steps 2); at
    p=0.002 -> [False, False, False, False]; reset() clears the streak. Both models. PASS
  - controller: a stub adapter carrying the real DoneDetector on [0.9, 0.2, 0.9, 0.9, 0.1] stops the primitive with
    stopped_by policy_done after 4 policy calls in 0.43 s against the 20 s timeout, watchdog and timeout both 0.
    runtime/controller.py unchanged. PASS
  - docs/policy.md (dataset row, _shared bullet, "The termination head" section with the numbers, export and ACT
    table) and docs/controller.md (one section under "The cycle") updated. PASS
  - head size at the configured scale: 52 481 params on a 408-D feature for diffusion (0.018% of 293.1 M) and
    diffusion_small (0.172% of 30.5 M), 65 793 on 512-D for ACT (0.127% of 51.6 M). TorchScript still traces with
    the hooks in place (max diff vs eager 0.0). PASS
  - not met / follow-up: no `done_loss` column in loss.csv -- that needs policy/train.py, which this task's file
    boundary excludes (another builder is refactoring it); both wrappers expose `last_losses["done_loss"]` per step
    for it. No real-data or on-robot number: the head has never seen a recorded G1 episode (R5).

## T-041  Session pre-flight: a read-only go/no-go table before any hardware session
status: accepted
priority: P1
phase: 1
owner: opus
depends_on: T-018, T-019, T-020, T-010
hardware: none
deliverables:
  - tools/hardware_checks/session_preflight.py: prints one table with a row per check and PASS/FAIL/SKIP: session gate status
    (runtime.safety.SessionGate), every config file's UNMEASURED keys that Phase 1 needs measured before motion (list from
    config, e.g. safety box, dds interface, hand port, calibration present), each device reachable via the read-only drivers
    (arm state within timeout, hand connected, glove, pose, top/oblique cameras) with the achieved rate over 3 s, board
    calibration file present and fresh, disk free vs Q-002, git status clean and HEAD hash, e-stop named in the session
    checklist (Q-004 answered); exit 0 only if every motion-relevant row passes; `--json` output
  - tests on mocks/fakes: every row's PASS and FAIL paths; exit code rules
acceptance:
  - tests pass; running it on this laptop today prints the table with the expected FAIL/SKIP rows (output in BUILD_LOG.md)
notes: Read-only, no session needed. T-021's session procedure starts with this tool.
result: (opus, 2026-09-12T23:10+07:00, commit d0e6820; branch wt/t041)
  - `.venv/bin/python -m pytest tests/test_session_preflight.py -q` -> 29 passed in 4.87 s, exit 0. Both the PASS and
    the FAIL path of every row (session gate, e-stop, config key, device, calibration, disk, git), the exit-code rule
    on hand-built rows, the `*` marking, and the CLI (table, --json, --budget 0 -> 2). PASS
  - `.venv/bin/ruff check .` -> "All checks passed!", exit 0. PASS
  - `.venv/bin/python tools/hardware_checks/session_preflight.py` on this laptop today -> exit 1, 8.6 s wall,
    "0/28 motion-relevant checks pass", "NO-GO for a motion session." Full table in agents/BUILD_LOG.md. PASS
    Rows: session gate SKIP (no session file); e-stop named FAIL (the checklist says only "e-stop within reach",
    Q-004 unanswered); 25 config rows FAIL, one per MOTION_KEYS placeholder (envelope box + limits + velocity +
    first-step cap + watchdog, dds_interface, the three control gains, the four latencies, pico_to_pelvis,
    hand port + pinch poses, top camera, the four apriltag keys); device arm/hand/glove/pose/top/palm SKIP, each
    carrying the driver's own message naming H-002/H-003/H-004 or the config key; device oblique PASS at
    30.0 Hz over 91 samples in 3.0 s (ORBBEC: Ego left, the only device plugged in); board calibration FAIL
    (config/board_calib.yaml absent, H-001); dataset disk FAIL (12.0 GB free vs the 500 GB target, Q-002);
    git FAIL (run before this commit; the clean-tree branch is covered by a test).
  - exit rule: 0 only when every motion-relevant row is PASS; a SKIP on one of them is not a pass. The motion-relevant
    rows are the 25 placeholders, the e-stop, and `arm`/`hand` (the two devices a motion command can reach, R1);
    the session gate, the sensor devices, calibration, disk and git are reported and unstarred.
  - `--json` prints the same rows as a JSON list of {check, status, detail, motion_relevant}. PASS
  - read-only: no Guard built, no writer imported, `hardware/session.enable` only read through SessionGate.status();
    config/safety.yaml unchanged. PASS
  - the pre-commit run caught a real defect: git_row inherited the hook's GIT_DIR/GIT_INDEX_FILE and reported the
    wrong repository; it now scrubs every GIT_* variable from the child environment, with a regression test. PASS
  - deviations logged in BUILD_LOG: the tool is 362 lines, not under 300 (D-013 item 2 -- the touch-list has no
    sibling module to move the report/CLI types into); the 500 GB disk target is a documented module constant
    because no config file in the touch-list is the right home for it.

## T-042  Module splits per D-013 (no behaviour change)
status: accepted
priority: P2
phase: 1
owner: opus
depends_on: T-033, T-018, T-019, T-020
hardware: none
deliverables:
  - drivers/dds.py (the DDS binding from g1_arm.py), drivers/serial_discovery.py (from dexh15.py and pxcap.py),
    teleop/clutch.py (from loop.py), policy/train_io.py (checkpoint/EMA/atomic save from train.py), board/detect.py (the
    blob detection and classification rules from perception.py), tools/hardware_checks/preflight_report.py (table rendering
    and the MOTION_KEYS data from session_preflight.py); each origin file
    re-exports what tests import; no test changes except import paths where a test imported a private name
  - line counts before and after per file in BUILD_LOG.md
acceptance:
  - full suite passes with the same test count; `git diff --stat` shows moves, and a `grep` proves no logic line changed
    beyond imports (describe the method)
notes: Pure refactor; do it in one commit per file so a revert is cheap.
result: six commits, one per origin file, each through the full pre-commit gate (no --no-verify):
  f053463 drivers/g1_arm.py 332 -> 297 + drivers/dds.py 60 (ArmUnavailable, dds_binding, default_subscriber)
  3c7f32b drivers/dexh15.py 485 -> 441, drivers/pxcap.py 483 -> 483 + drivers/serial_discovery.py 61
  43e7c87 teleop/loop.py 388 -> 286 + teleop/clutch.py 126 (ClutchState, Clutch)
  e3c341e policy/train.py 849 -> 674 + policy/train_io.py 218 (checkpoints, disk guard, EMA)
  b0b142b board/perception.py 1001 -> 809 + board/detect.py 216 (rules block, _area_px)
  67d88aa tools/hardware_checks/session_preflight.py 362 -> 307 + preflight_report.py 78 (MOTION_KEYS, Row, render)
  no test file changed: every origin re-exports what its tests import, private names included.
  test count identical: `pytest --collect-only -q` reports 810 before (at 0af6aee) and 810 after.
  no-logic-change proof: ast-parse each file, drop imports and docstrings, ast.unparse, sort the
  logical lines, and diff origin-before against (origin-after + new module). All six diffs are one
  added line, the new module's own __all__. Method and commands in BUILD_LOG.md.

## T-043  Second checked point: the fingertip pinch point in the workspace box (D-010, tightening)
status: accepted
priority: P1
phase: 1
owner: opus
depends_on: T-011, T-033
hardware: none
deliverables:
  - runtime/fk.py: `left_arm_points(q7, waist_yaw) -> dict[str, np.ndarray]` returning the wrist point and the fingertip pinch
    point = wrist frame transformed by config/robot.yaml `tool.pinch_offset_m` (new UNMEASURED placeholder, e.g.
    [0.12, 0.0, -0.03] in the wrist frame, with the frame definition documented; a rotation is applied from the wrist body's
    xquat via mujoco, so the point follows wrist roll/pitch/yaw, which is what D-010 found missing)
  - runtime/safety.py: the workspace box is checked at BOTH points (config/safety.yaml `workspace_box_m.points:
    [left_wrist_yaw_link, pinch_point]`, an added key; the existing `point` stays for compatibility and both must be inside);
    SafetyViolation names which point left the box; nothing else in config/safety.yaml changes
  - tests: wrist roll/pitch/yaw now move the pinch point (each by > 1 cm for 0.3 rad, print), a pose whose wrist is inside the
    box but whose pinch point is outside is refused naming pinch_point, teleop/retarget.py still solves against the wrist
    (unchanged) and the loop's mock session stays at 0 refusals with the placeholder offset (if it does not, the placeholder
    offset is wrong for the mock circle: adjust `mock.pose_center_m`, not the safety file, and say so)
acceptance:
  - tests pass with printed numbers; `git diff config/safety.yaml` shows only the added `points` key and its comment (Fable
    checks nothing was loosened); docs/safety.md updated
notes: A tightening (two points must be inside instead of one). The real offset is measured in Phase 1 on the hand (T-022).
result:
  - commit: 4ee2d42, through the full pre-commit gate (ruff + whole suite): 837 passed, 15 skipped in 654.41 s.
    agents/BUILD_LOG.md 2026-09-12T09:42+07:00 has the full entry.
  - runtime/fk.py `left_arm_points` returns {left_wrist_yaw_link, pinch_point}; pinch_point = xpos + xmat @
    config/robot.yaml tool.pinch_offset_m (rotated, so it follows wrist roll/pitch/yaw). `left_arm_fk` unchanged, so
    teleop/retarget.py and tests/test_retarget.py are untouched and still solve against the wrist.
  - 0.3 rad on one joint, displacement of pinch_point (wrist origin in brackets): shoulder_pitch 121.38 (84.97),
    shoulder_roll 72.88 (57.95), shoulder_yaw 95.58 (59.71), elbow 92.61 (55.07), wrist_roll 14.94 (0.00),
    wrist_pitch 51.82 (13.75), wrist_yaw 35.87 (0.00), waist_yaw 105.39 (74.43) mm. All 8 joints > 1 cm; the three
    wrist joints were the criterion.
  - Wrist-in/fingertip-out: shoulder pitch -0.70 rad puts the wrist at [0.2831, 0.1, 0.2647] m INSIDE and the
    fingertip at [0.4059, 0.0756, 0.2998] m outside by 19.8 mm on z -> refused, "pinch_point at ... on axis z by
    0.0198 m". The same command is admitted by a wrist-only envelope (asserted in the same test).
  - Cross-check against an independent xquat evaluation on a fresh model, 20 random configurations: worst 1.110e-16 m.
    left_arm_fk call time 10.9 us mean of 1000 (8.4 us at T-011).
  - Teleop mock session: 917 ticks at 30.000 Hz, 0 refusals with the placeholder offset, so mock.pose_center_m was NOT
    changed. Closest approach to a box face over the session: wrist 19.5 mm, pinch_point 16.1 mm.
  - `git diff --stat config/safety.yaml`: 1 file changed, 7 insertions(+), 0 deletions(-) -- the `points` key and its
    comment, nothing else, nothing loosened. config/robot.yaml gained the `tool:` block (UNMEASURED, T-022 measures it).
  - Deviations (detail in BUILD_LOG): the placeholder offset is [0.12, 0.0, -0.05] not the task's [0.12, 0.0, -0.03],
    which moves the fingertip only 8.97 mm under wrist roll and fails the > 1 cm criterion; xmat instead of xquat in the
    implementation (xquat is the test's cross-check); tests/test_mock_drivers.py needed a 1-assertion change (the
    fingertip leaves the box before the wrist on its ramp) or the full-suite pre-commit gate could not pass.

## T-044  Phase 1 session runbook
status: accepted
priority: P1
phase: 1
owner: opus
depends_on: T-041, T-043
hardware: none
deliverables:
  - docs/runbook_phase1.md: the exact sequence for the first sessions, each step a command or a physical action with its
    check: (1) read-only day: H-002, H-003, H-004 steps, stream_stats for every device for 10 minutes, list_devices, fill the
    config keys, run session_preflight until only the e-stop and session rows fail; (2) calibration: H-001 still,
    board.calibration, verify perception on the real still; (3) first motion session: Q-004 answered and written into
    enable_session.py's checklist text, enable_session.py by a human, preflight GO, T-021's arm_latency step with the human's
    hand on the e-stop, T-024 envelope test, T-022 hand bench, T-023 reachability; what to write in BUILD_LOG.md for each
    (what moved, envelope in force with config hashes, observed outcome); abort criteria and what to do after an abort
  - a "what the agents will do with the results" paragraph per step (which config keys get MEASURED, which DECISIONS entry
    Fable writes, which task closes)
acceptance:
  - every command in the runbook exists and runs with --help (a test greps the runbook for `.venv/bin/python ...` lines and
    runs each with --help, exit 0); every H-item and Q-item referenced exists in the agents files (test)
notes: Docs only, plus the test. This is what Alois reads before the first hardware day.
result: (opus, 2026-09-12, branch wt/t044, commit d12cbaf)
  - docs/runbook_phase1.md (459 lines): day 0 baseline, day 1 read-only (H-002, H-003, H-004a/b,
    list_devices, the six config keys to fill, seven 600 s stream_stats runs, pytest -m readonly,
    session_preflight --json), day 2 calibration (H-001 stills, board.calibration on both, the
    perception comparison in prose because board/perception.py has no CLI), day 3 motion (3.0 Q-004
    answered and committed into config/safety.yaml session.checklist by a human, 3.1 mock rehearsal,
    3.2 preflight GO, 3.3 enable_session.py by a human, 3.4 T-021 latency, 3.5 T-024 envelope with
    T-043 (in progress) noted for the pinch_point refusals, 3.6 T-022 hand bench, 3.7 T-023
    reachability, 3.8 close the session), then the abort section. All 20 steps carry a `Check:`;
    3.4-3.7 each state what moves and the BUILD_LOG entry (what moved, the envelope in force with
    runtime.config.config_hash("safety"), the session, the outcome, a SAFETY INCIDENT: line on
    contact or out-of-envelope motion, which stops the loop under R4(c)). Abort: seven conditions,
    the human's three actions (the e-stop named in Q-004's answer, Ctrl-C, delete the session file),
    what the agent writes before any retry, and what happens after. A "what the agents do with the
    results" paragraph per day names the config keys that become MEASURED, the docs/sdks.md verdicts
    (A2/A3/A4), the DECISIONS entries Fable writes and the tasks that close.
  - docs/README.md: one index row. tests/test_runbook.py (196 lines): the acceptance test.
  - `.venv/bin/python -m pytest tests/test_runbook.py -q` -> 32 passed, 1 skipped in 8.19 s.
    `.venv/bin/ruff check .` -> All checks passed! PASS
  - acceptance 1: the parametrised test collected 22 `.venv/bin/python` lines from the fenced blocks
    and ran each with --help from the repo root (exit 0, non-empty stdout): 21 ran, 1 skipped.
    Entry points: list_devices.py, stream_stats.py, brio_still.py, session_preflight.py,
    enable_session.py, -m pytest, -m board.calibration, -m teleop.loop, -m runtime.controller.
  - acceptance 2: every H-, Q-, D- and T- id in the runbook is defined as a heading in the agents
    file that owns it (H-001..H-004; Q-002, Q-004, Q-005, Q-007, Q-010; D-002, D-004, D-007, D-010,
    D-018; T-008, T-010, T-018..T-024, T-041, T-043, T-044). PASS
  - deviation (BUILD_LOG has the argument): enable_session.py --help exits 2, not 0 -- it refuses
    every argument by design so nothing but a human at a terminal can write hardware/session.enable.
    The parametrised test skips it by name and test_enable_session_rejects_arguments asserts the
    stricter behaviour (exit 2 plus the usage line). Every other command meets the criterion as
    written. The runbook contains no `python -c` snippet (a test asserts it), because one cannot be
    checked with --help; the config hash is named in prose as runtime.config.config_hash("safety").
  - R1/R2/R3: no motion command is possible from anything added; --help returns inside argparse.
    config/safety.yaml untouched. Only the five files in the touch list changed.

## T-045  HUMAN_APPROVED status and per-step gating in the pre-flight (D-022)
status: accepted
priority: P1
phase: 1
owner: opus
depends_on: T-041, T-043
hardware: none
deliverables:
  - runtime/config.py: `_check_status_keys` accepts HUMAN_APPROVED beside UNMEASURED and MEASURED; `unmeasured()` still lists
    UNMEASURED only; a new `status_of(name, key)` helper
  - tools/hardware_checks/preflight_report.py: MOTION_KEYS entries gain a `gates` field naming the runbook step they gate
    (t021_latency, t024_envelope, t022_hand, t023_reach, phase2_recording); session_preflight.py gains `--for STEP`
    (default: the strictest, every key) and treats HUMAN_APPROVED as PASS only for envelope keys and only for the steps D-022
    names; the table shows the status word in a column
  - tests: HUMAN_APPROVED accepted by the loader; the pre-flight with `--for t021_latency` on a config copy whose envelope keys
    are HUMAN_APPROVED and whose latencies are UNMEASURED reports GO for that step and NO-GO for `--for phase2_recording`
  - docs/config.md (the third status), docs/safety.md and docs/runbook_phase1.md (step 3.0 gains "approve the envelope
    values" with the exact keys and the command to show them)
acceptance:
  - tests pass; running `session_preflight.py --for t021_latency` on this laptop still says NO-GO (nothing is approved yet)
    with the e-stop and the approval rows named (output in BUILD_LOG.md)
notes: No value changes in config/safety.yaml; only the loader and the tool learn a status word. The approval itself is a
  human commit (R3).
result: (opus, 2026-09-12T11:20+07:00, commit 9f4e6b1)
  - runtime/config.py: STATUS_VALUES = {UNMEASURED, MEASURED, HUMAN_APPROVED}; unmeasured() unchanged (UNMEASURED only);
    new status_of(name, key, root=None) -> str | None reading the value itself, a `<key>_status` sibling, or an ancestor's.
  - preflight_report.py: MOTION_KEYS is 25 MotionKey(name, key, why, gates, approved_ok) entries over
    STEPS = (t021_latency, t024_envelope, t022_hand, t023_reach, phase2_recording) with ALL_STEPS = "all" the default;
    11 entries are approved_ok (8 safety envelope keys + robot.control.kp/kd/weight_ramp_s) and gate every step;
    dds_interface gates the four arm steps; hand port + pinch poses gate t022_hand, t023_reach, phase2_recording;
    top.device, the 4 apriltag keys, the 4 latencies and teleop.pico_to_pelvis gate t023_reach and phase2_recording.
    Row gains key_status; render gains the status column and step-aware verdict wording (the ALL_STEPS wording unchanged).
  - session_preflight.py: config_rows(root, step); collect(step=...); --for STEP (unknown step exits 2); --show-envelope
    prints the 11 approvable keys, their values, their status word and the exact `<leaf>_status: HUMAN_APPROVED` line, and
    exits 0 writing nothing. HUMAN_APPROVED is PASS only on an approved_ok key; UNMEASURED is FAIL on every key.
  - tests 22 new: test_config.py 70 -> 75, test_session_preflight.py 29 -> 42, test_runbook.py 32 -> 36 (+1 skip unchanged).
    On the D-022 morning-of-day-3 config copy (envelope HUMAN_APPROVED, latencies/transform/pinch poses UNMEASURED):
    --for t021_latency exit 0; --for phase2_recording exit 1 on exactly the 7 keys day 3 measures; --for all exit 1.
  - acceptance on this laptop, `session_preflight.py --for t021_latency --budget 1`: exit 1, "0/15 checks that gate
    t021_latency pass" naming the e-stop row, the 11 envelope/gain rows, robot.network.dds_interface and the arm and hand
    devices; "NO-GO for t021_latency." Full table without --for unchanged at 0/28, "NO-GO for a motion session."
    Full output and the commands in agents/BUILD_LOG.md.
  - config/ untouched (git diff --stat lists no file under config/); no session file created, read or restored.

## T-046  Orbbec Ego 10-minute read-only stream statistics; oblique capture placeholders measured
status: accepted
priority: P1
phase: 1
owner: opus
depends_on: T-010
hardware: read-only
deliverables:
  - agents/BUILD_LOG.md: the 600 s run `.venv/bin/python tools/hardware_checks/stream_stats.py --backend real --stream oblique --seconds 600 --json`
    with the command, the full JSON, achieved rate, drop count and frames missed, interval and jitter p50/p99/max, and one
    frame's mean/std as in T-010 (proof it is imagery, not a black stream). Every run made is reported, including bad ones.
  - config/cameras.yaml `oblique` only: `device` -> the /dev/v4l/by-id/... path of 'ORBBEC: Ego left' and `device_right` ->
    the by-id path of 'ORBBEC: Ego right' (both from `list_devices.py --json`); `resolution` [1600, 1200] with
    `resolution_status: MEASURED`; `fps_status: MEASURED`; `fourcc_status: MEASURED`; `policy_resolution` [640, 480]
    unchanged; a comment citing T-010 (device negotiates 1600x1200@30 MJPG whatever is asked) and T-046 (600 s run)
  - docs/sdks.md 8.2: the "Rate/units/resolution: UNMEASURED" line replaced by the measured numbers and the by-id nodes;
    docs/drivers.md and docs/config.md wherever they describe the oblique placeholders
  - tests: whatever config test enumerates UNMEASURED keys or statuses still passes; add a test that an `oblique` spec with
    resolution [1600, 1200] and policy_resolution [640, 480] yields (480, 640, 3) frames through the mock/downscale path,
    unless tests/test_cameras.py already asserts exactly that (say which test if so)
acceptance:
  - 600 s real run in BUILD_LOG.md: achieved rate within 1% of 30 Hz, 0 drops, jitter p99 < 10 ms. If any run has drops,
    report it as measured and do not re-run to get a clean number; a second run may follow, both are logged.
  - `grep -c UNMEASURED config/cameras.yaml` goes from 21 to 16 and `git diff config/cameras.yaml` touches only the
    `oblique` block; `top` and `palm` rows untouched
  - `.venv/bin/python -m pytest -q` green with the Ego attached (count in BUILD_LOG.md); `.venv/bin/ruff check .` clean
  - `.venv/bin/python tools/hardware_checks/session_preflight.py --no-devices` output unchanged before/after apart from
    any oblique row (paste both if a row changed)
notes: D-024. Pure sensor read: no session, no motion path, R1 not engaged. Run the 600 s stream with nothing else holding
  /dev/video4 (do not run the pytest readonly camera tests at the same time). Do not touch policy/, runtime/, `top`, `palm`,
  config/safety.yaml, third_party/. If the by-id link for either node is absent, leave that key UNMEASURED and say why.
result: (opus, 2026-09-14T12:55+07:00, commit 2f8443a)
  - **600 s run 1** (the deliverable's command verbatim, node via usb_id discovery, `/dev/video4
    'ORBBEC: Ego left'`, negotiated 1600x1200 @ 30 MJPG): 17948 frames in 598.23 s, **fps 30.000**,
    **222 drops / 222 frames missed**, interval p50 33.3632 / p99 51.2306 / max 67.4397 ms,
    **jitter p50 4.8938 / p99 18.3244 / max 34.1064 ms**. Full JSON in BUILD_LOG.md.
  - **600 s run 2** (same command after the config edit, so through the new by-path selector; agent
    idle throughout): 17955 frames in 598.47 s, **fps 30.000**, **207 drops / 207 missed**, interval
    p50 33.3592 / p99 51.1936 / max 67.7593 ms, **jitter p50 4.9635 / p99 18.3303 ms**. Both logged.
  - Acceptance 1: **rate PASS** (30.000 Hz, 0.0% off 30, bound 1%); **drops FAIL** (222 and 207,
    bound 0); **jitter p99 FAIL** (18.32 / 18.33 ms, bound < 10 ms). Not re-run for a clean number,
    per the task. Cause not agent load and not run length: 30 s -> 19 drops, and T-010's exact 10 s
    command -> 4 drops / p99 18.00 ms today against 0 drops / p99 1.44-2.94 ms on 2026-09-11. The Ego
    is on a USB 2.0 (480 Mbps) link and exposes no exposure control over UVC. Raised as **H-005**.
  - Frame is imagery, not a black stream: (480, 640, 3) uint8, **mean 102.7, std 42.0** (T-010: 81.4 / 46.7).
  - Acceptance 2: `grep -c UNMEASURED config/cameras.yaml` **21 -> 16**; `git diff config/cameras.yaml`
    touches the `oblique` block **and the file's own header comment** (it said "Nothing here was
    measured ... no stream was ever opened", which this task made false -- flagged as a deviation in
    BUILD_LOG.md, one line to revert). `top` and `palm` rows byte-identical.
  - **Deviation, deliberate: `device`/`device_right` are `/dev/v4l/by-path/...`, not by-id.** This unit
    gives both of its UVC functions the single by-id name
    `usb-ORBBEC_EGO_ORBBEC_AZER76400HV-video-index0`; it resolved to `/dev/video4 'Ego left'` at T-010
    and resolves to `/dev/video6 'Ego right'` now, so the asked-for value would have pointed `oblique`
    at the right camera silently. by-path carries the interface number (1.0 left, 1.2 right). Evidence
    (`udevadm`, `ls -l`) and the reversal instructions are in BUILD_LOG.md.
  - Acceptance 3: `.venv/bin/ruff check .` -> All checks passed! `.venv/bin/python -m pytest -q` with
    the Ego attached -> recorded in BUILD_LOG.md's commit section (pre-commit gate, no --no-verify).
  - Acceptance 4: `session_preflight.py --no-devices` before vs after differs in **one line only**, the
    `git` row (tree clean -> uncommitted paths, during the run); no oblique row exists in the pre-flight
    and none was added. Both outputs in BUILD_LOG.md's safety section.
  - Tests: `tests/test_config.py::test_camera_devices_are_all_unresolved` asserted `oblique.device` is a
    placeholder and could not survive; it is now `..._except_the_one_that_was_streamed` plus a new
    `test_the_oblique_capture_mode_is_the_one_the_ego_negotiates`. The (480, 640, 3) deliverable is the
    new hardware-free `tests/test_cameras.py::test_the_measured_oblique_capture_mode_downscales_to_the_policy_frame`;
    the existing `test_real_frames_arrive_at_the_policy_resolution[oblique]` asserts the same shape but
    is `readonly` and skips without the camera, so it could not stand in for it.

## T-047  Camera frames stamped with the V4L2 kernel buffer timestamp; stream_stats separates late delivery from loss
status: accepted
priority: P1
phase: 2
owner: opus
depends_on: T-046
hardware: read-only (one readonly test and one 600 s check; everything else on mocks)
deliverables:
  - drivers/cameras.py: after each successful grab(), read the kernel buffer timestamp (cv2 CAP_PROP_POS_MSEC on the V4L2
    backend; CLOCK_MONOTONIC ms, verified in D-025) and use it as the frame's stamp in runtime.clock ns units; fall back to
    the arrival stamp when the value is 0, non-monotonic, or more than 100 ms from arrival, and count the fallbacks; the
    frame carries `stamp_source: kernel|arrival` (or the driver exposes a counter) so the recorder card can report it;
    document in docs/drivers.md and docs/clock.md how the kernel stamp relates to runtime.clock.now_ns (both monotonic)
  - tools/hardware_checks/stream_stats.py: report both stamps for cameras (kernel and arrival: rate, interval, jitter), a
    `frames_lost = round(span * nominal) + 1 - received` figure separate from `drops` (which stays as the late-delivery
    count), and `arrival_minus_kernel_ms` p50/p99/max; the --json schema gains those keys and the text output shows them
  - tests (mocks): a fake capture whose get(POS_MSEC) returns a clean 30 Hz grid while grab() returns late-and-burst
    arrival times; assert the kernel stamps are used, jitter on the kernel stamp is < 1 ms, drops counted on arrival,
    frames_lost 0; fallback triggered when POS_MSEC is 0; a readonly test on the Ego asserting kernel stamps are within
    100 ms of arrival and monotonic
  - recorder: no logic change; verify with the existing mock end-to-end test that the skew statistic still comes from the
    frame stamp (it should pick up the kernel stamp automatically); say so in BUILD_LOG.md
acceptance:
  - mock tests as above green; full suite green; ruff clean
  - readonly 600 s run on the Ego with the new stream_stats: frames_lost, drops, arrival jitter p99, kernel-stamp jitter
    p99 and arrival_minus_kernel p99 all in BUILD_LOG.md; the run is reported whatever it shows (R5). Expected from D-025:
    frames_lost 0, kernel-stamp jitter p99 < 2 ms; if kernel-stamp jitter p99 >= 10 ms, say so and H-005 step 1 follows
  - MockCamera unchanged in interface; every existing camera test passes without edits except where the stamp field is new
notes: D-025. Run the 600 s check with the host quiet (no pytest in parallel; the pre-commit hook's suite counts). Do not
  touch policy/, runtime/safety.py, config/*.yaml except adding a `stamp_source` key to config/cameras.yaml `defaults` if a
  switch is genuinely needed (default kernel). Nothing under third_party/. No session, no motion path.
result: (opus, 2026-09-14T13:55+07:00, commit 4c90884)
  - **600 s readonly run on the Ego**, host quiet, same by-path node: 17900 frames in 596.625 s,
    **fps 30.000**, **frames_lost 0**, **drops 0**, `stamp_source {kernel: 17900}` (zero fallbacks).
    **Kernel-stamp jitter p50 0.1373 / p99 0.8114 / max 9.3713 ms**; arrival jitter p50 0.2339 /
    **p99 3.2895** / max 16.1731 ms; **arrival_minus_kernel p50 9.5089 / p99 12.0682 / max 26.2418 /
    min 5.4121 ms**. Acceptance: frames_lost 0 **PASS**, kernel jitter p99 < 2 ms **PASS** (0.81);
    not >= 10 ms, so H-005 step 1 is not forced. Full JSON in BUILD_LOG.md.
  - This run had 0 drops on both trains (quiet host), so the loss/late separation is demonstrated
    there only as arrival jitter p99 4x the kernel's; it is demonstrated exactly in
    `test_the_kernel_stamp_is_clean_while_arrival_shows_the_drop` (scripted capture: arrival 1 drop /
    2 late, kernel 0 drops and jitter p99 < 1 ms, frames_lost 0 on both).
  - `.venv/bin/python -m pytest -q` -> **901 passed, 17 skipped in 464.22 s**; `.venv/bin/ruff check .`
    -> clean; `tests/test_cameras.py` -> 38 passed, 4 skipped (the 4 are `top`, which is not attached).
  - Recorder unchanged and verified by reading the path (`poll()` pushes `stamped.ts_ns`, line 215;
    skew is computed from those) plus the existing 60 s mock end-to-end tests, still green.
  - Deviation, narrowing only: the kernel stamp is believed when it is <= 100 ms behind arrival and
    <= 1 ms ahead of it, not +/-100 ms, because a frame is captured before `read()` returns and the
    one-sided window keeps emitted stamps non-decreasing across a fallback. Logged in BUILD_LOG.md.
  - `config/cameras.yaml` `defaults.timestamp` now reads falsely but was **not** edited (the task
    forbade config edits); a one-line replacement is proposed at the end of the BUILD_LOG entry.
