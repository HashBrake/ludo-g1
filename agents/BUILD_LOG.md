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
