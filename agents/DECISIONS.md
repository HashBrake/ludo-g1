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
