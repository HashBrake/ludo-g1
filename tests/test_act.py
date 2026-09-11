"""ACT baseline wrapper, temporal ensembling, smoke training, export and latency (T-030; 5.7, 5.8).

Structured as `tests/test_diffusion.py` and at the same scale: the mock session `tests/test_recorder.py`'s
`Rig` records under `tmp_path`, 64x48 frames, a deliberately small transformer (`TINY`), so that a
suite that must stay under three minutes still exercises the real code path -- the real lerobot
ACTPolicy, the real goal projection, the real training loop, the real bundle. The numbers a *decision*
rests on (D-019: ACT against the diffusion policy at the configured frame sizes and model size) are
measured by the CLI commands recorded in `agents/BUILD_LOG.md`, not here (R5).

Nothing here touches hardware and nothing sends a command (R1, R2).
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import numpy as np
import pytest
import torch
from lerobot.policies.act.configuration_act import ACTConfig
from lerobot.policies.act.modeling_act import ACTTemporalEnsembler
from torch.utils.data import DataLoader

from engine.interface import Command, Primitive
from eval.run_eval import make_policy
from policy._shared import BUNDLE_FILE, IMAGE_KEYS, WEIGHTS_FILE, Normalizer, dataset_stats, observation_frame
from policy.act import ACTAdapter, ACTSpec, GoalACTPolicy, TemporalEnsemble
from policy.dataset import CAMERAS, LudoDataset
from policy.diffusion import DiffusionAdapter
from policy.export import BUNDLE_FORMATS, export, open_bundle
from policy.train import POLICIES, policy_kind, train
from runtime import config
from runtime.policy_api import ActionChunk, Observation, Policy
from tests.test_diffusion import move, observation

#: Model overrides that keep a real ResNet-18 backbone and a real transformer but fit in a test suite.
TINY = {"image_hw": (48, 64), "dim_model": 64, "n_heads": 4, "dim_feedforward": 128,
        "n_encoder_layers": 1, "n_vae_encoder_layers": 1, "latent_dim": 8}
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
    from tests.test_recorder import Rig

    rig = Rig(tmp_path_factory.mktemp("act"))
    rig.run(1.0)
    rig.rec.mark_success()
    rig.rec.stop_episode()
    rig.run(0.5, Command(Primitive.ROLL, None, None, None))
    rig.rec.stop_episode()
    rig.rec.close()
    return rig.root, rig.cfg


@pytest.fixture(scope="module")
def spec(session) -> ACTSpec:
    return ACTSpec.from_config(session[1], **TINY)


@pytest.fixture(scope="module")
def data(session, spec) -> LudoDataset:
    """At the policy's own observation history (T-034): ACT takes one frame per sample, and only one."""
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
        device="cpu", out_dir=out, log_dir=out, run_name="tiny_act", spec=spec, config_root=session[1],
        stats_samples=8, augment=True,
    )


@pytest.fixture(scope="module")
def bundle(run, tmp_path_factory) -> tuple[Path, dict]:
    """The exported inference bundle of that run."""
    out = tmp_path_factory.mktemp("bundle")
    manifest = export(Path(run["checkpoint"]), out)
    return out, manifest


# --------------------------------------------------------------------------------------------------
# acceptance 1: forward pass shapes -- and why the goal channels need a wrapper here too
# --------------------------------------------------------------------------------------------------


def test_lerobot_act_has_one_backbone_three_channels_wide(spec, batch) -> None:
    """The reason `GoalACTPolicy` projects 5 -> 3 (docs/policy.md): one shared ResNet-18."""
    model = GoalACTPolicy(spec)
    backbones = [name for name, _m in model.lerobot.named_modules() if name.endswith("backbone")]
    assert backbones == ["model.backbone"]  # one encoder for all three cameras, unlike DiffusionConfig
    assert not hasattr(ACTConfig, "use_separate_rgb_encoder_per_camera")
    prepared = model._lerobot_batch(batch)
    prepared[IMAGE_KEYS["top"]] = torch.rand(2, 3 + spec.goal_channels, *spec.image_hw)
    with pytest.raises(RuntimeError, match="3 channels"):
        model.lerobot.predict_action_chunk(prepared)


def test_the_two_models_of_5_7_see_the_same_inputs(spec) -> None:
    """CLAUDE.md 5.7: the baseline is trained on the same data; it must also be fed the same way."""
    training = config.load("training")
    assert training["act"]["encoder_image_hw"] == training["diffusion"]["encoder_image_hw"]
    assert training["act"]["obs_history"] == 1  # ACTConfig refuses anything else
    assert training["act"]["expose"] == training["diffusion"]["chunk"]  # same contract to the controller
    assert training["act"]["pretrained_backbone_weights"] is None  # as DiffusionConfig's default
    # ... and the shared feeding code is one module both import, not one model importing from the
    # other through its private names (T-030 review, done in T-034).
    assert spec.n_obs_steps == 1 and spec.lerobot_config().n_obs_steps == 1
    assert isinstance(GoalACTPolicy(spec).norm, Normalizer)
    for module in ("act", "diffusion"):
        source = (Path(__file__).resolve().parent.parent / "policy" / f"{module}.py").read_text(encoding="utf-8")
        assert "from policy._shared import" in source
        assert "_Normalizer" not in source and "_image_tensor" not in source


def test_an_obs_history_act_cannot_take_is_refused(session, tmp_path) -> None:
    """T-034: `act.obs_history` other than 1 is a config error, not a dataset ACT chokes on later."""
    import yaml

    out = tmp_path / "config"
    out.mkdir()
    for name in config.NAMES:
        block = config.load(name, root=session[1])
        if name == "training":
            block["act"]["obs_history"] = 2
        (out / f"{name}.yaml").write_text(yaml.safe_dump(block, sort_keys=False), encoding="utf-8")
    with pytest.raises(config.ConfigError, match="act.obs_history must be 1"):
        ACTSpec.from_config(out)


def test_forward_and_predict_shapes(spec, batch, data) -> None:
    model = GoalACTPolicy(spec)
    model.norm.load_stats(dataset_stats(data, samples=8, seed=0))
    model.train()
    loss = model(batch)
    assert loss.shape == () and torch.isfinite(loss)

    model.eval()
    actions = model.predict(batch)
    assert actions.shape == (2, spec.chunk, spec.action_dim)  # all 32; the adapter exposes 16
    assert torch.isfinite(actions).all()

    # the 1x1 projection: RGB through unchanged, goal channels at zero weight (module docstring)
    assert tuple(model.goal_proj.weight.shape) == (3, 3 + spec.goal_channels, 1, 1)
    assert torch.equal(model.goal_proj.weight[:, :3, 0, 0], torch.eye(3))
    assert float(model.goal_proj.weight[:, 3:].abs().sum()) == 0.0
    # lerobot sees 12 = 9 state + 3 task one-hot, and *no* observation-step dimension
    prepared = model._lerobot_batch(batch)
    assert tuple(prepared["observation.state"].shape) == (2, spec.cond_state_dim)
    for key in IMAGE_KEYS.values():
        assert tuple(prepared[key].shape) == (2, 3, *spec.image_hw)
    with pytest.raises(ValueError, match="one observation step"):
        model._images({name: batch[name].unsqueeze(1) for name in CAMERAS})


# --------------------------------------------------------------------------------------------------
# acceptance 2: temporal ensembling -- the same weights as lerobot, over a chunk instead of one action
# --------------------------------------------------------------------------------------------------


def _compare_with_lerobot(chunk: int, dim: int, coeff: float, queries: int, *, float64: bool) -> float:
    """Largest difference between this ensemble and lerobot's over ``queries`` stride-1 updates."""
    rng = np.random.default_rng(0)
    mine = TemporalEnsemble(chunk=chunk, expose=1, coeff=coeff, stride=1)
    theirs = ACTTemporalEnsembler(coeff, chunk)
    if float64:
        # lerobot builds its weights in float32 (`torch.exp(-coeff * torch.arange(n))`); recomputing
        # exactly that expression in float64 isolates the recursion from the dtype it is built in,
        # which is the only thing the two implementations differ by.
        theirs.ensemble_weights = torch.exp(-coeff * torch.arange(chunk, dtype=torch.float64))
        theirs.ensemble_weights_cumsum = torch.cumsum(theirs.ensemble_weights, dim=0)
    worst = 0.0
    for _ in range(queries):
        prediction = rng.normal(size=(chunk, dim))
        ours = mine.update(prediction)[0]
        upstream = theirs.update(torch.as_tensor(prediction[None], dtype=torch.float64))[0].numpy()
        worst = max(worst, float(np.abs(ours - upstream).max()))
    return worst


def test_temporal_ensemble_matches_lerobots_own(capsys) -> None:
    """At stride 1 and one exposed action, this is exactly `ACTTemporalEnsembler` (module docstring).

    Two numbers: against lerobot as it ships (its exponential weights are float32, this is float64,
    and that difference alone is ~1e-8), and against the same recursion in float64, where the offline
    form and the online form agree to machine precision.
    """
    chunk, dim, coeff, queries = 8, 3, 0.01, 12
    shipped = _compare_with_lerobot(chunk, dim, coeff, queries, float64=False)
    promoted = _compare_with_lerobot(chunk, dim, coeff, queries, float64=True)
    with capsys.disabled():
        print(f"\n[T-030] temporal ensemble vs lerobot ACTTemporalEnsembler over {queries} queries: "
              f"max abs difference {shipped:.2e} as shipped (float32 weights), {promoted:.2e} in float64")
    assert shipped < 1e-6
    assert promoted < 1e-12


def test_temporal_ensemble_weights_every_prediction_of_one_step() -> None:
    """The offline form of the same rule at the controller's stride (action_hz / policy_hz = 3)."""
    chunk, expose, dim, coeff, stride = 32, 16, 2, 0.01, 3
    rng = np.random.default_rng(1)
    ensemble = TemporalEnsemble(chunk=chunk, expose=expose, coeff=coeff, stride=stride)
    first = rng.normal(size=(chunk, dim))
    # Nothing to average with yet: the first query is handed straight through.
    assert np.allclose(ensemble.update(first), first[:expose])
    second = rng.normal(size=(chunk, dim))
    out = ensemble.update(second)
    w = np.exp(-coeff * np.arange(2))
    # action-step 3 (= the second query's step 0) was predicted by both queries, oldest first
    assert np.allclose(out[0], (w[0] * first[stride] + w[1] * second[0]) / w.sum())
    assert np.allclose(out[expose - 1], (w[0] * first[stride + expose - 1] + w[1] * second[expose - 1]) / w.sum())
    # a prediction that can no longer cover anything is dropped, so the history stays bounded
    for _ in range(chunk):
        ensemble.update(rng.normal(size=(chunk, dim)))
    assert len(ensemble._history) <= -(-chunk // stride)
    ensemble.reset()
    assert ensemble._history == [] and ensemble._t == 0


# --------------------------------------------------------------------------------------------------
# acceptance 3: 30 steps on the mock session reduce the loss, through the same train.py
# --------------------------------------------------------------------------------------------------


def _probe(model: GoalACTPolicy, batch: dict[str, torch.Tensor], seed: int = 1234) -> float:
    """The loss on one fixed batch with a fixed VAE noise draw, so two models compare.

    ACT's loss samples the VAE latent with the reparameterisation trick, so one forward pass is a
    noisy estimate; seeding the global RNG immediately before the call makes the *same* draw for
    both models and turns the comparison into a measurement.
    """
    model.train()
    torch.manual_seed(seed)
    with torch.no_grad():
        return float(model(batch))


def test_smoke_train_reduces_the_loss(run, spec, batch, capsys) -> None:
    checkpoint = torch.load(run["checkpoint"], map_location="cpu", weights_only=True)
    assert checkpoint["policy"] == "act" and run["policy"] == "act"
    trained = GoalACTPolicy(spec)
    trained.load_state_dict(checkpoint["state_dict"])

    torch.manual_seed(run["args"]["seed"])  # the seed policy/train.py set before building the model
    initial = GoalACTPolicy(spec)
    initial.load_state_dict({k: v for k, v in checkpoint["state_dict"].items() if k.startswith("norm.")},
                            strict=False)

    before, after = _probe(initial, batch), _probe(trained, batch)
    with capsys.disabled():
        print(f"[T-030] smoke train {STEPS} steps, batch {BATCH}, lr {LR}, {spec.image_hw} frames")
        print(f"[T-030]   training loss: step 1 {run['loss_first']:.4f} -> step {STEPS} {run['loss_last']:.4f} "
              f"(mean of the last 10: {run['loss_mean_last_10']:.4f})")
        print(f"[T-030]   fixed-probe loss: before {before:.4f} -> after {after:.4f} "
              f"({100 * (before - after) / before:+.1f}%)")
    assert after < before, "30 steps did not reduce the loss on a fixed probe batch"
    assert run["loss_last"] < run["loss_first"], "training loss at step 30 was not below step 1"

    rows = (Path(run["checkpoint"]).parent / "loss.csv").read_text(encoding="utf-8").splitlines()
    assert rows[0] == "step,loss,elapsed_s" and len(rows) == STEPS + 1
    record = json.loads((Path(run["checkpoint"]).parent / "run.json").read_text(encoding="utf-8"))
    assert record["policy"] == "act" and record["spec"]["chunk"] == spec.chunk == 32
    assert len(record["config_hashes"]["training"]) == 64 and record["frames"] == run["frames"]


def test_policy_act_is_one_flag_and_changes_nothing_else(monkeypatch, tmp_path) -> None:
    """CLAUDE.md 5.7: the baseline trains on the same data; `--policy act` is the whole difference."""
    from policy import train as train_module

    seen: dict = {}

    def fake_train(sessions, **kwargs):
        seen.update(kwargs, sessions=list(sessions))
        return {"run": "x", "policy": policy_kind(kwargs["spec"]), "frames": 0, "parameters": 0,
                "config_hashes": {"training": ""}, "dataset_manifest_sha256": "", "loss_first": 1.0,
                "loss_last": 0.5, "loss_mean_last_10": 0.5, "elapsed_s": 0.0,
                "checkpoint": str(tmp_path / "checkpoint.pt")}

    monkeypatch.setattr(train_module, "train", fake_train)
    assert train_module.main(["--sessions", "data/raw/s", "--policy", "act"]) == 0
    block = config.load("training")["act"]
    assert isinstance(seen["spec"], ACTSpec) and seen["sessions"] == ["data/raw/s"]
    assert seen["steps"] == block["train_iterations"] and seen["batch_size"] == block["batch_size"]
    assert seen["learning_rate"] == block["learning_rate"] and seen["weight_decay"] == block["weight_decay"]
    assert seen["seed"] == block["seed"] and seen["augment"] is True

    seen.clear()
    assert train_module.main(["--sessions", "data/raw/s"]) == 0
    assert policy_kind(seen["spec"]) == "diffusion"  # the default is unchanged
    assert set(POLICIES) == {"diffusion", "act"}


# --------------------------------------------------------------------------------------------------
# acceptance 4: the export round trip
# --------------------------------------------------------------------------------------------------


def test_export_writes_a_self_contained_act_bundle(bundle, capsys) -> None:
    directory, manifest = bundle
    assert (directory / BUNDLE_FILE).is_file() and (directory / WEIGHTS_FILE).is_file()
    assert manifest["policy"] == "act" and manifest["format"] == BUNDLE_FORMATS["act"] == "ludo-g1/act-bundle/1"
    assert len(manifest["weights_sha256"]) == 64 and manifest["train_run"]["dataset_manifest_sha256"]
    with capsys.disabled():
        print(f"[T-030] torchscript: {manifest['torchscript'] or 'no -- ' + str(manifest['trace_error'])}"
              f" (max diff vs eager {manifest['torchscript_max_diff']})")
    assert (manifest["torchscript"] is None) != (manifest["trace_error"] is None)
    assert manifest["torchscript_used_at_inference"] is False
    if manifest["torchscript"]:
        assert (directory / manifest["torchscript"]).is_file()
        assert manifest["torchscript_max_diff"] <= 1e-4


def test_export_round_trip_gives_identical_actions(bundle, run, spec) -> None:
    """ACT is deterministic at inference (zero latent), so two adapters agree exactly."""
    directory, _manifest = bundle
    obs = observation()
    chunks = []
    for _ in range(2):
        adapter = ACTAdapter(directory)
        adapter.reset(move())
        chunks.append(adapter.act(obs).actions)
    assert np.allclose(chunks[0], chunks[1], atol=1e-12)

    # and the bundle is the checkpoint: the same weights produce the same actions out of train()
    checkpoint = torch.load(run["checkpoint"], map_location="cpu", weights_only=True)
    model = GoalACTPolicy(spec)
    model.load_state_dict(checkpoint["state_dict"])
    model.eval()
    adapter = ACTAdapter(directory)
    adapter.reset(move())
    direct = model.predict({key: value.unsqueeze(0) for key, value in adapter._frame(obs).items()})
    # the first query has nothing to average with, so the exposed chunk is the prediction's head
    assert np.allclose(direct[0, : spec.expose].numpy(), adapter.act(obs).actions, atol=1e-5)


def test_open_bundle_dispatches_on_the_policy_field(bundle, tmp_path) -> None:
    """One reader for both models of 5.7, and a bundle that lies about its format is refused."""
    directory, manifest = bundle
    assert isinstance(open_bundle(directory), ACTAdapter)
    forged = tmp_path / "forged"
    forged.mkdir()
    (forged / WEIGHTS_FILE).write_bytes((directory / WEIGHTS_FILE).read_bytes())
    (forged / BUNDLE_FILE).write_text(json.dumps({**manifest, "policy": "diffusion"}), encoding="utf-8")
    with pytest.raises(ValueError, match="format"):
        open_bundle(forged)
    (forged / BUNDLE_FILE).write_text(json.dumps({**manifest, "policy": "nope"}), encoding="utf-8")
    with pytest.raises(ValueError, match="knows"):
        open_bundle(forged)


# --------------------------------------------------------------------------------------------------
# acceptance 5: the adapter is a Policy, and how long act() takes
# --------------------------------------------------------------------------------------------------


def test_adapter_satisfies_the_policy_protocol(bundle, spec) -> None:
    adapter = ACTAdapter(bundle[0])
    assert isinstance(adapter, Policy) and not isinstance(adapter, DiffusionAdapter)
    adapter.reset(move())
    chunk = adapter.act(observation())
    assert isinstance(chunk, ActionChunk)
    assert chunk.actions.shape == (spec.expose, spec.action_dim) and chunk.hz == spec.action_hz
    assert len(chunk) == spec.expose == 16  # the controller's contract, unchanged (5.2)
    assert adapter.done(observation()) is False  # no termination head: the controller's timeout ends it
    assert adapter.spec.ensemble_stride == 3 and adapter.spec.temporal_ensemble is True
    # the ensemble accumulates across calls within a primitive and is dropped by reset
    adapter.act(observation())
    assert len(adapter.ensemble._history) == 2 and adapter.calls == 2
    adapter.reset(move())
    assert adapter.ensemble._history == [] and adapter.calls == 0
    # a frame at the configured camera sizes is resized onto the encoder, not refused
    big = {"top": [640, 480], "oblique": [640, 480], "palm": [320, 240]}
    assert adapter.act(observation(big)).actions.shape == (spec.expose, spec.action_dim)


def test_act_latency(bundle, spec, capsys) -> None:
    """Print the per-call cost. The number that matters is the CLI one in BUILD_LOG (full model)."""
    adapter = ACTAdapter(bundle[0])
    obs = observation()
    adapter.reset(move())
    adapter.act(obs)  # warm-up: the first call pays lazy allocations
    started = time.perf_counter()
    for _ in range(LATENCY_TRIALS):
        adapter.act(obs)
    mean_ms = (time.perf_counter() - started) * 1e3 / LATENCY_TRIALS
    with capsys.disabled():
        print(f"[T-030] adapter.act() at {spec.image_hw} encoder, dim_model {spec.dim_model}: "
              f"{mean_ms:.0f} ms mean over {LATENCY_TRIALS} calls (budget at 10 Hz: 100 ms)")
    assert mean_ms > 0


def test_run_eval_loads_an_act_bundle(bundle) -> None:
    """`--policy bundle PATH` takes either model of 5.7 and records which one it ran (R5)."""
    policy, info = make_policy(["bundle", str(bundle[0])], "mock")
    assert isinstance(policy, ACTAdapter)
    assert info["tag"] == "bundle" and info["policy"] == "act"
    assert info["checkpoint_sha256"] == bundle[1]["weights_sha256"]
    assert info["inference_steps"] is None and info["dataset_manifest_sha256"]


def test_nothing_in_policy_act_imports_a_driver_or_a_hardware_check() -> None:
    """R2: `policy/` learns and exports; it never reaches a device or a bring-up script."""
    source = (Path(__file__).resolve().parent.parent / "policy" / "act.py").read_text(encoding="utf-8")
    assert "tools.hardware_checks" not in source and "import drivers" not in source
    assert "from drivers" not in source


def test_the_observation_is_the_one_of_5_3(spec, bundle) -> None:
    """The adapter takes the same `Observation` the diffusion adapter does, through the same code."""
    obs = observation()
    assert isinstance(obs, Observation) and obs.goal.shape[0] == spec.goal_channels
    assert set(CAMERAS) == set(IMAGE_KEYS)
    adapter = ACTAdapter(bundle[0])
    built = adapter._frame(obs)
    shared = observation_frame(obs, adapter.device)
    assert set(built) == set(shared) and all(torch.equal(built[k], shared[k]) for k in built)
