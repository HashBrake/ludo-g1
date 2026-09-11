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

`requirements.txt` is fully pinned (direct and transitive). Torch and LeRobot arrive only when a task
asks for them; MuJoCo, mink and `pico_bridge` arrived with T-012, for the arm IK path
(agents/DECISIONS.md D-006).

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

### One OpenCV distribution only

`opencv-python-headless` was removed in T-012 (agents/DECISIONS.md D-008); `opencv-python`, which
`unitree_sdk2py` depends on and which `teleop/operator_ui.py` will need, is the only distribution that
owns `cv2/`:

```bash
uv pip list | grep -i opencv        # -> opencv-python 5.0.0.93, one line
.venv/bin/python -c "import cv2; print(cv2.__file__)"
```

Both distributions install into the same `site-packages/cv2/`, so uninstalling one deletes files the
other still needs. After removing the headless build, reinstall the GUI one before trusting `cv2`:

```bash
uv pip install --reinstall-package opencv-python -r requirements.txt
```

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

## Repo layout

Per CLAUDE.md 5.1. `data/` (datasets, checkpoints, logs), `.venv/` and `hardware/session.enable` are
git-ignored. `third_party/` is vendored and never modified in place.
