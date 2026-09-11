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
