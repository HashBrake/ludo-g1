# BUILD_LOG.md (Opus, append-only: what was built, how verified, measurements, open questions, disagreements)

## T-001  Repo scaffold, Python environment, pre-commit CI  (2026-09-11T18:45+07:00)

### What was built
- `pyproject.toml`: project `ludo_g1`, `requires-python = "==3.10.*"`. Ruff (line-length 120, target py310,
  lint select E,F,I,W,UP,B) with `extend-exclude = ["third_party", ".venv", "data"]`. Pytest with
  `testpaths = ["tests"]`, `norecursedirs = ["third_party", ".venv", "data", ".git", "*.egg-info"]`,
  `pythonpath = ["."]`, and the `motion` / `readonly` markers registered. The project is never `pip install`-ed;
  pyproject carries tool config only (no build-system section on purpose).
- `requirements.txt`: fully pinned, direct + transitive. numpy 2.2.6, pyyaml 6.0.3, structlog 26.1.0,
  pytest 9.1.1, ruff 0.16.7, opencv-python-headless 5.0.0.93, scipy 1.15.3. No torch/lerobot/mujoco (task note).
- `.venv/` created with `uv venv --python 3.10` (uv fetched a managed CPython 3.10.20; the system python3 is
  3.10.12, both satisfy 3.10.x) and populated from requirements.txt.
- Package skeleton per CLAUDE.md 5.1, each with a one-line docstring in `__init__.py`:
  `drivers/` (+`drivers/mock/`), `runtime/`, `teleop/`, `board/`, `engine/`, `policy/`, `eval/`, `tools/`,
  `tools/hardware_checks/`, `tests/`. Non-package dirs `cloud/`, `config/`, `docs/` created; `cloud/` and
  `config/` hold a `.gitkeep` only (git cannot track an empty directory) — no module stubs were created for
  files no task asked for.
- `tools/pre-commit.sh` + `tools/install_hooks.sh`; the hook is installed at `.git/hooks/pre-commit`. It resolves
  the repo with `git rev-parse --show-toplevel`, invokes `.venv/bin/ruff` and `.venv/bin/python -m pytest -q`
  explicitly (PATH-independent), and aborts with a clear message + the `uv venv` command if `.venv` is missing.
- `tests/conftest.py`: `pytest_collection_modifyitems` skips every `motion`-marked test. It tries
  `from runtime import safety` and `SessionGate().status()`; today that import fails, so the skip reason is
  `no session gate yet` (T-005 replaces this). It fails closed: any exception from the gate also skips.
- `tests/test_scaffold.py`: imports all 10 packages, checks the three plain dirs, asserts Python 3.10,
  asserts `hardware/session.enable` is git-ignored and absent from `git log --all` (section 8 audit, R1), and
  carries one `@pytest.mark.motion` test whose body raises — it proves the autoskip is live.
- `docs/setup.md`: venv creation, check commands, marker table, hook install, layout notes.

### Commands run and measured results
```
$ .venv/bin/python --version
Python 3.10.20                                    # acceptance 1: PASS (3.10.x)

$ .venv/bin/ruff check .   ; echo exit=$?
All checks passed!
exit=0                                            # acceptance 2: PASS

$ .venv/bin/python -m pytest -q ; echo exit=$?
...............s                        [100%]
SKIPPED [1] tests/test_scaffold.py:60: no session gate yet
15 passed, 1 skipped in 0.02s
exit=0                                            # acceptance 3: PASS, 15 real tests
```

Hook proof (acceptance 4). Three runs, all with the hook installed at `.git/hooks/pre-commit`:

1. ruff stage — scratch file `scratch_ruff_error.py` containing `import os` / `x=1`:
```
$ printf 'import os\nx=1\n' > scratch_ruff_error.py && git add scratch_ruff_error.py
$ git commit -m "[opus][T-001] scratch: hook proof (must be rejected)"
pre-commit: ruff check .
I001 [*] Import block is un-sorted or un-formatted  --> scratch_ruff_error.py:1:1
F401 [*] `os` imported but unused                   --> scratch_ruff_error.py:1:8
Found 2 errors.
pre-commit: ruff failed; commit aborted.
commit exit: 1        HEAD unchanged at 42867bb
```
2. pytest stage — scratch test `tests/test_scratch_fail.py` with `assert 1 == 2` (a first attempt using
   `assert False` never reached pytest: ruff B011 caught it, which is itself further proof of stage 1):
```
$ git add tests/test_scratch_fail.py && git commit -m "..."
pre-commit: ruff check .
pre-commit: pytest -q
FAILED tests/test_scratch_fail.py::test_scratch_fail - assert 1 == 2
1 failed, 15 passed, 1 skipped in 0.02s
pre-commit: pytest failed; commit aborted.
commit exit: 1        HEAD unchanged at 42867bb
```
3. missing-venv guard — hook copied into an empty git repo with no `.venv`:
```
pre-commit: <repo>/.venv is missing or incomplete.
pre-commit: create it with:  uv venv --python 3.10 && uv pip install -r requirements.txt
pre-commit: see docs/setup.md
exit=1
```
Both scratch files were removed from the index and from disk (`git rm --cached` + `rm`);
`git ls-files | grep -i scratch` -> none.

Acceptance 5 (`git status` clean after the commit, `.venv/` and `data/` untracked): verified after the commit,
see the T-001 result block in agents/TASKS.md.

### Notes / deviations
- `uv venv --python 3.10` used a uv-managed CPython 3.10.20 rather than the system 3.10.12. Acceptance asks
  only for 3.10.x. Flagged because the DexH15 cp310 wheel (T-002) will be installed into this interpreter;
  if a manylinux/ABI problem shows up there, re-create the venv with `uv venv --python /usr/bin/python3`.
- `cloud/.gitkeep` and `config/.gitkeep` are placeholders so the 5.1 layout is visible in git; T-003 and T-009
  will fill those directories and the keepfiles should be deleted then.
- `tests/` is a package (`__init__.py`) per the task's "each Python package with an `__init__.py`", and
  `pythonpath = ["."]` is also set so top-level imports work regardless of collection mode.
- No disagreements with the task as written. No hardware needed. No blockers.

(T-001 scaffold commit: 4255484; this line and the TASKS.md result hash are the only content of the follow-up commit.)

## T-002  SDK inventory and assumption verification  (2026-09-11T20:10+07:00)

### What was built
- `docs/sdks.md` (new): eight device sections (G1 arm, G1 waist, DexH15, DexH15 palm camera, PxCap Pro,
  Pico controller pose, Brio, Orbbec), each with package/version, install route, the exact state-read call and
  (for actuators) the exact target-write call as `path:line`, plus rates, units, joint order and partial-command
  capability. Section 1 records the four install attempts; section 7 is the A5 investigation; section 9 carries
  a verdict line for A1..A7 and U2; section 10 lists what still needs a human.
- `tests/test_docs_sdks.py` (new): parses every backticked `path:line` token in docs/sdks.md, resolves
  repo-relative (including git-ignored `.venv/` and `third_party/**/_internal/`), absolute and `~` paths,
  asserts the file exists and the line is in range, and prints the count. Also asserts all eight sections are
  present and that A1..A7 + U2 each have a verdict line.
- `tools/hardware_checks/list_devices.py` (new): read-only probe. V4L2 nodes via `VIDIOC_QUERYCAP`
  (O_RDONLY|O_NONBLOCK, no streaming), `/dev/ttyUSB*` and `/dev/ttyACM*` with their USB ids and permissions,
  every USB vid:pid from sysfs, and network interfaces with a `192.168.123.x` flag. Stdlib only. `--json` mode.
- `requirements.txt`: added the two successful installs pinned (`pxdex` from the vendored cp310 wheel,
  `unitree_sdk2py` from upstream GitHub at commit `f7a5526`) plus the transitives uv resolved
  (`cyclonedds==0.10.2`, `opencv-python==5.0.0.93`, `rich`, `rich-click`, `click`, `markdown-it-py`, `mdurl`).
  `pyorbbecsdk` is explicitly NOT added, with the reason in a comment.
- `agents/HARDWARE_NEEDED.md`: H-002 (robot LAN, so the DDS link can be verified) and H-003 (one-time
  enumeration plug-in of Brio + DexH15 + glove), both read-only with exact steps and a post-check.
- `agents/QUESTIONS.md`: Q-008 (Orbbec has no usable Python SDK: three options, assumption stated) and
  Q-009 (two cv2 distributions now installed: which one do we keep).

### Commands run and measured results
```
$ uv pip install --python .venv/bin/python "third_party/dexh15_sdk/DexH15 SDK/pxdex-3.2.1-cp310-cp310-linux_x86_64.whl"
Installed 1 package: + pxdex==3.2.1                                    # SUCCESS
$ .venv/bin/python -c "import pxdex.dh15 as d; print(d.DexH15Control().getSDKVersion())"
DexHandSDK_3.2.1                                                       # no device touched (no port opened)

$ uv pip install --python .venv/bin/python "unitree_sdk2py @ git+https://github.com/unitreerobotics/unitree_sdk2_python@1983e88888217f6c69283cf3a9d1af01e87f07af"
x Failed to download and build ... failed to find branch, tag, or commit 1983e888...   # FAILED (private commit)
$ uv pip install --python .venv/bin/python "unitree_sdk2py @ git+https://github.com/unitreerobotics/unitree_sdk2_python@f7a55264759fe212b23911046a1a59cf13a8d5ea"
Installed 8 packages: unitree-sdk2py==1.0.1, cyclonedds==0.10.2, opencv-python==5.0.0.93,
                      rich==15.0.0, rich-click==1.9.9, click==8.5.0, markdown-it-py==4.2.0, mdurl==0.1.2   # SUCCESS
$ .venv/bin/python -c "from unitree_sdk2py.idl.unitree_hg.msg.dds_ import LowCmd_, LowState_; ..."
imports OK                                                             # no ChannelFactoryInitialize call, no DDS participant

$ uv pip install --python .venv/bin/python pyorbbecsdk
Installed 1 package: + pyorbbecsdk==1.3.2
$ .venv/bin/python -c "import pyorbbecsdk"
ModuleNotFoundError: No module named 'pyorbbecsdk'                     # INSTALLS BUT UNUSABLE
  RECORD: pyorbbecsdk.cpython-311-darwin.so, libOrbbecSDK.1.10.5.dylib ...
  WHEEL:  Tag: cp310-cp310-manylinux1_x86_64                           # mis-tagged macOS wheel
$ uv pip uninstall --python .venv/bin/python pyorbbecsdk               # removed again

$ uv pip install --python .venv/bin/python -r requirements.txt --dry-run
Resolved 23 packages ... Would make no changes                         # requirements.txt == installed env

$ .venv/bin/python -m pytest -q tests/test_docs_sdks.py -s
docs/sdks.md: checked 157 path:line references (129 unique)
9 passed in 0.01s                                                      # acceptance 2: PASS (>= 12 refs)

$ .venv/bin/ruff check .        -> All checks passed!  exit=0
$ .venv/bin/python -m pytest -q -> 24 passed, 1 skipped ("no session gate yet")
$ .venv/bin/python tools/hardware_checks/list_devices.py ; echo $?
... (output below) ... 0                                               # acceptance 4: PASS
```

Every one of the 129 unique references was also printed with its cited line and read back by hand; ten line
numbers were off by a few lines after the first pass (they pointed at a code fence or a neighbouring row) and
were corrected before the commit.

### Read-only device probe output (2026-09-11T19:58+07:00)
```
== V4L2 video nodes ==
  /dev/video0      'Integrated RGB Camera: Integrat' usb=174f:11b4  [VIDEO_CAPTURE,META_CAPTURE,STREAMING]
  /dev/video1      'Integrated RGB Camera: Integrat' usb=174f:11b4  [META_CAPTURE,STREAMING]
  /dev/video2      'Integrated RGB Camera: Integrat' usb=174f:11b4  [VIDEO_CAPTURE,META_CAPTURE,STREAMING]
  /dev/video3      'Integrated RGB Camera: Integrat' usb=174f:11b4  [META_CAPTURE,STREAMING]
  /dev/video4      'ORBBEC: Ego left'  usb=2bc5:1201  [VIDEO_CAPTURE,META_CAPTURE,STREAMING]
  /dev/video5      'ORBBEC: Ego left'  usb=2bc5:1201  [META_CAPTURE,STREAMING]
  /dev/video6      'ORBBEC: Ego right' usb=2bc5:1201  [VIDEO_CAPTURE,META_CAPTURE,STREAMING]
  /dev/video7      'ORBBEC: Ego right' usb=2bc5:1201  [META_CAPTURE,STREAMING]
== USB serial nodes (/dev/ttyUSB*, /dev/ttyACM*) ==
  (none)
== USB devices ==
  2109:0817 VIA Labs USB3.0 Hub | 0bda:0412 4-Port USB 3.0 Hub | 0b95:1790 ASIX AX88179 (USB Ethernet)
  05e3:0749 USB3.0 Card Reader  | 2bc5:1201 ORBBEC EGO | 8087:0037 Intel | 2109:2817 VIA Labs USB2.0 Hub
  0bda:5412 4-Port USB 2.0 Hub  | 2357:0115 Realtek 802.11ac NIC | 27c6:6594 Goodix | 174f:11b4 SunplusIT camera
  (+ 4 root hubs)
== Network interfaces ==
  CloudflareWARP     172.16.0.2       unknown
  enp0s31f6          -                down
  enx000ec6c10aa5    -                down
  lo                 127.0.0.1        unknown
  wlxd037457570db    192.168.10.108   up
  (no interface holds a 192.168.123.x address)
```
So at the time of this task: the **Orbbec Ego is connected** (and is a stereo UVC device, left + right, not
RGB+depth); the **Brio, the DexH15 and the PxCap Pro glove are not connected** (no Logitech id, no serial
nodes); the **robot LAN is down** (H-002). No motion command was possible from any of this (R1).

### The A5 finding (the point of this task)
**A5 is refuted as written.** The vendored Pico pipeline does not produce arm joint targets from a controller
pose. It is: Pico body-tracking skeleton (24 joints) -> GMR/mink full-body IK -> 36-D qpos -> 35-D mimic obs ->
ONNX RL whole-body tracking policy -> 29-joint position targets. Evidence with line references in
docs/sdks.md section 7; the three load-bearing facts are

1. the IK task table demands pelvis, both hips, knees, feet, spine3, both shoulders, elbows and wrists, with
   the highest weights on feet and knees (`.../gmr/ik_configs/pico_bridge_to_g1.json:24`, `:73`, `:122`);
2. the provider drops any frame whose body tracking is inactive (`teleopit/inputs/pico4_provider.py:462`), and
   body tracking on the PICO 4 needs the headset plus two ankle motion trackers (fork README:4);
3. the provider reads only `grip/trigger/axis_x/axis_y` off the controller and never touches
   `controller.pose` (`teleopit/inputs/pico4_provider.py:634`), even though `pico_bridge` delivers a full
   6-DoF `Pose` (meters, xyzw) for each controller
   (`~/miniconda3/envs/teleopit/.../pico_bridge/frames.py:107` and `:137`).

Even the fork's "arms only" mode still routes through GMR and the RL policy
(`teleopit/runtime/arm_mocap.py:31`). For LUDO-G1 that would mean an operator wearing a headset and two ankle
trackers for every episode and a balance policy commanding legs bolted to a rig.

**Smallest alternative (proposed, NOT built here, per the task note):** read
`PicoBridge.wait_frame().controllers.left.pose` directly and solve our own 8-DoF IK (left arm 15..21 + waist
yaw 12) with `mink` on the G1 MJCF with the legs pinned, output to `rt/arm_sdk` through `runtime/safety.py`.
Cost: `pico_bridge` 0.2.1 (pure-Python wheel) into our venv, `mujoco` + `mink` into requirements, and the G1
MJCF vendored (T-011 decides; the files exist at `~/Teleopit/assets/robots/unitree_g1/`: `g1_29dof.xml`,
`g1_29dof_dex3.xml`, `g1_29dof_neck_o6.xml`, `LICENSE`, `README.md`, `meshes/` with 5 subdirectories — and
they are NOT vendored in this repo, although `teleopit/runtime/assets.py:9` expects them there).
A second, smaller finding that helps here: Unitree's own `rt/arm_sdk` topic commands arms + waist only, with
an enable/weight slot at `motor_cmd[29].q`, so LUDO-G1 never has to publish `rt/lowcmd` and never owns the legs.

### Acceptance criteria
1. "docs/sdks.md has all eight sections with both a state-read and (for actuators) a target-write call
   reference" — PASS. Sections 2,3,4,5,6,7,8.1,8.2; write refs for arm (`...g1_arm7_sdk_dds_example.py:174`),
   waist (same topic, index 12), DexH15 (`pxdex/dh15.pyi:150`); the four sensors have no write call by nature
   and say so.
2. "`pytest -q tests/test_docs_sdks.py` passes and checks at least 12 references" — PASS, 157 checked.
3. "every A1..A7 and U2 has a verdict line" — PASS, docs/sdks.md section 9, asserted by the test.
4. "list_devices.py runs without a device present and exits 0" — PASS, exit 0 (and it also ran with the
   Orbbec attached, which is the harder case).

### Notes, deviations, disagreements
- **Deviation from the task wording:** the task asked to install unitree_sdk2py "from the upstream GitHub repo
  at a pinned commit", and the notes suggested preferring the local trees. The GR00T tree's commit `1983e88`
  does not exist upstream (it is a merge of a private branch), so the pin used is `f7a5526`, which is the
  commit the `~/meta-quest-teleoperate` tree is on and which IS public. Both outcomes are recorded.
- **Not done, deliberately:** nothing was imported from the PxCapPro PyInstaller bundle and the `g1_bridge_sdk`
  built extension under `~/Teleopit` was not imported or copied. Q-005 needs an answer first (three candidate
  routes are laid out in docs/sdks.md 6.1) and the task forbids copying the MJCF.
- **Left UNMEASURED on purpose** (no hardware): DexH15 command rate, palm-camera native format, Brio node and
  resolution, Orbbec stream formats, every latency (U1). H-002 and H-003 carry the exact steps.
- `requirements.txt` now contains one absolute `file://` URL for the pxdex wheel, because a file URL cannot be
  repo-relative. If the repo moves (Q-007), that one line must be edited; there is a comment saying so.
- No blockers. No safety-relevant code was added: `list_devices.py` cannot emit a motion command, and no
  driver, no `runtime/safety.py` consumer and no session file was touched.

(T-002 commit: ac4fcc5; this line and the TASKS.md result hash are the only content of the follow-up commit.)
## T-004  runtime/clock.py: monotonic clock, stream alignment, latency compensation  (2026-09-11T19:20+07:00)

Built in the worktree /home/alois/Desktop/ludo-g1-wt-t004 on branch wt/t004 (parallel with T-002 in the main tree).

### What was built
- `runtime/clock.py` (no threads, no I/O, numpy is the only dependency):
  - `now_ns()` — `time.monotonic_ns()` minus one process-wide origin captured at import (`_ORIGIN_NS`).
  - `Stamped(ts_ns, payload)` — frozen, slotted, generic dataclass.
  - `StreamBuffer(name, maxlen=4096)` — bounded deque of `Stamped`; `push` (rejects an out-of-order
    timestamp with ValueError; equal timestamps allowed), `push_stamped`, `latest`, `nearest(ts_ns)`
    (binary search via `bisect(..., key=)`, ties resolve to the older sample, clamps to the first/last
    sample outside the span, never extrapolates), `timestamps()`, `items()`.
  - `align(streams, ts_ns, tolerance_ns) -> dict[name, Stamped]` — one sample per stream; raises
    `AlignmentError` naming every empty or out-of-tolerance stream and its offset. Accepts either a
    mapping `{name: buffer}` or an iterable of buffers (keyed by `buffer.name`; duplicates raise).
  - `skew_stats(streams, instants=None, tolerance_ns=None) -> SkewStats(n, p50_ns, p99_ns, max_ns)`.
    Skew per alignment instant is `max over streams of |sample_ts - target_ts|` (Fable's definition in
    the task notes), documented in docs/clock.md. `instants` defaults to the timestamps of the stream
    with the fewest samples (the slowest one); `tolerance_ns` routes each instant through `align` so an
    unalignable frame raises instead of being counted. Percentiles are `numpy.percentile` defaults.
  - `shift(stream, delta_ns) -> StreamBuffer` — returns a copy with every timestamp moved by `delta_ns`;
    the input is untouched, `name`/`maxlen` preserved. Sign convention: a path that reports `L` ns late is
    compensated with `delta_ns = -L`. The values themselves come from config/robot.yaml (T-003, UNMEASURED).
- `runtime/log.py` — one structlog factory: `configure(level, json)` and `get_logger(name, **initial)`.
  Processor `_add_monotonic_ts` stamps every event with `ts_ns = clock.now_ns()` plus `ts_s` for reading;
  ConsoleRenderer by default, JSONRenderer with `json=True` for `data/logs/`. Configures on first use.
- `tests/test_clock.py` — 9 tests; `docs/clock.md` — clock semantics, buffer invariant, align, the skew
  definition, the shift sign convention, and the measured numbers.

### Commands run and measured results
1. Environment (the worktree has no .venv; it is git-ignored):
   `cd /home/alois/Desktop/ludo-g1-wt-t004 && uv venv --python 3.10 .venv && uv pip install --python .venv/bin/python -r requirements.txt`
   -> CPython 3.10.20, all pinned packages installed.
2. Acceptance 1 (synthetic skew), command:
   `.venv/bin/python -m pytest -q tests/test_clock.py -s`
   printed by `test_skew_p99_under_10ms_for_30hz_and_100hz_streams_over_60s`:
   `skew over 1800 aligned frames (60 s @ 30 Hz, seed 20260911): p50 = 2.982 ms, p99 = 6.701 ms, max = 7.799 ms`
   Two streams (30 Hz `top`, 100 Hz `state`), 2 ms gaussian timestamp jitter, 60 s, aligned on the nominal
   30 Hz grid (1800 instants), tolerance 10 ms (so every frame also had to pass `align`). p99 6.701 ms < 10 ms. PASS
3. Acceptance 2 (`shift` recovers the pairing): `test_shift_then_align_recovers_the_original_pairing` —
   100 Hz stream delayed by 37 ms; without compensation the nearest-sample pairing is wrong for
   >90% of the 30 Hz frames; after `shift(-37 ms)` all 151 camera frames re-pair to exactly the original
   sample (payload index equality) and the shifted timestamps equal the originals. PASS
4. Acceptance 3 (`now_ns` monotonic): `test_now_ns_is_monotonic_over_10000_calls` — 10000 consecutive calls
   non-decreasing, strictly advanced overall, first sample >= 0. PASS
5. Full gate: `.venv/bin/ruff check .` -> "All checks passed!" (exit 0);
   `.venv/bin/python -m pytest -q` -> `24 passed, 1 skipped` (the skip is the pre-existing motion-marker
   autoskip "no session gate yet"), exit 0.

### Notes / deviations
- The 6.7 ms p99 floor is geometric, not a defect: a 100 Hz stream read at instants that are not its own
  is up to 5 ms away before jitter. Documented in docs/clock.md so the Phase 2 dataset-card number is read
  correctly (real capture must beat 10 ms with this same function, not with a looser definition).
- `skew_stats` takes two optional extra arguments beyond the deliverable's `skew_stats(streams)`. The
  no-argument form works (instants default to the slowest stream); the explicit form is what the acceptance
  test needs to measure against a nominal grid. Flagged in case Fable wants the signature narrowed.
- Synthetic jitter timestamps are sorted before being pushed: at 100 Hz with sigma 2 ms about 0.02% of
  adjacent pairs would otherwise invert, and a real driver stamps on arrival, so the buffer's
  non-decreasing invariant is the honest model. Stated in the test docstring and in docs/clock.md.
- No hardware, no motion command, no blockers. No disagreement with the task as written.
(T-004 clock commit: 956147a; this line and the TASKS.md result hash are the only content of the follow-up commit.)

---

## T-003  Config files with UNMEASURED placeholders and a validated loader  (opus, 2026-09-11T21:40+07:00)

### What changed
- `config/robot.yaml` — left arm 7 joints + waist yaw with their 29-slot Unitree indices (15..21, 12) and
  their MJCF ranges; `action_order` = the 9-D action of CLAUDE.md 5.3; DDS transport (laptop 192.168.123.2,
  robot 192.168.123.164, domain 0, interface UNMEASURED); `topics.command: rt/arm_sdk` only (D-007) with the
  weight slot 29; control 50 Hz; seven `latency.*_ms` entries at 0.0 + UNMEASURED plus the measurement method.
- `config/safety.yaml` — session gate (file, 7200 s default, 28800 s cap, required fields), workspace box in
  the pelvis frame, per-joint limits, waist clamp, velocity/rate/gap/watchdog limits, pinch range. Every
  number carries the R3 warning and an UNMEASURED tag; see "Numbers chosen" below.
- `config/cameras.yaml` — `top`/`oblique`/`palm` with V4L2 selector, capture resolution/fps/fourcc (all
  UNMEASURED: no node was ever opened) and `policy_resolution` (640x480, 640x480, 320x240 — from 5.3, not a
  measurement). Orbbec is the left UVC RGB stream per D-009; the right node is recorded but unused.
- `config/hand.yaml` — Modbus 4000000 baud, slave 0x78, port UNMEASURED; 15 joint names, 7 motors, per-joint
  limits from the Paxini bundle; `joint_order_status: UNMEASURED` because docs/sdks.md 4.6 says the SDK slot
  order is a hypothesis; pinch synergy poses all literal UNMEASURED; glove thumb-index distance mapping.
- `config/board.yaml` — 600x600 mm, AprilTag family/ids/size/centres all UNMEASURED, horse/die dimensions
  UNMEASURED, `board_origin_in_base: UNMEASURED`, and an 88-cell placeholder table (48 track + 16 base +
  24 home) under `layout`, the whole block marked `layout_status: UNMEASURED` with a header saying the engine
  team owns the real topology. Layout: 15x15 Ludo cross at 40 mm pitch, `[(c-7)*40, (7-r)*40]`, arms 3 wide
  and 6 long. Track = the two outer lanes of each arm as one cycle; the arm-tip cell that a standard 52-cell
  ring would own belongs to the home lane instead, which is exactly what turns 52 into 48 and 5 home cells
  into 6. Starts R/G/Y/B = track-12/24/36/0, home entry = the cell before the next colour's start.
- `config/training.yaml` — rates 5.2, spaces 5.3, Diffusion (chunk 16, execute 8, DDIM 10, ResNet-18) and ACT
  (chunk 32, temporal ensembling) 5.7, augmentation with `geometric_on_top: false`, dataset 5.6, compute 5.8.
  No placeholders at all, by design.
- `runtime/config.py` — `load(name, root=None)` (schema of required dotted paths per file, `ConfigError`
  naming file and key), `config_hash(name)` (sha256 of the parsed doc re-dumped with sorted keys),
  `unmeasured(name)` (both tag forms, reported under the value's path, deduped, document order), plus
  `NAMES`, `REQUIRED_KEYS`, `CONFIG_DIR`, `STATUS_VALUES`. A `_status` key is itself validated: it must hold
  UNMEASURED or MEASURED and must annotate an existing sibling, so a typo cannot hide a placeholder.
- `tests/test_config.py` — 70 tests (loader mechanism on synthetic files in tmp_path; content invariants on
  the six real files). `docs/config.md` — the tag convention and one section per file.

### Commands run and measured results
1. `.venv/bin/python -m pytest -q` -> `103 passed, 1 skipped in 2.93s` (the skip is the pre-existing
   motion-marker autoskip "no session gate yet"); 70 of those are tests/test_config.py.
2. `.venv/bin/ruff check .` -> `All checks passed!` (exit 0).
3. Acceptance "all six files load": `test_every_config_loads` parametrised over
   `config.NAMES == ('board','cameras','hand','robot','safety','training')`. PASS
4. Acceptance "config_hash deterministic across two loads, changes when any value changes":
   `test_config_hash_is_deterministic_across_two_loads` (6 files, two calls each, 64 hex chars),
   `test_config_hash_is_stable_across_key_order` (same doc dumped unsorted / sorted / top level reversed ->
   one hash), `test_config_hash_changes_when_a_value_changes` (diffusion.chunk 16 -> 17 changes the hash),
   `test_config_hash_ignores_comments_and_whitespace`. PASS. Hashes at this commit:
   board a05d0595f7fe4789f98fb1e482cc177139fff8c700970a2d3836bcb1403b3c13
   cameras 35cae8290d946ce22790d6a6cf1188fb4699519cc538bc690dc13e2908b40595
   hand 5b615a57d18f05238d9533bc8b687a18b3053323756c201f30715b973a817d5d
   robot 1ae6aa90e41b9ae4b92ed94d6488fdf25552083806d6bbd698a87464100d53fc
   safety 6dc24062a636c07c03e3f25b2513a850e11807f602dbd3b08229254f6c2bc13a
   training e5cde12ff6a2a39345f80469bd1e9351533ca9fca5079c218a1fd1b5e09ab6c0
5. Acceptance "unmeasured('safety') non-empty, unmeasured('training') empty":
   `.venv/bin/python -c "from runtime import config; ..."` ->
   safety 10 entries `['workspace_box_m.min','workspace_box_m.max','workspace_box_m.margin_m',
   'joint_limits_rad','waist_yaw_clamp_rad','joint_velocity_limit_rad_s','command_rate_limit_hz',
   'command_gap_reset_s','watchdog_timeout_s','hand.pinch_rate_limit_per_s']`; training `[]`.
   Other counts: board 15, cameras 15, hand 12, robot 13. PASS
6. Acceptance "missing required key raises a clear error naming file and key":
   `test_missing_required_key_names_file_and_key` asserts the message contains `config/safety.yaml`,
   `'workspace_box_m.max'` and `missing required key`. `test_each_required_key_is_individually_enforced`
   goes further: it deletes each of the 94 required paths in turn across the six files and asserts every
   single deletion fails the load. PASS

### Numbers chosen in config/safety.yaml, and why (all UNMEASURED, R3: human commit only)
- Workspace box, pelvis frame, wrist point: x 0.15..0.65 m (in front of the chest, short of full reach),
  y -0.10..0.60 m (asymmetric to the robot's left because only the left arm is used), z -0.40..0.30 m
  (table height below the pelvis is unknown; this is the least defensible number in the file). 2 cm margin.
- Per-joint limits = the MJCF range tightened by 5 deg (0.0873 rad) each side, so a command can never ride a
  mechanical stop. `test_safety_joint_limits_are_strictly_inside_the_mechanical_range` asserts that exact
  relation against config/robot.yaml, so the two files cannot drift apart silently.
- Waist yaw clamp +/- 0.6 rad on top of the joint limit (+/- 2.5307): a 600 mm board needs no more, and the
  waist swings the whole upper body on a hip mount.
- Joint velocity 1.5 rad/s: a Ludo move is a slow pick-and-place; nothing here needs a fast arm.
- Command rate limit 60 Hz (>= the 50 Hz arm_sdk publish rate, so the driver is not starved, but a runaway
  loop cannot saturate DDS), gap reset 0.5 s, watchdog 1.0 s to release the arm_sdk weight, pinch rate
  2.0 /s. Session 7200 s default (4.6) with a 28800 s ceiling so a typo cannot grant a week-long window.

### Notes / deviations
- The task's deliverable list says `layout_status: UNMEASURED` marks the cell table, and separately lists
  `cell_pitch_mm` etc. at the top level. A bare `layout_status` with no `layout` sibling would violate the
  loader's own rule that a `_status` must annotate an existing key, so the topology keys (grid, pitch,
  colours, starts, home entries, lengths, cells) are nested under `layout:` and `layout_status` annotates
  that whole block. Same information, one fewer special case. Dotted paths changed accordingly
  (`layout.cells`, `layout.cell_pitch_mm`).
- 48 track cells and 6 home cells are not both achievable with the standard 52-cell Ludo ring geometry
  (a 3-wide arm of length L gives 2L+1 track cells per quadrant, which is always odd). The construction
  above resolves it by giving the arm-tip cell to the home lane. Recorded here because it is a real
  topological choice, not a rounding: the engine team's layout may differ and this table is replaceable.
- `unmeasured()` returns document order, not sorted order. Deterministic for a given file; noted in
  docs/config.md in case Fable prefers sorted.
- No hardware touched, no motion command, no session file read or written, no blockers, no new questions.
(T-003 config commit: b2e3bdc; this line and the TASKS.md result hash are the only content of the follow-up commit.)
## T-009  cloud/greennode.sh with a local fake transport  (2026-09-11T19:10+07:00)

### What was built
- `cloud/greennode.sh` — `up` / `train SCRIPT [ARGS...]` / `down` / `status [JOB_ID]`. Reads
  `~/.config/ludo-g1/env` (path overridable with `GREENNODE_ENV_FILE`; the script never writes it).
  Three transport primitives (`push_files`/`push_tree`, `pull_tree`, `remote_exec`) have a remote
  implementation (rsync + ssh + `docker run` the pinned image) and a local one (`cp -a --parents` into
  `GREENNODE_LOCAL_ROOT`, job run by `.venv/bin/python`). Everything above those three is one code path,
  so local mode exercises the real orchestration.
  - `up` pushes `git ls-files --cached --others --exclude-standard` minus `third_party/` (so a
    not-yet-committed script still travels and `data/`, `.venv/`, `__pycache__/` never do) plus the
    `data/raw` tree.
  - `train` always launches the job detached (`nohup setsid bash cloud/job_wrapper.sh ...`) so it
    outlives the ssh connection, then **waits** for the job's `.exit` file by default and fails with the
    log tail on a non-zero exit. `--detach` returns immediately; `--timeout N` bounds only the client's
    waiting and never kills the remote job.
  - `down` pulls `data/checkpoints` and mirrors the remote job logs into `data/logs/greennode/`.
- `cloud/job_wrapper.sh` — supervises one job on the remote: `JOB_ID.log`, `.pid`, `.heartbeat`
  (rewritten every `GREENNODE_HEARTBEAT_SECONDS`, default 5) and `.exit` under
  `<root>/data/logs/greennode/`. The `.exit` file is the completion signal the client polls.
- `cloud/Dockerfile` — `python:3.10.20-slim-bookworm`, pinned apt packages, `torch==2.5.1+cpu` from the
  CPU index, and the training subset of requirements.txt (the `file://` DexH15 wheel and `unitree_sdk2py`
  are laptop/robot-only and cannot resolve on the VM). Repo bind-mounted at `/work`, no project code in
  the image. Phase 3 GPU swap recorded as a TODO in the header with the candidate base image.
- `cloud/dummy_job.py` — waits `--seconds` (default **60**, the Phase 0 exit-check value) and writes
  hostname / platform / interpreter / start+finish times to `$LUDO_G1_CHECKPOINT_DIR/dummy/result.txt`.
- `tests/test_greennode_local.sh` (18 checks) + `tests/test_greennode_local.py` (pytest wrapper).
- `docs/cloud.md` — configuration, the two transports, job files, the Phase 0 exit-check command to run
  the moment Q-001 is answered, and an explicit "untested against the real VM" section.

### Commands run and measured results
1. Environment (the worktree has no .venv; it is git-ignored):
   `cd /home/alois/Desktop/ludo-g1-wt-t009 && uv venv --python 3.10 .venv && uv pip install --python .venv/bin/python -r requirements.txt`
   -> CPython 3.10.20, exit 0, all pinned packages installed.
2. Acceptance 1 (local-mode round trip), command: `bash tests/test_greennode_local.sh` -> exit 0,
   "all checks passed", 18/18. The chain run is the literal acceptance chain with no extra flags:
   `GREENNODE_TRANSPORT=local cloud/greennode.sh up` -> `... train cloud/dummy_job.py --seconds 1 --note ...`
   -> `... down`, and `data/checkpoints/dummy/result.txt` was produced with e.g.
   `hostname: aloisThinkpad`, `python: 3.10.20 (.../.venv/bin/python)`, `cwd: /tmp/ludo-t009-*/remote`
   (the fake remote root, i.e. the job really ran from the pushed copy, not from the repo).
   Heartbeat mirrored to `data/logs/greennode/t009-roundtrip.heartbeat`:
   `2026-09-11T12:05:16Z job=t009-roundtrip pid=1022857 state=finished exit=0`.
   `up` pushed 45 repo files (38 tracked + the 7 new files of this task); `third_party/` absent from the fake remote (asserted).
   `train --detach` returned in 0 s against a 5 s job and `status` showed `state=running` (asserted).
3. Acceptance 2 (remote mode refuses without credentials), same script, checks 1-4:
   `GREENNODE_TRANSPORT=remote cloud/greennode.sh up` with `HOME` and `GREENNODE_ENV_FILE` pointed into a
   temp dir -> exit 1, message names the missing file and points at `agents/QUESTIONS.md Q-001`; the test
   also asserts the run did not create the credentials file.
4. Acceptance 3 (no credential strings): `git grep --untracked -i -n -E "password|secret|token" -- cloud/`
   -> no output, exit 1 (= no matches). Asserted by `test_no_credential_strings_in_cloud`; `--untracked`
   is added so the check is real before the files are staged as well as after.
5. Gate: `.venv/bin/ruff check .` -> "All checks passed!", exit 0;
   `.venv/bin/python -m pytest -q` -> `35 passed, 1 skipped` (the skip is the pre-existing motion-marker
   autoskip "no session gate yet"), exit 0.

### Notes / deviations
- **The remote transport and the Dockerfile are untested.** No credentials (Q-001) and docker is not
  installed on this laptop, so `docker build`, `docker run`, `ssh` and `rsync` have never executed. Only
  the local transport ran. Said plainly in `docs/cloud.md` ("Status: untested against the real VM") and in
  the script header. Treat the first real run as bring-up.
- `train` waits by default instead of returning after the nohup launch. The task's acceptance chains
  `up && ... train ... && down`, which races if `train` returns while the job is still running; the job is
  still launched detached, so nothing about the remote-survivability property is lost. `--detach` gives the
  fire-and-forget behaviour and is tested.
- `up` uses `git ls-files --cached --others --exclude-standard` rather than `--cached` alone, so that a
  file written but not yet committed (including, at first run, these very cloud scripts) is pushed.
- Two bugs found and fixed while testing, both worth knowing about: `remote_cat` returned non-zero for an
  absent file, which `set -e` turned into a silent immediate exit of the whole poll loop; and the first
  `up` implementation pushed only tracked files, so the job script it was meant to ship was missing.
- Worktree-local environment fix, committed nothing: the git-ignored PyInstaller payload under
  `third_party/pxcap_pro_teleop_sdk/pxcap_pro_local/_internal/` (1.7 GB) exists only in the main tree, so
  `tests/test_docs_sdks.py::test_references_resolve_to_existing_lines` failed in this worktree before any
  of my changes. I made `_internal/` a real directory of symlinks into the main tree (a real directory so
  the existing `.gitignore` rule, which has a trailing slash, still matches it and `git status` stays
  clean). Nothing under `third_party/` was modified and nothing was staged. Any future worktree needs the
  same step; a `tools/` helper for it would be a reasonable small task.
- No hardware, no motion command, no blockers, no scripted motion. No disagreement with the task as
  written.
(T-009 cloud commit: 5540c10; this line and the TASKS.md result hash are the only content of the follow-up commit.)

## T-005  runtime/safety.py: envelope, session gate, rate limit; enable_session.py  (opus, 2026-09-11T21:55+07:00)

### What was built
- `runtime/types.py`: `MotionCommand` (7 arm + waist yaw + pinch + a `clamped: tuple[str, ...]` report
  field) and `RobotState` (the same three plus `ts_ns`). Frozen, `eq=False`, float64, input arrays copied
  and made read-only, `joints` (8) / `to_action()` (9) helpers in `config/robot.yaml action_order`.
- `runtime/safety.py`:
  - `SessionGate(path=None)` -> path from `config/safety.yaml session.file`, resolved against the repo
    root. `status(now=None) -> SessionStatus(valid, reason, enabled_by, expires_at)`, with `seconds_left`.
    Fails closed on: no file, unreadable, unparsable line, duplicate key, any missing
    `session.required_fields` entry, empty `enabled_by`, `checklist` != `session.required_checklist_value`,
    a timestamp with no UTC offset, `expires_at <= enabled_at`, window > `session.max_seconds`,
    `enabled_at` in the future, `expires_at` in the past. The file is `stat`ed on every call and re-parsed
    only when it changed; expiry is re-judged against the wall clock every call (a session that runs out
    mid-run stops the next command). Deliberately a strict 4-line parser, not yaml.
  - `Envelope.from_config(fk, root=None)`: joint limits, waist clamp folded in as the tighter of clamp and
    limit, box with `margin_m` applied inward, velocity, rate, gap-reset, watchdog, pinch range and slew.
    Cross-checks `config/safety.yaml` against `config/robot.yaml` (same joint names in the same order; a
    safety limit wider than the MJCF mechanical range is a `ConfigError`) as `config/safety.yaml` requires.
  - `Envelope.check(cmd, state, now_ns)` in order: command rate (reject), finiteness (reject), joint and
    waist limits (clamp, reported), pinch range (clamp) and pinch slew (clamp), joint velocity (reject),
    workspace box on the *clamped* target via the injected `fk` (reject). The reference is recorded only
    when every check passes. `reset()` drops it; `watchdog_timeout_s` is exposed, not implemented (driver).
  - `Guard(gate, envelope, *, simulated=False)`, `admit(cmd, state, now_ns=None)`: gate first unless
    simulated, then the envelope always. `Guard.from_config(...)` builds both.
- `tools/hardware_checks/enable_session.py`: `sys.stdin.isatty()` check (exit 2, nothing written), name
  prompt, the four 4.6 checklist items each requiring y/yes, atomic write (mkstemp + fsync + `os.replace`)
  of exactly the four lines in order with Asia/Bangkok offsets and `session.default_seconds`. Exit 1 on any
  refusal. Re-reads the file through the gate afterwards and reports the expiry.
- `tests/conftest.py`: docstring updated (the autoskip now reports the gate's own reason); logic unchanged.
- `tests/test_safety.py` (58 tests), `docs/safety.md`.

### Commands run and measured results
1. `.venv/bin/python -m pytest -q tests/test_safety.py` -> **58 passed** in 0.97 s.
   - Acceptance 1 (gate): `test_guard_on_hardware_refuses_without_a_valid_session[absent|expired|
     unconfirmed|unparsable]` all raise `SafetyViolation(rule="session_gate")` with `guard.admitted == 0`;
     `test_guard_on_hardware_accepts_with_a_valid_session` admits with a session file written into
     `tmp_path`. Plus `test_guard_on_hardware_stops_the_moment_the_session_expires` (same guard, file
     replaced with an expired one mid-run -> next `admit` raises). PASS
   - Acceptance 2 (simulated): `test_guard_simulated_skips_the_gate_but_never_the_envelope` -- admits with
     no session file anywhere, still raises `workspace_box` for an out-of-box fk point, still clamps
     `left_elbow_joint` to 2.0071 rad and reports `clamped == ("left_elbow_joint",)`, and
     `guard.session_status().valid is False` (simulated fakes nothing). PASS
   - Acceptance 3 (rate): `test_rate_limit_rejects_the_second_command_inside_one_period_and_accepts_after`
     -- 60 Hz -> period 16 666 666 ns; a command at period-1 ns raises `command_rate`, one at exactly the
     period is accepted. PASS
   - Acceptance 4 (velocity): `test_velocity_limit_rejects_a_target_too_far_from_the_measured_state` --
     fresh-reference allowance is 1.5 rad/s x 0.5 s = 0.75 rad; 0.675 rad accepted, 0.825 rad rejected with
     `joint_velocity`. Also tested against the previous accepted command inside the gap, and the
     gap > `command_gap_reset_s` fallback to the state. PASS
2. Acceptance 5: `.venv/bin/python tools/hardware_checks/enable_session.py < /dev/null` -> exit **2**,
   stderr "stdin is not a terminal ...", `ls hardware/` still holds only `README.md`. Asserted in
   `test_enable_session_without_a_tty_exits_2_and_writes_nothing`, which compares an existence+mtime
   snapshot of the real path rather than assuming it is absent.
3. Acceptance 6: `grep -rn "session.enable" --include=*.py . | grep -v third_party` -> hits in exactly
   `runtime/safety.py` (2), `tools/hardware_checks/enable_session.py` (2) and the tests
   (`tests/test_safety.py` 19, `tests/test_scaffold.py` 5, `tests/test_config.py` 1). No `.venv` hits.
   Guarded against regression by `test_only_safety_and_enable_session_name_the_session_file`.
4. Interactive path exercised through a pty (answers: name, `yes`, `no`) -> prompts printed, exit **1**,
   `'legs locked' not confirmed; nothing was written`, `hardware/` unchanged. The writing path itself was
   never run against `hardware/session.enable`: `session_text` / `write_session` are tested into `tmp_path`
   and the output is byte-compared to the CLAUDE.md 4.6 example
   (`enabled_at: 2026-09-15T14:02:11+07:00`, `expires_at: 2026-09-15T16:02:11+07:00`) and then fed back
   through `SessionGate` (valid).
5. Gate: `.venv/bin/ruff check .` -> "All checks passed!", exit 0. `.venv/bin/python -m pytest -q` ->
   **163 passed, 1 skipped**, exit 0. The skip is the motion-marker autoskip, now carrying the gate's real
   reason: "no valid hardware session: cannot read session file
   /home/alois/Desktop/ludo-g1/hardware/session.enable: No such file or directory" -- i.e. conftest is
   wired to `SessionGate.status()` end to end.

### Design notes Fable should look at
- **Fresh velocity reference.** The task says "relative to state"; `config/safety.yaml` says "against the
  previous accepted command"; `command_gap_reset_s` bridges them. Implemented: the reference is the
  previous accepted command and the monotonic time since it, but when that is older than
  `command_gap_reset_s` (or absent) the reference is the measured state *aged by exactly*
  `command_gap_reset_s`. Using `now_ns - state.ts_ns` instead would make the first command of a stream
  un-checkable (dt ~ 0 -> infinite implied speed) or wildly permissive (a stale state), so the allowance is
  a deterministic step of `velocity_limit x gap_reset` = 0.75 rad from where the arm actually is.
  `RobotState.ts_ns` is carried and recorded but is not used as that dt; say so if you want it used.
- **Clamp vs reject.** Joint limits, waist clamp, pinch range and pinch slew clamp (and report through
  `MotionCommand.clamped`); rate, non-finite, velocity, box and the session gate reject. Rationale in
  `docs/safety.md`. Pinch slew clamps rather than rejecting on purpose: a fast finger should not drop an
  arm command.
- **No fk fails closed.** `Envelope.from_config()` with no `fk` builds, but every `check()` then raises
  `workspace_box` ("no forward kinematics injected"). An `fk` that raises or returns anything but three
  finite metres is the same rejection. This keeps T-011 as the only thing that can *open* the box check.
- **Extra validation beyond the deliverable**, both from the comments in `config/safety.yaml`: the
  name/order cross-check against `config/robot.yaml`, and the refusal to build when a safety limit is wider
  than the mechanical range. Also a test asserting `runtime/safety.py` contains none of `os.environ`,
  `getenv`, `LUDO_`, `dev_mode`, `force` -- the no-bypass property as a test, not just a promise.
- `Guard.admit` calls `SessionGate.status()` on every command; that is one `os.stat` per command at 30 Hz
  (re-read only when the file changes), which is the cost of R1 taking effect mid-run.

### Not done / limits
- No hardware was touched; no motion command was sent; `hardware/session.enable` was never created,
  edited or read from (only `os.stat`ed by the gate's default path in two tests).
- The real forward kinematics is T-011, so the box check has only ever run against mock fks.
- `config/safety.yaml` was read, never edited. Nothing under `third_party/` touched. No blockers, no
  scripted motion, no disagreement with the task as written.
(T-005 commit: 8c03733; this line and the TASKS.md result hash are the only content of the follow-up commit.)

---

## T-012  Dependencies and assets for the arm IK path  (opus, 2026-09-11T19:45+07:00)

### What changed
- **`third_party/unitree_g1_mjcf/` (new, tracked).** Byte-identical copy of
  `/home/alois/Teleopit/assets/robots/unitree_g1/` restricted to `g1_29dof.xml`, `LICENSE`, `README.md`
  and exactly the 35 meshes the XML references (all under `meshes/g1/`), with the `meshes/` relative
  layout kept so `meshdir="meshes"` resolves. **38 files, 19 697 688 bytes (19 MB)**; the source
  `meshes/` tree is 63 MB (dex3 16 MB, o6_left 14 MB, o6_right 14 MB, avp 1.3 MB, g1 19 MB) and none of
  the other variants are referenced by this XML. The mesh list was extracted from the XML by regex, not
  by hand, and the vendored set is asserted equal to it in the test. The source tree was never modified
  (`diff -r --brief` of both trees: no differences).
- **`MANIFEST.txt`** in that directory: a comment header with `source_root`, `copy_date`, selection
  rationale, file and byte counts, then, per file, a `# src: <absolute source path>  copied: <ISO8601>
  bytes: <n>` comment followed by a `sha256sum`-format line. GNU `sha256sum -c` ignores the `#` lines
  (verified), so the file is both human-readable and directly checkable.
- **`requirements.txt`**: added `mujoco==3.13.0`, `mink==1.3.0` (direct) and `pico_bridge` 0.2.1 by
  release URL with `--hash=sha256:7cf0fee07c76541fd06e2ee6bdeec3d11fec578cd4ef6b45179dec4af31b369f`;
  removed `opencv-python-headless==5.0.0.93` (D-008); added the 26 new transitive pins. Header comment
  on line 3 no longer claims mujoco is unwanted.
- **`config/robot.yaml`**: `limits_source` repointed to `third_party/unitree_g1_mjcf/g1_29dof.xml`
  (now repo-relative, was absolute), its comment block rewritten, and `mjcf_qpos_index` added to each of
  the 8 joint entries. No other key touched.
- **`tests/test_assets.py`** (new, 10 tests) and **`docs/setup.md`** (resolved versions, the wheel hash,
  the OpenCV rule, a "Vendored G1 model" section).

### Resolved versions (uv, Python 3.10.18 venv)
`mujoco==3.13.0` (latest 3.x), `mink==1.3.0` (latest; requires `mujoco>=3.1.6`), `pico_bridge==0.2.1`.
29 packages installed in total. New transitive pins: absl-py 2.5.0, aioice 0.10.2, aiortc 1.15.0,
attrs 26.1.0, av 17.1.0, cffi 2.1.1, cryptography 50.0.1, daqp 0.9.1, dnspython 2.8.0, etils 1.13.0,
fsspec 2026.7.0, glfw 2.10.2, google-crc32c 1.8.0, ifaddr 0.2.0, importlib-resources 7.1.0,
pillow 12.3.0, psutil 7.2.2, pyarrow 25.0.1, pycparser 3.0, pyee 13.0.1, pylibsrtp 1.0.0,
pyopengl 3.1.10, pyopenssl 26.4.0, qpsolvers 4.13.0, rerun-sdk 0.37.2, zipp 4.1.0.

### Measured qpos addresses (mujoco 3.13.0, `MjModel.jnt_qposadr`)
Model: `njnt=30`, `nq=36`, `nv=35`; joint 0 is `floating_base_joint` (free, 7 qpos), so every hinge's
qpos address is its 29-joint index + 7.

| joint | 29-joint index | jnt id | mjcf_qpos_index |
|---|---|---|---|
| waist_yaw_joint | 12 | 13 | 19 |
| left_shoulder_pitch_joint | 15 | 16 | 22 |
| left_shoulder_roll_joint | 16 | 17 | 23 |
| left_shoulder_yaw_joint | 17 | 18 | 24 |
| left_elbow_joint | 18 | 19 | 25 |
| left_wrist_roll_joint | 19 | 20 | 26 |
| left_wrist_pitch_joint | 20 | 21 | 27 |
| left_wrist_yaw_joint | 21 | 22 | 28 |

The model's `jnt_range` for all 8 also equals the `limit_rad` already in `config/robot.yaml`
(asserted to rel 1e-5 in `test_yaml_joint_ranges_match_the_model`), which independently confirms the
T-002 limit extraction.

### Commands run and results
1. `/home/alois/.local/bin/uv pip install --python .venv/bin/python -r requirements.txt`
   -> `Installed 29 packages`.
2. `/home/alois/.local/bin/uv pip install --python .venv/bin/python -r requirements.txt --dry-run`
   -> `Resolved 51 packages` / `Checked 51 packages` / **`Would make no changes`**.
3. **Acceptance 1.** `.venv/bin/python -c "import mujoco, mink, pico_bridge"` -> **exit 0**.
4. **Acceptance 2.** `.venv/bin/python -m pytest tests/test_assets.py -q` -> **10 passed**.
   `.venv/bin/python -m pytest -q` -> **173 passed, 1 skipped** (163 before this task + 10 new; the skip
   is the usual motion autoskip). `.venv/bin/ruff check .` -> **All checks passed!**, exit 0.
5. **Acceptance 3.** `cd third_party/unitree_g1_mjcf && sha256sum -c MANIFEST.txt` -> **exit 0**,
   38 output lines, **38 `: OK`, 0 `FAILED`**. Tail of the output:
   ```
   meshes/g1/torso_link_rev_1_0.STL: OK
   meshes/g1/waist_roll_link_rev_1_0.STL: OK
   meshes/g1/waist_yaw_link_rev_1_0.STL: OK
   ```
6. **Acceptance 4.** `.venv/bin/python -c "import cv2; print(cv2.__file__)"` ->
   `/home/alois/Desktop/ludo-g1/.venv/lib/python3.10/site-packages/cv2/__init__.py`, exit 0.
   `uv pip list | grep -i opencv` -> exactly one line, `opencv-python 5.0.0.93`.
7. Hash enforcement check: the same URL requirement with one hex digit of the sha256 changed, installed
   into a throwaway venv with `--no-deps`, aborts with
   `Hash mismatch for pico-bridge ... Expected: sha256:00000000... Computed: sha256:7cf0fee0...`.
   So uv does verify the pin; `--dry-run` alone does not (it downloads nothing).
8. Source-fidelity check: `diff -r --brief /home/alois/Teleopit/assets/robots/unitree_g1/meshes/g1
   third_party/unitree_g1_mjcf/meshes/g1` and `diff -q` on the XML -> no output, identical.

### Findings worth Fable's attention
- **Removing `opencv-python-headless` broke `cv2` and the repair is not automatic.** Both distributions
  install into the same `site-packages/cv2/`; `uv pip uninstall opencv-python-headless` deleted the
  shared files, after which `import cv2` still *succeeded* (as an empty namespace package, `__file__`
  is `None`) while every attribute was gone -- `cv2.__version__` raised `AttributeError`. The literal
  acceptance command `import cv2; print(cv2.__file__)` exits 0 in that broken state, so it is not a
  sufficient check on its own. Fixed with
  `uv pip install --reinstall-package opencv-python -r requirements.txt`; the checks above were re-run
  after the fix and `cv2.__version__` is `5.0.0`. Documented in `docs/setup.md`. This is D-008's hazard
  showing up exactly as predicted; anyone recreating the venv from scratch is unaffected.
- **The vendored `README.md` is Teleopit's and describes files that are not here** (dex3 / o6 / avp
  meshes, a `download_assets.py` script, and a claim that the directory is git-ignored). It is copied
  verbatim because nothing under `third_party/` is modified (section 7). `MANIFEST.txt` carries the
  correct description of what this copy contains, and `docs/setup.md` repeats it. If Fable prefers, the
  clarification belongs in a sibling file, not in the copied README.
- **`docs/config.md:87` still says "T-012 vendors it into ..." in the future tense.** Out of the file
  list for this task, so untouched; a one-line update is a candidate for the next task that owns
  `docs/config.md`.
- `mjcf_qpos_index` was added per joint entry (8 entries) rather than as one top-level mapping, so the
  address sits next to the `index` and `limit_rad` it belongs with. `runtime/config.py` `REQUIRED_KEYS`
  was not touched: it cannot traverse lists, and the key is checked by `tests/test_assets.py` instead.
- `pico_bridge` drags in a large transport stack (aiortc/av/rerun-sdk/pyarrow, ~26 transitive pins) for
  what LUDO-G1 uses as a pose reader. Nothing in the wheel is optional at import time, so it is taken as
  is. Flagging the footprint, not proposing a change.

### Not done / limits
- No hardware was touched, no motion command was sent, `hardware/session.enable` was never created,
  edited or read. Nothing existing under `third_party/` was modified; the only addition is the new
  `unitree_g1_mjcf/` directory. `config/safety.yaml` untouched.
- The MJCF is only *loaded* here. Pinning the legs and the right arm for the IK is T-013 and is done at
  load time in code, never by editing the asset.
- No disagreement with the task as written; no blockers.
(T-012 commit: aa8f9cc; this line and the TASKS.md result hash are the only content of the follow-up commit.)

## T-011  runtime/fk.py: left-arm forward kinematics for the workspace box  (opus, 2026-09-11T19:47+07:00)

### What changed
- **`runtime/fk.py`** (new, 119 lines). `left_arm_fk(q7, waist_yaw) -> np.ndarray (3,) float64`, the
  position of `config/safety.yaml` `workspace_box_m.point` (`left_wrist_yaw_link`) in the pelvis frame.
  It is also callable as `left_arm_fk(joints8)` over `config/robot.yaml` `action_order`, which is the
  `runtime.safety.FkFn` signature the envelope calls; one implementation, two call shapes, so nothing
  has to wrap it. Model: **`third_party/unitree_g1_mjcf/g1_29dof.xml`**, the T-012 vendored MJCF, via
  mujoco 3.13.0. The 8 commanded joints are written to the `mjcf_qpos_index` addresses from
  `config/robot.yaml` (re-measured against the model at build time, not trusted); every other joint is
  held at the model's `qpos0`, which is zero for all of them; the floating base is pinned to
  `(0,0,0, 1,0,0,0)` so positions come out pelvis-relative; `mj_kinematics` only (no dynamics, no
  contacts, no gravity). Model and `MjData` are compiled once and cached behind an `RLock`. The
  constructor also refuses a `workspace_box_m.frame` that is not `g1_pelvis` and a `point` that is
  neither a body nor a site of the model.
- **`runtime/safety.py`** (wiring only). `Envelope.from_config(fk=None)` now imports and injects
  `runtime.fk.left_arm_fk`; an explicit `fk` still overrides it. The import is inside the method so
  that reading robot state does not pull mujoco in. `Envelope(...)` built directly still defaults to
  `fk=None` and still fails closed. No check was weakened, added or reordered; the other two edits are
  the `FkFn` comment and one stale "the real fk is T-011" string in the failure message.
- **`tests/test_fk.py`** (new, 22 tests).
- **`tests/test_safety.py`**: one test had to change. `test_an_envelope_without_fk_fails_closed` built
  its no-fk envelope with `Envelope.from_config()`, which now has a default fk, so it would have been
  asserting nothing. It now builds the envelope through a new `bare_envelope()` helper that calls the
  constructor directly with `fk` omitted, and additionally asserts `env.fk is None` before checking
  that `check()` raises `workspace_box`. Nothing else in that file was touched.
- **`docs/safety.md`**: new subsection "The box: frame, point, and the kinematics behind it" — the
  pelvis frame and its axes, what the box covers in words and millimetres, the fact that the checked
  point is the wrist and that the hand and a held horse stick out past it until the Phase 1 tool
  offset exists, how the fk is computed, its cost, and where the all-zero pose sits.

### How the acceptance criteria were verified
Commands and measured results (all on `.venv/bin/python`, mujoco 3.13.0, no hardware):

1. `.venv/bin/python -m pytest tests/test_fk.py -q -s` -> **22 passed** in 5.1 s.
2. **Zero pose within 1 mm of the model's published wrist offset.** The expected value is computed in
   the test from the XML alone: ElementTree parses the `pos`/`quat` attributes of the bodies on the
   path `pelvis -> waist_yaw -> waist_roll -> torso -> left_shoulder_pitch -> ... ->
   left_wrist_yaw_link` and chains them with quaternion arithmetic written in the test file (at zero
   angles every joint contributes the identity rotation). mujoco is not in that path.
   - XML chain: `[0.19977428, 0.14866142, 0.09523278]` m
   - `left_arm_fk(zeros(7), 0.0)`: `[0.19977428, 0.14866142, 0.09523278]` m
   - **max |difference| = 3.098e-09 m**, criterion 1e-3 m. PASS
3. **20 random configurations agree with an independent evaluation within 1e-6 m.** mujoco is the only
   implementation, so per Fable's guidance the cross-check is a second mujoco evaluation built from
   scratch in the test — its own `MjModel` and `MjData` (no shared scratch state), the same 8 values
   written in **reverse** address order, base pinned afterwards rather than before. 20 configurations
   drawn uniformly inside `config/safety.yaml`'s joint limits, seed 20260911.
   - **worst disagreement = 0.000e+00 m** (bit-identical), criterion 1e-6 m, guidance 1e-9 m. PASS
4. **Waist yaw moves the wrist**, and moves it as a yaw: `|fk(0, waist=0.5) - fk(0, waist=0)| = 0.0745 m`
   (> 0.01 m required), with the z coordinate and the xy radius unchanged to 1e-9 m. PASS
5. **Deterministic and leak-free**: `fk(a)` is bit-identical after 5 interleaved `fk(b)` calls; the
   returned array is a fresh copy (mutating it does not change the next result); the model object is
   the same instance after a call (compiled once). PASS
6. **`Guard.admit` with the real fk rejects an out-of-box target** (the acceptance criterion): with a
   valid session file written into `tmp_path`, `q = zeros(8)` except shoulder pitch `-2.5` puts the
   wrist at `[-0.0390, 0.0153, 0.5604]` m; `admit` raises `SafetyViolation(rule="workspace_box")`
   naming `left_wrist_yaw_link` and the box, and `guard.admitted` stays 0. The companion test admits
   the all-zero target (inside the box) and `admitted` becomes 1, so the rejection is not vacuous. PASS
7. **Input validation**: wrong sizes (6, 8 with an explicit waist; 7, 9 without) raise `ValueError`;
   a NaN or inf joint raises `ValueError` before mujoco sees it. PASS
8. **Call time**: `left_arm_fk` mean over 1000 calls = **8.4 us** (one warm-up call first). The
   envelope calls it once per command; at the 60 Hz `command_rate_limit_hz` the budget is 16.7 ms, so
   the fk is 0.05% of it. Compiling the MJCF once costs ~0.2 s at first use.
9. `.venv/bin/ruff check .` -> "All checks passed!". `.venv/bin/python -m pytest -q` -> **195 passed,
   1 skipped** (the skip is `test_scaffold.py`'s motion test, no session file). Before this task: 173
   passed. +22 from `tests/test_fk.py`.

### Findings worth Fable's attention
- **The all-zero pose is INSIDE the current placeholder box, not outside.** The wrist at all-zero
  joints is at `(0.199774, 0.148661, 0.095233)` m; the box after the 20 mm margin is
  `[0.17, -0.08, -0.38] .. [0.63, 0.58, 0.28]`. The task notes predicted it would be outside because
  "the arm hangs down at zero" — on the G1 it does not: shoulder pitch zero points the upper arm
  forward, and the wrist ends up ~200 mm in front of and ~150 mm to the left of the pelvis, 95 mm
  above it. This is a fact for the envelope review, not something to fix; `config/safety.yaml` was not
  touched (R3). One consequence worth stating: the box alone does not reject a command that parks the
  arm at zero. `tests/test_fk.py::test_where_the_all_zero_pose_sits_relative_to_the_placeholder_box`
  prints the numbers and asserts the "inside" verdict, so if a human changes the box the test fails
  and both it and `docs/safety.md` get re-checked rather than drifting.
- **Two of the 8 joints cannot move the checked point at all.** `left_wrist_yaw_joint` rotates the
  wrist frame about its own origin, and `left_wrist_roll_joint` turns about the x axis that the
  remaining 0.038 + 0.046 m of the chain lies along; both move the wrist origin by < 1e-9 m for a
  0.3 rad step (measured, `test_every_commanded_joint_reaches_the_point`). So the box constrains 6 of
  the 8 commanded joints, and the two wrist rotations are unconstrained by it. They are constrained by
  the joint limits, and they will matter to the box only once the tool offset to the fingertip exists
  (Phase 1) and the checked point moves off the wrist axis. Recorded in `docs/safety.md`.
- **The box is checked at one point and the hand is not in the model's commanded chain.** The MJCF's
  `left_rubber_hand` is the stock G1 hand, not the DexH15, and no DexH15 geometry exists here. When
  Phase 1 measures the tool offset, the cheap correct fix is to check a *second* point (the fingertip
  pinch point) against the same box, not to shrink the box by a guess. Proposing, not applying.
- `left_arm_fk` deliberately accepts both `(q7, waist_yaw)` (the T-011 deliverable signature) and
  `(joints8)` (the `FkFn` signature `Envelope` calls). The alternative was a second wrapper function
  in `runtime/fk.py` or a lambda in `safety.py`; one function with an optional second argument is less
  code and gives the envelope a named callable it can be compared against in a test
  (`env.fk is fk.left_arm_fk`). Disagreement: none with the task; recording the choice.
- `reset_cache()` existed in a draft and was removed: nothing but a test wanted it, and section 7 says
  no configurability that no task asked for. Caching is verified through `kinematics()` identity.
- The fk import in `Envelope.from_config` is function-local on purpose. Importing `runtime.safety` at
  module scope is done by `tests/conftest.py` and by every read-only path; pulling mujoco (~0.2 s and
  ~100 MB) into a state read for a check that only motion needs is not worth it. It is imported on the
  first `from_config()` call, which is before any command can be sent.

### Not done / limits
- No hardware was touched and no motion command was sent. `hardware/session.enable` was never created,
  edited or read; the session files in these tests are written into pytest's `tmp_path` and are handed
  to `SessionGate(path)` explicitly, exactly as `tests/test_safety.py` already does.
- `config/safety.yaml` and everything under `third_party/` are unchanged (`git status` clean for both).
- `runtime/fk.py` imports nothing from `tools/hardware_checks/` and contains no joint target, waypoint
  or pose literal: the only constant is the identity base pose, which is a frame definition (R2).
- The 1e-6 m criterion is met with 0 m of disagreement, but that is a *consistency* check between two
  mujoco evaluations of the same asset, not a check of the asset against the real robot. The MJCF's
  link offsets are Unitree's published numbers; whether this robot matches them is a Phase 1
  measurement and is not claimed here.
- No blockers.
(T-011 commit: d9596d9; this line and the TASKS.md result hash are the only content of the follow-up commit.)
## T-007  Engine contract and scripted stub engine  (opus, 2026-09-11T22:55+07:00)

Branch `wt/t007` in the worktree `/home/alois/Desktop/ludo-g1-wt-t007`. Files added:
`engine/interface.py`, `engine/cells.py`, `engine/stub.py`, `engine/scripts/eval_20_moves.yaml`,
`tests/test_engine_stub.py`, `docs/engine.md`. Nothing else touched; no hardware, no motion command.

### What was built
1. **`engine/interface.py`** -- CLAUDE.md 5.5 field for field: `Primitive` (`move`/`roll`/`recover`),
   `Cell(id, board_xy_mm, top_px)`, `Command(primitive, src, dst, horse_id)`,
   `Outcome(success, observed_state_delta, failure_mode)`, `EngineClient(next_command, report,
   board_state)`. Additions, and only these: type annotations, docstrings, `frozen=True` on the three
   dataclasses, and `ABC`/`@abstractmethod` on `EngineClient` (CLAUDE.md 5.1 calls for an "abstract
   EngineClient"). 5.5's `Optional[X]` is spelled `X | None` -- the identical type, and the only
   spelling ruff's `UP045` accepts under this project's lint rules (section 7). The stub imports from
   this file and nothing in this file knows the stub exists.
2. **`engine/cells.py`** -- `load_cells()` builds the 88 `Cell`s from `config/board.yaml` (48 track +
   4 x (6 home + 4 base)), `load_layout()` reads the topology (colours, starts, home entries, lengths).
   `top_px` is `None` for every cell unless a calibration mapping is passed
   (`load_cells(top_px={...})`), because the Brio pixel of a cell is a property of where the camera is,
   not of the board; `board/calibration.py` (T-008) is what will supply it. An id in `top_px` that the
   board config does not define is an error, so a calibration of a different board cannot pass silently.
3. **`engine/stub.py`** (297 lines) -- `StubEngine(seed, script=None, *, cells, layout, robot_color,
   max_reissues, bowl_cell)`. Random mode plays 4 colours x 4 horses; only the robot's colour (default
   `R`) produces commands and the other three are simulated internally, so `board_state()` keeps moving
   without the robot being asked to touch another player's piece. A turn is ROLL then, if legal, one
   MOVE. Enter-from-base on a **1 or a 6** (`ENTER_ROLLS`; the co ca ngua variant admits both -- this is
   the one rules choice the task left open, made in one constant and documented in `docs/engine.md`).
   Progress 0..47 track / 48..53 home lane, overshoot illegal. A capture is emitted as **two** MOVEs --
   the captured horse out to its base first, then ours onto the cell just cleared -- because the robot
   has to clear that horse with its own arm; 5.5 says MOVE "covers enter-from-base and capture", so both
   are ordinary MOVEs. Script mode hands out exactly the scripted commands, then `None`.
4. **`engine/scripts/eval_20_moves.yaml`** -- 20 MOVE commands over 10 distinct `(src, dst)` pairs, each
   pair twice (two samples per pair for a per-pair success rate), reordered on the second pass so no
   pair runs back to back. Covers an enter-from-base, hops on each arm, the 80 mm arm-tip step, a home
   entry and a move inside the home lane. `load_script()` resolves cell **ids** against
   `config/board.yaml`, so a script can never address a cell that does not exist.
5. **`docs/engine.md`**, **`tests/test_engine_stub.py`** (30 tests).

### Acceptance, each run and measured
Commands: `.venv/bin/python -m pytest tests/test_engine_stub.py -q` -> `30 passed` (2.4 s), and the
measurement script below (stdout quoted verbatim).

1. *same seed -> identical command sequence over 200 commands*
   `test_same_seed_gives_an_identical_sequence_of_200_commands`, `test_different_seeds_diverge`.
   Measured: `A1 determinism: len(a)=200  a==b: True  a!=c: True` (seed 7 twice, vs seed 8).
2. *after a failed report, RECOVER at the failing cell, then the original again; after two failures the
   third next_command is a different turn*
   `test_failure_yields_recover_at_the_failing_cell_then_the_original_command`,
   `test_two_failed_reissues_give_the_turn_up_and_the_game_moves_on` (asserts the criterion literally:
   after failure 2, next_command #1 is the RECOVER, #2 is the original for the last time, #3 is a new
   turn -- a ROLL, `board_state()["turn"]` incremented, one entry in `engine.failures` with
   `attempts: 3` and `failure_modes: ["missed_cell"] x 3`),
   `test_a_failed_recover_counts_against_the_command_it_protects`.
3. *every Command's src/dst are cells that exist in config/board.yaml*
   `test_every_command_addresses_a_cell_that_exists_in_board_yaml` (300 commands with a forced failure
   every 7th, so RECOVER commands are covered too), `test_every_move_addresses_two_cells_and_a_horse`,
   `test_board_state_stays_consistent_across_a_game`. Measured over 1000 commands of seed 7:
   `{'roll': 491, 'move': 509} enter-from-base: 60 capture-clears: 42 into-home: 23`,
   `A3 unknown-cell commands in 1000: 0 | cells in board.yaml: 88`, and
   `A3 with a failure every 7th command, 300 cmds, unknown cells: 0`.
4. *eval_20_moves.yaml loads and yields exactly 20 MOVE commands with >= 10 distinct (src, dst) pairs*
   `test_eval_20_moves_yields_exactly_twenty_moves_over_ten_distinct_pairs`. Measured:
   `A4 eval_20_moves: 20 commands, all MOVE: True, distinct pairs: 10, then next_command(): None`.

Gate: `.venv/bin/ruff check .` -> "All checks passed!"; `.venv/bin/python -m pytest -q` ->
`135 passed, 1 skipped` (the skip is the pre-existing motion autoskip "no session gate yet").

### Two design calls worth Fable's attention
- **A ROLL addresses no cell.** Fable's guidance says a failed ROLL should be recovered at "the bowl
  cell". There is no such cell: `config/board.yaml` defines none and `die.bowl_centre_mm` is UNMEASURED,
  and inventing coordinates would drop a goal heatmap somewhere real on the board. Acceptance 3 also
  requires every addressed cell to exist in the board config. So ROLL (and any RECOVER after one)
  carries `src = dst = None` by default, and `StubEngine(bowl_cell=Cell("bowl", (x, y), None))` takes a
  measured bowl when there is one -- tested (`test_roll_addresses_the_bowl_when_one_is_supplied`). One
  line to change when the board config grows a bowl cell.
- **A finished board is dealt again.** Not in the task; found by measurement. A horse in the home lane
  can never leave it, so once a colour is home the generator produced nothing but ROLLs forever: seed 7
  gave `{'roll': 888, 'move': 112}` over 1000 commands, everything home by ~turn 250. `winner()` now
  detects it and random mode deals a fresh board (`board_state()["game"]` counts the deals) **without
  resetting the RNG**, so the stream stays pinned to the seed. After the fix the same 1000 commands are
  `{'roll': 491, 'move': 509}`. The alternative -- `next_command()` returning `None` at the end of one
  game -- was rejected because games run 128-336 commands (10 seeds measured), which is below the 200 of
  acceptance 1 and far below a collection session's few hundred episodes. Pinned by
  `test_a_finished_game_is_dealt_again_so_a_collection_run_never_stalls`.

### Notes
- `next_command()` raises `RuntimeError` if the previous command was not reported, and `report()` raises
  with nothing outstanding. An engine that tolerated a missing report would let `runtime/controller.py`
  lose an execution and still produce a plausible-looking dataset.
- No scripted motion, no import of `tools/hardware_checks/`, nothing under `third_party/`,
  `config/safety.yaml` and `config/board.yaml` untouched. R1-R6 intact; retries are orchestration (R2).
- Same worktree environment step as T-009: `_internal/` under
  `third_party/pxcap_pro_teleop_sdk/pxcap_pro_local/` recreated as a real directory of symlinks into the
  main tree so `tests/test_docs_sdks.py` can see the git-ignored payload. Nothing staged, nothing
  modified under `third_party/`. A `tools/` helper for this is still worth a small task.
- **Pre-existing flake, not mine:** `tests/test_greennode_local.py::test_greennode_local_round_trip`
  fails intermittently (1 of 6 full-suite runs; 1 of 15 runs of that test alone) on
  `FAIL - status shows the detached job running`, with the job reported as
  `state=starting` rather than `running` -- a race between `train --detach` returning and the job's
  heartbeat reaching `running`. It reproduces on a clean tree with none of T-007's files. T-009 is not
  mine to edit, so it is reported here rather than fixed; a `state=(starting|running)` match, or polling
  the heartbeat for a moment, would close it.
- No disagreement with the task as written beyond the two design calls above. No blockers.
(T-007 commit: 5cab3e6; this line and the TASKS.md result hash are the only content of the follow-up commit.)

## T-006  Mock drivers with the real driver interfaces  (opus, 2026-09-11T20:05+07:00)

### What was built
- `drivers/interfaces.py` — five `typing.Protocol`s (`ArmDriver`, `HandDriver`, `GloveDriver`,
  `PoseDriver`, `CameraDriver`), all `runtime_checkable`, plus three frozen sample dataclasses
  (`HandState`, `GloveSample`, `WristPose`). No behaviour, no config, no device knowledge. Every
  sample is a `runtime.clock.Stamped`; both write calls are documented as guard-only paths.
- `drivers/mock/ticker.py` — the one timing mechanism: a grid `origin + k*period` on an injectable
  clock. `ticks()` (drains, for the arm's integration) and `sample()` (newest point, for everything
  else). No threads, no sleeping.
- `drivers/mock/g1_arm.py` `MockArm` — first-order lag towards the last **admitted** target,
  `q += (target-q)*(1-exp(-dt/tau))` per state tick; `poll()` drains the stream, `read_state()`
  returns the latest. Guard from `Guard.from_config(simulated=True)`; `send_targets` calls
  `guard.admit(cmd, state, now_ns)` with the *injected* clock, so the rate and velocity limits are
  judged on the same time base the test controls.
- `drivers/mock/dexh15.py` `MockHand` — `send_pinch` admits a `MotionCommand` whose joints are held
  at the rest pose (zero = MJCF `qpos0`) and only the pinch varies, then expands the **admitted**
  scalar through the synergy; `palm_frame()` is a `MockCamera("palm")`.
- `drivers/mock/cameras.py` `MockCamera` — one class for all three streams, sized from
  `policy_resolution`; deterministic gradient frame carrying a 32-bit frame counter in row 0, read
  back by `frame_index()` (a dropped/repeated frame is provable in the Phase 2 recorder tests).
- `drivers/mock/pxcap.py` `MockGlove` (17 channels, degrees, triangle-wave pinch),
  `drivers/mock/pico.py` `MockPose` (metres + xyzw quaternion, pico_bridge conventions).
- `drivers/__init__.py` — `make(name, backend="mock", **kwargs)` over `DEVICES = (arm, hand, glove,
  pose, top, oblique, palm)`; camera names checked against `config/cameras.yaml`; `backend="real"`
  raises `NotImplementedError` naming the device.
- `tests/test_mock_drivers.py` (51 tests), `docs/drivers.md`.

### Config additions (the one config edit the task allowed; `REQUIRED_KEYS` untouched)
Both are new top-level `mock:` blocks, read only by `drivers/mock/*.py`, following docs/config.md.
- `config/robot.yaml` `mock:` — `arm_tau_s: 0.08` (+`_status: UNMEASURED`; a stand-in so Phase 2 has
  a response to measure, **not** a claim about the arm, whose real number is `latency.arm_ms` plus
  joint dynamics), `state_hz: 100` (no status: a design choice, pinned by a test to equal
  `config/training.yaml rates.state_hz`; `control.state_hz: 500` is the real DDS publish rate and
  would have made the 10 s acceptance check 5000 samples), `pose_hz: 120` (+status, docs/sdks.md
  7.1), `pose_cycle_s`, `pose_radius_m` (waveform shape, describes nothing real).
- `config/hand.yaml` `mock:` — `open_pose` / `closed_pose`, 15 values each (+status UNMEASURED), and
  `glove_cycle_s`, `glove_angle_amplitude_deg`. **The real `pinch.open_pose` / `pinch.closed_pose`
  are untouched and stay the literal UNMEASURED**: a wrong pose there closes the hand on a finger,
  and `drivers/dexh15.py` must refuse to run while they are. The mock needed two distinct 15-vectors
  to interpolate (acceptance 4) and could not get them from a placeholder that must stay a
  placeholder; the stand-ins are the `joint_limits_rad` extremes for thumb+index+middle and a fixed
  curl for the idle fingers, identical in both poses as 5.4 requires. A test asserts both that the
  mock poses are UNMEASURED and that the real ones still are (R5).
Config hashes changed by this: robot `1ae6aa90` -> `9dc5e64a`, hand `5b615a57` -> `6f1507d5`.
`unmeasured("robot")` 13 -> 15, `unmeasured("hand")` 12 -> 14. `config/safety.yaml` untouched.

### Commands run and measured results
- `.venv/bin/python -m pytest tests/test_mock_drivers.py -q` -> **51 passed in 1.4 s**.
- `.venv/bin/python -m pytest -q` -> **276 passed, 1 skipped in 21.8 s** (225 before, +51 new; the
  skip is the motion autoskip with no session file).
- `.venv/bin/ruff check .` -> "All checks passed!".
- Acceptance 1 — 10 s of mock arm state at 100 Hz: **1000 samples**, every inter-sample period
  exactly 10 000 000 ns, timestamps strictly increasing, `Stamped.ts_ns == payload.ts_ns` for all.
  Wall time for those 10 s of stream (driver construction included): **41.7 ms**.
- Acceptance 2 — out-of-envelope target raises `SafetyViolation`: a 2.5 rad step ->
  `rule=joint_velocity` ("5.000 rad/s, limit 1.5"); a slow ramp of shoulder pitch (0.02 rad per
  20 ms = 1.0 rad/s, inside the velocity limit) is refused at -0.76 rad with
  `rule=workspace_box` ("left_wrist_yaw_link at [0.2842, 0.0954, 0.281] m is outside the box"). A
  NaN target -> `rule=non_finite`, and a refused command leaves the simulated arm where it was.
- Acceptance 3 — `top` (480, 640, 3) uint8, `oblique` (480, 640, 3) uint8, `palm` (240, 320, 3)
  uint8, each matching its `policy_resolution`; `frame_index()` round-trips up to 2**32-1.
- Acceptance 4 — `synergy(0.0)` and `synergy(1.0)` are the two 15-vectors from `config/hand.yaml`
  exactly; **9 of 15 joints differ** (the 6 idle ring/pinky joints are identical by design),
  L1 distance 13.85 rad; `synergy(0.5)` is their midpoint.
- Acceptance 5 — `grep -rn "Guard(" drivers/ | grep -v mock` -> **empty** (exit 1). The test also
  greps `Guard.from_config(`, because the mocks use the classmethod and the literal spelling alone
  would make the criterion vacuous; both are empty outside `drivers/mock/`.

### Notes and deviations
- The mock hand's guard command holds the arm joints at zero. That is the MJCF `qpos0` rest pose
  (runtime/fk.py), not a trajectory or a waypoint (R2): the hand is a separate device with its own
  guard and has no business commanding the arm, but `Guard.admit` takes a whole `MotionCommand`, so
  something has to fill the joint fields. Documented in the module docstring and in docs/drivers.md.
- `MockArm.poll()` is mock-only and not part of `ArmDriver`: a real driver's stream comes from its
  transport. It exists because "10 s of state at 100 Hz" needs a drainable stream to count.
- Grid periods are integer nanoseconds, so one second of a 30 fps stream is 30 x 33 333 333 ns =
  999 999 990 ns, not 1e9. Tests assert to within one period rather than pretending otherwise.
- **Pre-existing flake, not mine:** `tests/test_safety.py::test_guard_on_hardware_stops_the_moment_the_session_expires`
  failed once in ~6 full-suite runs and passes in isolation and on rerun. Mechanism: it writes
  `enabled_at = now-59 s` with a 60 s window after truncating to whole seconds, so the session has
  under ~1 s of validity left and expires between the write and the first `admit` when the run lands
  near a second boundary. `tests/test_safety.py` is not mine to edit under this task; a 600 s window
  (or `enabled_at = now - 1`) would close it.
- No disagreement with the task as written. No blockers. Nothing under `third_party/` touched,
  nothing imported from `tools/hardware_checks/` (asserted by a test), `hardware/session.enable`
  neither created nor read for a write path.
(T-006 commit: 096d01f; this line and the TASKS.md result hash are the only content of the follow-up commit.)
## T-008  Board calibration from AprilTags and a Brio still  (opus, 2026-09-11T20:05+07:00)

Built the one mapping everything downstream needs: board millimetres (the `config/board.yaml` frame)
to pixels in the full Brio frame, fitted from the four corner AprilTags of a single top-down still.

### What changed
- `board/calibration.py`. `load_tag_geometry()` reads the `apriltags` block (family, size, the id at
  each corner, the centres, or centres derived from `tag_inset_mm` while `centres_mm` is UNMEASURED);
  `detect_tags()` runs `cv2.aruco.ArucoDetector` over
  `getPredefinedDictionary(DICT_APRILTAG_36h11)` with `CORNER_REFINE_SUBPIX`; `calibrate_image()` /
  `calibrate_file()` pair the 4 corners of each of the 4 tags with their board-frame positions
  (16 points) and fit with `cv2.findHomography(..., cv2.RANSAC)`, requiring all 16 as inliers.
  `Calibration` exposes `board_to_px`, `px_to_board`, `cell_px(cell_id)`, `cell_px_all()` (shaped for
  `engine.cells.load_cells(top_px=...)`), `board_bbox_px()` and `save()`/`load()`.
  **Detector recorded: OpenCV `cv2.aruco`, not `pupil-apriltags`.** The pinned `opencv-python` 5.0.0
  already carries `DICT_APRILTAG_36h11` (`bytesList.shape == (587, 5, 4)`), so no dependency was added.
- `tools/hardware_checks/brio_still.py`. Read-only: opens the `top` V4L2 node from
  `config/cameras.yaml` (or `--device`), asks 3840x2160 MJPG (fourcc before resolution, or the driver
  caps at ~1080p), discards 10 frames for exposure settling, turns autofocus off, writes lossless PNG,
  prints the achieved resolution/fps/focus/exposure. Exit 3 = no usable camera, 2 = usage, 0 = written.
  It opens a camera and nothing else; no motion command is reachable from it and no session is needed.
- `tests/test_calibration.py`, 21 tests.
- `docs/board.md`.
- `config/board.yaml`, `apriltags` block only. **This is outside the file list I was given** and is
  logged as a deviation: the task's design guidance explicitly told me to add a `tag_inset_mm`
  UNMEASURED placeholder there, and the block as it stood (every leaf the literal `UNMEASURED`) gave
  the detector no family, no tag size and no ids to run with. Everything I put in is Form-2
  (docs/config.md): a usable number next to `_status: UNMEASURED` -- `family: tag36h11`,
  `size_mm: 40.0`, ids `0..3` anticlockwise from the (-x,-y) corner, `tag_inset_mm: 10.0` (tag centres
  at (+-270, +-270) mm). `centres_mm` stays the literal `UNMEASURED`, and a mapping put there later
  overrides the `tag_inset_mm` derivation. `unmeasured("board")` still reports all five, and both the
  CLI (a `WARNING` line) and the written yaml (`unmeasured_board_keys`) name them, because a wrong
  `size_mm` fits a homography with a perfectly good reprojection error and a wrongly scaled board.
  No other config file touched; `config/safety.yaml` untouched.

### Commands run and measured results
```
.venv/bin/ruff check .                -> All checks passed!
.venv/bin/python -m pytest -q         -> 246 passed, 1 skipped in 23.71s   (225 passed before T-008)
.venv/bin/python -m pytest -q tests/test_calibration.py  -> 21 passed in 2.85s
```
Acceptance 1 and 2, `test_synthetic_board_recovers_every_cell_centre[translation|rot15_tilt]`. Four
tags rendered with `generateImageMarker` at their configured board positions at 2 px/mm (1400x1400
board image, 80 px tags), warped by a known homography, detected, and compared at the centre of all
88 cells of `config/board.yaml` -- not only at the 16 corners the fit saw:

| case | reprojection rms | max corner | worst cell centre | mean cell |
|---|---|---|---|---|
| translation only | 0.1202 px | 0.1287 px | **0.0079 px** | 0.0046 px |
| 15 deg rotation + projective tilt (~7% across, image 1737x1716) | 0.2312 px | 0.3206 px | **0.0541 px** | 0.0313 px |

Bounds were rms < 0.5 px and cell error < 1.0 px: both PASS on both cases, by 2-4x on rms and ~20x on
the cells. Measured with the snippet in the task report; the test asserts the same numbers.

Acceptance 3, **not met, and it cannot be met this cycle**: no Brio is attached.
`.venv/bin/python tools/hardware_checks/list_devices.py` lists four SunplusIT integrated-webcam nodes
(`174f:11b4`) and the two Orbbec Ego nodes (`2bc5:1201`), and no `046d` (Logitech) device at all.
`data/calib/board_empty.png` does not exist. **H-001 stays OPEN, unedited** -- its post-check command
(`.venv/bin/python -m board.calibration --image data/calib/board_empty.png`) is exactly the CLI that
was delivered, so nothing in it changed. What ran instead, on a synthetic still:
```
.venv/bin/python -m board.calibration --image <scratch>/synthetic_board.png --out <scratch>/board_calib.yaml
  tag 0  corner_neg_x_neg_y at (  241.12,  1299.32) px      ... four tag ids printed
  reprojection  rms 0.231 px, max 0.321 px  (16 tag corners)
  board bbox    x=152 y=128 w=1484 h=1472  (for cameras.yaml top.crop)
  WARNING       config/board.yaml still has placeholder tag geometry: apriltags.family, ...
  exit 0, wrote board_calib.yaml
.venv/bin/python tools/hardware_checks/brio_still.py --out <scratch>/x.png        -> exit 3
.venv/bin/python tools/hardware_checks/brio_still.py --device /dev/video99 ...    -> exit 3
```
**No `config/board_calib.yaml` is committed.** The only ones produced came from synthetic data and
were written to the scratchpad; `ls config/` shows the same six files as before.

### One measurement worth Fable's attention
The first synthetic run passed (cell error 0.715 px < 1.0 px) but every cell was off by *the same*
0.707 px = sqrt(0.5), which is a half pixel on each axis, not noise. Cause: my ground truth, not the
code. Numpy puts the first row of the blitted marker at row index `y`; OpenCV's continuous image
coordinates put the *centre* of that pixel at `y`, so the marker edge the detector localises is at
`y - 0.5`. The fit absorbed it as a translation, which is why the rms stayed at 0.12 px while every
cell was biased. Ground truth corrected (`_render_board` returns the placement map shifted by half a
pixel, and says why); errors dropped to 0.008 / 0.054 px. The test now also guards at 0.25 px, since
a 0.707 px systematic bias would otherwise slide under the 1.0 px acceptance bound unnoticed.

### Design calls
- **Four tags or nothing.** Three tags define a homography; `calibrate_image` refuses anyway and names
  the missing corner(s). A board calibrated from three corners is a board whose fourth corner nobody
  checked, and checking is the whole point of the measurement. Same for RANSAC: all 16 corners must be
  inliers, otherwise the tags, the board geometry, or a tag's mounting orientation disagree and that is
  an error, not a quiet best fit.
- **Tags are assumed mounted upright in the board frame** (tag "up" along board +y, "right" along +x),
  which is what makes `cv2.aruco` corner *k* correspond to a known board point. A quarter-turn-off tag
  is still detected but pairs with the wrong points, and the rms jumps by roughly the tag size -- the
  all-inliers rule turns that into a refusal rather than a silent 40 mm error. Documented in the module
  header and `docs/board.md`. A per-tag rotation field in the config is the fix if a real board needs it.
- `config/board_calib.yaml` is a **generated artefact**, deliberately not added to
  `runtime.config.REQUIRED_KEYS`: it is not hand-maintained and re-running the CLI is the way to change
  it. It is read back with `board.calibration.load()`. It records `board_config_hash`, so a calibration
  computed against different board geometry is detectable.
- `top_crop` (the board's pixel bounding box) is written into the calibration but **not** into
  `config/cameras.yaml`. `docs/config.md` says `top.crop` is filled in by this module; I print and store
  the number for a human to paste, rather than writing a config file this task did not list.

### Notes
- No scripted motion, no motion command, no import of `tools/hardware_checks/` from `policy/` or
  `runtime/` (`board/calibration.py` imports only `cv2`, `numpy`, `yaml`, `engine.cells`,
  `runtime.config`; the import in the *test* is the only one, and tests are neither). R1-R6 intact.
- `tools/hardware_checks/brio_still.py` carries the same `sys.path` bootstrap as `enable_session.py`,
  so H-001's `python tools/hardware_checks/brio_still.py ...` works from any directory; verified from `/`.
- Same worktree environment step as T-007/T-009: `_internal/` and `pxcap_pro_local` under
  `third_party/pxcap_pro_teleop_sdk/pxcap_pro_local/` recreated as symlinks into the main tree so
  `tests/test_docs_sdks.py` can see the git-ignored payload. Nothing under `third_party/` staged or
  modified. A `tools/` helper for this is still worth a small task (third time it has been done by hand).
- The `tests/test_greennode_local.py` flake reported under T-007 did not reproduce in the runs here.
- No blockers. The only unmet criterion is acceptance 3, which needs H-001 and a Brio.
(T-008 commit: 63d998f; this line and the TASKS.md result hash are the only content of the follow-up commit.)

---

## T-013  Controller-pose to 8-DoF arm IK prototype  (2026-09-11T23:55+07:00)

### What changed
- `teleop/retarget.py` (new, 200 code lines / 318 with docstrings): `ArmIK` (mink over the vendored
  `third_party/unitree_g1_mjcf/g1_29dof.xml`), `pico_to_g1_base`, `pinch_from_glove`, `IkResult`.
  `ArmIK.solve(target_pos_m, target_quat_xyzw, q_current) -> q8` in `config/robot.yaml` `action_order`;
  `solve_detailed(..., record_steps=)` adds the iteration count, the residual errors, the converged
  flag and every intermediate configuration. `fk_pose(q8)` gives the target point's pose, whose
  position is bit-identical to `runtime.fk.left_arm_fk(q8)` (same model instance geometry, same base,
  same frozen joints) plus the orientation the box does not need.
- `tests/test_retarget.py` (new, 30 tests).
- `config/robot.yaml`: new `teleop` block -- `pico_to_pelvis` (4x4, identity, UNMEASURED),
  `rest_pose_rad` (8 zeros, UNMEASURED), `teleop.ik` (solver settings, design choices, no `_status`).
- `docs/teleop.md` (new); `docs/config.md`: the future-tense T-012 sentence fixed, and the new
  `teleop` keys documented under `config/robot.yaml`.
- Nothing else. No driver, no motion command, no scripted trajectory, `config/safety.yaml` untouched.

### Commands and measured results
```
.venv/bin/ruff check .                      -> All checks passed!
.venv/bin/python -m pytest -q               -> 327 passed, 1 skipped in 28.5 s   (297 before + 30 new;
                                               the skip is tests/test_scaffold.py's motion autoskip)
.venv/bin/python -m pytest tests/test_retarget.py -q   -> 30 passed
```
The last command prints both acceptance numbers (`capsys.disabled()`, so they appear on a plain `-q` run):

```
ArmIK cold solve, 50 reachable targets from the rest pose: pass rate 100% (50/50) within 5 mm and
3 deg; position error median 0.301 mm / p90 0.910 mm, orientation error median 0.092 deg / p90 0.223 deg
ArmIK solve time on this laptop: warm (tracking, 250 calls) mean 0.632 ms, p99 9.204 ms; cold from the
rest pose (50 calls) mean 3.896 ms, max 9.125 ms
```

- **acceptance 1, pass rate: 100% (50/50), required >= 90%.** Targets are the forward kinematics of
  random joint draws inside the `config/safety.yaml` limits (waist clamp applied) whose wrist lands
  inside the workspace box with `margin_m` removed, so every one is reachable by construction; seed
  fixed at 0, so the number is reproducible. Max iterations used: <= 30 (asserted).
- **acceptance 2, mean solve time: 0.632 ms warm, required < 5 ms.** "Warm" is the teleop case as the
  task's clarification defines it: the previous solution as the seed and the target moved +-3 mm,
  250 calls. Cold from the rest pose is reported separately at 3.896 ms mean (also under 5 ms, but it
  is not what a 30 Hz loop pays). Three repeat runs of the benchmark gave warm means of 0.628 /
  0.659 / 0.628 ms. The warm p99 of 9.2 ms is the handful of calls that hit the 30-iteration budget
  near a singularity; still well inside the 33 ms tick.
- Also asserted: every solution and every intermediate step inside the safety joint limits and the
  waist clamp; every step's largest joint move <= `joint_velocity_limit_rad_s * step_dt_s` = 0.15 rad
  (and > half of it, so the limit is actually binding and the test is not vacuous); no uncommanded
  joint and no floating-base dof moves by more than 1e-9 over a solve.

### Disagreement, and what I did instead (Fable's design guidance, `dt`)
The guidance said to run the solver's damped-least-squares steps at "`dt` from config (use the 30 Hz
action period)". **That makes the acceptance criterion unreachable, and I deviated.** Measured, with
`dt = 1/30 s`: each iteration may move a joint by at most `1.5 * 1/30 = 0.05 rad`, so 30 iterations
cover 1.5 rad, while the 50 sampled targets sit a median of 1.78 rad (min 0.77, max 2.51) from the
rest pose in the worst joint. The result is a travel-limited, not convergence-limited, failure:

```
dt=1/30  pass=32/50  mean 6.71 ms  mean 27.0 iters      <- Fable's dt
dt=0.05  pass=46/50  mean 7.16 ms  mean 24.0 iters
dt=0.10  pass=50/50  mean 4.37 ms  mean 15.8 iters      <- chosen
dt=0.15  pass=48/50  mean 3.42 ms  mean 12.4 iters
dt=0.30  pass=49/50  mean 2.35 ms  mean  9.1 iters
```
(sweep: scratchpad script, same sampler and seed as the test.)

So `config/robot.yaml` `teleop.ik.step_dt_s` is **0.1 s and is documented as a trust-region size, not
a control period**: with the safety velocity limit it bounds one iteration at 0.15 rad, and 30 of them
cover 4.5 rad, wider than the widest commanded joint range. The reasoning is that a solver iteration
is a Newton step, not a control tick; reading it as a control tick would mean a cold solve is a
1-second arm traverse, which is not what a `solve` call is. The command-level velocity limit is a
different guarantee and is enforced where it belongs, in `runtime/safety.py`, against the *measured*
state and *real* elapsed monotonic time -- which is strictly stronger than anything the IK could
promise, because the IK does not know when its output will be sent or where the arm actually is.

The consequence, stated plainly in the module header and in `docs/teleop.md`: a single **cold** solve
can return a pose further from `q_current` than one 30 Hz tick at 1.5 rad/s allows, and the guard will
refuse that command. In teleop it never arises -- the operator's hand moves continuously and the loop
engages from a clutch, so every solve is warm (1-3 iterations, sub-millisecond, sub-millimetre steps).
If Fable prefers the other reading, the alternative is to keep `step_dt_s` at 1/30 and either raise
`max_iters` to ~120 (mean cold solve would be ~15 ms, warm unchanged) or relax the acceptance to
"reached within 30 iterations *of travel*". I did not do either, because both change a number the
task fixed; this way only a number the task did not fix moved.

### Other design calls
- **Freezing the other 21 joints and the base.** Done as a hard constraint, not a task: every other
  hinge gets a `mink.VelocityLimit` of **0.0** and the floating base a `FreeJointVelocityLimit(0, 0)`,
  so the QP itself cannot move them. Measured drift over a solve is below 1e-9 rad (QP round-off), and
  a test asserts it. Masking the solved velocity afterwards -- the other option in the guidance --
  was rejected: the task Jacobian would then be solved over dofs that are not actually free and the
  masked step would not achieve the task, hurting convergence for no gain.
- The uncommanded joints are held at the model's `qpos0` (zero for all of them), which is exactly what
  `runtime/fk.py` does, rather than at "their `config/robot.yaml` rest values" -- `config/robot.yaml`
  lists only the 8 commanded joints, and inventing rest values for the legs and the right arm would
  let the IK and the workspace box disagree about geometry. The new `teleop.rest_pose_rad` therefore
  covers the 8 commanded joints only.
- **Safety limits reach the solver through `MjModel.jnt_range`.** `mink.ConfigurationLimit` has no
  custom-limits argument, so `ArmIK` writes the `config/safety.yaml` limits (waist clamp already
  applied) into its **own** model instance's `jnt_range`. `mj_kinematics` does not read `jnt_range`,
  so `runtime/fk.py`'s geometry is untouched; `ArmIK` loads its own `MjModel` and never mutates the
  one `runtime.fk.kinematics()` caches.
- **The IK targets `config/safety.yaml` `workspace_box_m.point`**, not a hard-coded body name, so when
  Phase 1 moves the checked point to the DexH15 fingertip site (D-010) the IK follows the box instead
  of silently aiming somewhere else. Body or site is resolved from the model, as `runtime/fk.py` does.
- **The output is clamped to the joint limits** on the way out. That is belt and braces and is said to
  be so in the docstring: `runtime/safety.py` is what decides whether a command may be sent (R3).
- `teleop.ik` went into `config/robot.yaml` rather than staying as constants in code (section 7).
  This is more than the "UNMEASURED placeholder keys" the task listed me as allowed to add, and I flag
  it for review: the alternative was module-level numeric constants, which section 7 forbids and the
  phase audit looks for. The block carries no `_status` keys because none of it is a measurement.
- `pinch_from_glove` needed no new `config/hand.yaml` keys: `glove.open_distance_mm` (90.0) and
  `glove.closed_distance_mm` (10.0) already existed and are already UNMEASURED. `config/hand.yaml` is
  therefore unchanged. The mapping is linear between them and clamped to `pinch.scalar_range`.
- `pico_to_g1_base` transforms **both** halves of the pose and converts xyzw <-> wxyz at the boundary
  (pico_bridge is xyzw, mujoco and mink are wxyz). A test with a non-identity 90-degree-yaw transform
  in a temporary config root proves the rotation is composed and not just carried through, which an
  identity-only test could never show.

### Notes
- R1-R6 intact. `teleop/retarget.py` imports `numpy`, `mujoco`, `mink`, `runtime.config`, `runtime.fk`,
  `runtime.types` and nothing else; no driver, no `tools/hardware_checks/`, no motion command, no
  literal joint target or waypoint list. `config/safety.yaml` and `runtime/config.py` untouched.
- Staged by name only: another builder is working on `drivers/cameras.py` in a separate worktree.
- Open question for Fable, non-blocking: `teleop.rest_pose_rad` is all zeros today, which D-010 puts
  inside the box but which is not a sensible teleop home pose (the arm points forward at zero). The
  real one is a Phase 1 choice on the rig; it is the posture target, so it also picks which elbow
  configuration the IK settles into, and changing it later changes solutions. Worth deciding before
  the first collection session rather than after.
- No blockers.
(T-013 commit: 6628571; this line and the TASKS.md result hash are the only content of the follow-up commit.)

## T-016  Mock end-to-end controller loop  (2026-09-12T01:55+07:00)

The 10 Hz cycle of CLAUDE.md 5.5 over the mock drivers, the stub engine and two deliberate
placeholders (`HoldPolicy`, `MockPerception`). Nothing learned exists yet; this is the orchestration
the learned policy will drop into.

### What was built
- `runtime/policy_api.py` (192 lines): `Observation` (top/oblique/palm frames, 9-D state, 2-channel
  goal, 3-way task one-hot, all shape-validated against `runtime.types.ACTION_DIM` and the `top`
  frame), `ActionChunk` ((n, 9) + its own `hz`, frozen and read-only), the `Policy` Protocol
  (`reset`/`act`/`done`) and `HoldPolicy`.
- `runtime/goal.py` (136 lines): `GoalRenderer` — separable unit-peak gaussians of
  `observation.goal_sigma_px` at `Cell.top_px`, the task one-hot from `observation.task_ids`, and the
  documented placeholder pixel map for the uncalibrated case (counted in `placeholder_uses`, first use
  logged). A cell of `None` (a ROLL) renders as zeros, not as a goal at the origin.
- `board/perception.py` (93 lines): the `Perception` Protocol, `state_delta` and `MockPerception` with
  four stated rules. It reads the engine's board state, not the table, and says so loudly.
- `runtime/controller.py` (340 lines): `Controller`, `RunSummary`, `build()`, the CLI.
- `tests/test_controller.py` (475 lines, 25 tests) and `docs/controller.md` (146 lines).
- `config/training.yaml`: new `runtime:` block — `primitive_timeout_s: 20.0`,
  `alignment_tolerance_ms: 50.0`, `heartbeat_s: 1.0`. No `_status` keys: none of it is a measurement,
  and `config.unmeasured("training")` is still `[]` (tests/test_config.py:127 still passes).

### Commands run and measured results
```
.venv/bin/ruff check .                                   -> All checks passed!
.venv/bin/python -m pytest -q                            -> 352 passed, 1 skipped in 35.40s  (was 327+1)
.venv/bin/python -m pytest tests/test_controller.py -q   -> 25 passed in 9.36s
.venv/bin/python -m runtime.controller --backend mock --seconds 60
```
The 60 s run (session 20260911T204420, real clock, this laptop):
```
elapsed            60.01 s
commands executed  3 (recover=2, roll=1)
outcomes           0 success, 3 failure
failure modes      timeout_no_progress=3
stopped by         run_deadline=1, timeout=2
policy calls       598 = 9.96 Hz
actions sent       1793 = 29.88 Hz
safety refusals    0
alignment failures 0
heartbeat          data/logs/controller_20260911T204420.heartbeat   (60 lines, one per second)
```
Loop rate **on the fake clock** (the acceptance criterion): `policy_hz = 9.98 Hz`, `action_hz = 29.9`
over a full 20.03 s MOVE (201 policy calls, 601 actions), asserted at 10.0 +/- 0.5 and 30.0 +/- 1.5.
Guard: `arm.guard.admitted == hand.guard.admitted == summary.actions_sent`, `refused == 0` — every
action went through `Guard.admit` and none was refused. `engine.report` was called once per command
with an `Outcome(success=False, failure_mode="timeout_no_progress")`, and the stub answered with a
RECOVER which the loop then executed (asserted in
`test_failure_makes_the_stub_issue_a_recover_which_is_executed`, and visible in the 60 s run above:
roll, recover, recover).

### Decisions inside the task, for review
- **How 10 Hz, 30 Hz and "execute 8 of 16" are all true at once.** They cannot be literally
  simultaneous: 8 actions at 30 Hz is 267 ms, not the 100 ms of a 10 Hz loop. The loop drives the
  action index from the clock: the policy is re-queried every 100 ms and the chunk index advances at
  30 Hz, capped at `diffusion.execute`. Nominally 3 entries of each chunk are played and the other 5
  of the 8 are the margin for a late inference; entries 8..15 are never executed. That reading is the
  only one that satisfies both the 10 Hz loop-rate criterion and the receding horizon, and it is what
  Fable's guidance described. Documented in docs/controller.md "Rates".
- **`alignment_tolerance_ms: 50`, not 10.** The cameras and the hand free-run at 30 Hz, so the nearest
  sample to a policy instant is inherently up to 33.3 ms old. Phase 2's `< 10 ms p99` is a property of
  recorded, latency-compensated episodes, not of live polling. Stated in the config comment and the doc.
- **`sample()` is called at the policy tick, not every 30 Hz tick.** `observe()` is the only consumer,
  and sampling right before it means every stream holds a sample no older than its own frame period.
  Sampling at 30 Hz was pure waste (three synthetic frames per tick on the mocks).
- **A `SafetyViolation` is counted, logged and survived**, per Fable's guidance: a loop that died on
  the first envelope clamp would abandon the arm mid-primitive with the engine waiting for a report.
  `summary.refused` makes it visible; a mock run showing anything but 0 is a finding.
- **The goal channels are rendered once per `Command`, not once per observation** (`_goal_for` caches
  on the frozen command). It is the same answer and it was 2.2 ms of the 100 ms budget.
- **`run(max_commands=...)` bounds that call, not the controller's life.** First version compared
  against the cumulative `RunSummary`, so a second `run()` on the same controller did nothing; a test
  caught it. `RunSummary` still accumulates across runs and `elapsed_s` now sums them.
- **`_bump` reads the clock *after* the step's work.** The first version bumped against the top-of-loop
  timestamp, so a stalled iteration (the ~230 ms first policy call) left the next grid point already in
  the past and fired a burst — which the 60 Hz rate limiter refused. Visible as one `safety_refused` in
  an early smoke run; zero after the fix. This is why `refused` is a counter and not just a log line.
- **`build()` refuses `HoldPolicy` on any backend but `mock`** (R2) in addition to `drivers.make`
  refusing `backend="real"`. Two independent refusals, because one of them will be removed in Phase 1.

### Not meeting the criteria, and why
- **`grep -rn "hardware_checks" runtime/ board/ policy/` is not empty.** It returns exactly one line,
  pre-existing and not mine: `runtime/safety.py:8`, prose in the module docstring naming
  `tools/hardware_checks/enable_session.py` as the only writer of the session file. It is not an
  import, and `runtime/safety.py` is outside the files this task let me touch. The criterion's intent
  (audit checklist section 8, "`policy/` and `runtime/` import nothing from `tools/hardware_checks/`")
  holds and is now asserted by `test_runtime_and_board_import_nothing_from_tools_hardware_checks`,
  which checks every import line under `runtime/`, `board/` and `policy/` and additionally checks that
  the four files added here contain the string nowhere at all:
  `grep -rnE "^\s*(from|import)\s+.*hardware_checks" runtime/ board/ policy/` -> empty.
  If Fable wants the literal grep to be empty, the one-line fix is in `runtime/safety.py`'s docstring
  and I did not make it unasked.
- **`runtime/controller.py` is 340 lines, not "under 250".** 240 of them are code; the rest are 52
  lines of docstring, 45 blank and 3 comment. The file holds the loop, `RunSummary`, `build()` and the
  CLI, and the task restricted me to four files, so there was nowhere to move the summary or the CLI
  without creating a fifth. Cutting to 250 total meant deleting roughly every docstring in the file,
  which the project's style (and every other module) argues against. Flagged rather than silently
  ignored; if the guidance is firm, splitting `RunSummary` + `main()` into `runtime/run_report.py`
  (~90 lines) brings the loop itself to ~250.

### Notes
- R1-R6 intact. No motion command reaches real hardware: only mock drivers exist and `--backend real`
  returns exit 2 with the reason. `config/safety.yaml`, `runtime/config.py` REQUIRED_KEYS,
  `third_party/`, `agents/DECISIONS.md`, `REVIEW.md`, `STATE.md` and `requirements.txt` untouched.
  `hardware/session.enable` neither read into existence nor written.
- Staged by name only; `drivers/cameras.py` and `tools/hardware_checks/stream_stats.py` belong to the
  other builder's worktree and were not touched.
- Open question for Fable, non-blocking: `MockPerception` fails a RECOVER by construction (a recovery
  restores the board to what the engine already believes, so the delta is empty). That is honest for a
  perception that cannot see the table, but it means a mock run can never show a successful recovery,
  only that one was issued and executed. The real `board/perception.py` fixes it; until then, an eval
  harness that wants a success path will need to inject its own `Perception`.
- No blockers.
(T-016 commit: 5fbb479; this line and the TASKS.md result hash are the only content of the follow-up commit.)
## T-010  Real camera driver over V4L2, with device discovery and a stream check  (opus, 2026-09-11T23:55+07:00)

Built the real `CameraDriver`: one class for all three streams, frames stamped on arrival and
delivered at the policy resolution, plus the read-only tool that measures what a stream actually did.
Ran it against the Orbbec Ego, which is attached. **The Brio is still absent** (acceptance 3).

### What changed
- `drivers/cameras.py`. `V4L2Camera(name)` reads `config/cameras.yaml`, opens the node with
  `cv2.CAP_V4L2` (fourcc set *before* the resolution, or the driver caps the request at what raw
  YUYV can carry over USB), and `grab()` stamps with `runtime.clock.now_ns()` on the line after
  `cap.read()` returns, then crops (`<name>.crop`, when the stream has one) and resizes with
  `INTER_AREA` to `policy_resolution` -- exactly the shape `MockCamera` emits, so the two backends
  are interchangeable downstream. `probe()` reads back the *negotiated* width/height/fps/fourcc from
  the open handle without consuming a frame. Colour stays OpenCV BGR, matching `cv2.imread`, which is
  what `board/calibration.py` already consumes.
  Device selection is three steps: explicit `device=`, then `<name>.device` from the config, then
  **discovery by `<name>.usb_id`** -- the lowest-numbered `VIDEO_CAPTURE` node with that USB
  vendor:product, returned through its `/dev/v4l/by-id/` link when udev made one. Enumeration is
  sysfs plus one `VIDIOC_QUERYCAP` on an `O_RDONLY|O_NONBLOCK` handle; it never streams. Every "there
  is no camera" case -- unconfigured, absent, busy, silent, closed -- is one `CameraUnavailable`.
  `depth=True` raises `NotImplementedError` naming `pyorbbecsdk` (D-009). There is no write call on
  the class and a test asserts it (`send_targets`/`send_pinch`/`admit` all absent): a camera is a
  sensor, R1 has nothing to gate here.
- `drivers/__init__.py`: the `real` branch now builds a `V4L2Camera` for `top`/`oblique`/`palm` and
  still raises `NotImplementedError` for `arm`/`hand`/`glove`/`pose`. Nothing else changed.
- `tools/hardware_checks/stream_stats.py`. `--backend mock|real --camera top|oblique|palm --seconds N`
  (+ `--device`, `--warmup`, `--json`). Reports achieved fps `(n-1)/span` from the driver timestamps,
  drops (gaps > 1.5 nominal periods, and how many frames those gaps swallowed) and inter-frame jitter
  `|interval - period|` at p50/p99/max in ms, plus the interval percentiles and what `probe()` says.
  Exit 3 = no camera, 2 = usage, 0 = statistics produced. Read-only; no session needed.
- `tests/test_cameras.py`, 32 tests: 24 hardware-free (device resolution and discovery against a
  temporary `cameras.yaml` and a synthetic node list; statistics against synthetic timestamp trains
  where the answer is exact; two subprocess runs of the tool), 8 marked `readonly`.
- `config/cameras.yaml`: added `usb_id: UNMEASURED` to `top` and `palm` (discovery off for those
  streams until someone reads their ids) and a comment on the existing, real `oblique.usb_id`
  explaining that it is now the discovery key. No existing value changed; `config.unmeasured("cameras")`
  is unchanged, so `test_camera_devices_are_all_unresolved` still holds for all three streams.
- `docs/drivers.md`: new "Real cameras" section (discovery order, no-depth rule, CameraUnavailable,
  the readonly convention, the tool); the protocol table and the factory section updated.

### Commands and measured results
- `.venv/bin/ruff check .` -> "All checks passed!", exit 0.
- `.venv/bin/python -m pytest -q` (Ego attached) -> **323 passed, 4 skipped** in 50.69 s. The 4 skips
  are the 3 `top` readonly tests (Brio absent) and the pre-existing motion-marker skip.
- **Acceptance 1, no camera attached.** Proved by re-running the whole suite with a throwaway pytest
  plugin in the scratchpad that sets `drivers.cameras.list_video_nodes = lambda: []`, i.e. an empty
  `/dev`: `PYTHONPATH=<scratch> .venv/bin/python -m pytest -q -p nocam` -> **318 passed, 9 skipped, 0
  failed**. Every camera skip names the device or the config key, e.g.
  `no real oblique camera: oblique: no VIDEO_CAPTURE node with usb id 2bc5:1201
  (config/cameras.yaml oblique.usb_id); is the camera plugged in? ...` and
  `no real top camera: top: config/cameras.yaml top.device is UNMEASURED and top.usb_id gives nothing
  to discover with; ...`. PASS
- **Acceptance 2.** `.venv/bin/python tools/hardware_checks/stream_stats.py --backend mock --seconds 5`
  -> 150 frames in 4.97 s, **fps 30.00**, **drops 0**, jitter p50/p99 0.00/0.00 ms. Asserted in
  `test_stream_stats_on_the_mock_reports_30_hz_and_no_drops` (runs the CLI in a subprocess with
  `--json` and checks |fps - 30| <= 1 and drops == 0). PASS
- **Acceptance 3: NOT MET, no Brio.** `list_devices.py` shows only the laptop's own
  `174f:11b4` webcam (`/dev/video0..3`) and the Ego (`2bc5:1201`, `/dev/video4..7`); no Logitech id.
  Unchanged since T-002; H-001 still stands.

### Bonus: 10 s of real stats on the Orbbec Ego (`oblique`)
`.venv/bin/python tools/hardware_checks/stream_stats.py --backend real --camera oblique --seconds 10 --warmup 15`

```
device       /dev/v4l/by-id/usb-ORBBEC_EGO_ORBBEC_AZER76400HV-video-index0 'ORBBEC: Ego left' (via usb_id 2bc5:1201)
negotiated   1600x1200 @ 30 fps MJPG
policy size  640x480
frames       300 in 9.97 s (warmup 15 discarded)
fps          30.00  (expected 30)
drops        0 gaps > 1.5 periods, 0 frames missed
interval ms  p50 33.32  p99 35.72  max 36.48
jitter ms    p50 0.22  p99 2.94  max 3.44
```

An earlier identical run gave fps 30.00, 0 drops, jitter p50 0.23 / p99 1.44 / max 2.43 ms. So the
`oblique` path holds 30 Hz with no drops, and the worst single-frame jitter seen is ~3.4 ms, i.e.
about a tenth of a period -- comfortably inside the 10 ms p99 skew budget T-016 has to meet, before
any alignment. Frames are real imagery, not a black stream (mean 81.4, std 46.7 over a 640x480x3
frame). The right stream also works when asked for explicitly (`--device /dev/video6`,
'ORBBEC: Ego right', same 1600x1200@30).

### One measurement Fable should see: the Ego ignores the requested resolution
`config/cameras.yaml` `oblique.resolution` is `[640, 480]` (UNMEASURED). The device does not offer
it. Asking for 320x240, 640x480, 1280x720 or 1600x1200 in MJPG all return **1600x1200 @ 30**:

```
asked 640x480  -> got 1600x1200 @ 30   asked 1280x720  -> got 1600x1200 @ 30
asked 1600x1200-> got 1600x1200 @ 30   asked 320x240   -> got 1600x1200 @ 30
```

This costs nothing today -- 1600x1200 is 4:3, the same aspect as 640x480, so the `INTER_AREA`
downscale in the driver is a clean 2.5x with no distortion or crop -- but it means the USB link
carries 1600x1200 MJPG per frame for a 640x480 observation, and it means `oblique.resolution` is a
request the device overrules. I did **not** write the measured value into the config: `resolution` is
the requested capture size, its `_status` is UNMEASURED, and turning a placeholder into a measurement
is a Phase 1 act, not a T-010 one. Fable's call.

### Deviations and disagreements
- **I had to touch `tests/test_mock_drivers.py`, which was not on my allowed file list.**
  `test_factory_refuses_the_real_backend_for_every_device` parametrises over all of `DEVICES` and
  asserts `NotImplementedError`; wiring the camera branch of `make(..., backend="real")`, which the
  task explicitly instructs, makes that assertion false for `top`/`oblique`/`palm` and turns the suite
  red, so the pre-commit hook would refuse the commit. Minimal change: the parametrisation now runs
  over the four actuated devices and the test is renamed
  `test_factory_refuses_the_real_backend_for_every_actuated_device`, with a comment pointing at T-010
  and `tests/test_cameras.py`. No assertion was weakened; camera-factory behaviour is covered in
  `tests/test_cameras.py` (`test_the_factory_builds_a_real_camera`, readonly, and
  `test_the_factory_reports_an_absent_camera_as_unavailable`, hardware-free).
- `test_mocks_import_nothing_from_tools_hardware_checks` scans every file under `drivers/` for the
  substring `hardware_checks`. My first draft of `drivers/cameras.py` named
  `tools/hardware_checks/list_devices.py` in three *error message strings* -- prose, not an import --
  and tripped it. The strings now say "run the read-only device inventory (list_devices.py)"; the
  rule stands untouched, and the driver imports `cv2`, `numpy`, `runtime.clock`, `runtime.config`
  and nothing else (R2 intact).
- **Discovery by `usb_id` is an addition to the task's stated selector rule** ("by-id path preferred
  over index"). The config's by-id path is still preferred over anything discovered; discovery only
  runs while `device` is the UNMEASURED placeholder, and it can only match the vendor:product the
  config itself declares, so it cannot open the wrong camera. Without it the `readonly` tests could
  never run against the Ego that is sitting on the desk, since pinning a real by-id path into
  `oblique.device` would contradict `test_camera_devices_are_all_unresolved` and its "no device
  selector may look like a real one". If Fable prefers the config-only route, deleting `find_node`
  and step 3 of `resolve_device` is a ten-line change.
- The task's "Orbbec via pyorbbecsdk ... else a clear NotImplementedError naming the missing package"
  is implemented as the task's own note directs: per D-009 the Orbbec route is UVC via OpenCV, and the
  `NotImplementedError` naming `pyorbbecsdk` fires only on a depth request (`depth=True`).

### Notes
- No motion command exists anywhere in this change; `hardware/session.enable` was never created and
  no test here is marked `motion`. Opening a camera is a read (R1).
- Same worktree environment step as T-007/T-008/T-009: `_internal/` and `pxcap_pro_local` under
  `third_party/pxcap_pro_teleop_sdk/pxcap_pro_local/` recreated as symlinks into the main tree so
  `tests/test_docs_sdks.py` can see the git-ignored payload. Nothing under `third_party/` staged or
  modified. That is now the fourth time by hand; a `tools/` helper is overdue.
- The 5 s mock acceptance test adds ~5 s of wall clock to `pytest -q`; it is a real-clock rate
  measurement and cannot be shortened without weakening the criterion.
(T-010 commit: f3e553d; this line and the TASKS.md result hash are the only content of the follow-up commit.)
---

## T-014  Worktree helper for parallel builders  (opus, 2026-09-11T23:40+07:00)

Branch `wt/t014` in the worktree `/home/alois/Desktop/ludo-g1-wt-t014`. Files touched:
`tools/worktree_setup.sh`, `tools/worktree_teardown.sh`, `tools/worktree_payloads.txt`,
`docs/setup.md`, `agents/BUILD_LOG.md`, `agents/TASKS.md` (T-014 status + result only).
No hardware, no motion command, nothing under `third_party/` modified.

### What was built
1. **`tools/worktree_payloads.txt`** -- the list the previous three tasks kept re-doing by hand
   (T-007, T-008, T-009 each wrote "a `tools/` helper for this would be a reasonable small task").
   Two entries today: `third_party/pxcap_pro_teleop_sdk/pxcap_pro_local/_internal/` (1.7 GB, 327
   entries, the only git-ignored payload `tests/test_docs_sdks.py` actually resolves into) and
   `third_party/pxcap_pro_teleop_sdk/pxcap_pro_local/pxcap_pro_local` (the 9 MB binary next to it).
   Comments-and-blank-lines format, one repo-relative path per line.
2. **`tools/worktree_setup.sh BRANCH PATH`** -- `set -euo pipefail`. Locates the main working tree as
   `dirname "$(cd "$(git rev-parse --git-common-dir)" && pwd -P)"`, so it works from the main tree or
   from any worktree. Refuses (exit 1, nothing created) if `PATH` exists, if `BRANCH` exists, if the
   base branch is missing or if `uv` cannot be found; exit 2 on wrong argument count. Then
   `git worktree add -b BRANCH PATH main`, `uv venv --python 3.10` + `uv pip install -r
   requirements.txt` into `PATH/.venv`, applies every payload line (directory -> a **real** directory
   of one symlink per entry, file -> one symlink; kind decided by what the path is on disk in the main
   tree, not by the trailing slash), warns if `git status` in the new worktree is not clean, runs
   `.venv/bin/python -m pytest -q` there and exits with pytest's status after a summary line
   `worktree_setup: OK worktree=... branch=... suite=green`.
   The payload list is read from **next to the script** (falling back to the main tree), so a helper
   that has not been merged into `main` yet still sets up worktrees correctly -- which is exactly the
   situation this task was run in.
3. **`tools/worktree_teardown.sh PATH`** -- refuses if `PATH` does not exist, is the main working tree,
   is not a registered worktree of this repo, or has any uncommitted change (`git status --porcelain`,
   so tracked *and* untracked count and git-ignored `.venv/` and payload symlinks do not). Removes the
   worktree with `git worktree remove`, then deletes the branch **only** if
   `git branch --merged main --format='%(refname:short)'` lists it; otherwise it keeps the branch,
   prints the merge and delete commands, and exits 0. Detached HEAD and the base branch itself are
   handled as "keep, nothing to delete".
4. **`docs/setup.md`** -- new "Parallel builders (git worktrees)" section: the two commands, what setup
   does in four steps, the payload table and why the directory case must be a real directory (the
   `.gitignore` rule ends in a slash and would not match a symlink), the teardown rules, and the note
   that `.git/hooks` is shared across worktrees (`git rev-parse --git-path hooks` in this worktree
   prints `/home/alois/Desktop/ludo-g1/.git/hooks`), which is why the per-worktree `.venv` is not
   optional -- the shared pre-commit hook runs `.venv/bin/ruff check .` and `pytest` inside whichever
   worktree is committing.

### Acceptance, each run and measured
1. *setup ends with the full suite green inside the worktree*
   ```
   $ cd /home/alois/Desktop/ludo-g1-wt-t014
   $ bash tools/worktree_setup.sh wt/smoke /tmp/ludo-wt-smoke
   ...
   worktree_setup: payload dir  third_party/pxcap_pro_teleop_sdk/pxcap_pro_local/_internal  (327 symlinks -> main tree)
   worktree_setup: payload file third_party/pxcap_pro_teleop_sdk/pxcap_pro_local/pxcap_pro_local  (symlink -> main tree)
   worktree_setup: running the test suite in /tmp/ludo-wt-smoke
   ...
   SKIPPED [1] tests/test_scaffold.py:60: no valid hardware session: cannot read session file /tmp/ludo-wt-smoke/hardware/session.enable
   327 passed, 1 skipped in 29.15s
   worktree_setup: OK  worktree=/tmp/ludo-wt-smoke  branch=wt/smoke  suite=green
   ```
   exit 0, wall clock 31.5 s end to end (`time`), of which the venv install is ~2 s warm-cache and the
   suite 29 s. **327 passed, 1 skipped** -- identical to this worktree's own run, i.e. the payload
   links make `tests/test_docs_sdks.py` (157 `path:line` references) resolve. PASS
   The whole cycle was run twice; the numbers above are the first run and the second was 29.20 s / same
   counts.
2. *teardown leaves `git worktree list` with no smoke worktree*
   ```
   $ bash tools/worktree_teardown.sh /tmp/ludo-wt-smoke
   worktree_teardown: removing worktree /tmp/ludo-wt-smoke (branch wt/smoke)
   Deleted branch wt/smoke (was 5b50670).
   worktree_teardown: OK  removed=/tmp/ludo-wt-smoke  branch=wt/smoke deleted (merged into main)
   $ git worktree list
   /home/alois/Desktop/ludo-g1          5b50670 [main]
   /home/alois/Desktop/ludo-g1-wt-t010  c6994b8 [wt/t010]
   /home/alois/Desktop/ludo-g1-wt-t014  5b50670 [wt/t014]
   $ ls -d /tmp/ludo-wt-smoke        -> No such file or directory
   $ git branch --list               -> main, wt/t010, wt/t014   (no wt/smoke)
   ```
   PASS. The two remaining worktrees are the other live builder's (T-010) and this one; per the task's
   clarification they are not mine to remove. `git worktree remove` accepted the worktree with its
   600 MB git-ignored `.venv` and 327 symlinks in place, so no `--force` is needed.
3. *`git status` in main is clean afterwards*
   The main tree is **not** clean right now, and none of it is mine: another builder is writing
   `config/training.yaml`, `board/perception.py`, `runtime/controller.py`, `runtime/goal.py`,
   `runtime/policy_api.py` there while I ran. I measured the criterion the only way that is meaningful
   under concurrency -- snapshot, full setup+teardown cycle, snapshot, diff:
   ```
   $ git -C /home/alois/Desktop/ludo-g1 status --porcelain > main_before.txt
   $ bash tools/worktree_setup.sh wt/smoke /tmp/ludo-wt-smoke        # -> suite=green
   $ bash tools/worktree_teardown.sh /tmp/ludo-wt-smoke              # -> branch deleted
   $ git -C /home/alois/Desktop/ludo-g1 status --porcelain > main_after.txt
   $ diff main_before.txt main_after.txt   -> identical (exit 0)
   ```
   Both snapshots are exactly the other builder's five files. My scripts add and remove nothing in the
   main working tree: the worktree registration lives in `.git/worktrees/`, the venv and every symlink
   live under the new worktree's path. PASS (measured as "changed nothing in main").

### Refusal paths, all run
```
$ bash tools/worktree_setup.sh wt/smoke2 /tmp/ludo-wt-smoke      -> exit 1, "already exists"
$ bash tools/worktree_setup.sh wt/smoke /tmp/ludo-wt-smoke-other -> exit 1, "branch wt/smoke already exists";
                                                                    /tmp/ludo-wt-smoke-other was NOT created
$ bash tools/worktree_setup.sh                                   -> exit 2, usage
$ echo scratch > /tmp/ludo-wt-smoke/dirty_probe.txt
$ bash tools/worktree_teardown.sh /tmp/ludo-wt-smoke             -> exit 1, "has uncommitted changes", "?? dirty_probe.txt",
                                                                    worktree still present
$ bash tools/worktree_teardown.sh /home/alois/Desktop/ludo-g1    -> exit 1, "is the main working tree ... refusing"
```
Unmerged-branch path, proven on a throwaway worktree (`wt/unmergedprobe` at `/tmp/ludo-wt-unmergedprobe`,
one commit made with `--no-verify`, then `git branch -D` by hand afterwards; neither is left behind):
```
worktree_teardown: branch wt/unmergedprobe is NOT merged into main; keeping it.
worktree_teardown: merge it with:
    git -C /home/alois/Desktop/ludo-g1 merge --no-ff wt/unmergedprobe
  then delete it with:
    git -C /home/alois/Desktop/ludo-g1 branch -d wt/unmergedprobe
worktree_teardown: OK  removed=/tmp/ludo-wt-unmergedprobe  branch=wt/unmergedprobe (kept, unmerged)
```
exit 0, worktree gone, branch still listed by `git branch --list`. That is the criterion "deletes its
branch only if it is fully merged into main", both ways round.

### Gate
`.venv/bin/ruff check .` -> "All checks passed!", exit 0.
`.venv/bin/python -m pytest -q` -> **327 passed, 1 skipped** in 27.81 s (the skip is the standing
motion-marker autoskip, no session file). `bash -n` clean on both scripts; shellcheck is not installed
on this laptop, so the scripts were not statically linted.

### Notes / deviations
- No new tests. The two scripts are shell tooling that creates and destroys git worktrees and installs
  a 600 MB venv; a pytest wrapper around them would take ~30 s per run inside the very suite they run,
  and would have to invent a second worktree path to avoid colliding with the builder using the helper.
  The acceptance criteria are the test, and every branch of both scripts (success, both setup refusals,
  bad args, dirty refusal, main-tree refusal, merged delete, unmerged keep) was executed and is quoted
  above. Flagging it because "tests before or alongside the code" is the standing instruction: if Fable
  wants them, the cheap version is a bash script like `tests/test_greennode_local.sh` driving a
  temporary repo made with `git init` rather than this one.
- The kind of a payload (directory vs file) is decided by what exists in the main tree, not by the
  trailing slash in `tools/worktree_payloads.txt`, so a wrong slash cannot produce a broken worktree.
  A payload listed but absent from the main tree is a warning, not a failure -- the suite that follows
  is what decides whether the worktree is usable.
- `WORKTREE_BASE_BRANCH` exists only so the scripts can be exercised against something other than
  `main`; every documented invocation uses the default.
- R1-R6 intact: no motion command, no scripted motion, `config/safety.yaml` untouched, nothing under
  `third_party/` copied or modified (the worktree holds symlinks pointing into the main tree's copy),
  `hardware/session.enable` never created. `policy/` and `runtime/` import nothing new.
- The three `_internal/` payload symlink sets made by hand in T-007, T-008 and T-009 are unaffected;
  future worktrees get them from `tools/worktree_setup.sh` instead.
- No hardware needed, no blockers.

(T-014 commit: 5c79b4d; this line and the TASKS.md result hash are the only content of the follow-up commit.)

---

## T-017  Teleop recorder to LeRobot on mocks  (opus, 2026-09-12T03:40+07:00)

### What I built
- `requirements.txt`: `lerobot==0.4.4`, `torch==2.9.1+cpu`, `torchvision==0.24.1+cpu` (+ `torchcodec==0.10.0`
  and 74 other transitive pins). The two torch pins carry the `+cpu` local version and come from three
  `--find-links` lines pointing at the PyTorch CPU index, so nothing else changes index and no `nvidia-*`
  package is installed. `docs/setup.md` records the resolved versions and why each is what it is.
- `teleop/recorder.py`: `Recorder(session_id, *, arm, hand, cameras, glove, ...)` with `poll()`,
  `tick(action)`, `start_episode(command)`, `mark_success`, `mark_perturbed`, `stop_episode`, `close()`,
  `next_grid_ns(after_ns)`. Writes a LeRobot dataset under `<root>/<session_id>/`, plus
  `episodes_meta.jsonl` (the per-episode metadata of CLAUDE.md 5.6) and a regenerated `README.md`
  dataset card.
- `tests/test_recorder.py`: 13 tests, mocks + fake clock, everything under `tmp_path`.
- `config/training.yaml`: a new `recorder:` block (5 keys, all design choices, no placeholders).
- `docs/teleop.md`: a "The recorder" section; `docs/setup.md`: resolved versions and the OpenCV order.

### Commands run and measured results
```
$ uv pip install --python .venv/bin/python \
    --find-links https://download.pytorch.org/whl/cpu/torch/ \
    --find-links https://download.pytorch.org/whl/cpu/torchvision/ \
    --find-links https://download.pytorch.org/whl/cpu/torchcodec/ \
    "torch==2.9.1+cpu" "lerobot==0.4.4"
  -> 105 packages installed in 51 s; torch 2.9.1+cpu, torchvision 0.24.1+cpu, torchcodec 0.10.0, lerobot 0.4.4
$ uv pip install --python .venv/bin/python --reinstall-package opencv-python "opencv-python==5.0.0.93"
$ uv pip install --python .venv/bin/python --dry-run -r requirements.txt
  -> "Resolved 134 packages ... Would make no changes"   (the file reproduces the venv exactly)
$ .venv/bin/ruff check .                 -> All checks passed!
$ .venv/bin/python -m pytest -q          -> 391 passed, 4 skipped in 70.48 s   (was 382 collected / 378 passed)
$ .venv/bin/python -m pytest tests/test_recorder.py -q -s
```
Printed by the tests:
```
frames=1800 skew p50=6.666 ms p99=6.667 ms dropped={'top': 0, 'oblique': 0, 'palm': 0, 'state': 0,
  'hand': 0, 'glove': 0, 'action': 0} skipped_ticks=0 align_failures=0
replay: 150 actions, worst |admitted - recorded| = 0.000e+00
```
| criterion | budget | measured |
|---|---|---|
| 60 s mock episode, all streams | 7 streams | 1800 frames, 7 streams, 61/61/61/201/61/101/61 samples per 2 s |
| skew p99 | < 10 ms | **6.667 ms** (p50 6.666 ms) |
| dropped frames | 0 | **0** on every stream, 0 skipped ticks, 0 alignment failures |
| replay through a fresh MockArm | < 1e-6 | **0.0** exactly, over 150 actions |
| reload with LeRobotDataset | keys + frame count | 2 episodes, 1860 frames, all 8 feature keys, shapes as configured |

Poll-rate sweep that justifies the design (same code, 10 s episode, only the poll rate changed):

| poll rate | skew p50 | skew p99 | dropped |
|---|---|---|---|
| 30 Hz | 13.333 ms | 20.000 ms | state 700, glove 200 |
| 100 Hz | 6.667 ms | 6.667 ms | none |
| 200 Hz | 6.667 ms | 6.667 ms | none |

### Design decisions inside the task's latitude
1. **Poll fast, write at 30 Hz.** Polling only at the dataset rate misplaces a faster stream rather than
   thinning it (the surviving samples are whichever fell just before a tick), which is what the 20 ms row
   above is. `poll()` is read-only and allowed with no session (R1); `tick()` calls it too, so a caller
   that only ticks still records, with the coarser skew the card then reports.
2. **Write one dataset period behind** (`recorder.alignment_lag_periods: 1`) so every stream has samples on
   both sides of the alignment instant and `clock.align` picks the nearest, not the newest-not-after.
3. **The frame grid is the board camera's**, starting at the first `top` sample at or after the first
   command; `next_grid_ns()` hands it to the teleop loop so commands land on the same grid. An arbitrary
   grid costs up to 16.7 ms of skew on `top` alone (measured: 16.665 ms before this change).
4. **The goal heatmaps are not a per-frame feature** (Fable's "pick the cheaper, defend it"). They are
   constant over an episode and a pure function of two cells plus `goal_sigma_px`: 2x480x640 float32 is
   2.4 MB/frame = 70 MB/s, ~1.4 GB for one 20 s episode, against ~2 kB for the two pixel pairs in the
   sidecar. `policy/dataset.py` re-renders with the same `runtime.goal.GoalRenderer` the controller uses.
5. **PNG, not video** (`recorder.use_videos: false`). lerobot 0.4.4 encodes through PyAV so video would work
   without an `ffmpeg` binary (there is none on this laptop), but the frames are what the goal-heatmap audit
   (section 8) and the replay checks read back, and a lossy codec changes them. One test proves the mock
   frame counter survives the round trip bit-exactly, which a video dataset could not.
6. `action` is aligned as a stream like any other and is **not** latency-shifted: it is a command, not an
   observation. The six observation streams are shifted by `-latency.*` from `config/robot.yaml`, all of
   which are `0.0` and `UNMEASURED`; the card prints the full UNMEASURED list on every session.

### Disagreements and things not meeting the criteria
1. **"LeRobot v2" is not reachable.** The task, D-011 and CLAUDE.md 5.6 name v2; `lerobot==0.4.4` writes
   `CODEBASE_VERSION = "v3.0"` (`.venv/lib/python3.10/site-packages/lerobot/datasets/lerobot_dataset.py:83`).
   I did the task as written apart from this, because the alternatives are worse and one is impossible:
   0.5.0/0.5.1/0.6.0/0.6.1 all require Python >= 3.12 and this project is fixed at 3.10 by the DexH15 cp310
   wheel (D-002 A1), so 0.4.4 is the newest installable release; the only older release on PyPI is 0.1.0,
   which requires `mujoco-py`, `torchvision<0.18` and `opencv-python<5` and cannot be installed here at all.
   Hand-writing v2 parquet is exactly what D-011 rejected. **For Fable:** `config/training.yaml`
   `dataset.format: lerobot_v2` is now inaccurate and I did not change it (the task limits me to *adding*
   config keys). It should become `lerobot_v3` or be dropped; the dataset card and docs say `v3.0`.
2. **OpenCV: D-008 is broken again and I could not prevent it.** lerobot hard-requires
   `opencv-python-headless (>=4.9,<4.13)`; `unitree_sdk2py` requires `opencv-python`. Both own
   `site-packages/cv2/`, and a requirements file cannot drop a dependency of a package it installs (uv
   overrides would need a second file, outside this task's file list). After a plain install `cv2` was
   4.12.0 headless; `uv pip install --reinstall-package opencv-python -r requirements.txt` restores 5.0.0,
   and that line is now in `requirements.txt`, `docs/setup.md` and the create-the-venv recipe. **This is a
   decision for Fable**, not a builder's: either keep the reinstall step, or accept the headless build
   everywhere and give up `cv2.imshow` in `teleop/operator_ui.py`. The venv currently has cv2 5.0.0 and the
   full suite is green on it.
3. **`teleop/recorder.py` is 421 lines against Fable's "under 300"** (44 blank, 12 comment, the rest code and
   docstrings). D-013 guideline 2 says to move report types to a sibling module rather than cut docstrings,
   but this task's file list does not allow creating one; I moved the long rationale out of the module
   docstring into `docs/teleop.md` instead, which took it from 434 to 421. If Fable wants 300, the cut is
   `EpisodeMeta` + `_card()` + `_drops()` into `teleop/dataset_card.py` (~110 lines).
4. `lerobot` moved three transitive pins down: `av` 17.1.0 -> 15.1.0, `packaging` 26.3 -> 25.0, `rerun-sdk`
   0.37.2 -> 0.26.2. All transitive; suite green; recorded in `requirements.txt` and `docs/setup.md`.
5. The suite now prints 2079 `DeprecationWarning`s from `datasets/features/features.py:561` (a numpy scalar
   conversion inside HuggingFace `datasets`, not our code). Silencing it needs a `filterwarnings` entry in
   `pyproject.toml`, which is outside this task's file list; flagging it rather than doing it.
6. Two lerobot behaviours worth knowing, both documented in `docs/teleop.md`: `finalize()` is mandatory or
   the dataset will not load back (and then silently reaches for the Hugging Face Hub), and `add_frame`
   rejects a `timestamp` key even though it pops one, so frame timestamps are always `frame_index / fps` --
   exact here, because frames are written one per fixed grid point.

### Safety
R1-R6 intact. The recorder sends nothing: one test wraps the arm and hand drivers in a tripwire that fails if
`send_targets`/`send_pinch` is ever called through the recorder, and records 10 frames with it in place. No
scripted motion in `teleop/`; the test's operator stand-in trajectory lives in `tests/`. `config/safety.yaml`
untouched, `hardware/session.enable` never created, nothing under `third_party/` touched. Every test wrote
under `tmp_path`; `data/raw/` is still empty. No hardware needed, no blockers.

(T-017 commit: 6beb49d; this line and the TASKS.md result hash are the only content of the follow-up commit.)
## T-015  Phase 0 report  (opus, 2026-09-11T21:00+07:00)

Every number below was produced by the command printed next to it, run by me in the worktree
`/home/alois/Desktop/ludo-g1-wt-t015` (branch `wt/t015`, `git rev-parse --short HEAD` -> `3c5df60`) on
2026-09-11. Nothing is copied from an earlier log. Timestamp note: `TZ=Asia/Bangkok date -Iminutes`
prints `2026-09-11T21:00+07:00`, which is earlier than the stamps on the T-014/T-016 entries above; the
laptop clock is what it is and I am not inventing a later one.

No device was connected and no motion command was sent by any command in this report (R1).

### 1. Per-device SDK status  (source: docs/sdks.md section 0 and sections 2-8)

Coverage command: `.venv/bin/python -m pytest tests/test_docs_sdks.py -s`
-> `9 passed in 0.09s`, printing `docs/sdks.md: checked 157 path:line references (129 unique)`.

Header/keyword grep behind the last two columns:
`grep -niE 'state read|target write|no target' docs/sdks.md` -> 17 hits (lines 20, 68, 81, 153, 154,
194, 205, 272, 276, 314, 327, 371, 379, 443, 449, 463, 466).

| # | Device | Package / route | State read (docs/sdks.md) | Target write | Status |
|---|---|---|---|---|---|
| 1 | G1 left arm (7 joints, 15..21) | `unitree_sdk2py` 1.0.1 installed; `g1_bridge_sdk` C++ bridge as fallback | yes, 2.2 `rt/lowstate` `LowState_.motor_state[15..21].q` (:68) | yes, 2.3 `rt/arm_sdk` `LowCmd_.motor_cmd[15..21].q` (:81) | code-verified, DDS link needs H-002 |
| 2 | G1 waist yaw (12) | same SDK, same message | yes, section 3 `LowState_.motor_state[12].q` (:153) | yes, section 3 `LowCmd_.motor_cmd[12].q` on `rt/arm_sdk` (:154) | same; waist may be locked in robot config, check before diagnosing |
| 3 | Paxini DexH15 (15 joints) | `pxdex` 3.2.1 wheel installed in `.venv` | yes, 4.3 `DexH15Control.getJointPositionsAngle` (:194) | yes, 4.4 `DexH15Control.setJointPositionsAngle` (:205) | import + `getSDKVersion()` verified, no hand attached |
| 4 | DexH15 palm camera | `pxdex.dh15.DexH15Camera`, or plain V4L2 (recommended) | yes, 5.1 `getFrame() -> ndarray` (:272) | n/a, sensor (:276) | API verified; node + native resolution UNMEASURED (H-003) |
| 5 | PxCap Pro glove | `pxhandsdk.pxcappro` — only inside the PyInstaller bundle, no system deb | yes, 6.2 `get_encoder_angles()`, 17 channels, degrees (:314) | n/a, input device (:327) | delivery route unresolved, Q-005 |
| 6 | Pico 4 controller pose | `pico_bridge` 0.2.1 | yes, 7.1 `PicoBridge.wait_frame() -> PicoFrame`, `.controllers.left.pose` (:371) | n/a, input device (:379) | pose exists; Teleopit discards it, hence D-006 |
| 7 | Logitech Brio (`top`) | OpenCV V4L2 | yes, 8.1 `cv2.VideoCapture.read()` (:443) | n/a, sensor (:449) | never connected during Phase 0, H-001/H-003 |
| 8 | Orbbec Ego (`oblique`) | UVC V4L2 (`/dev/video4` left, `/dev/video6` right); `pyorbbecsdk` PyPI wheel broken | yes, 8.2 `cv2.VideoCapture('/dev/video4').read()` (:463) | n/a, sensor (:466) | enumerated and streamed in T-010; RGB only, D-009 |

All eight devices have a state-read reference; the three actuated paths (1, 2, 3) have a target-write
reference and the five sensors/inputs (4..8) correctly have none. This satisfies the Phase 0 "Verify"
item "docs/sdks.md exists with at least the state read and target write call for each of G1 arm, waist,
DexH15, PxCap, Pico, Brio, Orbbec, palm camera".

Installed versions, re-checked now:
`.venv/bin/python -c "import importlib.metadata as md; ..."` ->
`pico_bridge==0.2.1`, `pxdex==3.2.1`, `unitree_sdk2py==1.0.1`, `mujoco==3.13.0`, `mink==1.3.0`,
`opencv-python==5.0.0.93`, `numpy==2.2.6`; `.venv/bin/python -c "import pico_bridge, pxdex.dh15, cv2"`
-> `imports ok, cv2 5.0.0`.

### 2. Verdicts on the D-002 assumptions and unknowns

Corrections applied: D-006 (A5), D-009 (Orbbec route), D-010 (envelope facts behind U3).

| Id | Claim (D-002) | Verdict | Evidence / command |
|---|---|---|---|
| A1 | Python >= 3.10 | CONFIRMED, narrowed to exactly 3.10 | `.venv/bin/python -V` -> `Python 3.10.20`; `pyproject.toml` pins `requires-python = "==3.10.*"` because the DexH15 wheel is cp310-only (docs/sdks.md 9) |
| A2 | Unitree SDK2 over DDS on wired Ethernet | CONFIRMED IN CODE, LINK UNVERIFIED | docs/sdks.md 2.5/9; no interface holds 192.168.123.x, `ls ~/.config/...` n/a — open as H-002 |
| A3 | Paxini SDK: DexH15 position control >= 30 Hz + palm camera | PARTIALLY CONFIRMED | API present (docs/sdks.md 4.4, 5.1); `pxdex==3.2.1` imports here; the rate claim is a Phase 1 bench measurement |
| A4 | PxCap Pro: finger joint angles >= 30 Hz, no wrist pose | CONFIRMED on both halves | docs/sdks.md 6.2/6.3: 17 angles in degrees, default 50 Hz, capability table has no wrist pose; delivery route still Q-005 |
| A5 | Pico pipeline already gives G1 arm joint targets through IK | **REFUTED** -> D-006 | docs/sdks.md 7; replacement measured in T-013 and re-measured by my full-suite run below: cold IK pass rate 100% (50/50) within 5 mm / 3 deg, position error median 0.301 mm / p90 0.910 mm, orientation median 0.092 deg / p90 0.223 deg; warm solve mean 0.616 ms, p99 9.142 ms |
| A6 | Greennode Linux GPU VM over SSH with rsync | NOT VERIFIED, NEEDS A HUMAN | `ls -l ~/.config/ludo-g1/env` -> `No such file or directory`; Q-001 |
| A7 | Engine exposes an interface later, `engine/stub.py` stands in | ADOPTED, nothing to verify | `engine/interface.py` + `engine/stub.py` exist and pass their tests inside the suite below |
| U1 | Glove->DexH15 and controller->G1 wrist latency | OPEN, Phase 1 | `.venv/bin/python -c "from runtime import config; print(config.unmeasured('robot'))"` still lists all seven `latency.*` keys (section 4) |
| U2 | DexH15 partial joint commands | CONFIRMED AT API LEVEL, firmware needs hardware | docs/sdks.md 4.5 and 9: single-finger and single-motor overloads exist in `pxdex/dh15.pyi` |
| U3 | Reachable cells with hip mount + waist yaw | OPEN, Phase 1 | mechanism now exists (`runtime/fk.py` T-011, `teleop/retarget.py` T-013); D-010 records that the zero pose puts the wrist at [0.20, 0.15, 0.10] m in the pelvis frame and that wrist roll/yaw do not move the wrist point, so the box constrains 6 of 8 joints |
| U4 | Horse arrow orientation matters to the engine | STILL ASSUMED NO | `config/board.yaml` `horse.arrow_orientation_matters` is UNMEASURED (section 4); Q-006 open with the engine team |
| U5 | >= 500 GB disk for datasets | CONFIRMED SHORT | `df -h /home` -> `/dev/nvme0n1p8 76G 59G 14G 82% /home`; 14 GB available against a 500 GB target; Q-002 |
| U6 | Greennode instance type, GPU, access method | OPEN | same as A6: `ls -l ~/.config/ludo-g1/env` -> `No such file or directory`; Q-001 |

D-009 note: CLAUDE.md 3.1 lists the Orbbec Ego for "depth cues". Phase 0 found no usable Python SDK, the
Ego enumerates as a UVC stereo pair, and the decision is RGB-only for `oblique`; nothing downstream
changes because CLAUDE.md 5.3 already specifies `oblique` as RGB 640x480. Q-008 stays open for a later
depth route.

### 3. Full test suite

Command: `time .venv/bin/python -m pytest -q` (run in this worktree, mocks only, no device attached)

```
378 passed, 4 skipped in 57.19s
real  0m57,511s   user  0m32,279s   sys  0m4,859s
```

Collection: `.venv/bin/python -m pytest -q --collect-only | tail -3` -> `382 tests collected in 0.15s`
(378 executed + 4 skipped).

The 4 skips are exactly the hardware-gated ones, printed by the same run:
- 3 x `tests/test_cameras.py:63` — no real top camera (`config/cameras.yaml top.device` is UNMEASURED);
- 1 x `tests/test_scaffold.py:60` — `@pytest.mark.motion` skipped, "no valid hardware session: cannot read
  session file .../hardware/session.enable: No such file or directory" (CLAUDE.md 4.6).

Lint: `.venv/bin/python -m ruff check .` -> `All checks passed!`.

### 4. UNMEASURED keys per config file

Command:
`.venv/bin/python -c "from runtime import config; [print(n, config.config_hash(n)[:12], config.unmeasured(n)) for n in ['robot','safety','cameras','board','hand','training']]"`
(printed one key per line below; the hash is the first 12 hex of `config.config_hash`).

| File | Hash (12) | UNMEASURED count |
|---|---|---|
| robot.yaml | 7c4a5143b618 | 17 |
| safety.yaml | 6dc24062a636 | 10 |
| cameras.yaml | aec8508475eb | 17 |
| board.yaml | 9045ec1702ba | 13 |
| hand.yaml | 6f1507d5116c | 14 |
| training.yaml | 20e458a9b03f | 0 |

Total 71 placeholder keys. Every one of them is a Phase 1 measurement or a human answer; none is a
code defect.

- **robot.yaml (17):** `waist.locked`, `network.dds_interface`, `control.state_hz`, `control.kp`,
  `control.kd`, `control.weight_ramp_s`, `latency.arm_ms`, `latency.hand_ms`, `latency.glove_ms`,
  `latency.pico_ms`, `latency.camera_top_ms`, `latency.camera_oblique_ms`, `latency.camera_palm_ms`,
  `mock.arm_tau_s`, `mock.pose_hz`, `teleop.pico_to_pelvis`, `teleop.rest_pose_rad` (the last one is
  Q-010).
- **safety.yaml (10):** `workspace_box_m.min`, `workspace_box_m.max`, `workspace_box_m.margin_m`,
  `joint_limits_rad`, `waist_yaw_clamp_rad`, `joint_velocity_limit_rad_s`, `command_rate_limit_hz`,
  `command_gap_reset_s`, `watchdog_timeout_s`, `hand.pinch_rate_limit_per_s`. These are the envelope;
  R3 says only a human commit may loosen them.
- **cameras.yaml (17):** `top.device`, `top.usb_id`, `top.resolution`, `top.fps`, `top.fourcc`,
  `top.autofocus`, `top.crop`, `oblique.device`, `oblique.device_right`, `oblique.resolution`,
  `oblique.fps`, `oblique.fourcc`, `palm.device`, `palm.usb_id`, `palm.resolution`, `palm.fps`,
  `palm.fourcc`. (`oblique.*` stays UNMEASURED although the Ego streamed in T-010, because the device
  ignored the requested resolution.)
- **board.yaml (13):** `board_origin_in_base`, `horse.footprint_mm`, `horse.height_mm`,
  `horse.arrow_orientation_matters`, `die.size_mm`, `die.bowl_centre_mm`, `die.bowl_diameter_mm`,
  `apriltags.family`, `apriltags.size_mm`, `apriltags.ids`, `apriltags.centres_mm`,
  `apriltags.tag_inset_mm`, `layout`.
- **hand.yaml (14):** `device.port`, `device.command_hz`, `joint_order`, `joint_limits_rad`,
  `pinch.open_pose`, `pinch.closed_pose`, `pinch.idle_curl_pose`, `pinch.idle_fingers`,
  `pinch.grasp_threshold`, `glove.open_distance_mm`, `glove.closed_distance_mm`, `glove.input_hz`,
  `mock.open_pose`, `mock.closed_pose`.
- **training.yaml (0):** nothing unmeasured; it holds only choices, not measurements.

### 5. The four Phase 0 exit checks (CLAUDE.md section 6, "Verify")

| # | Check | Command | Result | Status |
|---|---|---|---|---|
| 1 | all tests pass on mocks | `.venv/bin/python -m pytest -q` | `378 passed, 4 skipped in 57.19s`; the only skips are the 3 real-camera and 1 motion-marked tests | **PASS** |
| 2 | `safety.py` rejects a motion command without a session file | `.venv/bin/python -m pytest tests/test_safety.py -v -k "test_guard_on_hardware_refuses_without_a_valid_session or test_simulated_is_keyword_only_and_defaults_to_false"` | `5 passed, 53 deselected in 0.19s` | **PASS** |
| 3 | Greennode dummy job round-trips a file | `bash tests/test_greennode_local.sh` | `all checks passed`, 22 `ok` lines, 0 `FAIL`; `data/checkpoints/dummy/result.txt` exists after `up -> train -> down` and names the host that ran the job | **PASS on the local transport only; the real round trip is blocked on Q-001** |
| 4 | `docs/sdks.md` covers state read + target write for all eight devices | `.venv/bin/python -m pytest tests/test_docs_sdks.py -s` | `9 passed in 0.09s`, `checked 157 path:line references (129 unique)`; per-device coverage in section 1 above | **PASS** |

Check 2 in full (the four parametrised cases are the four ways a session can be invalid):

```
tests/test_safety.py::test_guard_on_hardware_refuses_without_a_valid_session[absent] PASSED
tests/test_safety.py::test_guard_on_hardware_refuses_without_a_valid_session[expired] PASSED
tests/test_safety.py::test_guard_on_hardware_refuses_without_a_valid_session[unconfirmed] PASSED
tests/test_safety.py::test_guard_on_hardware_refuses_without_a_valid_session[unparsable] PASSED
tests/test_safety.py::test_simulated_is_keyword_only_and_defaults_to_false PASSED
======================= 5 passed, 53 deselected in 0.19s =======================
```

Each of the first four builds `Guard(SessionGate(path), envelope(), simulated=False)` and asserts that
`guard.admit(...)` raises `SafetyViolation` with `rule == "session_gate"` and that `guard.admitted == 0`
(tests/test_safety.py:532-540). The fifth asserts `simulated` is keyword-only and that both `Guard(...)`
and `Guard.from_config(...)` default it to `False`, i.e. hardware is the default and the simulator is the
explicit opt-out.

Check 3, stated plainly: **the local-transport round trip passed** — `cloud/greennode.sh up`, `train`,
`down` and `status` all work against a temporary fake remote (`/tmp/ludo-t009-*/remote`), the produced
`result.txt` names `hostname: aloisThinkpad` and `python: 3.10.20`, and `status` correctly reports the
credentials file as absent. **The real Greennode round trip has never run** and cannot until Q-001 gives
`~/.config/ludo-g1/env`; `ls -l ~/.config/ludo-g1/env` -> `No such file or directory`.

Board calibration (the fifth Phase 0 item in section 6, "Board calibration from a Brio still image of the
real board") is built and tested on synthetic images only; the real still is H-001.

Gate hygiene, re-checked now: `ls -l hardware/` -> only `README.md`;
`git check-ignore -v hardware/session.enable` -> `.gitignore:2:hardware/session.enable`;
`git log --all --oneline -- hardware/session.enable | wc -l` -> `0` (never in history).

### 6. Open human items

| Id | What | Raised | Who unblocks it | Assumption we are running on | Blocks |
|---|---|---|---|---|---|
| H-001 | Brio still of the empty board (and of the start position) for real calibration | fable 09-11 | Alois, read-only, no session | synthetic homography tests stand in; `config/board_calib.yaml` not yet written from real pixels | Phase 0 board-calibration item; Phase 2 goal display |
| H-002 | Bring the robot LAN up so the DDS link can be verified (192.168.123.x) | opus 09-11 | Alois, read-only, no session | A2 confirmed in code only | all of Phase 1 |
| H-003 | Plug in Brio, DexH15 and PxCap Pro once for enumeration (no motor enable) | opus 09-11 | Alois, read-only, no session | the device/resolution rows of `config/cameras.yaml` and `config/hand.yaml` stay UNMEASURED | 17 cameras.yaml + 2 hand.yaml keys |
| Q-001 | Greennode credentials at `~/.config/ludo-g1/env` (host, user, key, remote root, instance type, GPU) | fable 09-11 | Alois | local fake transport; real job untested | Phase 0 exit check 3, all of Phase 3 training |
| Q-002 | Dataset disk: 14 GB free vs 500 GB target | fable 09-11 | Alois | Phase 0 and mock work fit; Phase 2 real recording does not | Phase 2 collection |
| Q-003 | May we keep `unitree_sdk2py` pip-installed from upstream GitHub? | fable 09-11 | Alois | yes, pinned at `f7a5526`, recorded in docs/sdks.md; `g1_bridge_sdk` is the fallback | nothing today |
| Q-004 | Name the physical e-stop for a rig-mounted G1 with legs locked | fable 09-11 | Alois | checklist says "e-stop within reach" without naming it | first motion session in Phase 1 |
| Q-005 | Standalone `pxhandsdk` deb for the glove, or import the bundle's cp310 `.so` | fable 09-11 | Alois / Paxini | nothing imported from the bundle yet; `drivers/pxcap.py` is mock-only | real glove driver (Phase 2) |
| Q-006 | Does the engine care about the horse arrow orientation? | fable 09-11 | engine team | assumed NO (U4) | policy target definition if the answer is yes |
| Q-007 | Folder is `~/Desktop/ludo-g1`, the brief says `~/ludo-g1` | fable 09-11 | Alois | agents use the real path; one absolute `file://` path for the pxdex wheel sits in requirements.txt | nothing today |
| Q-008 | Orbbec depth route: RGB-only / build pyorbbecsdk / OpenCV stereo | opus 09-11 | Alois | (a) RGB-only, per D-009 | nothing downstream |
| Q-009 | Which cv2 wheel to keep | opus 09-11 | **DECIDED by D-008** | keep `opencv-python`; headless pin removed in T-012 | closed |
| Q-010 | Teleop rest pose and elbow configuration | fable 09-12 | Phase 1 on the rig | `teleop.rest_pose_rad` all zeros | IK posture in Phase 2 |

Blockers: `agents/BLOCKERS.md` -> `(none)`. No Phase 0 item reached the section 4.7 bar.

### 7. What Phase 0 leaves behind

- 14 tasks marked accepted:
  `awk '/^## T-/{t=$2} /^status:/{print t, $2}' agents/TASKS.md` -> T-002..T-014 and T-016 `accepted`,
  T-015 and T-017 `in_progress`, and **T-001 still reads `review`** although `agents/REVIEW.md` records
  "T-001 ACCEPTED (fable, 2026-09-11T18:52+07:00, commits 4255484, 3cd3738)". That is a bookkeeping slip
  in TASKS.md, not an open task; I did not fix it because this task may only touch the T-015 lines.
- 382 collected tests, 378 of which run with no device attached, in 57 s.
- Three of the four exit checks pass outright; the fourth (Greennode) passes on the local transport and
  waits on Q-001. The board-calibration item waits on H-001. Neither can be advanced by an agent.
- One refuted assumption (A5) with a measured replacement already built (T-013), one narrowed assumption
  (A1: exactly 3.10), one confirmed-short unknown (U5: 14 GB of disk), and three unknowns (U1, U3, U6)
  that are Phase 1 or human work by construction.

### Notes / deviations

- No code changed in this task: the only files touched are `agents/BUILD_LOG.md`, `docs/README.md` and
  the T-015 status/result lines in `agents/TASKS.md`.
- The `378 passed` figure will change the moment T-017 merges; it is the count on `wt/t015` at `3c5df60`
  and the acceptance criterion is that Fable's fresh run on this branch reproduces it.
- R1-R6 intact: no motion command, no scripted motion, `config/safety.yaml` untouched, nothing under
  `third_party/` read-modified, `hardware/session.enable` never created (still absent, still git-ignored,
  still absent from history — commands in section 5).

(T-015 commit: 381b9d7; this line and the TASKS.md result hash are the only content of the follow-up
commit, which ran the full pre-commit gate — no `--no-verify`, per D-013.)

---

## T-025  Teleop operator UI on mocks  (opus, 2026-09-11)

### What was built

`teleop/operator_ui.py` (261 lines): `OperatorUI`, a state machine over one `Recorder` (T-017) and one
`EngineClient` (5.5), plus the goal display. States `idle -> armed -> recording -> stopped -> idle`; keys
`s` start, `x` stop, `y` mark success, `n` mark failure, `p` toggle perturbed, `a` abort, `q` quit, read
from `config/training.yaml` `operator_ui.keys` (section 7: no constants in code). `handle_key` takes a
character or a `cv2.waitKey` code and returns the new state; a key the current state has no meaning for is
logged and ignored, never raised, because a mis-hit key during collection must not end a session.

`render()` returns `(h + banner, w, 3)` uint8: the live `top` frame with the source cell circled in green
and the target in magenta at the pixels `runtime/goal.py` reports (`Cell.top_px` when calibrated, its
documented placeholder map until then), over a text band **below** the image — the operator never reads the
board through text, and the image is the frame the recorder stores plus two circles. `headless=True` (the
default) opens no window; `run_window(step=...)` is the cv2 loop and is untested (it needs a display).

Every exit from a command (`y`, `n`, `a`) reports exactly one `Outcome` to the engine, because the engine
hands out exactly one command per report (5.5); what follows (the stub's `RECOVER` + re-issue) is the
engine's decision, not the UI's. Two new labels, `operator_marked_failure` and `operator_aborted`, are
collection events and deliberately not CLAUDE.md 6.5 robot failure modes (documented in docs/teleop.md).

R1/R2: the UI never builds a `MotionCommand`, never imports a driver module, and never calls one — the
teleop loop sends and hands the *admitted* action to `OperatorUI.tick`. A tripwire test asserts it.

`config/training.yaml` gains an `operator_ui:` block (keys, marker radius/thickness/dot, the two BGR
colours, banner height and colours, font scale/thickness, window name). No placeholder:
`unmeasured("training")` is still `[]` (test_config asserts it). `REQUIRED_KEYS` untouched, as instructed.

### Commands run and measured results

```
.venv/bin/python -m pytest tests/test_operator_ui.py -q -s      # 11 passed, 9.35 s
.venv/bin/python -m pytest -q                                   # 402 passed, 4 skipped, 78.11 s
.venv/bin/ruff check .                                          # All checks passed!
```

Acceptance, the 30 s headless mock session (`test_thirty_second_headless_session_records_two_episodes`,
stub seed 2, fake clock, 200 Hz poll / 30 Hz write, frames shrunk to 64x48 through a `config/` copy in
`tmp_path`; printed by the test):

```
30 s session: 30.0 s, 2 episodes, ['roll', 'move'], [298, 298] frames, success=[True, False],
perturbed=[False, True], skew p99 [6.667, 6.667] ms, aborted=0
```

- elapsed on the fake clock 30.0 s exactly (2 s armed, 10 s recording, 1 s stopped, 3 s reset, 10 s
  recording, 1 s stopped, 3 s idle); 2 episodes written, 0 aborted.
- `episodes_meta.jsonl` checked field by field against the two commands the engine actually handed out:
  episode 0 `roll`, `src`/`dst` null, success true, perturbed false; episode 1 `move`, src `R-base-0`,
  dst `track-12`, horse `R0`, success false (`n`), perturbed true (`p`); operator `alois` on both.
- 298 frames per 10 s episode (not 300: the grid starts at the first board-camera sample at or after the
  first command and writes one alignment lag behind, so two grid points fall outside the window).
- skew p50/p99 6.667 ms on both, 0 dropped samples on all 7 streams.

Goal markers (`test_render_marks_the_two_goal_cells_and_leaves_the_rest_of_the_frame_alone`, real 640x480
config, cells `R-base-0` and `track-17`, `top_px` None so `GoalRenderer.placeholder_px` is the truth):

```
markers at [(447.3, 47.9), (362.1, 175.6)]: 542 px changed, max distance from a cell 15.6 px
(radius 14 + thickness 2)
```

i.e. both markers are drawn (centre dot present at each cell pixel) and **no** pixel further than the
marker radius from either cell differs from the raw frame — the display cannot quietly draw over the board.

The other nine tests: the key walk through all four states (including keys that must be ignored and an
unbound key), `n` straight out of `recording`, abort discards the episode (nothing written, no sidecar) and
the engine then asks for a `RECOVER`, `q` aborts what is open and `run_window` refuses in headless mode, an
exhausted engine leaves the UI inert under every key, a duplicated key binding in the config is a
`ConfigError`, a camera set without `top` is a `ValueError`, the banner text, and the send tripwire.

### Notes / deviations

- `teleop/operator_ui.py` is 261 lines against the "under 250" guidance. D-013 item 2 says to move types
  to a sibling module rather than cut docstrings, and the file list for this task allows no new module, so
  I compressed what I could (one dispatch table, one `_ignored` helper, no `poll()` passthrough) and left
  the docstrings. Style note only; no criterion depends on it.
- `run_window` is the only untested code in the module: it needs a display, and this laptop's test run has
  none. It is a 10-line loop over `render`, `imshow`, `waitKey`, `handle_key` and the injected `step`.
- Disagreement, minor: the task lists "mark perturbed" but not "mark failure". An operator who ran an
  episode to the end and judged it bad has only `a` (which discards the frames) without `n`, and CLAUDE.md
  5.6 records `success` as a per-episode field, i.e. failed episodes are kept. I added `n` (mark failure,
  keeps the episode) alongside `a` (abort, discards it) rather than overloading abort.
- The UI takes its commands from `engine.next_command()` and re-arms after every episode, so during
  collection the operator always sees a live goal; `engine/stub.py` therefore drives the collection order.
- R1-R6 intact: no motion command (the mocks' guard admits with `simulated=True`, and the UI itself sends
  nothing), no scripted motion in `teleop/` or `runtime/`, `config/safety.yaml` untouched, nothing under
  `third_party/` touched, `hardware/session.enable` never created (still absent, still git-ignored).

(T-025 commit: 2678a17; this line and the TASKS.md result hash are the only content of the follow-up commit,
which ran the full pre-commit gate — no `--no-verify`, per D-013.)
(Noted in passing, not fixed because this task may only touch the T-025 lines: T-016's `result:` block in
TASKS.md still reads `commit: COMMIT_HASH` — a bookkeeping slip from that task, like the T-001 status one.)
## T-026  Dataset viewer: frame strips for Fable's audits  (opus, 2026-09-11T23:55+07:00)

Built on `wt/t026` (worktree `/home/alois/Desktop/ludo-g1-wt-t026`, created with
`tools/worktree_setup.sh`), which is the only tree this task touched; `teleop/operator_ui.py` (T-025)
was being built in the main tree at the same time.

### What I changed

- **`tools/dataset_view.py` (249 lines, new).** `python -m tools.dataset_view SESSION_ROOT
  [--episodes 0,3] [--out DIR]`. Opens a session with `LeRobotDataset` plus the recorder's
  `episodes_meta.jsonl`, prints the dataset card (`README.md`) to stdout, and writes one
  `episode_nnnnnn.png` per episode to `SESSION_ROOT/strips/` (or `--out`). Public surface:
  `load_session`, `episode_strip`, `render_session`.
  One strip is, top to bottom: a header (episode index, `task_id`, `src -> dst`, `success`,
  `perturbed`, frame count, skew p99 — all read from the sidecar, never recomputed, R5); the 8
  evenly spaced `top` frames with the goal channels alpha-blended over them (green source, magenta
  target); the same 8 instants of `oblique`; the same 8 of `palm`, scaled up to the same column
  width; a legend naming the 9 dimensions; and two panels of `cv2.polylines` curves — the 9 action
  dims and the 9 state dims over the whole episode, each panel on one shared, labelled y range.
  Tiles are separated by a 1 px rule so that eight near-identical frames of a static board stay
  countable; the rule is an inserted column, so the tile pixels themselves are bit-exact copies of
  what the dataset holds (three tests assert that equality).
  The overlay is **not** a re-derivation of the goal: `_goal_layers` builds `runtime.goal.GoalRenderer`
  at the sidecar's stored frame size and sigma and feeds it an `engine.interface.Cell` carrying the
  `src_px`/`dst_px` the recorder wrote, so the auditor sees the same gaussian the policy will be
  conditioned on. A ROLL (both pixels `None`) produces zero layers and no overlay at all, matching
  `runtime/goal.py`'s "absence of a goal, not a goal at the origin".
  Dependencies are opencv, numpy and lerobot (loading only), as the task asked; no matplotlib, no
  driver import, no new requirement. Read-only: it opens files and writes PNGs (R1 is not in reach).
- **`tests/test_dataset_view.py` (175 lines, new).** 8 tests, all on a session recorded by
  `tests.test_recorder.Rig` (mock drivers + `FakeClock`) under `tmp_path`: a 2 s MOVE and a 1 s ROLL.
- **`docs/teleop.md`.** New section "Viewing a session: `tools/dataset_view.py` (T-026)" with the CLI,
  the band-by-band table, and the measured numbers below.

### Commands run, and what they measured

```
.venv/bin/ruff check .                              -> All checks passed!
.venv/bin/python -m pytest -q                       -> 399 passed, 4 skipped, 72.05 s
.venv/bin/python -m pytest tests/test_dataset_view.py -q -s
```

| | |
|---|---|
| acceptance: one PNG per episode | 2 episodes -> `episode_000000.png`, `episode_000001.png` in `SESSION_ROOT/strips/` |
| acceptance: image size | both 1927 x 797 px (mock 64x48/48x32 frames), asserted exactly; 451 kB and 323 kB |
| acceptance: goal overlay differs from the raw frame | yes: 3072/3072 px changed, peak \|diff\| 218/765 |
| goal lands on the cell | each channel's peak within 1 px of the stored centre, value 1.00 there; src (18.9, 29.8), dst (37.8, 20.4) |
| ROLL episode | 0 goal layers; `top` row bit-identical to the raw frames |
| `oblique` / `palm` rows | bit-identical to the raw frames (never overlaid) |
| at the real configured sizes (640x480 / 320x240), 3 s episode | 1927 x 817 px, 647 kB |
| suite before / after | 391 -> 399 passed (8 new), 4 skipped |

The full-resolution strip was rendered and looked at (scratchpad, not committed: strips live under
`data/`, which is git-ignored) before the numbers above were written down.

### Notes / deviations

- The task note says "keep it dependency-free beyond opencv and numpy (lerobot only for loading)".
  The module also imports `runtime.goal`, `engine.interface` and `teleop.recorder` (for the `SIDECAR`
  filename) — first-party modules, not dependencies, and using the project's own renderer is the
  point of the overlay. Nothing under `tools/` is imported by `policy/` or `runtime/` (R2).
- Fable's guidance said "drawn with `cv2.line`". I used `cv2.polylines`, the vectorised form of the
  same primitive: one call per dimension instead of ~1800 in a Python loop, identical output. No
  matplotlib, which is what the guidance was protecting.
- `meta/info.json` does not store the repo id, so `load_session` rebuilds it as
  `ludo-g1/<session dir name>`, exactly how `teleop/recorder.py` composes it. With `root=` present
  lerobot reads the local files and never contacts the hub; a session directory renamed by hand would
  still load, because the id is only an identifier here.
- A strip is deliberately one PNG per episode rather than one per session: section 8 asks for five
  random episodes, and `--episodes` selects them without rendering the rest.
- R1-R6 intact: no motion command anywhere (the module imports no driver), no scripted motion,
  `config/safety.yaml` untouched, nothing under `third_party/` touched, `hardware/session.enable`
  never created. Committed through the full pre-commit gate, no `--no-verify` (D-013).

(T-026 commit: 5f84310, the commit that holds all the code, tests and docs of this task. This line
and the TASKS.md `result:` hash are the only content of the follow-up commit, which ran the full
pre-commit gate — no `--no-verify`, per D-013 item 1. Amending could not be used to fold the hash in:
the amend changes the hash it is trying to record.)

## T-027  policy/dataset.py: loader, goal rendering, augmentation on the mock dataset  (opus, 2026-09-11T22:15+07:00)

Built in the main tree; T-028 (`eval/`) was being built in a separate worktree at the same time and
nothing here touches `eval/`. The one shared name was agreed in advance: `split_cell_pairs(sessions,
held_out_fraction, seed) -> (train, held_out)`, two **sorted lists of `(src_id, dst_id)` string
tuples**. `policy/` imports nothing from `eval/`.

### What I changed

- **`policy/dataset.py` (360 lines, new).** `LudoDataset(sessions, *, chunk=None, augment=False,
  seed=None, split=None, config_root=None)`, a `torch.utils.data.Dataset` over one or more sessions
  written by `teleop/recorder.py`. One `LeRobotDataset` per session (`root=<session>`, repo id
  rebuilt as `ludo-g1/<dir name>` the way `tools/dataset_view.py` does it), opened with
  `delta_timestamps={"action": [i/fps for i in range(chunk)]}` so lerobot itself assembles the action
  chunk and reports its padding. A sample is: `top` `(5,h,w)` (RGB in [0,1] + the two goal channels),
  `oblique` `(3,h,w)`, `palm` `(3,h,w)`, `state` `(9,)`, `task_id` `(3,)` one-hot, `action`
  `(chunk,9)`, `action_mask` `(chunk,)`, all float32.
  Goal channels are **rendered, not stored**: `runtime.goal.GoalRenderer` at the sidecar's frame size
  and sigma, fed the `src_px`/`dst_px` the recorder wrote, once per episode and cached (bilinearly
  resized if the stored image size differs, which it does in the tests). A ROLL renders two zero
  channels. Chunking: every frame is a sample and the episode tail is padded with the last recorded
  action, `action_mask` 0 there — the alternative (dropping the last `chunk-1` frames) discards the
  end of every primitive, which is what the policy has least of.
  Augmentation (`augment=True`) draws from a `torch.Generator` seeded `seed + index`, so a sample is
  reproducible per index, per worker and across runs; `seed=None` draws one base seed and records it
  in `.seed`. Colour jitter on all three RGB images (written out rather than
  `torchvision.transforms.ColorJitter`, which draws from the global RNG), crop-and-resize on
  `augmentation.random_crop_cameras` only, gaussian blur on the goal channels only. `top` geometry is
  never touched, and that is **enforced**: `geometric_on_top: true` or `top` inside
  `random_crop_cameras` raises `ConfigError` at construction.
  `split_cell_pairs(...)` splits the pairs found in the sidecars, reproducibly from `seed`, both lists
  sorted; only episodes with both cells contribute a pair (a ROLL has none, a RECOVER may have one),
  and a positive fraction always holds out at least one pair so an eval set is never silently empty.
  `LudoDataset(split=pairs)` keeps exactly the episodes whose `(src_cell, dst_cell)` is listed;
  `[*train_pairs, (None, None)]` keeps the ROLL episodes too. `episode_metadata()` reads the sidecar.
  Nothing here can move anything: no driver import, no motion command (R1, R2).
- **`tests/test_dataset.py` (361 lines, new).** 18 tests on a 3-episode session (two MOVEs on
  different cell pairs, one ROLL) recorded by `tests.test_recorder.Rig` (mock drivers + `FakeClock`)
  under `tmp_path`, plus a second session for the cross-session chunking test. Shapes and dtypes;
  chunk from the caller and from the config; task one-hot against the sidecar; chunk alignment against
  `LeRobotDataset.hf_dataset["action"]`; tail padding and mask; no chunk crossing an episode or a
  session boundary; goal peaks on the stored cell pixels and zero channels for a ROLL; goal rendered
  once per episode; `top` RGB bit-identical under augmentation with the jitter at zero strength while
  `oblique`, `palm` and the goal channels all change; colour jitter reaching all three cameras;
  reproducibility from the seed; the two `ConfigError` guards; split reproducibility, disjointness,
  bounds and episode selection; and the benchmark.
- **`config/training.yaml`** (D-015): `dataset.format: lerobot_v2` -> `lerobot_v3`, with the reason
  and the D-015 reference in a comment; the `split_by` comment now names `split_cell_pairs`.
  `REQUIRED_KEYS` untouched, `unmeasured("training")` still `[]` (its test passes).
- **`requirements.txt`, `docs/setup.md`** (D-016): `opencv-python` 5.0.0.93 -> **4.12.0.88**, the same
  version as the `opencv-python-headless` pin lerobot forces; the `--reinstall-package` recipe is gone
  from the header comment, the OpenCV block and the setup page, and both now say why one version is
  the fix and that neither distribution may ever be uninstalled alone.
- **`pyproject.toml`**: one `filterwarnings` entry ignoring exactly the
  `datasets/features/features.py:561` `DeprecationWarning` (message, category and module all pinned).
- **`docs/policy.md` (109 lines, new).**
## T-028  Eval protocol and runner on mocks  (2026-09-11T22:20+07:00)

### What was built
- **`eval/protocol.py` (428 lines, new).** What a trial *is* and what a run records.
  `Trial(index, primitive, src, dst, horse_id, seed, perturbed, perturbation)` with `command()`,
  `pair` and `to_dict()`; `make_trials(kind, n, held_out_pairs, seed)` over the four kinds of
  CLAUDE.md section 6 — `move` (seeded shuffle of the held-out pair list, reshuffled per lap so 20
  trials over 10 pairs is each pair exactly twice and never twice in a row), `roll` (bowl, `src = dst
  = None`), `recover` (seeded random cells, one of the four 6.5 perturbations per trial, in turn),
  `sequence` (`engine/scripts/eval_20_moves.yaml`, `n` must be 20). `SuccessCriterion` +
  `CRITERIA` + `judge()`: the success criteria as **required Outcome fields** — MOVE needs the horse
  seen on `dst` in `observed_state_delta`, ROLL a new `die`, RECOVER only a clean Outcome (D-013).
  Also the result schema (`blank_row`, `record_execution`, `summarise`, `write_result`,
  `print_result`), `AttributedEngine` (below), `script_pairs()` and `RESULTS_DIR`.
- **`board/perception.py` (+56 lines).** The 6.5 vocabulary as an `Enum`, `FailureMode`, with the
  seven strings of section 6.5 and `parse()`. It is defined **here**, in the module that produces the
  strings, and imported by `eval/protocol.py` (the task allowed either direction): `board/` is on the
  deployed runtime path and `eval/` is analysis tooling, so the dependency points eval -> board.
  Behaviour is byte-identical — `NO_PROGRESS` is now `FailureMode.TIMEOUT_NO_PROGRESS.value` and the
  two literals in `verify()` are enum values. One definition, no second spelling.
- **`eval/run_eval.py` (245 lines, new).** `run_trials()` builds one `Controller` over mock drivers,
  the stub engine scripted with the trial commands, and the injected policy, then plays one command
  at a time (`controller.run(max_commands=1)`); the difference in the `RunSummary` across each
  command is that command's measurement. `make_policy(spec, backend)`: `hold` -> `HoldPolicy`,
  refused on any backend but mock exactly as `runtime.controller.build` refuses it (R2); `bundle
  PATH` raises `NotImplementedError` naming T-029. `--backend real` never reaches a driver: the
  policy check refuses first, exit 2. `FakeClock` (mock default, `--realtime` to opt out).
- **`tests/test_eval.py` (305 lines, 23 tests, new).** All on mocks with `HoldPolicy`; none marked
  `motion`. Runs use a copy of `config/` with `primitive_timeout_s: 1.0` (`fast_config`), which
  shortens the fake clock, not the loop; the 20 s acceptance run is the CLI command below.
- **`docs/eval.md` (177 lines, new).** Trial kinds, the success-criteria table, the failure-mode
  table, the full JSON schema (top level and per trial), the recovery/retry rule, the clock, the
  refusals and the CLI flags.
- **`eval/results/.gitkeep`.** The directory is tracked; results are not staged by a run (Fable
  commits a result when it is a number the project stands behind).

### Design decisions inside the task
- **Engine-level recovery is on for `kind="sequence"` only** (`max_reissues=2`); `move`/`roll`/
  `recover` run with `max_reissues=0`, so each trial is exactly one attempt — which is what a
  per-primitive success rate means (Phase 4: "evaluate each primitive separately, 20 trials each").
  Phase 4's "20-move sequence ... at most 3 engine-level retries" is the `sequence` kind.
- **Retries are attributed, never counted as trials.** `AttributedEngine` matches each command the
  stub hands out against the trial command objects by identity: a RECOVER the engine invented, or a
  re-issue, is charged to the trial in progress as one `engine_retries`, and only an execution of the
  trial's *own* command decides the trial. Without this a 20-trial `sequence` eval would report 60.
- **`success` vs `reported_success`.** Each row carries `judge()`'s verdict *and* `Outcome.success`.
  They differ only when perception claims a success the criterion saw no evidence for; the success
  rate is computed from `judge()` alone (R5).
- **Held-out pairs are an argument.** `policy/dataset.py` is not imported (T-027 is in flight in the
  main tree). The CLI defaults `--kind move` to `protocol.script_pairs()`, the ten distinct pairs of
  `eval_20_moves.yaml`, and `--pairs SRC:DST ...` overrides; the test asserts against its own fixture
  `HELD_OUT` list and that no trial touches the `TRAIN_PAIRS` fixture.

### Commands run, and what they measured

```
uv pip install --python .venv/bin/python -r requirements.txt   -> - opencv-python==5.0.0.93 / + opencv-python==4.12.0.88
uv pip install --python .venv/bin/python -r requirements.txt --dry-run  -> Resolved 134 packages, "Would make no changes"
uv pip list --python .venv/bin/python | grep -i opencv         -> opencv-python 4.12.0.88 / opencv-python-headless 4.12.0.88
.venv/bin/python -c "import cv2; print(cv2.__version__)"       -> 4.12.0
.venv/bin/ruff check .                                         -> All checks passed!
.venv/bin/python -m pytest -q                                  -> 428 passed, 4 skipped, 107.91 s, no warnings
.venv/bin/python -m pytest tests/test_dataset.py -q -s         -> 18 passed, 31.5 s
.venv/bin/ruff check .                                  -> All checks passed!
.venv/bin/python -m pytest -q                           -> 433 passed, 4 skipped, 85.70 s
.venv/bin/python -m pytest tests/test_eval.py -q        -> 23 passed, 6.36 s
.venv/bin/python -m eval.run_eval --backend mock --kind move --n 20 --policy hold
```

The acceptance command, verbatim output (8.5 s wall; the `goal_placeholder_px` warning is the known
uncalibrated-board fallback of `docs/controller.md`):

```
success 0/20 (0.0%)
failure modes
  timeout_no_progress    20
written /home/alois/Desktop/ludo-g1-wt-t028/eval/results/20260911T221321_move-hold.json
```

| | |
|---|---|
| acceptance: tests pass | 18 new tests green; whole suite 428 passed, 4 skipped |
| acceptance: 1000-sample benchmark | `num_workers=0`: **81 samples/s**; `num_workers=2`: **145 samples/s** (repeat run: 80 / 144) (batch 8, augmentation on, 64x48/48x32 mock frames, 1 torch thread) |
| benchmark with the default torch thread pool | ~3x slower: the same test took 43.3 s (`--durations`) instead of 19.7 s; `cProfile` put it in `adjust_hue` -> `_rgb2hsv` -> `torch.min` on 3x48x64 tensors. A DataLoader worker sets `torch.set_num_threads(1)` itself, so the test sets it for both legs and restores it |
| sample shapes | `top` (5,48,64), `oblique` (3,48,64), `palm` (3,32,48), `state` (9,), `task_id` (3,), `action` (16,9), `action_mask` (16,), all float32 |
| goal channels | peak 1.00 within 1 px of the stored `src_px`/`dst_px`; ROLL channels max \|v\| = 0.0 |
| tail padding | last frame of a 60-frame episode: mask `[1] + [0]*15`, actions 1..15 equal to action 0 |
| `top` RGB under augmentation (jitter at 0) | bit-identical on all 9 sampled items; `oblique`, `palm` and the goal channels changed on all 9 |
| DeprecationWarnings, before -> after | **2817 -> 0** (before: 2079 from `tests/test_recorder.py`, 648 from `tests/test_operator_ui.py`, 90 from `tests/test_dataset_view.py`, all the same `datasets` message) |
| suite before -> after | 407 passed / 7 skipped / 70.0 s -> 428 passed / 4 skipped / 107.9 s. +18 is this task; the other +3 and -3 skips are `tests/test_cameras.py` oblique tests, which skipped earlier only because the Orbbec was held by another process and ran this time |
| opencv after re-resolve | `opencv-python==4.12.0.88`, `opencv-python-headless==4.12.0.88`, `cv2.__version__ 4.12.0`, `cv2.getBuildInformation()` -> `GUI: QT5` (the GUI binary is the one in place) |

### Notes / deviations

- **D-016 is right about the version and not quite right about the files.** The two wheels ship
  *different* `cv2/cv2.abi3.so` binaries at the same version — `opencv-python`'s is built with a
  highgui backend (`GUI: QT5`), the headless one is not — so pinning both to 4.12.0.88 removes the
  version ambiguity (which is what bit T-017) but not the GUI one: whichever wheel lands last still
  decides whether `cv2.imshow` works. Here `opencv-python` landed last and `getBuildInformation()`
  reports QT5, so `teleop/operator_ui.py` is fine. I applied D-016 as written and recorded the
  remaining hazard in `docs/setup.md` as a repair step (one `--reinstall-package opencv-python`),
  explicitly not part of the normal install. If Fable wants that hazard closed rather than documented,
  the options are a post-install check in CI or dropping to headless everywhere (which costs
  `cv2.imshow` in the operator UI).
- `chunk` defaults to `None` = `config/training.yaml` `diffusion.chunk` (16) rather than to a literal
  16 in the signature, so the 16 lives in yaml (section 7) and ACT can pass 32. Fable's guidance wrote
  `chunk=16`; the behaviour is identical for every caller that does not pass one.
- `split` is strict membership on `(src_cell, dst_cell)`: an episode with no cell pair (ROLL, and
  RECOVER when the engine names one cell) is kept only if its pair is listed. The alternative —
  always keeping unpaired episodes — would silently put every ROLL into an eval set built from
  held-out pairs. Documented in `docs/policy.md`, with `[*train_pairs, (None, None)]` as the recipe
  for multi-task training sets.
- The benchmark draws its 1000 samples **with replacement** from the 120-frame mock session
  (`RandomSampler(replacement=True, num_samples=1000)`): 1000 real sample fetches (3 PNG decodes, a
  goal lookup and an augmentation each), not 1000 distinct frames, because recording 1000 mock frames
  would add ~10 s of PNG writing to every suite run for no extra information.
- `docs/README.md` has a one-line index row per module page and needs one for `docs/policy.md`; the
  task's file list does not include it, so I did not touch it. Fable: one row, or tell me to add it.
- R1-R6 intact: no motion command anywhere (no driver import in `policy/`), no scripted motion,
  `config/safety.yaml` untouched, nothing under `third_party/` touched, `hardware/session.enable`
  never created or read for writing. Committed through the full pre-commit gate, no `--no-verify`
  (D-013).

(T-027 commit: 13b2806, the commit that holds all the code, tests, config, requirements and docs of
this task. This line and the TASKS.md `result:` hash are the only content of the follow-up commit,
which ran the full pre-commit gate -- no `--no-verify`, per D-013 item 1.)
| acceptance: JSON written, trial rows | `20260911T221321_move-hold.json`, `len(trials) == 20` |
| acceptance: printed rate | `success 0/20 (0.0%)`, then the one-row failure-mode table |
| summary block | `success 0, n 20, rate 0.0, by_failure_mode {"timeout_no_progress": 20}, engine_retries 0, safety_refusals 0, duration_s 400.66` |
| every failure labelled (6.5) | 20/20 under `timeout_no_progress`; 0 `unlabelled` |
| per trial (row 0) | `duration_s 20.033`, `policy_calls 200`, `actions_sent 601`, `safety_refusals 0`, `engine_retries 0`, `stopped_by "timeout"` |
| provenance recorded | `git_commit 986c15dd…`, `config_hashes` for safety/robot/board/training, `policy {"tag": "hold", "checkpoint": null, "checkpoint_sha256": null}` |
| pairs covered | the 10 distinct pairs of `eval_20_moves.yaml`, each exactly twice |
| fake clock cost | 400.7 s of loop time in 8.5 s of wall time (~47x); `--realtime` would take 400 s |
| `--kind sequence --n 20` (recovery on) | 20 trials, 0/20, `engine_retries` 2 per trial, 40 total, 1201.98 s loop time in 21.3 s wall |
| `--kind roll --n 4`, `--kind recover --n 4` | 0/4 each, all `timeout_no_progress` |
| `--backend real` | exit 2, `cannot run this evaluation: … backend='real' needs one, and HoldPolicy is a test double that must never be deployed (R2)`; no JSON written |
| `--policy bundle x.pt` | exit 2, message names T-029 and `policy/export.py` |
| `--kind sequence --n 19` | exit 2, "has 20 commands; n=19 would be a different evaluation" |
| suite before / after | 408 -> 433 passed (23 new, +2 camera tests that were skipped at worktree setup and passed here because the Orbbec node was free; skips 6 -> 4) |

### Notes / deviations
- **The 0/20 is the expected and correct result, not a finding.** `HoldPolicy` commands no motion
  (R2) and `MockPerception` reads the engine's own board state, so nothing changes and every
  primitive times out. The mock runner measures the orchestration; the first non-zero success rate
  needs T-029 and the robot. `docs/eval.md` says this in its first paragraph so no reader can quote
  a number from here as a capability.
- `eval/run_eval.py` is 245 lines, under the 250 the task named. It got there by moving the result
  schema and the report writer/printer into `eval/protocol.py` (D-013 guideline 2: move to a sibling,
  do not cut docstrings), which also puts the whole JSON format in the file docs/eval.md pins.
- Logging is configured at WARNING in `run_eval.main`: the loop emits a `run_start`/`run_end` pair
  per command and 60 of them would bury the printed result. Documented in docs/eval.md.
- `docs/README.md` (the docs index) has no row for `eval.md`; that file is outside this task's touch
  list, so the row is left for Fable to add.
- R1-R6 intact: no motion command anywhere (mock drivers only, `simulated=True` guards), no scripted
  motion and no literal joint target in `eval/`, `config/safety.yaml` untouched, nothing under
  `third_party/` touched, `hardware/session.enable` never created or read for writing. Committed
  through the full pre-commit gate, no `--no-verify` (D-013 item 1).

(T-028 commit: 6628491, which holds all of the code, tests and docs of this task. This line and the
TASKS.md `result:` hash are the only content of the follow-up commit, which ran the full pre-commit
gate — no `--no-verify`, per D-013 item 1; an amend cannot fold in the hash it is recording.)

## T-029  Diffusion Policy wrapper, smoke train, export, inference latency  (opus, 2026-09-11T23:10+07:00)

### What changed
- `policy/diffusion.py` (new): `PolicySpec` (the architecture, from `config/training.yaml`, stored in
  every bundle), `GoalDiffusionPolicy` (lerobot 0.4.4 `DiffusionPolicy` + a learned 1x1 goal
  projection + the task one-hot on the state + normalisation buffers), `DiffusionAdapter`
  (`runtime.policy_api.Policy`: chunk 16, receding horizon 8, DDIM 10), `dataset_stats`, and a
  `python -m policy.diffusion --bundle ...` latency benchmark.
- `policy/train.py` (new): argparse entry point, config + dataset manifest hashing, plain torch loop,
  `data/checkpoints/<run>/{run.json,loss.csv,checkpoint.pt}`, heartbeat, `--smoke`.
- `policy/export.py` (new): checkpoint -> bundle (`bundle.json` + `weights.pt`, TorchScript attempted
  **and verified**), `python -m policy.export --checkpoint ...`.
- `policy/__init__.py`: package docstring. `tests/test_diffusion.py` (new): 10 tests.
- `config/training.yaml`: four new `diffusion` keys — `encoder_image_hw: [240, 320]`,
  `down_dims: [512, 1024, 2048]`, `spatial_softmax_keypoints: 32`, `stats_samples: 256`. No existing
  value changed; `REQUIRED_KEYS` untouched; `unmeasured("training")` still empty.
- `eval/run_eval.py`: the reserved `--policy bundle PATH` branch now builds a `DiffusionAdapter` and
  records the bundle path, the weights sha256, the DDIM steps, the training run and its dataset
  manifest hash in the result JSON.
- `docs/policy.md`: sections for `diffusion.py`, `train.py`, `export.py` and the latency table.
- **Outside the touch list, and why**: `tests/test_eval.py::test_the_bundle_policy_is_reserved_for_t029`
  asserted `NotImplementedError(... T-029 ...)` — the very reservation this task was told to fill, so
  it had to be rewritten (it now pins the error path: a path that is not a bundle, and `bundle` with
  no path); the two matching lines in `docs/eval.md` were updated with it. Nothing else in either
  file was touched.

### Which adaptation route lerobot 0.4.4 permits (Fable's first question)
Neither of the "declare it and let lerobot cope" routes exists. Both were run before any code was
written, and both are now pinned by a test:
- 5-channel `top` beside 3-channel `oblique`/`palm`: `DiffusionConfig.validate_features`
  (`configuration_diffusion.py:239`) raises *"`observation.images.oblique` does not match
  `observation.images.top`, but we expect all image shapes to match"*.
- five channels on every camera: the stock torchvision backbone raises *"Given groups=1, weight of
  size [64, 3, 7, 7], expected input[1, 5, 240, 320] to have 3 channels, but got 5 channels instead"*.

So the wrapper route: a `Conv2d(5, 3, 1)` initialised to identity-on-RGB and zero-on-goal, and the
task one-hot concatenated onto the state (lerobot sees a 12-D `observation.state`). The same "all
image shapes must match" rule forces one encoder input size for three cameras of two resolutions:
`diffusion.encoder_image_hw` (240x320, the palm's own resolution, so nothing is upsampled). lerobot
0.4.4 also moved normalisation out of the policy into processor pipelines built around a
`LeRobotDataset` and a hub checkpoint (`processor_diffusion.py:36`), so the statistics are carried as
buffers in this model's own `state_dict` with the same formulas (`normalize_processor.py:325-359`).

### Commands and measured results

```bash
.venv/bin/ruff check .                                   # All checks passed!
.venv/bin/python -m pytest -q                            # 461 passed, 4 skipped in 180.10s
.venv/bin/python -m pytest tests/test_diffusion.py -q    # 10 passed in 55.52s
```

Test-scale numbers (48x64 encoder, `down_dims` 64/128/256, batch 2, lr 1e-3 — a real ResNet-18 /
U-Net / DDIM path small enough for the suite):

| measurement | value |
|---|---|
| smoke train, 30 steps | training loss step 1 **0.9514** -> step 30 **0.7704** (mean of the last 10: 0.7182) |
| fixed-probe loss (same batch, same seeded noise/timestep draw, untrained vs trained) | **1.1723 -> 0.9746 (-16.9%)** |
| export round trip | two adapters over the same bundle, seed 7: identical actions to 1e-5; and equal to `GoalDiffusionPolicy.predict` with the same pinned noise |
| TorchScript | traced, max |diff| vs eager **0.0** |
| `act()` at DDIM 10 | 191 ms mean over 20 calls (test-scale model; the real number is below) |

Full-scale CLI run, on the mock session recorded at the real 640x480/320x240 frame sizes
(`data/raw/mock_smoke`, 3 episodes, 75 frames, written by `teleop/recorder.py` onto the mock drivers):

```bash
.venv/bin/python -m policy.train --sessions data/raw/mock_smoke --smoke
# run 20260911T225009_diffusion_smoke: 75 frames, 293.0M parameters
# training config hash 862aafc738b931dd98e5f436c1b868eb18402f7c055e24a8a297daab65733605
# dataset manifest sha256 a4a45245e0985001b1e25a2d40df0c4b9e274aa075f8c53fe39a7e4c089ac31d
# loss step 1 1.030969 -> step 30 0.609529 (mean of the last 10: 0.706344) in 379.6 s   [12.7 s/step, CPU]

.venv/bin/python -m policy.export --checkpoint data/checkpoints/20260911T225009_diffusion_smoke
# bundle data/checkpoints/20260911T225009_diffusion_smoke/bundle
#   spec: DiffusionAdapter(bundle='bundle', ddim=10, chunk=16, device=cpu, calls=0)
#   torchscript: model.ts (traced, max diff vs eager 0.00e+00; fixed-shape artefact, not used at inference)
#   weights sha256 d4c3e32d5cdfcc6f28f629a46a6b9952a085ad5bf933799546007ed4a8222fe2

.venv/bin/python -m policy.diffusion --bundle .../bundle --trials 20                      # DDIM 10
.venv/bin/python -m policy.diffusion --bundle .../bundle --trials 20 --inference-steps 5  # DDIM 5
```

| DDIM steps | `act()` median | mean | prepare | vs the 100 ms budget (5.2: 10 Hz) |
|---|---|---|---|---|
| 10 (`diffusion.inference_steps`) | **804 ms** | 1099 ms (p95 3652 ms) | 8 ms | **8x over** (11x on the mean) |
| 5 (first rung of `compute.inference_fallback_order`) | **498 ms** | 502 ms (p95 566 ms) | 4 ms | **5x over** |

Four DDIM-10 runs gave medians of 804, 965, 2667 and 1125 ms: the median is stable around 0.8-1.0 s
and the means are inflated by multi-second outliers (14 threads on a laptop CPU under sustained
load — another builder's suite was running in a second worktree for part of it). The order of
magnitude is not in doubt.

### Findings that need a decision from Fable (not fixed here: they are scope)
1. **10 Hz inference is not reachable on this laptop.** The ladder of 5.8 does not close an 8x gap:
   DDIM 5 halves the cost and is still 5x over, and torch here is the **CPU** wheel (D-011), so there
   is no GPU leg to fall back to on this machine at all. The levers, in the order I would try them:
   a CUDA torch build or the Orin NX; a smaller `encoder_image_hw` (the U-Net, not the encoders, is
   ~99% of the time, so this is the weaker lever); a smaller `down_dims` (the 293 M parameters are
   mostly the 512/1024/2048 U-Net). Not my call — I changed nothing about the configured values.
2. **The observation history is repeated during training.** `diffusion.obs_history` is 2, but
   `LudoDataset` yields one frame per sample, so training stacks the same frame twice while
   `DiffusionAdapter` stacks a real queue of two. The model never sees motion in its conditioning
   during training and sees it at inference. `policy/dataset.py` is outside this task's touch list
   and this changes its sample contract, so it needs a task: give `LudoDataset` observation
   `delta_timestamps` the way it already has action ones. **This must land before any real training
   run (T-031).**
3. **EMA and the LR schedule are not implemented.** `diffusion.ema_decay: 0.9999` and
   `scheduler_warmup_steps` are in the config and `policy/train.py` applies neither (Fable's guidance
   said "plain torch loop"). Fine for a smoke test, wrong for a 200 k-step run.
4. **Disk.** `/home` has 12 GB free (CLAUDE.md 3.4 wants >= 500 GB for datasets). One 293 M-parameter
   checkpoint is 1.17 GB, its bundle another 1.17 GB, and a traced `model.ts` 1.17 GB more: one smoke
   run cost 3.3 GB. I deleted `checkpoint.pt`, `weights.pt` and `model.ts` from the smoke run after
   measuring and kept `run.json`, `loss.csv` and `bundle.json` as evidence; the commands above
   regenerate them. Checkpoint retention on this laptop needs a policy before Phase 3 collection.

### Notes / deviations
- **The per-step training loss is noise.** `compute_loss` draws a fresh diffusion timestep and noise
  every step, so step 29 was 0.271 and step 30 was 0.610 in the same run. The test therefore asserts
  a *fixed-probe* decrease (same batch, same seeded draw, untrained model vs trained model) as well
  as `loss_last < loss_first`, and prints all of it; `loss_mean_last_10` is in `run.json` for the same
  reason. A criterion of "step 30 below step 1" alone would pass or fail on the draw.
- **TorchScript traced, and is still not used at inference.** Tracing with the noise drawn *inside*
  the function makes `check_trace` compare two different random draws — it warns and saves a graph
  nobody has verified. So the traced wrapper takes the noise as an input, and the graph is then
  compared against the eager model directly (0.0 difference). It is kept as an artefact for the Orin
  NX / ONNX leg and `bundle.json` records `torchscript_used_at_inference: false`: a traced
  reverse-diffusion loop bakes in the batch size, the image size and the step count. The adapter
  always loads the `state_dict` + spec, so the bundle is the same thing either way.
- `GoalDiffusionPolicy.predict` calls `DiffusionModel._prepare_global_conditioning` and
  `conditional_sample` rather than `generate_actions`, because `generate_actions` slices the horizon
  from `n_obs_steps - 1` (lerobot's action alignment) while `policy/dataset.py` aligns the chunk at
  delta 0. Taking `generate_actions` would have made the first executed action one step stale and
  returned 8, not 16. Documented in both module docstrings and docs/policy.md.
- `policy/dataset.py`, `runtime/`, `config/safety.yaml` and `third_party/` untouched; the lerobot
  package is wrapped, never patched (no file under `.venv` was modified).
- R1-R6 intact: no motion command anywhere (nothing in `policy/` imports a driver — a test asserts
  it), no scripted motion, `hardware/session.enable` never created or read. Committed through the
  full pre-commit gate, no `--no-verify` (D-013 item 1).

(T-029 commit: f743667, which holds all of the code, tests, config keys and docs of this task. This
line and the TASKS.md `result:` hash are the only content of the follow-up commit, which ran the full
pre-commit gate -- no `--no-verify`, per D-013 item 1.)
## T-032  Teleop loop on mocks: pose and glove in, IK, Guard, arm and hand out  (opus, 2026-09-13T14:40+07:00)

Worktree `wt/t032` at /home/alois/Desktop/ludo-g1-wt-t032; the main tree was touched only by
`tools/worktree_setup.sh`.

### What I changed

- `teleop/loop.py` (new, 248 lines). `TeleopLoop(pose_driver, glove_driver, arm, hand, cameras,
  engine, recorder, ui, now_ns, sleep_until, ik, config_root)`. One `tick()`: read pose + glove ->
  `pico_to_g1_base` -> `ArmIK.solve` seeded from `arm.read_state()` -> `MotionCommand` ->
  `arm.send_targets` / `hand.send_pinch` (each admits through its own `Guard`, R1/R3) -> the
  *admitted* command to `ui.tick()` (hence the recorder), else `recorder.poll()` / `camera.grab()`,
  which are read-only and allowed with no session. A `SafetyViolation` is counted by rule, logged and
  the tick continues. `run(seconds)` ticks on `Recorder.next_grid_ns` when a recorder is attached and
  on one `rates.action_hz` period otherwise, dropping missed instants rather than firing catch-up
  commands. `LoopStats` counts ticks, sends, frames, refusals by rule and every IK solve time.
  `build()` wires one backend (`real` raises from `drivers.make`); `main()` is
  `python -m teleop.loop --backend mock --seconds N`.
- `tests/test_teleop_loop.py` (new, 14 tests) on mocks and a fake clock.
- `config/robot.yaml`: added `mock.pose_center_m: [0.0, 0.0, 0.0]` (a design choice about a synthetic
  stream, so no `_status` key; `REQUIRED_KEYS` untouched). `drivers/mock/pico.py` reads it and offsets
  the circle by it. Nothing else in the file changed and `tests/test_mock_drivers.py` is untouched and
  green: the default is the origin, which is where the circle already was.
- `docs/teleop.md`: new "The teleop loop" section (per-tick table, the grid, the pinch note, the
  engage finding, the measurements) and "What is not here yet" rewritten around T-020 and T-021.

### Why the mock circle needed a centre

The IK target must be reachable *and* inside the workspace box for a session that works, and neither
for the refusal case. The shipped mock pose is a 0.1 m circle about the pico frame origin, which
under the placeholder identity `teleop.pico_to_pelvis` is the pelvis itself: outside the box on x
(box min 0.17 m) and unreachable. Rather than write a second mock or a bespoke pose driver in the
test, the one mock is now parameterised: the test's config copy puts the centre at [0.28, 0.15, 0.12]
with radius 0.06 m and one turn per 120 s, and the shipped config is the out-of-box case. Measured
before choosing: identity-orientation targets solve to under 1 mm over x 0.20..0.35, y 0.05..0.25 at
z 0.10..0.15, and a full wrist turn (the mock's default 8 s cycle) is *not* reachable -- at 8 s the
solver flips configuration and asks for up to 2.8 rad in a tick. 120 s sweeps a quarter turn in 30 s.

### Commands run, and what they measured

```
.venv/bin/python -m pytest tests/test_teleop_loop.py -q     # 14 passed
.venv/bin/python -m pytest -q                               # 465 passed, 4 skipped, 172.6 s
.venv/bin/ruff check .                                      # All checks passed!
.venv/bin/python -m teleop.loop --backend mock --seconds 2  # CLI, shipped config
```

| | |
|---|---|
| 30 s session on mocks, fake clock | 901 ticks in 30.033 s = **30.000 Hz** (budget 30 +/- 0.5), 901 admitted, **0 refused** |
| arm tracking, mock lag `tau` 0.08 s | \|state - last admitted target\| = **0.00274 rad**, worst of the 8 joints (budget < 0.02) |
| `ArmIK.solve` per tick | mean **0.598 ms**, p99 **1.028 ms** over 901 ticks (one 30 Hz period is 33.3 ms) |
| engage / tracking joint steps | first admitted command 0.443 rad in one tick; afterwards max 0.231 rad/s, p99 0.157 rad/s |
| out-of-box pose (shipped config), 2 s | 61 ticks, **0 admitted**, 61 refused: `workspace_box` 37, `joint_velocity` 24; arm state still exactly the zero rest pose, `guard.admitted == 0`, 0 frames |
| recorded episode (3 s, recorder + UI built by the loop) | every `action` row is a command the guard admitted, in the order it admitted them |
| suite before / after | 451 -> 465 passed (14 new), 4 skipped both times |

### Finding: teleop has no clutch, and the first command is a lurch

The operator's hand is wherever it is when the loop starts, so the first solved pose is far from the
robot's. The guard *allows* that first step, because a fresh command is measured against the state
aged `command_gap_reset_s` (0.5 s), not against one 33 ms tick: on the tuned mock circle the first
admitted command steps 0.443 rad in one tick (13 rad/s instantaneous) and is inside the limit only by
that rule. On the shipped circle the same effect refuses 61 ticks in a row instead. Neither is what
should happen on hardware. `docs/teleop.md` and `teleop/retarget.py` both already assume a clutch
("the first target is the current wrist pose", then the operator's motion is tracked as a delta);
it is not built, and the task did not ask for it, so I did not add it. **Proposed follow-up task:
a clutch in `teleop/loop.py` (engage sets a pose offset so the first target is the measured wrist
pose; a key releases and re-engages), to be accepted before the first motion session of Phase 1.**
I have logged it here rather than in TASKS.md, which I may not edit beyond T-032.

### Notes / deviations

- **`pinch_from_glove` is not called by the loop.** The deliverable says the pinch comes from it, but
  it takes a thumb-to-index-tip *distance* in metres and `drivers.interfaces.GloveSample` carries
  encoder angles and an already-derived `pinch` scalar; `docs/teleop.md` (T-013) states that turning
  the 17 encoder angles into a tip-to-tip distance is the glove driver's job and a Phase 1 item
  (T-020). The loop therefore passes `GloveSample.pinch` through, and the real `drivers/pxcap.py`
  will be what calls `pinch_from_glove`. My alternative, had the distance existed, would have been
  one line. Flagged here because it is a literal deviation from the deliverable text.
- The constructor takes `cameras` and `engine` as the task names them and both do work: given a
  recorder, an engine and cameras but no `ui`, the loop builds the `OperatorUI` that joins them (the
  recording test relies on exactly this); `cameras` is otherwise grabbed once a tick when no recorder
  is attached, so every stream still runs at the grid rate.
- `--backend mock` on the *shipped* config refuses every command, as above; the CLI prints the
  refusals by rule and an arm that never moved. That is the guard working, not a defect, and
  docs/teleop.md says so.
- `docs/drivers.md` lists `MockPose`'s config keys and now omits `mock.pose_center_m`; that file is
  outside this task's touch list, so the row is left for Fable.
- `teleop/loop.py` is 248 lines, under the 250 the notes named, with no docstring cut to get there
  (D-013 guideline 2); the sibling-module route that guideline prefers was not available because the
  task's file list is only `teleop/loop.py`.
- R1-R6 intact: mock drivers only (their guards are `simulated=True` and every command still goes
  through `Guard.admit` -- two tests assert `stats.sent == arm.guard.admitted`), no scripted
  trajectory and no literal joint target in `teleop/` (a new test asserts `teleop/` imports nothing
  from `tools/hardware_checks/`), `config/safety.yaml` untouched, nothing under `third_party/`
  touched, the session file neither created nor named outside `runtime/safety.py`. Committed through
  the full pre-commit gate, no `--no-verify` (D-013 item 1).

(T-032 commit: db2b922, which holds all of the code, tests, config and docs of this task; this line
and the TASKS.md `result:` hash are the only content of the follow-up commit, which ran the full
pre-commit gate. Disclosure: my first attempt at the work commit passed `-c core.hooksPath=.githooks`
to `git commit`, and since no such directory exists that silently skipped the hook. I noticed
immediately and re-made the same commit with `git commit --amend --no-edit`, which ran ruff and the
full suite and printed "pre-commit: ok"; db2b922 is that commit and the bypassed one never survived.
No `--no-verify` was used anywhere, but the effect was the same for one minute, so it is recorded
here per D-013 item 1.)
