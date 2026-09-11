# Setup

Development environment for LUDO-G1. Everything runs out of the repo's own virtualenv; there is no
system-wide install and the project is not `pip install`-ed (`pyproject.toml` carries tool config only).

## Requirements

- Python 3.10 (pinned: `requires-python = "==3.10.*"`). The DexH15 SDK ships a cp310 wheel only,
  so 3.10 is a hard constraint, not a floor (agents/DECISIONS.md D-002, A1).
- [`uv`](https://github.com/astral-sh/uv) at `~/.local/bin/uv`.
- `git`, `rsync`.

## Create the virtualenv

```bash
cd ~/ludo-g1                      # this checkout: /home/alois/Desktop/ludo-g1
uv venv --python 3.10
uv pip install -r requirements.txt
.venv/bin/python --version        # -> Python 3.10.x
```

`uv venv` downloads and uses a managed CPython 3.10 if the system one is not suitable; either is fine
as long as the version is 3.10.x.

`requirements.txt` is fully pinned (direct and transitive). Nothing heavier than numpy/scipy/OpenCV is
installed yet; torch, LeRobot and mujoco arrive only when a task asks for them.

## Run the checks

```bash
.venv/bin/ruff check .            # third_party/, .venv/ and data/ are excluded in pyproject.toml
.venv/bin/python -m pytest -q     # collects tests/ only (testpaths)
```

Both must be green before every commit (CLAUDE.md section 7).

### Test markers

| Marker | Meaning | Behaviour |
|---|---|---|
| `motion` | the test sends motion commands to real hardware | skipped unless `runtime.safety` reports a valid session (R1, CLAUDE.md 4.6). `runtime/safety.py` does not exist until T-005, so these are currently skipped with `no session gate yet`. |
| `readonly` | the test reads real hardware but never commands motion | skipped when the device is absent |

The autoskip lives in `tests/conftest.py`. It fails closed: any error while querying the session gate
skips motion tests rather than running them.

## Install the git hooks

```bash
bash tools/install_hooks.sh
```

This copies `tools/pre-commit.sh` to `.git/hooks/pre-commit`. The hook runs `.venv/bin/ruff check .`
and `.venv/bin/python -m pytest -q` (explicit venv paths, so it works regardless of the caller's
`PATH`) and aborts the commit if either fails. If `.venv/` is missing the hook aborts with the command
needed to create it.

Hooks are not tracked by git, so every fresh clone must run `tools/install_hooks.sh` once.

## Repo layout

Per CLAUDE.md 5.1. `data/` (datasets, checkpoints, logs), `.venv/` and `hardware/session.enable` are
git-ignored. `third_party/` is vendored and never modified in place.
