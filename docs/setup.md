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

`requirements.txt` is fully pinned (direct and transitive). MuJoCo, mink and `pico_bridge` arrived
with T-012 for the arm IK path (agents/DECISIONS.md D-006); torch (CPU) and LeRobot with T-017
(D-011). One `uv pip install -r requirements.txt` is the whole install: there is no follow-up step
any more (see "Two OpenCV distributions" below, agents/DECISIONS.md D-016).

### Versions resolved for the arm IK path (T-012, 2026-09-11)

| Package | Pin | Note |
|---|---|---|
| `mujoco` | `3.13.0` | latest 3.x; loads `third_party/unitree_g1_mjcf/g1_29dof.xml` |
| `mink` | `1.3.0` | latest; requires `mujoco>=3.1.6`, so 3.13.0 satisfies it |
| `pico_bridge` | `0.2.1` | pure-Python wheel, by URL + sha256 (below) |

The three together pulled in 29 packages, all pinned in `requirements.txt` under *transitive*: mink's
solver stack (`qpsolvers`, `daqp`, `absl-py`, `etils`, `fsspec`, `importlib-resources`, `zipp`),
mujoco's viewer deps (`glfw`, `pyopengl`), and `pico_bridge`'s transport stack (`aiortc`, `aioice`,
`av`, `cryptography`, `pyopenssl`, `cffi`, `pycparser`, `pyee`, `pylibsrtp`, `dnspython`, `ifaddr`,
`attrs`, `psutil`, `google-crc32c`, `rerun-sdk`, `pyarrow`, `pillow`).

`pico_bridge` is not on PyPI. It is the wheel the vendored fork's `pico4` extra names
(`third_party/g1_pico_teleop/pyproject.toml:54`) and it is pinned by URL **and** hash:

```
https://github.com/BotRunner64/pico-bridge/releases/download/v0.2.1/pico_bridge-0.2.1-py3-none-any.whl
sha256:7cf0fee07c76541fd06e2ee6bdeec3d11fec578cd4ef6b45179dec4af31b369f
```

uv accepts `--hash=sha256:...` on a URL requirement even when the other requirements carry no hash,
and it does enforce it: installing the same URL with one hex digit changed aborts with
`Hash mismatch for pico-bridge` (checked in T-012).

### Versions resolved for the recorder (T-017, 2026-09-12)

| Package | Pin | Note |
|---|---|---|
| `lerobot` | `0.4.4` | the **last** release that installs on Python 3.10 (0.5.0+ require >= 3.12) |
| `torch` | `2.9.1+cpu` | last cp310 CPU wheel on the PyTorch index; lerobot caps torch at < 2.11 |
| `torchvision` | `0.24.1+cpu` | resolved with it, same index |
| `torchcodec` | `0.10.0` | pulled in by lerobot, from PyPI |

`lerobot` writes datasets at its own `CODEBASE_VERSION`, which is `v3.0`
(`.venv/lib/python3.10/site-packages/lerobot/datasets/lerobot_dataset.py:83`), not the "v2" of
CLAUDE.md 5.6; agents/BUILD_LOG.md (T-017) records why there is no alternative. The whole install is
CPU-only — no `nvidia-*` package is pulled in — because the two torch pins carry the `+cpu` local
version, which exists only on the PyTorch CPU index that the three `--find-links` lines at the top of
`requirements.txt` add. Those lines are `--find-links`, not `--index-url`, so everything else keeps
resolving from PyPI.

`lerobot` also moved three transitive pins down: `av` 17.1.0 -> 15.1.0, `packaging` 26.3 -> 25.0,
`rerun-sdk` 0.37.2 -> 0.26.2. All three are transitive for this project and the suite is green on
them.

### Two OpenCV distributions, pinned to one version (D-016)

D-008 removed `opencv-python-headless` in T-012 so that one distribution owned `site-packages/cv2/`.
`lerobot` 0.4.4 hard-requires `opencv-python-headless (>=4.9,<4.13)` and `unitree_sdk2py` requires
`opencv-python`, so **both** are installed again, and a requirements file cannot drop a dependency of
a package it installs. Whichever lands last owns `cv2/`. D-016's answer is to make that harmless:
both are pinned to **the same version**, `4.12.0.88`, so the `cv2` module is that version whatever
the order, and the reinstall step T-017 needed is gone. After an install:

```bash
.venv/bin/python -c "import cv2; print(cv2.__version__)"   # -> 4.12.0
```

Never uninstall either one on its own: both write into the same `cv2/` directory, so removing one
deletes files the other still needs.

One thing the shared version does **not** pin is the GUI: the two wheels ship different
`cv2/cv2.abi3.so` binaries at the same version, and only `opencv-python`'s is built with a highgui
backend (`cv2.getBuildInformation()` -> `GUI: QT5`), which is what `teleop/operator_ui.py` needs for
`cv2.imshow`. On this laptop `opencv-python` landed last and the check reports QT5. If a future
install leaves the headless binary in place, `imshow` raises *"The function is not implemented"* and
one `uv pip install --reinstall-package opencv-python -r requirements.txt` puts it back — that is a
repair, not a step of the normal install.

## Vendored G1 model

`third_party/unitree_g1_mjcf/` is a byte-identical copy of the 29-DoF G1 MJCF from
`/home/alois/Teleopit/assets/robots/unitree_g1/`, restricted to `g1_29dof.xml`, `LICENSE`, `README.md`
and exactly the 35 meshes that XML references (38 files, 19 MB; the full source `meshes/` tree is
63 MB and also carries dex3, avp and o6 variants this project never loads). The `meshes/` relative
layout is preserved so the XML's `meshdir="meshes"` still resolves.

The model is Unitree's, under the BSD-3 licence kept next to it. It is never modified: `config/`
records what it says (`limits_source`, `mjcf_line`, `mjcf_qpos_index`), and anything the IK needs done
to it -- pinning the legs, freezing the right arm -- happens at load time in code.

Verify the copy at any time:

```bash
cd third_party/unitree_g1_mjcf && sha256sum -c MANIFEST.txt   # 38 lines, all OK
```

`tests/test_assets.py` re-checks the same hashes, loads the model with MuJoCo, and asserts that the
qpos addresses recorded in `config/robot.yaml` still match the compiled model.

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

## Parallel builders (git worktrees)

Fable may run two builders at once when their tasks touch disjoint files (CLAUDE.md 4.2). The second
builder works in a *git worktree*: a second checkout of the same repository on its own branch, sharing
one `.git` directory.

```bash
bash tools/worktree_setup.sh wt/t042 /home/alois/Desktop/ludo-g1-wt-t042
# ... build, commit on wt/t042, get it merged into main ...
bash tools/worktree_teardown.sh /home/alois/Desktop/ludo-g1-wt-t042
```

`tools/worktree_setup.sh BRANCH PATH` refuses if `PATH` already exists or `BRANCH` already exists, then:

1. `git worktree add -b BRANCH PATH main` (the base branch is `main`; override with `WORKTREE_BASE_BRANCH`),
2. creates `PATH/.venv` with `uv venv --python 3.10` and installs `requirements.txt` into it
   (`.venv/` is git-ignored, so a new worktree never has one),
3. links in every path listed in `tools/worktree_payloads.txt` (below),
4. runs `.venv/bin/python -m pytest -q` inside the worktree and exits with pytest's status, printing a
   final `worktree_setup: OK ... suite=green` line.

Both scripts find the main working tree through `git rev-parse --git-common-dir`, so they can be run
from any checkout of the repo. Nothing is written into the main working tree.

### The git-ignored payloads

Some vendored files are on disk but too large for git (agents/DECISIONS.md D-003), so a fresh worktree
does not have them and `tests/test_docs_sdks.py` fails there. `tools/worktree_payloads.txt` lists them,
one repo-relative path per line:

| Entry | What the setup script creates in the worktree |
|---|---|
| a directory in the main tree | a **real** directory holding one symlink per entry of the main tree's directory |
| a file in the main tree | a single symlink |

The directory case is a real directory on purpose: the matching `.gitignore` rule ends in a slash, which
does not match a symlink, and the worktree's `git status` has to stay clean. Nothing under
`third_party/` is copied or modified; the worktree only points at the main tree's copy. Add a line to
`tools/worktree_payloads.txt` whenever a new git-ignored on-disk payload becomes a test dependency.

### Teardown

`tools/worktree_teardown.sh PATH` refuses if that worktree has uncommitted changes (tracked *or*
untracked; git-ignored files such as `.venv/` and the payload symlinks do not count), refuses if `PATH`
is the main working tree or is not a registered worktree of this repo, then removes the worktree. It
deletes the branch **only** if `git branch --merged main` lists it; otherwise it leaves the branch in
place, prints the merge and delete commands, and still exits 0.

### Notes

- Git hooks live in the shared `.git/hooks` (`git rev-parse --git-path hooks` in a worktree resolves to
  the main repo's), so the pre-commit hook installed once in the main tree also guards commits made in
  every worktree. It runs `.venv/bin/ruff check .` and `.venv/bin/python -m pytest -q` **in the
  worktree**, which is why step 2 above is not optional.
- Never work in another builder's worktree. `git worktree list` shows who is where.

## Repo layout

Per CLAUDE.md 5.1. `data/` (datasets, checkpoints, logs), `.venv/` and `hardware/session.enable` are
git-ignored. `third_party/` is vendored and never modified in place.
