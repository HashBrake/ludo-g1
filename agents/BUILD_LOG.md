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
- `.venv/bin/python -m pytest -q` -> **275 passed, 1 skipped in 21.4 s** (225 before, +50 new; the
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
