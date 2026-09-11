"""Diffusion Policy wrapper, smoke training, export and inference latency (T-029; CLAUDE.md 5.7, 5.8).

Everything here runs on the mock session `tests/test_recorder.py`'s `Rig` records under `tmp_path`,
at 64x48 frames and with a deliberately small model (`TINY`), so that a suite that must stay under
three minutes still exercises the real code path: the real lerobot policy, the real goal projection,
the real training loop, the real bundle. The numbers a *decision* rests on -- the smoke-train losses
and the inference latency at the configured frame sizes and model size -- are measured by the CLI
commands recorded in `agents/BUILD_LOG.md`, not here (R5).

Nothing here touches hardware and nothing sends a command (R1, R2).
"""

from __future__ import annotations

import json
import time
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest
import torch
from lerobot.configs.types import FeatureType, PolicyFeature
from torch.utils.data import DataLoader

from engine.interface import Cell, Command, Primitive
from eval.run_eval import make_policy
from policy.dataset import CAMERAS, LudoDataset
from policy.diffusion import (
    BUNDLE_FILE,
    IMAGE_KEYS,
    WEIGHTS_FILE,
    DiffusionAdapter,
    GoalDiffusionPolicy,
    PolicySpec,
    dataset_stats,
)
from policy.export import export
from policy.train import train
from runtime.policy_api import ActionChunk, Observation, Policy
from tests.test_recorder import SMALL, Rig

#: Model overrides that keep a real ResNet-18 / U-Net / DDIM path but fit in a test suite.
TINY = {"image_hw": (48, 64), "down_dims": (64, 128, 256), "spatial_softmax_keypoints": 8}
#: The smoke run of the task: 30 steps, and a learning rate that moves a model that far in 30 steps.
STEPS, BATCH, LR = 30, 2, 1e-3
#: Latency is measured over 20 calls, as the task asks.
LATENCY_TRIALS = 20


# --------------------------------------------------------------------------------------------------
# fixtures: one mock session, one tiny training run, one bundle, shared by every test below
# --------------------------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def session(tmp_path_factory) -> tuple[Path, Path]:
    """Two short episodes (a MOVE and a ROLL) recorded onto the mocks. Returns (session, config)."""
    rig = Rig(tmp_path_factory.mktemp("diffusion"))
    rig.run(1.0)
    rig.rec.mark_success()
    rig.rec.stop_episode()
    rig.run(0.5, Command(Primitive.ROLL, None, None, None))
    rig.rec.stop_episode()
    rig.rec.close()
    return rig.root, rig.cfg


@pytest.fixture(scope="module")
def spec(session) -> PolicySpec:
    return PolicySpec.from_config(session[1], **TINY)


@pytest.fixture(scope="module")
def data(session, spec) -> LudoDataset:
    """At the policy's own observation history (T-034): two frames per sample for the diffusion model."""
    return LudoDataset([session[0]], chunk=spec.chunk, n_obs_steps=spec.n_obs_steps, config_root=session[1])


@pytest.fixture(scope="module")
def batch(data) -> dict[str, torch.Tensor]:
    """One fixed batch of two samples: the probe every loss comparison here is made on."""
    return next(iter(DataLoader(data, batch_size=2, shuffle=False)))


@pytest.fixture(scope="module")
def run(session, spec, tmp_path_factory) -> dict:
    """The 30-step smoke run of the task, on the mock session, on CPU."""
    out = tmp_path_factory.mktemp("checkpoints")
    return train(
        [session[0]], steps=STEPS, batch_size=BATCH, learning_rate=LR, weight_decay=1e-6, seed=0,
        device="cpu", out_dir=out, log_dir=out, run_name="tiny", spec=spec, config_root=session[1],
        stats_samples=8, augment=True,
    )


@pytest.fixture(scope="module")
def bundle(run, tmp_path_factory) -> tuple[Path, dict]:
    """The exported inference bundle of that run."""
    out = tmp_path_factory.mktemp("bundle")
    manifest = export(Path(run["checkpoint"]), out)
    return out, manifest


def observation(size: dict[str, list[int]] | None = None, seed: int = 0) -> Observation:
    """A synthetic observation of CLAUDE.md 5.3 at the given frame sizes (default: the mock's)."""
    sizes = SMALL if size is None else size
    rng = np.random.default_rng(seed)
    frames = {name: rng.integers(0, 256, (hw[1], hw[0], 3), dtype=np.uint8) for name, hw in sizes.items()}
    goal = rng.random((2, frames["top"].shape[0], frames["top"].shape[1])).astype(np.float32)
    return Observation(ts_ns=0, top=frames["top"], oblique=frames["oblique"], palm=frames["palm"],
                       state=np.linspace(-0.2, 0.2, 9), goal=goal, task_id=np.array([1.0, 0.0, 0.0], dtype=np.float32))


def move() -> Command:
    return Command(Primitive.MOVE, Cell("track-01", (0.0, 0.0), (10.0, 10.0)),
                   Cell("track-05", (10.0, 10.0), (20.0, 20.0)), "R1")


# --------------------------------------------------------------------------------------------------
# acceptance 1: forward pass shapes -- and why the goal channels need a wrapper at all
# --------------------------------------------------------------------------------------------------


def test_lerobot_refuses_a_five_channel_top_beside_three_channel_cameras(spec) -> None:
    """The reason `GoalDiffusionPolicy` projects instead of declaring 5 channels (docs/policy.md)."""
    features = {key: PolicyFeature(FeatureType.VISUAL, (3, *spec.image_hw)) for key in IMAGE_KEYS.values()}
    features[IMAGE_KEYS["top"]] = PolicyFeature(FeatureType.VISUAL, (5, *spec.image_hw))
    features["observation.state"] = PolicyFeature(FeatureType.STATE, (spec.cond_state_dim,))
    config = replace(spec.lerobot_config(), input_features=features)
    with pytest.raises(ValueError, match="we expect all image shapes to match"):
        config.validate_features()


def test_forward_and_predict_shapes(spec, batch) -> None:
    model = GoalDiffusionPolicy(spec)
    loss = model(batch)
    assert loss.shape == () and torch.isfinite(loss)

    actions = model.predict(batch)
    assert actions.shape == (2, spec.chunk, spec.action_dim)  # the whole horizon, not `execute` of it
    assert torch.isfinite(actions).all()

    # the 1x1 projection: RGB through unchanged, goal channels at zero weight (module docstring)
    assert tuple(model.goal_proj.weight.shape) == (3, 3 + spec.goal_channels, 1, 1)
    assert torch.equal(model.goal_proj.weight[:, :3, 0, 0], torch.eye(3))
    assert float(model.goal_proj.weight[:, 3:].abs().sum()) == 0.0
    # the batch arrives with a real history (T-034), not one frame the model has to repeat
    assert tuple(batch["top"].shape) == (2, spec.n_obs_steps, 3 + spec.goal_channels, *SMALL["top"][::-1])
    assert tuple(batch["state"].shape) == (2, spec.n_obs_steps, 9)
    assert tuple(batch["obs_mask"].shape) == (2, spec.n_obs_steps)
    # lerobot sees 12 = 9 state + 3 task one-hot, one entry per observation step
    prepared = model._lerobot_batch(batch)
    assert tuple(prepared["observation.state"].shape) == (2, spec.n_obs_steps, spec.cond_state_dim)
    for key in IMAGE_KEYS.values():
        assert tuple(prepared[key].shape) == (2, spec.n_obs_steps, 3, *spec.image_hw)


def test_statistics_cover_the_goal_channels_and_the_task_one_hot(data, spec) -> None:
    stats = dataset_stats(data, samples=8, seed=0)
    assert tuple(stats["top_mean"].shape) == (3 + spec.goal_channels, 1, 1)
    assert tuple(stats["state_min"].shape) == (spec.cond_state_dim,)
    assert tuple(stats["action_min"].shape) == (spec.action_dim,)
    model = GoalDiffusionPolicy(spec)
    model.norm.load_stats(stats)
    assert bool(model.norm.fitted)
    # min/max normalisation is exactly invertible, which is what the adapter relies on
    sample = torch.rand(4, spec.action_dim) * 0.1
    assert torch.allclose(model.norm.unnormalize_action(model.norm.action(sample)), sample, atol=1e-5)
    with pytest.raises(KeyError, match="unknown statistic"):
        model.norm.load_stats({"nope": torch.zeros(1)})


# --------------------------------------------------------------------------------------------------
# acceptance 2: 30 steps on the mock session reduce the loss
# --------------------------------------------------------------------------------------------------


def _probe(model: GoalDiffusionPolicy, batch: dict[str, torch.Tensor], seed: int = 1234) -> float:
    """The loss on one fixed batch with a fixed noise and timestep draw, so two models compare.

    `compute_loss` samples its own epsilon and diffusion timestep, so a single training-step loss is
    a noisy estimate of nothing in particular; seeding the global RNG immediately before the call
    makes the *same* draw for both models and turns the comparison into a measurement.
    """
    torch.manual_seed(seed)
    with torch.no_grad():
        return float(model(batch))


def test_smoke_train_reduces_the_loss(run, spec, batch, capsys) -> None:
    checkpoint = torch.load(run["checkpoint"], map_location="cpu", weights_only=True)
    trained = GoalDiffusionPolicy(spec)
    trained.load_state_dict(checkpoint["state_dict"])
    trained.eval()

    torch.manual_seed(run["args"]["seed"])  # the seed policy/train.py set before building the model
    initial = GoalDiffusionPolicy(spec)
    initial.load_state_dict({k: v for k, v in checkpoint["state_dict"].items() if k.startswith("norm.")},
                            strict=False)
    initial.eval()

    before, after = _probe(initial, batch), _probe(trained, batch)
    with capsys.disabled():
        print(f"\n[T-029] smoke train {STEPS} steps, batch {BATCH}, lr {LR}, {spec.image_hw} frames")
        print(f"[T-029]   training loss: step 1 {run['loss_first']:.4f} -> step {STEPS} {run['loss_last']:.4f} "
              f"(mean of the last 10: {run['loss_mean_last_10']:.4f})")
        print(f"[T-029]   fixed-probe loss: before {before:.4f} -> after {after:.4f} "
              f"({100 * (before - after) / before:+.1f}%)")
    assert after < before, "30 steps did not reduce the loss on a fixed probe batch"
    assert run["loss_last"] < run["loss_first"], "training loss at step 30 was not below step 1"

    rows = (Path(run["checkpoint"]).parent / "loss.csv").read_text(encoding="utf-8").splitlines()
    # the curve gained a validation and a learning-rate column in T-035; tests/test_train.py owns them
    assert rows[0] == "step,loss,val_loss,lr,elapsed_s" and len(rows) == STEPS + 1
    record = json.loads((Path(run["checkpoint"]).parent / "run.json").read_text(encoding="utf-8"))
    assert record["dataset_manifest_sha256"] == run["dataset_manifest_sha256"]
    assert len(record["config_hashes"]["training"]) == 64 and record["frames"] == run["frames"]


# --------------------------------------------------------------------------------------------------
# acceptance 3: the export round trip
# --------------------------------------------------------------------------------------------------


def test_export_writes_a_self_contained_bundle(bundle, capsys) -> None:
    directory, manifest = bundle
    assert (directory / BUNDLE_FILE).is_file() and (directory / WEIGHTS_FILE).is_file()
    assert manifest["format"] == "ludo-g1/diffusion-bundle/1"
    assert len(manifest["weights_sha256"]) == 64 and manifest["train_run"]["dataset_manifest_sha256"]
    with capsys.disabled():
        print(f"[T-029] torchscript: {manifest['torchscript'] or 'no -- ' + str(manifest['trace_error'])}"
              f" (max diff vs eager {manifest['torchscript_max_diff']})")
    # Whichever way tracing went, the bundle must say so rather than leave it unrecorded (5.8), and
    # the adapter must not depend on it either way.
    assert (manifest["torchscript"] is None) != (manifest["trace_error"] is None)
    assert manifest["torchscript_used_at_inference"] is False
    assert manifest["weights_source"] == "ema"  # T-035: a bundle carries the run's averaged weights
    if manifest["torchscript"]:
        assert (directory / manifest["torchscript"]).is_file()
        assert manifest["torchscript_max_diff"] <= 1e-4


def test_export_round_trip_gives_identical_actions(bundle, run, spec) -> None:
    """Two adapters over the same bundle, same seed, same observation: the same chunk to 1e-5."""
    directory, _manifest = bundle
    obs = observation()
    chunks = []
    for _ in range(2):
        adapter = DiffusionAdapter(directory, seed=7)
        adapter.reset(move())
        chunks.append(adapter.act(obs).actions)
    assert np.allclose(chunks[0], chunks[1], atol=1e-5)

    # and the bundle is the checkpoint: the same weights produce the same actions out of train().
    # "the same weights" are the EMA of the run since T-035, which is what the bundle carries.
    checkpoint = torch.load(run["checkpoint"], map_location="cpu", weights_only=True)
    model = GoalDiffusionPolicy(spec)
    model.load_state_dict(checkpoint["ema_state_dict"])
    model.eval()
    noise = torch.randn((1, spec.chunk, spec.action_dim), generator=torch.Generator().manual_seed(7))
    adapter = DiffusionAdapter(directory, seed=7)
    adapter.reset(move())
    direct = model.predict(
        {key: value.unsqueeze(0) for key, value in adapter._frame(obs).items()}, noise=noise
    )
    assert np.allclose(direct[0].numpy(), adapter.act(obs).actions, atol=1e-5)


# --------------------------------------------------------------------------------------------------
# acceptance 4: the adapter is a Policy, and how long act() takes
# --------------------------------------------------------------------------------------------------


def test_adapter_satisfies_the_policy_protocol(bundle, spec) -> None:
    adapter = DiffusionAdapter(bundle[0], seed=0)
    assert isinstance(adapter, Policy)
    adapter.reset(move())
    chunk = adapter.act(observation())
    assert isinstance(chunk, ActionChunk)
    assert chunk.actions.shape == (spec.chunk, spec.action_dim) and chunk.hz == spec.action_hz
    assert len(chunk) == spec.chunk >= spec.execute  # the controller plays `execute` of what it gets
    assert adapter.done(observation()) is False  # no termination head: the controller's timeout ends it
    # the observation queue fills from one frame and is dropped by reset (receding horizon, 5.2)
    assert len(adapter._queue) == spec.n_obs_steps
    adapter.reset(move())
    assert len(adapter._queue) == 0 and adapter.calls == 0
    # a frame at the configured camera sizes is resized onto the encoder, not refused
    big = {"top": [640, 480], "oblique": [640, 480], "palm": [320, 240]}
    assert adapter.act(observation(big)).actions.shape == (spec.chunk, spec.action_dim)


def test_act_latency_at_ddim_10(bundle, spec, capsys) -> None:
    """Print the per-call cost. The number that matters is the CLI one in BUILD_LOG (full model)."""
    adapter = DiffusionAdapter(bundle[0], seed=0)
    assert adapter.spec.inference_steps == 10  # config/training.yaml diffusion.inference_steps
    obs = observation()
    adapter.reset(move())
    adapter.act(obs)  # warm-up: the first call pays lazy allocations
    started = time.perf_counter()
    for _ in range(LATENCY_TRIALS):
        adapter.act(obs)
    mean_ms = (time.perf_counter() - started) * 1e3 / LATENCY_TRIALS
    with capsys.disabled():
        print(f"[T-029] adapter.act() at DDIM 10, {spec.image_hw} encoder, {TINY['down_dims']} U-Net: "
              f"{mean_ms:.0f} ms mean over {LATENCY_TRIALS} calls (budget at 10 Hz: 100 ms)")
    assert mean_ms > 0


def test_run_eval_loads_a_bundle(bundle) -> None:
    """The `--policy bundle PATH` hook of eval/run_eval.py (T-028) now resolves to this adapter."""
    policy, info = make_policy(["bundle", str(bundle[0])], "mock")
    assert isinstance(policy, DiffusionAdapter)
    assert info["tag"] == "bundle" and info["checkpoint_sha256"] == bundle[1]["weights_sha256"]
    assert info["inference_steps"] == 10 and info["dataset_manifest_sha256"]


def _observation_of(sample: dict[str, torch.Tensor]) -> Observation:
    """The current frame of one dataset sample as the `Observation` the controller would build."""
    current = {name: (sample[name][-1] if sample[name].ndim == 4 else sample[name]) for name in CAMERAS}
    state = sample["state"][-1] if sample["state"].ndim == 2 else sample["state"]
    return Observation(
        ts_ns=0,
        top=current["top"][:3].permute(1, 2, 0).numpy(),
        oblique=current["oblique"].permute(1, 2, 0).numpy(),
        palm=current["palm"].permute(1, 2, 0).numpy(),
        state=state.numpy(),
        goal=current["top"][3:].numpy(),
        task_id=sample["task_id"].numpy(),
    )


def test_the_adapter_queue_and_the_dataset_history_agree(session, spec, bundle) -> None:
    """T-034: training history and inference queue are the same tensor, order and padding included.

    Two consecutive frames of a recorded episode are fed to the adapter as two `Observation`s; after
    each call its assembled batch is compared with the dataset's own sample at that frame. The first
    call exercises the padding rule at the start of an episode (the adapter repeats the only frame it
    has; lerobot's `delta_timestamps` clamping repeats the episode's first frame), the second the
    ordering (oldest first).
    """
    root, cfg = session
    data = LudoDataset([root], chunk=spec.chunk, n_obs_steps=spec.n_obs_steps, config_root=cfg)
    assert spec.n_obs_steps == 2
    episode = data.episodes[0]
    adapter = DiffusionAdapter(bundle[0], seed=0)
    adapter.reset(move())
    for step in range(spec.n_obs_steps):
        index = next(i for i, (pos, frame) in enumerate(data._index)
                     if pos == 0 and frame == episode.start + step)
        sample = data[index]
        batch = adapter._batch(_observation_of(sample))
        for key in (*CAMERAS, "state"):
            assert torch.equal(batch[key][0], sample[key]), f"{key} differs at step {step}"
        assert torch.equal(batch["task_id"][0], sample["task_id"].expand(spec.n_obs_steps, -1))
        assert sample["obs_mask"].tolist() == ([0.0, 1.0] if step == 0 else [1.0, 1.0])
    assert len(adapter._queue) == spec.n_obs_steps


def test_nothing_in_policy_imports_a_driver_or_a_hardware_check() -> None:
    """R2: `policy/` learns and exports; it never reaches a device or a bring-up script."""
    for name in ("_shared", "dataset", "diffusion", "train", "export"):
        source = (Path(__file__).resolve().parent.parent / "policy" / f"{name}.py").read_text(encoding="utf-8")
        assert "tools.hardware_checks" not in source and "import drivers" not in source
        assert "from drivers" not in source
    assert set(CAMERAS) == set(IMAGE_KEYS)
