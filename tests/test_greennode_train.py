"""T-031: `up` -> `train policy/train.py --smoke` -> `down` through cloud/greennode.sh, local transport.

What this proves, without credentials and without docker (Q-001; `docker` is not installed on this
laptop, so cloud/Dockerfile is reviewed by reading and the pins are checked by
:func:`test_training_requirements_match_requirements_txt` below):

* a real training run reaches the fake remote as *code*, not as a call: the job imports the pushed
  tree, so the checkpoint is written under the remote root and only `down` brings it back;
* the run record carries the dataset manifest hash and the six config hashes (CLAUDE.md 5.6, 5.8,
  R5), and those config hashes are the *pushed* config's, not the laptop's;
* `greennode.sh train` repeats both hashes on its own stdout after the job (they are in the job log).

The run is the tiny test scale of ``tests/test_diffusion.py``: the mock session is recorded at 64x48
frames and the pushed config is shrunk (encoder 48x64, U-Net 64/128/256, 8 keypoints) before the job
starts. Shrinking the *pushed* copy is also the check that the job ran the pushed tree: a run.json
whose training hash is the laptop config's would mean the job imported the wrong config/. The full
scale is a 2.3 GB checkpoint (D-019, Q-002: 12 GB free), which is not something a test may write.

Nothing here touches hardware and nothing sends a command (R1, R2).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest
import yaml

from engine.interface import Command, Primitive
from policy.train import dataset_manifest_hash
from runtime import config
from tests.test_recorder import Rig, config_root

REPO = Path(__file__).resolve().parents[1]
GREENNODE = REPO / "cloud" / "greennode.sh"
TRAIN_REQUIREMENTS = REPO / "cloud" / "requirements-train.txt"
DOCKERFILE = REPO / "cloud" / "Dockerfile"

#: Named so that a leftover from a killed run is obvious. Both are deleted by the fixtures.
SESSION_NAME = "t031_mock_smoke"
RUN_NAME = "t031-smoke"
JOB_ID = "t031-train"

#: The U-Net and encoder of tests/test_diffusion.py's TINY, written into the *pushed* config.
TINY_DIFFUSION = {"encoder_image_hw": [48, 64], "down_dims": [64, 128, 256],
                  "spatial_softmax_keypoints": 8, "stats_samples": 8}

#: Packages pinned in both requirements.txt and cloud/requirements-train.txt; the versions must agree
#: (torch and torchvision modulo the local version: `+cpu` on the laptop, `+cu128` in the image).
SHARED_PINS = ("torch", "torchvision", "lerobot", "numpy", "pyyaml", "structlog", "scipy",
               "opencv-python-headless")
#: requirements.txt entries that must never appear in the image (see cloud/requirements-train.txt).
EXCLUDED_FROM_IMAGE = ("pxdex", "unitree_sdk2py", "mujoco", "mink", "pico_bridge")


def _pins(path: Path) -> dict[str, str]:
    """``name -> version`` for the plain ``name==version`` lines of a requirements file."""
    pins = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if "==" in line and not line.startswith("-"):
            name, version = line.split("==", 1)
            pins[name.strip().lower()] = version.strip()
    return pins


@pytest.fixture(scope="module")
def session(tmp_path_factory) -> Iterator[Path]:
    """Two short mock episodes recorded under ``data/raw/``, which is the tree `up` pushes."""
    rig = Rig(tmp_path_factory.mktemp("t031"))
    rig.run(0.5)
    rig.rec.mark_success()
    rig.rec.stop_episode()
    rig.run(0.3, Command(Primitive.ROLL, None, None, None))
    rig.rec.stop_episode()
    rig.rec.close()

    destination = REPO / "data" / "raw" / SESSION_NAME
    shutil.rmtree(destination, ignore_errors=True)
    shutil.copytree(rig.root, destination)
    try:
        yield destination
    finally:
        shutil.rmtree(destination, ignore_errors=True)


@pytest.fixture(scope="module")
def round_trip(session, tmp_path_factory) -> Iterator[dict]:
    """Run up -> train -> down once and hand the outcome to every test below.

    Returns the two subprocess results, the fake remote root, and the checkpoint sizes measured
    before the run directory is deleted again (Q-002: nothing this suite writes may stay on disk).
    """
    tmp = tmp_path_factory.mktemp("t031-remote")
    home, remote = tmp / "home", tmp / "remote"
    home.mkdir()
    environment = {
        **os.environ,
        "HOME": str(home),
        "GREENNODE_ENV_FILE": str(home / ".config" / "ludo-g1" / "env"),
        "GREENNODE_TRANSPORT": "local",
        "GREENNODE_LOCAL_ROOT": str(remote),
        "GREENNODE_HEARTBEAT_SECONDS": "1",
        "GREENNODE_JOB_ID": JOB_ID,
    }

    def run(*args: str, timeout: float = 900) -> subprocess.CompletedProcess:
        done = subprocess.run([str(GREENNODE), *args], cwd=REPO, env=environment,
                              capture_output=True, text=True, timeout=timeout, check=False)
        print(f"$ cloud/greennode.sh {' '.join(args)}\n{done.stdout}\n{done.stderr}")
        return done

    checkpoints = REPO / "data" / "checkpoints" / RUN_NAME
    shutil.rmtree(checkpoints, ignore_errors=True)
    try:
        up = run("up")
        assert up.returncode == 0, up.stderr
        # The pushed copy is shrunk to the test scale (see the module docstring): the model the job
        # builds and the config hashes it records both come from this, not from the repo's config/.
        config_root(remote, small=True)
        training = yaml.safe_load((remote / "config" / "training.yaml").read_text(encoding="utf-8"))
        training["diffusion"].update(TINY_DIFFUSION)
        (remote / "config" / "training.yaml").write_text(yaml.safe_dump(training, sort_keys=False),
                                                         encoding="utf-8")

        train = run("train", "policy/train.py", "--sessions", f"data/raw/{SESSION_NAME}",
                    "--smoke", "--run-name", RUN_NAME)
        down = run("down")
        sizes = {p.name: p.stat().st_size for p in sorted(checkpoints.glob("*"))} if checkpoints.is_dir() else {}
        record = checkpoints / "run.json"
        curve = checkpoints / "loss.csv"
        yield {"train": train, "down": down, "remote": remote, "sizes": sizes,
               "loss_csv": curve.read_text(encoding="utf-8") if curve.is_file() else "",
               "run": json.loads(record.read_text(encoding="utf-8")) if record.is_file() else None}
    finally:
        # The weights are deleted whatever happened: their measured size is in the report instead.
        shutil.rmtree(checkpoints, ignore_errors=True)
        for leftover in (REPO / "data" / "logs" / "greennode").glob(f"{JOB_ID}.*"):
            leftover.unlink()


def test_train_job_succeeds(round_trip) -> None:
    """The job runs policy/train.py inside the fake remote and exits 0."""
    train = round_trip["train"]
    assert train.returncode == 0, train.stderr
    assert "finished with exit code 0" in train.stdout, train.stdout


def test_checkpoint_round_trip(round_trip) -> None:
    """ACCEPTANCE: `down` brings back a run directory the remote wrote, with its loss curve."""
    sizes = round_trip["sizes"]
    assert set(sizes) >= {"run.json", "loss.csv", "checkpoint.pt"}, sizes
    print("checkpoint sizes at the test scale (deleted after the run): "
          + ", ".join(f"{name} {size / 1e6:.1f} MB" for name, size in sizes.items()))
    assert sizes["checkpoint.pt"] > 0
    rows = round_trip["loss_csv"].strip().splitlines()
    # the curve gained a val_loss and an lr column in T-035; tests/test_train.py owns them
    assert rows[0] == "step,loss,val_loss,lr,elapsed_s" and len(rows) == 31, rows[:2]  # 30 steps of --smoke


def test_run_json_carries_both_hashes(round_trip, session) -> None:
    """ACCEPTANCE: the two hashes are in run.json, and the config hash is the *pushed* config's."""
    run = round_trip["run"]
    assert run is not None, "no run.json came back from `down`"
    assert run["dataset_manifest_sha256"] == dataset_manifest_hash([session])
    pushed = round_trip["remote"] / "config"
    assert run["config_hashes"] == {name: config.config_hash(name, pushed) for name in config.NAMES}
    assert run["config_hashes"]["training"] != config.config_hash("training"), (
        "the job used the laptop's config/, not the pushed one: the transport is not isolating the tree"
    )
    assert run["sessions"][0]["path"] == f"data/raw/{SESSION_NAME}"
    assert run["frames"] > 0 and run["args"]["steps"] == 30


def test_hashes_are_echoed_by_greennode_and_in_the_job_log(round_trip) -> None:
    """`train` repeats the run's identity lines, and they are in the job log the wrapper wrote."""
    run = round_trip["run"]
    manifest, config_hash = run["dataset_manifest_sha256"], run["config_hashes"]["training"]
    assert f"dataset manifest sha256 {manifest}" in round_trip["train"].stdout
    assert f"training config hash {config_hash}" in round_trip["train"].stdout
    log = (round_trip["remote"] / "data" / "logs" / "greennode" / f"{JOB_ID}.log").read_text(encoding="utf-8")
    assert manifest in log and config_hash in log
    heartbeat = (REPO / "data" / "logs" / "greennode" / f"{JOB_ID}.heartbeat").read_text(encoding="utf-8")
    assert "state=finished exit=0" in heartbeat, heartbeat


def test_training_requirements_match_requirements_txt() -> None:
    """The image's pins are requirements.txt's, and the two unresolvable entries are absent."""
    repo_pins, image_pins = _pins(REPO / "requirements.txt"), _pins(TRAIN_REQUIREMENTS)
    for name in SHARED_PINS:
        assert name in image_pins, f"{name} is missing from cloud/requirements-train.txt"
        want, got = repo_pins[name].split("+")[0], image_pins[name].split("+")[0]
        assert want == got, f"{name}: requirements.txt pins {want}, the image pins {got}"
    assert image_pins["torch"].endswith("+cu128"), "the image must install the CUDA torch build, not +cpu"
    assert image_pins["torchvision"].endswith("+cu128")
    text = TRAIN_REQUIREMENTS.read_text(encoding="utf-8")
    for name in EXCLUDED_FROM_IMAGE:
        assert f"\n{name}" not in text, f"{name} must not be an image dependency"


def test_dockerfile_is_a_cuda_image_for_this_repo() -> None:
    """Read-only lint of cloud/Dockerfile: docker is not installed here, so this is the only check."""
    text = DOCKERFILE.read_text(encoding="utf-8")
    assert text.startswith("# Training image"), "the header explains why the image is unbuilt here"
    assert "FROM nvidia/cuda@sha256:" in text, "the CUDA base must be pinned by digest"
    assert "cloud/requirements-train.txt" in text, "the image must install the pinned training subset"
    assert "python3.10" in text, "Python 3.10 is fixed by D-002 A1 and lerobot 0.4.4 (D-015)"
    assert "PYTHONPATH=/work" in text, "the bind-mounted repo must be importable by policy/train.py"
