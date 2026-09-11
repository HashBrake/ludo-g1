"""The training loop of T-035: EMA, warmup + cosine, a validation split, resume, and the small config,
and of T-036: periodic checkpoints, a crash and its resume, pruning, and the disk guard.

Everything that trains here runs on the mock session `tests/test_recorder.py`'s `Rig` records under
`tmp_path`, at 64x48 frames and with the deliberately small model `tests/test_diffusion.py` uses, so
that the real loop -- the real lerobot policy, the real dataset, the real checkpoint -- fits in a
test suite. The one exception is the `diffusion_small` latency, which is measured on the **configured**
model at the **configured** frame sizes, because a latency measured on a toy says nothing (D-019);
the number a decision rests on is the 20-call sweep in `agents/BUILD_LOG.md` (R5).

Every test that writes weights deletes them again, the T-036 runs use a one-encoder model (16.0 M
parameters, 256 MB a checkpoint, against 38.4 M and 615 MB), and the disk guard is tested against a
mocked free-space reading rather than by filling the disk: Q-002 leaves 12 GB on this laptop.

Nothing here touches hardware and nothing sends a command (R1, R2).
"""

from __future__ import annotations

import csv
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import torch
import yaml

from engine.interface import Cell, Command, Primitive
from policy._shared import benchmark, set_torch_threads, synthetic_observation
from policy.dataset import LudoDataset, split_cell_pairs
from policy.diffusion import DiffusionAdapter, GoalDiffusionPolicy, PolicySpec
from policy.export import EMA_WEIGHTS, RAW_WEIGHTS, export
from policy.train import (
    CHECKPOINT_FILE,
    EMA,
    NO_PAIR,
    DiskGuardError,
    _StepSampler,
    atomic_save,
    check_checkpoint_disk,
    checkpoint_bytes,
    lr_multiplier,
    prune_step_checkpoints,
    train,
    warmup_for,
    write_step_checkpoint,
)
from runtime import config
from tests.test_diffusion import TINY
from tests.test_recorder import Rig, config_root

#: This repo, the tree the CLI tests copy their packages from.
REPO = Path(__file__).resolve().parents[1]

#: Four MOVE cell pairs and one ROLL, so a cell-pair split has something to split.
PAIRS = [("R-base-1", "track-03"), ("R-base-2", "track-17"), ("track-03", "track-09"),
         ("track-09", "R-home-1")]
#: The runs here are short on purpose: what is under test is the loop, not convergence.
STEPS, BATCH, LR = 10, 2, 1e-3
#: Latency trials in the suite. The recorded number is the 20-call sweep of BUILD_LOG.md.
LATENCY_TRIALS = 5


def command(src: str, dst: str) -> Command:
    return Command(Primitive.MOVE, Cell(src, (0.0, 0.0), (10.0, 10.0)), Cell(dst, (10.0, 10.0), (20.0, 20.0)), "R1")


@pytest.fixture(scope="module")
def session(tmp_path_factory) -> tuple[Path, Path]:
    """Five episodes: one MOVE per cell pair of `PAIRS`, plus a ROLL (which has no pair at all)."""
    rig = Rig(tmp_path_factory.mktemp("train"))
    for src, dst in PAIRS:
        rig.run(0.4, command(src, dst))
        rig.rec.mark_success()
        rig.rec.stop_episode()
    rig.run(0.3, Command(Primitive.ROLL, None, None, None))
    rig.rec.stop_episode()
    rig.rec.close()
    return rig.root, rig.cfg


@pytest.fixture(scope="module")
def move_only_session(tmp_path_factory) -> tuple[Path, Path]:
    """One MOVE episode and nothing else: a session whose every episode can be held out."""
    rig = Rig(tmp_path_factory.mktemp("train_moves"))
    rig.run(0.3, command(*PAIRS[0]))
    rig.rec.stop_episode()
    rig.rec.close()
    return rig.root, rig.cfg


@pytest.fixture(scope="module")
def spec(session) -> PolicySpec:
    return PolicySpec.from_config(session[1], **TINY)


def run_training(session, spec, out: Path, name: str, **kwargs) -> dict:
    """One short run of the real loop with the tiny model."""
    defaults = {"steps": STEPS, "batch_size": BATCH, "learning_rate": LR, "weight_decay": 1e-6,
                "seed": 0, "device": "cpu", "out_dir": out, "log_dir": out, "run_name": name,
                "spec": spec, "config_root": session[1], "stats_samples": 8, "augment": True}
    return train([session[0]], **{**defaults, **kwargs})


# --------------------------------------------------------------------------------------------------
# the schedule: linear warmup, then cosine (acceptance: the multiplier at 0, at warmup, at the end)
# --------------------------------------------------------------------------------------------------


def test_warmup_then_cosine_multiplier_at_zero_warmup_and_end(capsys) -> None:
    warmup, total = 5, 106  # 100 cosine steps after the warmup, so the midpoint below is a step
    middle = warmup + (total - warmup - 1) // 2
    zero, at_warmup, half, end = (lr_multiplier(s, warmup=warmup, total=total)
                                  for s in (0, warmup, middle, total))
    with capsys.disabled():
        print(f"\n[T-035] lr multiplier (warmup {warmup}, total {total}): step 0 {zero:.4f}, "
              f"step {warmup} {at_warmup:.4f}, step {middle} {half:.4f}, step {total} {end:.4f}")
    assert zero == pytest.approx(1 / warmup)          # the first step trains, at one warmup increment
    assert at_warmup == pytest.approx(1.0)            # continuous at the join, from both sides
    assert lr_multiplier(warmup - 1, warmup=warmup, total=total) == pytest.approx(1.0)
    assert half == pytest.approx(0.5, abs=1e-9)       # cosine is symmetric about its midpoint
    # the floor is reached at the run's last step, which is index total - 1: steps are 0-based
    assert lr_multiplier(total - 1, warmup=warmup, total=total) == pytest.approx(0.0, abs=1e-12)
    assert end == pytest.approx(0.0, abs=1e-12)
    # strictly increasing through the warmup, strictly decreasing after it
    ramp = [lr_multiplier(s, warmup=warmup, total=total) for s in range(warmup)]
    decay = [lr_multiplier(s, warmup=warmup, total=total) for s in range(warmup, total)]
    assert ramp == sorted(ramp) and decay == sorted(decay, reverse=True)
    # a floor other than 0 is reached exactly at the last step, not before
    assert lr_multiplier(total - 1, warmup=warmup, total=total, min_ratio=0.1) == pytest.approx(0.1)
    with pytest.raises(ValueError):
        lr_multiplier(-1, warmup=warmup, total=total)


def test_the_warmup_is_clamped_to_a_tenth_of_a_short_run() -> None:
    """500 configured steps must not swallow a 30-step smoke run (`warmup_for`)."""
    assert warmup_for(500, 200_000) == 500
    assert warmup_for(500, 30) == 3
    assert warmup_for(0, 200_000) == 0
    assert warmup_for(500, 5) == 1  # never zero once asked for: the first step is still a warmup step
    assert lr_multiplier(0, warmup=warmup_for(500, 30), total=30) == pytest.approx(1 / 3)


def test_the_run_applies_the_schedule_it_recorded(session, spec, tmp_path) -> None:
    """The `lr` column of loss.csv is `learning_rate` x the multiplier, step by step."""
    record = run_training(session, spec, tmp_path, "schedule", steps=6, warmup_steps=500)
    warmup = record["args"]["warmup_steps"]
    assert warmup == warmup_for(500, 6) == 1
    rows = list(csv.DictReader((Path(record["checkpoint"]).parent / "loss.csv").open(encoding="utf-8")))
    assert [row["step"] for row in rows] == [str(s) for s in range(1, 7)]
    for row in rows:
        want = LR * lr_multiplier(int(row["step"]) - 1, warmup=warmup, total=6, min_ratio=0.0)
        assert float(row["lr"]) == pytest.approx(want, rel=1e-9)
    assert float(rows[-1]["lr"]) == pytest.approx(0.0, abs=1e-12)


# --------------------------------------------------------------------------------------------------
# EMA: the average is kept, stored, and is what a bundle exports
# --------------------------------------------------------------------------------------------------


def test_ema_averages_the_parameters_and_warms_up() -> None:
    """The recursion and its ramp, on a model small enough to check by hand."""
    model = torch.nn.Linear(2, 2, bias=False)
    with torch.no_grad():
        model.weight.fill_(0.0)
    ema = EMA(model, 0.9999)
    assert ema.current_decay() == pytest.approx(1 / 10)  # (1 + 0) / (10 + 0): the ramp, not 0.9999
    with torch.no_grad():
        model.weight.fill_(1.0)
    ema.update(model)
    assert float(ema.shadow["weight"][0, 0]) == pytest.approx(0.9)  # 0.1 * 0 + 0.9 * 1
    assert ema.step == 1
    # the buffers of the exported state come from the live model, the parameters from the average
    state = ema.state_dict(model)
    assert float(state["weight"][0, 0]) == pytest.approx(0.9)
    assert not torch.equal(state["weight"], model.weight)
    with pytest.raises(ValueError):
        EMA(model, 1.0)


def test_ema_changes_the_exported_weights(session, spec, tmp_path, capsys) -> None:
    """Acceptance: the bundle carries the average by default, and `--raw` carries the other one."""
    record = run_training(session, spec, tmp_path, "ema")
    payload = torch.load(record["checkpoint"], map_location="cpu", weights_only=True)
    assert payload["ema_step"] == STEPS and payload["ema_state_dict"]

    ema_bundle = export(record["checkpoint"], tmp_path / "bundle_ema", trace=False)
    raw_bundle = export(record["checkpoint"], tmp_path / "bundle_raw", trace=False, weights=RAW_WEIGHTS)
    assert ema_bundle["weights_source"] == EMA_WEIGHTS and raw_bundle["weights_source"] == RAW_WEIGHTS
    assert ema_bundle["weights_sha256"] != raw_bundle["weights_sha256"]

    live = payload["state_dict"]
    averaged = payload["ema_state_dict"]
    parameters = dict(GoalDiffusionPolicy(spec).named_parameters())
    diffs = [float((averaged[key] - live[key]).abs().max()) for key in parameters]
    with capsys.disabled():
        print(f"[T-035] EMA vs last-step weights after {STEPS} steps: largest difference "
              f"{max(diffs):.3e} over {len(diffs)} parameter tensors")
    assert max(diffs) > 0.0, "the average is identical to the live weights: it was never updated"
    # the statistics buffers are the live ones in both, so a bundle of either is self-contained
    assert torch.equal(averaged["norm.action_min"], live["norm.action_min"])
    # and the exported bundle is loadable and answers as a policy
    adapter = DiffusionAdapter(tmp_path / "bundle_ema", seed=0)
    assert adapter.model.state_dict()["norm.action_min"].shape == live["norm.action_min"].shape


# --------------------------------------------------------------------------------------------------
# the validation split: by cell pair, disjoint, and written to loss.csv
# --------------------------------------------------------------------------------------------------


def test_validation_pairs_are_disjoint_from_the_training_pairs(session, spec, tmp_path) -> None:
    """Acceptance: what the validation loss is measured on was never trained on (`split_by: cell_pair`)."""
    root, cfg = session
    train_pairs, held_out = split_cell_pairs([root], 0.25, 0)
    assert len(held_out) == 1 and set(train_pairs) == {p for p in PAIRS if p not in held_out}
    assert not set(train_pairs) & set(held_out)

    record = run_training(session, spec, tmp_path, "val", steps=6, val_fraction=0.25, val_every=3,
                          val_batches=1)
    assert [tuple(p) for p in record["validation"]["held_out_pairs"]] == held_out

    shared = {"chunk": spec.chunk, "n_obs_steps": spec.n_obs_steps, "config_root": cfg}
    trained_on = LudoDataset([root], split=[*train_pairs, NO_PAIR], **shared)
    validated_on = LudoDataset([root], split=held_out, **shared)
    assert {e.pair for e in validated_on.episodes} == set(held_out)
    assert not {e.pair for e in trained_on.episodes} & set(held_out)
    assert NO_PAIR in {e.pair for e in trained_on.episodes}, "the ROLL episode must stay in training (T-027)"
    assert record["frames"] == len(trained_on) and record["validation"]["frames"] == len(validated_on)
    assert len(trained_on) + len(validated_on) == len(LudoDataset([root], **shared))


def test_the_validation_loss_is_a_column_of_loss_csv(session, spec, tmp_path, capsys) -> None:
    """Section 8 wants the curves saved: one row per step, `val_loss` filled where a pass ran."""
    record = run_training(session, spec, tmp_path, "val_curve", steps=6, val_fraction=0.25, val_every=3,
                          val_batches=1)
    path = Path(record["checkpoint"]).parent / "loss.csv"
    rows = list(csv.DictReader(path.open(encoding="utf-8")))
    assert list(rows[0]) == ["step", "loss", "val_loss", "lr", "elapsed_s"]
    measured = {int(row["step"]): row["val_loss"] for row in rows if row["val_loss"]}
    with capsys.disabled():
        print(f"[T-035] validation loss on {record['validation']['frames']} held-out frames: "
              + ", ".join(f"step {s} {float(v):.4f}" for s, v in sorted(measured.items())))
    assert sorted(measured) == [3, 6]  # every `val_every` steps, and always the last step
    assert all(float(v) > 0 for v in measured.values())
    assert record["validation"]["loss_last"] == pytest.approx(float(measured[6]))

    # with no validation split there is no column content, and no held-out pairs
    plain = run_training(session, spec, tmp_path, "no_val", steps=2)
    plain_rows = list(csv.DictReader((Path(plain["checkpoint"]).parent / "loss.csv").open(encoding="utf-8")))
    assert [row["val_loss"] for row in plain_rows] == ["", ""]
    assert plain["validation"]["held_out_pairs"] == [] and plain["validation"]["loss_last"] is None


def test_holding_out_every_pair_leaves_only_the_episodes_that_have_none(session, spec, tmp_path) -> None:
    """`val_fraction` 1.0 holds out every MOVE pair; the ROLL episode has no pair and stays."""
    root, cfg = session
    record = run_training(session, spec, tmp_path, "all_held", steps=2, val_fraction=1.0, val_every=2,
                          val_batches=1)
    assert [tuple(p) for p in record["validation"]["held_out_pairs"]] == sorted(PAIRS)
    only_roll = LudoDataset([root], chunk=spec.chunk, n_obs_steps=spec.n_obs_steps, config_root=cfg,
                            split=[NO_PAIR])
    assert record["frames"] == len(only_roll) and len(only_roll.episodes) == 1


def test_a_split_that_holds_out_everything_is_refused(move_only_session, spec, tmp_path) -> None:
    """With no ROLL episode to fall back on, holding out every pair is an empty training set, and says so."""
    with pytest.raises(ValueError, match="training split is empty"):
        run_training(move_only_session, spec, tmp_path, "empty", steps=1, val_fraction=1.0)


# --------------------------------------------------------------------------------------------------
# resume: 10 steps + resume + 10 steps is the same run as 20 steps
# --------------------------------------------------------------------------------------------------


def losses_of(record: dict) -> list[tuple[int, float]]:
    rows = csv.DictReader((Path(record["checkpoint"]).parent / "loss.csv").open(encoding="utf-8"))
    return [(int(row["step"]), float(row["loss"])) for row in rows]


def test_the_step_sampler_is_positioned_by_step_not_by_epoch() -> None:
    """What makes a resume exact: index *k* of the stream depends on (seed, k) and nothing else."""
    from itertools import islice

    straight = list(islice(iter(_StepSampler(7, 2, seed=3)), 20))
    resumed = list(islice(iter(_StepSampler(7, 2, seed=3, start_step=4)), 12))
    assert straight[8:] == resumed                      # 4 steps of batch 2 = 8 indices in
    assert sorted(straight[:7]) == list(range(7))       # the first epoch is a permutation
    assert sorted(straight[7:14]) == list(range(7))     # and so is the next one, with another draw
    assert straight[:7] != straight[7:14]
    assert list(islice(iter(_StepSampler(7, 2, seed=4)), 7)) != straight[:7]


def test_resume_reproduces_an_uninterrupted_run(session, spec, tmp_path, capsys) -> None:
    """Acceptance: train 10, resume, train 10 more == train 20 straight, to 1e-6."""
    straight = run_training(session, spec, tmp_path, "straight", steps=2 * STEPS)
    # the same run, interrupted after STEPS steps: `steps` is the horizon the schedule is computed
    # against, `stop_after` is how much of it this invocation runs
    first = run_training(session, spec, tmp_path, "half", steps=2 * STEPS, stop_after=STEPS)
    second = run_training(session, spec, tmp_path, "resumed", steps=2 * STEPS,
                          resume=first["checkpoint"])
    assert losses_of(first) == losses_of(straight)[:STEPS]

    whole, continued = losses_of(straight), losses_of(second)
    assert [s for s, _ in continued] == list(range(1, 2 * STEPS + 1))  # the curve carries the first half
    assert continued[:STEPS] == losses_of(first)
    worst = max(abs(a - b) for (_s, a), (_t, b) in zip(whole, continued, strict=True))
    with capsys.disabled():
        print(f"[T-035] resume: largest |loss difference| over {2 * STEPS} steps between one run and "
              f"{STEPS}+{STEPS} with a restart: {worst:.3e}")
    assert worst < 1e-6
    # and the weights themselves agree, not only the losses they produced
    a = torch.load(straight["checkpoint"], map_location="cpu", weights_only=True)
    b = torch.load(second["checkpoint"], map_location="cpu", weights_only=True)
    assert b["step"] == 2 * STEPS and b["ema_step"] == 2 * STEPS
    assert b["run"]["args"]["resumed_at_step"] == STEPS
    for key, value in a["state_dict"].items():
        assert torch.allclose(value.float(), b["state_dict"][key].float(), atol=1e-6), key
    for key, value in a["ema_state_dict"].items():
        assert torch.allclose(value.float(), b["ema_state_dict"][key].float(), atol=1e-6), key


def test_resume_refuses_a_checkpoint_of_another_architecture(session, spec, tmp_path) -> None:
    record = run_training(session, spec, tmp_path, "arch", steps=2)
    other = PolicySpec.from_config(session[1], **{**TINY, "down_dims": (32, 64, 128)})
    with pytest.raises(ValueError, match="different architecture"):
        run_training(session, other, tmp_path, "arch_resumed", steps=4, resume=record["checkpoint"])
    with pytest.raises(ValueError, match="already at step"):
        run_training(session, spec, tmp_path, "arch_done", steps=2, resume=record["checkpoint"])


# --------------------------------------------------------------------------------------------------
# the smaller configuration of D-019, and the thread pool of D-020
# --------------------------------------------------------------------------------------------------


def test_the_small_config_is_the_same_model_with_three_levers_pulled() -> None:
    full, small = PolicySpec.from_config(), PolicySpec.from_config(block="diffusion_small")
    assert small.image_hw == (120, 160) and full.image_hw == (240, 320)
    assert small.separate_encoder_per_camera is False and full.separate_encoder_per_camera is True
    assert small.down_dims == (128, 256, 512) and full.down_dims == (512, 1024, 2048)
    # everything the controller and the dataset depend on is unchanged, or they are not comparable
    assert (small.chunk, small.execute, small.n_obs_steps) == (full.chunk, full.execute, full.n_obs_steps)
    assert (small.state_dim, small.action_dim, small.goal_channels) == (full.state_dim, full.action_dim,
                                                                       full.goal_channels)
    with pytest.raises(config.ConfigError, match="no 'nope' block"):
        PolicySpec.from_config(block="nope")


def test_small_config_latency_at_ddim_10_and_5(tmp_path, capsys) -> None:
    """Print what the D-019 fallback costs per call, at the configured frame sizes and model size.

    Five calls here; the recorded numbers are the 20-call thread sweep in `agents/BUILD_LOG.md`.
    """
    spec = PolicySpec.from_config(block="diffusion_small")
    model = GoalDiffusionPolicy(spec)
    checkpoint = tmp_path / "checkpoint.pt"
    torch.save({"policy": "diffusion", "spec": spec.to_dict(), "state_dict": model.state_dict()}, checkpoint)
    del model
    export(checkpoint, tmp_path / "bundle", trace=False)
    checkpoint.unlink()  # 122 MB of untrained weights: measured, then gone (Q-002)

    sizes = config.load("training")["observation"]["images"]
    observation = synthetic_observation(sizes, 3)
    budget_ms = 1e3 / float(config.load("training")["rates"]["policy_hz"])
    medians = {}
    for steps in (10, 5):
        adapter = DiffusionAdapter(tmp_path / "bundle", seed=0, inference_steps=steps)
        parameters = sum(p.numel() for p in adapter.model.parameters()) / 1e6
        medians[steps] = benchmark(adapter, observation, trials=LATENCY_TRIALS)["median_ms"]
        with capsys.disabled():
            print(f"[T-035] diffusion_small ({parameters:.1f}M params, {spec.image_hw} encoder, shared) "
                  f"DDIM {steps}: median {medians[steps]:.0f} ms over {LATENCY_TRIALS} calls "
                  f"(budget {budget_ms:.0f} ms), torch threads {adapter.torch_threads}")
    assert medians[5] < medians[10], "fewer DDIM steps must cost less; otherwise the measurement is noise"


def test_the_torch_thread_pool_is_configured_and_applied_once() -> None:
    """D-020: one configured value, applied by whoever gets there first, never resized after."""
    configured = int(config.load("training")["compute"]["torch_threads"])
    assert set_torch_threads() == configured == torch.get_num_threads()
    assert set_torch_threads(threads=configured + 1) == configured  # idempotent: the first call wins
    assert torch.get_num_threads() == configured


# --------------------------------------------------------------------------------------------------
# T-036: periodic checkpoints, crash resume, pruning, and the disk guard
# --------------------------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def small_spec(session) -> PolicySpec:
    """`spec` with one shared ResNet-18 instead of three.

    16.0 M parameters against 38.4 M, so every checkpoint these tests write is 256 MB and not 615 MB
    (Q-002: 12 GB free). Nothing under test here depends on the number of encoders.
    """
    return PolicySpec.from_config(session[1], **TINY, separate_encoder_per_camera=False)


def test_the_checkpoint_size_estimate_is_parameters_x_4_x_4_plus_a_margin() -> None:
    """The guard's arithmetic: fp32 weights, their EMA, and Adam's two moments, plus 10%."""
    assert checkpoint_bytes(1_000_000) == int(1_000_000 * 4 * 4 * 1.1)
    assert checkpoint_bytes(0) == 0
    # monotone, and an over-estimate of the real file (asserted against a real one further down)
    assert checkpoint_bytes(2_000_000) == 2 * checkpoint_bytes(1_000_000)


def test_the_disk_guard_refuses_a_run_that_cannot_hold_its_checkpoints(tmp_path, monkeypatch,
                                                                      capsys) -> None:
    """Acceptance: the guard triggers on a mocked free-space reading, names Q-002, and can be waived."""
    parameters = 293_000_000                     # the configured diffusion policy of 5.7
    estimate = checkpoint_bytes(parameters)
    free = [int(1.5 * estimate)]                 # enough for one checkpoint, not for the factor of 2

    def fake_usage(path):                        # os.statvfs is what shutil.disk_usage reads
        assert Path(path) == tmp_path
        return shutil._ntuple_diskusage(10 * estimate, 10 * estimate - free[0], free[0])

    monkeypatch.setattr(shutil, "disk_usage", fake_usage)
    with pytest.raises(DiskGuardError) as refusal:
        check_checkpoint_disk(tmp_path, parameters, factor=2.0)
    message = str(refusal.value)
    with capsys.disabled():
        print(f"\n[T-036] disk guard: {parameters / 1e6:.0f}M parameters -> checkpoint estimate "
              f"{estimate / 1e9:.2f} GB, factor 2 needs {2 * estimate / 1e9:.2f} GB, mocked free "
              f"{free[0] / 1e9:.2f} GB -> refused")
    assert "Q-002" in message and "disk_guard_factor 2" in message
    assert f"{estimate / 1e9:.2f} GB" in message and f"{free[0] / 1e9:.2f} GB free" in message

    # --no-disk-guard: the same shortfall, logged instead of raised
    numbers = check_checkpoint_disk(tmp_path, parameters, factor=2.0, enforce=False)
    assert numbers == {"parameters": parameters, "estimate_bytes": estimate,
                       "required_bytes": 2 * estimate, "free_bytes": free[0], "factor": 2.0,
                       "enforced": False}
    # and with room it passes without either
    free[0] = 3 * estimate
    assert check_checkpoint_disk(tmp_path, parameters, factor=2.0)["free_bytes"] == free[0]


def test_a_run_whose_checkpoints_do_not_fit_never_starts(session, small_spec, tmp_path,
                                                         monkeypatch) -> None:
    """The guard runs before the first step: no checkpoint, no loss.csv, one second wasted."""
    monkeypatch.setattr(shutil, "disk_usage",
                        lambda path: shutil._ntuple_diskusage(1 << 40, (1 << 40) - 1_000_000, 1_000_000))
    with pytest.raises(DiskGuardError, match="Q-002"):
        run_training(session, small_spec, tmp_path, "no_room", steps=2)
    assert sorted(p.name for p in (tmp_path / "no_room").iterdir()) == []
    # the same run trains once the guard is waived, and says so in the record
    record = run_training(session, small_spec, tmp_path, "no_room", steps=1, disk_guard=False)
    assert record["args"]["disk_guard"] is False
    assert Path(record["checkpoint"]).is_file()
    shutil.rmtree(Path(record["checkpoint"]).parent)   # 256 MB of test weights, measured then gone


def test_prune_step_checkpoints_keeps_the_last_k_and_touches_nothing_else(tmp_path) -> None:
    """Acceptance: exactly K step checkpoints remain; checkpoint.pt and a bundle are never pruned."""
    for step in (100, 20, 3, 4000):
        (tmp_path / f"checkpoint_step{step}.pt").write_bytes(b"x")
    (tmp_path / CHECKPOINT_FILE).write_bytes(b"x")
    (tmp_path / "run.json").write_bytes(b"{}")
    (tmp_path / "bundle").mkdir()                       # the EMA export: never a step checkpoint
    (tmp_path / "bundle" / "weights.pt").write_bytes(b"x")

    assert [p.name for p in prune_step_checkpoints(tmp_path, keep=2)] == ["checkpoint_step3.pt",
                                                                         "checkpoint_step20.pt"]
    assert sorted(p.name for p in tmp_path.iterdir()) == ["bundle", "checkpoint.pt",
                                                          "checkpoint_step100.pt",
                                                          "checkpoint_step4000.pt", "run.json"]
    assert prune_step_checkpoints(tmp_path, keep=2) == []          # already at K: nothing to do
    assert len(prune_step_checkpoints(tmp_path, keep=0)) == 2      # K=0 keeps none of them
    assert sorted(p.name for p in tmp_path.iterdir()) == ["bundle", "checkpoint.pt", "run.json"]
    assert (tmp_path / "bundle" / "weights.pt").is_file()


def test_a_checkpoint_write_that_dies_leaves_the_previous_one(tmp_path, monkeypatch) -> None:
    """Why every write is temp-then-rename: a half-written checkpoint is never named checkpoint.pt."""
    path = tmp_path / CHECKPOINT_FILE
    atomic_save({"step": 1, "weight": torch.ones(4)}, path)
    assert torch.load(path, weights_only=True)["step"] == 1
    assert not list(tmp_path.glob("*.tmp"))

    real_save = torch.save

    def dying_save(payload, target, *args, **kwargs):
        real_save(payload, target, *args, **kwargs)     # the bytes land in the .tmp file
        raise OSError("No space left on device")        # and the process dies before the rename

    monkeypatch.setattr(torch, "save", dying_save)
    with pytest.raises(OSError, match="No space left"):
        atomic_save({"step": 2, "weight": torch.zeros(4)}, path)
    assert torch.load(path, weights_only=True)["step"] == 1, "the previous checkpoint was destroyed"
    assert [p.name for p in tmp_path.glob("*.tmp")] == ["checkpoint.pt.tmp"]


def test_a_step_checkpoint_is_an_alias_of_checkpoint_pt(tmp_path) -> None:
    """The periodic copy costs no second write and no second inode, and both names survive pruning."""
    alias, pruned = write_step_checkpoint({"step": 7, "weight": torch.ones(2)}, tmp_path, 7, keep=1)
    assert alias.name == "checkpoint_step7.pt" and pruned == []
    latest = tmp_path / CHECKPOINT_FILE
    assert alias.stat().st_ino == latest.stat().st_ino      # one file, two names
    assert not list(tmp_path.glob("*.tmp"))

    alias2, pruned2 = write_step_checkpoint({"step": 8, "weight": torch.zeros(2)}, tmp_path, 8, keep=1)
    assert [p.name for p in pruned2] == ["checkpoint_step7.pt"]
    assert torch.load(latest, weights_only=True)["step"] == 8
    assert alias2.stat().st_ino == latest.stat().st_ino
    assert sorted(p.name for p in tmp_path.iterdir()) == ["checkpoint.pt", "checkpoint_step8.pt"]


def test_the_loop_checkpoints_every_n_steps_and_keeps_the_last_k(session, small_spec, tmp_path,
                                                                 capsys) -> None:
    """Acceptance: the real loop's run directory holds exactly checkpoint.pt plus the K newest."""
    record = run_training(session, small_spec, tmp_path, "periodic", steps=6, checkpoint_every=2,
                          keep_last=1)
    directory = Path(record["checkpoint"]).parent
    try:
        # steps 2 and 4 were written (6 is the end of the invocation, checkpointed below anyway),
        # and keep_last 1 pruned step 2
        assert sorted(p.name for p in directory.iterdir()) == ["checkpoint.pt", "checkpoint_step4.pt",
                                                               "loss.csv", "run.json"]
        step4 = torch.load(directory / "checkpoint_step4.pt", map_location="cpu", weights_only=True)
        latest = torch.load(directory / CHECKPOINT_FILE, map_location="cpu", weights_only=True)
        assert step4["step"] == 4 and step4["run"]["complete"] is False
        assert latest["step"] == 6 and latest["run"]["complete"] is True
        assert [row[0] for row in step4["losses"]] == [1, 2, 3, 4]
        size = (directory / CHECKPOINT_FILE).stat().st_size
        estimate = record["checkpoint_bytes_estimate"]
        with capsys.disabled():
            print(f"[T-036] {record['parameters'] / 1e6:.1f}M parameters: checkpoint.pt is "
                  f"{size / 1e6:.1f} MB, the guard's estimate {estimate / 1e6:.1f} MB "
                  f"({estimate / size:.2f}x), disk guard factor {record['args']['disk_guard_factor']:g}")
        assert size < estimate, "the estimate must not be under the real file, or it is not a guard"
    finally:
        shutil.rmtree(directory)   # ~512 MB of test weights: measured, then gone (Q-002)


# --- the crash: a real `python -m policy.train` killed mid-run, then resumed ------------------------

#: The packages `python -m policy.train` needs, copied so that the CLI can be run against a
#: test-sized config: `runtime.config.CONFIG_DIR` is fixed relative to the imported runtime/config.py,
#: so a shrunk config/ only reaches the CLI as part of a tree (the same thing `greennode.sh up` pushes).
CLI_PACKAGES = ("board", "drivers", "engine", "eval", "policy", "runtime", "teleop", "tools")
#: The tiny model, written into the copied tree's `diffusion` block. One shared encoder: 16.0 M
#: parameters, so each of the three runs below writes a 256 MB checkpoint (Q-002).
CLI_DIFFUSION = {"encoder_image_hw": [48, 64], "down_dims": [64, 128, 256],
                 "spatial_softmax_keypoints": 8, "stats_samples": 8, "encoder_per_camera": False}
#: 8 steps, batch 2: enough for a crash at step 5 to have two periodic checkpoints behind it.
CLI_STEPS, CLI_FAULT_AT, CLI_EVERY = 8, 5, 2


@pytest.fixture(scope="module")
def cli_tree(tmp_path_factory) -> Path:
    """A copy of the source packages with a shrunk config, to run the CLI at test scale."""
    tree = tmp_path_factory.mktemp("cli")
    for package in CLI_PACKAGES:
        shutil.copytree(REPO / package, tree / package,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    config_root(tree, small=True)
    training = yaml.safe_load((tree / "config" / "training.yaml").read_text(encoding="utf-8"))
    training["diffusion"].update(CLI_DIFFUSION)
    (tree / "config" / "training.yaml").write_text(yaml.safe_dump(training, sort_keys=False),
                                                   encoding="utf-8")
    return tree


def cli_train(tree: Path, session_root: Path, out: Path, name: str, *extra: str):
    """`python -m policy.train` in a subprocess against the copied tree. Returns the completed run."""
    argv = [sys.executable, "-m", "policy.train", "--sessions", str(session_root), "--out-dir", str(out),
            "--run-name", name, "--steps", str(CLI_STEPS), "--batch-size", "2", "--lr", "1e-3",
            "--seed", "0", "--val-fraction", "0", "--stats-samples", "8", "--device", "cpu", *extra]
    done = subprocess.run(argv, cwd=tree, env={**os.environ, "PYTHONPATH": str(tree)},
                          capture_output=True, text=True, timeout=1800, check=False)
    print(f"$ {' '.join(argv[1:])}\n{done.stdout}\n{done.stderr[-2000:]}")
    return done


@pytest.fixture(scope="module")
def crash_and_resume(cli_tree, session, tmp_path_factory) -> dict:
    """Three real CLI runs: 8 steps straight, 8 steps crashing at 5, and the resume of the crash.

    Each run's weights are deleted as soon as the next one no longer needs them; what this fixture
    keeps is text (Q-002: nothing here may leave 256 MB behind).
    """
    out = tmp_path_factory.mktemp("crash")
    root = session[0]
    straight = cli_train(cli_tree, root, out, "straight", "--checkpoint-every", "0")
    straight_losses = (out / "straight" / "loss.csv").read_text(encoding="utf-8") if straight.returncode == 0 else ""
    shutil.rmtree(out / "straight", ignore_errors=True)

    crashed = cli_train(cli_tree, root, out, "crashed", "--checkpoint-every", str(CLI_EVERY),
                        "--keep-last", "1", "--fault-at-step", str(CLI_FAULT_AT))
    files = sorted(p.name for p in (out / "crashed").iterdir()) if (out / "crashed").is_dir() else []
    last = out / "crashed" / f"checkpoint_step{CLI_FAULT_AT - 1}.pt"
    same_inode = last.is_file() and last.stat().st_ino == (out / "crashed" / CHECKPOINT_FILE).stat().st_ino
    step = int(torch.load(last, map_location="cpu", weights_only=True)["step"]) if last.is_file() else -1

    resumed = cli_train(cli_tree, root, out, "resumed", "--checkpoint-every", "0",
                        "--resume", str(last))
    resumed_losses = (out / "resumed" / "loss.csv").read_text(encoding="utf-8") if resumed.returncode == 0 else ""
    for name in ("crashed", "resumed"):
        shutil.rmtree(out / name, ignore_errors=True)
    return {"straight": straight, "crashed": crashed, "resumed": resumed, "files": files,
            "checkpoint_step": step, "same_inode": same_inode,
            "curves": {"straight": straight_losses, "resumed": resumed_losses}}


def curve(text: str) -> list[tuple[int, float]]:
    return [(int(row["step"]), float(row["loss"])) for row in csv.DictReader(text.splitlines())]


def test_a_crashed_run_leaves_the_last_periodic_checkpoint_whole(crash_and_resume) -> None:
    """The process really died at step 5, and what it left behind is the step-4 checkpoint."""
    crashed = crash_and_resume["crashed"]
    assert crashed.returncode != 0, crashed.stdout
    assert "InjectedFault" in crashed.stderr and f"--fault-at-step {CLI_FAULT_AT}" in crashed.stderr
    # written at steps 2 and 4, pruned to the last one; no run.json and no loss.csv (it never finished)
    assert crash_and_resume["files"] == ["checkpoint.pt", f"checkpoint_step{CLI_FAULT_AT - 1}.pt"]
    assert crash_and_resume["checkpoint_step"] == CLI_FAULT_AT - 1
    assert crash_and_resume["same_inode"], "the step copy must be a hard link, not a second write"


def test_resuming_the_crash_reproduces_the_uninterrupted_run(crash_and_resume, capsys) -> None:
    """ACCEPTANCE: the loss sequence of crash + resume is the straight run's, step for step."""
    assert crash_and_resume["straight"].returncode == 0, crash_and_resume["straight"].stderr
    assert crash_and_resume["resumed"].returncode == 0, crash_and_resume["resumed"].stderr
    straight = curve(crash_and_resume["curves"]["straight"])
    resumed = curve(crash_and_resume["curves"]["resumed"])
    assert [s for s, _ in straight] == list(range(1, CLI_STEPS + 1))
    assert [s for s, _ in resumed] == list(range(1, CLI_STEPS + 1))
    worst = max(abs(a - b) for (_s, a), (_t, b) in zip(straight, resumed, strict=True))
    with capsys.disabled():
        print(f"[T-036] crash at step {CLI_FAULT_AT}, resume from checkpoint_step{CLI_FAULT_AT - 1}.pt: "
              f"largest |loss difference| against the straight {CLI_STEPS}-step run {worst:.3e} "
              f"(losses {[round(v, 4) for _s, v in resumed]})")
    assert worst < 1e-6
