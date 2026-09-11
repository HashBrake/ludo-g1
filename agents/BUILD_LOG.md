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
