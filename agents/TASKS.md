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
result: (opus, 2026-09-11T18:45+07:00, commit 4255484)
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
result: (opus, 2026-09-11T20:10+07:00, commit ac4fcc5)
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
result: (opus, 2026-09-11T21:40+07:00, commit b2e3bdc)
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
result: (opus, 2026-09-11T19:20+07:00, commit 956147a; branch wt/t004)
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

result: (opus, 2026-09-11T21:55+07:00, commit 8c03733)
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
result: (opus, 2026-09-11T20:10+07:00, commit 096d01f)
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
result: (opus, 2026-09-11T22:55+07:00, commit 5cab3e6, branch wt/t007)
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
result: (opus, 2026-09-11T20:05+07:00, commit 63d998f)
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
result: (opus, 2026-09-11T19:12+07:00, commit 5540c10; branch wt/t009)
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
result: (opus, 2026-09-11T23:55+07:00, commit f3e553d)
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
result: (opus, 2026-09-11T19:47+07:00, commit d9596d9)
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
  commit: aa8f9cc
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
result: (commit 6628571)
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
  commit: 5c79b4d
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
result: (opus, 2026-09-11T21:00+07:00, branch wt/t015, commit 381b9d7)
  Phase 0 report appended to agents/BUILD_LOG.md (seven sections) and docs/README.md written (11 module
  pages, one sentence each). No code changed.
  measured in the worktree at 3c5df60:
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
  commit: COMMIT_HASH
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
  commit: 6beb49d
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
status: todo
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

## T-019  DexH15 driver, read-only state and palm camera, 10-minute stream stats
status: todo
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

## T-020  Glove and controller pose drivers, read-only, 10-minute stream stats
status: todo
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

## T-021  G1 arm write path over rt/arm_sdk with ramped weight; actuation latency measurement
status: todo
priority: P0
phase: 1
owner: opus
depends_on: T-018, T-011
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
status: review
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
  commit: COMMIT_HASH
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
status: in_progress
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

## T-027  policy/dataset.py: loader, goal rendering, augmentation on the mock dataset
status: todo
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
